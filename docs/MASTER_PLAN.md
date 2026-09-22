# Master Plan

_Last updated: 2026-09-15 (rev 4 — consolidation merged)_

> **Rev 4:** a parallel session consolidated the trading stack into one `sportsbot/`
> package (per-sport Elo/Markov models, Polymarket + Kalshi exchange clients, risk-managed
> paper-first bot, walk-forward backtests, CI) and it merged to `main` as PR #1. This
> branch adopted it and pruned the superseded per-project engines (v1 scanner,
> kalshi-engine, standalone substrate milestone tools — now `sportsbot/substrate_bridge/`
> and `substrate/arv_cli.py`). Everything below rev 4 is historical context; the
> authoritative architecture doc is now `docs/ARCHITECTURE.md` + the root `CLAUDE.md`.

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

### 1. Polymarket sports betting bot (`polymarket-edge/`)

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
- [x] Line-movement momentum generator
- [x] Weather generator: open-meteo daily-high forecasts vs Kalshi KXHIGH* strike
      bands (Normal error model, lead-time-scaled sigma, per-station forecast cache;
      keyless API). Daily settlement makes these the fastest calibration feedback
      loop in the engine.
- [x] Weather sigma calibrator (`python -m src.weather_calibrate` + daily systemd
      timer): fits sigma_base/sigma_per_day empirically — from open-meteo's
      previous-runs history where reachable, else from self-logged forecasts scored
      against Kalshi's own settled bands (the YES band's midpoint is the observed
      high).
- [ ] Injuries/news feed generator (needs a data source decision)
- [x] Calibration/report harness over recorded scans (`python -m src.report` in each engine)
- [x] Real settlement tracking: reports score against actual Kalshi results / Gamma resolutions,
      falling back to a price proxy for still-open markets
- [x] Kalshi market discovery via `/events` (curated feed; the raw `/markets` firehose is buried
      in auto-generated MVE shard markets) + prod market data in dry-run (`read_prod`)
- [x] CI: pytest for all three modules on every push
- [x] `arb-scanner/`: cross-platform Polymarket↔Kalshi complement arbitrage + bundle
      detection with fee model and suspect-match quarantine (detect-only)
- [x] Risk gate in both engines: kill-switch file + daily realized-loss circuit breaker
- [x] Repo Claude skills: `pre-live-gate`, `engine-health`
- [x] Momentum generator (steady line-movement drift; complement of mean-reversion)
- [x] Replay backtester (`python -m src.backtest` in kalshi-engine): re-runs the substrate
      over recorded price history and Brier-scores every generator against real outcomes —
      offline parameter tuning with no API calls
- [x] Migrated the live order path off `py-clob-client` (archived) to the official
      `polymarket-client` SDK — same SDK and usage pattern as sportsbot's exchange
      client; lazily imported, adapter unit-tested with a fake client.
- [x] Weather arm stands down where it has no informational edge, after a live dry-run
      staked 356 contracts against an already-settled San Antonio low:
      * lead time and the day's extremum window are now measured in station-local
        time (from open-meteo's `utc_offset_seconds`), so the arm abstains once the
        low is set overnight (10:00) or the high by late afternoon (17:00) — past
        those hours the book prices an observed value and we hold a forecast;
      * a forecast more than `max_divergence_sigma` (1.5) from the mean implied by
        the event's own bucket prices is treated as a mismatched input rather than
        an edge, which is what Miami (-4.4F against the book, two days running) and
        Singapore (-3.1C) look like;
      * `python -m src.weather_divergence` (polymarket-edge) shows the whole
        forecast-vs-market table, so a city can be vetted before it has settled
        history, and every estimate now records the market-implied mean for the
        report to score against.
- [ ] Open question the divergence table raised: our sigma (2.4F same-day, fit on n=14
      Kalshi settlements) is roughly twice the sigma the bucket prices imply, so the
      model systematically fades narrow centre buckets — a variance bet, not a mean
      bet. Needs settled-outcome evidence (`python -m src.report`) before it is
      treated as edge; the recorded `mkt=` means make that measurable.
- [ ] Import old chat history into `docs/chat-imports/` and mine it for parameters/ideas we already settled on
