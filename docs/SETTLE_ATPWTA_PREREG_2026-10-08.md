# ATP/WTA average maker held to settlement — powered confirmation pre-registration (2026-10-08)

Follows `docs/SETTLE_VPIN_2026-10-08.md`, where ATP/WTA was positive but
underpowered. Committed before any of this data was pulled.

## Data
- Every settled KXATPMATCH and KXWTAMATCH market with volume, closing
  2026-08-05 … 2026-10-05, that was **not** in the earlier 600-market sample
  (≈ 2,300 markets).
- This data has never been looked at.
- Same pre-match cut and the same fee rule.

## Change from the first test, disclosed
- The VPIN split is dropped: all pre-match fills are pooled, because the first test
  showed VPIN does not separate on these one-sided books.
- This decision was made after seeing the first result. The confirmation data is
  untouched, so it does not bias this test.

## Pass bar
- One observation per market: the contract-weighted mean maker P&L per contract
  after fees.
- Mean > 0 with market-level t ≥ 2 **and** day-clustered t ≥ 2.
- Also reported: the result with the top 2% of fills removed, and the YES/NO maker
  split.

## If it passes
- It means the *average* ATP/WTA maker earns at settlement.
- It does **not** show that a new back-of-queue maker would. That still needs the
  live fill pilot (`reports/Prediction market profitable edges.md`).
