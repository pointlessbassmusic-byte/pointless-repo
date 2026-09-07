# pointless-repo

Master holding repo for both trading/prediction engines. This is now the **single source of truth** —
develop on the laptop, push here, deploy to the Linode server with one command.

## Projects

| Project | Path | Target platform | Master file |
|---|---|---|---|
| Sports Betting Bot | [`polymarket-bot/`](polymarket-bot/) | Polymarket (sports events) | [`polymarket-bot/PROJECT.md`](polymarket-bot/PROJECT.md) |
| Predictions Engine / Substrate | [`kalshi-engine/`](kalshi-engine/) | Kalshi (event contracts) | [`kalshi-engine/PROJECT.md`](kalshi-engine/PROJECT.md) |
| Arbitrage Scanner (detect-only) | [`arb-scanner/`](arb-scanner/) | Polymarket ↔ Kalshi cross-platform | [`arb-scanner/config.yaml`](arb-scanner/config.yaml) |

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

## Arbitrage scanner

`arb-scanner/` matches the same prediction across Polymarket and Kalshi and reports
locked-in complement arbs (buy YES on one platform + NO on the other for < $1 after fees)
plus same-platform YES+NO bundles. **Detection only — it never places orders**, and
implausibly large cross-platform "edges" are quarantined as `suspect_match` (they almost
always mean the two questions are not actually the same). Every candidate needs a human
to confirm both markets resolve identically before trading it.

```bash
cd arb-scanner && python -m src.main --once
```

## Risk gate

Both engines check a risk gate before placing any live order:
- `touch data/KILL_SWITCH` halts live trading immediately (orders record as `blocked:`)
- a daily realized-loss circuit breaker (`risk.max_daily_loss_usd`) blocks new live
  orders for the rest of the day once settled losses cross the limit

## Claude Code skills

`.claude/skills/` ships repo-specific skills any Claude Code session here can use:
`pre-live-gate` (checklist gate before flipping an engine to live) and `engine-health`
(standard diagnostics + how to read them).

## Upstream inspiration

Concepts adapted from the user's forks: [ImMike/polymarket-arbitrage](https://github.com/ImMike/polymarket-arbitrage)
(cross-platform matching, fee-aware arb math, risk manager; reimplemented from scratch) and
[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) (MIT —
circuit-breaker and pre-trade-gate skill patterns, adapted for prediction markets).

## Calibration report

After the engines have been scanning for a while, check whether the models beat the market:

```bash
cd polymarket-bot && python -m src.report   # or --days 14
cd kalshi-engine  && python -m src.report
```

The Kalshi engine can also replay all recorded price history through the substrate
offline — tune generator parameters in `config.yaml` and re-score instantly:

```bash
cd kalshi-engine && python -m src.backtest
```
