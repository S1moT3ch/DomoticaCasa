# Sistema Monitor MQTT -> Telegram Bot

Un servizio leggero ed efficiente in Python situato nel percorso `~/www/domotica/bot/` (`z:\www\domotica\bot\`) per tracciare ed inviare lo stato di tutti i dispositivi domotici (Shelly, Zigbee2MQTT, Tasmota, Home Assistant) sul tuo Bot Telegram.

---

## 📌 1. Token Telegram & Chat ID

Il file `.env` situato in `z:\www\domotica\bot\.env` contiene la tua configurazione:

```env
TELEGRAM_BOT_TOKEN=8997206889:AAG...
TELEGRAM_CHAT_ID=283084390

MQTT_BROKER=localhost
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=

MQTT_TOPIC=#
```

---

## 🤖 2. Comandi Telegram (Interfaccia User-Friendly)

- 🔌 **/shelly** : Mostra un report specifico per tutti i dispositivi Shelly con:
  - 🟢 **ACCESO** / 🔴 **SPENTO** per ciascun relè/canale
  - ⚡ Consumo in Watt (`14.5 W`) se supportato dalla presa/relè
  - 📊 Riepilogo complessivo (es. *2 Accesi | 1 Spento | 1850 W Totali*)
  - 🕒 Orario dell'ultimo aggiornamento ricevuto da MQTT
- 📊 **/stato** : Dashboard domotica completa (Shelly + sensori temperatura + altri dispositivi).
- 📱 **/dispositivi** : Elenco di tutti i topic MQTT attivi ricevuti dal broker.
- ❓ **/aiuto** : Guida ai comandi.

---

## 🚀 3. Come Avviare il Servizio

### Su Linux / OpenWrt:
```bash
cd ~/www/domotica/bot
chmod +x start.sh
./start.sh
```

### Su Windows (Da Terminale o Doppio Clic):
Doppio clic su **`start.bat`** oppure:
```cmd
python z:\www\domotica\bot\main.py
```
