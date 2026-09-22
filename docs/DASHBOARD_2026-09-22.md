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

## What the decision feed showed on the first live cycles

707 markets discovered, 298 scanned, **zero bets** — and the feed says why:

| passes | reason |
|---|---|
| 154 | spread wider than the 0.030 limit — fills would give back the edge |
| 134 | model not confident enough to price (uncertainty over 0.20) |
| 10 | no two-sided book |

Not one market reached the edge test. The binding constraints right now are
spread and model uncertainty, which is worth knowing before anyone reaches for
the edge threshold: loosening `min_edge` would change nothing here. This is
also why the feed records passes at all — on a near-efficient slate, a feed
that only showed bets would show an empty page and tell you nothing.

## Running it

```bash
sportsbot run -c config/sim.yaml     # the $100 paper book on live markets
sportsbot board --loop 60            # rebuild the page every minute
```

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
