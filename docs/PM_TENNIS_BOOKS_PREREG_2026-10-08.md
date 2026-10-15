# Is Polymarket tennis beaten by the sportsbook close? — pre-registration (2026-10-08)

The Kalshi version (`docs/PINNACLE_TENNIS_2026-10-08.md`) FAILED. Polymarket is
the venue the user prefers for sports, so the same question is asked there.
Committed before any Polymarket price was compared with an odds row.

## Data
- **Odds:** the same archived tennis-data 2026 ATP/WTA files, bookmaker-average
  close (AvgW/AvgL), Shin de-vigged. Pinnacle columns are descriptive only.
- **Polymarket:** Gamma events tagged `tennis`, `sportsMarketType == moneyline`,
  resolved 0/1, `gameStartTime` between 2026-01-01 and 2026-08-01, volume ≥ $10k.
  Trades from `data-api.polymarket.com/trades` (international exchange; the US
  exchange is not readable without a key, disclosed).
- **Pre-match cut:** `gameStartTime − 30 min`. Scheduled tennis times are
  "not before" times, so a cut 30 min earlier is pre-match unless the previous
  match on court retired early (rare; disclosed, not corrected).
  - Last-trade price = Polymarket close. Ask proxy = last taker-BUY trade price
    of the YES outcome before the cut; bid proxy = last taker-SELL price.
  - Markets with no trade in the 6 h before the cut are skipped.
- **Matching:** same as the Kalshi test (surname + initial of both players,
  date ±1 day).

## H1 — accuracy (descriptive)
Brier of Polymarket's last pre-cut trade vs the book-average close, paired t.

## H2 — tradable (decision)
- Rule: buy YES if book_fair − ask − fee ≥ 0.02; NO if
  (1 − book_fair) − (1 − bid) − fee ≥ 0.02. fee = 0.0695·p(1−p) (Polymarket US
  taker). Held to settlement, one trade per match.
- Split: start before 2026-04-16 vs from it. Pass: ≥ 100 trades per half, both
  halves mean > 0, second-half t ≥ 2 and ROI ≥ 1%.
- Caveat as before: the *closing* book line is used as fair value, so H2 is an
  upper bound for a live bot. A FAIL is decisive; a PASS needs a forward test.

## H3 — venue comparison (descriptive)
For matches present in both this sample and the Kalshi Jan–Aug tapes: the
Kalshi last trade at the same cut timestamp vs Polymarket's. Median absolute
gap, and each venue's Brier on the common set. No decision attached.
