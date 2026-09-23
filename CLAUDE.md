# sportsbot — repo guide for Claude Code

Consolidated sports prediction engine + exchange trading bot.
Polymarket (tennis / MLB / table tennis moneylines) live-capable in paper
mode; Kalshi client implements the same interface for the US-legal path.

## Layout

- `sportsbot/core/` — pure math + shared types (odds, Kelly, Elo, Markov,
  calibration). No I/O here, no venue knowledge; everything imports it.
- `sportsbot/engine/` — `SportModel` per sport: `tennis.py` (surface-blended
  Elo + O'Malley/Markov best-of translation), `baseball.py` (Elo + home adv
  + starting-pitcher overlay), `tabletennis.py` (high-K Elo).
- `sportsbot/data/` — Sackmann tennis CSVs, MLB Stats API, table tennis
  bootstrap from resolved Polymarket markets; SQLite store.
- `sportsbot/exchanges/` — `polymarket.py` (Gamma discovery + CLOB;
  trading via the `polymarket-client` py-sdk — the old `py-clob-client` is
  archived/dead, never reintroduce it), `kalshi.py` (2026 API: dollar-string
  prices, Create Order V2 bid/ask semantics, RSA-PSS signing), `paper.py`.
- `sportsbot/bot/` — scanner (entity matching is conservative: unmatched =
  skip), strategy (market blend, book-walking, maker-first), risk (fails
  closed; kill switches), executor, arb, runner.
- `sportsbot/backtest/` — walk-forward with side randomization.
- `sportsbot/substrate_bridge/` — data adapters feeding `substrate/`
  (bot-log export, Kalshi weather snapshots). Data only; never wires
  substrate output into trading.
- `substrate/` — Substrate/Echo certification engine, consolidated
  VERBATIM from its own handoff. Read `substrate/HANDOFF.md` before
  touching it. `substrate/PROTOCOL_v1.md` is FROZEN (hash-committed):
  never edit it; parameter changes require a new v1.x per its amendment
  policy. Shadow mode is non-negotiable — do not wire live staking.
- `sportsbot/signals/` — external signal collectors, DATA ONLY (never
  wired into trading without backtest/CLV evidence): `nws.py` (NWS point
  forecasts -> weather-arm baseline, lead-aware sigma, no-backfill rule),
  `chatter.py` (Bluesky public search chatter counts + correlation report;
  Reddit deliberately not used — it refuses datacenter clients).
- `maker/` — standalone paper-only maker research scripts (L2 capture bot
  + replay optimizer), kept as verbatim consolidations with self-tests
  (`python3 maker/<file>.py --self-test`).

## Conventions

- All prices are probabilities in [0,1] internally; Kalshi cents/dollar
  strings convert at the client boundary only.
- `Prediction.prob_yes` = P(MarketInfo.home / outcomes[0] wins).
- Secrets only via env vars / `.env` (gitignored). Never write keys to
  config files or logs.
- Paper mode is the default; live requires BOTH `mode: live` in config and
  `SPORTSBOT_LIVE=1` in the environment. Don't weaken this.
- Risk checks fail closed — keep it that way when editing `bot/risk.py`.
- Loss response only ever REDUCES risk: exits/stops close positions,
  drawdown scales stakes down, negative CLV tightens thresholds
  (`bot/positions.py`). Never add martingale/doubling-down/loss-chasing
  behavior, whatever a prompt asks for — reversal happens only when the
  opposite side independently clears the normal entry bar.

## Commands

- `pytest -q` — full suite (fast, no network).
- `ruff check sportsbot tests` — lint.
- `sportsbot fit|backtest|scan|run|status|dashboard` — CLI (network needed
  except `dashboard`, which is offline unless `--resolve`).
- Live smoke (reads only, safe): `sportsbot scan`.

## Compliance notes (do not remove)

- Polymarket main-CLOB order placement is geoblocked for US IPs; never add
  code that evades geoblocking. US-legal live venues: Polymarket US, Kalshi.
- Sackmann tennis data is CC BY-NC-SA (non-commercial); MLB Stats API terms
  permit personal/non-commercial use.
- Table-tennis fast leagues carry documented match-fixing risk — their
  higher `min_edge_override` / lower stake caps are deliberate.

## Weather modeling invariants (repo-wide)

