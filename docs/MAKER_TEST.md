# The maker fill-share test (pre-registered, 2026-10-12)

Result 15 (`docs/EDGE_VERDICT_2026-09-23.md`) measured that a bid resting
one tick under MLB taker prints on Polymarket earns **+0.6 pt of
closing-line value per fill** (+1.1 to +1.8 pt behind the best-CLV
wallets), interval above zero, and that every one of those fills was a
queue-priority fill at the existing bid. The tape cannot say what share
of them a *new* order would get. That share is the only number standing
between rank 2 of the research report and a funded arm, and it can only
be measured with real resting orders. This page fixes the test before it
runs so the answer cannot be argued with afterwards.

## What runs

The touch-maker arm (`sportsbot/bot/touch.py`, config block `touch:`):
on each pre-game MLB moneyline with a two-sided book, rest **$1** at the
best bid, then on the next cycle $1 at the best ask (`improve: false`,
the configuration Result 15 measured), cancel after the bot's
`order_ttl_seconds`, re-post while the window holds. One market holds at
most `max_stake_per_market` ($4) of cost basis. Every fill is a `bets`
row on the arm `baseball/touch/maker`, graded against the venue close by
the position manager and against the Pinnacle close by the sharp-line
harness once `ODDS_API_KEY` is set.

Venue: **Kalshi** (US-legal; the maker fee is 0.0025 × 0.5 per MLB
contract, about 0.12 pt of the measured 0.6) or **Polymarket US** when
it carries MLB moneylines. Never the main Polymarket CLOB from a US IP.

## What is recorded, per resting order

- posted price, side, size, the book at posting (bid/ask/sizes)
- time to fill or cancel; filled size
- the venue close and the sharp close for the market
- the taker prints that crossed the level while it rested (the paper
  venue's fill rule applied to the same tape, so paper and live fill
  rates can be compared order by order)

## How the evidence is kept

The executor writes the trail itself, so the read-out needs no extra
logging: every order's `raw` carries the arm that posted it, the book
it saw (best bid/ask and sizes), when it was placed, when it first
filled and how long it had rested; when it leaves the book -- filled,
cancelled at the TTL or pulled by the venue -- the executor applies the
paper venue's fill rule to the public tape over the window it rested in
and stores `tape_through`, the size the tape would have credited. Paper
orders carry both the venue's `tape_filled` and the shadow; they agree
by construction and the report prints the mismatch count as a
self-check. `sportsbot maker-report` reads it all back.

## Pre-registered read-out

Run until **200 fills** or **30 calendar days**, whichever comes first.
Then (`sportsbot maker-report --mode live`):

1. **Fill share.** Live fills divided by the fills the tape rule would
   have credited to the same orders. Result 15's numbers hold only in
   proportion to this share.
2. **CLV per fill.** Mean CLV at the venue close with a market-clustered
   bootstrap interval. The arm's gate (`docs/ALLOCATION.md`) is the usual
   one: ≥ 200 graded fills, interval lower bound above zero, Brier
   < 0.25, fees verified.
3. **Adverse selection by wait.** CLV per fill split by time-to-fill
   (< 30 s, 30–120 s, > 120 s). If the fast fills carry the CLV and the
   slow ones give it back, the TTL is the knob, not the size.

Decision rule, fixed now: the arm is funded beyond the test size only
if (1) × (Result 15's CLV) still clears the venue's maker fee **and** (2)
passes the gate on its own fills. Either failing closes rank 2 as a
standalone arm; informed-flow direction (queue item 2) is then its only
remaining form, data-logged.

## Cost of the test

At $1 a side and $4 per market the worst case is bounded by the sport
budget and the daily-loss switch in `bot/risk.py`; the expected cost is
the adverse selection on a few hundred one-dollar fills — tens of
dollars, not hundreds. The information is worth more than that; nothing
larger is justified until the share is known.

## What would make this moot

A fill share near zero (the queue is long and patient) ends it. A share
near one on a venue with a maker fee that eats the half-spread also ends
it. Anything in between is a number the allocator can use.
