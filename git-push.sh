#!/bin/sh
# Script di sincronizzazione Git -> GitHub con avanzamento sempre attivo di default
cd /www || exit 1

# Definizione colori per terminale ANSI
CYAN='\033[1;36m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
RED='\033[1;31m'
BOLD='\033[1m'
NC='\033[0m' # Reset colore

printf "\n${CYAN}========================================================${NC}\n"
printf "${BOLD}   🔄 SINCRONIZZAZIONE PROGETTO DOMOTICA ➔ GITHUB${NC}\n"
printf "${CYAN}========================================================${NC}\n"

# [1/4] Controllo stato modifiche
printf "${YELLOW}🔍 [1/4] Controllo modifiche locali in corso...${NC}\n"
STATUS_OUTPUT=$(git status --porcelain)

if [ -n "$STATUS_OUTPUT" ]; then
    printf "\n${BOLD}📋 [2/4] File modificati o aggiunti da inviare:${NC}\n"
    git status -s
    printf "\n"

    printf "${YELLOW}📦 [3/4] Aggiunta file all'archivio e creazione commit...${NC}\n"
    git add -A
    MSG="${1:-Update domotica: $(date '+%d/%m/%Y %H:%M:%S')}"
    git commit -m "$MSG"
    printf "${GREEN}✔ Commit locale creato con successo.${NC}\n"
else
    printf "${GREEN}✔ Nessun nuovo file da indicizzare. Procedo direttamente con la sincronizzazione remota.${NC}\n"
    MSG="${1:-Verifica sincronizzazione}"
fi

COMMIT_HASH=$(git rev-parse --short HEAD)

# [4/4] Caricamento su GitHub (SEMPRE ATTIVO DI DEFAULT)
printf "\n${YELLOW}🚀 [4/4] Caricamento / Verifica su GitHub in corso (git push)...${NC}\n"
git push --progress origin main

if [ $? -eq 0 ]; then
    printf "\n${GREEN}========================================================${NC}\n"
    printf "${GREEN}🎉 SINCRONIZZAZIONE CON GITHUB COMPLETATA CON SUCCESSO!${NC}\n"
    printf "${GREEN}========================================================${NC}\n"
    printf "${BOLD}📌 Hash Commit :${NC} %s\n" "$COMMIT_HASH"
    printf "${BOLD}💬 Messaggio   :${NC} %s\n" "$MSG"
    printf "${BOLD}🌐 Link Commit :${NC} https://github.com/S1moT3ch/DomoticaCasa/commit/%s\n" "$COMMIT_HASH"
    printf "${GREEN}========================================================${NC}\n\n"
else
    printf "\n${RED}========================================================${NC}\n"
    printf "${RED}❌ ERRORE: Impossibile completare il caricamento su GitHub.${NC}\n"
    printf "${RED}Verifica la connessione internet o i permessi della chiave SSH.${NC}\n"
    printf "${RED}========================================================${NC}\n\n"
    exit 1
fi
