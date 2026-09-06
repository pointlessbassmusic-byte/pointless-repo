# pointless-repo

Master holding repo for both trading/prediction engines. This is now the **single source of truth** —
develop on the laptop, push here, deploy to the Linode server with one command.

## Projects

| Project | Path | Target platform | Master file |
|---|---|---|---|
| Sports Betting Bot (v3 fair-value) | [`polymarket-bot/`](polymarket-bot/) | Polymarket (sports events) | [`polymarket-bot/PROJECT.md`](polymarket-bot/PROJECT.md) |
| Predictions Engine — Substrate/Echo | [`substrate/`](substrate/) | shadow mode (certification instrument) | [`substrate/PROJECT.md`](substrate/PROJECT.md) |
| Kalshi market-access layer | [`kalshi-engine/`](kalshi-engine/) | Kalshi (event contracts) | [`kalshi-engine/PROJECT.md`](kalshi-engine/PROJECT.md) |

## Repo layout

```
docs/               Master plan, server docs, chat-history imports
polymarket-bot/     Polymarket sports betting bot — v3 fair-value (VPS, pull pending), v2 websocket (v2/), v1 scanner (src/), archive/
substrate/          Substrate/Echo predictions engine (certification instrument, shadow mode)
kalshi-engine/      Kalshi market-access layer (API v2 client + generic trading loop)
deploy/             Server setup + deploy scripts + systemd units
scripts/            Utilities (chat import sync, etc.)
```

## Quick start (laptop)

```bash
git clone https://github.com/pointlessbassmusic-byte/pointless-repo.git
cd pointless-repo

# Polymarket bot
cd polymarket-bot && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys
python -m src.main --dry-run

# Kalshi engine
cd ../kalshi-engine && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys
python -m src.main --dry-run
```

## Deploy to server (Linode Ubuntu, 97.107.138.196)

```bash
./deploy/setup_server.sh    # one-time server bootstrap
./deploy/deploy.sh          # every deploy after that
```

See [`docs/SERVER.md`](docs/SERVER.md) for details, and [`docs/MASTER_PLAN.md`](docs/MASTER_PLAN.md)
for the overall architecture and the migration story (Terminus → laptop workflow).

## Importing old chat history

Past sports-betting / prediction-engine chat sessions can't be pulled automatically — export or paste
them into [`docs/chat-imports/`](docs/chat-imports/) (one markdown file per chat). See
[`docs/CHAT_HISTORY_IMPORT.md`](docs/CHAT_HISTORY_IMPORT.md). They get versioned here and synced to
the server on every deploy.

## Safety defaults

Both engines start in **dry-run mode** — they scan markets, compute edges, and log intended trades
without placing real orders. Live trading requires `live: true` in the config **and** `--live` on the
command line.
