# Forward test: NO on tennis longshots at T − 30 (Polymarket US) — pre-registration (2026-10-12)

`docs/PMUS_SURVEY_2026-10-12.md` found tennis longshots (mid < 0.15) settling
about 3¢ under their price, and the NO-on-longshots rule positive only in the
second half of the data. The survey's verdict was FAIL and stands. This is
the forward test it named as the only honest follow-up. Committed before the
recorder has stored a single quote.

## Data
`sportsbot/signals/prematch.py` records the top of book of every open
match-winner market (tennis, baseball, table tennis) on Polymarket US and
Kalshi every 5 minutes from the public feeds, and settles them from the
venues' results. Quotes are time-stamped by the recorder, not by a venue.

## Observation
The snapshot nearest **T − 30 min** (within ±10 min) per market; two-sided,
spread ≤ 10¢, mid in [0.03, 0.97].

## Primary rule (decision)
Polymarket US, tennis, mid ≤ 0.15: buy NO at 1 − bid, hold to settlement,
fee 0.0695·p(1−p). **Pass:** n ≥ 150 observations, mean net P&L per contract
> 0 with t ≥ 2. One cell, one test; no split, because the data are forward.
Anything else reported (FAV, DOG, other sports, Kalshi, the calibration
table) is descriptive.

## Caveats, fixed now
- The quote is the displayed top of book at T − 30; a real order would be a
  taker at that ask with queue and size limits. A PASS is an upper bound and
  moves the rule to a paper-trading sleeve (`allocation.paused` until it has
  30 CLV observations there), not to live sizing.
- Tennis scheduled starts slip late more often than early; a snapshot taken
  at T − 30 can occasionally be in-play. Not corrected; it works against the
  rule, not for it.
- Expected run time to n = 150 at the current rate of ~60 pre-match tennis
  markets a day with ~7% longshots: roughly five weeks.
