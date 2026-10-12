# Polymarket US calibration survey — results (2026-10-12)

Rules: `docs/PMUS_SURVEY_PREREG_2026-10-09.md` (+ amendment 1, pre-data).
Code: `research/pmus_survey/`.

## Bottom line
**FAIL.** Polymarket US prices its match-winner markets as well as Kalshi
does. Thirty minutes before the start, the settle rate sits within about
1¢ of the quoted mid in every bucket with real volume, and none of the
pre-registered taker rules survives both halves of the data. Tennis and
Kalshi at the same moment are 0.5¢ apart. The venue is young; its prices
are not.

## Data
- 15,220 resolved markets (6,000 tennis, 6,000 table tennis, all 3,220
  baseball), Nov 2025 – Oct 2026, one-minute price history from the venue's
  own gateway. Quote = last point at or before T − 30 min (ask = long price,
  bid = 1 − short price). 12,795 usable after the pre-registered quote rule
  (two-sided, spread ≤ 10¢, mid in [0.03, 0.97]).
- Split at the sampled universe's median start date, 2026-08-07.

## H1 — calibration (settle rate − mid, ¢, ± s.e.)
| Group | n | [.03,.15) | [.15,.35) | [.35,.65) | [.65,.85) | [.85,.97] |
|---|---|---|---|---|---|---|
| ALL | 12,795 | −2.4 ± 1.4 | −0.1 ± 1.0 | −0.4 ± 0.5 | −0.3 ± 1.0 | +0.6 ± 1.3 |
| baseball | 3,210 | n<30 | +0.6 ± 3.4 | −0.1 ± 0.9 | +0.2 ± 4.4 | n<30 |
| table tennis | 5,188 | +1.7 ± 5.5 | +0.7 ± 1.6 | −1.3 ± 0.8 | +1.3 ± 1.6 | −4.7 ± 5.8 |
| tennis | 4,397 | −2.9 ± 1.4 | −0.9 ± 1.4 | +1.1 ± 1.2 | −1.5 ± 1.4 | +1.1 ± 1.4 |

The only bucket more than two standard errors from zero is tennis longshots
(mid < 0.15), which settle about 3¢ below their price: the familiar
favourite–longshot shape, at about the size the Kalshi survey found on
non-sports categories. The taker fee at those prices is ~1¢ and the spread
rule allows up to 10¢, so this is the cell H2 tests.

## H2 — taker rules at T − 30, net of the 0.0695 fee
| Cell | Discovery (n, ¢/contract, t) | Confirmation (n, ¢, t, ROI) |
|---|---|---|
| tennis LONG-NO (buy NO on mid ≤ 0.15) | 159, −0.85, −0.39 | 146, +3.77, +2.28, +4.1% |
| tennis FAV (buy YES on mid ≥ 0.85) | 221, −1.57, −0.79 | 211, +0.57, +0.31, +0.6% |
| tennis DOG (buy YES on mid ≤ 0.15) | 159, −2.57, −1.16 | 146, −6.50, −3.92, −61% |
| ALL LONG-NO | 178, −2.03, −0.91 | 166, +3.92, +2.50, +4.3% |
| ALL FAV / DOG | 232, −1.49 / 178, −1.46 | 242, −0.68 / 166, −6.83 |
| table tennis (all three rules) | n = 11–19 | n = 20–31 |

**Verdict: FAIL.** No cell passes the discovery half; the confirmation-half
LONG-NO signal (t 2.3–2.5) is the ≈ 0.7 chance passes the pre-registration
expected across the cells, and its own first half was negative. The
pre-registered pass bar required both. The honest follow-up, if any, is a
forward paper test of "NO on tennis longshots at T − 30" on the live book,
not a backtest rerun.

## H3 — Polymarket US vs Kalshi, same match, same T − 30 (descriptive)
Tennis, 311 matches with a fresh Kalshi print and agreeing results:
Brier 0.2106 vs 0.2108 (t −0.21), median gap 0.5¢, p90 1.5¢. Baseball: no
Kalshi MLB tapes in the Jan–Aug set, so no comparison.

## Consequences
1. Polymarket US is not a softer venue than Kalshi for the sports the bot
   trades; venue choice stays a question of fees, fills and legality.
2. Research ledger: 13 pre-registered tests, 13 FAIL. The live pilot's
   purpose (fills and markouts) is unchanged, and the allocator funds
   nothing on the strength of this survey.
3. Dropped from scope and still unmeasured on this venue: football,
   basketball, hockey, MMA, esports.
