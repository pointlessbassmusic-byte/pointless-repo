# Kalshi vs Polymarket in-play lead-lag (MLB) — pre-registration (2026-10-05)

Committed before any trade data for this test was pulled.

## Data
- MLB moneylines on both venues for games ending in September 2026.
- Polymarket: from the market list in research/sharps. Kalshi: KXMLBGAME,
  matched on team name and start time (within 3 h).
- Second-stamped trades on both venues:
  - Polymarket: data-api `/trades?market=`, paged backwards with `end=`.
  - Kalshi: `/markets/trades`.
- Every trade is converted to P(Polymarket outcome[0] wins).
- In-play window: game start (Polymarket `gameStartTime`) to the last trade.

## Measurement 1: who leads (descriptive)
- Per game, build each venue's last-trade price on a 1-second grid.
- Cross-correlate 10-second price changes at lags −60 … +60 s (step 1 s), and
  report the peak lag, pooled over games.

## Measurement 2: tradable test (the decision)
**Event.** Polymarket's price moves ≥ 0.05 within 15 s (first crossing; at most
one event per game per 5 min). τ = the time of the trade that completes the move.
Only events where Kalshi's last trade at τ has moved less than half as far in the
same direction since τ − 15 s.

**Entry.** Buy on Kalshi in the direction of the move:
- YES on the team whose price rose.
- Price = the first Kalshi trade at or after τ + d whose taker bought that same
  side (i.e. the ask actually paid).
- d ∈ {2, 5, 10} s; **primary d = 5 s**.
- Fee 0.07·p(1−p). Held to Kalshi settlement.

**Split.** Discovery = games ending Sep 1–15; confirmation = Sep 16–30.

**Pass (confirmation, d = 5 s).** ≥ 100 events, mean P&L > 0, day-clustered
t ≥ 2, ROI ≥ 1%; discovery mean > 0.

The reverse direction (Kalshi moves first → buy on Polymarket) is reported
descriptively only. Polymarket's main exchange is not usable from the US.

## Rules note
Using public exchange prices from another venue is ordinary cross-market
information, not courtsiding or insider data.
