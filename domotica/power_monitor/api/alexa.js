const { RedisClusterManager } = require('./_redis_manager');

module.exports = async (req, res) => {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');

    console.log('[ALEXA SKILL] Richiesta ricevuta:', JSON.stringify(req.body));

    const cluster = new RedisClusterManager();
    let powerW = null;
    let voltageV = null;
    let currentA = null;

    if (cluster.isConfigured()) {
        try {
            const stateData = await cluster.getState();
            const state = stateData.state;
            if (state) {
                powerW = state.latest_power_w;
                voltageV = state.latest_voltage;
                currentA = state.latest_current;
            }
            console.log(`[ALEXA SKILL] Dati (nodo ${stateData.activeNode}): power=${powerW}, v=${voltageV}, a=${currentA}`);
        } catch (e) {
            console.error('[ALEXA SKILL] Errore lettura Redis (possibile quota esaurita):', e.message);
        }
    } else {
        console.error('[ALEXA SKILL] Nessun database Redis configurato');
    }

    let speechText = "";
    if (powerW !== null && powerW !== undefined && !isNaN(parseFloat(powerW))) {
        const pw = Math.round(parseFloat(powerW));
        const v = (voltageV && !isNaN(parseFloat(voltageV))) ? parseFloat(voltageV).toFixed(0) : "225";
        const a = (currentA && !isNaN(parseFloat(currentA))) ? parseFloat(currentA).toFixed(1) : (pw / 225).toFixed(1);
        speechText = `Attualmente il consumo di casa è di ${pw} Watt, con una tensione di ${v} Volt e assorbimento di ${a} Ampere.`;
    } else {
        speechText = "Il monitoraggio remoto dei consumi è temporaneamente non disponibile. Verifica lo stato dal bot Telegram.";
    }

    // Risposta standard Alexa Skill Kit (supporta sia SSML che PlainText)
    const alexaResponse = {
        version: "1.0",
        response: {
            outputSpeech: {
                type: "SSML",
                ssml: `<speak>${speechText}</speak>`
            },
            card: {
                type: "Simple",
                title: "Consumi Casa",
                content: speechText
            },
            shouldEndSession: true
        }
    };

    console.log('[ALEXA SKILL] Risposta inviata ad Alexa:', speechText);
    return res.status(200).json(alexaResponse);
};
