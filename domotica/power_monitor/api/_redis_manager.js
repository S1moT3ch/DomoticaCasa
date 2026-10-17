const { Redis } = require('@upstash/redis');

/**
 * Gestore Ridondanza Multi-Database Redis per Upstash / Vercel KV
 * Supporta Failover Automatico (Primary <-> Backup) e selezione atomica dello stato più recente
 */
class RedisClusterManager {
    constructor() {
        this.primary = null;
        this.backup = null;
        this._initClients();
    }

    _initClients() {
        // 1. Database Primario
        const primaryUrl = process.env.UPSTASH_REDIS_REST_URL || process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL_PRIMARY;
        const primaryToken = process.env.UPSTASH_REDIS_REST_TOKEN || process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN_PRIMARY;
        if (primaryUrl && primaryToken) {
            this.primary = {
                name: 'PRIMARY',
                client: new Redis({ url: primaryUrl, token: primaryToken }),
                isQuotaExceeded: false
            };
        }

        // 2. Database di Backup / Ridondanza (istanza distinta)
        const backupUrl = process.env.UPSTASH_REDIS_REST_URL_BACKUP || process.env.KV_REST_API_URL_BACKUP || process.env.UPSTASH_REDIS_REST_URL_2;
        const backupToken = process.env.UPSTASH_REDIS_REST_TOKEN_BACKUP || process.env.KV_REST_API_TOKEN_BACKUP || process.env.UPSTASH_REDIS_REST_TOKEN_2;
        if (backupUrl && backupToken && backupUrl !== primaryUrl) {
            this.backup = {
                name: 'BACKUP',
                client: new Redis({ url: backupUrl, token: backupToken }),
                isQuotaExceeded: false
            };
        }
    }

    isFailoverError(err) {
        if (!err) return false;
        const msg = String(err.message || err).toLowerCase();
        return (
            msg.includes('max requests limit exceeded') ||
            msg.includes('limit exceeded') ||
            msg.includes('quota') ||
            msg.includes('429') ||
            msg.includes('econnrefused') ||
            msg.includes('etimedout') ||
            msg.includes('timeout') ||
            msg.includes('network')
        );
    }

    isConfigured() {
        return !!(this.primary || this.backup);
    }

    hasRedundancy() {
        return !!(this.primary && this.backup);
    }

    async _readFromNode(node) {
        const results = await node.client.mget(
            'monitor_state',
            'last_heartbeat',
            'last_heartbeat_server',
            'last_heartbeat_firewall',
            'latest_power_w',
            'latest_voltage',
            'latest_current'
        );

        const rawState = results ? results[0] : null;
        const legacyHb = results ? results[1] : null;
        const legacyServer = results ? results[2] : null;
        const legacyFw = results ? results[3] : null;
        const legacyPw = results ? results[4] : null;
        const legacyV = results ? results[5] : null;
        const legacyA = results ? results[6] : null;

        let parsed = null;
        if (rawState) {
            parsed = typeof rawState === 'object' ? rawState : JSON.parse(rawState);
        }
        if (!parsed) parsed = {};
        if (!parsed.nodes) parsed.nodes = {};

        // Ripristino eventuali chiavi legacy
        if (!parsed.last_heartbeat && legacyHb) {
            parsed.last_heartbeat = parseInt(legacyHb, 10);
        }
        if (!parsed.nodes.server?.last_heartbeat && legacyServer) {
            parsed.nodes.server = { ...(parsed.nodes.server || {}), last_heartbeat: parseInt(legacyServer, 10) };
        }
        if (!parsed.nodes.firewall?.last_heartbeat && legacyFw) {
            parsed.nodes.firewall = { ...(parsed.nodes.firewall || {}), last_heartbeat: parseInt(legacyFw, 10) };
        }
        if (parsed.latest_power_w === undefined && legacyPw !== null) {
            parsed.latest_power_w = legacyPw;
        }
        if (parsed.latest_voltage === undefined && legacyV !== null) {
            parsed.latest_voltage = legacyV;
        }
        if (parsed.latest_current === undefined && legacyA !== null) {
            parsed.latest_current = legacyA;
        }

        return {
            state: parsed,
            activeNode: node.name,
            hasBackup: this.hasRedundancy()
        };
    }

