# pointless-repo

Master holding repo for both trading/prediction engines. This is now the **single source of truth** —
develop on the laptop, push here, deploy to the Linode server with one command.

## Projects

| Project | Path | Target platform | Master file |
|---|---|---|---|
| Sports Betting Bot | [`polymarket-bot/`](polymarket-bot/) | Polymarket (sports events) | [`polymarket-bot/PROJECT.md`](polymarket-bot/PROJECT.md) |
| Predictions Engine / Substrate | [`kalshi-engine/`](kalshi-engine/) | Kalshi (event contracts) | [`kalshi-engine/PROJECT.md`](kalshi-engine/PROJECT.md) |

## Repo layout

```
docs/               Master plan, server docs, chat-history imports
polymarket-bot/     Polymarket sports betting bot (Python)
kalshi-engine/      Kalshi predictions engine + signal substrate (Python)
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
command line. In live mode an engine never re-orders a market it already has a placed order on, and
open exposure recorded in the database counts against `max_total_exposure` across restarts.

## Calibration report

After the engines have been scanning for a while, check whether the models beat the market:

```bash
cd polymarket-bot && python -m src.report   # or --days 14
cd kalshi-engine  && python -m src.report
```
