# The maker fill-share test (pre-registered, 2026-10-12)

Result 15 (`docs/EDGE_VERDICT_2026-09-23.md`) measured that a bid resting
one tick under MLB taker prints on Polymarket earns **+0.6 pt of
closing-line value per fill** (+1.1 to +1.8 pt behind the best-CLV
wallets), interval above zero, and that every one of those fills was a
queue-priority fill at the existing bid. Its 2026-10-13 addenda measured
the same half-point on NHL (27 markets), NFL (24) and college football
(45; realised return agrees) — the books in season once the World Series
ends — a point of CLV on EPL outcome markets (21; sample), nothing
measurable yet on NBA, nothing on UFC, and on LoL/CS2 a positive CLV
with a NEGATIVE realised return, which excludes esports from this test
whatever the paper fill share says. The tape cannot say what share of
those fills a *new* order would get. That share is the only number standing
between rank 2 of the research report and a funded arm, and it can only
be measured with real resting orders. This page fixes the test before it
runs so the answer cannot be argued with afterwards.

## What runs

The touch-maker arm (`sportsbot/bot/touch.py`, config block `touch:`):
on each pre-game MLB, NHL or NFL moneyline (`touch.sports`) with a
two-sided book, rest **$1** at the
best bid, then on the next cycle $1 at the best ask (`improve: false`,
the configuration Result 15 measured), cancel after the bot's
`order_ttl_seconds`, re-post while the window holds. One market holds at
most `max_stake_per_market` ($4) of cost basis. Every fill is a `bets`
row on the arm `<sport>/touch/maker`, graded against the venue close by
the position manager and against the Pinnacle close by the sharp-line
harness once `ODDS_API_KEY` is set.

Every live order carries a **venue-enforced expiry** of max(TTL + 60 s,
240 s): Kalshi `expiration_time`, Polymarket `expiration` (the SDK's floor
is 3 minutes), Polymarket US `TIME_IN_FORCE_GOOD_TILL_DATE` +
`goodTillTime` (sent as RFC 3339 UTC; the docs give no request format, so
confirm it on the first order with the others). A crash, a host reboot or
a failed cancel therefore cannot leave a quote resting into the game,
where Result 23 shows the price moves seconds after each pitch and a
resting order is what gets picked off. An order whose expiry could not
fall a minute before the start is refused locally.

Venue: **Polymarket US** (preferred since 2026-10-13) or **Kalshi**, both
US-legal. Kalshi's own tape now carries the same measurement (EDGE_VERDICT
Result 24, 396 MLB games): +0.80 to +0.93c per fill net of its maker fee
at the front of the queue, zero on strict through-fills. These are bounds
only: counting the queue in front removes the NHL edge (Result 15
addendum 3), so nothing here sizes the test. Kalshi has the
public tape the fill-share denominator needs today. Never the main Polymarket CLOB from a US IP.

Why Polymarket US first, from its own fee schedule and gateway
(`sportsbot/exchanges/polymarket_us.py`, `exchange: polymarket_us`):

* **Makers pay nothing.** Takers pay 0.0695 × C × p × (1 − p), as on
  Kalshi; makers earn a 0.0125 × C × p × (1 − p) rebate until 2026-10-19
  13:00 ET and from then a volume-tiered rebate that is zero below $10M a
  month. Kalshi charges makers $0.0025 a contract on NHL/NFL (0.25 pt)
  and half that on MLB.
* **Every game has a start time** (`gameStartTime`), so the NHL/NFL leg
  needs no `ODDS_API_KEY`; Kalshi's tickers carry none.