    /**
     * Lettura stato intelligente con Failover e selezione automatica dello stato PIÙ RECENTE
     * Evita di leggere stati vecchi/congelati se il Primario è bloccato in scrittura
     */
    async getState() {
        if (!this.primary && !this.backup) {
            throw new Error('Nessun database Redis configurato');
        }

        // Se entrambi i database sono configurati: lettura parallela per selezionare il più fresco
        if (this.primary && this.backup) {
            const [pRes, bRes] = await Promise.allSettled([
                this._readFromNode(this.primary),
                this._readFromNode(this.backup)
            ]);

            const pData = pRes.status === 'fulfilled' ? pRes.value : null;
            const bData = bRes.status === 'fulfilled' ? bRes.value : null;

            if (pRes.status === 'rejected' && this.isFailoverError(pRes.reason)) {
                this.primary.isQuotaExceeded = true;
            }
            if (bRes.status === 'rejected' && this.isFailoverError(bRes.reason)) {
                this.backup.isQuotaExceeded = true;
            }

            // Se entrambi i nodi hanno risposto, selezioniamo quello con il last_heartbeat più recente
            if (pData && bData) {
                const pTs = parseInt(pData.state?.last_heartbeat || 0, 10);
                const bTs = parseInt(bData.state?.last_heartbeat || 0, 10);

                // Se Backup ha dati più recenti (es. Primary congelato dal 25 settembre perché senza quota di scrittura)
                if (bTs > pTs) {
                    this.primary.isQuotaExceeded = true; // Primary non è aggiornato
                    return bData;
                } else {
                    return pData;
                }
            }

            if (bData) return bData;
            if (pData) return pData;

            throw pRes.reason || bRes.reason || new Error('Nessun nodo Redis disponibile');
        }

        const singleNode = this.primary || this.backup;
        return await this._readFromNode(singleNode);
    }

    /**
     * Scrittura stato con failover automatico
     */
    async saveState(stateObj) {
        const payloadStr = JSON.stringify(stateObj);
        let writeSuccessful = false;
        let lastErr = null;
        let writtenNodes = [];

        const targets = [];
        // Se Primary non è marcato con quota esaurita, lo tentiamo per primo, altrimenti Backup
        if (this.backup && this.primary && this.primary.isQuotaExceeded) {
            targets.push(this.backup);
            targets.push(this.primary);
        } else {
            if (this.primary && !this.primary.isQuotaExceeded) targets.push(this.primary);
            if (this.backup && !this.backup.isQuotaExceeded) targets.push(this.backup);
        }

        if (targets.length === 0) {
            if (this.backup) targets.push(this.backup);
            if (this.primary) targets.push(this.primary);
        }

        for (const node of targets) {
            try {
                await node.client.set('monitor_state', payloadStr);
                writeSuccessful = true;
                writtenNodes.push(node.name);
            } catch (err) {
                lastErr = err;
                if (this.isFailoverError(err)) {
                    node.isQuotaExceeded = true;
                    console.warn(`[REDIS FAILOVER] Scrittura fallita su ${node.name} (${err.message}). Switch su nodo di riserva...`);
                } else {
                    console.error(`[REDIS ERROR] Scrittura fallita su ${node.name}:`, err.message);
                }
            }
        }

        if (!writeSuccessful) {
            throw lastErr || new Error('Impossibile salvare lo stato su nessun nodo Redis');
        }

        return {
            success: true,
            writtenNodes
        };
    }
}

module.exports = {
    RedisClusterManager
};
