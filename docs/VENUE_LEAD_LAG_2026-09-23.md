# Does Polymarket lead Kalshi? Measured on the same soccer matches

`python -m src.venue_study` in polymarket-edge; rows in
`polymarket-edge/docs/venue_rows.csv`. Rerunnable; the sample grows every
match week.

## The question

The one edge class with evidence behind it is a public reference that leads
the market. Kalshi is the US-legal venue and Polymarket the deeper one, so
the obvious candidate is: Polymarket's price as a reference for Kalshi
trades. If Kalshi's quote converges toward Polymarket's rather than the
reverse, the gap is tradeable on Kalshi with no model. This measures that on
settled matches.

## Data

- Kalshi's `KXLALIGAGAME`, `KXBUNDESLIGAGAME`, `KXLIGUE1GAME` series (three
  markets per match: home, tie, away), every settled market: 153 games,
  2026-08-13 to 2026-09-20 (the series launched in August). Hourly
  candlesticks give the quoted bid and ask at 1h / 6h / 24h before kickoff.
  Volumes $0.1M to $2.7M per market.
- Polymarket rows from the sportsbook study (last trade at the same horizons,
  outcome). Kalshi names mapped to football-data names by an explicit table,
  joined on league, teams and date; 148 of 153 paired, zero unmatched names.
- 444 outcome rows per horizon.

## Result: the two venues are the same price

| horizon | Brier Polymarket | Brier Kalshi mid | diff (95% CI) | median gap | Kalshi spread |
|---|---|---|---|---|---|
| 1h | 0.1917 | 0.1915 | +0.0002 [-0.0006, +0.0010] | 0.5c | 1.0c |
| 6h | 0.1916 | 0.1919 | -0.0003 [-0.0008, +0.0002] | 0.5c | 1.0c |
| 24h | 0.1933 | 0.1933 | +0.0000 [-0.0006, +0.0006] | 0.5c | 1.0c |

Median disagreement is half a cent, the 90th percentile 1.5c, inside
Kalshi's own 1c spread plus its fee. Only 3 of 444 rows at 1h show
Polymarket 2c above Kalshi's ask, and all three lost.

## Lead-lag: if anyone leads, it is Kalshi

Regress each venue's move from horizon h to 1h on the other venue's lead at
h. A coefficient of 1 means the mover closes the whole gap.

| window | Kalshi moves toward Polymarket | Polymarket moves toward Kalshi |
|---|---|---|
| 6h -> 1h | beta +0.27 (t +4.5) | beta +0.50 (t +6.2) |
| 24h -> 1h | beta -0.09 (t -0.7) | beta +1.07 (t +7.6) |

From a day out, Kalshi's quote does not move toward Polymarket at all;
Polymarket's price moves all the way to Kalshi's. Part of that is
measurement: Polymarket's price is the last trade, which can be hours old a
day before kickoff, while Kalshi's is a live quoted mid, and a stale print
converging on a live quote looks like "following". That bias runs one way,
so the honest reading is: no evidence that Polymarket leads Kalshi on these
markets, and some that Kalshi's quote is the more current of the two.

## What this changes

- Polymarket's price is not a leading reference for Kalshi soccer markets.
  Cross-venue "edges" on these events are the arb-scanner's suspect-match
  class, not opportunities, which the scanner already assumes.
- The reference-price class now has one confirmed member (fed-funds futures
  vs Fed decision buckets) and three measured non-members (sportsbook
  consensus, the other venue, in-house Elo). Anything proposed next in that
  class should be measured the same way before a line of engine code is
  written: settled rows, both prices, paired Brier, a lead-lag regression.
- Sample is six weeks of one season. The script reruns as Kalshi's history
  grows; the conclusion is stable at the 1c level already because the gap
  distribution, not the outcome count, is what decides it.
