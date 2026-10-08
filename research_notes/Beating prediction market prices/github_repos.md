# Public repositories for sports prediction / prediction-market trading (tennis, MLB, table tennis, Polymarket/Kalshi): catalogue and credibility assessment

Research date: 2026-10-07. Star counts, last-push dates and licenses were pulled from the GitHub search API on that date unless otherwise noted; "last commit" below means the repository `pushed_at` date. README claims were read via fetch; where a README and the API disagreed (e.g. license) both values are given.

Credibility scale used throughout:
- **A** = market-aware evaluation (vs bookmaker/exchange prices) with fees modelled AND realized or forward-tested results reported with sample sizes / confidence intervals.
- **B** = market-aware backtest (ROI/CLV vs odds) but fees not modelled, or results look in-sample / tuned on the test metric.
- **C** = accuracy-only evaluation (AUC/accuracy/Brier vs outcomes), out-of-sample, no market comparison.
- **D** = no evaluation reported (infrastructure, scaffolding, or claims without numbers).
- **F** = sales funnel / SEO spam / keyword-stuffed clone; do not run with keys.

## Key question 1: Tennis repos (Kovalchik-style Elo, point-level Markov/O'Malley, Sackmann pipelines, in-play) and which report CLV/ROI vs Pinnacle/Betfair/Polymarket

### Takeaway
Only one widely-starred tennis repo reports ROI against bookmaker odds (edouardthom/ATPBetting, 467 stars, abandoned 2021), and its +58%/+70% ROI figures come from hyperparameters tuned on ROI with a flawed random-betting baseline, so they are not credible as evidence of beating the market. Every other tennis repo found is either accuracy-only (AUC 0.72–0.76, ~66–69% accuracy, which is roughly what a market-implied-probability baseline achieves) or has no evaluation; no public repo was found that reports CLV against Pinnacle/Betfair/Polymarket closing prices for tennis, and no open-source in-play tennis model repo surfaced at all.

### Cited Findings

