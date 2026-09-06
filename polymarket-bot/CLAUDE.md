> **Imported 2026-09-06 (chat-handoff drop 2), verbatim below this note.** Original context:
> written 2026-08-19 for Claude Code running ON the VPS in ~/sports-bot-v2. In this repo the
> durable content (fees, falsified strategies, thesis, gates, task queue) is folded into
> PROJECT.md; operational state (tmux sessions, pending reboot) is a snapshot of Aug 19 and
> may be stale. The fv_bot/edge_model code is NOT yet in the repo — see scripts/pull_from_server.sh.

# CLAUDE.md — Polymarket Fair-Value Sports Bot

Handoff from Claude chat (mobile) → Claude Code (desktop), 2026-08-19.
This file is the project's persistent context. Read fully before acting.

## What this is

A fair-value trading system for Polymarket sports markets (tennis + MLB live;
table tennis pending an odds provider), running on the user's Linode VPS.
Everything so far was delivered as terminal heredoc pastes from a phone;
Claude Code now takes over maintenance, tuning, and buildout.

## Server

- Linode, Ubuntu 24.04 (noble), login as `root`. Last known IP: 97.107.138.196
  (verify with the user; prompt shows `root@localhost`).
- Two ways to work:
  - **A (preferred):** install Claude Code on the VPS itself and run it in
    `~/sports-bot-v2`. Native installer (recommended, no Node needed):
    `curl -fsSL https://claude.ai/install.sh | bash`. npm is legacy
    (`npm install -g @anthropic-ai/claude-code`, needs Node 22+).
    Docs: https://docs.claude.com/en/docs/claude-code/overview
  - **B:** run from desktop over SSH. Set up key auth first
    (`ssh-copy-id root@<IP>`) so commands never hang on password prompts.
- A kernel reboot has been pending for days. Reboot only when no tmux job is
  mid-run (downloads are resume-safe; fvbot restarts cleanly).
- `~` is a graveyard of ~15 prior bot iterations (tennis_h2h v5–v11,
  sharp_oracle, hybrid/hf/final, an old ChatGPT "Sports_Bot_V2_Trade_Fix").
  Do not modify them; archiving to `~/archive` is queued as a task.

## Hard-won conclusions — do not relitigate without new data

1. **Fees (2026):** sports taker fee = `shares × rate × p × (1−p)`;
   rate 0.03 from 2026-03-30, **0.05 since July 2026**. Makers pay zero and
   share a 15% rebate pool. Round-trip taker near p=0.5 ≈ 5% of notional.
   Fees nearly vanish at price extremes — the schedule itself favors trading
   mispriced favorites/longshots over coin-flips.
2. **Falsified by the user's own backtests:** taker momentum (negative in all
   configs), taker mean-reversion (edge smaller than spread+fees), and
   anchorless maker quoting (no fills / adverse selection — resting bids fill
   exactly when a point is lost).
3. **Current thesis:** external fair value (devigged sharp-book odds,
   Pinnacle-preferred) vs Polymarket. Maker orders inside the gap; taker only
   on large dislocations or as stop-loss insurance. Polymarket is the soft
   venue; sportsbooks are the sharps.
4. **Table tennis:** trades on Polymarket (Setka Cup + WTT, high match
   turnover) but the-odds-api has no TT. Fair-value coverage needs an
   OpticOdds / Sportradar / BetsAPI-class provider (task 8).

## Files in ~/sports-bot-v2 (venv at .venv — always `.venv/bin/python`)

| File | Purpose |
|---|---|
| `history_downloader.py` | Windowed Gamma scan (end_date windows, resume-safe) + CLOB minute prices → `data/markets.csv`, `data/prices/*.csv` |
| `analyze_history.py` | Calibration (+logistic fit), shock-reversion, momentum autocorr, OLS w/ t-stats, correlations → `analysis_workbook.xlsx` + `strategy_params.json` |
| `edge_model.py` | Fair-value engine + logger + conservative taker-only paper. `--report` summarizes `edge_log.csv`: net-edge frequency after fees, ~5-min convergence, paper P&L |
| `fv_bot.py` | Execution layer (imports edge_model): maker bid inside gap (≥ MAKER_EDGE), taker entry (≥ TAKER_EDGE), convergence exits, FAIR_STOP, MAX_HOLD, per-sport allocations/caps, drawdown halt, `STOP` kill file. Paper by default. Writes `fv_trades.csv`, `fv_session.log`, `edge_log.csv` |
| `.env` | All knobs + `ODDS_API_KEY` (the-odds-api.com). Live gates commented out |

The full websocket bot (`trading_bot.py`: real-time CLOB books, complement
arb, offline self-test) lives in `Polymarket_Sports_Bot_v2.zip`, downloadable
from the chat — useful as infra source for task 7, superseded in priority by
`fv_bot.py`.

## tmux state at handoff