* **Its own books, measured 2026-10-13 (read-only, no keys):**

  | sport | moneylines listed | in a 12 h window | spread | median touch size |
  |---|---|---|---|---|
  | NHL | 45 | 12 | 0.010 | 58,770 |
  | MLB | 5 | 1 | 0.005 | 819,395 |
  | NBA | 26 | 3 | 0.010 | 2,497 |
  | Tennis | 110 | 29 | 0.010 | 80 |
  | College football | 118 | 2 | 0.005 | 167,521 |

  Touch size is the queue a new $1 order joins. It is deep on every
  venue: on 16 NHL games sampled across all three books (EDGE_VERDICT
  Result 15 addendum 3) the touch held 80-130k contracts on each, and
  Polymarket US turned its queue over fastest (0.21 an hour against
  main's 0.17 and Kalshi's 0.11).
  * Front-of-queue fills earned +0.45 pt there, against +0.41 on Kalshi
    net of its fee and +0.33 on main.
  * Strict sweeps were rare everywhere (one per 4-25 market-hours).

  Where real fills fall between those bounds is the fill-share question
  this test exists to answer.

Two caveats specific to it. (1) It publishes no keyless trade tape: trades
stream only over the authenticated markets websocket. The recorder
`sportsbot us-tape` (`exchanges/polymarket_us_tape.py`, unit
`sportsbot-ustape.service`, built 2026-10-19) subscribes every moneyline
starting within 24 h and writes `data/us_tape.sqlite`. Its sessions say
which windows are covered. The paper venue and the live test's
fill-share denominator (`tape_through`) read that file. A window it does
not cover is a `TapeGap`, and the order then carries no shadow rather
than a zero. Without the recorder the read-out reports live fills and CLV
per fill with no shadow. The handshake was checked against the live
endpoint with a dummy key (401 "API key not found"). With a real key,
`sportsbot us-tape --verify` (2026-10-25) confirms the documented field
names and the YES price frame: every sampled trade must parse with a known
taker intent, and a print on a slug the tape has covered without a gap must
equal the public `/bbo` last trade, not 1 − p. (2) Order prices are always in YES terms (buying
NO at 0.40 is sent as a short at 0.60) and `participateDontInitiate` makes
every maker order post-only; confirm both on the first order by hand, as
step 4 below says.
Kalshi's NHL and NFL tickers carry no start time. Since 2026-10-14 the
client derives one from the market's `occurrence_datetime` (the scheduled
end, 3.0 h after the start on 69 of 69 games checked) minus 3.5 h: half
an hour early, so quoting stops before the real start. The NHL/NFL leg
therefore no longer needs `ODDS_API_KEY`; the key still supplies the
sharp close the read-out grades against. The paper sim on Polymarket has
the start from Gamma and needs nothing.

## What is recorded, per resting order

- posted price, side, size, the book at posting (bid/ask/sizes)
- time to fill or cancel; filled size
- the venue close and the sharp close for the market
- the taker prints that crossed the level while it rested (the paper
  venue's fill rule applied to the same tape, so paper and live fill
  rates can be compared order by order)
- `queue_ahead`: the contracts resting on our side at our price or better
  when the order went in, i.e. the queue it joined (from 2026-10-20)
