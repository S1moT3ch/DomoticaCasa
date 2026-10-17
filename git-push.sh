#!/bin/sh
# Script di sincronizzazione rapida Git -> GitHub
cd /www || exit 1

echo "🔍 Verifica modifiche in corso..."
if [ -z "$(git status --porcelain)" ]; then
    echo "✅ Nessuna modifica da inviare."
    exit 0
fi

echo "📦 Aggiunta file modificati..."
git add .

MSG="${1:-Update domotica: $(date '+%Y-%m-%d %H:%M:%S')}"
echo "📝 Commit: $MSG"
git commit -m "$MSG"

echo "🚀 Invio su GitHub..."
git push origin main

if [ $? -eq 0 ]; then
    echo "🎉 Sincronizzazione con GitHub completata con successo!"
else
    echo "❌ Errore durante il push su GitHub."
    exit 1
fi
