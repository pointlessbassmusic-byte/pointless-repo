# PROJECT: Polymarket Sports Betting Bot

_Master project file — everything about this engine lives here.
See [`CLAUDE.md`](CLAUDE.md) (imported handoff, 2026-08-19) for the full context that
produced the current thesis; its go-live gates are binding._

## Generation lineage (newest first)

| Gen | What | Where | Status |
|---|---|---|---|
| **v3 — fair-value system** (`edge_model.py` + `fv_bot.py`) | Devigged sharp-book fair value (Pinnacle-preferred) vs Polymarket; maker orders inside the gap; taker only on large dislocations / stop-loss | **VPS only**: `~/sports-bot-v2` — not yet in repo; retrieve with `scripts/pull_from_server.sh` | **Current production** (paper mode, tmux `fvbot`) |
| v2 — websocket bot (`trading_bot.py`) | Real-time CLOB books, scalp/fade/complement-arb, offline self-test | [`v2/`](v2/) | Superseded as strategy; **infra source** for task 7 (websocket books) |
| v1 — consensus scanner | Gamma discovery → The Odds API devigged multi-book median → edge vs CLOB ask | `src/` | Experimental (built 2026-09-06); same fair-value thesis as v3, taker-style execution doesn't survive fees. Reusable parts: devig/consensus code, market↔game matcher |
| dashboard paper trader (Aug 14) | Tennis-only paper trading vs **SharpOracle** external fair value; multiplier exits; the fair-value thesis precursor | [`archive/dashboard-aug2026/`](archive/dashboard-aug2026/) | Superseded by v3 |
| "Optimal Build" (Jul 17) | Consolidated momentum-taker bot + tick recorder + replay harness + paper quoter; produced the falsification verdicts below | [`archive/optimal-jul2026/`](archive/optimal-jul2026/) | Strategy dead; tick/replay/quoter infra reusable |
| pre-history | ~15 iterations in the VPS home dir (tennis_h2h v5–v11, sharp_oracle, hybrid/hf/final, …) | VPS `~` (archive queued) + [`archive/paste-era/`](archive/paste-era/) installers | Graveyard — do not modify |

Full generation details and reuse notes: [`archive/README.md`](archive/README.md).
Active maker research (paper-only L2 capture + pre-registered replay, drop 5):
[`maker-lab/`](maker-lab/README.md) — the working pattern for tasks 6–7 below.

## Hard-won conclusions — do not relitigate without new data

(Condensed from the Aug 19 handoff in [`CLAUDE.md`](CLAUDE.md) and the Jul 17 optimal-build
handoff in [`archive/optimal-jul2026/CLAUDE_CODE_HANDOFF.md`](archive/optimal-jul2026/CLAUDE_CODE_HANDOFF.md).)

0. **Data honesty (Jul 17):** the 30-day historical xlsx and "Simulated_Matches" xlsx were
   synthetic — nothing calibrated from them counts. Old V2 dashboard P&L screenshots are not
   evidence (BOOTSTRAP_TRADES + zero fees). The only trustworthy data is the bots' own logs
   against live quotes, and `real_ticks.csv`-style true bid/ask capture.

1. **Fees (2026):** sports taker fee = `shares × rate × p × (1−p)`; rate 0.03 from
   2026-03-30, **0.05 since July 2026**. Makers pay zero + share a 15% rebate pool.
   Round-trip taker near p=0.5 ≈ 5% of notional. Fees ≈ 0 at price extremes — the schedule
   itself favors trading mispriced favorites/longshots over coin-flips.
2. **Falsified by the user's own backtests:** taker momentum (negative in all configs);
   taker mean-reversion (edge < spread+fees); anchorless maker quoting (no fills / adverse
   selection — resting bids fill exactly when a point is lost).
3. **Current thesis:** external fair value from devigged sharp-book odds vs Polymarket.
   Maker orders inside the gap (≥ `MAKER_EDGE`); taker entries only ≥ `TAKER_EDGE` or as
   stop-loss insurance. Polymarket is the soft venue; sportsbooks are the sharps.