- `tape_printed`: the uncapped size the tape printed through the level
  (`tape_through` is capped at the order's size). The report splits fill
  share by whether `tape_printed` covered `queue_ahead` plus the order
  (a strict FIFO fill was possible) or not (only a front-of-queue fill
  could have happened). This is addendum 3's question, measured on real
  orders.

## How the evidence is kept

The executor writes the trail itself, so the read-out needs no extra
logging: every order's `raw` carries the arm that posted it, the book
it saw (best bid/ask and sizes), when it was placed, when it first
filled and how long it had rested; when it leaves the book -- filled,
cancelled at the TTL or pulled by the venue -- the executor applies the
paper venue's fill rule to the public tape over the window it rested in
and stores `tape_through`, the size the tape would have credited. Paper
orders carry both the venue's `tape_filled` and the shadow; they agree
by construction and the report prints the mismatch count as a
self-check. `sportsbot maker-report` reads it all back.

## Pre-registered read-out

Run until **200 fills** or **30 calendar days**, whichever comes first.
Then (`sportsbot maker-report --mode live`):

1. **Fill share.** Live fills divided by the fills the tape rule would
   have credited to the same orders. Result 15's numbers hold only in
   proportion to this share.
2. **CLV per fill.** Mean CLV at the venue close with a market-clustered
   bootstrap interval. The arm's gate (`docs/ALLOCATION.md`) is the usual
   one: ≥ 200 graded fills, interval lower bound above zero, Brier
   < 0.25, fees verified.
3. **Adverse selection by wait.** CLV per fill split by time-to-fill
   (< 30 s, 30–120 s, > 120 s). If the fast fills carry the CLV and the
   slow ones give it back, the TTL is the knob, not the size.

Decision rule, fixed now: the arm is funded beyond the test size only
if (1) × (Result 15's CLV) still clears the venue's maker fee **and** (2)
passes the gate on its own fills. Either failing closes rank 2 as a
standalone arm; informed-flow direction (queue item 2) is then its only
remaining form, data-logged.

## Rehearsal on Polymarket US (2026-10-19, paper, no keys)

`config/maker_test.yaml` was run for one cycle in paper, with `exchange:
polymarket_us` and a scratch store. Results:

- **Discovery and quoting:** 46 markets; the touch arm posted 6 orders
  (NHL 4, MLB 1, NFL 1); `still_resting` 0.
- **Order bodies:** in YES terms (a NO at 0.58 is a `BUY_SHORT` at 0.42),
  with `participateDontInitiate: true`.
- **Ticks and sizes:** prices sit on each market's tick (MLB 0.005, NFL
  0.0025, NHL 0.01). Every listed moneyline has `minimumTradeQty` 0.01,
  so the 2-decimal quantity a $1 stake gives is accepted.
- **Doctor:** in paper, WARN only (keys, tape). On a live copy without
  `SPORTSBOT_LIVE`: FAIL on both keys and on the missing tape. Rerun
  2026-10-26 on a live Polymarket US copy: the FAILs are exactly the
  operator's steps (both keys, the tape, `live.funded`) plus the
  `SPORTSBOT_LIVE` WARN; every code-side check passes. Since then an
  online doctor also runs `tape.polymarket_us.frame` (the `--verify`
  check): FAIL in live until a covered print confirms the YES frame.

The rehearsal found one measurement bug. With no tape recorded, every
order's shadow read `tape_through: 0.0`, a guess that a live run would
have reported as "the tape never reached us". No record is now a
`TapeGap`, and the shadow stays absent; a regression test covers it.

## Starting it (operator runbook)

Everything below is the operator's: nothing in the repository turns this
on. `config/maker_test.yaml` is the complete config for the test, shipped
in paper mode; `sportsbot doctor -c config/maker_test.yaml` is the
preflight and exits non-zero on any FAIL.

1. **Fund and key the venue.** A dedicated account holding only what the
   test may lose (the risk block stops the day at a $10 realised loss and
   kills the bot at $25; tens of dollars is the expected cost).
   *Polymarket US:* create a key at polymarket.us/developer and put
   `POLYMARKET_US_KEY_ID` and `POLYMARKET_US_SECRET_KEY` in `.env`, then set
   `exchange: polymarket_us` in your copy of the config (step 2). *Kalshi:*
   put `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` (mode 600, owned by
   the `sportsbot` user) in `.env` and `KALSHI_ENV=prod` (unset or `demo`
   sends orders to the demo exchange; `doctor` warns). The Kalshi key must
   be an **RSA** key from Kalshi's API-keys page: an Ed25519 key (what some
   other Kalshi tools use, ~120 bytes) cannot sign these requests, and
   `doctor` FAILs it. `ODDS_API_KEY` is optional: on either venue it
   gives the sharp close the read-out grades against (Kalshi NHL/NFL start
   times now come from the market itself).
