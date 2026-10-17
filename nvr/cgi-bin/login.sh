#!/bin/sh

# --- CONFIGURAZIONE CREDENZIALI ---
SECRETS_FILE="/www/cgi-bin/.nvr_secrets"
[ -f "$SECRETS_FILE" ] && . "$SECRETS_FILE"
VALID_USER="${NVR_USER:-admin}"
VALID_PASS="${NVR_PASS:-SIMANCA!01}"
# ----------------------------------

# Leggiamo i dati inviati dal modulo (POST)
if [ "$METHOD" = "POST" ] || [ "$REQUEST_METHOD" = "POST" ]; then
    read -n "$CONTENT_LENGTH" QUERY_STRING_POST
    
    # Estrazione sicura dei parametri
    USER_INPUT=$(echo "$QUERY_STRING_POST" | sed -n 's/^.*user=\([^&]*\).*$/\1/p' | sed 's/%\([0-9A-F][0-9A-F]\)/\\x\1/g;s/+/ /g' | xargs -0 printf "%b")
    PASS_INPUT=$(echo "$QUERY_STRING_POST" | sed -n 's/^.*pass=\([^&]*\).*$/\1/p' | sed 's/%\([0-9A-F][0-9A-F]\)/\\x\1/g;s/+/ /g' | xargs -0 printf "%b")

    # Verifica credenziali
    if [ "$USER_INPUT" = "$VALID_USER" ] && [ "$PASS_INPUT" = "$VALID_PASS" ]; then
        # Cookie valido per 24 ore
        echo "Set-Cookie: nvr_session=autorizzato; Path=/; Max-Age=86400; HttpOnly"
        echo "Status: 302 Moved Temporarily"
        echo "Location: home.sh"
        echo ""
        exit
    else
        ERROR="Credenziali errate!"
    fi
fi

echo "Content-type: text/html; charset=utf-8"
echo ""

cat <<EOF
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link rel="manifest" href="../manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="NVR">
    <link rel="apple-touch-icon" href="/icon.png">
    <style>
        body { background: #000; color: #fff; font-family: -apple-system, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .login-box { background: #111; padding: 30px; border-radius: 15px; border: 1px solid #333; width: 85%; max-width: 320px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        .logo { font-size: 40px; margin-bottom: 10px; }
        h2 { font-weight: 300; margin-bottom: 25px; letter-spacing: 1px; color: #aaa; }
        input { width: 100%; padding: 14px; margin-bottom: 15px; background: #222; border: 1px solid #444; color: #fff; border-radius: 10px; box-sizing: border-box; font-size: 16px; outline: none; transition: border-color 0.3s; }
        input:focus { border-color: #007bff; }
        button { width: 100%; padding: 14px; background: #007bff; border: none; color: #fff; border-radius: 10px; font-weight: bold; font-size: 16px; cursor: pointer; }
        button:active { transform: scale(0.98); opacity: 0.9; }
        .error { color: #ff3b30; font-size: 13px; margin-top: 15px; background: rgba(255,59,48,0.1); padding: 8px; border-radius: 5px; }
    </style>
</head>
<body>
    <div class="login-box">
        <div class="logo">&#128737;</div>
        <h2>NVR LOGIN</h2>
        <form method="POST">
            <input type="text" name="user" placeholder="Nome Utente" required autocomplete="username">
            <input type="password" name="pass" placeholder="Password" required autocomplete="current-password">
            <button type="submit">ACCEDI</button>
        </form>
        $( [ -n "$ERROR" ] && echo "<div class='error'>$ERROR</div>" )
    </div>
    <script>
  	if ('serviceWorker' in navigator) {
    		window.addEventListener('load', () => {
      		navigator.serviceWorker.register('/sw.js')
        	.then(reg => console.log('Service Worker registrato!', reg))
        	.catch(err => console.error('Errore registrazione SW:', err));
    		});
  	}
</script>
</body>
</html>
EOF