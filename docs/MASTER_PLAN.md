# Master Plan

_Last updated: 2026-09-22 (rev 5 — engine suite rejoined, signals layer landed)_

> **Rev 5 (2026-09-22):** three strands merged back into `main`. The engine
> suite (`polymarket-edge/`, `kalshi-engine/`, `arb-scanner/`) returned as an
> independent deployment alongside `sportsbot/` — the two never import each
> other, and the root `CLAUDE.md` now carries both rulebooks. `sportsbot
> doctor` gates go-live, exit-rule replay validates the exit logic offline,
> and the external-signals layer (`sportsbot/signals/`) shipped with one
> measured result and one honest negative: the NWS weather arm correlates
> (+0.862 Pearson at decision lead) and is being scored against real
> settlements via `sportsbot weather-score`, while the Bluesky chatter arm is
> shelved — its eligible pool tops out at 21 events, below the n ≥ 30 bar its
> own report enforces. See `docs/SIGNALS_2026-09-17.md`.

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
- [x] Import old chat history into `docs/chat-imports/` (five drops, per-drop
      manifests with secret scans) and mine it for parameters/ideas already settled
- [x] External signals layer (`sportsbot/signals/`), DATA ONLY until backtest/CLV
      evidence: NWS point forecasts as a decision-time weather baseline with a
      lead-aware sigma and a no-backfill rule; Bluesky chatter collection with an
      n ≥ 30 gate before any correlation is claimed
- [x] Weather-arm triple-null on real settlements (`sportsbot weather-score`):
      coin 0.2500 → climatology 0.1828 → market 0.0751 across 168 settled rows
- [x] `sportsbot doctor` go-live preflight; exit-rule replay backtest
- [ ] Score the first cohort carrying decision-time NWS baselines (84 markets,
      targets Sep 21–22) — does the forecast arm beat climatology? beat the market?
- [ ] Deploy `main` to the Linode box and let the paper stack accrue toward the
      go-live gate (≥200 settled bets, positive mean CLV, Brier < 0.25, fees verified)
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
- [ ] Open question the divergence table raised, now measured: across 34 city-days our
      sigma runs a median **1.85x** the sigma the bucket prices imply (range 0.47-5.57),
      and the market's figure is itself a floor because the open-ended end buckets pull
      the tails in. So the model fades narrow centre buckets systematically — a bet on
      variance, not on temperature, which is what every NO signal in the last cycles
      has been. `weather_divergence` now prints both sigmas and the ratio. Deciding it
      needs settled outcomes, not priors: score the recorded `mkt=` means
      (`python -m src.report`), and cross-check against `sportsbot weather-score`'s
      168-row sample, before either widening the market's view or narrowing ours.
      Kalshi's bands say the same in F: ratio median 1.48 over 25 station-days.
- [x] `python -m src.weather_divergence` in kalshi-engine too, so stations get vetted
      the same way cities are. First run (25 station-days, all lead 0): median forecast
      error -0.72F, but New Orleans -5.7, Miami -4.4, the SFO low -4.4, Austin -3.1.
      Miami reads -4.4F against Polymarket's buckets on the same day — two independent
      books agreeing puts the error in our grid cell, not in either market. Still not a
      settled outcome, so `bias_f` stays unfitted until `weather_calibrate` has truth.
- [x] **Sportsbook reference vs Polymarket, measured** (`docs/SPORTSBOOK_VS_POLYMARKET_2026-09-23.md`,
      `python -m src.sportsbook_study`). 1,048 settled matches across four leagues, free
      football-data.co.uk lines vs Polymarket's trade log 1h/6h/24h before kickoff. An hour
      out Polymarket is as sharp as every closing line (paired Brier within ±0.0006, beats
      Pinnacle on its 185-match subset), the regression weight is all on Polymarket, the
      median gap is 0.8c against a 4c min_edge, and buying the book's side of a 2-3c gap
      loses. The sportsbook-consensus thesis fails on liquid soccer; it needs a source that
      leads the market, and a retail-book consensus does not. The item below is therefore
      no longer the unlock it was written up as — the key would switch on a model with no
      measured edge. US sports remain unmeasured; the script is the template.
- [ ] **polymarket-edge's headline model has never run.** Its 2090 recorded estimates
      are 100% weather: five sports are configured but `ODDS_API_KEY` is unset, so the
      odds client returns nothing and sportsbook-consensus fair value produces zero
      estimates, while the cycle still reports signals as normal. Needs a (free, 500
      req/month) key from the-odds-api.com in `.env`. Until then the only independent
      signal either engine has is weather, and three lines now say the book prices that
      better than we do: settled Brier 0.0690 vs climatology 0.1813 (sportsbot, 186
      rows), our sigma running 1.48-1.85x the market-implied one, and our own report's
      0.0156 against the market's 0.0071 over 1576 estimates on proxy outcomes.
      `python -m src.main` now warns at startup and `python -m src.report` prints the
      arm mix, so a one-armed run is visible instead of inferred.
- [x] **Resolved-market study** (`polymarket-edge/src/calibration_study.py`,
      `docs/RESOLVED_MARKET_STUDY_2026-09-23.md`): 1,327 liquid resolved markets, 7,278
      sampled prices from the data API's trade log, horizons measured from the *scheduled*
      end (measuring from `closedTime` manufactures fake longshot edge — a "by <date>"
      market that resolves YES closes when the event happens). Verdict: no price-only
      taker edge. Favorites lose 0.5-2% per $1 at every threshold and horizon before
      spread; underdogs are worse. One category is different: FOMC decision buckets,
      50/50 paid at >=0.90 within a week, +2.1% (24h) to +3.4% (168h) net of 1c, t=7.3,
      because fed-funds futures are a sharper reference than the book. Geopolitics and
      news favorites are the opposite (-10% to -37%). The Fed trade is short-vol: one
      surprise erases ~40 wins; breakeven surprise rate ~2.4%. Kalshi `KXFEDDECISION`
      carries the same buckets. Oct 2026 is a coin flip today — nothing to buy until the
      final week.
- [x] Cross-check: sportsbot's market-aware MLB backtest (PR #21, 910 games) reached the
      same verdict independently — the model carries no information the Kalshi price
      lacks. Two studies, two venues, two methods: the price is the sharper source unless
      an external reference beats it. That leaves reference-price edges (Fed decisions,
      sportsbook consensus) as the only class with evidence behind it.
- [x] Operationalise the Fed rule as a recorder (`arb-scanner/src/fed_watch.py`, runs in
      every arb-scanner cycle and as `python -m src.fed_watch --report`). Reads Kalshi's
      KXFEDDECISION series and Polymarket's fed-rates tag, pairs the five buckets per
      meeting across venues, and fires when a bucket's ask is >= 0.90 within 7 days of
      the decision (Kalshi close_time — no calendar to maintain) and the other venue's mid
      is >= 0.85. First fire per (meeting, bucket, venue) is stored as the entry with a
      fixed $25 stake; rows settle from Kalshi results and the report prints paid/settled,
      mean net return, breakeven surprise rate and a rule-of-three bound. Not wired to
      any executor: the settled record it builds is what pre-live-gate requires first.
      Live 2026-09-23: Oct hike-25 0.51/0.54, hold 0.47/0.46 at 35 days — not firing.
- [ ] Decide, on the first settled fires, whether the Kalshi leg goes to kalshi-engine's
      executor in dry-run. Blocked on the record above; the generic ensemble dilutes a
      2-3c edge below `min_edge` and `max_price: 0.95` excludes the buckets, so it needs
      its own path, not a generator.