2. **Make your own copies; never edit the tracked files on the server.**
   The daily `deploy/update.sh` pulls with `--ff-only` and stops on a
   locally modified tracked file. `*.local.yaml` is git-ignored:
   ```bash
   cd /opt/sportsbot
   sudo -u sportsbot cp config/maker_test.yaml config/maker_test.local.yaml
   sudo -u sportsbot cp config/allocation.yaml config/allocation.local.yaml
   ```
   In `maker_test.local.yaml` set `mode: live`, the venue (`exchange`),
   and `accounts.real.starting_balance` / `bankroll.amount` to the funded
   amount (the knobs are scaled for $100). It already writes its own
   `data/maker_test.sqlite` and reads `config/allocation.local.yaml`.
3. **Give the arm a budget, by hand, in `config/allocation.local.yaml`.**
   Live money reaches an arm only through a manual weight, a green gate,
   or a learning budget. For this test the learning budget is the right
   door, because it is bounded and pauses itself on a bad week:
   ```yaml
   learning:
     budget_usd: 30.0            # $10 a sport: ten concurrent $1 contracts each
     weekly_loss_stop_usd: 15.0  # pause the learning arms for the week at this 7-day loss
   arms:
     baseball/touch/maker: {weight: null, learn: true}
     hockey/touch/maker:   {weight: null, learn: true}
     football/touch/maker: {weight: null, learn: true}
   ```
   Those are the only lines that change. The per-sport cap
   (`max_fraction_per_sport`, 20% of the bankroll) still applies, so a
   $100 bankroll caps each sport at $20 whatever the budget says. If the
   file is missing, no arm gets money (fails closed).
   `sportsbot allocation -c config/maker_test.local.yaml` prints which
   arms are funded and why.
4. **Start the tape first** (Polymarket US only): once the keys are in
   `.env`, run `sudo systemctl restart sportsbot-ustape`. `update.sh`
   installs the unit, which exits cleanly while the keys are missing. Then
   check it with `sudo -u sportsbot .venv/bin/sportsbot us-tape --status`.
   After an hour or so of games, run `... sportsbot us-tape --verify`: it
   must end `us-tape verify: OK` (fields parse, prints are in YES terms).
   The doctor FAILs a live `polymarket_us` config whose tape is missing,
   silent or unverified, because the test would then measure nothing or
   measure it against the wrong side. Let the recorder run for at least a
   few minutes before the first order, since an order placed before a
   market's subscription has no shadow.
5. **Preflight, then run it as its own unit beside the sim.**
   ```bash
   sudo -u sportsbot env SPORTSBOT_LIVE=1 .venv/bin/sportsbot doctor -c config/maker_test.local.yaml
   ```
   No FAIL rows (a WARN for the not-yet-created database is expected).
   Then install `deploy/sportsbot-maker.service` and start it:
   ```bash
   sudo cp deploy/sportsbot-maker.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now sportsbot-maker
   journalctl -u sportsbot-maker -f
   ```
   Without `SPORTSBOT_LIVE=1` the runner logs that it is forcing paper
   mode and places nothing real, whatever the config says. On Polymarket
   US, before leaving it unattended, watch the first order by hand: check
   in the app that a NO order shows the price the log printed (the venue
   books it in YES terms) and that a maker order resting at the touch was
   not executed against on arrival (post-only). The daily update never
   restarts this unit: after reading a change that reached the server,
   `systemctl restart sportsbot-maker` yourself.
6. **Read it out** with `sportsbot maker-report -c config/maker_test.local.yaml
   --mode live` at 200 fills or 30 days, per the section above. **Stop it**
   with `systemctl disable --now sportsbot-maker`, or `touch
   data/KILL_SWITCH` for an immediate halt of every book on the box
   (`sportsbot reset-kill-switch` clears it).

## Cost of the test

At $1 a side and $4 per market the worst case is bounded by the sport
budget and the daily-loss switch in `bot/risk.py`; the expected cost is
the adverse selection on a few hundred one-dollar fills — tens of
dollars, not hundreds. The information is worth more than that; nothing
larger is justified until the share is known.

## What would make this moot

A fill share near zero (the queue is long and patient) ends it. A share
near one on a venue with a maker fee that eats the half-spread also ends
it. Anything in between is a number the allocator can use.
