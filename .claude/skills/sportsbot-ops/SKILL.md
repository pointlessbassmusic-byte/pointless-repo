---
name: sportsbot-ops
description: Operate, evaluate, and safely evolve the sportsbot trading system in this repo — fitting models, running backtests, reading status/CLV, tuning config thresholds, and deploying to the server. Use when the user asks to run/check/tune/deploy the bot or interpret its performance.
---

# sportsbot operations

## Ground rules (never violate)

1. Paper mode is the default. Never set `SPORTSBOT_LIVE=1`, flip
   `mode: live`, or weaken a risk limit unless the user explicitly asks in
   this conversation AND the go-live gate in `deploy/DEPLOYMENT.md` is met
   (200+ paper bets / positive mean CLV / Brier < 0.25).
2. Never print, commit, or log private keys. Secrets live in `.env` only.
3. Never add code that evades Polymarket's geoblock. US-legal live venues
   are Polymarket US and Kalshi.
4. When editing `bot/risk.py` or `core/staking.py`, run
   `pytest tests/test_bot.py tests/test_core.py -q` before finishing.

## Common tasks

- **Health check**: `sportsbot status` → read exposure, PnL, mean CLV,
  Brier, kill-switch state. Positive mean CLV with flat PnL = keep going;
  negative CLV = model rot, recommend pausing.
- **Refresh ratings**: `sportsbot fit baseball` (minutes),
  `fit tennis` (downloads Sackmann CSVs), `fit table_tennis`
  (bootstraps from resolved Polymarket markets).
- **Evaluate a model change**: `sportsbot backtest <sport>` before and
  after; compare log loss to the benchmarks the CLI prints. A change that
  worsens walk-forward log loss is rejected regardless of story.
- **Dry scan**: `sportsbot scan` shows current markets, model vs market
  prices, and would-be intents without ordering.
- **Deploy**: commit → push → on server
  `git -C /opt/sportsbot pull && systemctl restart sportsbot`; verify with
  `journalctl -u sportsbot -n 50`.

## Tuning map

| knob | file | effect |
|---|---|---|
| blend.model_weight | config/default.yaml | trust in model vs market (raise only with CLV evidence) |
| bankroll.min_edge | config/default.yaml | bet selectivity |
| bankroll.kelly_multiplier | config/default.yaml | aggression (0.25 default; never > 0.5) |
| sports.*.min_edge_override | config/default.yaml | per-sport selectivity (TT is higher on purpose) |
| risk.* | config/default.yaml | kill switches — raise thresholds only with user sign-off |
