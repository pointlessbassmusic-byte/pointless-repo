# sportsbot — repo guide for Claude Code

Consolidated sports prediction engine + exchange trading bot.
Polymarket (tennis / MLB / table tennis moneylines) live-capable in paper
mode; Kalshi client implements the same interface for the US-legal path.

## Layout

- `sportsbot/core/` — pure math + shared types (odds, Kelly, Elo, Markov,
  calibration). No I/O here, no venue knowledge; everything imports it.
- `sportsbot/engine/` — `SportModel` per sport: `tennis.py` (surface-blended
  Elo + O'Malley/Markov best-of translation), `baseball.py` (Elo + home adv
  + starting-pitcher overlay), `tabletennis.py` (high-K Elo).
- `sportsbot/data/` — Sackmann tennis CSVs, MLB Stats API, table tennis
  bootstrap from resolved Polymarket markets; SQLite store.
- `sportsbot/exchanges/` — `polymarket.py` (Gamma discovery + CLOB;
  trading via the `polymarket-client` py-sdk — the old `py-clob-client` is
  archived/dead, never reintroduce it), `kalshi.py` (2026 API: dollar-string
  prices, Create Order V2 bid/ask semantics, RSA-PSS signing), `paper.py`.
- `sportsbot/bot/` — scanner (entity matching is conservative: unmatched =
  skip), strategy (market blend, book-walking, maker-first), risk (fails
  closed; kill switches), executor, arb, runner.
- `sportsbot/backtest/` — walk-forward with side randomization.
- `sportsbot/substrate_bridge/` — data adapters feeding `substrate/`
  (bot-log export, Kalshi weather snapshots). Data only; never wires
  substrate output into trading.
- `substrate/` — Substrate/Echo certification engine, consolidated
  VERBATIM from its own handoff. Read `substrate/HANDOFF.md` before
  touching it. `substrate/PROTOCOL_v1.md` is FROZEN (hash-committed):
  never edit it; parameter changes require a new v1.x per its amendment
  policy. Shadow mode is non-negotiable — do not wire live staking.
- `maker/` — standalone paper-only maker research scripts (L2 capture bot
  + replay optimizer), kept as verbatim consolidations with self-tests
  (`python3 maker/<file>.py --self-test`).

## Conventions

- All prices are probabilities in [0,1] internally; Kalshi cents/dollar
  strings convert at the client boundary only.
- `Prediction.prob_yes` = P(MarketInfo.home / outcomes[0] wins).
- Secrets only via env vars / `.env` (gitignored). Never write keys to
  config files or logs.
- Paper mode is the default; live requires BOTH `mode: live` in config and
  `SPORTSBOT_LIVE=1` in the environment. Don't weaken this.
- Risk checks fail closed — keep it that way when editing `bot/risk.py`.

## Commands

- `pytest -q` — full suite (fast, no network).
- `ruff check sportsbot tests` — lint.
- `sportsbot fit|backtest|scan|run|status` — CLI (network needed).
- Live smoke (reads only, safe): `sportsbot scan`.

## Compliance notes (do not remove)

- Polymarket main-CLOB order placement is geoblocked for US IPs; never add
  code that evades geoblocking. US-legal live venues: Polymarket US, Kalshi.
- Sackmann tennis data is CC BY-NC-SA (non-commercial); MLB Stats API terms
  permit personal/non-commercial use.
- Table-tennis fast leagues carry documented match-fixing risk — their
  higher `min_edge_override` / lower stake caps are deliberate.
