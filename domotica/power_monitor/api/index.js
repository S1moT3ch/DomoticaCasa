module.exports = (req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.status(200).send(JSON.stringify({
        status: 'online',
        service: 'Power Outage Monitor & Telegram Watchdog',
        timestamp: new Date().toISOString(),
        endpoints: {
            heartbeat: 'POST /api/heartbeat',
            alexa: 'POST /api/alexa (Alexa Skill Webhook)',
            cronCheck: 'GET /api/cron/check-heartbeat'
        }
    }, null, 2));

};