- `fvbot` — running `fv_bot.py` (paper) since Aug 19 ~16:00 UTC.
- `history` — **absent**: the overnight job
  (`history_downloader.py --days 365 --sports table_tennis,mlb` chained into
  `analyze_history.py`, logging to `overnight.log`) either completed (session
  exits on completion) or never started. Verify first (task 1).
- Zombies: `polybot` ×2 (one with a mangled name) + `quoter` since Jul 17,
  `recorder` since Aug 14. `recorder`'s purpose is unconfirmed —
  **ask the user before killing it**; the July ones are the falsified-strategy
  generation.

## .env knobs (defaults in code)

`FAIR_POLL_SECONDS=90` (odds API, costs credits) · `BOOK_POLL_SECONDS=12`
(Gamma, free) · `TAKER_EDGE=0.04` · `MAKER_EDGE=0.015` · `EXIT_EDGE=0.005` ·
`STOP_FAIR=0.05` · `MAX_HOLD_SECONDS=3600` · `ORDER_TTL_SECONDS=150` ·
`TICK=0.01` · `PER_TRADE_USD=10` · `MAX_OPEN_PER_SPORT=5` ·
`SPORT_ALLOCATIONS=tennis=100,mlb=100` · `DAILY_MAX_DRAWDOWN_PCT=10` ·
`KILL_FILE=STOP` · `TAKER_FEE_RATE=0.05`
Live only: `DRY_RUN`, `LIVE_CONFIRM`, `PRIVATE_KEY`, `POLY_FUNDER`,
`POLY_SIGNATURE_TYPE` (1 = email/Magic proxy, 2 = browser-wallet proxy).

Odds-credit economics: ~1 call per active sport key per FAIR_POLL; free tier
500/month ≈ a 4–6 h session at 90 s. `tmux kill-session -t fvbot` when idle to
stop burn.

## Known caveats in current code

- Paper maker fills use a crossed-ask heuristic (no queue position) —
  an optimistic upper bound. The go-live gate exists because of this.
- **Live maker fill tracking is not implemented** (the local heuristic would
  desync from the exchange). First live runs must be taker-only:
  `MAKER_EDGE=0.99`.
- Live taker fills are recorded at the ask (approximation); tick is fixed at
  0.01; books come from Gamma polling (12 s), not websocket.
- The live order layer (py-clob-client) has never touched the real exchange.

## Task queue (ordered)

1. **Status audit:** `tail fv_session.log`, `tail overnight.log`,
   `ls data/prices | wc -l`, check for `analysis_workbook.xlsx` +
   `strategy_params.json`, `tmux ls`. Report before changing anything.
2. If history incomplete → rerun downloader (resume-safe), then analyzer, then
   restart `fvbot` so it loads `strategy_params.json`.
3. After a real session (evening MLB slate / tennis day):
   `.venv/bin/python edge_model.py --report`. Decision metrics: % polls with
   net edge >1¢/2¢ per sport, convergence shrink %, closed paper P&L.
4. Zombie cleanup (confirm `recorder` with user) + archive old bots from `~`.
5. Tune `TAKER_EDGE`/`MAKER_EDGE`/`EXIT_EDGE` from report data; consider a
   price-extreme tilt (fees ≈ 0 in the tails).
6. Live maker fill tracking: poll order status via py-clob-client
   (`get_order`) instead of the crossed-ask heuristic; reconcile local
   positions from the exchange.
7. Real-time books: swap Gamma polling for the CLOB websocket (working pattern
   in the zip's `trading_bot.py`).
8. TT fair-value provider adapter behind a `fetch_fair`-compatible interface.
9. If fair-value edge is insufficient → Tier 2: game-state Markov win-prob for
   tennis/TT from a live score feed (only edge class that beats taker fees on
   speed).
10. Go-live procedure — never before the gate below.

## Go-live gate & safety rails (non-negotiable)

- Gate, per sport: **30+ paper fills, positive net P&L after modeled fees, on
  data the thresholds were not tuned on.**
- `DRY_RUN=true` is the default. Live requires all three: `DRY_RUN=false`,
  `LIVE_CONFIRM=I-ACCEPT-FULL-LOSS-RISK`, `PRIVATE_KEY`. Never weaken these
  gates or their checks in code.
- `PRIVATE_KEY` lives only in `~/sports-bot-v2/.env`. Never echo, log, commit,
  or copy it. If a git repo is created, `.env` goes in `.gitignore` first.
- First live run: one sport, taker-only, minimal `PER_TRADE_USD`, watch the
  log for order rejections.
- `touch ~/sports-bot-v2/STOP` = instant halt + quote cancellation.
- Claude Code does not flip live mode on its own initiative under any
  circumstances — that switch belongs to the human, after the gate.
- Test discipline: `py_compile` + module self-tests before restarting any tmux
  session. Restart pattern:
  `tmux kill-session -t fvbot 2>/dev/null; tmux new-session -d -s fvbot 'cd ~/sports-bot-v2 && .venv/bin/python fv_bot.py 2>&1 | tee fv_session.log'`

## Suggested first prompt

> Read CLAUDE.md, then run the status audit from task 1 and report what you
> find before changing anything.
