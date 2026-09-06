---
name: pre-live-gate
description: Checklist gate to run BEFORE enabling live trading (live:true + --live) on any engine in this repo. Use when the user asks to go live, flip an engine to live mode, or asks whether an engine is ready for real money. Blocks the flip unless every check passes.
---

# Pre-Live Gate

Evaluate whether an engine (polymarket-bot, kalshi-engine) is ready for real
money. Do not edit `live:` in any config until every check below passes; report
each check's status to the user either way.

Checklist concepts adapted from tradermonty/claude-trading-skills
(pre-trade-discipline-gate, drawdown-circuit-breaker; MIT).

## Checks

1. **Dry-run history** — the engine's SQLite DB (`data/*.db`) holds at least 7
   days of scans: `SELECT MIN(ts), MAX(ts), COUNT(*) FROM scans`.
2. **Calibration beats the market** — `python -m src.report` shows the model's
   Brier score at or below the market baseline on a meaningful sample
   (n >= 100 resolved outcomes). A model that loses to the market baseline has
   no edge to monetize.
3. **Settlements populated** — the settlements table is non-empty, so the
   daily-loss circuit breaker has real data to act on.
4. **Risk config sane** — `risk.max_daily_loss_usd` is set and is a small
   fraction of `strategy.bankroll_usd`; `strategy.max_total_exposure` and
   `max_stake_per_market` are values the user confirms they can lose.
5. **Kill switch tested** — `touch data/KILL_SWITCH`, run one `--once --live`
   cycle, confirm orders are recorded as `blocked:kill switch...`, then remove
   the file. Never skip this: it is the only manual off-switch.
6. **Credentials scoped** — API keys in `.env` only (never committed), and for
   kalshi-engine the user explicitly chose `use_demo: false`.
7. **Bankroll confirmation** — the user states, in their own words, the max
   amount they accept losing. Do not proceed on silence.

## Output

Print a pass/fail table with evidence per check. If anything fails, name the
smallest next step that would make it pass (e.g. "keep scanning until N>=100").
Only after all seven pass, make the two-switch change the user asked for.
