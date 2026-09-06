#!/usr/bin/env bash
# Push docs/chat-imports/ to the server without a full deploy. Run from repo root:
#   ./scripts/sync_chats.sh
set -euo pipefail
SERVER="${SERVER:-root@97.107.138.196}"
rsync -avz docs/chat-imports/ "$SERVER:/opt/pointless-repo/docs/chat-imports/"
echo "chat imports synced to server"
