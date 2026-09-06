# Master Plan

_Last updated: 2026-09-06 (rev 3 — chat-handoff drops 1–4 integrated)_

> **Drops 2–4:** the Polymarket bot's full generation history is now in the repo. Current
> production is the **fair-value system** (`fv_bot.py`/`edge_model.py`, Aug 19 — still on the
> VPS only; retrieve with `scripts/pull_from_server.sh`). The Jul 17 optimal build and Aug 14
> dashboard paper trader are archived under `polymarket-bot/archive/` with the falsification
> record. See `polymarket-bot/PROJECT.md` for the whole lineage.

> **Drop 1 changed the picture.** The uploaded handoffs contain the real prior work:
> the **Substrate/Echo engine** (`substrate/` — the authoritative predictions engine, a
> shadow-mode certification instrument with frozen protocol v1.0) and **Polymarket Sports
> Bot v2** (`polymarket-bot/v2/` — maker-first scalp/fade/arb, built for the 2026 taker-fee
> regime). The engines scaffolded earlier the same day are repositioned: the v1 consensus
> scanner as a signal source, `kalshi-engine/` as the Kalshi market-access layer.
> Read `substrate/CLAUDE.md` for the invariants that bind all future work.

## Where we came from / where we are

- **Before:** development over Terminus (phone SSH client) directly into the Linode Ubuntu box.
  Slow, no version control discipline, work lived only on the server.
- **Now:** development on the laptop against this GitHub repo. The Linode box becomes a pure
  **runtime host**: it runs whatever is on `main`, deployed via `deploy/deploy.sh`, managed by
  systemd. Nothing is edited on the server anymore.

```
laptop (dev) ──git push──▶ GitHub (source of truth) ──deploy.sh──▶ Linode (runtime)
```

## The two engines

### 1. Polymarket sports betting bot (`polymarket-bot/`)

Scans Polymarket sports events, builds a fair-probability estimate for each outcome from
sportsbook consensus odds (de-vigged) plus model priors, compares against Polymarket's order book,
and takes positions when the edge clears a threshold. Sizing via fractional Kelly.

Pipeline per cycle:
1. **Discover** — Gamma API: active sports events/markets (NFL, NBA, MLB, NHL, soccer, …).
2. **Price** — CLOB API: current best bid/ask + midpoint for each token.
3. **Model** — fair probability from The Odds API sportsbook consensus (de-vigged, multi-book
   median), optionally blended with an Elo prior.
4. **Edge** — `edge = fair_prob − ask` (for buys). Filter by min edge, min liquidity, time to event.
5. **Size** — fractional Kelly capped by per-market and total bankroll limits.
6. **Execute** — dry-run logs intent; live mode places limit orders via `py-clob-client`.
7. **Record** — every scan, signal, and order into SQLite for later analysis.

### 2. Kalshi predictions engine / substrate (`kalshi-engine/`)

A general prediction **substrate**: a pluggable set of signal generators that each emit
`(market, probability, confidence)` forecasts, combined by a weighted ensemble, traded on Kalshi
where the ensemble disagrees with the market price by more than the threshold.

Pipeline per cycle:
1. **Scan** — Kalshi API v2: open markets filtered by series/category from config.
2. **Substrate** — every registered `SignalGenerator` produces forecasts for markets it understands
   (market-implied baseline, mean-reversion, time-decay favorite drift; add more under
   `src/substrate/generators/`).
3. **Ensemble** — confidence-weighted average → final probability per market.
4. **Edge + size** — same edge/Kelly logic as the Polymarket side.
5. **Execute** — dry-run by default; live mode places limit orders via the authed v2 API
   (RSA key-pair signing).
6. **Record** — SQLite.

## Shared design decisions

- **Python 3.10+**, minimal dependencies, no framework lock-in.
- **Dry-run by default.** Two explicit switches (config `live: true` + CLI `--live`) to trade real money.
- **SQLite** on-box for logs/positions (simple, backed up by copying one file). Move to Postgres only
  if we outgrow it.
- **Secrets in `.env`** (never committed). `.env.example` documents every variable.
- **systemd timers** on the server run each engine on its cycle; logs via `journalctl`.

## Roadmap

- [x] Repo scaffold, master project files, working v1 models (this commit)
- [ ] Fill `.env` keys, run both engines dry on the laptop, sanity-check signals
- [ ] Deploy to Linode, run dry for ≥1 week, review SQLite logs
- [ ] Calibration review: are fair probs beating market closes?
- [ ] Turn on live mode with small bankroll caps
- [ ] Add generators: injuries/news feed, weather (outdoor sports), line-movement momentum
- [ ] Backtest harness over recorded scans
- [ ] Import old chat history into `docs/chat-imports/` and mine it for parameters/ideas we already settled on
