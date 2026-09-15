# Architecture

One repo, two deliverables sharing a substrate:

1. **Polymarket sports bot** (go-live): scans Polymarket sports event markets
   (tennis, MLB baseball, table tennis), prices them with the prediction
   engine, and places risk-managed limit orders. Paper mode by default;
   live mode is an explicit config + env-var opt-in.
2. **Prediction engine / substrate model** (Kalshi-ready): sport models
   behind a common interface, producing calibrated probabilities that any
   venue adapter can consume. The Kalshi client implements the same
   `ExchangeClient` contract so switching venues is configuration, not code.

## Layers

```
                ┌────────────────────────────────────────────┐
                │                  bot/                      │
                │  runner ─ scanner ─ strategy ─ risk ─ exec │
                └───────┬───────────────┬────────────────────┘
                        │               │
        ┌───────────────▼──┐      ┌─────▼──────────────────┐
        │     engine/      │      │      exchanges/        │
        │ tennis  baseball │      │ polymarket  kalshi     │
        │ table_tennis     │      │ paper                  │
        └───────┬──────────┘      └─────┬──────────────────┘
                │                       │
        ┌───────▼───────┐               │
        │     data/     │               │
        └───────┬───────┘               │
                │       ┌───────────────▼┐
                └──────►│     core/      │◄── backtest/
                        │ types odds elo │
                        │ markov staking │
                        │ calibration    │
                        └────────────────┘
```

- `core/` — pure math + shared types. No I/O, no venue knowledge. **Fixed
  contracts**: everything imports these; nothing here imports upward.
- `engine/` — `SportModel` implementations. Input: `EventInput`; output:
  `Prediction` (P(side A wins)). Tennis = surface-blended Elo decomposed
  into a point-level Markov chain; baseball = Elo with home advantage,
  rest and starting-pitcher adjustment; table tennis = high-K Elo with
  set-based Markov refinement.
- `data/` — ingestion: Jeff Sackmann ATP/WTA CSVs, MLB Stats API
  (statsapi.mlb.com), table-tennis results. Local SQLite cache.
- `exchanges/` — `ExchangeClient` implementations translating venue payloads
  into core types at the boundary. Prices are probabilities [0,1]
  everywhere inside the system (Kalshi cents ÷ 100 at the edge).
- `bot/` — the live loop: discover markets → match to model entities →
  predict → blend with market price → compute edge after fees/slippage →
  Kelly-size under caps → execute → track. Risk layer can veto everything
  (kill switches on drawdown, stale data, calibration decay).
- `backtest/` — replay historical results through the engine, score
  calibration (Brier/log-loss) and simulate flat/Kelly staking vs. closing
  prices.

## Trading pipeline (one scan cycle)

1. **Scan**: `list_sports_markets` per sport tag → `MarketInfo[]`.
2. **Match**: fuzzy-match market participants to model entity names;
   unmatched markets are skipped and logged (never guessed).
3. **Predict**: `SportModel.predict(EventInput)` → `prob_raw`.
4. **Blend**: `prob = w·model + (1-w)·market_mid` (default w = 0.30 — the
   market is the prior; the model only pushes when it disagrees).
5. **Edge**: `edge = prob − buy_price − fees − slippage_buffer`. Both sides
   (YES/NO) considered; only one can clear the threshold.
6. **Stake**: fractional Kelly (default 0.25×) under per-market, per-sport
   and total-exposure caps; skip below venue minimum.
7. **Risk gate**: daily-loss limit, max-drawdown kill switch, stale-quote
   guard, calibration monitor, live/paper flag.
8. **Execute**: post limit order at our price (maker where possible);
   never cross a spread wider than the configured max; track fills;
   cancel-on-timeout.
9. **Record**: every intent/order/fill and the market close price for CLV.

## Safety defaults

- Paper mode unless `SPORTSBOT_LIVE=1` **and** `mode: live` in config.
- Quarter Kelly, 3% minimum edge, 5% bankroll cap per market.
- Table tennis minor leagues carry a match-fixing risk premium: higher
  minimum edge, smaller caps.
- All credentials only from environment variables; nothing on disk.
