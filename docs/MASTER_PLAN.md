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
- **Venue split (2026-10-02, owner decision).** Sports → Polymarket (`sportsbot`
  `exchange: polymarket`, `polymarket-edge/`). Kalshi → event markets (weather,
  economics, …): `kalshi-engine` skips the Sports category via
  `markets.exclude_categories`. Polymarket's main CLOB blocks order placement
  from US IPs; for a US-based live account the legal route is Polymarket US
  (separate regulated API, not yet implemented). Never circumvent the geoblock.

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
- [x] NBA / NFL / NHL under the sharp anchor (`data/teams.py`, both venue
      clients, Odds API keys in the harness, config + allocation arms): the slate
      no longer goes dark in November. Sharp signal only (no rating model); Kalshi
      start times unknown for these series (fail closed on entry there).
- [x] Capital allocation and learning loop (`sportsbot/bot/portfolio.py`,
      `config/allocation.yaml`, `docs/ALLOCATION.md`): strategy arms
      `sport/signal/style` with per-arm evidence and gate; money from manual
      weights → evidence shares (CLV interval lower bound) → a bounded learning
      budget with a weekly loss stop; equity-mode bankroll (profits roll in);
      fractional risk limits; the sharp line as a selectable model
      (`engine/sharpline.py`); dashboard arms/results panels. Tighten-only
      throughout; the file cannot turn trading on.
- [x] Result 14 (`backtest/wallet_follow.py`): copying wallets is dead as a
      taker; informed MLB flow exists (+1.0 pt) but a copy nets −0.7 pt.
      Strict maker fill model and backtest policy parity landed with it.
- [x] Result 18 (`wallet-follow --maker`, `backtest/updown_maker.py`): the
      informed MLB wallets trade earlier, larger and off-tape (sharp line /
      news); delay does not matter, the fee does; a resting-bid follow earns
      ≈ 0 under strict fills and ≈ +0.3 pt/signal only at the front of the
      queue. Maker paired positions on BTC 5-minute windows lose in all 60
      configurations (the unpaired leg is by construction the loser).
- [x] Result 19 (`backtest/maker_flow.py`, rank 3 of the research report):
      the maker side of Polymarket MLB pre-game flow earns +0.15 to +0.33 pt
      to the close (CI excludes zero in both halves) plus a ~0.24 pt rebate;
      low trailing VPIN pays most, fills ≥ $100 lose in the test window, and
      settlement P&L is too noisy (±3.5 pt over 205 games) to decide anything.
- [x] Result 20 (`polymarket-edge/src/cb_study.py`): the Fed rule does not
      generalise to every central bank. Across 15 banks it fired 87 times on
      103 meetings, lost 3 (Colombia twice, Israel once, all at 0.90–0.94)
      and returned −0.7%. The G10 subset (Fed, ECB, BoE, BoJ, BoC, RBA,
      RBNZ) fired 57 times with 0 losses at +2.3%, bounding the loss rate
      at 5.3% (was 16%). That split is post hoc and needs ~130 forward fires
      to beat the 2.4% breakeven. Next step, if the operator approves:
      extend the recorder to the six other G10 banks, record only.
- [x] Sharp-line CLV harness (`sportsbot/signals/sharp.py`, rank 1 of
      `reports/Beating prediction market prices.md`): budgeted Pinnacle snapshots
      via The Odds API, Shin de-vig, every decision and bet graded against the
      pre-start sharp close net of fees, event-clustered bootstrap CI, and the
      pre-registered PASS/FAIL criterion (≥1,000 graded decisions, CI excluding
      zero). Tighten-only enforcement through the adaptive layer. Paper mode;
      collection starts once `ODDS_API_KEY` is set.