**Market-aware (rating B)**
- edouardthom/ATPBetting ("A strategy for tennis matches betting"): 467 stars, 179 forks, Jupyter Notebook, last push 2021-09-23, no license file (API returns no license) — [GitHub API/search](https://github.com/edouardthom/ATPBetting)
- Data: tennis-data.co.uk ATP matches Jan 2000–Mar 2018, 44,708 matches, with Pinnacle (PSW/PSL) and Bet365 (B365W/B365L) pre-match odds; author added Elo ratings — [Notebook](https://raw.githubusercontent.com/edouardthom/ATPBetting/master/Beating%20the%20bookmakers%20on%20tennis%20matches.ipynb)
- Model: XGBoost; features = Pinnacle odds, each player's win% and matches played over last X days, recent injuries, Elo, one-hot of most frequent players; "confidence" = model probability / bookmaker implied probability; rolling scheme trains on previous 10,700 matches with 300 held out for validation and predicts 2,000 matches per model; 11,054 matches predicted — [Notebook](https://raw.githubusercontent.com/edouardthom/ATPBetting/master/Beating%20the%20bookmakers%20on%20tennis%20matches.ipynb)
- Reported results: betting all matches loses money; "if we bet on 5% of the matches, we get a ROI of 70%"; "betting on 10% of the matches leads to an average ROI of 58%" on ~1,100 matches 2013–2018; accuracy 68.7%; author states hyperparameters were tuned for ROI rather than accuracy — [Notebook](https://raw.githubusercontent.com/edouardthom/ATPBetting/master/Beating%20the%20bookmakers%20on%20tennis%20matches.ipynb)
- The notebook's "random betting" baseline sums the winner's odds over a random half of matches (not a random side selection) and has no fixed seed, so the baseline bars are unreliable; basic strategies: Pinnacle flat-betting favourites loses "~2%" — [Notebook](https://raw.githubusercontent.com/edouardthom/ATPBetting/master/Beating%20the%20bookmakers%20on%20tennis%20matches.ipynb)
- welo (R, CRAN, not GitHub-native; mirror at github.com/cran/welo): v0.1.5 built 2026-09-19, GPL-3, author Vincenzo Candila; `tennis_data(YEAR, Circuit)` downloads tennis-data.co.uk ATP/WTA files; `betting()` bets $1 when model-prob / implied-prob > r using "Best_odds", "Avg_odds" or "B365_odds", returns number of bets, ROI % and a bootstrap CI (R=2000, alpha=0.1); `random_betting()` is a random-side benchmark; `welofit()` reports Brier and log-loss; based on Angelini, Candila, De Angelis (2022) "Weighted Elo rating for tennis match predictions", EJOR 297(1):120–132; the docs show no example ROI values — [welo manual](https://vincenzocandila.r-universe.dev/welo/doc/manual.html)

**Accuracy-only (rating C)**
- buildoak/tennis-xgboost-autoresearch: 24 stars, MIT code, data in `data/extension/` CC BY-NC-SA 4.0, last push 2026-03-17, Python 3.11+; XGBoost with ~417 features (overall/surface/serve/return Elo, form windows, H2H, career stats, rank momentum); data = Sackmann tennis_atp/tennis_wta plus TML-Database and tennisexplorer.com for 2025–26; strict temporal split, test = 2026 matches (607 ATP); final ATP ROC-AUC 0.7611, expected accuracy ~68.7%; `elo_diff` top feature at 10%; an earlier 0.8523 AUC run was leakage and reverted; no ROI/CLV/odds comparison — [GitHub](https://github.com/buildoak/tennis-xgboost-autoresearch)
- BrandoPolistirolo/Tennis-Betting-ML: 52 stars, MIT, last push 2022-02-18; logistic regression (SGDClassifier, L2, alpha 0.01, 30-fold CV); features from FiveThirtyEight-style Elo incl. surface Elo, PCA serve/return ratings with EMA smoothing, H2H games balance, performance score, service mistakes, age difference; data = Kaggle "A large tennis dataset for ATP and ITF betting" (Evan Hallmark, scraped, author could not verify scraper), ~125,938 singles matches from 2000; 70/30 split; accuracy ~66%, ROC AUC 0.72; README discusses odds only as background, no ROI — [GitHub](https://github.com/BrandoPolistirolo/Tennis-Betting-ML)
- sven-lerner/DeepTennis (CS230 course project): 9 stars, Jupyter, updated 2024-10-19; LSTM on sequential point-by-point data from the four majors (Sackmann data); reports 79.5% accuracy across all points on 2014 test matches; no market comparison — [GitHub search](https://github.com/sven-lerner/DeepTennis); [search summary](https://github.com/nathanij/atpPredictor)
- mcekovic/tennis-crystal-ball (Ultimate Tennis Statistics): 293 stars, Java, Apache-2.0, last push 2022-02-22, 89 open issues; topics include elo-rating, forecast, prediction; not Python and no market-aware evaluation surfaced in metadata — [GitHub API/search](https://github.com/mcekovic/tennis-crystal-ball)

**No evaluation reported (rating D)**
- gmalbert/tennis-predictions: 9 stars, GPL-3.0 (README claims "permissively licensed", conflicting), last push 2026-10-07 (active), Python 3.11, 219 commits, no test suite; features Elo, serve stats, surface form, H2H counts, market probabilities; data = TennisMyLife (1968–present), tennis-data.co.uk odds 2020–2025, Matchstat RapidAPI live odds (500-call/month guard), optional The Odds API; odds matching 81.5% success; training reports accuracy/AUC/Brier/log-loss but README gives no values; UI flags "edge" when model prob > market implied prob; no ROI/CLV — [GitHub](https://github.com/gmalbert/tennis-predictions)
- livetennisapi/polymarket-tennis: 271 stars, 230 forks, 13 commits, MIT, last push 2026-09-28, Python 3.10+, httpx only; observe-only ("no order execution, no wallet or key handling"); discovers tennis markets on Polymarket Gamma API (keyless), matches to live matches via name/date heuristics (returns None when ambiguous), shows price next to live score/server/break point; Live Tennis API free tier 30 req/min and 100/day; vendor-authored by the Live Tennis API team; reports "400+ open singles moneyline markets" on 2026-08-18 but no performance numbers; "Tennis Bot Arena" is paper-only with a synthetic tape; tests are offline fixtures with ruff + pytest — [GitHub](https://github.com/livetennisapi/polymarket-tennis)
- urwishpatel2003/tennis-engine: 0 stars, created 2026-08-17; "surface-aware Elo blended with a point-level serve/return model through an exact Barnett-Clarke Markov chain" producing win prob, set scores, game handicap and totals; no evaluation visible in metadata (not fetched) — [GitHub search](https://github.com/urwishpatel2003/tennis-engine)
- 9pankajs-ui/Tennis-match-result-predictor: 0 stars, created 2026-06-14; Elo + point-level Markov chains + gradient boosting on 77,000+ ATP matches; no evaluation visible (not fetched) — [GitHub search](https://github.com/9pankajs-ui/Tennis-match-result-predictor)
- francoisguan-code/ATP-Tennis-Match-Prediction: 3 stars, Jupyter, 39,541 matches 2011–2024 from Sackmann; not fetched — [GitHub search](https://github.com/francoisguan-code/ATP-Tennis-Match-Prediction)
- nathanij/atpPredictor: 0 stars, Jupyter, 2022; Sackmann data; author reports "promising results" without verification — [GitHub](https://github.com/nathanij/atpPredictor)

**Vendor method guides (no repo, no results)**
- OddsPapi blog "Tennis ELO Model in Python: Beat the Closing Line" (June 2026): surface-weighted Elo (surface_weight 0.6, K=32), multiplicative de-vig of Pinnacle, CLV vs Pinnacle close; only quantitative content is one worked example (Musetti vs Rune, French Open 2026: Pinnacle de-vigged 64.8%, Kalshi 1.587 ~ +2.8% EV, +7.7% CLV on one bet); author concedes "One worked example proves nothing"; no GitHub repo linked; promotional for OddsPapi API — [OddsPapi](https://oddspapi.io/blog/?p=2938)
- Pinnacle shut public API access in July 2025; older repos pointing at Pinnacle endpoints return nothing unless commercial partner — [dev.to](https://dev.to/ryankr/pinnacle-killed-its-public-api-heres-how-to-get-pinnacle-odds-in-2026-with-code-39pe)

**Table tennis**
- Roni-quant/topspin-lab: 0 stars, MIT, last push 2026-05-20, Python 3.10+, tests/ dir; Elo (K=32, base 1500) feeding a 9-feature Random Forest (Elo diff ~59% importance, recent form, workload); data = 157,836 ITTF singles matches scraped from results.ittf.link back to 1988; frozen-model test on 2026 World Team Championships London (822 rubbers): 75.06% accuracy, AUC 0.8356, Brier 0.1666, log loss 0.5022 (pure Elo 73.97%); walk-forward 2024–2026 (~21k matches, monthly refit): 70.26% accuracy, AUC 0.7794; explicitly no odds/market comparison — [GitHub](https://github.com/Roni-quant/topspin-lab)
- bikerlfh/probetsapp: 0 stars, created 2026-02-02, Django REST + React "table tennis betting prediction platform with ML-powered analysis", no license, no evaluation visible — [GitHub search](https://github.com/bikerlfh/probetsapp)
- Bianchinoo/tabletennis: 0 stars, 2022, "Prediction of match results (table tennis with AI and ELO)", no evaluation visible — [GitHub search](https://github.com/Bianchinoo/tabletennis)
- GitHub searches for "table tennis prediction betting" and "table tennis elo prediction" returned 1 and 2 results respectively; nothing found for fast-league (TT Cup / Setka Cup / Liga Pro) models — [GitHub search](https://github.com/search?q=table+tennis+elo+prediction)

### Inferences
- The ATPBetting ROI claims (58–70%) are almost certainly an artefact of selecting the top-confidence decile after tuning hyperparameters on ROI across the same 11k-match window; nothing in the notebook models fees, slippage, limits, or line movement, and the odds are pre-match Pinnacle/Bet365 (not closing). Treat as B at best; the repo is a useful feature-engineering reference, not evidence.
- The 66–69% accuracy / 0.72–0.76 AUC band reported by the accuracy-only repos is consistent with what the bookmaker implied probability alone achieves on ATP main-tour matches, so none of them demonstrates information beyond the market.
- welo is the only toolkit with a proper ROI-with-bootstrap-CI harness and a random-side benchmark, but it compares to Bet365/best/average odds (soft books) rather than Pinnacle closing, and it is R, so it would need porting to fit sportsbot's Python core.
- livetennisapi/polymarket-tennis has an unusual fork-to-commit ratio (230 forks / 13 commits) and is vendor tooling; its Gamma discovery and name-matching heuristics (return None on ambiguity) mirror sportsbot's conservative entity matching and could be compared for edge cases, but it contributes no evidence of edge.
- Sportsbot's own tennis stack (surface-blended Elo + O'Malley/Markov) is already more complete than any public Python repo found; the gap in the ecosystem is a CLV-against-Pinnacle/Polymarket-close evaluation harness, which no public tennis repo supplies.

### Gaps
- Could not retrieve the final "ROI variability study" section of the ATPBetting notebook (only the first 300k of 517k characters were read); the 1%-threshold figures and the author's variance conclusions are unknown.
- No GitHub repo was found that reports tennis CLV against Pinnacle, Betfair or Polymarket closing prices (searches: "tennis betting odds ROI backtest" returned 0 results; "closing line value python betting" returned only football/MLB/NFL projects).
- No in-play tennis model repo surfaced ("tennis in-play live win probability model" returned 0 results); DeepTennis is point-sequence prediction from historical data, not a live model.
- The Kovalchik (2016) benchmark claim that bookmaker consensus beats Elo and Elo beats other history-based methods appeared only in a search summary without a primary URL; verify against the original paper before citing.
- The Sackmann repo README/LICENSE could not be fetched directly (404 via fetch tool); the CC BY-NC-SA 4.0 licence is attested only by a Hugging Face mirror (see Key question 4).

## Key question 2: MLB repos (Statcast/pybaseball, starting-pitcher projections, FiveThirtyEight Elo and successors, weather/park/umpire features) and market-aware evaluation

### Takeaway
MLB public repos are thin and new: the two with market-aware designs (mmoore07129/mlb-kalshi-bot with a net-of-fee Kalshi EV engine and CLV tracker; justinloo12/beat-the-books-public with CLV grading) report no realized results, and the mlb-kalshi-bot author's own CLV backtest found their XGBoost model anti-calibrated versus a Pinnacle-led blend. FiveThirtyEight never open-sourced its MLB Elo code (only CSV outputs and methodology articles exist), and no open-source ZiPS/Steamer/PECOTA-style projection system was found.

### Cited Findings

**Market-aware design, no results yet (rating B/D)**
- mmoore07129/mlb-kalshi-bot: 2 stars, 8 commits, created 2026-04-27, last push 2026-05-02, Python 3.12, license not stated, tests/ folder present; Kalshi KXMLBGAME moneylines; fair value = vig-free blend 0.55 Pinnacle / 0.30 LowVig / 0.15 BetOnline sourced through The Odds API; net-of-fee EV vs Kalshi order book; flat $20 YES orders above a dynamic threshold; XGBoost fallback only when Pinnacle has no line (24,686 regular-season games 2015–2025, 53 features, MLB Stats API) plus a circuit breaker for >50pp gaps; custom RSA-PSS Kalshi client using `cryptography`, settlement via `/portfolio/settlements` using Kalshi-reported fees; CLV tracker (`clv_snapshot.py`) every 5 min 11:00–24:00 ET recording sharp-book fair price, last write before first pitch = closing line, computes probability-space and ROI-space CLV; `snapshot_all_analyzed.py` captures Pinnacle close for every analysed game — [GitHub](https://github.com/mmoore07129/mlb-kalshi-bot)
- Reported numbers: model accuracy on held-out 2025 56.0% ± 0.5% overall, 65.9% in the >=66% confidence band (n=323); 2024–2025 CLV backtest found the model anti-calibrated on disagreement games, the sharp blend winning ~77% of head-to-heads at >=20pp gap; no live P&L, ROI or fee-inclusive results; described as data-collection phase with small bankroll — [GitHub](https://github.com/mmoore07129/mlb-kalshi-bot)
- justinloo12/beat-the-books-public: 0 stars, 405 commits, last push 2026-07-21, Python 3.11, no license, tests/ dir; Monte Carlo run expectancy from Statcast via pybaseball + MLB Stats API; independent modules for pitchers, bullpens, offense, lineups, weather (Tomorrow.io key), umpires, market movement, synthesis; no-vig pricing, Kelly sizing, FastAPI dashboard; picks graded on CLV defined as change in no-vig implied probability between pick odds and close, using the last DraftKings snapshot before first pitch as a "closing-line proxy, not the true close"; README gives no record, ROI, CLV or sample sizes — [GitHub](https://github.com/justinloo12/beat-the-books-public)

**Accuracy-only (rating C)**
- SJTreadway/mlb_py: 0 stars, 2 forks, MIT, last push 2026-08-17; XGBoost/LightGBM on Statcast (barrel rate, exit velocity, wind, platoon splits, pitcher matchup), daily pipeline with "betting edge calculation", reported AUC 0.620 — [GitHub search](https://github.com/SJTreadway/mlb_py)
- jacobpieczynski/MLB-Game-Prediction-V3.0: 2 stars, updated 2025-05-28, pybaseball + linear regression; not fetched — [GitHub search](https://github.com/jacobpieczynski/MLB-Game-Prediction-V3.0)

**No evaluation visible (rating D), not fetched**
- romanesquibel562/mlb-sports-betting-predictions: 18 stars, last push 2026-08-02, no license, "real-time Statcast data, advanced ML models" — [GitHub search](https://github.com/romanesquibel562/mlb-sports-betting-predictions)
- companygondu-cyber/MLB-SYSTEM-ig-montecarlopicks (1 star; HGB/RF/XGBoost ensemble, "dynamic ELO calibration"), KAL311/BeatTheBooks (1 star; pulls live book lines), Gavin-Dsouza/sports-prediction-platform (0 stars; MLflow), JasonDoug/Moneyball (0 stars; Statcast ingestion, pitch-mix matchup, bullpen fatigue), Youdontknowme469/Sports-bot (0 stars; Elo + market probs + pitcher stats, paper trading, pushed 2026-10-06) — [GitHub search](https://github.com/search?q=MLB+prediction+model+betting+language%3Apython)

**FiveThirtyEight Elo**
- fivethirtyeight/data `mlb-elo` folder contains only a README; CSVs (`mlb_elo.csv` back to 1871, `mlb_elo_latest.csv`) are hosted at projects.fivethirtyeight.com; 28 columns including `elo1_pre/elo2_pre/elo_prob1`, `rating1_pre/rating_prob1` (preseason-projection-informed ratings), `pitcher1/pitcher2`, `pitcher1_rgs/pitcher2_rgs`, `pitcher1_adj/pitcher2_adj`, scores; no model code; license not stated on the folder page — [GitHub](https://github.com/fivethirtyeight/data/tree/master/mlb-elo)
- Methodology: Elo with home-field, margin of victory, park and era effects, travel, rest and starting pitchers; 2016 version: home-field worth 24 Elo points, travel and rest adjustments up to ~5 points each; 2019 projections credit "statistical model by Jay Boice and Nate Silver" — [FiveThirtyEight 2016](https://fivethirtyeight.com/features/how-our-2016-mlb-predictions-work); [FiveThirtyEight methodology](https://fivethirtyeight.com/features/how-our-mlb-predictions-work); [2019 page](https://projects.fivethirtyeight.com/2019-mlb-predictions)
- Third-party mirror of the game-by-game Elo file exists on DataHub ("scraped from FiveThirtyEight") — [DataHub](https://datahub.io/fivethirtyeight/mlb-elo)
- GitHub search "fivethirtyeight mlb elo" returned a single 0-star CSV dump repo; search "mlb elo starting pitcher rating model" returned 0 repos — [GitHub search](https://github.com/Celtmeister/https-projects.fivethirtyeight.com-mlb-api-mlb_elo.csv)

**Data infrastructure**
- jldbc/pybaseball: 1,730 stars, 430 forks, MIT, last push 2026-01-04, 138 open issues; Statcast, Baseball Reference, FanGraphs — [GitHub API/search](https://github.com/jldbc/pybaseball)
- Good Sport AI (commercial, not open source) self-reports tennis +4.4% over 156 graded bets in 90 days and "beat the closing line 82% of the time" on MLB while describing MLB game lines as about break-even; the page is internally inconsistent — [mcpservers.org listing](https://mcpservers.org/hi/servers/good-sport-ai)

### Inferences
- The most informative MLB finding is negative: the one repo that measured its own XGBoost model against a Pinnacle-led blend via CLV concluded the model was anti-calibrated on the games where it disagreed with the market, which matches sportsbot's design choice of market-blending and CLV-gated thresholds.
- Both market-aware MLB repos are essentially "follow Pinnacle, trade the Kalshi/Polymarket discrepancy" systems rather than independent forecasters; they are useful as references for Kalshi fee accounting (`/portfolio/settlements`) and CLV snapshotting cadence, not as model evidence.
- Nothing public reproduces FiveThirtyEight's pitcher-adjusted ratings; sportsbot's Elo + starting-pitcher overlay would have to be validated against the historical `mlb_elo.csv` columns (`pitcher1_adj`, `rating_prob1`) rather than against any open code.

### Gaps
- No open-source ZiPS/Steamer/PECOTA-style projection implementation was found in any search; those systems remain proprietary.
- No MLB repo was found that includes umpire or park-factor features with a published market-aware evaluation; beat-the-books-public has umpire/weather modules but no results.
- License of the fivethirtyeight/data repository was not visible on the fetched page (the repo as a whole is generally distributed under CC BY 4.0, but this was not confirmed in this research).
- Whether The Odds API still carries Pinnacle after Pinnacle's July 2025 API shutdown was not confirmed; mlb-kalshi-bot relied on it as of May 2026.

## Key question 3: Open-source Polymarket/Kalshi bots (market makers, arb scanners, copy-trading, up/down crypto bots): realized performance and SDK currency

### Takeaway
Across ~30 bots examined, exactly two publish honest realized numbers and both are null or marginal: ryanfrigo/kalshi-ai-trading-bot's forward test (n=55) concludes "NO MEASURED EDGE" (58% win vs 60% implied), and crollila/polymarket-vegas-edge's 232 real Polymarket positions returned +6.9% per dollar with a 95% CI of [-3.5%, +17.8%] and 69% of profit from two trades. The high-star bots (CloddsBot 2.9k, poly-maker 1.5k, weather-bot 777, BTC-15min 589) report no fee-adjusted P&L; several are vendor- or token-funded funnels; and the SDK landscape moved in May 2026 (py-clob-client archived; Polymarket/py-sdk `polymarket-client` is the official replacement, while docs and poly-maker still reference `py-clob-client-v2`).

### Cited Findings

**Official SDKs / status**
- Polymarket/py-clob-client is archived (last push 2026-05-25), 1,226 stars, MIT; README says the client "is no longer functional and should not be used for new or existing integrations" and directs users to Polymarket/py-sdk — [GitHub API/search](https://github.com/Polymarket/py-clob-client); [search summary](https://github.com/Polymarket/py-sdk)
- Polymarket/py-sdk ("Unified Python SDK for Polymarket DeFi"): package `polymarket-client` (`pip install polymarket-client`), 137 stars, 37 forks, MIT, created 2026-05-04, last push 2026-10-07, 772 commits, 33 open issues; 0.x semver where minor releases may break; "All Perps APIs are currently experimental"; README does not mention py-clob-client/v2 — [GitHub](https://github.com/Polymarket/py-sdk)
- Polymarket docs are inconsistent: the Python getting-started page calls `polymarket-client` the official SDK (beta), while the CLOB clients page lists `pip install py-clob-client-v2` — [docs.polymarket.com python](https://docs.polymarket.com/getting-started/python); [docs.polymarket.com clients](https://docs.polymarket.com/developers/CLOB/clients)
- Polymarket/agents (official AI-agent framework): 3,792 stars, MIT, archived by owner 2026-05-11, last push 2024-11-05; requires OpenAI key and funded wallet; no performance claims; ToS prohibits US persons from trading including via agents — [GitHub](https://github.com/Polymarket/agents)
- Kalshi: official docs deprecate the old `kalshi-python` package in favour of `kalshi_python_sync` / `kalshi_python_async`, host `https://api.elections.kalshi.com/trade-api/v2`, API-key + RSA-PSS signing — [docs.kalshi.com python-sdk](https://docs.kalshi.com/python-sdk); [docs.kalshi.com sdks](https://docs.kalshi.com/sdks/overview)
- Kalshi/kalshi-starter-code-python: 102 stars, 74 forks, last push 2025-03-07, no license shown, README: "This is not an SDK"; 12 open issues — [GitHub](https://github.com/Kalshi/kalshi-starter-code-python)

**Bots with honest realized/forward numbers (rating A-minus: honest but null/marginal)**
- ryanfrigo/kalshi-ai-trading-bot: 612 stars, 197 forks, MIT, last push 2026-10-07, 204 commits, pytest runs without credentials (live tests skipped), CI badge; strategies: AI directional (one OpenRouter LLM call per decision, default `anthropic/claude-sonnet-4.5`, $10/day cap, quarter-Kelly), "Safe Compounder" (edge-based NO-side maker orders, no LLM), "Beast Mode" ("historically led to significant losses"); edge-measurement harness (Brier, log-loss, edge vs book) and an "Edge Policy" that blocks losing categories; forward-only verdict on author's live account n=55 published 2026-10-02: "NO MEASURED EDGE", 58% win rate vs 60% implied (-1.9 pts), Brier 0.2356, NO side +7.6 pts (n=37), YES side -22.7 pts (n=17); fee treatment not stated; no backtest (requires a price corpus not shipped); custom RSA-signed Kalshi client, SDK not named — [GitHub](https://github.com/ryanfrigo/kalshi-ai-trading-bot)
- crollila/polymarket-vegas-edge: 0 stars, MIT, last push 2026-08-13, 193 offline tests; de-vigged FanDuel moneylines (The Odds API, free tier 500 req/month, historical paywalled) vs live Polymarket prices for NFL and college basketball, buy when Polymarket >= ~4c cheaper, quarter-Kelly capped at 10%; post-mortem on 232 real positions, $12,615 deployed over 7 months: realized +$231.76, ROI per dollar +6.9% with 95% CI [-3.5%, +17.8%], 69% of profit from two positions (ex-those +2.4%), college basketball -2.6% over 126 positions; fees approximated as flat 0.5% buffer; no realized CLV reported; author: "the edge is unproven" — [GitHub](https://github.com/crollila/polymarket-vegas-edge)

**Market makers (rating D: no P&L)**
- warproxxx/poly-maker: 1,505 stars, 489 forks, MIT, last push 2026-07-09, 37 commits, 4 open issues; two-sided post-only quotes on Polymarket CLOB V2 political markets, ranked by reward/rebate income vs volatility and spread, inventory skew, toxicity estimate, regime machine that pulls quotes on news, heartbeat dead-man switch, daily-loss kill switch; wraps `py-clob-client-v2` (not py-clob-client, not polymarket-client); 83 tests, ruff and mypy strict; README: market making "can lose money", "a reference implementation and a research harness, not a guaranteed-profitable product" — [GitHub](https://github.com/warproxxx/poly-maker)
- Polymarket/poly-market-maker (official "Market maker keeper for the Polymarket CLOB"): 326 stars, 103 forks, created 2022-02-24, 18 open issues; not fetched — [GitHub search](https://github.com/Polymarket/poly-market-maker)
- tfrmma/prediction-market-maker (7 stars; Avellaneda-Stoikov for binaries on Polymarket CLOB V2 + Kalshi, Hyperliquid hedging, EIP-712/RSA-PSS signing, kill switches), 0xtitan6/polymarket-mm (5 stars, Go, Avellaneda-Stoikov), miladhist/polymarket-market-maker (16 stars), apostleoffinance/prediction-market-maker-bot (15 stars, Rust), OrderBookTrade/GhostGuard (13 stars, Rust; detects "ghost fills" against Polymarket makers by verifying fills on-chain) — [GitHub search](https://github.com/search?q=polymarket+market+maker)

**Arb scanners (rating D)**
- polystrategist/kalshi-arbitrage-bot: 55 stars, 0 forks, MIT, last push 2026-07-13, 12 commits, Python 3.8+, no tests; scans Kalshi for YES+NO != 100% and bid>ask; tiered maker/taker fee schedule applied and filters for positive net profit ("rates approximate"); custom REST client on `api.elections.kalshi.com/trade-api/v2`; no results — [GitHub](https://github.com/polystrategist/kalshi-arbitrage-bot)
- Synpath-ai/prediction-market-arbitrage-trading-bot (37 stars, MIT, vendor "Built with synpath.dev"), TopTrenDev/polymarket-kalshi-arbitrage-bot (48 stars, Rust, no license), P-x-J/polymarket-arbitrage-bot (38 stars, MIT, last push 2025-08-23), coleschaffer/Gabagool (44 stars, "delta-neutral volatility arbitrage") — [GitHub search](https://github.com/search?q=kalshi+trading+bot)

**Copy-trading / multi-strategy frameworks (rating D or F)**
- alsk1992/CloddsBot: 2,903 stars, 338 forks, MIT, TypeScript/Node 22+, last push 2026-10-02, 32 open issues; Claude-powered agent across Polymarket, Kalshi, Betfair, Smarkets and crypto venues; requires `ANTHROPIC_API_KEY`; README lists a Solana token contract address ("Clodds CA"), pay-per-use USDC services (token launch $1.00, swap $0.10), marketplace 5% platform fee, "10.7k clones in 14 days" badge; no realized P&L or backtest; client libraries/API versions not stated — [GitHub](https://github.com/alsk1992/CloddsBot)
- HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits: 471 stars, 127 forks, MIT, Rust, last push 2026-09-07, 122 commits; About: "Copy Trading is production-ready; nine more strategies in development"; dry-run default (`enable_trading: false`); Telegram is the main call to action, hosted paper-trading beta "closed for now", links to pnlpro.fit; "No fake testimonials, no cherry-picked P&L" but no P&L at all — [GitHub](https://github.com/HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits)
- Drakkar-Software/OctoBot-Prediction-Market: 116 stars, GPL-3.0, last push 2026-03-30, 16 commits; copy-trading features marked work-in-progress, arbitrage "still under development", planned Kalshi integration, paper mode; UTM-tagged links to octobot.cloud; no performance numbers — [GitHub](https://github.com/Drakkar-Software/OctoBot-Prediction-Market)
- rustyneuron01/Polymarket-Sports-Trading-Bot: 133 stars, 62 forks, 3 commits, last push 2026-03-31, no license; despite "NHL, NBA, Tennis etc." only NHL is implemented; ESPN data; `requirements.txt` pins `py-clob-clients>=0.1.0` (not the official package name); no results; `test_models.py` exists — [GitHub](https://github.com/rustyneuron01/Polymarket-Sports-Trading-Bot)
- chainstacklabs/polymarket-alpha-bot: 180 stars, Apache-2.0, last push 2026-08-05 (Chainstack vendor); not fetched — [GitHub search](https://github.com/chainstacklabs/polymarket-alpha-bot)
- machina-sports/sports-skills: 242 stars, MIT, last push 2026-10-05; "agent skills for live sports data and prediction markets. Football, F1, Kalshi, Polymarket. Zero API keys"; not fetched — [GitHub search](https://github.com/machina-sports/sports-skills)
- mbordash/DRADIS: 27 stars, Rust, 10 strategies, Ollama advisor, topic tag "quantum-computing" — [GitHub search](https://github.com/mbordash/DRADIS)

**Up/down crypto and weather bots (rating D)**
- suislanchez/polymarket-kalshi-weather-bot: 777 stars, 188 forks, 67 open issues, 33 commits, last push 2026-03-02; license: GitHub API reports none, README badge says MIT (conflict); BTC 5-min Up/Down on Polymarket (RSI, momentum, VWAP deviation, SMA crossover, market skew; trades when edge > 2%) and weather KXHIGH on Kalshi + Polymarket (31-member GFS ensemble via Open-Meteo, NWS observed temps for settlement, edge > 8%, NYC/Chicago/Miami/LA/Denver); fractional Kelly 15%, cap 5% bankroll and $75/$100 per trade; Brier tracking; paper mode with $10,000 virtual bankroll; About claims "Highest profits $1.8k" with no period, bankroll, trade count or fee treatment; README says results are simulated and it "places no real trades"; no tests; custom Kalshi client, no SDK named — [GitHub](https://github.com/suislanchez/polymarket-kalshi-weather-bot)
- aulekator/Polymarket-BTC-15-Minute-Trading-Bot: 589 stars, 174 forks, 4 commits, last push 2026-02-28, MIT per README (API: none); spike/sentiment/divergence signals with weighted voting on NautilusTrader 1.222.0; $1 max per trade; `feedback/learning_engine.py` is "Placeholder for ML feedback loop" despite README claiming self-optimisation; FAQ cites "~75% win rate in early runs" (simulation, no counts/fees); custom `polymarket_client.py` + `patch_gamma_markets.py`; personal Telegram/Discord/Twitter links; requires `POLYMARKET_PK` private key; `run_bot.py` referenced but absent — [GitHub](https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot)
- ThinkEnigmatic/polymarket-bot-arena (81 stars, BTC 5-min), vvaifacai888/polymarket-5min-crypto-trading-bot (58 stars, NautilusTrader), doge-8/btc5m-web (21 stars, TS, backtesting), nicolastinkl/hermes_weatherbot (58 stars, MIT) — [GitHub search](https://github.com/search?q=polymarket+bot+trading+language%3Apython)

**Vendor-owned bots (rating D, conflict of interest)**
- OctagonAI/kalshi-trading-bot-cli: 394 stars, 109 forks, MIT, TypeScript, last push 2026-10-01, 290 commits; LLM research -> probability -> edge vs order book, half-Kelly, 5-gate risk engine; depends on Octagon API credits (3 per report, 100/day default) plus an LLM key (OpenAI default), optional Tavily; backtest/P&L numbers in README (Brier 0.168 vs 0.192, flat-bet ROI +7.8%) are labelled sample output; telemetry on by default; no fee modelling mentioned — [GitHub](https://github.com/OctagonAI/kalshi-trading-bot-cli)

**SEO spam / clone patterns (rating F)**
- Poly-Dev05/polymarket-trading-bot (78 stars) description is the phrase "Polymarket Trading Bot" repeated; dexoryn-china/polymarket-arbitrage-bot (41 stars) is keyword-stuffed in Chinese/English — [GitHub search](https://github.com/search?q=polymarket+bot+trading+language%3Apython)
- lalouannabel0072/kalshi-trading-bot (54 stars) and else24/kalshi-market-bot (42 stars) carry identical descriptions; AsArchitects/Kalshi-trade-bot (81 stars) and llllllilllll/Kalshi-Trade-Bot (78 stars) carry identical descriptions and identical last-push dates (2026-03-27) — [GitHub search](https://github.com/search?q=kalshi+trading+bot)
- Anmoldureha/polymarket-trading-bot-strategies (63 stars, no license, "5 sophisticated strategies"), discountifu/polymarket-trading-bot (73 stars, MIT, empty description), ArtemPavlov1994/polymarket-prediction-bot (63 stars, MIT) — unverified; treat as D until fetched — [GitHub search](https://github.com/search?q=polymarket+bot+trading+language%3Apython)

### Inferences
- The star counts in this niche are not a quality signal: the two repos with real, confidence-intervalled numbers have 0 and 612 stars, while the 2.9k-star project is token-funded and the 589-star BTC bot has 4 commits and a placeholder learning engine. Commit count, test suite, and whether the README reports a sample size with a CI are far better filters.
- Only poly-maker (py-clob-client-v2) and tfrmma/prediction-market-maker (CLOB V2 + RSA-PSS) are explicitly on current signing schemes; most bots hand-roll Gamma/CLOB/Kalshi clients, so none can be adopted as a drop-in for sportsbot's `polymarket-client` + Kalshi v2 interface without rework.
- For sportsbot's purposes, the transferable pieces are (a) poly-maker's toxicity/regime quote-pulling logic and kill switches, (b) mlb-kalshi-bot's fee-from-settlements and 5-minute CLV snapshot cadence, (c) crollila's post-mortem methodology (ledger-convention pitfalls, concentration analysis, CI on ROI), and (d) ryanfrigo's "Edge Policy" that disables categories after settled-trade evidence. None supply a tennis/MLB/table-tennis edge.
- The crollila result (+6.9%, CI spanning zero, 2 trades = 69% of profit) and the ryanfrigo result (-1.9 pts vs implied) are the best available public priors for "sportsbook-devig vs prediction-market" strategies: positive-looking but statistically indistinguishable from zero after a few hundred positions.

### Gaps
- No open-source bot was found that publishes a fee-adjusted, closing-line-graded track record on tennis, MLB or table-tennis markets on Polymarket or Kalshi.
- Issue trackers of the high-star bots were not read (page fetches exposed only counts: weather-bot 67 open issues, py-clob-client 162, py-sdk 33); whether users report losses or breakage is unknown.
- Whether `py-clob-client-v2` has its own maintained repository could not be confirmed; it appears only in docs and in poly-maker's dependency.
- Realized-performance claims in Telegram/Discord communities linked from the funnel repos were not examined (out of scope and unverifiable).

## Key question 4: Reusable infrastructure (odds-history datasets, CLV toolkits, Kelly/calibration libraries, Betfair loaders)

### Takeaway
The reusable, maintained pieces are data loaders rather than evaluation toolkits: pybaseball (1.7k stars, MIT), OddsHarvester (256 stars, MIT, active to 2026-10-07, OddsPortal incl. tennis), georgedouzas/sports-betting (810 stars, MIT, active, but football-only backtester with yield/ROI), flumine/betfair_data for Betfair, and the Sackmann archive (CC BY-NC-SA). No Python CLV toolkit or Kelly/calibration library of note surfaced on GitHub; welo (R) is the only packaged tennis ROI-with-bootstrap harness, and Pinnacle closing odds now require a paid feed since Pinnacle closed its public API in July 2025.

### Cited Findings

**Backtesting / modelling frameworks**
- georgedouzas/sports-betting: 810 stars, 150 forks, MIT, created 2019-01-08, last push 2026-09-24, 577 commits, CI, docs, changelog; dataloaders (football-data.co.uk stats + odds) and "bettors" wrapping any scikit-learn estimator; backtest reports number of bets, yield % per bet, ROI % and final cash across time-series splits; optional execution component via bookmaker API or browser automation (README warns this likely breaches bookmaker ToS); page shows football only, no tennis/baseball; Python version not visible — [GitHub](https://github.com/georgedouzas/sports-betting)
- octosport/octopy: 77 stars, MIT, 51 commits; football analytics (Poisson, Shin implied-odds method, power method, Elo); Kelly/calibration/backtest not confirmed on page — [GitHub](https://github.com/octosport/octopy)
- mbhynes/skelo: 17 stars, "NOASSERTION" license, last push 2022-12-02; Elo with sklearn interface — [GitHub search](https://github.com/mbhynes/skelo)
- JacobiusMakes/betting-model-starter (0 stars, MIT, template, created 2026-08-25) and parlayapi-notebooks (0 stars): ParlayAPI vendor templates for devig, EV, CLV backtest, "runs keyless via the free sandbox" — [GitHub search](https://github.com/JacobiusMakes/betting-model-starter)
- GitHub search "kelly criterion betting python library" returned 0 repositories; "closing line value python betting" returned only the six small projects listed above and in Q3 — [GitHub search](https://github.com/search?q=closing+line+value+python+betting)

**Exchange / Betfair loaders**
- betcode-org/flumine ("Betting trading framework"): 249 stars, 67 forks, MIT, created 2016, last push 2026-10-01, 21 open issues; topics include betfair, betdaq, smarkets, matchbook, kalshi, polymarket, streaming — [GitHub API/search](https://github.com/betcode-org/flumine)
- tarb/betfair_data ("Fast Python Betfair historical data file parser", Rust core): 47 stars, MIT, last push 2022-05-05, 15 open issues; homepage links the Betfair data-scientists "JSON to CSV revisited" tutorial — [GitHub API/search](https://github.com/tarb/betfair_data)

**Odds history scrapers / datasets**
- jordantete/OddsHarvester: 256 stars, 66 forks, MIT, created 2024-04-10, last push 2026-10-07, 2 open issues; Playwright-based OddsPortal scraper covering upcoming and historical odds plus community predictions across 11 sports and 100+ leagues incl. tennis; JSON/CSV output; historic command built around football leagues so tennis historic coverage must be checked — [GitHub API/search](https://github.com/jordantete/OddsHarvester); [search summary](https://github.com/jordantete/OddsHarvester)
- gingeleski/odds-portal-scraper: 129 stars, 59 forks, created 2017, 11 open issues — [GitHub search](https://github.com/gingeleski/odds-portal-scraper)
- jckkrr/Unlayering_Oddsportal (12 stars): notes that OddsPortal's anti-scraping defeats unsophisticated scrapers — [GitHub search](https://github.com/jckkrr/Unlayering_Oddsportal)
- tennis-data.co.uk: used as the odds source by ATPBetting (Pinnacle + Bet365 columns, 2000–2018), gmalbert/tennis-predictions (2020–2025), welo's `tennis_data()` loader, and a Mendeley Grand Slam 2011–2023 dataset; no standalone Python loader repo was found — [Mendeley](https://data.mendeley.com/datasets/hjwtvnpd7t/1); [welo manual](https://vincenzocandila.r-universe.dev/welo/doc/manual.html)
- Kaggle "A large tennis dataset for ATP and ITF betting" (Evan Hallmark): >2,000,000 matches with betting data, last updated ~7 years ago; used by Tennis-Betting-ML, whose author could not verify the scraper — [GitHub](https://github.com/BrandoPolistirolo/Tennis-Betting-ML)
- Hugging Face mirror Aneeshers/tennis-sackmann-archive states the Sackmann data is CC BY-NC-SA 4.0: attribute Sackmann and link to the originals, non-commercial, share-alike — [Hugging Face](https://huggingface.co/datasets/Aneeshers/tennis-sackmann-archive)
- No Hugging Face model or dataset for tennis match prediction was found in search; an IEEE DataPort "Rhythms of Victory" Wimbledon 2023 dataset is for academic review only — [IEEE DataPort](https://ieee-dataport.org/documents/data-set-rhythms-victory-predicting-professional-tennis-matches-using-machine-learning)
- The Odds API free tier is 500 requests/month and historical odds are paywalled — [crollila/polymarket-vegas-edge](https://github.com/crollila/polymarket-vegas-edge)
- Pinnacle shut public API access in July 2025; third parties now scrape Pinnacle's site and The Odds API notes those prices may be delayed — [dev.to](https://dev.to/ryankr/pinnacle-killed-its-public-api-heres-how-to-get-pinnacle-odds-in-2026-with-code-39pe)
- OddsPapi (vendor) claims free-tier historical odds with a three-bookmaker limit per historical call and 350+ bookmakers incl. Pinnacle and SBOBET — [OddsPapi blog](https://oddspapi.io/blog/?p=2938); [OddsPapi](https://oddspapi.io/us)
- Odds-API.io publishes a "modelled closing line" design note observing that tennis totals are single-line markets, which makes closing-line comparisons harder — [docs.odds-api.io](https://docs.odds-api.io/superpowers/specs/2026-08-31-modelled-closing-line-design.md)
- DanielTomaro13/sportsdata-mcp: 25 stars, MIT, "841 tools across 64 providers: cross-book betting odds, official league stats"; not fetched — [GitHub search](https://github.com/DanielTomaro13/sportsdata-mcp)
- Kalshi/tools-and-analysis: 23 stars, Jupyter, official Kalshi analysis notebooks — [GitHub search](https://github.com/Kalshi/tools-and-analysis)

### Inferences
- For tennis odds history that can be run from Python 3.11 with public data, tennis-data.co.uk yearly files (pandas-readable, Pinnacle + Bet365 columns per ATPBetting/welo usage) remain the only free source with sharp-book pre-match prices; closing prices at scale require a paid feed (The Odds API historical, OddsPapi, or OddsPortal scraping with its anti-bot risk).
- Nothing public replaces sportsbot's in-house Kelly/calibration/CLV code; welo's bootstrap-CI ROI and random-side benchmark pattern is worth re-implementing in `sportsbot/backtest/` (it is ~100 lines of logic).
- OddsHarvester is the most actively maintained odds-history scraper, but scraping OddsPortal is ToS-sensitive and brittle; it is a research-only data path, not a production dependency.
- Sackmann's CC BY-NC-SA licence (already noted in sportsbot's CLAUDE.md) also binds any derived dataset such as buildoak's `data/extension/`, so public repos that extend Sackmann data inherit the non-commercial restriction.

### Gaps
- A Python library offering Kelly sizing plus probability calibration for betting was not found by GitHub search; this may be a search-term limitation rather than true absence.
- The Betfair historical-data loaders were not tested for tennis market coverage; Betfair historical tennis data pricing was not researched.
- Whether The Odds API still exposes Pinnacle (and under what delay) after July 2025 was not confirmed.
- Kaggle notebooks implementing ROI on the Hallmark tennis dataset were not located; the Kaggle starter notebooks visible in search were generic.

## Key question 5: Which repos are abandoned, which are affiliate/sales funnels, and which have an active maintainer and tests

### Takeaway
Active with tests and an engaged maintainer: ryanfrigo/kalshi-ai-trading-bot, warproxxx/poly-maker, georgedouzas/sports-betting, jordantete/OddsHarvester, Polymarket/py-sdk, betcode-org/flumine, jldbc/pybaseball, livetennisapi/polymarket-tennis (vendor) and crollila/polymarket-vegas-edge (solo, 193 tests). Abandoned: ATPBetting (2021), Tennis-Betting-ML (2022), tennis-crystal-ball (2022), betfair_data (2022), Polymarket/agents and py-clob-client (archived 2026-05). Funnel/spam signals: CloddsBot (token + paid services), HarrierOnChain (Telegram-driven, closed managed service), aulekator BTC bot (Telegram/Discord, 4 commits, placeholder code), OctagonAI and Synpath (vendor credits), OctoBot (UTM-tagged cloud upsell), plus the identical-description clone pairs on Kalshi/Polymarket keywords.

### Cited Findings

**Active, tested, maintained (as of 2026-10-07)**
- ryanfrigo/kalshi-ai-trading-bot: last push 2026-10-07, 204 commits, 0 open issues, pytest without credentials, CI badge, GitHub Sponsors link only — [GitHub](https://github.com/ryanfrigo/kalshi-ai-trading-bot)
- warproxxx/poly-maker: last push 2026-07-09, 83 tests, ruff + mypy strict, optional live WebSocket integration test, 4 open issues — [GitHub](https://github.com/warproxxx/poly-maker)
- georgedouzas/sports-betting: last push 2026-09-24, 577 commits, CI/docs badges, changelog, 8 open issues — [GitHub API/search](https://github.com/georgedouzas/sports-betting)
- jordantete/OddsHarvester: last push 2026-10-07, 2 open issues — [GitHub API/search](https://github.com/jordantete/OddsHarvester)
- Polymarket/py-sdk: last push 2026-10-07, 772 commits, integration tests, 33 open issues — [GitHub](https://github.com/Polymarket/py-sdk)
- betcode-org/flumine: last push 2026-10-01 — [GitHub API/search](https://github.com/betcode-org/flumine)
- jldbc/pybaseball: last push 2026-01-04, 138 open issues — [GitHub API/search](https://github.com/jldbc/pybaseball)
- livetennisapi/polymarket-tennis: last push 2026-09-28, offline fixture tests, ruff + pytest, vendor-maintained — [GitHub](https://github.com/livetennisapi/polymarket-tennis)
- gmalbert/tennis-predictions: last push 2026-10-07, 219 commits, but no test suite — [GitHub](https://github.com/gmalbert/tennis-predictions)
- crollila/polymarket-vegas-edge: 193 offline tests, but only 10 commits and last push 2026-08-13 — [GitHub](https://github.com/crollila/polymarket-vegas-edge)

**Abandoned or archived**
- edouardthom/ATPBetting: last push 2021-09-23, 11 open issues, no license — [GitHub API/search](https://github.com/edouardthom/ATPBetting)
- BrandoPolistirolo/Tennis-Betting-ML: last push 2022-02-18, 7 commits — [GitHub API/search](https://github.com/BrandoPolistirolo/Tennis-Betting-ML)
- mcekovic/tennis-crystal-ball: last push 2022-02-22, 89 open issues — [GitHub API/search](https://github.com/mcekovic/tennis-crystal-ball)
- tarb/betfair_data: last push 2022-05-05, 15 open issues — [GitHub API/search](https://github.com/tarb/betfair_data)
- Polymarket/agents: archived 2026-05-11, last push 2024-11-05 — [GitHub](https://github.com/Polymarket/agents)
- Polymarket/py-clob-client: archived, last push 2026-05-25, 162 open issues — [GitHub API/search](https://github.com/Polymarket/py-clob-client)
- Kalshi/kalshi-starter-code-python: last push 2025-03-07, 12 open issues, predates current SDK — [GitHub API/search](https://github.com/Kalshi/kalshi-starter-code-python)
- mmoore07129/mlb-kalshi-bot (last push 2026-05-02, 8 commits), Roni-quant/topspin-lab (2026-05-20, 9 commits), zakariae-boui/value-betting-scanner (2026-07-13, 3 commits), justinloo12/beat-the-books-public (2026-07-21): single-author projects with no activity for 3–5 months — [GitHub API/search](https://github.com/mmoore07129/mlb-kalshi-bot)

**Funnel / conflict-of-interest signals**
- alsk1992/CloddsBot: Solana token contract address in README, paid x402/USDC services, marketplace fee, "10.7k clones in 14 days" badge — [GitHub](https://github.com/alsk1992/CloddsBot)
- HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits: Telegram as main CTA, hosted beta closed, managed setups "to be discussed on Telegram", pnlpro.fit link, Sponsor button — [GitHub](https://github.com/HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits)
- aulekator/Polymarket-BTC-15-Minute-Trading-Bot: Telegram/Discord/Twitter links, 4 commits, placeholder learning engine, requires private key in `.env`, FAQ says "Yes" to profitability with no data — [GitHub](https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot)
- OctagonAI/kalshi-trading-bot-cli: vendor-owned, requires Octagon credits, telemetry on by default, sample-output P&L — [GitHub](https://github.com/OctagonAI/kalshi-trading-bot-cli)
- Drakkar-Software/OctoBot-Prediction-Market: UTM-tagged octobot.cloud links, Sponsor button, WIP features — [GitHub](https://github.com/Drakkar-Software/OctoBot-Prediction-Market)
- livetennisapi/polymarket-tennis and OddsPapi/ParlayAPI templates: vendor-authored to drive API sign-ups (disclosed) — [GitHub](https://github.com/livetennisapi/polymarket-tennis); [GitHub search](https://github.com/JacobiusMakes/betting-model-starter)
- Clone pairs with identical descriptions: AsArchitects/Kalshi-trade-bot and llllllilllll/Kalshi-Trade-Bot; lalouannabel0072/kalshi-trading-bot and else24/kalshi-market-bot; keyword-stuffed Poly-Dev05/polymarket-trading-bot and dexoryn-china/polymarket-arbitrage-bot — [GitHub search](https://github.com/search?q=kalshi+trading+bot)
- rustyneuron01/Polymarket-Sports-Trading-Bot: 133 stars on 3 commits, only NHL implemented despite "Tennis" in the description, non-standard dependency name — [GitHub](https://github.com/rustyneuron01/Polymarket-Sports-Trading-Bot)
- suislanchez/polymarket-kalshi-weather-bot: 67 open issues on 33 commits, unsupported "$1.8k" claim, no tests, license mismatch between API and README — [GitHub](https://github.com/suislanchez/polymarket-kalshi-weather-bot)

**Runnable from Python 3.11 with public data vs needs paid feeds**
- Public-data, Python 3.10/3.11-compatible: buildoak/tennis-xgboost-autoresearch (3.11+, Sackmann), Roni-quant/topspin-lab (3.10+, ITTF scrape), livetennisapi/polymarket-tennis (3.10+, Gamma keyless; Live Tennis API free tier 100 req/day), warproxxx/poly-maker (needs funded wallet to trade), polystrategist/kalshi-arbitrage-bot (3.8+, needs Kalshi key), georgedouzas/sports-betting (football-data.co.uk), welo (R, tennis-data.co.uk) — [GitHub](https://github.com/buildoak/tennis-xgboost-autoresearch); [GitHub](https://github.com/Roni-quant/topspin-lab); [GitHub](https://github.com/livetennisapi/polymarket-tennis)
- Needs paid or rate-limited feeds: mmoore07129/mlb-kalshi-bot (3.12; The Odds API for Pinnacle/LowVig/BetOnline), justinloo12/beat-the-books-public (3.11; Tomorrow.io, optional OddsJam), gmalbert/tennis-predictions (3.11; Matchstat RapidAPI 500 calls/month, The Odds API), crollila/polymarket-vegas-edge (The Odds API 500 req/month, historical paywalled), ryanfrigo/kalshi-ai-trading-bot (OpenRouter LLM spend, funded Kalshi account), OctagonAI CLI (Octagon credits + LLM key), CloddsBot (Anthropic key, Node 22) — [GitHub](https://github.com/mmoore07129/mlb-kalshi-bot); [GitHub](https://github.com/gmalbert/tennis-predictions); [GitHub](https://github.com/crollila/polymarket-vegas-edge)
- Legacy Python stacks likely needing dependency pinning: ATPBetting (2018-era pandas/xgboost notebook), Tennis-Betting-ML (2022 sklearn) — [GitHub API/search](https://github.com/edouardthom/ATPBetting)

### Inferences
- A practical triage rule from this survey: require (1) a test suite runnable without credentials, (2) a README that states sample size and a confidence interval or admits the absence of one, and (3) no Telegram/token/credit-purchase call to action. Only ryanfrigo, crollila, poly-maker and (for data) OddsHarvester/py-sdk/flumine/pybaseball/sports-betting pass all three.
- Fork/star inflation is visible where forks approach or exceed commits by an order of magnitude (polymarket-tennis 230 forks/13 commits; rustyneuron01 62 forks/3 commits; aulekator 174 forks/4 commits); these are marketing or scripted-fork patterns, not adoption.
- None of the surveyed repos should be adopted as a trading dependency for sportsbot; the defensible imports are ideas (CLV snapshot cadence, fee-from-settlement accounting, category kill-policy, bootstrap-CI ROI) and data loaders (pybaseball, tennis-data.co.uk files, Sackmann archive under its NC licence).

### Gaps
- Issue content (as opposed to counts) was not read for any repo, so reports of losses, key leaks or breakage in issue threads are not captured.
- Reddit r/algobetting threads linking to repos with results could not be located via search; the mirrors returned were 2020-era beginner posts with no CLV tables.
- Several mid-star repos (chainstacklabs/polymarket-alpha-bot, machina-sports/sports-skills, Polymarket/poly-market-maker, romanesquibel562/mlb-sports-betting-predictions, Anmoldureha, ArtemPavlov1994, discountifu) were catalogued from search metadata only and not fetched; their evaluation status is unknown.
