# Milestone 1 — first season-scale real-history report (2026-09-08)

Run on this repo's laptop-era environment; fully reproducible:

```
polymarket-bot/v2$ python3 history_downloader.py --days 365 \
    --sports tennis,table_tennis,mlb --max-markets 400 --fidelity 2 --out <data>
substrate$ python3 bot_backtest.py --data <data> --out bot_events.csv --report
```

## Data

- Discovery: **377,110 outcome tokens** across the year 2025-09-08 → 2026-09-06;
  top 400 markets kept per sport (2,400 tokens).
- Price availability: the CLOB served minute history for only **532/2,400 tokens**
  (`interval=max` fallback; the `startTs/endTs` form returns empty for resolved markets,
  and older resolved markets appear purged server-side). The usable sample skews toward
  recent months but spans **2025-10-31 → 2026-08-30**.
- Adapter yield: **265 events** (decision time = game start − 60 min; last pre-decision
  price; one event per condition; 7 unresolved and 928 no-pre-decision-snapshot markets
  dropped, never backfilled). Outcome balance 100 YES / 165 NO.
- Events CSV: [`bot_events_2026-09-08.csv`](bot_events_2026-09-08.csv) (ingest.py schema).

## Report (fit-on-prior discipline: longshot fit on first 132, scored on last 133)

```
longshot fit: a=-0.049  b=0.987      (≈ identity)
  market           n=133  brier=0.1780  log=-0.5270
  market_longshot  n=133  brier=0.1782  log=-0.5274
  fusion           n=133  brier=0.1781  log=-0.5272
  fusion weights:  market=0.503  market_longshot=0.497
```

## Reading

- **The market null has real skill** on this slate: Brier 0.178 vs 0.25 for a coin.
  This is the null any channel must beat — exactly the instrument's design.
- **No detectable favorite-longshot bias at 60-minute lead** in this sample: the fitted
  correction is ≈ identity (b=0.987), so the corrected expert adds nothing and fusion
  splits ~50/50 between two near-identical experts. (Contrast with the 30-event smoke
  test, where the tiny-sample fit went pathological and fusion correctly crushed it.)
- **Baseline is still a placeholder** (= market). The report gains its third expert when
  fv_bot's devigged sharp-book probabilities are imported from the VPS — that's the
  comparison the fair-value thesis actually cares about (does the sharp-book baseline
  beat Polymarket's own price?).

## Caveats

- No channel arm here — this is the market/baseline scaffolding of milestone 1, not a
  certification trial. Nothing here changes protocol parameters.
- Price-history purging means a true full-season backtest needs decision-time prices
  recorded live going forward (fv_bot's edge_log already does this on the VPS) rather
  than trusting retro availability.