- [x] Tape-verified paper maker fills (`exchanges/paper.py`, 2026-10-10): a
      resting paper order now fills only when the venue's PUBLIC taker tape
      prints through its limit (Polymarket data-api, Kalshi `/markets/trades`,
      both read without credentials), for the size that printed, paying the
      maker fee; the executor books those fills as bets on the `.../maker` arm.
      Before this, paper resting orders never filled, so the paper book could
      not produce maker evidence and the rank 2 experiment had no path to a
      gate. Still an upper bound on real fills (front of queue is implied), but
      the adverse selection a maker eats is real: the fill happens exactly when
      price moves through the order. Same crossing rule as the strict backtest
      (`core.books.print_crosses`).
- [x] Result 15 (`wallet-follow --maker-ticks`, 2026-10-12): resting one
      tick under MLB taker prints earns +0.6 pt CLV on fills (+1.1–1.8 pt
      behind the best-CLV wallets), interval above zero — the first measured
      positive CLV in the project — but every fill is a queue-priority fill
      at the existing bid and one tick deeper the edge vanishes; tennis shows
      none of it (2,000 games, intervals span zero). Rank 2's live
      minimum-size maker order on MLB is now the measurement that matters.
- [x] Touch-maker arm (`sportsbot/bot/touch.py`, `touch:` config block,
      `<sport>/touch/maker` arm, 2026-10-12): the executable form of Result 15
      — rest at the best bid/ask on MLB at $1 a side, one side per cycle,
      never crossing, through the normal risk check, executor, CLV grading
      and allocation gate. On in the $100 paper sim, off in the default
      config; the live minimum-size fill-share test is pre-registered in
      `docs/MAKER_TEST.md` and waits on the operator.
- [x] Maker test evidence trail and read-out (2026-10-13): every order
      carries its arm, the book at posting, placement and first-fill times,
      and -- once it leaves the book -- `tape_through`, the paper venue's fill
      rule applied to the public tape over the window it rested in (bounded
      by TTL and game start). `sportsbot maker-report` turns that into the
      pre-registered numbers: fill share vs the tape rule (orders and
      contracts), CLV per fill vs venue and sharp close with clustered
      intervals, adverse selection by wait, the 200-fill gate line, and a
      paper self-check (venue fill == shadow).
- [x] Result 15 addendum (2026-10-13): the same construction on Polymarket
      hockey (89 games), football (80) and basketball (40). NHL and NFL carry
      the touch edge — population maker baseline +0.0042 to +0.0063 (hockey,
      27 test markets) and +0.0053 to +0.0065 (football, 24), every interval
      above zero, the MLB number on a tenth of the markets; neither has
      persistent informed wallets. NBA is not measurable yet (11 pre-season
      markets, intervals span zero, one tape page lost to a 429): re-measure
      with ≥60 regular-season days. The sim's touch arm now rests on hockey
      and football too (`touch.sports`); the runner quotes those books for
      the touch arm even when the scanner dropped them for want of a sharp
      line (it needs no price), and the allocator no longer parks a touch
      arm behind the priceable-sport gate. Live-mode funding is unchanged:
      manual weight, evidence or `learn`, none of which is set.
- [x] Operator path for real money (2026-10-13): `SPORTSBOT_DISCORD_WEBHOOK`
      alerts (fills per cycle, risk halts once per six hours, nothing when
      unset; a failed post never stops a cycle), `sportsbot digest [--post]`
      (equity/CLV per book, touch posts and fills, distance to the bar),
      `sportsbot board-serve` + `sportsbot-boardserve.service` (board.html
      and dashboard.html only, localhost) for `tailscale serve --set-path
      /sportsbot` on the arbitrage desk's node — one private address for
      phone and desktop, no new auth surface. `deploy/DEPLOYMENT.md` has the
      commands; the webhook can be the desk's.
- [x] Polymarket discovery: listing-date floor removed (2026-10-13). Gamma's
      event `startDate` is the listing time; the 21-day floor hid every game
      the venue listed early — this week's NHL slate (46 open moneylines for
      Oct 13–18, listed Sep 16–21) and NFL week 7 (listed Sep 4–7). The sim's
      "142 hockey dropped" on Oct 12 were the far-dated games only. Found by
      the touch arm's read-only dry run (0 NHL games inside its 12 h window
      at 07:00Z; 193 hockey markets and today's 23:00Z games after the fix).
      Staleness stays per market on `gameStartTime`; volume ordering keeps
      live games on the first page (offsets 0–4 measured).
