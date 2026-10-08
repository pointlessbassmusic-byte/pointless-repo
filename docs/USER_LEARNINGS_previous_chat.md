# Polymarket Sports Trading — Consolidated Learnings
*Handoff for engine-optimization work. Compiled 2026-10-08 from the full build history (Jul–Sep 2026).*

---

## 0. One-paragraph summary

Every price-history-only strategy we tested on Polymarket sports lost after costs. Taker momentum: negative in all configs. Taker mean reversion: real but smaller than spread + fees. Anchorless maker quoting: zero fills or adverse selection. The 2026 sports fee schedule made all of this worse. The only thing that produced a measurable, fee-clearing edge in design was **external fair value** — a reference for what the price *should* be, coming from outside the price: devigged sharp-book odds (built), and game-state win-probability from the live score (built, MC-verified, feed integration pending). The system is now maker-first, fair-value-driven, with an optimizer that only recommends thresholds that survive an untouched holdout. **No live capital has been risked. No profitability has been demonstrated. Everything below is measured structure, not a result.**

---

## 1. The fee schedule — the fact that governs everything

Polymarket sports taker fee (2026):

```
fee_per_share = rate × p × (1 − p)        rate = 0.03 (Mar 2026) → 0.05 (Jul 2026)
```

- Makers pay **zero** and earn rebates (~15% on sports).
- At p = 0.50: 1.25% per side → a round-trip taker scalp costs **~5% of notional before spread**.
- At p = 0.93: ~0.33% per side. **Fees nearly vanish at the tails.** The fee curve tells you where to trade: mispriced favorites and longshots, not coin-flips at mid.
- Consequence: any strategy that crosses the spread twice to capture ~1¢ of noise is structurally dead. Frequency multiplies the toll, never the edge.

---

## 2. Falsified strategies — do not re-litigate without new data

| Strategy | Evidence | Verdict |
|---|---|---|
| Taker momentum / trend-following | Owner backtests on real minute data, all configs | Negative everywhere |
| Taker mean reversion (EWMA scalp) | Same data | Effect exists, smaller than spread + fees |
| Touch-joining maker quotes (no anchor) | Live paper session 1 | Zero fills |
| Hybrid maker-entry / taker-stop | ~200k tennis + ~83k baseball samples | Zero fills |
| Price-history regressions as alpha | 1-yr analyzer (calibration, shock-reversion, autocorr, OLS) | Useful for **calibration and parameters**, not as a signal source |

Why anchorless maker quoting fails: adverse selection. A resting bid fills precisely when someone who saw the point end knows the price should be lower. Without an independent fair value, a maker is paid the spread to be the slowest-informed participant.

---

## 3. What produced edge in design: external fair value

**Tier 1 — Sharp-book devig (built: `edge_model.py`)**
Pull h2h odds from a sharp book (Pinnacle preferred, then Betfair/Smarkets), strip the vig proportionally, compare to Polymarket mid. The entire strategy is one inequality:

```
trade iff |fair − mid| > half_spread + fee(p) + buffer
```

Maker when the gap is modest, taker only when it's large. Provider: the-odds-api (tennis + MLB). **No table tennis coverage there** — Setka Cup needs an OpticOdds / Sportradar / BetsAPI-class feed, *or* Tier 2.

**Tier 2 — Game-state Markov model (built: `markov_fair.py`, MC-verified)**
Tennis: full point → game → tiebreak → set → match chain with serve alternation. Table tennis: race-to-11, win-by-2, best-of-5. Anchor once per match (`fit_tennis(prematch_fair)` / `fit_tt(prematch_fair)` solves point-win probability so the fresh-match prob equals the pre-match price), then recompute fair on every score change.

Why this is the real moat — **swing sizes**:
- Deuce point at 4-4 in a deciding tennis set: fair moves **~9.7¢**
- 9-9 in a deciding table-tennis game: fair moves **~50¢**

You are not scalping noise against a 5% round-trip fee; you are front-running a repricing you *know* happened because you saw the score. Game-state edge doesn't fight the fee schedule, it dwarfs it. It also gives Setka Cup fair values with no odds provider at all. Remaining work: a live score feed beating the crowd's stream delay by seconds.

---

## 4. Architecture (as built, `~/sports-bot-v2`)

| File | Role |
|---|---|
| `edge_model.py` | Fair-value engine + edge logger + conservative taker-only paper. `--report` = **the decision tool** (net-edge frequency >1¢/>2¢, ~5-min convergence, paper P&L). |
| `fv_bot.py` v1.1 | Execution. Maker bid inside the gap at ≥ `MAKER_EDGE` (fee-free); taker at ≥ `TAKER_EDGE` after fees. Exits: CONVERGED, FAIR_STOP, MAX_HOLD, MARKET_GONE. Live mode polls exchange `size_matched` (partials, tracked/chased exits, cancel-all on start). Paper path byte-identical to v1.0. |
| `markov_fair.py` | Tier-2 fair value (tennis + TT). Pure math, no I/O. |
| `replay_edges.py` | Threshold optimizer over `TAKER × MAKER × EXIT` grid. **70/30 time split; recommends only holdout survivors.** Verified: recommends real edge, declines vanished edge. |
| `history_downloader.py` | 1-yr resolved markets + minute prices (windowed gamma scan, resume-safe). |
| `analyze_history.py` | Calibration (+ logistic slope/intercept), shock-reversion, lag-1 autocorr, OLS w/ t-stats, correlations → `analysis_workbook.xlsx` + `strategy_params.json`. |
| `CLAUDE.md` | On-box brief for the Claude Code session (runtime audit list, task queue, guardrails). |

