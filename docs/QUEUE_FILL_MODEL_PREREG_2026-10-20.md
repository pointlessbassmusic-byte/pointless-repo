# Pre-registration: predicting which touch joins fill (2026-10-20)

Committed before any of its data exists. The idea came from a peer
research session (model "K-queue-markov-fill").

## Why

Every touch-maker measurement agrees on the price side: +0.3 to +0.9¢
per fill at the front of the queue, nothing behind it. The decision
variable is the fill share. On 2026-10-19 a new $1 order behind the
queue filled 1.0% of the time
(`docs/QUEUE_ADJUSTED_TOUCH_RESULT_2026-10-19.md`), at every queue
depth. If the joins that fill can be predicted, the live test could
post only those. If they cannot, or if the predictable fills are the
adverse ones, the touch arm stays a $1 measurement for good.

## Data (forward only)

`deploy/sportsbot-queuelog.service` (`sportsbot queue-log`, 30 s Kalshi
books and Kalshi's public tape for every US game within 12 h) writes to
`data/queue_forward.sqlite`. It needs the operator to enable it.

- The test window is the **30 calendar days** from the first recorded
  day.
- Rates for day D are fitted only on days before D. The first 7 days are
  burn-in and are fitted only, never scored.

## Model (fixed now)

A birth-death view of the touch (Cont, Stoikov & Talreja, 2010),
reduced to what 30 s snapshots and a tape can identify:

- **Execution rate λ:** size printed at or through the touch per
  second, for that game's sport, in the trailing 30 minutes of the
  same game. Fall back to that sport's rate over earlier days.
- **Touch lifetime τ:** the median time the touch price survives at
  that sport, from earlier days.
- **Predicted fill probability for a back-of-queue join with Q ahead:**
  P(Poisson(λτ / s̄) ≥ Q / s̄ + 1), where s̄ is that sport's mean print
  size. This treats executions as a compound process in units of a
  typical print, with cancellations ahead ignored (conservative).

Joins are built exactly as in `scripts/queue_join.py`: JOIN-QUEUE,
30-minute deadline, never past the start.

## Decision

All scored joins over the 30 days are ranked by predicted fill
probability.

- **PASS:** the top quintile's realised fill share is **> 3×** the
  overall share, **and** its net CLV per fill (game-clustered 95%
  interval) is **not worse** than the overall net CLV per fill. That
  means its mean is ≥ the overall mean, or the two intervals overlap
  with the top quintile's lower bound ≥ the overall lower bound.
- **FAIL:** anything else.

A PASS does not change the live test by itself. It goes to the operator
as a proposal: post only where the predicted fill probability clears
the top-quintile threshold, at $1.

## Rule variants counted

This is one model and one decision. It is the 18th rule variant this
session has tried; the count is in the weekly status.