- [x] First Linode deploy of sportsbot (2026-10-13, operator-run): three
      setup defects found and fixed the same hour. (1) `setup_server.sh` ran
      the model fit from `/root`, which the service user cannot read
      (PR #65). (2) The MLB Stats API answered 406 Not Acceptable to the
      `sportsbot/1.0` agent from the Linode address while the same request
      returned 200 elsewhere; the client now sends browser-like headers,
      overridable with `SPORTSBOT_HTTP_USER_AGENT`. (3) Jeff Sackmann's
      tennis repositories were removed from GitHub in September 2026 (every
      year 404 on master and main), so `fit tennis` fell back to the Kalshi
      bootstrap (1,529 matches, no surfaces); the downloader now falls
      through to the `Aneeshers/tennis-sackmann-archive` mirror (CC BY-NC-SA
      4.0, snapshot June 2026). Tennis is not in the sim; MLB is, and until
      the fit succeeds on the box the sim drops MLB as "model unfit" and the
      touch arm quotes NHL/NFL only. Follow-up the same hour: the 406 held on
      every variant (browser headers, IPv4, HTTP/1.1) while `/teams` answered
      200, so it is a per-address refusal of `/schedule`; `fit baseball` now
      installs the committed `config/seeds/baseball.json` (refreshed by the
      daily check-in with `--seed-out`) when the API errors, and the sim's
      MLB context call was already guarded.
- [x] `config/maker_test.yaml` + runbook (2026-10-13): the pre-registered live
      fill-share test as one complete config (Kalshi, touch arm only at $1 a
      side, 120 s TTL, tennis/NBA off, risk scaled to $100: $10 day stop,
      $25 kill) shipped in paper mode. `docs/MAKER_TEST.md` "Starting it"
      lists the operator's five steps — Kalshi keys, funded balance, the
      three `learn: true` lines and a `learning.budget_usd` in
      `config/allocation.yaml`, `SPORTSBOT_LIVE=1` in a unit override — none
      of which the repository performs. `sportsbot doctor -c
      config/maker_test.yaml` is the preflight.
- [x] Polymarket survey and "touch all game books" (2026-10-13). Top 2,000
      events by 24 h volume: the venue's largest game books are esports (LoL
      Worlds $6.8M on 5 matches, Dota 2 $533k, CS2 $371k) and soccer (UCL
      $1.1M on 18 matches with one-tick books and $16k median at the touch;
      EPL, La Liga, EFL, AFC CL), then NFL $347k, NHL $198k, cricket, CFB
      $80k, UFC $80k, NBA $38k (preseason), MLB $21k (LCS); tennis $1.5M on
      127 events; table tennis $45 across 769 events (96 of the last 100
      resolved with zero volume). Added `Sport` members SOCCER / ESPORTS /
      MMA / COLLEGE_FOOTBALL / CRICKET, multi-league tag keys in the
      Polymarket client (three-way Yes/No books shaped per outcome), and
      `signal: touch`: no model is loaded, the scanner drops the sport as
      "no model", and the touch-only pass quotes it inside its window (the
      window is checked before any quote is fetched). The paper sim now rests
      $1 at the touch of every game book the venue quotes with size — the
      measured edge is still MLB/NHL/NFL only; everything else is fill-share
      measurement. First esports measurement (LoL, 150 matches, 30 days):
      maker baseline CLV +0.0065 [+0.0018, +0.0130] at 120 s but realised
      return −0.090 [−0.171, −0.001] per dollar — the pre-game close is a
      poor value proxy in fast-resolving series; not a candidate. Addendum 2
      in EDGE_VERDICT has all eight: college football carries the edge with
      both yardsticks agreeing (+0.0053 to +0.0081 CLV, realised +0.30
      [+0.02, +0.55] at 120 s) and joins MLB/NHL/NFL as a measured book;
      EPL +1.0–1.4 pt CLV on 21 outcome markets (returns ±0.45: sample);
      Dota 2 small-positive on both; CS2 repeats the LoL divergence (CLV
      positive, realised −0.11 [−0.22, −0.00]); UFC nothing on 14 markets;
      cricket nothing on 41 (both yardsticks span zero; 0.001 ticks);
      Champions League (no matchday in the 30-day window; run on 60 d, 26
      test outcome markets) +0.0065–0.0119 CLV, returns near zero ±0.3–0.5
      — soccer is a point of CLV on 47 outcome markets, return not yet
      readable. NCAAF needs a Kalshi start
      time (`americanfootball_ncaaf` harness key) before the live test can
      carry it.
