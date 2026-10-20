# Pre-registration: the touch-maker with the queue counted, second sample (2026-10-19)

Committed before the recording starts (22:15Z) and before any of it is read.

## Why

Every positive touch number in this repo (Result 15 on Polymarket,
Result 24 and `docs/KALSHI_TOUCH_RESULT_2026-10-14.md` on Kalshi, +0.6 to
+1.1¢ per fill) is the front-of-queue upper bound. The one queue-counted
measurement is Result 15 addendum 3. It used 16 NHL games on one night:
joining the existing Kalshi queue filled 22% of the time at −0.24 ±
0.31¢, and being first at a new price gave +0.07 ± 0.12¢ on 41 joins.
That measurement decides whether the live $1 test can ever become a
strategy, and its sample is one night of one sport. Its script was never
committed (`data/ratings/` is git-ignored). This test repeats it on a new
night, across every sport on the slate, with the analysis committed
(`scripts/queue_join.py`).

## Data

`sportsbot queue-log --minutes 240 --interval 30 --window-h 12` from
about 22:15Z, written to its own `data/queue_2026-10-19.sqlite`. It reads
Kalshi's book (best bid/ask and the size at each) every 30 s for every
US game starting within 12 h that Kalshi also lists, plus Kalshi's
public taker tape. Only Kalshi is scored, because Polymarket US has no
tape and main is geoblocked. Only samples and prints before each game's
start are used.

## Construction

Each game, each side, at most one join per 5 minutes (so joins are not
near-duplicates). A join is a $1 resting order:

- **JOIN-QUEUE:** at the best bid (YES) or best ask (NO side), behind the
  displayed size at that price. It fills once the cumulative size of
  later prints at or through its price exceeds that queue. Cancellations
  ahead are ignored, which is conservative.
- **NEW-PRICE:** at a sample where the best bid (or ask) has moved to a
  price not seen at the previous sample. The order is first at that
  price, so there is nothing queued ahead. It fills on the first later
  print at or through it.

Deadline: 30 minutes, and never past the start. CLV per fill is the
last pre-start Kalshi mid (in the side's frame) minus the fill price,
minus `kalshi_maker_fee_per_share`. Intervals use a game-clustered
bootstrap (2,000 draws, seed 19).

## Decision (JOIN-QUEUE is the primary row; it is what a new $1 order
actually does)

- **PASS:** at least 15 games with fills, and the 95% lower bound of net
  CLV per fill above 0. That would contradict addendum 3, and the live
  test would be worth scaling past $1 once its own fills agree.
- **FAIL:** an upper bound at or below 0. The queue-counted touch is not
  an edge on a second night either; the live test stays a $1
  measurement, and nothing is scaled.
- **INCONCLUSIVE:** otherwise.

NEW-PRICE and the per-sport rows are descriptive. The front-of-queue
version of the same joins (fill on any print at or through) is reported
alongside, as a check against Result 24.

## Amendment 1 (22:14Z, before any data is read)

The recording runs **120 minutes**, not 240: the session's background
jobs are capped at 2 h. It still spans the pre-start window of the NHL
slate (23:00–00:00Z starts) and Monday Night Football (00:15Z). The
bar is unchanged: 15 games with fills.

## Amendment 2 (23:46Z, before any data is read)

The container restarted at about 23:44Z and stopped the recorder after
91 minutes (22:13–23:44Z). It resumed into the same db at 23:46Z for up
to 120 more minutes, so there is a gap of about 2 minutes in every
game's samples. The join and fill rules do not depend on continuity
(fills need prints after the join, and the tape is fetched with
overlap), so the analysis is unchanged.
