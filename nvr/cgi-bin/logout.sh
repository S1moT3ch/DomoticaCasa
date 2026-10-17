#!/bin/sh
# Cancella il cookie impostando la scadenza immediata
echo "Set-Cookie: nvr_session=expired; Path=/; Max-Age=0; HttpOnly"
# Reindirizza l'utente alla pagina di login
echo "Status: 302 Moved Temporarily"
echo "Location: login.sh"
echo ""