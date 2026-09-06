# Claude Code Handoff — Polymarket Sports Trading Bot (Optimal Build)

## EMPIRICAL VERDICT (Jul 17, 2026 — read this first)

Retro-backtests on 268,026 REAL minute-level price points (186 live
Tennis/Baseball markets, via clob prices-history):

1. **Momentum-taker is falsified.** All 72 configs negative even at ZERO
   spread (win rates 1–16%, exp −$0.44 to −$1.01 per $10 trade). Minute-scale
   jumps systematically revert; buying them means buying local tops. Do not
   tune this further.
2. **Mean reversion exists but is smaller than trading costs.** Fading drops
   showed 67–88% win rates and positive expectancy at spread ≤0.01, flipping
   sharply negative at realistic 0.02–0.03 spreads (small samples, 6–28
   trades — directional evidence, not a proven strategy).
3. **Conclusion: the harvestable edge on these markets belongs to MAKERS** —
   resting orders that receive the spread, pay zero fee, and earn the 15%
   sports rebate, monetizing exactly the reversion that kills takers.
4. **Maker touch-joining also found no harvestable edge in session 1** (Jul 17
   tennis day, 193k real tennis bid/ask samples): only 8 moments all day had
   spread ≥ 0.02 inside the 0.15–0.85 band, and NONE filled within 2–5 min
   windows. Tennis books are quoted tight by incumbent makers; where spread
   existed, nothing crossed. Caveats: quote-snapshot granularity (~80s/token
   in quiet markets) cannot observe intra-interval fills, and overnight
   baseball was pre-game only. Session 2 (live MLB slate) must be re-run
   before concluding — but v1 conclusion stands: JOINING the touch is not a
   strategy; a real maker build needs full order-book WS capture (depth +
   queue position), quoting inside wide books only, with inventory management.
5. Data notes: prices-history serves ACTIVE markets only (resolved markets
   return empty); fetch via token ids from real_ticks.csv. Minute fidelity may
   overstate reversion via quote bounce — the maker build must validate
   against real_ticks.csv (true bid/ask) fill-simulation.

**Build priority is therefore inverted from the gap list below: maker-mode
is item #1.** The taker bot remains useful as data infrastructure
(discovery, WS streaming, tick recorder, dashboards) — its strategy layer
should not go live with capital in any parameterization.

## What this project is

Automated Tennis/Baseball trading on the Polymarket CLOB. Momentum entries on
live WebSocket quotes, three risk models (safe/base/risky) per sport with
independent capital buckets, fee-aware asymmetric exits. One consolidated
codebase (`optimal_trading_bot.py`, ~1300 lines, stdlib + requests + websockets;
py-clob-client only for live mode).

## Lineage — what was tried and what survived

The owner built four iterations before this. What each contributed:

