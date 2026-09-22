# The trading dashboard and the $100 sim book

_2026-09-22_

`sportsbot board` builds a single self-contained HTML page showing two books —
the $100 sim book and the real one — with the equity curve, where the money is
allowed to go and why, every decision the bot made including the passes, and
the go-live gate. No JavaScript, no CDN, no web server.

## The sim/real switch is a view, not a control

The page cannot start live trading and deliberately has no control that could.
Real orders still require `mode: live` in config **and** `SPORTSBOT_LIVE=1` in
the environment, both set by hand on the host. A button in a web page is the
wrong place for that decision, and a dashboard that could arm real money is one
stray click from an unreviewed live position. The test suite asserts the page
contains no `<script>` and no `<form>`.

## Where the $100 is allowed to go, and why

The question the allocator answers is "given what has actually been measured,
how much may each sport risk" — not "which model is most sophisticated".
Every sleeve carries the measurement behind its weight, and the page renders
that line verbatim so no dollar is unexplained.

| sleeve | prior | the evidence |
|---|---|---|
| baseball | 0.55 | Walk-forward n=11,661: log loss 0.6809 vs always-home 0.689, Brier 0.2440, 55.7% acc, calibration bins on the diagonal. The only model validated against a real baseline on a large sample. |
| tennis | 0.35 | Best-developed model (surface-blended Elo + O'Malley/Markov) and the sharpest target band in the research (0.60–0.63 log loss) — but **0 rated entities on this host**, so it is held at zero until `sportsbot fit tennis` runs somewhere that can reach the Sackmann CSVs. |
| table_tennis | 0.10 | Walk-forward n=414: log loss 0.6779 vs coin 0.6931 — beats a coin, but the engine records no edge against the market, and fast leagues carry documented match-fixing risk. Measurement-only sleeve. |
| weather | — | **Excluded.** On 168 settled Kalshi markets the market scores Brier 0.0751 against our best baseline's 0.1828. We are far worse than the price, so there is nothing to bet. It stays a substrate data arm, and the substrate is shadow-mode by protocol. |

Two properties hold and are tested:

- **Allocation reacts to CLV, never to a losing streak.** A sleeve grows only
  on positive *measured* closing-line value over at least `clv_min_bets`
  observations. A sleeve deep in the red with unchanged CLV keeps exactly its
  prior weight — being down is never a reason to size up. This is the same
  anti-martingale rule as `bot/positions.py`, which scales Kelly *down* on
  drawdown and tightens the edge bar on negative CLV.
- **Allocation only ever tightens the configured caps.** Each budget is the
  MIN of its allocation and `bankroll.max_fraction_per_sport`, so the risk
  layer stays fail-closed and this module can never loosen it.

## Honest status: nothing here is proven profitable yet

The bot's trading tables were empty before this work — zero bets, zero
predictions. There is no realized PnL record, so nothing has been *measured* as
profitable. What exists is per-model validation (the table above) and the
market-calibration record in `substrate/reports/`. The allocation therefore
starts from validation evidence and is designed to move as CLV accrues; the
dashboard exists so that movement is visible rather than asserted.

## The venue finding: Polymarket has nothing tradable for these sports

The sim's first cycles placed zero bets on 707 Polymarket markets. The
decision feed made the reason measurable rather than a guess, and the answer
turned out to be about the venue, not the thresholds.

Measured 2026-09-22, same hour, both venues:

| venue | sport | markets | median spread | ever <= 0.03 |
|---|---|---|---|---|
| Polymarket | table tennis | 350 | **0.88** | 0 of 282 |
| Polymarket | baseball | 15 | **0.94** | 0 |
| Kalshi | baseball | 86 | **0.010** | 23 of 25 sampled |
| Kalshi | tennis | 264 | **0.020** | 21 of 25 sampled |

The Polymarket books are not merely wide, they are empty shells: the best bid
and ask are a lone market maker at 0.03 / 0.97, with the next levels at 0.02 /
0.98. That read was verified against the raw order book rather than inferred
from a spread number — the bot was reading the venue correctly.

Two hypotheses died on the data. Liquidity does **not** arrive near match
time: table-tennis markets under 2h from start had a median spread of 0.94,
and in-play markets had no book at all. And the uncertainty filter was not
the blocker either — roughly 55% of the slate cleared it, and the inactivity
penalty never fired (maximum idle was 5 days against a 30-day threshold).
Polymarket's only listed MLB slate was the Sep-27 season finale, 131 hours
out; Kalshi listed that day's games.

So the sim runs on **Kalshi**: it is where the markets are, and it is the
US-legal venue for real money anyway (Polymarket's main CLOB geoblocks US
order placement, which this project does not attempt to evade).

Table tennis is switched off for the sim as a consequence. Kalshi lists no
table tennis, and Polymarket's table-tennis books cannot be traded at any
threshold. The model is fine; there is no market to trade it on.

## Kalshi MLB needed two fixes before it could scan

Pointing the sim at Kalshi scanned **zero** of 86 markets. Two real defects:

1. Kalshi lists one market per team and sets `no_sub_title` to the *same*
   team as `yes_sub_title`, so every market looked like a game against
   itself and the scanner dropped it (it skips `home == away`). The opponent
   has to come from the sibling market in the same event.
2. The display names are city-only short forms — "Los Angeles D", "Chicago
   WS" — which cannot match the full team names the ratings are keyed by.
   The ticker's team code maps cleanly instead.

There is a subtlety worth stating, because getting it backwards would be
invisible and wrong: `MarketInfo.home` means "the side the YES contract pays
on", but half of Kalshi's markets have the **visitor** as the YES side. The
scanner now models the real matchup — home advantage on the team with home
field, taken from the event ticker's away+home ordering — and then restates
the answer for the YES side, so `prob_yes = P(MarketInfo.home wins)` still
holds repo-wide. A test pins both halves: with equal Elo the home team is
favoured, the visitor is not, and the two sides sum to one.

After the fix: 86 of 86 markets scan.

## What the decision feed showed on the first live cycles

On Polymarket, 707 markets discovered, 298 scanned, **zero bets**: 154 passed
on spread, 134 on model uncertainty, 10 on no book. Not one reached the edge
test — which is what sent the investigation to the venue rather than the
thresholds.

On Kalshi the picture is completely different. Of 86 MLB markets scanned, 27
reached the edge test and were declined on their merits:

| passes | reason |
|---|---|
| 42 | no two-sided book (games further out) |
| 27 | edge under the 0.030 bar |
| 17 | spread wider than the 0.030 limit |

The closest calls were edges of +0.0035, +0.0046, +0.0121 and +0.0152 against
a 3-point bar — the bot pricing real books and declining because MLB
moneylines are near-efficient, exactly as the README's honest expectations
say. That is the system working, not idling. It will bet when an edge clears
3 points.

This is also why the feed records passes at all: on a near-efficient slate, a
feed that only showed bets would show an empty page and tell you nothing.

## Running it

```bash
sportsbot run -c config/sim.yaml     # the $100 paper book on live markets
sportsbot board -c config/sim.yaml --loop 60   # rebuild the page every minute
```

**The highest-value next action is `sportsbot fit tennis`.** Kalshi lists 264
tennis markets at a 0.020 median spread and the tennis model is the
best-developed in the repo — but it has zero rated players on this host,
because the web sandbox cannot reach the Sackmann CSVs. Run the fit on the
laptop or the VPS and the tennis sleeve turns on against the deepest liquid
slate available.

`config/sim.yaml` is a complete standalone config whose dollar knobs are scaled
to $100 — the $1,000 defaults would put a $50 max stake (half the account) in
one market and make Kelly-correct positions unplaceable under a $5 minimum.
On the server both run as `sportsbot-sim.service` and `sportsbot-board.service`;
view the page through an SSH tunnel, never an open port (see
`deploy/DEPLOYMENT.md`).

## Adding real money

The real panel stays empty and says so until you connect an account. When the
gate is green:

1. venue keys into `.env` on the host — never config, never the repo;
2. `accounts.real.starting_balance` set to what you have actually funded;
3. `mode: live` **and** `SPORTSBOT_LIVE=1`;
4. `sportsbot doctor` — exits non-zero if the host is not ready;
5. start at ~10% of intended bankroll.

The gate's fourth criterion — fees verified with one tiny manual trade — cannot
be measured from the database, so it is an operator attestation recorded with
`sportsbot verify-fees --note "..."` and shown as outstanding until then.
Treating an unmeasurable criterion as satisfied is how a gate becomes
decoration.
