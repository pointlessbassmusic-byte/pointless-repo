# Strategy lab: six more candidates, from the playbook and from the data

_2026-09-29_

After ten negative results, two questions: are there strategies from the
published playbook for prediction and sports markets that were never tested
here, and — if all ten were wrong — would the opposite trades have won?

Every candidate below is implemented in `sportsbot/backtest/strategy_lab.py`
and measured the same way: **one entry per event** (thousands of trades in
one market share a single outcome, so per-trade statistics would be fake),
**taker costs on every leg**, settled against the venue's own resolution.
Where a parameter could be tuned, it was chosen on the earliest 60% of
events and judged on the latest 40%, or on an independent window collected
after the rule was written down. None is wired into trading.

Already ruled out elsewhere and not repeated: favourite–longshot bias and
pre-game home-side drift (`EDGE_VERDICT` Results 3 and 6).

## "Wouldn't the opposite of the losing trades have been profitable?"

No — and the reason is the most useful thing in this document.

A trade and its opposite split the market's move between them, but **both
pay the round-trip cost**: the spread you cross and the fee you're charged.
So their returns sum to minus that cost, not to zero. Measured on the MLB
model, in the very sample where the model looked worst:

| model edge ≥ | follow | fade | follow + fade |
|---|---|---|---|
| 0.03 | −3.2% | +0.6% | **−2.6%** |
| 0.05 | −6.3% | +3.7% | **−2.6%** |
| 0.08 | −11.2% | +8.7% | **−2.5%** |

Every row sums to about −2.5%: the toll. Inverting a losing strategy only
wins when the original lost by *more* than the toll — when its signal was
genuinely backwards, not just useless. Most losing strategies are merely
useless (they lose roughly the toll), and their opposite loses it too.

The MLB model looked genuinely backwards in that sample: losses grew with
its confidence. So fading was tested where it counts, on **70 fresh games
played after the sample that suggested it** (Sep 23–28):

| edge ≥ | follow | fade |
|---|---|---|
| 0.03 | −1.3% | **−1.4%** |
| 0.05 | −2.5% | **−0.1%** |

Both sides lose. The anti-predictiveness belonged to one sample, not the
model; fading it is not a strategy. `strategy_lab.inversion_gap` computes
this sum for any strategy pair.

The same arithmetic shows up in every pair below: momentum vs fade, follow
vs fade the order flow. The two sides of a coin both pay to flip it.

## 1. Fade the model — no

Above. In-sample fade reached +9.2% (t 1.35) at edge ≥ 0.08; on fresh games
both follow and fade are negative.

## 2. Follow (or fade) the order flow — no

The literal opposite of the market-making result: if resting orders get
picked off, does trading *with* the takers pay? Signal: pre-game net taker
imbalance, above or below the training median; bet with or against it, hold
to settlement.

| | follow train | follow test | fade train | fade test |
|---|---|---|---|---|
| MLB (147 / 98) | +8.6% | +1.8% | −16.3% | −7.6% |
| Tennis (50 / 37) | −1.2% | −12.3% | −6.6% | +9.5% |

Signs flip between halves in both sports; no cell exceeds |t| 1.3 on test.
Noise.

## 3. Sibling overround arbitrage — no

Kalshi lists one market per competitor, on **separate order books**. Exactly
one YES pays $1 and exactly one NO pays $1, so the two YES asks summing
below $1 (or the two bids above $1) is locked profit. Scanned live across
127 open events, both books each:

| | median | extreme |
|---|---|---|
| sum of YES asks | 1.010 | min 0.990 |
| sum of YES bids | 0.990 | max 1.010 |

One tennis event did show asks summing to 0.990 — a genuine 1¢ pre-fee arb
— but taker fees on two legs make the all-in cost 1.012. Across all 127 the
cheapest lock costs **1.0103**. The books are held within one tick of each
other by someone faster and cheaper than a retail taker.

## 4. Cross-venue: trade Kalshi toward Polymarket — no

The most-cited edge in sports markets: use one venue's price as fair value
and trade another venue when it strays. 293 MLB games matched across Kalshi
and Polymarket at the same moment (3h before first pitch):

| | Brier |
|---|---|
| Kalshi | 0.2333 |
| Polymarket | 0.2334 |

Equally sharp, and **within 0.68¢ of each other on average, 2.5¢ at most**.
One game in 293 diverged by 2¢; none by 3¢. There is nothing to trade
toward. (The −0.5¢ mean gap is consistent with Kalshi's last print sitting
on the ask — 92% of pre-game MLB flow is takers buying YES — not a premium.
Polymarket is used as information only here; nothing is placed there.)

## 5. In-play overreaction: fade or follow a sharp move — no

The classic in-play trade: after the price jumps ≥ 10¢ within 5 minutes (a
run, a break of serve), bet on reversion; exit after 5 or 15 minutes, paying
spread and fee on both legs.

| MLB, test half (n≈95) | fade | momentum |
|---|---|---|
| jump ≥ 0.10, hold 5m | +0.5% (t 0.3) | −5.4% (t −3.4) |
| jump ≥ 0.10, hold 15m | +1.0% (t 0.5) | −5.9% (t −3.1) |
| jump ≥ 0.15, hold 15m | −2.2% (t −1.3) | −2.5% (t −1.4) |

Fade beats momentum everywhere, so there is mild real mean reversion — but
it is smaller than the round trip. Fade's train half was negative (MLB
−2.1%, t −2.1). Tennis is the same shape on n = 16–30.

## 6. Endgame: buy the near-certain in-play leader — pending confirmation

The popular Polymarket "high-probability" trade: buy the in-play leader once
it trades at ≥ 0.90–0.97 and hold for the last few cents. Exploratory run
on the 250-game MLB tape: **+7.0% on the test half (t 4.35) but −0.5% on the
train half** — one striking cell among roughly twelve tested, which is what
chance produces. Tennis showed t-values of 36–70, which are artifacts: n =
15–22 with zero losses, which at p ≈ 0.96 happens about half the time by luck.

**Status: confirmation in progress.** The rule was written down before any
new data was looked at (`endgame_confirm` in the session scratchpad,
mirrored by `strategy_lab.endgame`): first in-game print with max(p, 1−p) ≥
X, buy the leader as a taker, hold. Pass requires ROI > 0 with t ≥ 2 at
0.5¢ slippage **and** still positive at 1¢. It runs on 641 independent MLB
games (July 17 onward) that were not in the exploratory tape. This section
will be replaced with the result.

## Where that leaves the book

Sixteen hypotheses on real venue data (one pending). The ten from before, plus six here:

| strategy | verdict |
|---|---|
| Fade the model | both sides lose on fresh games |
| Follow / fade order flow | noise; signs flip between halves |
| Sibling overround arb | cheapest lock 1.0103 after fees |
| Cross-venue (Kalshi ↔ Polymarket) | equally sharp; gaps < 2.5¢ |
| In-play overreaction | mild reversion, smaller than the round trip |
| Endgame | confirmation pending on 641 independent games |

The pattern across all sixteen is the same: these markets are efficient to
within about one tick, and every strategy that needs to *cross* a spread
and pay a taker fee is fighting a toll of 2–3% that the edges available
here do not cover.
