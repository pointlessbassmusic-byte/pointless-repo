# Live pilot plan: the first $100–250 (2026-10-08)

## What the pilot is, honestly
Every pre-registered strategy test so far has **failed** (`docs/research_ledger.json`,
rendered on the board). No sleeve has evidence of an edge. The pilot therefore
does not deploy a proven strategy; it buys the one thing simulation cannot
produce: **real fills, real adverse selection and real closing-line value (CLV)**
at the smallest size the venue allows. The learning loop below turns that
evidence into allocation, and it can only ever move money toward sleeves that
measure well and away from those that do not.

## Venue
**Kalshi**, via the existing live client, demo exchange by default
(`KALSHI_ENV`), prod only when the operator sets it.
- Polymarket is the preferred sports venue, but main-CLOB order placement is
  geoblocked for US IPs (never evaded), and the repo has no Polymarket-US
  trading client yet. Building one (api.polymarket.us, Ed25519 keys) is the
  next venue task; until then the pilot's sports go through Kalshi, which is
  US-legal and already wired for fills, cancels and settlement.

## Capital and loss limits (`config/pilot.yaml`)
| Knob | Value | Meaning at $150 |
|---|---|---|
| `bankroll.amount` | 150 | the allocator divides **min(amount, current equity)** |
| `max_stake_per_market` / fraction | $6 / 4% | one market cannot matter much |
| `max_total_exposure` | 40% | ≤ $60 at risk at once |
| `risk.daily_loss_limit` | $10 | ~7%/day, then stop until tomorrow |
| `risk.max_drawdown` | $40 | ~27% peak-to-trough: kill switch, manual reset |
| `kelly_multiplier` | 0.20 | fifth Kelly, scaled down further under drawdown |
| `min_minutes_before_start` | 20 | in-play leakage starts ~20 min out (PINNACLE_TENNIS) |

Hard cap on what can be lost before a human is involved: **$40** (the kill
switch), plus whatever is open at that moment (≤ $60 of cost basis, which is not
a loss unless every position settles at zero).

## The learning loop (what "optimize the allocation" means here)
Each cycle `Runner._sleeve_budgets` runs `bot/allocation.allocate` on **this
book's** equity and settled bets:
1. **Priors** per sleeve come from measured evidence (`SLEEVES` in
   `bot/allocation.py`), or from the operator's `allocation.manual` weights.
2. **Measured CLV** multiplies the prior once a sleeve has ≥ `clv_min_bets` (30)
   CLV observations: ×0.4 at −7.5¢ mean CLV up to ×1.6 at +7.5¢. Nothing moves
   before 30 observations; nothing moves on P&L alone.
3. **Caps** still bind: per-sport fraction, total exposure, per-market stake.
   The allocator can only tighten the risk layer.
4. **Drawdown** shrinks Kelly toward a floor; **negative rolling CLV** raises a
   sleeve's edge bar and halves its stake cap (`positions.adaptive_overrides`).
5. Every budget change is written to `allocation_log` and shown on the board
   ("Learning log") and in `sportsbot allocation`.

Rules that do not bend, whatever a prompt says: a losing sleeve is never sized
up to recover; a strategy that failed its pre-registered test is never funded;
reversal happens only when the opposite side independently clears the entry bar.

## Manual control
In `config/local.yaml` (merged over any config):
```yaml
allocation:
  manual: {tennis: 0.6, baseball: 0.4}   # replaces the priors
  paused: [table_tennis]                 # budget 0 until removed
```
Manual weights steer; the measurements and caps still apply on top. To stop
everything: `touch`-equivalent is the kill switch (`kill_switch_tripped` in KV)
or `systemctl stop sportsbot-pilot`.

## Promotion / demotion bars (pre-registered now, before any live bet)
- **Promote** a sleeve (allow it to grow past its prior): ≥ 100 settled live
  bets, mean CLV > 0 with t ≥ 2, realized ROI > 0. Reviewed weekly from the
  board; the CLV multiplier already does the sizing.
- **Demote** (pause via `allocation.paused`): mean CLV < −2¢ after ≥ 50 bets,
  or the sleeve alone accounts for ≥ 60% of the book's drawdown.
- **End the pilot**: kill switch trips, or 8 weeks pass with no sleeve meeting
  the promote bar. Either outcome is a result, written to the ledger.

## Go-live checklist (every item, in order)
1. Fund the venue account; put `KALSHI_*` keys in `/opt/sportsbot/.env`. Never in config, chat, or logs.
2. `accounts.real.starting_balance` and `bankroll.amount` = the deposit (doctor FAILs otherwise).
3. `sportsbot doctor -c config/pilot.yaml` exits 0 (it also FAILs if the pilot shares the sim's DB).
4. `sportsbot verify-fees --note "<source>"` after checking the venue's current fee schedule.
5. Follow the `pre-live-gate` skill checklist.
6. `mode: live` is already set in `pilot.yaml`; the second switch is `SPORTSBOT_LIVE=1` in the unit's environment. Install `deploy/sportsbot-pilot.service`; the board unit points at `pilot.yaml` too.
7. First week: `KALSHI_ENV=demo` to prove the plumbing end to end (demo prices are synthetic, so no CLV conclusions), then prod.

## What the pilot measures that simulation cannot
Every booked fill is marked at +5 s, +60 s, +5 min and +30 min against the
live book (`bot/markout.py`; board card "Fill quality (markout)",
`sportsbot status`). A fill under water at +5 s was picked off. The
promote bar above is read together with this: a sleeve whose fills average
below the fee at +60 s is adverse-selected whatever its P&L says, and the
operator pauses it. Maker orders are post-only on both venues, so a resting
order can never be filled as a taker at a price the edge math never saw.

## What the operator sees
- `sportsbot board -c config/pilot.yaml --loop 60` → `data/board.html`: equity curve, sleeve budgets with the reason for every dollar, the learning log, decisions, the go-live gate, and the research ledger.
- `sportsbot allocation -c config/pilot.yaml` for the same in the terminal.
- `sportsbot status -c config/pilot.yaml` now reports the live book only.
