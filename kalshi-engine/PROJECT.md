# PROJECT: Kalshi Predictions Engine / Substrate

_Master project file — everything about this engine lives here._

## Goal

A general-purpose prediction **substrate** trading on Kalshi: pluggable signal generators each emit
probability forecasts for markets they understand; a confidence-weighted ensemble combines them;
trades fire where the ensemble disagrees with the market price by more than a threshold. Same
edge/Kelly discipline as the Polymarket bot.

## Status

- **v1 (current):** working scan → substrate → ensemble → edge → execute(dry-run) → record loop.
- Auth via Kalshi API v2 **RSA key-pair signing** (the current scheme; email/password login is dead).
- Live orders gated behind `live: true` + `--live`.

## Architecture

```
src/
  main.py                 entrypoint + scan loop
  config.py               YAML config + .env loading
  client.py               Kalshi Trade API v2 client (RSA-PSS signed requests)
  substrate/
    base.py               SignalGenerator ABC + Forecast dataclass
    ensemble.py           confidence-weighted ensemble
    generators/
      market_implied.py   baseline: current market price as forecast (anchor)
      mean_reversion.py   fade short-horizon overreactions vs. recent price history
      time_decay.py       favorite-drift as expiry approaches
  strategy/edge.py        edge + fractional Kelly (buy YES or NO side)
  execution/executor.py   dry-run logger / live limit orders
  storage/db.py           SQLite: scans, price history, forecasts, orders
```

## Kalshi API v2 essentials

- Base URL: `https://api.elections.kalshi.com/trade-api/v2` (demo:
  `https://demo-api.kalshi.co/trade-api/v2`).
- Auth headers on every private request:
  - `KALSHI-ACCESS-KEY`: your API key id (from kalshi.com account settings)
  - `KALSHI-ACCESS-TIMESTAMP`: unix ms
  - `KALSHI-ACCESS-SIGNATURE`: base64 RSA-PSS-SHA256 signature of `timestamp + METHOD + path`
    (path only, no query string), signed with your downloaded RSA private key.
- Public reads (`/markets`, `/events`, `/series`) need no auth; portfolio/orders do.
- Prices are integer **cents** (1–99). Orders: `POST /portfolio/orders` with
  `action=buy`, `side=yes|no`, `type=limit`, `yes_price`/`no_price` in cents, `count` contracts.

## The substrate

Every generator implements:

```python
class SignalGenerator(ABC):
    name: str
    def forecast(self, market: Market, ctx: Context) -> Forecast | None: ...
```

`Forecast = (prob_yes, confidence, rationale)`. The ensemble takes the confidence-weighted average
of all forecasts for a market. `market_implied` (the current price, moderate confidence) is always
on, anchoring the ensemble so a single noisy generator can't run away.

Adding a generator = one new file in `src/substrate/generators/` + register it in `main.py`.
That's the whole point of the substrate: new ideas are plug-ins, not rewrites.

## Strategy parameters (config.yaml)

- `min_edge` (default **0.05**), `kelly_fraction` (**0.25**), `bankroll_usd`,
  `max_stake_per_market`, `max_total_exposure`, `min_volume`, `min_price`/`max_price`,
  `categories`/`series` filters — see `config.yaml` comments.

## Run

```bash
python -m src.main --dry-run
python -m src.main --once --dry-run
python -m src.main --live       # requires live: true in config + real API creds
```

Set `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` in `.env`
(`use_demo: true` in config targets the demo exchange for safe live testing).

## TODO / next

- [ ] More generators: news/LLM analyst, weather (climate markets), polling averages (politics),
      base-rate priors per series
- [ ] Calibration tracking: Brier score per generator from recorded forecasts vs. settlements
- [ ] Auto-weight generators by trailing calibration instead of static confidence
- [ ] Exit logic + position management (v1 is entry-only)
