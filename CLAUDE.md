# pointless-repo

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

## Commands (run inside a module directory)

```bash
python -m pytest tests -q          # every module
python -m src.main --once --dry-run  # one scan cycle (arb-scanner: --once)
python -m src.report               # calibration vs real settlements (bot/engine)
python -m src.backtest             # kalshi-engine: replay history offline
```

## Safety invariants — do not weaken

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

## API landmines (all discovered the hard way — tests cover them)

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
- `py-clob-client` (live orders, lazily imported) is dormant upstream —
  migrate to `polymarket-client` before enabling live trading.

## Conventions

- Config: every knob lives in `config.yaml` with an inline comment; secrets in
  `.env` only (documented in `.env.example`), never committed.
- SQLite is the source of truth for scans/signals/orders/settlements; schema
  changes go in the `SCHEMA` string plus a `PRAGMA table_info` migration.
- Every bug fix lands with a regression test; CI runs all three suites on
  every push (`.github/workflows/tests.yml`).
- Repo skills: `pre-live-gate`, `engine-health` (see `.claude/skills/`).
- `docs/MASTER_PLAN.md` is the roadmap/decision log; update it when adding
  capabilities. Old chat transcripts belong in `docs/chat-imports/`.
