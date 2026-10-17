const { RedisClusterManager } = require('../_redis_manager');
const axios = require('axios');

const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
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

    const cluster = new RedisClusterManager();
    if (!cluster.isConfigured()) {
        return res.status(500).json({
            error: 'Vercel KV / Upstash Redis Non Configurato',
            help: 'Aggiungi KV Storage su Vercel per abilitare il watchdog.'
        });
    }

    try {
        const now = Date.now();

        // 1. Lettura COMPATTA dello stato con failover e selezione dello stato PIÙ RECENTE
        let stateData = null;
        try {
            stateData = await cluster.getState();
        } catch (readErr) {
            if (cluster.isFailoverError(readErr)) {
                return res.status(429).json({
                    status: 'quota_exceeded',
                    warning: 'Tutti i database Redis configurati hanno superato la quota mensile (500k). Reset previsto al 1° del mese.',
                    timestamp: now
                });
            }
            throw readErr;
        }

        let state = stateData.state || {};

        if (!state.nodes) state.nodes = {};
        if (!state.alerts) state.alerts = {};
        if (!state.recent_outage_events) state.recent_outage_events = [];
        if (!state.unreported_events_for_server) state.unreported_events_for_server = [];

        const activeSubscribers = state.active_subscribers || (TELEGRAM_CHAT_ID ? [TELEGRAM_CHAT_ID] : []);

        // 2. Se la chiamata proviene dal router/firewall (cron di OpenWrt tramite curl o parametro node=firewall)
        const queryNode = String(req.query?.node || '').toLowerCase();
        const userAgent = String(req.headers['user-agent'] || '').toLowerCase();
        const isFromRouter = queryNode === 'firewall' || userAgent.startsWith('curl');

        let stateChanged = false;
        if (isFromRouter) {
            state.nodes.firewall = {
                ...(state.nodes.firewall || {}),
                last_heartbeat: now
            };
            if (!state.last_heartbeat || now > state.last_heartbeat) {
                state.last_heartbeat = now;
            }
            stateChanged = true;
        }

        const lastHeartbeat = state.last_heartbeat || null;
        const lastServer = state.nodes.server?.last_heartbeat || null;
        const lastFirewall = state.nodes.firewall?.last_heartbeat || null;

        const isOutageAlertActive = !!state.alerts.outage;
        const isServerAlertActive = !!state.alerts.server;
        const isFirewallAlertActive = !!state.alerts.firewall;

        let eventsSent = [];

        // Funzione per calcolare lo stato dettagliato e il timestamp dell'ultimo Heartbeat di ciascun nodo
        function calcNodeStatus(lastTs) {
            if (!lastTs) {
                return {
                    status: 'unknown',
                    lastHeartbeat: null,
                    lastHeartbeatIso: null,
                    lastHeartbeatTime: "Mai ricevuto",
                    elapsedSeconds: null,
                    elapsedTime: "N/D"
                };
            }
            const ts = parseInt(lastTs, 10);
            const sec = Math.max(0, Math.round((now - ts) / 1000));
            const d = new Date(ts);
            return {
                status: sec > TIMEOUT_SECONDS ? 'offline' : 'online',
                lastHeartbeat: ts,
                lastHeartbeatIso: d.toISOString(),
                lastHeartbeatTime: d.toLocaleString('it-IT', { timeZone: 'Europe/Rome' }),
                elapsedSeconds: sec,
                elapsedTime: `${sec}s`
            };
        }

        const serverNode = calcNodeStatus(lastServer);
        const firewallNode = calcNodeStatus(lastFirewall);

        // Calcolo differenze temporali globali
        const globalElapsedSeconds = lastHeartbeat ? Math.max(0, Math.round((now - parseInt(lastHeartbeat, 10)) / 1000)) : null;
        const serverTimedOut = serverNode.status === 'offline';
        const firewallTimedOut = firewallNode.status === 'offline';
        const bothTimedOut = (serverTimedOut && firewallTimedOut) || (globalElapsedSeconds !== null && globalElapsedSeconds > TIMEOUT_SECONDS && !lastServer && !lastFirewall);

        // SCENARIO 1: BLACKOUT TOTALE
        if (bothTimedOut) {
            if (!isOutageAlertActive) {
                state.alerts.outage = true;
                state.alerts.server = true;
                state.alerts.firewall = true;

                const startedAt = lastHeartbeat ? parseInt(lastHeartbeat, 10) : (now - TIMEOUT_SECONDS * 1000);
                state.outage_started_at = startedAt;
                state.server_down_started_at = startedAt;
                state.firewall_down_started_at = startedAt;
                stateChanged = true;

                await sendTelegramAlert(
                    "🚨 *ALLARME BLACKOUT / ASSENZA CORRENTE!*\n\n" +
                    `⚠️ Nessun segnale di vita (heartbeat) ricevuto da *CASA* da oltre *${globalElapsedSeconds || TIMEOUT_SECONDS} secondi*.\n\n` +
                    "• *Home Server (NVR/Bot)*: 🔴 OFFLINE\n" +
                    "• *Firewall / Gateway*: 🔴 OFFLINE\n\n" +
                    "I nodi principali sono entrambi disconnessi: probabile interruzione generale della corrente elettrica o guasto linea.",
                    activeSubscribers
                );
                eventsSent.push('blackout_alert');
            }
        } else {
            // RIPRISTINO AUTOMATICO DA BLACKOUT (se era attivo ma ora uno o entrambi i nodi sono online)
            if (isOutageAlertActive) {
                state.alerts.outage = false;
                state.alerts.server = serverTimedOut;
                state.alerts.firewall = firewallTimedOut;
                delete state.outage_started_at;
                stateChanged = true;

                await sendTelegramAlert(
                    "🟢 *RIPRISTINO CORRENTE ELETTRICA*\n\n" +
                    "⚡ La rete elettrica ed i nodi domotici di casa sono di nuovo **ONLINE** e comunicanti!",
                    activeSubscribers
                );
                eventsSent.push('blackout_recovered_alert');
            }

            // SCENARIO 2: DOWN SINGOLO HOME SERVER
            if (serverTimedOut && !isServerAlertActive && !isOutageAlertActive) {
                state.alerts.server = true;
                state.server_down_started_at = lastServer ? parseInt(lastServer, 10) : (now - TIMEOUT_SECONDS * 1000);
                stateChanged = true;

                await sendTelegramAlert(
                    "⚠️ *NODO DOMOTICO OFFLINE: HOME SERVER*\n\n" +
                    `🚨 ATTENZIONE: Il nodo *Home Server (NVR/Bot)* non invia heartbeat da oltre *${serverNode.elapsedSeconds} secondi*.\n\n` +
                    "• *Home Server (NVR/Bot)*: 🔴 OFFLINE\n" +
                    "• *Firewall / Gateway*: 🟢 ONLINE\n\n" +
                    "Il router comunica regolarmente con il Cloud: la connessione Internet è attiva. Il problema riguarda solo il server locale.",
                    activeSubscribers
                );
                eventsSent.push('server_down_alert');
            }

            // SCENARIO 3: DOWN SINGOLO FIREWALL
            if (firewallTimedOut && !isFirewallAlertActive && !isOutageAlertActive) {
                state.alerts.firewall = true;
                state.firewall_down_started_at = lastFirewall ? parseInt(lastFirewall, 10) : (now - TIMEOUT_SECONDS * 1000);
                stateChanged = true;

                await sendTelegramAlert(
                    "⚠️ *NODO DOMOTICO OFFLINE: FIREWALL*\n\n" +
                    `🚨 ATTENZIONE: Il nodo *Firewall / Gateway* non invia heartbeat da oltre *${firewallNode.elapsedSeconds} secondi*.\n\n` +
                    "• *Home Server (NVR/Bot)*: 🟢 ONLINE\n" +
                    "• *Firewall / Gateway*: 🔴 OFFLINE\n\n" +
                    "L'Home Server comunica regolarmente: possibile riavvio o anomalia del router/gateway.",
                    activeSubscribers
                );
                eventsSent.push('firewall_down_alert');
            }

            // RIPRISTINO AUTONOMO DEL FIREWALL
            if (!firewallTimedOut && isFirewallAlertActive && !isOutageAlertActive) {
                state.alerts.firewall = false;
                const startedAt = state.firewall_down_started_at || (now - TIMEOUT_SECONDS * 1000);
                const durationSec = Math.max(1, Math.round((now - startedAt) / 1000));
                delete state.firewall_down_started_at;
                stateChanged = true;

                const fwEvent = {
                    type: 'firewall_down',
                    node: 'firewall',
                    description: 'Down Firewall / Gateway',
                    started_at: startedAt,
                    started_at_iso: new Date(startedAt).toISOString(),
                    ended_at: now,
                    ended_at_iso: new Date(now).toISOString(),
                    duration_seconds: durationSec
                };

                state.recent_outage_events.unshift(fwEvent);
                if (state.recent_outage_events.length > 20) {
                    state.recent_outage_events = state.recent_outage_events.slice(0, 20);
                }
                state.unreported_events_for_server.push(fwEvent);

                await sendTelegramAlert(
                    "🟢 *NODO RIPRISTINATO: FIREWALL*\n\n" +
                    "✅ Il nodo *Firewall / Gateway* è di nuovo **ONLINE** e comunica regolarmente.\n" +
                    `⏱️ _Durata disconnessione_: *${durationSec} secondi*`,
                    activeSubscribers
                );
                eventsSent.push('firewall_recovered_alert');
            }
        }

        // Se lo stato è cambiato, salviamo con failover
        if (stateChanged) {
            await cluster.saveState(state);
        }

        const overallStatus = bothTimedOut ? 'blackout' : ((serverTimedOut || firewallTimedOut) ? 'partial_offline' : 'ok');
        const globalDate = lastHeartbeat ? new Date(parseInt(lastHeartbeat, 10)) : null;

        return res.status(200).json({
            status: overallStatus,
            isOutageAlertActive: bothTimedOut || isOutageAlertActive,
            eventsSent,
            lastHeartbeat: lastHeartbeat ? parseInt(lastHeartbeat, 10) : null,
            lastHeartbeatIso: globalDate ? globalDate.toISOString() : null,
            lastHeartbeatTime: globalDate ? globalDate.toLocaleString('it-IT', { timeZone: 'Europe/Rome' }) : "Mai ricevuto",
            elapsedSeconds: globalElapsedSeconds,
            elapsedTime: globalElapsedSeconds !== null ? `${globalElapsedSeconds}s` : "N/D",
            nodes: {
                server: serverNode,
                firewall: firewallNode
            },
            alerts: {
                outage: bothTimedOut || isOutageAlertActive,
                server: serverTimedOut || isServerAlertActive,
                firewall: firewallTimedOut || isFirewallAlertActive
            },
            active_node: stateData.activeNode,
            has_redundancy: cluster.hasRedundancy(),
            recent_outage_events: state.recent_outage_events || []
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

        console.error('Errore check-heartbeat:', err);
        return res.status(500).json({ error: 'Errore interno Redis', details: err.message });
    }
};
