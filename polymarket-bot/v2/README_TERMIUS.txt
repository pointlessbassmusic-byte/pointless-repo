POLYMARKET SPORTS BOT v2 — TENNIS / TABLE TENNIS / MLB
======================================================

READ THIS FIRST — WHY v2 IS DIFFERENT
-------------------------------------
Polymarket introduced taker fees on sports in 2026 (fee = shares x rate x
p x (1-p); rate 0.03 in March, 0.05 since July). Near 50c that is ~1.25%
of shares per side — a round-trip taker scalp costs roughly 5% of notional
BEFORE spread. Makers pay zero and receive rebates.

Your own earlier backtests already showed taker momentum/mean-reversion
scalping loses after spread — fees made it worse. So v2 is MAKER-FIRST:

  scalp  — rest a bid inside wide spreads; profit target is a resting
           maker sell a few ticks up. Earns the spread instead of paying it.
  fade   — after a sharp drop ("retail overreaction"), rest a maker bid at
           a discount. Up-spikes are covered automatically: when YES spikes,
           NO crashes, and the bot fades the NO side.
  arb    — the only taker strategy: if YES.ask + NO.ask + fees < $1.00,
           buy both. Profit is locked at resolution regardless of outcome.
  regression loop — analyze_history.py studies ~1yr of real Polymarket
           data and writes strategy_params.json, which the bot loads to
           tune shock thresholds and fade targets per sport.

Taker orders are otherwise used only as stop-loss insurance and as a
timeout fallback when a maker exit will not fill.

TERMIUS SETUP — START TO FINISH
-------------------------------
1. Upload Polymarket_Sports_Bot_v2.zip to your server home folder (SFTP).
2. In a terminal tab:

   cd ~
   sudo apt-get update && sudo apt-get install -y unzip
   unzip -o Polymarket_Sports_Bot_v2.zip
   cd sports_bot_v2_package
   chmod +x setup_sports_bot.sh
   ./setup_sports_bot.sh

3. Pull historical data (run overnight; resumable if interrupted):

   cd ~/sports-bot-v2
   tmux new -s history '.venv/bin/python history_downloader.py --days 365'

4. Build the Excel workbook + tuned parameters:

   .venv/bin/python analyze_history.py

   -> analysis_workbook.xlsx  (calibration, shock-reversion, momentum,
                               correlations, OLS regressions — download it
                               via SFTP and open in Excel)
   -> strategy_params.json    (per-sport overrides the bot auto-loads)

5. Pick allocations and start the bot (paper mode by default):

   nano .env        # set SPORT_ALLOCATIONS=tennis=150,table_tennis=100,mlb=50
   tmux new -s sportsbot ./run_bot.sh

   Detach: Ctrl+B then D · Reattach: tmux attach -t sportsbot
   Logs:   tail -f ~/sports-bot-v2/sports_trading_bot.log
   Stop:   Ctrl+C inside tmux (all resting orders are cancelled)
   Panic:  touch ~/sports-bot-v2/STOP   (halts entries + cancels quotes)

RISK CONTROLS (all in .env / strategy_params.json)
--------------------------------------------------
- Per-sport bankroll allocation; per-trade cap; max open positions/sport
- Stop loss (taker, fee-aware), maker take-profit with taker timeout
- Max hold time; DAILY_MAX_DRAWDOWN_PCT halts entries and cancels quotes
- SESSION_PROFIT_LOCK_PCT optionally banks a good session
- KILL_FILE for an instant manual halt; clean cancel-all on shutdown

GOING LIVE — THE GATE
---------------------
Paper maker fills are simulated from top-of-book and OVERSTATE how often
your quote gets filled (no queue position). Treat paper P&L as an upper
bound. The gate you already set for the tennis maker bot still applies,
per sport:

  30+ paper fills AND positive net P&L after modeled fees
  on data the parameters were NOT tuned on.

Only then, in ~/sports-bot-v2/.env:

  DRY_RUN=false
  LIVE_CONFIRM=I-ACCEPT-FULL-LOSS-RISK
  PRIVATE_KEY=0x...     (Polymarket wallet: Settings -> Export private key)
  POLY_FUNDER=0x...     (your Polymarket proxy/deposit address, if email login)
  POLY_SIGNATURE_TYPE=1 (email/Magic) or 2 (browser wallet)

Fund the account in the Polymarket app first (depositing there also sets
the token approvals the exchange needs). Start with the smallest
allocations you would genuinely not miss. Anything less than all three
gate variables set correctly and the bot refuses live mode.

The live order layer could not be exercised against the real exchange
from inside this build environment — on first live run, start with one
sport and a tiny allocation and watch the log for order rejections.

HONEST EXPECTATIONS
-------------------
Nothing here guarantees profit. Fees, adverse selection (your resting bid
gets filled precisely when someone knows the point just ended), and queue
position all eat maker edge. The workbook exists so decisions come from
measured effects with t-stats, not vibes — if the shock-reversion sheet
shows nothing after costs, believe it.
