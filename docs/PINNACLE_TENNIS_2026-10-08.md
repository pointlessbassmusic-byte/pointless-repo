# Kalshi tennis vs sportsbook closing line — results (2026-10-08)

Rules: `docs/PINNACLE_TENNIS_PREREG_2026-10-08.md`. All three amendments were
committed before any odds value was compared with a Kalshi price.
Code: `research/pinnacle_clv/`.

## Bottom line
**FAIL.** Kalshi's pre-match tennis price is as accurate as the sportsbook
consensus close. Trading Kalshi toward that consensus has no edge: the
pre-registered rule loses money. The clean re-check that follows lands within
noise, and that is even with the unfair advantage of seeing the closing line
early. Kalshi tracks the books to a median of about 1¢ in the hour before
play.

## Data
- **Odds:** tennis-data.co.uk 2026 ATP/WTA files, from Internet Archive
  snapshots of 2026-08-03. The live site blocks datacenter IPs and the user's
  browser.
  - **Pinnacle odds stop after January 2026** (71 ATP + 101 WTA rows), so the
    decision benchmark is the bookmaker-average close (AvgW/AvgL), Shin de-vigged
    (amendment 3).
- **Kalshi:** all 5,559 KXATPMATCH/KXWTAMATCH events closing Jan 1 – Aug 1 2026,
  one random side each, with full trade tapes from `/historical/trades`.
  - 2,283 matched to a tennis-data row. Skipped: no clean pre-match/in-play
    break on the tape (1,897), and not uniquely matched, e.g. qualifying
    rounds tennis-data omits (1,314).

## Pre-registered result (decision)
| | Kalshi last pre-cut trade | Book-avg close (Shin) |
|---|---|---|
| H1 Brier, n = 2,283 | 0.1926 | 0.2002 (diff −0.0076, t −4.95) |

| H2 rule (≥2¢ after fee, held to settlement) | Trades | ¢/trade | t | ROI |
|---|---|---|---|---|
| Jan 1 – Apr 15 | 543 | −1.51 | −0.83 | −3.6% |
| Apr 16 – Aug 1 | 735 | −5.26 | −3.35 | −12.1% |

**Verdict: FAIL.**

## Disclosed flaw: the in-play cut fires late (post-hoc, does not change the verdict)
- `prematch_cut` marks play as started at the first trade more than 10¢ from the
  opening print. An early break moves a tennis line only about 5–8¢, so that
  trade usually comes 10–20 minutes into play.
- Measured against the closing consensus:

| Kalshi price taken … before the cut | Median gap to consensus | Median trades in prior 5 min |
|---|---|---|
| 0 min | 7.4¢ | 34 |
| 5 min | 1.8¢ | 19 |
| 10 min | 1.5¢ | 12 |
| 20 min | 1.2¢ | 5 |
| 60 min | 1.0¢ | 2 |

- **What it means:** the "Kalshi beats the books" H1 result above is in-play
  information leaking in. It is not pre-match skill. The H2 losses are inflated
  for the same reason: the rule bet against a price that already knew the
  score.
- **Re-run with the cut moved back** (new `prematch_cut(backoff_s=…)`):

| Back-off | n | Brier Kalshi / book-avg | H2 first half | H2 second half |
|---|---|---|---|---|
| 20 min | 2,208 | 0.2008 / 0.2003 (t +1.0) | 51 trades, −3.9¢, t −0.8 | 100, +1.9¢, t +0.5 |
| 30 min | 2,192 | 0.2009 / 0.2002 (t +1.4) | 30, +6.0¢, t +0.9 | 58, +10.2¢, t +1.9 |

- **How to read the re-run:** none of these reaches the pass bar. Every one also
  uses the *closing* consensus against a Kalshi price 20–30 minutes older. That
  look-ahead flatters the rule, because it is the very definition of the
  closing line beating earlier prices.
- **What a live version would need:** sportsbook prices at the same moment, not
  the close. On these counts it would trade about 10–15 times a month out of
  about 1,600 matches. Even at the face value of the 30-minute row, that is
  pocket change before slippage.
- **Pinnacle (January only, descriptive):** 112 matches, Brier 0.2073 vs 0.2032
  (t +0.66). No reliable difference.

## Consequences
1. **Consensus-anchoring on Kalshi tennis is dead.** This is the
   `polymarket-edge/` thesis applied to Kalshi, and it has no measurable edge
   there. Kalshi already prices the books.
2. **Earlier studies that used `prematch_cut`** (`docs/SETTLE_VPIN_2026-10-08.md`,
   `research/settle_vpin/`) treated about 10–20 minutes of in-play trading as
   pre-match. Those studies FAILED, and contamination cannot turn a fail into a
   pass in that design. Treat their "pre-match" fill counts as mixed.
   - Future pre-match work on tennis tapes should use `backoff_s >= 1200`.
3. The remaining open evidence routes are unchanged: the VPS paper logs, and a
   small live fill pilot. A live, same-moment sportsbook feed (`ODDS_API_KEY`)
   could re-test the 30-minute row honestly, but the volume above caps its
   value.