Default thresholds: `TAKER_EDGE=0.04 MAKER_EDGE=0.015 EXIT_EDGE=0.005 STOP_FAIR=0.05 PER_TRADE_USD=10 MAX_OPEN_PER_SPORT=5`, 10% drawdown halt, `touch STOP` kill file.

---

## 5. Engine-optimization roadmap (the "replay everywhere" architecture)

This is the part the optimization chat should own.

1. **One log, many sources.** Add a `source` column to `edge_log.csv` rows (`devig` | `markov` | `ewma` | `blend_w`). Every fair-value producer writes the identical schema (`ts, sport, token, bid, ask, fair, source`). Backfill the dead EWMA model from `data/prices/*.csv` so the full year of history and the live logs replay through one harness. Replaying the falsified strategies should reproduce their failure — that is the harness's validation test.
2. **One policy, two consumers — highest-value refactor.** `fv_bot.on_row` and `replay_edges.replay_token` are two hand copies of the same rules; they *will* drift. Extract a pure, deterministic `policy.decide(book, fair, position, resting, params, now) → action`. Live bot and replayer call the same function. Backtest/live parity becomes structural.
3. **Three grid axes, one gate:** `source × thresholds × fill_model`, where fill_model ∈ {optimistic (crossed-ask, current), strict (trade must print through the quote), pessimistic (taker-only, fees both ways)}. A combo is believable only if it survives the holdout under **strict** and is non-negative under **pessimistic**. This turns the maker-fill-fantasy caveat into a column.
4. **Score models before policies.** Markets resolve and `markets.csv` holds resolutions. Compute Brier / log-loss per `source` on the holdout, **benchmarked against the market mid itself**. A model that doesn't out-predict the mid cannot be traded profitably at any threshold. Sweep blend weight `w·devig + (1−w)·markov` as another source. Far more sample-efficient than P&L replay.
5. **Then** wire the score feed, add `markov` as a live source, and let the Brier board decide the blend.

Rollout order: source column → policy extraction → fill-model axis → Brier scoreboard → markov/blend.

---

## 6. Known approximations and honesty notes

- **Paper maker fills are an upper bound.** Crossed-ask heuristic, no queue position. Treat paper P&L as optimistic until the strict fill model exists.
- History endpoint gives mid/last only — spread, depth, queue, and fees are **not** in the historical data. Paper/live fills remain the arbiter.
- The 1-yr history straddles the fee-regime change (free → 0.03 → 0.05). If full-year stats look muddy, slice post-July: `--days 130 --out data_recent`.
- In live mode, a position whose market vanishes is *booked* at last mark; real dollars arrive at resolution via redemption. Accounting approximation, not a loss.
- Live exits chase the bid rather than resting — deliberate: exit certainty over exit price.
- Markov model ignores serve effects in table tennis (empirically small) and averages set-start serve order (tiny error). Both verified against an independent Monte Carlo (n=30k, all within 4 SE).
- `py-clob-client` response field names (`size_matched`, `status`) drift between versions; the wrapper reads defensively but must be eyeballed against a real response on the first $1 order.

---

## 7. Guardrails (non-negotiable)

- Live requires **all** of: `DRY_RUN=false`, `LIVE_CONFIRM=I-ACCEPT-FULL-LOSS-RISK`, `PRIVATE_KEY` (+ `POLY_FUNDER` / `POLY_SIGNATURE_TYPE` for email-login wallets). The owner flips these; no automation sets them.
- Go-live gate **per sport**: 30+ paper fills AND positive P&L after modeled fees **on data the parameters were not tuned on**.
- First live run: one sport, taker-only (`MAKER_EDGE=0.99`), `PER_TRADE_USD` tiny, after a $1 single-order test confirms the order-status fields.
- Never claim or imply guaranteed profitability. Report measured numbers. If the shock-reversion sheet or the holdout says there's nothing after costs, believe it.

---

## 8. Open verification items (blocking everything downstream)

Nothing above has been confirmed against real server output since the fvbot session launched on Aug 19. Before any optimization work is trusted:

1. `edge_model.py --report` — is the net-edge distribution after fees non-trivial? Does it converge?
2. `tail fv_trades.csv` — are there fills at all, and what's the win rate by style (maker/taker)?
3. Coverage line from `overnight.log` — did the year of MLB + table-tennis history actually download?
4. `replay_edges.py` on the real log — does *anything* survive the holdout?
5. Odds-API quota — the free tier (~500 credits/mo) may be exhausted; `FAIR_POLL_SECONDS` controls burn.

The harness can be made universal now; its verdicts mean nothing until the real log feeds it.
