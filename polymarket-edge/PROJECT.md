# PROJECT: Polymarket Sports Betting Bot

_Master project file — everything about this engine lives here._

## Goal

Automated positive-EV betting on Polymarket **sports** markets: estimate fair probabilities from
de-vigged sportsbook consensus, buy outcome tokens when the ask price is meaningfully below fair
value, size with fractional Kelly, log everything.

## Status

- **v1 (current):** working scan → model → edge → size → execute(dry-run) → record loop.
- Live order placement wired via `py-clob-client` but gated behind `live: true` + `--live`.

## Architecture

```
src/
  main.py                 entrypoint + scan loop
  config.py               YAML config + .env loading
  clients/
    gamma.py              Gamma API: discover sports events/markets
    clob.py               CLOB API: prices/books; py-clob-client for orders
    odds_api.py           The Odds API: sportsbook odds feed
  models/
    devig.py              de-vig sportsbook odds → fair probabilities
    fair_value.py         match Polymarket markets ↔ sportsbook games, blend into fair prob
  strategy/
    edge.py               edge computation + filters + fractional Kelly sizing
  execution/
    executor.py           dry-run logger / live CLOB limit orders
  storage/
    db.py                 SQLite: scans, signals, orders
```

## Data sources & APIs

| Source | Base URL | Auth | Used for |
|---|---|---|---|
| Gamma API | `https://gamma-api.polymarket.com` | none | event/market discovery, metadata |
| CLOB API | `https://clob.polymarket.com` | none for reads; L1/L2 keys for orders | order books, prices, order placement |
| The Odds API | `https://api.the-odds-api.com/v4` | `ODDS_API_KEY` | sportsbook consensus odds (free tier: 500 req/mo) |

Order placement uses [`py-clob-client`](https://github.com/Polymarket/py-clob-client) with a
Polygon wallet private key (`POLYMARKET_PRIVATE_KEY`) and USDC allowance set on the exchange
contract. **Note:** Polymarket blocks US persons from trading — confirm jurisdiction/eligibility
before enabling live mode.

## Model (v1)

1. Pull odds for the sport from N sportsbooks (The Odds API, h2h market).
2. Per book: implied probs `1/decimal_odds`, de-vig with proportional normalization
   (`p_i / Σp_i`), then take the **median across books** → consensus fair prob.
3. Match to the Polymarket market by team names + start time (fuzzy match in `fair_value.py`).
4. Optional blend: `fair = w·consensus + (1−w)·market_mid` (config `model.blend_market_weight`,
   default 0.15) — shrinks toward the market to be humble about matching/model error.

## Strategy parameters (config.yaml)

- `min_edge` (default **0.04**): required `fair − ask` to buy.
- `kelly_fraction` (default **0.25**): quarter-Kelly.
- `max_stake_per_market`, `max_total_exposure`, `min_liquidity`, `min_hours_to_event`,
  `max_hours_to_event` — see `config.yaml` comments.

## Run

```bash
python -m src.main --dry-run          # default; also the safe explicit form
python -m src.main --once --dry-run   # single scan cycle, then exit
python -m src.main --live             # real orders (requires live: true in config too)
```

## TODO / next

- [ ] Elo/power-rating prior blended with consensus (consensus-only for v1)
- [ ] Sell-side logic (exit when edge flips negative beyond fees)
- [ ] Better market↔game matching (player props, spreads/totals — v1 is moneyline/h2h only)
- [ ] Backtests from recorded scans in `data/bot.db`
