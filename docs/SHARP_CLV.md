# Sharp-line CLV harness (2026-10-07)

Implements rank 1 of `reports/Beating prediction market prices.md`: make
closing-line value against a **sharp sportsbook** the only evaluation
metric, because every edge that has survived fees in the literature was
measured that way, and the venue's own close is contaminated (in-play
prints, home-side drift — `docs/EDGE_VERDICT_2026-09-23.md`).

## What it does

`sportsbot/signals/sharp.py`, wired into the runner cycle. Data only.

1. **Collect.** On a budgeted cadence (`sharp.snapshot_interval_minutes`,
   default 15) fetch h2h lines from The Odds API for the sports on the
   current slate (`baseball_mlb`; tennis keys discovered daily from
   `/v4/sports`; table tennis has no sharp line and is skipped). Each book's
   two-way implied probabilities are de-vigged with **Shin**
   (`core.odds.shin_devig`) and stored in `sharp_quotes`. Snapshots pause
   when the API reports fewer than `credits_reserve` credits left.
2. **Remember.** Every scanned market's participants and start time go to
   `market_meta`, so a decision can be matched to a sportsbook event after
   the venue delists it.
3. **Grade.** `sportsbot sharp-report` matches each decision (bet *and*
   skip) and each bet to its event (both participants ≥ 0.85 similarity,
   start within 6 h, no ambiguity) and scores it against the **close** =
   last sharp quote at or before `start − min_lead_minutes` (default 10).
   Quotes inside the lead window are refused, never used.
   - bets: `clv_net = sharp_close(side) − fill_price − fee(fill_price)`
   - skips: the model-preferred side at the venue mid, same formula — the
     number says whether declining was right against Pinnacle
   - settled bets also get `bets.sharp_closing_price` written (side frame)
4. **Report.** Mean net CLV with an **event-clustered** percentile
   bootstrap CI (both sides of a game, or several fills on a market, are
   one draw); split by sport and by bet/skip; the venue-vs-sharp gap at
   decision time and how often it exceeded the fee; the beta of
   `(sharp close − venue mid)` on `(model − venue mid)`; and, for settled
   bets, expected ROI (CLV/price) next to realised ROI (the 1:1 check).

## Pass criterion (pre-registered)

- at least **1,000 graded decisions**, and
- mean net CLV on **taken bets > 0** with the 95 % clustered CI excluding
  zero.

Anything else prints `FAIL` or `INSUFFICIENT`. Expected and realised ROI
should move together; a positive CLV with flat realised ROI on a large
sample is drift in the benchmark, not an edge.

## Enforcement (tighten-only)

With `sharp.enforce_adaptive: true` the adaptive layer's rolling CLV
(`bot/positions.adaptive_overrides`) reads the sharp close where one
exists. That layer only ever raises a sport's min edge and cuts its stake
cap; swapping the benchmark can make a sport stricter against Pinnacle,
never looser than its configured base.

## Cost

Credits, not requests: one h2h call per sport key per snapshot. At a
15-minute cadence with one MLB key and ~6 active tennis keys that is
~26k credits/month, inside the $59 tier (100k). The free tier (500) covers
about two days at that cadence, or two weeks at hourly. Historical
snapshots cost 10× and are not fetched here.

## Running it

```
ODDS_API_KEY=... in .env
sportsbot run                  # collects + records every cycle
sportsbot sharp-snapshot       # one manual snapshot
sportsbot sharp-report         # grade and print the verdict (offline)
```

## Known limits

- Decisions written before this harness have no `market_meta` row and
  fall out of the funnel (`no_meta`); the report prints the funnel so the
  loss is visible.
- Kalshi tennis markets carry no start time; they match on names only and
  an ambiguous pair (same two players twice in the window) is skipped.
- Skips are scored at the venue mid, not at a fillable price; that flatters
  the skip CLV by half a spread and is stated in the report.
- The Odds API's Pinnacle feed is a website scrape and "may incur a delay";
  the close is therefore at least `min_lead_minutes` old by construction.
