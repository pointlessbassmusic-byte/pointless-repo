# Polymarket US calibration survey — pre-registration (2026-10-09)

Polymarket US (QCEX DCM) has listed sports match-winner markets since
November 2025. `docs/SURVEY_2026-10-01.md` found Kalshi sports priced within
±0.4¢ of the settle rate in every bucket; nothing has measured this venue.
Committed before any price history was pulled. Metadata only (market lists,
slugs, settlement values) was looked at while writing this.

## Universe
- Every resolved match-winner market on `gateway.polymarket.us` with
  `startDate` from 2025-11-01 to 2026-10-01: `sportsMarketType` in
  {`moneyline`, `*_match_winner`, `*_full_game_winner`}, two sides, both with
  a `teamId`. League = the slug's second token (`aec-mlb-…` → mlb). Sport
  groups: baseball, tennis, table_tennis, football, basketball, hockey,
  soccer, mma, esports, other.
- Settlement = the long side's resolved `price` (1/0), cross-checked
  against `/settlement` on a 200-market sample; markets with neither are dropped.

## Quote
- `GET /v1/price-history?symbol={slug}&fidelity=1` over
  [`gameStartTime` − 60 min, `gameStartTime`]; the last point at or before
  **T − 30 min**. ask = `longPrice`, bid = 1 − `shortPrice`, mid = (bid+ask)/2.
- Require both sides present, 0.03 ≤ mid ≤ 0.97, spread ≤ 0.10.
- Disclosed: `gameStartTime` is the scheduled start; tennis starts late more
  often than early, so T − 30 is pre-match with rare exceptions (not corrected).

## Split
Discovery = markets with `startDate` before the **median `startDate` of the
universe** (computed from the market list, before any price is pulled, and
recorded in the results doc); confirmation = the rest.

## H1 — calibration (descriptive)
Settle rate − mid by mid bucket [0.03,0.15), [0.15,0.35), [0.35,0.65),
[0.65,0.85), [0.85,0.97], by sport group and pooled, with n and the standard
error. Nothing is decided on H1.

## H2 — taker rules at T − 30 (decision)
Per sport group, held to settlement, fee = 0.0695·p·(1−p) per contract:
- **FAV**: buy YES when mid ≥ 0.85, at the ask.
- **LONG-NO**: buy NO when mid ≤ 0.15, at 1 − bid.
- **DOG**: buy YES when mid ≤ 0.15, at the ask (the mirror, so a longshot bias
  in either direction is caught).
Discovery pass: n ≥ 100, mean net P&L per contract > 0 with t ≥ 2.
Confirmation pass: same cell, n ≥ 100, mean > 0, t ≥ 2, ROI ≥ 1%.
With 10 groups × 3 rules = 30 cells, ≈ 0.7 discovery passes are expected
by chance at t ≥ 2; a cell must pass both halves.
Verdict: PASS only if at least one cell passes both halves. Any PASS is an
upper bound (the T − 30 quote is treated as fillable at its displayed ask)
and triggers a forward paper test on the live book before any sizing.

## H3 — venue comparison (descriptive)
For tennis and MLB markets that match a Kalshi market in the Jan–Aug 2026
tapes (`research/pinnacle_clv`, same name rules), the Kalshi last trade at
the same T − 30 timestamp: median |gap| and each venue's Brier on the common
set, Kalshi prints under 2 h old only (the stale-print lesson from
`docs/PM_TENNIS_BOOKS_2026-10-08.md`).
