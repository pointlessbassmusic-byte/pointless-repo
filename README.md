# pointless-repo · sportsbot

Consolidated **sports prediction engine + exchange trading bot** in Python.

Two deliverables on one substrate:

1. **Polymarket sports bot** — scans tennis, MLB baseball, and table tennis
   moneyline markets, prices them with the prediction engine, and places
   risk-managed limit orders. **Paper mode by default** with real market
   data and conservative simulated fills.
2. **Prediction engine / substrate model** — per-sport models behind one
   interface producing calibrated probabilities. The **Kalshi** client
   implements the same `ExchangeClient` contract (2026 API: dollar-string
   prices, Create-Order-V2, RSA-PSS auth), so switching venues is a config
   change — this is the US-legal live path.

Built against the **current (Sept 2026) APIs**, live-verified:
Gamma tag discovery, CLOB V2 books, `polymarket-client` py-sdk (the old
`py-clob-client` is archived and non-functional), Kalshi
`/portfolio/events/orders`, MLB Stats API, Sackmann tennis datasets.

## Quick start

```bash
git clone https://github.com/pointlessbassmusic-byte/pointless-repo.git
cd pointless-repo
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
cp .env.example .env            # add keys later; not needed for paper mode

sportsbot fit baseball          # train team Elo from MLB Stats API (~2 min)
sportsbot fit tennis            # train player Elo from Sackmann ATP/WTA data
sportsbot fit table_tennis      # bootstrap from resolved Polymarket markets

sportsbot scan                  # live markets vs model — no orders
sportsbot backtest tennis       # walk-forward evaluation vs benchmarks
sportsbot run                   # paper-trading loop
sportsbot status                # exposure, PnL, CLV, calibration, kill switch
```

Server deployment (systemd, hardening, cron): see
[`deploy/DEPLOYMENT.md`](deploy/DEPLOYMENT.md). Architecture and the trading
pipeline: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## What the models are

| sport | model | key parameters (from the 2024-26 literature) |
|---|---|---|
| tennis | surface-blended Elo → Markov chain | K = 250/(n+5)^0.4; 50/50 overall/surface blend; WElo margin-of-victory; O'Malley point→game→set→match DP for best-of-3↔5 translation |
| baseball | team Elo + SP overlay | K=4, home +24 Elo, MOV damping, ⅓ season reversion, online starting-pitcher adjustment (probable pitchers from MLB Stats API) |
| table tennis | high-frequency Elo → set Markov | K = 40/√(1+n/30) floor 14; inactivity-widened uncertainty; best-of-5↔7 via race-to-11 chain |

## What makes it go-live-ready (and honest)

- **Market-prior blending**: bets on `0.3·model + 0.7·market` — the market
  is the prior; the model must disagree hard to trigger a bet.
- **Quarter-Kelly** sizing under per-market / per-sport / total / daily
  caps; maker-first execution inside the spread; book-walking fill prices;
  never more than 25% of visible depth.
- **Risk layer fails closed**: drawdown kill switch, daily loss limit,
  stale-quote guard, pre-match cutoff, rolling-calibration pause.
- **Position management, never loss-chasing** (`bot/positions.py`): open
  positions are re-priced every cycle — an edge reversal or a hard stop
  (sellable value < 50% of cost) closes them; drawdown scales Kelly *down*
  toward a floor (anti-martingale); a sport whose rolling CLV goes negative
  gets a higher edge bar and smaller caps until it recovers. Reversal only
  happens when the opposite side independently clears the normal entry bar.
- **CLV tracking from day one** — closing-line value is the earliest true
  signal of edge; the go-live gate in DEPLOYMENT.md is CLV-based.
- **Conservative entity matching**: a market whose participants can't be
  confidently matched to rated players is skipped, never guessed.
- **Arb scanner** (consolidated from the `polymarket-arbitrage` fork's
  strategy): same-book bundle arb and Polymarket↔Kalshi cross-venue arb
  detection, logged for review.

**Honest expectations** (from the research baked into `docs/`): headline
moneyline markets are near-efficient; realistic targets are ~0.60-0.63 log
loss in tennis and 0.66-0.68 in MLB, with edge hunted in softer corners
(early lines, WTA/Challengers, table-tennis leagues) — and table-tennis fast
leagues carry documented match-fixing risk, so they get stricter thresholds.

## Also in this repo

