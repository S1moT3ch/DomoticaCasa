const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const axios = require('axios');
require('dotenv').config();

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3000;
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID;
const HEARTBEAT_SECRET = process.env.HEARTBEAT_SECRET || 'SecretHeartbeatKey2026';
const TIMEOUT_SECONDS = parseInt(process.env.TIMEOUT_SECONDS || '90', 10);

let lastHeartbeatTime = Date.now();
let isPowerOutageAlertSent = false;

// Funzione per inviare messaggi su Telegram
async function sendTelegramAlert(message) {
    if (!TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID) {
        console.error('[!] Telegram Bot Token o Chat ID non configurati!');
        return;
    }
    const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`;
    try {
        await axios.post(url, {
            chat_id: TELEGRAM_CHAT_ID,
            text: message,
            parse_mode: 'Markdown'
        });
        console.log('[+] Notifica Telegram inviata con successo');
    } catch (err) {
        console.error('[!] Errore invio notifica Telegram:', err.message);
    }
}

// 1. Endpoint HTTP Ping / Heartbeat (per HTTP Keep-Alive)
app.post('/api/heartbeat', (req, res) => {
    const token = req.headers['x-heartbeat-secret'] || req.body.secret;
    if (token !== HEARTBEAT_SECRET) {
        return res.status(401).json({ error: 'Unauthorized' });
    }

    lastHeartbeatTime = Date.now();
    console.log(`[${new Date().toISOString()}] Heartbeat HTTP ricevuto da casa.`);

    // Se l'allarme era attivo ed ora il server risponde, invia notifica di ripristino
    if (isPowerOutageAlertSent) {
        isPowerOutageAlertSent = false;
        sendTelegramAlert(
            "🟢 *RIPRISTINO CORRENTE ELETTRICA*\n\n" +
            "⚡ La rete elettrica ed il server domotico di casa sono di nuovo **ONLINE** e funzionanti!"
        );
    }

    return res.json({ status: 'ok', timestamp: lastHeartbeatTime });
});

// Endpoint per verificare lo stato del server monitor
app.get('/health', (req, res) => {
    const secondsSinceLast = Math.round((Date.now() - lastHeartbeatTime) / 1000);
    res.json({
        status: 'running',
        lastHeartbeatSecondsAgo: secondsSinceLast,
        isPowerOutageAlertSent: isPowerOutageAlertSent
    });
});

// Creazione Server HTTP e WebSocket
const server = http.createServer(app);
const wss = new WebSocket.Server({ server, path: '/ws/heartbeat' });

wss.on('connection', (ws, req) => {
    console.log('[+] Nuova connessione WebSocket dal server domotico');

    ws.on('message', (message) => {
        try {
            const data = JSON.parse(message);
            if (data.secret === HEARTBEAT_SECRET) {
                lastHeartbeatTime = Date.now();
                console.log(`[${new Date().toISOString()}] Heartbeat WS ricevuto.`);

                if (isPowerOutageAlertSent) {
                    isPowerOutageAlertSent = false;
                    sendTelegramAlert(
                        "🟢 *RIPRISTINO CORRENTE ELETTRICA*\n\n" +
                        "⚡ La rete elettrica ed il server domotico di casa sono di nuovo **ONLINE**!"
                    );
                }
                ws.send(JSON.stringify({ status: 'ack', time: Date.now() }));
            }
        } catch (e) {
            console.error('[!] Errore parsing messaggio WS:', e.message);
        }
    });

    ws.on('close', () => {
        console.log('[!] Connessione WebSocket chiusa');
    });
});

// 2. Watchdog Timer (Controllo continuo ogni 10 secondi)
setInterval(() => {
    const elapsedSeconds = (Date.now() - lastHeartbeatTime) / 1000;

    if (elapsedSeconds > TIMEOUT_SECONDS && !isPowerOutageAlertSent) {
        isPowerOutageAlertSent = true;
        console.warn(`[⚠️] ASSENZA DI CORRENTE DETECTED! Nessun heartbeat da ${Math.round(elapsedSeconds)} secondi.`);
        
        sendTelegramAlert(
            "⚡ *ALLARME BLACKOUT - ASSENZA DI CORRENTE!*\n\n" +
            `🚨 ATTENZIONE: Il server domotico non invia il segnale di vita (heartbeat) da oltre *${Math.round(elapsedSeconds)} secondi*.\n\n` +
            "🔌 Probabile blackout o interruzione della rete elettrica in casa."
        );
    }
}, 10000);

server.listen(PORT, () => {
    console.log(`=======================================================`);
    console.log(`  POWER MONITOR & BLACKOUT ALERT SERVER STARTED`);
    console.log(`  Listening on port: ${PORT}`);
    console.log(`  Timeout soglia blackout: ${TIMEOUT_SECONDS} secondi`);
    console.log(`=======================================================`);
});
