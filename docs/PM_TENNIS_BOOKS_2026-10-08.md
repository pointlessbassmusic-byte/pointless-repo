# Polymarket tennis vs sportsbook closing line — results (2026-10-08)

Rules: `docs/PM_TENNIS_BOOKS_PREREG_2026-10-08.md` (committed before any
Polymarket price was compared with an odds row). Code: `research/pm_tennis_books/`.

## Bottom line
**FAIL.** Polymarket's pre-match tennis price (30 min before the scheduled
start) is as accurate as the sportsbook consensus close and sits a median
0.8¢ from it. The pre-registered rule found only 52 trades in seven months,
far below the 100-per-half bar, and used the *closing* line as fair value,
which a live bot never has. Kalshi and Polymarket, measured at the same
moment, are the same price.

## Data
- Gamma: 9,939 tennis moneylines with `gameStartTime` Jan 1 – Aug 1 2026 and
  volume ≥ $10k; pre-cut trade tapes from data-api for all of them.
- 1,960 matched a tennis-data row with book-average odds (Polymarket lists
  many Challenger/ITF matches tennis-data does not; 7,696 unmatched, 281 with
  no trade in the 6 h before the cut).
- Pinnacle columns: 0 of the 172 January rows matched a Polymarket market in
  the window (Polymarket's tennis coverage in early January was thin), so no
  Pinnacle comparison exists here.

## H1 — accuracy (descriptive)
| | Polymarket last trade, T−30 min | Book-average close (Shin) |
|---|---|---|
| Brier, n = 1,960 | 0.2023 | 0.2028 (PM − book −0.0005, t −0.58) |

Median |book − Polymarket| = 0.8¢.

## H2 — tradable (decision)
| Rule (≥ 2¢ after the 0.0695·p(1−p) US taker fee) | Trades | ¢/trade | t | ROI |
|---|---|---|---|---|
| Jan 1 – Apr 15 | 2 | +14.2 | — | — |
| Apr 16 – Aug 1 | 50 | +9.2 | +1.63 | +30% |

**Verdict: FAIL** (≥ 100 trades per half required; t < 2). With the
international 0.05 fee: 3 and 52 trades, same picture.

The positive mean on 50 trades is the closing-line look-ahead the
pre-registration warned about: the rule bets when the *close* disagrees with
Polymarket 30 minutes earlier, which is the closing line beating an earlier
price, not an edge a bot could capture. About 7 such trades a month exist.

## H3 — Polymarket vs Kalshi at the same timestamp (descriptive)
`analyze.py` reports n 1,931, Polymarket Brier 0.2012 vs Kalshi 0.2458, p90
gap 39¢. That number is an artefact and is corrected here:
- Kalshi's "last trade before the cut" was sometimes a day old (p90 age
  1,286 min: an opening print, never updated), and about 9% of the
  surname-and-date pairings were a different match (settlement results
  disagree).
- Restricting to Kalshi prints under 2 h old (n 2,360):
  **Polymarket 0.1995, Kalshi 0.1993, median gap 1.0¢, p90 2.0¢.**
  Under 15 min old (n 2,063): 0.1977 vs 0.1977.
- The two venues carry the same pre-match price. Neither lags the other by
  anything a taker could cross the fee for, matching `docs/LAG_PREREG`'s
  in-play finding (Kalshi leads by ~3 s, unexploitable).

## Consequences
1. Sportsbook-consensus anchoring has no tradable edge on **either** venue
   for tennis pre-match. This closes the `polymarket-edge/` thesis for tennis
   on the data available.
2. The pre-match price is the same on Kalshi and Polymarket: venue choice
   for sports is about fees, legality and fills, not about who prices better.
3. Research ledger: 12 pre-registered tests, 12 FAIL. The remaining evidence
   routes are unchanged: the live pilot (fills, adverse selection, CLV) and
   the VPS paper logs.