- **`substrate/`** — the Substrate/Echo prediction-certification engine
  (consolidated from its own project handoff, verbatim; see
  `substrate/HANDOFF.md` and the frozen `substrate/PROTOCOL_v1.md`).
  QRNG scheduler → commit-reveal ledger → triple-null scoring →
  anytime-valid e-process gate → Hedge fusion. **Shadow mode by protocol:
  nothing in it stakes money.** The bridge lives in
  `sportsbot/substrate_bridge/`:
  - `sportsbot substrate-export` — bot predictions/outcomes → substrate
    ingest schema (milestone 1: real-history backtest feed).
  - `sportsbot weather-snapshot [--loop 3600]` — Kalshi weather-dailies
    decision-time snapshot service, read-only (milestone 2).
  - `substrate/arv_cli.py` — ARV session runner (milestone 3): real image
    pool, sealed double-blind open→transcribe→judge→resolve workflow,
    SQLite CommitLedger persistence, pre-registered 20% feedback ablation
    (`python3 substrate/arv_cli.py --self-test`).
  - `substrate/dashboard.py` — dashboard (milestone 4): self-contained
    HTML with per-arm e-process wealth curves, Hedge fusion weights, score
    tables, and trial counts, built from the ingest CSVs and the ARV
    ledger; `--loop 300` regenerates with auto-refresh
    (`python3 substrate/dashboard.py --self-test`). One-shot from the bot
    side: `sportsbot dashboard` exports events and builds the HTML in one
    command. Real-data snapshots live in `substrate/reports/` — on the
    first settled weather cohort the triple null lands coin 0.25 →
    climatology 0.193 → market 0.052 Brier, market-vs-coin certifies on
    both arms, and fusion strips the weaker experts' weight.
- **`sportsbot/signals/`** — external data feeds (research-first): NWS
  forecasts now back the weather arm's baseline at decision time (first
  live check: Pearson +0.86 vs market on next-day markets), and
  `sportsbot signals-scan` collects public social chatter with an
  evidence-gated correlation report. See `docs/SIGNALS_2026-09-17.md`.
- **`maker/`** — paper-only tennis market-making research: live L2
  capture + conservative queue-fill maker bot, and a pre-registered
  parameter replay optimizer. See `maker/README.md`.

## Provenance & side projects (kept alongside the consolidated engine)

- **`docs/`** — master plan, Linode server runbook, and the **chat-import
  manifests** recording exactly what each handoff drop contained and where it
  landed. New drops keep getting manifested there.
- **`polymarket-bot/`** — the pre-consolidation generation history: the Aug 19
  fair-value handoff (`CLAUDE.md` — the fv_bot/edge_model system still on the
  VPS), the v2 websocket bot + history downloader (`v2/`, still the milestone-1
  retro-data tool), and `archive/` with the Jul 17 falsification record and
  data-honesty findings. Read before re-litigating any strategy idea.
- **`substrate/reports/`** — real-data evidence runs (first season-scale
  milestone-1 report: 265 events, market Brier 0.178, longshot fit ≈ identity).
- **`dotless/`** — the .less remix server (separate music project; own
  `PROJECT.md`).
- **`scripts/`** — `pull_from_server.sh` (fetch fv_bot-era code/logs from the
  VPS, secret-safe), `sync_chats.sh`.

## Compliance

- Polymarket main-CLOB **order placement is geoblocked from US IPs** (reads
  are open). This project does not evade geoblocks. US-legal live venues:
  Polymarket US (separate API) and **Kalshi** (CFTC-regulated) — the Kalshi
  client here is demo-ready (`KALSHI_ENV=demo`).
- Sackmann tennis data: CC BY-NC-SA (non-commercial). MLB Stats API:
  personal/non-commercial terms. Review before commercial use.
- Nothing here is financial advice; trade only what you can afford to lose.

---

## Additional engine suite (independent modules)

Three standalone modules built in the optimize-chats-to-code sessions — separate
venvs, configs, SQLite DBs, and systemd units; nothing here imports sportsbot or
the imported `polymarket-bot/` VPS-bot history:

| Module | What it does | Money risk |
|---|---|---|
| [`polymarket-edge/`](polymarket-edge/) | Sportsbook-consensus + weather fair value vs Polymarket books | dry-run by default |
| [`kalshi-engine/`](kalshi-engine/) | Signal-generator substrate + ensemble on Kalshi (weather calibrated daily) | dry-run by default |
| [`arb-scanner/`](arb-scanner/) | Cross-platform Polymarket↔Kalshi complement-arb detection | never trades |

Per module: `python -m pytest tests -q`, `python -m src.main --once --dry-run`,
`python -m src.report`; kalshi-engine adds `python -m src.backtest` (offline
replay) and `python -m src.weather_calibrate` (daily sigma/bias fit, systemd
timer), polymarket-edge adds `python -m src.weather_divergence` (forecast vs
the temperature the bucket prices imply, city by city). Server bootstrap: `deploy/engines_setup.sh` then `deploy/deploy.sh`
(targets `/opt/pointless-repo`, separate from sportsbot). Docs:
`docs/MASTER_PLAN.md`; skills: `pre-live-gate`, `engine-health`.

