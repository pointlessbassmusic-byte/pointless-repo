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

## Commands

- `pytest -q` — full suite (fast, no network).
- `ruff check sportsbot tests` — lint.
- `sportsbot fit|backtest|scan|run|status` — CLI (network needed).
- Live smoke (reads only, safe): `sportsbot scan`.

## Compliance notes (do not remove)

- Polymarket main-CLOB order placement is geoblocked for US IPs; never add
  code that evades geoblocking. US-legal live venues: Polymarket US, Kalshi.
- Sackmann tennis data is CC BY-NC-SA (non-commercial); MLB Stats API terms
  permit personal/non-commercial use.
- Table-tennis fast leagues carry documented match-fixing risk — their
  higher `min_edge_override` / lower stake caps are deliberate.

---

## Additional modules: polymarket-bot / kalshi-engine / arb-scanner

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
| `polymarket-bot/` | Sportsbook-consensus fair value vs Polymarket order books | dry-run by default |
| `kalshi-engine/` | Pluggable signal-generator substrate + ensemble on Kalshi | dry-run by default |
| `arb-scanner/` | Cross-platform Polymarket↔Kalshi complement arbitrage detection | never trades |

### Commands (run inside a module directory)

```bash
python -m pytest tests -q          # every module
python -m src.main --once --dry-run  # one scan cycle (arb-scanner: --once)
python -m src.report               # calibration vs real settlements (bot/engine)
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