- [x] Polymarket US venue client (2026-10-13, `exchanges/polymarket_us.py`,
      `exchange: polymarket_us`). Measured first, keyless: its fee schedule
      charges takers 0.0695 x C x p x (1 - p) and makers NOTHING (a 0.0125
      rebate until 2026-10-19 13:00 ET, volume tiers after, zero below $10M
      a month), every game carries `gameStartTime`, and its own books are
      deep on team sports (NHL touch median 58,770 contracts, MLB 819,395,
      CFB 167,521) and thin on tennis (80). Discovery by league series, one
      moneyline per event (props, totals and spreads skipped), team names
      canonical, MLB home from the side flagged home; books, settlement,
      Ed25519-signed orders (YES-terms prices, `participateDontInitiate` from
      the new `Order.post_only`, which the executor sets for maker arms),
      cancel by slug, open orders, positions, balance; GETs retry on 429/5xx,
      orders never. Paper mode on it cannot fill (no keyless trade tape); the
      live test's tape shadow there needs a websocket collector, buildable
      once keys exist. `docs/MAKER_TEST.md` now prefers this venue. Soccer is
      not mapped (UCL events carry no moneyline type there).
- [x] 2026-10-20: US sportsbook close vs Polymarket (EDGE_VERDICT Result 26,
      `sportsbot espn-lines`). Pre-registered, keyless (DraftKings / ESPN BET
      closes from ESPN's core API), 6,130 NFL/NBA/NHL/MLB games.
      * Not a member, but the nearest miss so far: the Brier is a hair better
        pooled (t -2.5), with look-ahead in the book's favour.
      * The T-15 taker toward the book nets +4.2 +- 2.5 per $1 (t 1.7),
        under its bar.
      * Forward record APPROVED by the user the same day
        (`signals/book_lines.py`, `sportsbot-booklines.service`). Their bar:
        100 T-15 trades on Polymarket US at the ask net of fees, mean > 0,
        t >= 1.65 -> pre-live gate.
- [x] Polymarket US trade tape (2026-10-19, `exchanges/polymarket_us_tape.py`,
      `sportsbot us-tape`, `sportsbot-ustape.service`). This is the live maker
      test's fill-share denominator, built ahead of the keys from the
      documented markets websocket (TRADE subscription, REST signing on the
      handshake, 100 slugs per subscription, heartbeats).
      * Coverage: the recorder logs every connection and subscription, so
        the client's `recent_trades` serves only windows it covered. Any
        other window raises `TapeGap`, and the executor's shadow and the
        paper fill rule then record nothing rather than zero.
      * Checked: the live endpoint refused a dummy key with 401 "API key
        not found", so the handshake format reaches the key lookup.
      * Doctor: it FAILs a live `polymarket_us` config with no live tape.
      * Deploy: `update.sh` installs the unit, which exits cleanly until
        the keys are in `.env`.
- [x] Venue queue study (2026-10-14, `substrate_bridge/queue_study.py`,
      `sportsbot queue-log` / `queue-report`, EDGE_VERDICT Result 15
      addendum 3). Polymarket US, main and Kalshi were sampled on the same
      games every 30 s for 10.5 h, using the US public `/bbo` trade counter
      as a coarse tape. All 16 NHL games:
      * The touch is 80-130k contracts deep on every venue; US turns its
        queue over fastest (0.21/h vs 0.17 main, 0.11 Kalshi).
      * Front-of-queue CLV per fill: US +0.45 pt (no fee), Kalshi +0.66
        gross / +0.41 net, main +0.33.
      * Strict sweeps are rare everywhere before the start.
      Polymarket US stays the live-test venue; Kalshi is a close second.
- [x] 2026-10-14 sweep (EDGE_VERDICT Result 21). Tools:
      `sportsbot wallet-review` (one wallet: settlement, mark-out and mirror),
      `wallet-scan` (the leaderboard in 10 categories: no copyable skill),
      `fresh-wallets` (new wallets buying long shots in size lose −9.7 pt per
      share, t −3.6), `data/ratings/move_study.py` (big pre-game moves,
      followed or faded, lose the costs), and kalshi-engine
      `crypto_hourly_study` / `crypto_hourly_recorder` (Kalshi's hourly BTC
      ladder beats a spot model at every horizon). One lead goes forward:
      fading fresh-wallet long-shot buys, pre-registered as
      `sportsbot fresh-watch` (`sportsbot-freshwatch.service`, which
      update.sh installs). The bar is >= 200 fades across >= 60 markets
      with t >= 2. The same day the fade FAILED its retrospective holdout
      (t 1.5; five markets carry 96%). The server record continues only
      as free evidence.
- [x] 2026-10-15: Kalshi hourly S&P 500 / Nasdaq-100 ladders vs the index
      (EDGE_VERDICT Result 22, kalshi-engine `index_hourly_study`).
      Pre-registered; 5,597 settled events, reaching back through Kalshi's
      `/historical` archive. Thirty minutes out, the book beats the index
      model (Brier +0.0042, t ≈ 8), and the taker loses 3.6 pt. This is
      the seventh reference-price non-member. Kalshi's other hourly
      financial ladders (EUR/USD, GBP/USD, USD/JPY, Treasury yields, Dow,
      Nikkei, KOSPI) are the same construction against an even deeper
      reference. Measure them only with a new registration, and expect
      the same answer.
- [ ] Game-vs-series consistency monitor (MLB postseason): checked 2026-10-09
      on Gamma — the only live series-winner market (`mlb-playoffs-who-will-
      win-series-guardians-vs-white-sox`, 0.56/0.44) traded $101 in 24h against
      $44k on the World Series champion market; the per-team advance markets
      are already resolved. Too thin to matter this postseason; revisit next
      October before building.
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
- [x] **Polymarket as a leading reference for Kalshi: measured, no**
      (`docs/VENUE_LEAD_LAG_2026-09-23.md`, `python -m src.venue_study`). 148 soccer
      matches on both venues since Kalshi's game series launched in August: quotes 0.5c
      apart at the median inside a 1c Kalshi spread, Brier identical at every horizon,
      three rows in 444 with a 2c gap (all lost), and the lead-lag regression says
      Polymarket's price converges on Kalshi's from a day out, not the reverse (part of
      that is last-trade staleness on Polymarket, which biases one way). The
      reference-price class is one confirmed member (fed-funds futures) and three measured
      non-members; measure the next candidate the same way before writing engine code.
- [x] **CPI nowcast as a leading reference: measured, no**
      (`docs/CPI_NOWCAST_VS_POLYMARKET_2026-09-29.md`, `python -m src.cpi_study`). The
      Cleveland Fed's chart files hold every daily nowcast vintage since 2013 with actuals;
      sigma fitted 2013-2024, bucket probabilities with no free parameter, 279 settled US
      buckets across 37 events. Polymarket beats the nowcast by 0.018 Brier at 24h and
      0.029 at 1h (CIs exclude zero), the regression weight is all on the price, taking the
      nowcast's side loses. Fourth measured non-member of the reference class; the Fed
      reference is a market pricing the same event, which is the property to look for next.
- [x] **Deribit options as a leading reference for BTC/ETH digitals: measured, no**
      (`docs/CRYPTO_DERIBIT_VS_POLYMARKET_2026-09-29.md`, `python -m src.crypto_study`).
      17,425 settled Polymarket BTC/ETH fixed-time digitals (1,795 events, Mar 2024 - Sep
      2026) priced by a zero-drift lognormal on Deribit spot and DVOL at 1h/6h/24h. Brier
      identical at every horizon on 17k digitals, regression weight split in half, and on
      prints under 10 minutes old the market is slightly better with the DVOL-side trade at
      -0.8% / +3.7% / -17% per $1; >= 0.90 digitals return -0.5% to -1.3%. Fifth measured
      non-member, and the first that is itself a deep market: the Fed property is a
      reference pricing the identical event against a thin crowd, not depth alone. The
      first pass showed +122%/$1 from a one-hour candle-timestamp look-ahead, now a
      CLAUDE.md landmine with a test.
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
- [x] Extend the recorder to the other G10 banks (`arb-scanner/src/cb_watch.py`,
      2026-10-13, operator go-ahead after Result 20). ECB, BoE, BoJ, BoC, RBA and RBNZ on
      Kalshi (`KXCBDECISION*`) and Polymarket (Global-Rates / ecb tags), Fed gates
      unchanged, its own `cb_signals` table and `docs/cb_signals.csv` export. Single-venue
      buckets (no Kalshi market) are recorded with a 1.5-day lead floor and scored
      apart. A failed Kalshi read drops the bank for the cycle (regression test). Live
      2026-10-13: all six paired on both venues, nothing in the 7-day window (BoJ Oct
      hold 0.94/0.95 and ECB Oct hold 0.93/0.92 at ~16 days).
      **First fire 2026-10-22 (two_venue):** ECB Oct 29 hold, Kalshi ask 0.970 / Polymarket
      ask 0.966 at 7.0 days, +2.9% / +3.5% if it pays. BoC and RBNZ Oct are inside the
      window but contested (hike-25 at 0.82/0.83 and 0.77/0.80), so they do not fire.
      2026-10-23: BoJ Oct 30 hold fired (two_venue), Kalshi ask 0.960 / Polymarket ask 0.970
      at 6.5 days, +3.9% / +3.1% if it pays.
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
      2026-09-28: hike-25 0.69/0.69 at 30 days, still below the bar. The watcher now runs
      daily at 13:00 UTC from a session routine and exports its signal table to
      `arb-scanner/docs/fed_signals.csv` after every run, so the record survives the
      ephemeral sandbox; a fire or settlement is committed the same day.
      **First fire 2026-10-22:** Oct 28 FOMC hold, Kalshi ask 0.91 (Polymarket mid 0.915)
      and Polymarket ask 0.92 (Kalshi mid 0.905) at 6.2 days, +9.3% / +8.7% if it pays.
      The entry sits in the 0.90–0.94 band where all three of Result 20's losses were
      entered, and this is a contested cycle (hold was 0.62 on 10-14).
      2026-10-03: Polymarket rows were costed fee-free, but Gamma's feeSchedule puts
      the Fed buckets at rate 0.05 (0.25c per $1 at 0.95, about a tenth of the measured +2.1%). Both
      the recorder and the cross-venue detector now charge each market's own Gamma
      rate (0.05 when unreported), and suspect matches are judged on the gross gap so
      a fee cannot relabel a wrong-question pair as an arb. No rows had been recorded,
      so nothing needed restating.
- [ ] Decide, on the first settled fires, whether the Kalshi leg goes to kalshi-engine's
      executor in dry-run. Blocked on the record above; the generic ensemble dilutes a
      2-3c edge below `min_edge` and `max_price: 0.95` excludes the buckets, so it needs
      its own path, not a generator.

### Possible strategy build-out (candidate queue, 2026-10-10)

Ordered by what the evidence so far supports. Nothing here is funded; each
item names the measurement that would move it, and the standing rules apply
(paper first, judge by CLV, tighten-only capital, never martingale).

1. **Rank 2: maker-only quoting, start on MLB** (report rank 2). Result
   15 gives it the first positive measured number (+0.6 pt CLV per fill at
   the touch, +1.1–1.8 behind informed flow) with one unmeasurable: the
   fill share of a new order in the touch queue. The decisive experiment
   is a minimum-size live maker order that records fill rate and CLV per
   fill; it is the smallest capital question in the queue. The 2026-10-13
   addenda extend the measured edge to NHL, NFL and college football (same
   half-point, realised returns agreeing), so the test is not bounded by
   the MLB season; soccer is the promising unknown (a point of CLV on 21
   outcome markets, sample growing in paper daily); esports are excluded on
   realised returns; NBA is a December re-measurement. Kalshi measured
   2026-10-13 (EDGE_VERDICT Result 24, 396 MLB games): +0.80 to +0.93c per
   fill net of its maker fee at the front of the queue, zero on strict
   through-fills, so the live test can run there with a working
   fill-share denominator today. Also
   measurable in paper: tape-verified fills produce `baseball/model/maker`
   and `baseball/sharp/maker` evidence that the per-arm gate reads (≥200
   CLV-graded fills, interval lower bound > 0). Gate on the sharp close once
   `ODDS_API_KEY` is set; until then the venue close is the yardstick. The
   recurring venue perks belong here, not in a promo list: Polymarket's 15%
   maker rebate on sports (ignored in the fill model, so measured CLV is
   conservative) and Kalshi's liquidity program (OLIP: daily per-market
   payments for two-sided resting size), whose terms are to be read from the
   venue at the time, never assumed. Prior from Result 19: the maker side of
   the incumbent MLB pre-game flow earns +0.15 to +0.33 pt to the close plus
   ~0.24 pt rebate, on small fills only (fills ≥ $100 lose in the test
   window), so cap resting size per level well under $100; a VPIN quote-pull
   rule costs fills without paying (even the high tercile is positive).
