# The touch-maker with the queue counted, second night — result (2026-10-19)

Pre-registered in `docs/QUEUE_ADJUSTED_TOUCH_PREREG_2026-10-19.md`
(amendments 1–2). Read once with `scripts/queue_join.py`.

## Data

`sportsbot queue-log`, 22:13Z on 10-19 to 00:20Z on 10-20, about 127
minutes. Two container restarts cut it at 23:44Z (resumed 23:45Z) and
at 00:20Z (not resumed; the pre-registered 120 minutes were already
covered).

| | |
|---|---|
| Kalshi book samples | 7,225 |
| Kalshi tape prints | 20,331 |
| Games with pre-start Kalshi samples | 39 |
| Mix | mostly tennis, plus 4 NHL games, MNF and an MLB postseason game |
| Median Kalshi touch size | **988 contracts** |

## Result (net of the Kalshi maker fee, game-clustered 95% intervals)

| join | joins | fills | fill rate | games with fills | net CLV per fill |
|---|---|---|---|---|---|
| **JOIN-QUEUE** (behind the displayed queue) | 1,634 | **17** | **1.0%** | 5 | −0.19¢ [−1.01, +0.04] |
| NEW-PRICE (first at a moved touch) | 56 | 8 | 14.3% | 6 | +0.14¢ [−0.26, +0.53] |
| FRONT (fill on any print at or through) | 1,634 | 431 | 26.4% | 25 | +0.28¢ [+0.12, +0.44] |

Per sport, JOIN-QUEUE filled 0 of 44 joins on MLB, 1 of 120 on NHL,
2 of 48 on NFL and 14 of 1,422 on tennis.

**Verdict: INCONCLUSIVE** by the pre-registered rule: 5 games with
fills, short of 15, and an interval that includes zero.

## Reading

The verdict is inconclusive. The finding is not:

- **A $1 order that joins a real Kalshi queue almost never fills.**
  About 1,000 contracts sit at a typical touch, and in 2 hours of
  pre-game tape the prints at or through that price exceeded the queue
  on 1% of joins. The fills that did come were not positive.
- **Front-of-queue fills are positive again.** That is the +0.28¢ row,
  and the third time this bound has been measured on Kalshi (Result 24
  +0.80¢ MLB, the 2026-10-14 tape result +0.67¢ NHL).
- **That number belongs to whoever is first in line.** A new order is
  not first.

This agrees with Result 15 addendum 3 on a second night and a wider
slate. Addendum 3 had a 22% fill rate on its NHL night against 1% here.
The difference is how much tape crossed the touch, and neither number
makes a new $1 order into the front of the queue.

## What it changes

- **The live $1 test.** Expect few fills. At about 1% per order, the
  read-out's 200 fills need on the order of 20,000 resting orders; at
  one order per market per 120 s cycle, that is weeks of slates, as
  `docs/MAKER_TEST.md` already allows (200 fills or 30 days). Its new
  `tape_queue` and `tape_strict` columns will show directly whether real
  fills track this 1%.
- **Being first is the only version with an edge.** Being first at a
  new price filled 14% here and was about flat. That was addendum 3's
  answer too. A touch arm that only quotes when it can **improve** the
  price (spread ≥ 2 ticks, `touch.improve: true`) is the one variant
  left to measure. It is not adopted from this data: 56 joins on 6
  games is a description, not a test.
- **Nothing is scaled.** The ledger row is INCONCLUSIVE, so it funds
  nothing.
