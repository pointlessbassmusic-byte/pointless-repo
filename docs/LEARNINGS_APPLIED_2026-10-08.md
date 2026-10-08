# Learnings from the previous (non-Claude-Code) chat — what was applied (2026-10-08)

Source: `docs/USER_LEARNINGS_previous_chat.md` (the fv_bot / edge_model
generation on the VPS).

| Learning | Status in this repo |
|---|---|
| Fees govern everything; trade where p(1−p) is small, never scalp mid | Already enforced (fee-aware edges; entry band). Polymarket US fees updated in docs: taker 0.0695, maker rebate 0.0125 |
| Falsified strategies (taker momentum, mean-reversion scalp, anchorless maker quoting) | Independently re-falsified here on Kalshi (SURVEY, CRYPTO15, LIP_HIST, SETTLE_VPIN) |
| **External fair value**, tier 1: de-vigged sharp book | `polymarket-edge/` already does this. The deciding experiment is a CLV harness against Pinnacle, which needs `ODDS_API_KEY` (see the report) |
| **External fair value**, tier 2: game-state Markov | **Added** `tennis_win_prob_from_state`, `tennis_point_leverage`, `tt_win_prob_from_state` (`sportsbot/core/markov.py`). Monte Carlo-verified. First use is *defensive*: pull or widen maker quotes before high-leverage points |
| One policy, two consumers (live = replay) | **Done** for the Kalshi backtests: `live_policy_bet` calls `bot.strategy.evaluate_market_verbose`. Default `policy=live`; `legacy` reproduces old docs |
| Fill-model axis (optimistic / strict / pessimistic) | **Added** `fill_model` pessimistic / optimistic to the backtests. "Strict" (trade-through) lives in the tape-based research tools (`research/settle_vpin`, `research/lip_hist`) |
| Score models before policies (Brier vs market mid per source) | Already the repo's method (VS_MARKET, EDGE_VERDICT beta tests): the MLB model beta is 0.065, t 0.20, i.e. no edge |
| Maker fee realism | **Fixed**: Kalshi maker fee is now 0.0175·mult·p(1−p) when priced; Challenger/ITF are maker-free (series fee_type verified live) |
| Open verification: ~7 weeks of v3 paper logs on the VPS never scored | **Needs the owner**: pull `edge_log.csv` and `fv_trades.csv` (below) |

## Pulling the v3 logs (owner, from a machine with SSH to the VPS)
`scripts/pull_from_server.sh` deliberately skips data. The logs hold only
prices, fair values and paper fills, no secrets. Pull them with:

    rsync -avz root@97.107.138.196:'~/sports-bot-v2/edge_log.csv' \
          root@97.107.138.196:'~/sports-bot-v2/fv_trades.csv' polymarket-bot/v3-logs/

Then commit them (or share them in chat). They answer the CLV question
directly: de-vigged sharp fair value against the Polymarket mid, with
convergence and paper fills, over about seven weeks.