2. **Informed-flow direction as a second signal** (Results 14–15, 18): the
   CLV-selected MLB taker wallets carry +1.0 pt; copying them as a taker
   nets −0.7 pt; resting one tick under them collects +1.1 to +1.8 pt CLV
   on the fills it gets (Result 15, front of queue implied), about double
   the population maker number. Under strict fills (a print must trade
   through the bid) the same follow at their price fills 4.9% of the time
   and earns ≈ +0.001 per signal (Result 18). The two bracket the answer;
   the open quantity is the fill share of a new order at the touch, which
   the tape cannot give and the live minimum-size maker order in item 1
   can. Data-logged only until then.
3. **Perk and promotion harvest** (assessed 2026-10-09; one-time, finite,
   operator-executed). Terms move, so re-verify every figure on the venue's
   own page before acting; the numbers below are what was read then.
   - Sportsbook sign-up bonus bets hedged on Kalshi: the only class with
     positive expected cash. A bonus bet's stake is not returned, so the
     hedge converts roughly 65–75% of face value depending on the price band
     and Kalshi's fee; the hedge leg goes through the normal risk layer like
     any other order. US-legal books and Kalshi only.
   - Exchange deposit credits (Kalshi ~$10, Polymarket US ~$20): not
     withdrawable, must be traded through. Treat as free learning budget for
     the maker arm, nothing more.
   - Crypto deposit matches (Kraken-style 2–3% on a large deposit, locked
     12–18 months): capital trapped below the bot's hurdle; declined.
   - Hard limits: one offer per verified identity, no multi-accounting, no
     referral self-dealing, no automation of KYC'd sign-ups. Bouncing between
     venues to re-earn bonuses is not a loop; it is a short list that empties.
   - Build only if the operator wants it: a `docs/PERKS_LEDGER.md` with each
     offer's terms, cap, lock date and realised conversion, so the harvest is
     accounted like any other arm.
