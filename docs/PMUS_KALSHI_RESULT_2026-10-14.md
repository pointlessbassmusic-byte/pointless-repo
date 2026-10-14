# Kalshi vs Polymarket US on the same games — result (2026-10-14)

Pre-registered in `docs/PMUS_KALSHI_PREREG_2026-10-14.md` (plus
amendment 1). Read once, after the recording ended.

## Data

114 games listed on both venues: 40 tennis, 31 NHL, 24 NBA, 14 NFL and 5
MLB. Both books were read every 20 s for 60 minutes (10:34–11:34Z),
giving 12,198 paired snapshots, 11,811 of them two-sided on both venues.
All in-play samples (1,126) are tennis, from 18 matches; the US games
were all pre-game.

## A — complement arbitrage (taker on both venues): FAIL

| | samples | ≥ 1¢ net with ≥ 10 contracts | best | median |
|---|---|---|---|---|
| pre-game | 10,685 | 0 | −1.42¢ | −4.68¢ |
| in-play | 1,126 | 35 (3.1%) on 11 matches | +13.1¢ | −3.58¢ |
| all | 11,811 | 35 (0.30%) | | |

The bar was 1% of samples on 5 games, so this fails. Pre-game the two
venues never cross: the best moment still lost 1.4¢ a pair.

The in-play hits are the moment after a point. Kalshi has repriced and
Polymarket US has not yet. Only 5 of 42 such gaps were still there one
sample (20 s) later, and one WTA match produced most of the largest ones.
These are latency gaps, not resting money. Two 20 s snapshots taken one
after the other cannot show whether two taker orders would have caught
one.

## B — which venue leads: Polymarket US follows Kalshi (criterion met)

Next-h mid change regressed on the gap `m_K − m_P`, clustered by game:

| | h | β (PM US follows; > 0) | t | β (Kalshi follows; < 0) | t |
|---|---|---|---|---|---|
| all | 60 s | **+0.415** | **+3.3** | +0.027 | +0.4 |
| in-play | 20 s | **+0.925** | **+33.4** | +0.114 | +2.9 |
| pre-game | 60 s | +0.007 | +2.8 | −0.006 | −2.5 |
| pre-game | 180 s | +0.016 | +2.7 | −0.020 | −4.2 |

- **In play:** Polymarket US closes about 93% of a gap to Kalshi's mid
  within one 20 s sample. Kalshi does not follow Polymarket US.
- **Pre-game:** the two venues are 1–2 cents apart and both drift toward
  each other by 1–2% of the gap over 3 minutes. That is nothing to trade.
- **Maker leg, descriptive only:** a resting order one tick inside
  Polymarket US on the side Kalshi moved, marked to the Polymarket US mid
  180 s later, gives +0.70¢ [+0.46, +1.01] on n = 2,069. **No fill model
  is claimed.** That order fills only if someone sells into it before the
  venue reprices, and with the venue repricing within 20 s, the fills it
  does get are most likely the adverse ones.

## C — rest on Polymarket US, hedge as taker on Kalshi (descriptive)

- **Pre-game:** a ≥ 1¢ lock was available in 1.5% of samples; the 95th
  percentile lock is +0.15¢. There is nothing here.
- **In play:** 13.6% of samples, but these are the same transient
  post-point gaps as in A.

## What it means

The US-legal venue pair behaves like Kalshi and the international
Polymarket did (Result 10, PRs #33–#39), only faster. Kalshi leads, the
follower catches up within seconds, and neither price leaves a pre-game
gap worth a fee.

The only open question is a **latency** one: can a resting or taking
order on Polymarket US act inside the few seconds after a Kalshi in-play
move? That needs sub-second data (both venues' websockets), not 20 s
polling, and it is an in-play race against professional market makers,
which the risk layer deliberately blocks (`min_minutes_before_start`).
It is noted, not queued: the earlier international version of this race
(the ledger's "in-play lag (jump-follow)") was a FAIL.

Nothing changes in the bot.