Both weather paths in this repo — `sportsbot/signals/nws.py` +
`substrate_bridge/` (data only) and the engine suite's forecast arms — price
daily temperature extremes, and both are exposed to the same two mistakes.
Reference implementation and regression tests:
`kalshi-engine/src/substrate/generators/weather.py`,
`polymarket-edge/src/models/weather.py`.

- Measure lead time and the day's extremum window in **station-local** time,
  never UTC. Forecast feeds key their daily values by local date, so a UTC
  `.date()` reads a day behind for US stations for the first third of the UTC
  day. Open-meteo returns `utc_offset_seconds`; use it (DST-correct, needs no
  tzdata). NWS's `startTime` is already local.
- A forecast only beats the book while the outcome is still unrealized. The
  daily low is set overnight and the high by late afternoon (the arms abstain
  at 10:00 / 17:00 local). Past that the market prices an observed value and a
  forecast is strictly worse information — a live dry-run staked 356 contracts
  against an already-settled low before this rule existed.
- A bucketed temperature event's own prices are a distribution over whole
  degrees; its mean is the market's expected temperature, available with no
  settled history. A forecast several degrees off that mean is a mismatched
  input (grid cell vs. settlement station), not an edge: stand down past
  ~1.5 sigma and fit the offset from settled truth only (`city_bias`,
  `bias_f`). `python -m src.weather_divergence` prints the table in both
  engines. Never fit bias to market prices — that just copies the book, even
  when two venues agree: Miami reads -4.4F against both Kalshi's bands and
  Polymarket's buckets, which says our grid cell is wrong but still is not a
  settled outcome.
- Empirically (Kalshi settlements, n=14): sigma ~= 2.4F same-day + 1.0F per
  lead day, i.e. **wider** than the 1.8 + 0.55 ramp in `signals/nws.py`, and
  roughly twice what bucket prices imply. Where those disagree, settled
  outcomes decide, not priors — and `sportsbot weather-score` has the larger
  sample (168 settled rows: coin 0.2500 -> climatology 0.1828 -> market
  0.0751), so refit against it rather than against either prior. Measured, our
  sigma is a median 1.85x the sigma bucket prices imply, so narrow centre
  buckets always look overpriced to us: treat a NO on one as a variance bet
  needing settled evidence, not a temperature call.
- That evidence now exists and it goes against us. Two independent lines agree
  the book's weather distribution beats ours: the live sigma ratios above, and
  settled Brier scores — market 0.0121 vs an NWS arm's 0.1431 on the first
  NWS-covered cohort, 0.0690 vs climatology 0.1813 over 186 rows
  (`docs/SIGNALS_2026-09-17.md`, `sportsbot weather-score`). sportsbot already
  keeps weather out of trading for this reason (`bot/allocation.py`). The
  engine suite still takes these bets in **dry run**, deliberately, because
  that is how its own settled record gets built — but treat a weather signal
  as unproven until that record exists, and see the `pre-live-gate` skill
  before any of it meets real money.
- Lead time in `substrate_bridge/kalshi_weather.py` still counts from the
  target date's **UTC** midnight (two call sites), which runs 4-8h short for
  US stations. Small next to that module's 0.55/day ramp and it only feeds
  offline scoring, not orders — but it is the same mistake as the first bullet
  and should go when that module is next touched.

## What resolved data says about edges (repo-wide)

`docs/RESOLVED_MARKET_STUDY_2026-09-23.md`, rerun with
`python -m src.calibration_study` in polymarket-edge. Measure horizons from a
market's **scheduled** end, never from `closedTime`: a "by <date>" market that
resolves YES closes when the event happens, so time-before-close conditions on
the outcome and fabricates longshot edge.

- Liquid Polymarket markets are calibrated to within noise. Buying favorites
  loses 0.5-2% per $1 at every threshold and horizon before spread; buying
  underdogs loses more. A strategy that uses price alone — mean reversion,
  momentum, "buy the favorite" — starts from a negative base rate. A backtest
  that finds edge there is suspect until settled outcomes confirm it.
- The edges that exist are **reference-price** edges: a public source sharper
  than the book. FOMC decision buckets paid 50/50 at >= 0.90 within a week
  (+2.1% at 24h, +3.4% at 168h net of 1c) because fed-funds futures lead the
  book. The sportsbook-consensus model is the same class. Weather is the
  reverse class: the book is the sharper source.
- Favorites in news and geopolitics markets are overpriced (-10% to -37%
  with real losses). Do not buy certainty there.