4. **Score-feed latency measurement** before any in-play code. **Closed
   for MLB, 2026-10-13 (EDGE_VERDICT Result 23):** on 873 scoring plays in
   200 games the venue's price covers half the move a median 6 s after the
   pitch and 8 s before the official feed stamps the play (97% of home runs
   are priced before the stamp). A taker reading the feed instantly nets
   −0.16c [−0.53, +0.20] per $1, and it is negative by 5–30 s. In-play is a
   maker game at best; write no in-play taker code. **Closed for the NFL
   too, 2026-10-19 (Result 25):** half the move prints 10 s after the
   snap, and a taker at snap + 20 s nets −0.32c, worse later. NBA is
   unmeasured (`sportsbot score-latency` / `nfl-latency` are the
   templates).
5. **Game-vs-series consistency monitor** (MLB postseason): deferred to next
   October, see the roadmap item above ($101/24h series volume this year).
6. **Weather arm**: stays excluded (beats climatology, loses to the market,
   15 complete dates). Re-open only if a decision-time forecast source with a
   measured lead-time edge over NWS appears. Measurement fix first: the v1
   cohort scores the *earliest* forecast on record before first sighting
   (mean age 1.5 d); scoring the *latest* pre-sighting forecast cut the arm's
   Brier from 0.150 to 0.125 on the 168 rows checked 2026-10-11 without
   changing the ordering (SIGNALS, 2026-10-11). A v2 cohort file with that
   rule, run alongside v1, needs the operator's go-ahead; v1 rows are never
   rewritten.


