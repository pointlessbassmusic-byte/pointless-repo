# Sportsbook reference vs Polymarket, measured on settled soccer

`python -m src.sportsbook_study` in polymarket-edge; rows in
`polymarket-edge/docs/sportsbook_rows.csv`. Rerunnable, no key needed.

## The question

polymarket-edge's headline model prices Polymarket sports markets from a
de-vigged sportsbook consensus. It has never run (`ODDS_API_KEY` unset), and
the resolved-market study left it as the one engine model with a reason to
exist: a "reference-price" edge, like the Fed rule, where a public source is
sharper than the book. This study measures that premise directly, with free
data, before anyone spends effort turning the model on.

## Data

- **Polymarket**: every resolved moneyline market (home / draw / away) under
  the `epl`, `la-liga`, `bundesliga` and `ligue-1` tags, Dec 2025 to Sep 2026.
  Price = last trade at 1h, 6h and 24h before `gameStartTime`, from the data
  API's trade log, normalised to YES. Outcome from the final `outcomePrices`.
- **Bookmakers**: football-data.co.uk season files for E0, SP1, D1, F1 (2025-26
  and 2026-27). De-vigged proportionally. Lines used: market average at the
  pre-weekend collection (`AvgH/D/A`) and at close (`AvgCH/CD/CA`), Bet365
  closing, Betfair Exchange closing, Pinnacle closing where quoted (185
  matches; blank for most of 2025-26 onward).
- **Matching**: explicit team-alias table for both of Polymarket's naming
  conventions, same league, match date within a day of the UTC kickoff, and
  the two sources must agree on the result. 1,051 of 1,060 Polymarket matches
  paired, zero unmatched names. Three La Liga matches printed 0.999 an hour
  "before kickoff" (re-listed fixtures with a wrong kickoff time) and are
  dropped by rule: 1,048 matches, 3,144 outcome rows per horizon.
- Polymarket volume per market: terciles at $88k and $309k, max $13M. The
  three-way price sum at 1h has median 1.010, so the book's overround is ~1c.

## Result 1: an hour before kickoff, Polymarket is as sharp as the books

Paired Brier on the same rows (negative = Polymarket closer to the outcomes),
bootstrap 95% CI resampling matches:

| Reference line (1h before kickoff) | rows | Brier Polymarket | Brier book | diff | 95% CI |
|---|---|---|---|---|---|
| Market average, pre-weekend | 3,144 | 0.1978 | 0.1981 | -0.0004 | [-0.0012, +0.0005] |
| Market average, closing | 3,144 | 0.1978 | 0.1978 | -0.0001 | [-0.0006, +0.0005] |
| Bet365 closing | 3,144 | 0.1978 | 0.1981 | -0.0004 | [-0.0010, +0.0003] |
| Betfair Exchange closing | 2,874 | 0.1982 | 0.1981 | +0.0001 | [-0.0005, +0.0006] |
| Pinnacle closing (subset) | 555 | 0.1926 | 0.1944 | -0.0017 | [-0.0029, -0.0006] |

The closing lines are taken at kickoff, an hour *after* the Polymarket price,
and still do not beat it. Against Pinnacle, on the matches where it is
quoted, Polymarket is better and the interval excludes zero.

Regression of the outcome on both prices (linear probability), 1h: against
the contemporaneous pre-weekend line the weight sits entirely on Polymarket
(beta_polymarket +1.02, t 2.6; beta_book -0.08, t -0.2). Against the closing
line: beta_book +0.07, t 0.1. The book adds nothing the price does not have.

Disagreement is small: median |Polymarket - market-average close| is 0.8c at
1h (90th percentile 2.4c), 1.3c at 24h (90th 3.7c). polymarket-edge's
`min_edge` is 4c. On these markets the model would almost never fire.

## Result 2: where they disagree, the book is not the one that is right

Buy the Polymarket outcome when the book's probability exceeds the price by
a threshold, pay price + 1c, hold to settlement. Return per $1 staked:

