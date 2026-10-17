const { RedisClusterManager } = require('./_redis_manager');
const axios = require('axios');

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
const HEARTBEAT_SECRET = process.env.HEARTBEAT_SECRET || 'SecretHeartbeatKey2026';
const TIMEOUT_SECONDS = parseInt(process.env.TIMEOUT_SECONDS || '150', 10);
const REMOTE_SIGNATURE = "\n\n📡 _Messaggio inviato dal Server Remoto_";

async function sendTelegramAlert(message, subscribers = null) {
    if (!TELEGRAM_BOT_TOKEN) return;

    let chatIds = TELEGRAM_CHAT_ID ? [TELEGRAM_CHAT_ID] : [];
    if (Array.isArray(subscribers) && subscribers.length > 0) {
        chatIds = subscribers;
    }

    const fullMessage = message.includes("Server Remoto") ? message : `${message}${REMOTE_SIGNATURE}`;
    const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
    for (const chatId of chatIds) {
        try {
            await axios.post(url, {
                chat_id: chatId,
                text: fullMessage,
                parse_mode: 'Markdown'
            });
        } catch (err) {
            console.error(`Errore invio Telegram a ${chatId}:`, err.message);
        }
    }
}

module.exports = async (req, res) => {
    res.setHeader('Content-Type', 'application/json');

    if (req.method !== 'POST') {
        return res.status(405).json({ error: 'Method Not Allowed. Use POST.' });
    }

    const token = req.headers['x-heartbeat-secret'] || req.body?.secret;
    if (token !== HEARTBEAT_SECRET) {
        return res.status(401).json({ error: 'Unauthorized: Invalid Heartbeat Secret' });
    }

    const cluster = new RedisClusterManager();
    if (!cluster.isConfigured()) {
        return res.status(500).json({
            error: 'Vercel KV / Upstash Redis Non Configurato',
            help: 'Aggiungi KV Storage su Vercel per abilitare il salvataggio dei dati.'
        });
    }

    try {
        const now = Date.now();
        const node = String(req.body?.node || 'server').toLowerCase().trim();

        // 1. Lettura COMPATTA dello stato con failover automatico (Primary -> Backup)
        let stateData = null;
        try {
            stateData = await cluster.getState();
        } catch (readErr) {
            if (cluster.isFailoverError(readErr)) {
                return res.status(429).json({
                    status: 'quota_exceeded',
                    warning: 'Tutti i database Redis configurati hanno superato la quota mensile. Aggiungi UPSTASH_REDIS_REST_URL_BACKUP o attendi il reset.',
                    timestamp: now
                });
            }
            throw readErr;
        }

        let state = stateData.state || {};

        // Inizializzazione chiavi predefinite se primo avvio
        if (!state.nodes) state.nodes = {};
        if (!state.alerts) state.alerts = {};
        if (!state.recent_outage_events) state.recent_outage_events = [];
        if (!state.unreported_events_for_server) state.unreported_events_for_server = [];

        // Aggiornamento heartbeat globale e per nodo
        state.last_heartbeat = now;
        state.nodes[node] = {
            ...(state.nodes[node] || {}),
            last_heartbeat: now
        };

        // Telemetria di consumo per Alexa Skill e Dashboard
        if (req.body?.power_w !== undefined) {
            state.latest_power_w = req.body.power_w;
        }
        if (req.body?.voltage !== undefined) {
            state.latest_voltage = req.body.voltage;
        }
        if (req.body?.current !== undefined) {
            state.latest_current = req.body.current;
        }
        if (req.body?.power_timestamp !== undefined) {
            state.latest_power_ts = req.body.power_timestamp;
        }

        // Iscritti notifiche Telegram
        const subscribers = req.body?.subscribers;
        if (Array.isArray(subscribers) && subscribers.length > 0) {
            state.active_subscribers = subscribers;
        }
        const activeSubscribers = state.active_subscribers || (TELEGRAM_CHAT_ID ? [TELEGRAM_CHAT_ID] : []);

        let recoveredEventsForResponse = [];

        // 2. GESTIONE RIPRISTINO BLACKOUT
        if (state.alerts.outage) {
            state.alerts.outage = false;
            state.alerts.server = false;
            state.alerts.firewall = false;

            const startedAt = state.outage_started_at || (now - TIMEOUT_SECONDS * 1000);
            const durationSec = Math.max(1, Math.round((now - startedAt) / 1000));

            const blackoutEvent = {
                type: 'blackout',
                node: 'all',
                description: 'Blackout totale - Assenza di corrente elettrica',
                started_at: startedAt,
                started_at_iso: new Date(startedAt).toISOString(),
                ended_at: now,
                ended_at_iso: new Date(now).toISOString(),
                duration_seconds: durationSec
            };

            delete state.outage_started_at;
            delete state.server_down_started_at;
            delete state.firewall_down_started_at;

            state.recent_outage_events.unshift(blackoutEvent);
            if (state.recent_outage_events.length > 20) {
                state.recent_outage_events = state.recent_outage_events.slice(0, 20);
            }

            if (node === 'server') {
                recoveredEventsForResponse.push(blackoutEvent);
            } else {
                state.unreported_events_for_server.push(blackoutEvent);
            }

            await sendTelegramAlert(
                "🟢 *RIPRISTINO CORRENTE ELETTRICA*\n\n" +
                "⚡ La rete elettrica ed i nodi domotici di casa sono di nuovo **ONLINE** e funzionanti!\n" +
                `⏱️ _Durata interruzione_: *${durationSec} secondi*`,
                activeSubscribers
            );
        }

        // 3. GESTIONE RIPRISTINO SINGOLO NODO
        if (state.alerts[node]) {
            state.alerts[node] = false;

            const startedAt = state[`${node}_down_started_at`] || (now - TIMEOUT_SECONDS * 1000);
            const durationSec = Math.max(1, Math.round((now - startedAt) / 1000));
            delete state[`${node}_down_started_at`];

            const nodeFriendly = node === 'server' ? 'Home Server (NVR/Bot)' : (node === 'firewall' ? 'Firewall / Gateway' : node.toUpperCase());
            const nodeEvent = {
                type: `${node}_down`,
                node: node,
                description: `Down ${nodeFriendly}`,
                started_at: startedAt,
                started_at_iso: new Date(startedAt).toISOString(),
                ended_at: now,
                ended_at_iso: new Date(now).toISOString(),
                duration_seconds: durationSec
            };

            state.recent_outage_events.unshift(nodeEvent);
            if (state.recent_outage_events.length > 20) {
                state.recent_outage_events = state.recent_outage_events.slice(0, 20);
            }

            if (node === 'server') {
                recoveredEventsForResponse.push(nodeEvent);
            } else {
                state.unreported_events_for_server.push(nodeEvent);
            }

            await sendTelegramAlert(
                `🟢 *NODO RIPRISTINATO: ${node.toUpperCase()}*\n\n` +
                `✅ Il nodo *${nodeFriendly}* è di nuovo **ONLINE** e comunica regolarmente.\n` +
                `⏱️ _Durata disconnessione_: *${durationSec} secondi*`,
                activeSubscribers
            );
        }

        // Eventi pendenti per il server
        if (node === 'server' && state.unreported_events_for_server && state.unreported_events_for_server.length > 0) {
            recoveredEventsForResponse = recoveredEventsForResponse.concat(state.unreported_events_for_server);
            state.unreported_events_for_server = [];
        }

        // 4. WATCHDOG AUTOMATICO INCROCIATO SULL'ALTRO NODO
        const otherNode = node === 'server' ? 'firewall' : (node === 'firewall' ? 'server' : null);
        if (otherNode && !state.alerts.outage) {
            const lastOtherTs = state.nodes[otherNode]?.last_heartbeat;
            if (lastOtherTs) {
                const otherElapsed = Math.round((now - parseInt(lastOtherTs, 10)) / 1000);
                const isOtherAlertActive = state.alerts[otherNode];
                if (otherElapsed > TIMEOUT_SECONDS && !isOtherAlertActive) {
                    state.alerts[otherNode] = true;
                    state[`${otherNode}_down_started_at`] = parseInt(lastOtherTs, 10);

                    const otherFriendly = otherNode === 'server' ? 'Home Server (NVR/Bot)' : 'Firewall / Gateway';
                    const currentFriendly = node === 'server' ? 'Home Server' : 'Firewall';
                    await sendTelegramAlert(
                        `⚠️ *NODO DOMOTICO OFFLINE: ${otherNode.toUpperCase()}*\n\n` +
                        `🚨 ATTENZIONE: Il nodo *${otherFriendly}* non invia il segnale di vita (heartbeat) da oltre *${otherElapsed} secondi*.\n\n` +
                        `ℹ️ Il nodo *${currentFriendly}* è invece regolarmente *ONLINE*, quindi alimentazione e connettività sono presenti.\n` +
                        `Possibile blocco, riavvio o anomalia del singolo nodo.`,
                        activeSubscribers
                    );
                }
            }
        }

        // 5. Scrittura con failover automatico (Primary -> Backup)
        const saveRes = await cluster.saveState(state);

        return res.status(200).json({
            status: 'ok',
            node,
            timestamp: now,
            active_node: stateData.activeNode,
            written_nodes: saveRes.writtenNodes,
            has_redundancy: cluster.hasRedundancy(),
            recovered_events: recoveredEventsForResponse
        });
    } catch (err) {
        if (cluster.isFailoverError(err)) {
            console.warn('[REDIS FAILOVER] Tutti i database Redis hanno superato la quota mensile.');
            return res.status(429).json({
                status: 'quota_exceeded',
                warning: 'Tutti i database Redis configurati hanno superato la quota mensile (500k). Reset previsto al 1° del mese.',
                timestamp: Date.now()
            });
        }

        console.error('Errore esecuzione heartbeat:', err);
        return res.status(500).json({ error: 'Errore interno Redis', details: err.message });
    }
};
