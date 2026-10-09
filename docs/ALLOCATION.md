# Capital allocation and the learning loop (2026-10-09)

How money reaches a strategy, how the bot moves it as evidence arrives,
and what the operator controls by hand. Code: `sportsbot/bot/portfolio.py`,
wired into every runner cycle; shown on `sportsbot board`.

## Arms, not sports

An **arm** is `sport/signal/style`:

| part | values | meaning |
|---|---|---|
| sport | baseball, tennis, table_tennis | the market |
| signal | `model` (Elo-family rating), `sharp` (Pinnacle-anchored fair value from the harness) | what prices the market |
| style | `maker` (rest one tick inside the spread, no taker fee, rebate), `taker` (cross the spread, pay the fee) | how the order is placed |

Every fill is tagged with its arm (`bets.arm`), so each arm accrues its
own closing-line value, Brier and gate. The evidence this repo has
gathered separates exactly along these lines: rating models lose to the
line (Results 2–9), takers pay over the close (Result 14), and the only
measured profits sit with resting orders. Which signal runs for a sport
is `sports.<sport>.signal` in the config (one per sport); which styles
are possible follows from `execution.post_inside_spread`.

## Where the money comes from (priority order)

Each source can only tighten the caps in `default.yaml`
(`max_total_exposure`, `max_fraction_per_sport`, `max_stake_per_market`),
never widen them.

1. **Manual.** A `weight:` on an arm in `config/allocation.yaml` pins its
   share of the exposure cap. The operator's call wins, up to the sport cap.
2. **Evidence.** An arm whose gate is green shares the remaining cap in
   proportion to the **lower bound** of its CLV interval, not the mean: the
   lower bound is what the data guarantees. Gate = at least 200 CLV-graded
   fills, market-clustered 95% CI of mean CLV above zero, Brier under
   0.25, fees verified on the venue (`sportsbot verify-fees`).
3. **Learning.** An arm marked `learn: true` that has not passed its gate
   may risk an equal slice of `learning.budget_usd`, so that real fills
   accrue the evidence a gate needs. When the learning arms' 7-day
   realised loss reaches `learning.weekly_loss_stop_usd`, all of them pause
   until the window rolls off. Learning money is a bounded tuition fee.

Everything else gets $0 and the dashboard prints the reason. The paper
account is different by design: its fills cost nothing and are the
evidence, so every running arm shares the cap equally there.

**Losses never raise an allocation.** A losing arm's interval widens or
drops below zero and its budget falls; the drawdown and daily-loss
switches in `bot/risk.py` act on top; the adaptive layer tightens edge
bars on negative CLV. Repositioning toward what is measured to work is
this module's job; chasing what just lost is not in it.

## Rolling profits

`bankroll_mode: equity` sizes stakes against the account's live equity
(cash plus marked positions), so profits roll into the bankroll and
losses roll out. The risk limits that scale with the bankroll follow it:
`risk.daily_loss_fraction` and `risk.max_drawdown_fraction` make the
effective limit the tighter of the dollar figure and the fraction of
current equity, so a $100 account is not running $1,000 limits.

## Starting with real money ($100–250)

The file cannot turn trading on. Live orders still need both `mode: live`
in the config and `SPORTSBOT_LIVE=1` in the environment, plus venue keys
in `.env`. The path the evidence supports:

1. Fund Kalshi (the US-legal venue) and set `accounts.real.starting_balance`
   to what you actually deposited.
2. Put `ODDS_API_KEY` in `.env` so the sharp line flows; set
   `sports.baseball.signal: sharp` (rank 1 of the research report).
3. In `config/allocation.yaml` set `learning.budget_usd` to what you are
   prepared to spend learning (the report's rank 2 experiment is maker-only
   on MLB, where books are wide), `learning.weekly_loss_stop_usd` to the
   weekly loss that pauses it, and `learn: true` on `baseball/sharp/maker`.
4. `sportsbot doctor` must pass; run one tiny manual trade and record it
   with `sportsbot verify-fees`.
5. Flip `mode: live` and `SPORTSBOT_LIVE=1`. The learning arm risks its
   slice; nothing else is funded until its gate is green.

As fills settle, `sportsbot board` shows each arm's CLV interval and gate
progress. When an arm's interval clears zero on 200 graded fills, its
budget moves from the learning slice to an evidence share automatically;
when it does not, it stays a bounded learning cost or goes to zero.

## Changing allocations by hand

Edit `config/allocation.yaml`; the bot re-reads it every cycle. `weight:
null` is automatic; a number pins the share; `enabled: false` switches an
arm off; `learn: true` lets an unproven arm spend learning money. The
dashboard's arms table shows the budget each arm actually received and
which rule produced it.