| Build | Kept | Discarded |
|---|---|---|
| V2 "Trade Fix" (best codebase — this build's base) | Async architecture, quote/registry model, CSV audit trail, adaptive tick-size triggers, depth/staleness filters, %-of-bucket sizing, cooldowns | Flat fee rate defaulted to 0; tiny TP targets; BOOTSTRAP_TRADES on by default |
| HFT Hybrid ($150/6-bucket) | Equal-bucket idea informs allocations; spread-expansion (adverse selection) filter is worth porting — **not yet ported** | Sub-noise z-triggers (0.2σ); inverted TP/SL on "risky"; 3-min forced exits |
| Dual Sports V7 (auto-tuner) | Concept of runtime-tunable params via .env | "ML" tuner read a synthetic Excel file; DRY_RUN_SPAM_TEST forced negative-edge trades |
| Orchestrator/TradingEngine | Sub-market discovery goal (sets/innings) — partially covered by keyword lists | Per-market connection model (doesn't scale vs. one shared WS) |

## Critical context: the two data honesty issues

1. **`Polymarket_Historical_Data_30Days.xlsx` is synthetic.** 45.9% of rows have
   ask < bid (impossible), prices are uniform noise in 0.40–0.60, volatility is
   uniform in 0.01–0.05, and no stage structure exists (pre/during/post vols are
   identical). **Nothing was calibrated from it and nothing should be.** Same
   applies to `Simulated_Matches_Future.xlsx` from the V7 build. The only
   trustworthy data source in this project is the bot's own
   `dry_run_trades_optimal.csv` accumulated against live quotes.
2. **Earlier dashboards overstated performance.** V2 shipped with
   BOOTSTRAP_TRADES=true (forced entries so demos show activity) and zero fees.
   Any historical PnL screenshots from prior sessions are not evidence.

## The fee math that reshaped the strategy

July-2026 Polymarket sports taker fee: `fee = 0.05 × p × (1−p) × shares`
(≈1.25% of notional at p=0.50, →0 at extremes; verify current rate at
docs.polymarket.com/trading/fees — it changed twice in 2026 already).

Consequence: a taker round trip at mid prices costs ~0.025/share. V2's baseball
TP of 0.007 meant **every winning trade lost ~0.018/share net**. This build:
- Models the fee exactly in entry/exit PnL (`taker_fee()`)
- Raised TP floors (Baseball 0.032/SL 0.020, Tennis 0.042/SL 0.026 — TP > SL
  everywhere, enforced by self-test)
- Made TP dynamic: `required_tp = max(profile.tp, round_trip_fee + tick)`
- Self-test (`--self-test`) fails if any profile is fee-unprofitable

## Safety architecture (do not weaken)

- `DRY_RUN=true` default; simulated fills against live bid/ask
- Live requires ALL of: `DRY_RUN=false`, `LIVE_CONFIRM=I_UNDERSTAND_LIVE_RISK`,
  `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER`
- Live refuses to start if `BOOTSTRAP_TRADES=true`
- Failed live sells keep the position tracked for retry (never silently drop)
- `.env` is chmod 600; key never in code or logs

## Known gaps / next work (in priority order)

1. **Live fill verification.** `LiveExecutor.market_buy/sell` posts FOK orders
   but the recorded position uses quoted price, not actual fill. Parse the
   post_order response for real fill price/size before recording. This is the
   single most important gap before live use.
2. **Port the spread-expansion filter** from the HFT build (block entry when
   spread widened >10% tick-over-tick) — cheap adverse-selection protection.
3. **Maker-order mode.** Makers pay zero fee + earn rebates (15% on sports).
   A resting-limit entry variant would remove the 0.025/share fee drag entirely
   at the cost of fill uncertainty. Likely the real edge unlock.
4. **Volume data.** Gamma events carry volume/liquidity fields; rank candidates
   by actual volume, not just book depth ("high volume focus" is currently
   depth-proxied).
5. **Sub-market discovery.** Keyword lists catch match winners; set/inning
   markets need Gamma market-level (not event-level) scanning.
6. **Parameter sweeps** — partially DONE: `replay_harness.py` sweeps a 48-combo
   grid against recorded real ticks (`real_ticks.csv` from the bot's built-in
   tick recorder) with a 60/40 fit/holdout time split and fees always on.
   Extend to walk-forward with more windows as data accumulates.

## Deploy target

Ubuntu 24.04 VPS, root, tmux session `optimalbot`, dir `/root/sports-bot-optimal`.
See TERMIUS_RUNBOOK.md for exact commands. `install_optimal.sh` is idempotent
and backs up any existing bot before overwrite.

## Files in this handoff

- `optimal_trading_bot.py` — the consolidated bot (now with tick recorder + progress log)
- `replay_harness.py` — parameter sweeps vs recorded real ticks, fit/holdout split
- `fetch_history.py` — pulls real 30-day price history for resolved sports markets (VPS only)
- `install_optimal.sh` — idempotent installer (deps, venv, .env, tmux)
- `TERMIUS_RUNBOOK.md` — operator commands + live-switch procedure
- `polymarket_bot_handoff_spec.md` — full history of all four prior builds
