# Pre-registration: Kalshi vs Polymarket US on the same games (2026-10-14)

Committed at 10:40Z, after a 3-minute smoke run that checked team matching
and before any analysis of the recording that started at 10:35Z.

## Why

Every venue-pair study in `docs/EDGE_VERDICT_2026-09-23.md` (Results 10, 11)
paired Kalshi with the **international** Polymarket CLOB, which is
geoblocked for US order placement. The two venues a US account can trade
are Kalshi and Polymarket US (QCEX). That pair has never been measured.
Polymarket US makers pay nothing; Kalshi charges 0.07 × p(1−p) to takers
(0.5× on MLB) and a small maker fee.

## Data

`twovenue_sample.py`: every game listed on both venues (tennis, MLB, NHL,
NFL, NBA), matched by the unordered pair of normalised team/player names
plus the Kalshi ticker date; 114 games at start. Both books' top of book
and touch size are read every 20 s for 60 minutes, expressed as P(the
Kalshi YES team wins). Each game is tagged pre-game or in-play by the
Polymarket US start time.

## Test A — complement arbitrage (taker)

At each sample, buying the team on one venue and the opponent on the
other costs `ask_A + (1 − bid_B)` plus both taker fees (Kalshi
`0.07·m·p(1−p)`, m = 0.5 MLB else 1; Polymarket US `0.0695·p(1−p)`).

- **PASS:** at least 1% of samples, on at least 5 distinct games, show a
  net profit of 1¢ or more per pair, with at least 10 contracts at both
  touches.
- **FAIL:** anything less.

## Test B — does one venue lead the other?

Mid-price `m`. Gap `g = m_K − m_P`. For each venue, regress its mid change
over the next h ∈ {20 s, 60 s, 180 s} on the gap now. Standard errors are
clustered by game. Samples where either book is one-sided or wider than
5¢ are dropped.

- **Polymarket US follows Kalshi** if β_P > 0 with t ≥ 3 at 60 s. Kalshi
  follows Polymarket US if β_K < 0 with t ≥ 3. Both can hold.
- **Maker leg, descriptive only:** a resting order one tick inside the
  lagging venue's book, on the side the leader moved, is marked to the
  lagging venue's mid 180 s later. No fill model is claimed; a maker
  experiment would follow only if B holds, and even then only at minimum
  size behind the pre-live gate.

One recording, one read. Pre-game and in-play are reported separately
but share the verdict rule.
