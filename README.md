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

## Compliance

- Polymarket main-CLOB **order placement is geoblocked from US IPs** (reads
  are open). This project does not evade geoblocks. US-legal live venues:
  Polymarket US (separate API) and **Kalshi** (CFTC-regulated) — the Kalshi
  client here is demo-ready (`KALSHI_ENV=demo`).
- Sackmann tennis data: CC BY-NC-SA (non-commercial). MLB Stats API:
  personal/non-commercial terms. Review before commercial use.
- Nothing here is financial advice; trade only what you can afford to lose.
