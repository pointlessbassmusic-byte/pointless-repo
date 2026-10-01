# Kalshi exchange-wide strategy survey (research only, never wired to trading)

Reproduces docs/SURVEY_2026-10-01.md. Run in an empty working directory with
`series_all.json` (full `/series` listing) present:

1. `collect_enum.py` — settled markets on 20 sampled days (live listing; days past
   the historical cutoff come from `/historical/markets`, which ignores time
   filters — see the doc).
2. `collect_candles.py` — per-event candles, compacted to the pre-registered
   time points; `backfill_hist.py` for markets past the historical cutoff.
3. `grid.py` — the pre-registered grid; `calib.py` — descriptive calibration.
4. `maker_fill.py CATEGORY|SERIES SIDE BUCKET FRAC [N]` — trade-tape fill check.
5. `overround.py` — live scan of mutually exclusive events for basket arbitrage.