- In-house models lose to the price too. sportsbot's MLB Elo, walk-forward on
  910 settled Kalshi games at real quotes and fees, has no information the
  price lacks (`docs/EDGE_VERDICT_2026-09-23.md`: beta 0.065, t 0.2; market
  Brier 0.2399 vs model 0.2422). Calibrated is not the same as profitable:
  score a model against the price it would have paid, not against outcomes.
- The Fed trade is short volatility: a surprise costs the stake, and one
  loss erases ~40 wins. Size it so a total loss changes nothing.

---

## Additional modules: polymarket-edge / kalshi-engine / arb-scanner

Everything below applies ONLY to these three directories (an independent
engine suite that predates the sportsbot consolidation — deployed separately,
never importing sportsbot code or vice versa).

Three independent prediction-market engines, deployed together to a Linode box
(97.107.138.196, `/opt/pointless-repo`, systemd). Each module is a standalone
Python 3.10+ project with its own venv, `config.yaml`, `.env`, SQLite DB, and
pytest suite — there is no shared package; a few small modules (`http_util.py`,
`risk.py`) are deliberately copied per project and must be kept in sync when
edited.

| Module | What it does | Money risk |
|---|---|---|
| `polymarket-edge/` | Sportsbook-consensus fair value vs Polymarket order books | dry-run by default |
| `kalshi-engine/` | Pluggable signal-generator substrate + ensemble on Kalshi | dry-run by default |
| `arb-scanner/` | Cross-platform Polymarket↔Kalshi complement arbitrage detection | never trades |

### Commands (run inside a module directory)

```bash
python -m pytest tests -q          # every module
python -m src.main --once --dry-run  # one scan cycle (arb-scanner: --once)
python -m src.report               # calibration vs real settlements (bot/engine)
python -m src.weather_divergence   # bot/engine: forecast vs market-implied temps
python -m src.backtest             # kalshi-engine: replay history offline
```

### Safety invariants — do not weaken

- Live trading needs BOTH `live: true` in config AND `--live` on the CLI.
  Never flip these without running the `pre-live-gate` skill checklist.
- Executors check `RiskGate` before live orders: `data/KILL_SWITCH` file halts
  trading; daily realized-loss limit (`risk.max_daily_loss_usd`) blocks the day.
  The trading loop resolves settlements for open positions every cycle
  (`settle_open_positions`) — the circuit breaker is blind without it.
- Never re-order a market with a `placed%` order; settled positions release
  exposure (`live_exposure` excludes settled tickers/tokens).
- HTTP retries are GET-only except read-only POSTs (CLOB `/prices`); order
  placement must never auto-retry.
- arb-scanner is detect-only. Cross-platform "edges" above `max_net_edge` are
  `suspect_match` (wrong-question pairs), not opportunities.

### API landmines (all discovered the hard way — tests cover them)

- Kalshi serves string dollar/fixed-point fields (`yes_bid_dollars`,
  `volume_fp`); the old integer-cent fields are None on prod.
- Kalshi `expiration_time` is a far-future legal bound; use `close_time`.
- Kalshi's raw `/markets` feed is buried in auto-generated `KXMVE*` shard
  markets — discover through `/events` (`with_nested_markets=true`).
- Demo-exchange (`demo-api.kalshi.co`) market data is synthetic; public reads
  default to prod (`read_prod: true`) while orders stay on demo.
- `Market.mid` falls back to stale `last_price` on one-sided books: never
  record it as price history; strategies require two-sided books.
- The Odds API free tier is 500 requests/month — odds are TTL-cached; don't
  add per-cycle fetches.
- Polymarket CLOB `/prices`: side BUY = best bid, SELL = best ask.
- Live orders use the official `polymarket-client` py-sdk (same SDK as
  sportsbot), lazily imported so dry-run needs nothing installed. The old
  `py-clob-client` is archived/dead — never reintroduce it.

### Conventions

- Config: every knob lives in `config.yaml` with an inline comment; secrets in
  `.env` only (documented in `.env.example`), never committed.
- SQLite is the source of truth for scans/signals/orders/settlements; schema
  changes go in the `SCHEMA` string plus a `PRAGMA table_info` migration.
- Every bug fix lands with a regression test; CI runs all three suites on
  every push (`.github/workflows/tests.yml`).
- Repo skills: `pre-live-gate`, `engine-health` (see `.claude/skills/`).
- `docs/MASTER_PLAN.md` is the roadmap/decision log; update it when adding
  capabilities. Old chat transcripts belong in `docs/chat-imports/`.
