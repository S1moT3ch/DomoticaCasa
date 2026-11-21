const { RedisClusterManager } = require('./_redis_manager');

module.exports = async (req, res) => {
    res.setHeader('Content-Type', 'application/json; charset=utf-8');

    console.log('[ALEXA CONSUMO SKILL] Richiesta ricevuta:', JSON.stringify(req.body));

    const cluster = new RedisClusterManager();
    let powerW = null;

    if (cluster.isConfigured()) {
        try {
            const stateData = await cluster.getState();
            const state = stateData.state;
            if (state) {
                powerW = state.latest_power_w;
            }
            console.log(`[ALEXA CONSUMO SKILL] Dati (nodo ${stateData.activeNode}): power=${powerW}`);
        } catch (e) {
            console.error('[ALEXA CONSUMO SKILL] Errore lettura Redis (possibile quota esaurita):', e.message);
        }
    } else {
        console.error('[ALEXA CONSUMO SKILL] Nessun database Redis configurato');
    }

    let speechText = "";
    if (powerW !== null && powerW !== undefined && !isNaN(parseFloat(powerW))) {
        const pw = Math.round(parseFloat(powerW));
        speechText = `Attualmente il consumo di casa è di ${pw} Watt.`;
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
                title: "Consumo Attuale",
                content: speechText
            },
            shouldEndSession: true
        }
    };

    console.log('[ALEXA CONSUMO SKILL] Risposta inviata ad Alexa:', speechText);
    return res.status(200).json(alexaResponse);
};
