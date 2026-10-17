# 🚗 Bot Telegram Garage: AllarmeGarage & Serranda (2FA ACSAHomeUtils)

Questo servizio autonomo gestisce in parallelo due Bot Telegram dedicati al monitoraggio e notifica in tempo reale dello stato del Garage:

1. **Bot AllarmeGarage**:
   - Notifica `INSERITO` ogni volta che l'allarme del garage viene inserito.
   - Notifica `DISINSERITO` ogni volta che l'allarme del garage viene disinserito.
   - Risponde con lo stato attuale (`INSERITO` / `DISINSERITO`) inviando `/stato`.

2. **Bot Serranda**:
   - Notifica `APERTA` ogni volta che la serranda del garage viene aperta.
   - Notifica `CHIUSA` ogni volta che la serranda del garage viene chiusa.
   - Risponde con lo stato attuale (`APERTA` / `CHIUSA`) inviando `/stato`.

---

## 🔐 Sicurezza: Password e 2FA tramite Bot ACSAHomeUtils

Entrambi i bot sono protetti da un meccanismo di sicurezza a due livelli:

1. **Password**: Un utente non autorizzato che avvia il bot (`/start` o qualsiasi messaggio) riceve la richiesta di inserimento della password di registrazione.
2. **2FA su ACSAHomeUtils**: Quando l'utente inserisce la password corretta:
   - Viene generata una richiesta di autorizzazione all'Amministratore sul bot **ACSAHomeUtils** con pulsanti inline:
     - `[ Approva ✅ ]`
     - `[ Rifiuta ❌ ]`
   - L'utente rimane in attesa finché l'Amministratore non approva la richiesta.
   - Appena approvato, l'utente riceve la notifica di conferma e viene abilitato a ricevere gli aggiornamenti del bot.
   - Eventuali tentativi con password errata vengono immediatamente segnalati all'amministratore su ACSAHomeUtils.

---

## ⚙️ Configurazione (.env)

Il file di configurazione dedicato si trova in:
`/www/domotica/garage_bot/.env`

Parametri da compilare:

```env
# 1. Token dei due nuovi bot (da @BotFather)
ALLARME_GARAGE_BOT_TOKEN=INSERISCI_QUI_TOKEN_BOT_ALLARME_GARAGE
SERRANDA_BOT_TOKEN=INSERISCI_QUI_TOKEN_BOT_SERRANDA

# 2. Token bot ACSAHomeUtils e Chat ID Admin
ACSA_UTILS_BOT_TOKEN=8805753756:AAFFHH9rc9ikiOyQaPQVMloBRyGl_md_eAw
ADMIN_CHAT_ID=283084390

# 3. Password di registrazione
BOT_PASSWORD=DomoticaPass!2026
```

---

## 🚀 Gestione Servizio di Sistema

### OpenWrt (procd)
Il servizio è registrato come `/etc/init.d/garage-bot`:

```sh
# Avvio del servizio
/etc/init.d/garage-bot start

# Riavvio del servizio
/etc/init.d/garage-bot restart

# Arresto del servizio
/etc/init.d/garage-bot stop

# Abilitazione all'avvio del sistema
/etc/init.d/garage-bot enable

# Visualizzazione log in tempo reale
logread -f | grep -E "AllarmeGarage|Serranda|Garage"
```

### Systemd (Debian / Ubuntu / Raspberry Pi OS)
Il file di unit si trova in `/www/domotica/garage_bot/garage-bot.service`:

```sh
cp /www/domotica/garage_bot/garage-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now garage-bot
systemctl status garage-bot
```