4. **Table tennis** trades on Polymarket (Setka Cup + WTT) but the-odds-api has no TT —
   fair-value coverage needs an OpticOdds / Sportradar / BetsAPI-class provider.

## v3 components (on the VPS, venv at `.venv`)

| File | Purpose |
|---|---|
| `history_downloader.py` | Windowed Gamma scan + CLOB minute prices → `data/markets.csv`, `data/prices/*.csv` (resume-safe) |
| `analyze_history.py` | Calibration, shock-reversion, momentum autocorr, OLS, correlations → `analysis_workbook.xlsx` + `strategy_params.json` |
| `edge_model.py` | Fair-value engine + edge logger + conservative taker-only paper; `--report` summarizes `edge_log.csv` |
| `fv_bot.py` | Execution layer: maker bid inside gap, taker entry, convergence exits, `FAIR_STOP`, `MAX_HOLD`, per-sport allocations, drawdown halt, `STOP` kill file. Paper by default. Writes `fv_trades.csv`, `fv_session.log`, `edge_log.csv` |

Key `.env` knobs and odds-credit economics: see the handoff. Known caveats: paper maker
fills use a crossed-ask heuristic (optimistic); **live maker fill tracking is not
implemented** (first live runs must be taker-only, `MAKER_EDGE=0.99`); the live order layer
has never touched the real exchange.

## Go-live gate & safety rails (non-negotiable — from the handoff)

- Gate, per sport: **30+ paper fills, positive net P&L after modeled fees, on data the
  thresholds were not tuned on.**
- Live requires all three: `DRY_RUN=false`, `LIVE_CONFIRM=I-ACCEPT-FULL-LOSS-RISK`,
  `PRIVATE_KEY`. Never weaken these gates or their checks in code.
- `PRIVATE_KEY` lives only in the VPS `.env`. Never echo, log, commit, or copy it.
  `.env` is gitignored in this repo.
- `touch ~/sports-bot-v2/STOP` = instant halt + quote cancellation.
- Claude Code never flips live mode on its own initiative — that switch belongs to the
  human, after the gate. (Also: Polymarket blocks US persons — confirm eligibility.)

## Task queue (from the handoff, adapted to the laptop-era repo)

0. **Bring v3 into the repo:** run `scripts/pull_from_server.sh` from the laptop (pulls
   code + `strategy_params.json`, excludes `.env`/data/venv), review, commit as `v3/`.
1. Status audit on the VPS (fv_session.log, overnight.log, data/prices count, workbook,
   `tmux ls`) — report before changing anything.
2. If history incomplete → rerun downloader (resume-safe) → analyzer → restart `fvbot`.
3. After a real session: `edge_model.py --report`; decide on net-edge frequency,
   convergence shrink, closed paper P&L.
4. Zombie tmux cleanup (**confirm `recorder` with the user first**) + archive old bots.
5. Tune `TAKER_EDGE`/`MAKER_EDGE`/`EXIT_EDGE` from report data; consider price-extreme tilt.
6. Live maker fill tracking via py-clob-client `get_order` polling; reconcile from exchange.
7. Real-time books: swap Gamma polling for the CLOB websocket (pattern in `v2/trading_bot.py`).
8. TT fair-value provider adapter behind a `fetch_fair`-compatible interface.
9. If fair-value edge insufficient → Tier 2: game-state Markov win-prob from live scores.
10. Go-live procedure — never before the gate.

Cross-project: v3's `edge_log.csv`/`fv_trades.csv` and the history downloader's output are
the milestone-1 input for the substrate real-history backtest
([`../substrate/PROJECT.md`](../substrate/PROJECT.md)).

## APIs

| Source | Base URL | Used by |
|---|---|---|
| Gamma API | `https://gamma-api.polymarket.com` | v3 books (12s poll), v1 discovery, history downloader |
| CLOB API | `https://clob.polymarket.com` | prices/books; orders via `py-clob-client` (live layer untested) |
| The Odds API | `https://api.the-odds-api.com/v4` | v3 + v1 fair value (`ODDS_API_KEY`; free tier 500/mo — kill idle sessions) |
