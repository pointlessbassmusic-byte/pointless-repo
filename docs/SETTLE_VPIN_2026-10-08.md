# Maker fills held to settlement, split by VPIN — result (2026-10-08)

Pre-registration: `docs/SETTLE_VPIN_PREREG_2026-10-07.md`. Code: `research/settle_vpin/`.

Data: 1,500 sampled Kalshi markets (300 per series), Aug 5 – Oct 5 2026; full
trade tapes. 370 markets skipped (271 had no clean pre-match cut). Pre-match maker
fills held to settlement after the rounded maker fee.

| Series group | VPIN < 0.86: H1 (Aug 5–Sep 4) | VPIN < 0.86: H2 (Sep 5–Oct 5) | Verdict |
|---|---|---|---|
| ATP/WTA (maker fee 0.0175) | 115 mkts, **+2.74¢**, t 0.73 | 62 mkts, **+2.03¢**, t 0.46 | inconclusive |
| Challenger/ITF (no maker fee) | 35 mkts, −8.89¢, t −1.47 | 38 mkts, +2.57¢, t 0.45 | inconclusive |
| MLB (maker fee 0.00875) | 65 mkts, −7.45¢, t −1.53 | 45 mkts, −2.62¢, t −0.46 | **DEAD** (≤ 0 in both halves) |

The other groups for ATP/WTA (VPIN ≥ 0.86, warm-up) are also +2–4¢, except H2
warm-up at −0.26¢; none has t > 2.

## Reading
- **VPIN does not separate anything here.** 89% of taker contracts are YES-buys
  (ATP/WTA 57.2M YES vs 7.0M NO; MLB 92.7M vs 9.5M), so most buckets are
  one-sided and nearly every fill sits above 0.86.
- **A maker's settlement P&L is mostly a short-YES position.** It is dominated by
  each match's outcome; per-market s.d. is around 30¢, so a 2–3¢ effect needs about
  600 markets per half to reach t = 2. This test was underpowered for ATP/WTA.
- **Concentration:**
  - ATP/WTA total maker P&L in the sample was +$5.2M, of which the top 2% of fills
    earned +$9.8M.
  - In MLB the top 2% of fills earned +$16.9M against a total of −$0.14M.
- **Conclusions:**
  - MLB passive quoting is closed.
  - ATP/WTA gets a powered confirmation on the never-sampled markets:
    `docs/SETTLE_ATPWTA_PREREG_2026-10-08.md`.
