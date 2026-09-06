#!/usr/bin/env bash
# Start (or restart) the API in a tmux session called "dotless".
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
tmux kill-session -t dotless 2>/dev/null || true
tmux new -d -s dotless "cd $(pwd) && . .venv/bin/activate && [ -f .env ] && set -a && . ./.env && set +a; uvicorn app:app --host 0.0.0.0 --port 8000 2>&1 | tee -a server.log"
sleep 2
curl -s localhost:8000/health && echo && echo "running in tmux session 'dotless'  (tmux attach -t dotless)"