| Polymarket at | vs line | threshold | n | hit | return | se |
|---|---|---|---|---|---|---|
| 1h | pre-weekend (contemporaneous) | 2c | 421 | 0.261 | -7.3% | 9.0% |
| 1h | pre-weekend | 3c | 192 | 0.234 | -20.7% | 11.6% |
| 6h | pre-weekend | 2c | 292 | 0.253 | -11.6% | 10.4% |
| 24h | pre-weekend | 2c | 138 | 0.196 | -1.2% | 21.3% |
| 24h | closing (look-ahead) | 2c | 441 | 0.297 | +2.7% | 10.1% |
| 24h | closing (look-ahead) | 3c | 219 | 0.320 | +3.3% | 13.2% |

A live scanner has the contemporaneous line, and buying its disagreements
loses. Even a scanner that *knew the closing line a day early* would make
+2.7% with a 10-point standard error on 441 bets, which is the size of edge
PR #22's power analysis says needs ~10,000 bets to confirm. That is the
ceiling for this class of signal on these markets, and it is a look-ahead.

## Result 3: it holds in every slice

| Slice (1h, vs market-average close) | rows | Brier diff | 95% CI |
|---|---|---|---|
| Premier League | 873 | +0.0004 | [-0.0006, +0.0013] |
| La Liga | 906 | -0.0022 | [-0.0055, +0.0000] |
| Bundesliga | 693 | -0.0010 | [-0.0023, +0.0002] |
| Ligue 1 | 672 | +0.0004 | [-0.0011, +0.0022] |
| lowest volume tercile (< $88k) | 1,051 | -0.0015 | [-0.0032, -0.0000] |
| middle tercile | 1,051 | -0.0006 | [-0.0027, +0.0008] |
| highest tercile (> $309k) | 1,051 | +0.0002 | [-0.0008, +0.0014] |

No league and no volume band where the book is measurably sharper. The
thinnest tercile is where Polymarket does *best* relative to the book.
Polymarket's own calibration is consistent with the resolved-market study:
longshots slightly overpriced (bucket 0.0-0.1 trades 6.5c and hits 5.9%;
0.1-0.2 trades 15.4c, hits 14.4%), the rest within noise.

## What this changes

- The sportsbook-consensus thesis does not survive contact with settled
  data on liquid soccer. The premise, that a sportsbook line is a sharper
  reference than Polymarket's price, is false there: they are the same
  price to within a cent, and the residual disagreement goes the book's way
  less often than Polymarket's.
- `ODDS_API_KEY` is no longer the unlock it was described as. Turning the
  model on would produce near-zero signals at `min_edge: 0.04`, and the
  signals it did produce would come from the tail where the book is wrong.
- The reference-price class is narrower than "any public source": it needs
  a source that leads the market (fed-funds futures lead Polymarket's Fed
  buckets by days). A consensus of retail books does not lead the crowd
  that is already reading those books.
- Untested: NFL / NBA / MLB / NHL on Polymarket, the other four sports the
  engine configures. Those markets are more liquid than Ligue 1, not less,
  and the volume-tercile result gives no reason to expect the opposite.
  Measure before assuming: `sportsbook_study.py` is the template.

## Caveats

- football-data's "closing" line is a snapshot near kickoff, not
  second-exact; the pre-weekend line is collected days out. Both are stated
  as what they are above and the two bound the live case.
- Polymarket's price is the last trade, not the ask; the strategy charges
  1c for the spread and nothing for fees. Polymarket charges no maker/taker
  fee on these markets today.
- The Pinnacle subset (185 matches) is the sharp-book comparison and the
  smallest; it is also the one where Polymarket wins with an interval
  excluding zero.
- Per-bet return SD on these rows is ~1.5, so none of the strategy returns,
  positive or negative, is individually significant; the Brier and
  regression comparisons on 3,144 paired rows are the evidence.
