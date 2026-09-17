#!/usr/bin/env bash
# Pull the fair-value bot generation (v3: fv_bot.py / edge_model.py era) from the VPS
# into the repo as polymarket-bot/v3/. Run from the repo root on the laptop:
#   ./scripts/pull_from_server.sh
# Pulls code + tuned params only — never .env, keys, venvs, or bulk data.
set -euo pipefail

SERVER="${SERVER:-root@97.107.138.196}"
SRC='~/sports-bot-v2/'
DEST="polymarket-bot/v3"

mkdir -p "$DEST"
rsync -avz \
  --include='*.py' --include='*.json' --include='*.md' --include='*.txt' \
  --exclude='.env' --exclude='*.pem' --exclude='*.key' \
  --exclude='.venv/' --exclude='data/' --exclude='__pycache__/' --exclude='*' \
  "$SERVER:$SRC" "$DEST/"

echo "Pulled to $DEST. Safety check before committing:"
if grep -rniE "PRIVATE_KEY\s*=\s*['\"0-9a-fx]{20,}|api[_-]?key\s*[=:]\s*['\"][A-Za-z0-9_-]{12,}" "$DEST"; then
  echo "!! Possible embedded secret found above — remove it before committing."
  exit 1
fi
echo "No embedded secrets detected. Review 'git diff' then commit."