### 2026-10-08 → 10-12: pre-registered research round + live-pilot plumbing (sportsbot)

Decision log. The research ledger (`docs/research_ledger.json`, rendered on the
board) now holds 13 pre-registered tests, all FAIL: no price-only or
consensus-anchored strategy on Kalshi, Polymarket or Polymarket US survives
out-of-sample with real fees and fills. The remaining evidence is live fills.

- [x] Kalshi and Polymarket tennis vs the sportsbook closing line: both venues
      price like the books (Brier within 0.001, ~1¢ apart); found and fixed the
      late in-play cut in `prematch_cut` (`backoff_s`), erratum on SETTLE_VPIN
- [x] Polymarket US calibration survey (15,220 resolved markets): within ~1¢ of
      the settle rate in every volume bucket; no taker cell confirms
- [x] Polymarket US client (`exchanges/polymarket_us.py`): public gateway reads
      verified live, Ed25519-signed orders built from the API reference, post-only
      makers on both venues (`BetIntent.maker` → `Order.post_only`); five
      1-contract checks still required before the first real order
- [x] Live-pilot plumbing: per-book isolation (store/risk/runner/CLI),
      venue-confirmed fill accounting and restart recovery, fill markouts at
      +5s/+60s/+5m/+30m, board cards (fill quality, research ledger),
      `config/pilot.yaml` and `config/pilot_pmus.yaml` as twins, doctor live-book
      checks. (Reconciled with main 2026-10-13: main's arm-level portfolio
      allocator in `bot/portfolio.py` + `config/allocation.yaml` is the one that
      sizes; this branch's per-sport sleeve wiring and its learning log were
      retired so only one allocator drives money.)
- [x] Pre-match quote recorder (`signals/prematch.py`): keyless, both venues, the
      forward dataset for every later test; pre-registered T−30 longshot forward
      test (PM US tennis, NO on mid ≤ 0.15, n ≥ 150)
- [x] One-command server update (`deploy/update_sportsbot.sh`): units, recorders,
      pilot preflights, v3 log copy
- [ ] Server: run `update_sportsbot.sh`; keys in `.env`; Kalshi pilot on demo for a
      week, then prod; Polymarket US 1-contract checks, then its pilot
- [ ] Read the v3 paper logs (CLV of de-vigged sharp fair value vs the Polymarket mid)
- [ ] Forward test verdict at n = 150 (≈ 5 weeks of recording)
- [x] Reddit team-news recorder (`signals/reddit.py`, official OAuth API, data
      only) + pre-registered news-veto test on the maker test's fills; read out
      with the maker test (`sportsbot news-veto-report`). Operator: create a
      Reddit "script" app, keys in `.env`, `systemctl enable --now sportsbot-reddit`
- [x] `doctor`: Kalshi key must be RSA; live configs prove a signed read
