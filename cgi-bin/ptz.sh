#!/bin/sh
# Controllo del Cookie di sessione
if ! echo "$HTTP_COOKIE" | grep -q "nvr_session=autorizzato"; then
    echo "Status: 302 Moved Temporarily"
    echo "Location: login.sh"
    echo ""
    exit
fi
export PATH=/usr/sbin:/usr/bin:/sbin:/bin

# 1. Caricamento parametri
[ -z "$QUERY_STRING" ] && QUERY_STRING="$1"
eval $(echo "$QUERY_STRING" | sed 's/&/ /g')

# 2. Configurazione Credenziali e Logica Assi
# STEP definisce l'ampiezza del movimento (0.1 = piccolo, 0.2 = medio)
STEP=0.1

# Caricamento credenziali da file protetto locale (escluso da git)
SECRETS_FILE="/www/cgi-bin/.nvr_secrets"
[ -f "$SECRETS_FILE" ] && . "$SECRETS_FILE"

if [ "$cam" = "1" ]; then
    # CAMERA 1: Montata dritta (Standard)
    IP="${CAM1_IP:-192.168.4.160}"; USER="${CAM1_USER:-adminHome}"; PASS="$CAM1_PASS"
    INV_X=-1; INV_Y=1
elif [ "$cam" = "2" ]; then
    # CAMERA 2: Standard
    IP="${CAM2_IP:-192.168.3.130}"; USER="${CAM2_USER:-adminBox}"; PASS="$CAM2_PASS"
    INV_X=1; INV_Y=-1
elif [ "$cam" = "3" ]; then
    # CAMERA 3: Montata a testa in giù (Assi invertiti)
    IP="${CAM3_IP:-192.168.4.161}"; USER="${CAM3_USER:-adminHomeCorridoio}"; PASS="$CAM3_PASS"
    INV_X=1; INV_Y=-1
fi

gen_auth() {
    CREATED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    NONCE_RAW=$(head -c 12 /dev/urandom)
    NONCE=$(printf '%s' "$NONCE_RAW" | openssl enc -base64)
    PASS_DIGEST=$( (printf '%s' "$NONCE_RAW"; printf '%s' "$CREATED"; printf '%s' "$PASS") | openssl dgst -sha1 -binary | openssl enc -base64)
}

if [ -n "$IP" ]; then
    gen_auth
    
    # Calcolo assi con i moltiplicatori di orientamento
    case "$move" in
        up)    X=0; Y=$(awk "BEGIN {print $STEP * $INV_Y}") ;;
        down)  X=0; Y=$(awk "BEGIN {print -$STEP * $INV_Y}") ;;
        left)  X=$(awk "BEGIN {print -$STEP * $INV_X}"); Y=0 ;;
        right) X=$(awk "BEGIN {print $STEP * $INV_X}");  Y=0 ;;
        stop)  X=0; Y=0 ;;
    esac

    BODY="<RelativeMove xmlns=\"http://www.onvif.org/ver20/ptz/wsdl\">
            <ProfileToken>profile_1</ProfileToken>
            <Translation>
                <tt:PanTilt x=\"$X\" y=\"$Y\" xmlns:tt=\"http://www.onvif.org/ver10/schema\"/>
            </Translation>
          </RelativeMove>"

    curl -s --max-time 2 -H "Content-Type: application/soap+xml; charset=utf-8" -d "
<v:Envelope xmlns:v=\"http://www.w3.org/2003/05/soap-envelope\">
  <v:Header>
    <Action xmlns=\"http://www.w3.org/2005/08/addressing\">http://www.onvif.org/ver20/ptz/wsdl/RelativeMove</Action>
    <Security xmlns=\"http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd\">
      <UsernameToken>
        <Username>$USER</Username>
        <Password Type=\"http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest\">$PASS_DIGEST</Password>
        <Nonce EncodingType=\"http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary\">$NONCE</Nonce>
        <Created xmlns=\"http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd\">$CREATED</Created>
      </UsernameToken>
    </Security>
  </v:Header>
  <v:Body>$BODY</v:Body>
</v:Envelope>" "http://$IP:2020/onvif/service" > /dev/null
fi

echo "Content-type: text/plain"
echo ""
echo "OK"
