# Open-source repos for prediction-market and sports-exchange trading (evidence-graded inventory, as of 2026-10-07)

Method note: Repo metadata (stars, `pushed_at` = last push, license, archived flag, owner type) came from the GitHub repository search API on 2026-10-07. Star counts change daily. `pushed_at` is the last push to any branch, which is close to but not the same as the last commit on the default branch. Commit-history and per-repo REST endpoints were not reachable from this session, so READMEs were read through web fetches. No repo code was downloaded or executed. Search-API URLs are cited as `https://github.com/<owner>/<repo>`.

Evidence grades for profitability, used throughout:
- **A**: independently verifiable live P&L, such as an on-chain wallet that has been checked or an audited statement. *No repo in this survey reached grade A.*
- **B**: live P&L claimed with a pointer that can be checked, such as a public on-chain profile, but not verified here.
- **C**: live P&L claimed with no pointer that can be checked.
- **D**: paper or backtest results only, or a figure whose basis is ambiguous.
- **E**: no performance claim.
- **X**: red flags (impersonation, star/fork farming, keyword-stuffed descriptions, documented malware campaign).

---

## Q1. Which Kalshi, Polymarket / Polymarket US, and cross-venue arbitrage repos exist? (stars, last push, license)

### Takeaway
The official SDK picture changed in 2026. Polymarket archived `py-clob-client` on 2026-05-25 and points users to the unified `Polymarket/py-sdk` (PyPI `polymarket-client`). `py-clob-client-v2` and the official `polymarket-us` SDK (Ed25519 auth) are also active. Kalshi's official SDKs are `kalshi_python_sync`, `kalshi_python_async` and `kalshi-typescript`; the older `kalshi-python` is deprecated. Community Kalshi and Polymarket bot repos are numerous and heavily starred, but almost none publish verifiable results. Several high-star repos show star-farm or impersonation red flags.

### Cited Findings

**Official Polymarket (github.com/Polymarket)**
- `Polymarket/py-clob-client`: 1,226★, **archived**, last push 2026-05-25, MIT. The README says it was "archived by the owner on May 25, 2026", is "no longer functional and should not be used for new or existing integrations", and says to "migrate to our new unified SDK: https://github.com/Polymarket/py-sdk" — [GitHub](https://github.com/Polymarket/py-clob-client)
- `Polymarket/py-sdk`: 137★, last push 2026-10-07, MIT. It is published on PyPI as `polymarket-client` and covers "public data, authenticated account, trading, builder attribution, and wallet workflows". It is on the 0.x line, where minor releases "may include breaking changes", and "All Perps APIs are currently experimental" — [GitHub](https://github.com/Polymarket/py-sdk)
- `Polymarket/py-clob-client-v2`: 167★, last push 2026-10-06, MIT, 76 open issues — [GitHub](https://github.com/Polymarket/py-clob-client-v2). `Polymarket/rs-clob-client` (682★) is archived, and `rs-clob-client-v2` (143★) is active — [GitHub](https://github.com/Polymarket/rs-clob-client-v2). `Polymarket/clob-client` (TypeScript, 514★) is archived, and `Polymarket/clob-client-v2` (75★) is active — [GitHub](https://github.com/Polymarket/clob-client-v2). `Polymarket/ts-sdk` (43★) is the unified TypeScript SDK — [GitHub](https://github.com/Polymarket/ts-sdk)
- `Polymarket/polymarket-us-python`: 32★, last push 2026-10-06, MIT. It is the "Official Polymarket US Python SDK", installed with `pip install polymarket-us`, and needs Python 3.10+. Auth uses Ed25519 keys (UUID key ID plus Base64 secret, generated at polymarket.us/developer). It covers orders (create, modify, cancel, cancel-all, preview, close position), portfolio, RFQ (beta), public market data including sports and order books, and async-only WebSockets for orders, positions, books and trades. A 2.0.0 migration section signals breaking changes — [GitHub](https://github.com/Polymarket/polymarket-us-python)
- `Polymarket/agents` (AI agent trading framework): 3,793★, **archived**, last push 2024-11-05, MIT — [GitHub](https://github.com/Polymarket/agents)
- `Polymarket/poly-market-maker`: 326★, last push **2024-07-05** (stale), MIT. It is a "market maker keeper" with two strategies, Bands and AMM. It syncs every 30s by default: each cycle fetches the midpoint, computes target orders, diffs them against open orders, and cancels/places to match. It cancels all orders on SIGTERM. Self-described as "experimental" — [GitHub](https://github.com/Polymarket/poly-market-maker)
- `Polymarket/polymarket-cli` (Rust): 2,875★, last push 2026-05-26, no license detected — [GitHub](https://github.com/Polymarket/polymarket-cli). Other active official repos include `real-time-data-client` (TypeScript WebSocket, 229★), `polymarket-subgraph` (219★) and `agent-skills` (191★) — [GitHub org search](https://github.com/Polymarket)

**Official Kalshi (github.com/Kalshi and docs)**
- The Kalshi GitHub org has only 3 public repos. `Kalshi/kalshi-starter-code-python` has 102★, last push **2025-03-07**, and no license. `Kalshi/tools-and-analysis` has 23★, last push 2023-06-15, MIT — [GitHub](https://github.com/Kalshi/kalshi-starter-code-python), [GitHub](https://github.com/Kalshi/tools-and-analysis)
- The official SDKs are `kalshi_python_sync` and `kalshi_python_async` (PyPI) and `kalshi-typescript` (npm). "The older `kalshi-python` package is deprecated." Releases track the REST OpenAPI spec, usually weekly, but "may lag the API". Kalshi recommends treating the OpenAPI/AsyncAPI specs as the source of truth, or generating your own client. RSA keys work with all SDK versions; Ed25519 keys need SDK ≥3.31.0. The docs link only to package registries, not to source repos — [Kalshi docs](https://docs.kalshi.com/sdks/overview)

**Community Kalshi SDKs and clients**
- `arshka/pykalshi` (unofficial Python client with WebSocket support): 131★, last push 2026-07-29, MIT — [GitHub](https://github.com/arshka/pykalshi)
- `Reddimus/kalshi-cpp` (C++23 client generated from Kalshi's OpenAPI/AsyncAPI specs, RSA-PSS and Ed25519): 197★ — [GitHub](https://github.com/Reddimus/kalshi-cpp)
- `TexasCoding/kalshi-python-sdk`: 9★, created 2026-04 — [GitHub](https://github.com/TexasCoding/kalshi-python-sdk)

**Unified multi-venue SDKs ("CCXT for prediction markets")**
- `pmxt-dev/pmxt` (TypeScript, "unified API for trading on Polymarket, Kalshi, and more"): 2,165★, last push 2026-09-30, MIT. It has **1,388 open issues**, which suggests maintenance strain — [GitHub](https://github.com/pmxt-dev/pmxt)
- `guzus/dr-manhattan` (CCXT-style, covers Polymarket, Kalshi, Limitless, Opinion and Predict.fun, includes market-making examples): 204★, last push 2026-07-18, **no license** — [GitHub](https://github.com/guzus/dr-manhattan)
- `Synpath-ai/synpath` (Python, covers Kalshi, Polymarket, Polymarket US and Opinion, with smart order routing and "tick-level Kalshi order book history"): 46★, created 2026-09-28. It is very new and is a commercial company's repo — [GitHub](https://github.com/Synpath-ai/synpath)
- `betcode-org/flumine` (a Betfair-origin "betting trading framework" since 2016; its topics now list kalshi and polymarket): 249★, last push 2026-10-01, MIT — [GitHub](https://github.com/betcode-org/flumine)

**Community Kalshi bots**
- `rodlaf/KalshiMarketMaker`: 411★, last push 2026-04-14, MIT. It is an Avellaneda-Stoikov market maker with one worker per market and is detailed under Q3. No performance claims (grade E) — [GitHub](https://github.com/rodlaf/KalshiMarketMaker)
- `ryanfrigo/kalshi-ai-trading-bot` (AI/LLM strategy toolkit): 612★, last push 2026-10-07, MIT — [GitHub](https://github.com/ryanfrigo/kalshi-ai-trading-bot)
- `OctagonAI/kalshi-trading-bot-cli` (TypeScript; an LLM "deep research" probability estimate compared against the live book, with Kelly sizing and a "5-gate risk engine"): 394★, last push 2026-10-01, MIT. It is a vendor (OctagonAI) repo — [GitHub](https://github.com/OctagonAI/kalshi-trading-bot-cli)
- `suislanchez/polymarket-kalshi-weather-bot`: 777★, last push 2026-03-02, **no license**. It uses the 31-member GFS ensemble (via Open-Meteo); the fraction of members past the threshold gives the model probability. It trades when edge exceeds 8% and sizes at 15% fractional Kelly, capped at 5% of bankroll and $100. Calibration is tracked with a Brier score. The description says "(Highest profits $1.8k)", but the disclaimer says it is a "simulation tool for educational purposes. It does not place real trades or use real money" (grade D, likely paper) — [GitHub](https://github.com/suislanchez/polymarket-kalshi-weather-bot)
- `AruneshDev/Automated-Trading-System-Kalshi-Weather-Model` (notebook): 144★ — [GitHub](https://github.com/AruneshDev/Automated-Trading-System-Kalshi-Weather-Model)

**Community Polymarket bots**
- `warproxxx/poly-maker`: 1,505★, last push 2026-07-09, MIT. It is a maker-only market maker for CLOB V2 and wraps `py-clob-client-v2`. The README states "Market making on Polymarket is competitive and can lose money" (grade E). Details are under Q3 — [GitHub](https://github.com/warproxxx/poly-maker)
- `kachence/polymm`: 111★, last push 2026-08-16, MIT. It is a retired sports market-making and arbitrage bot that claims ~$5k net (grade B). Details are under Q4 — [GitHub](https://github.com/kachence/polymm)
- `chrisgillam/polymarket_gambot`: 26★, last push 2025-04-20, MIT. It compares sharp-book (Pinnacle) prices with Polymarket and sizes with Kelly (grade E) — [GitHub](https://github.com/chrisgillam/polymarket_gambot)
- `rustyneuron01/Polymarket-Sports-Trading-Bot` (ML on historical data for NHL, NBA and tennis): 133★, last push 2026-03-31, no license — [GitHub](https://github.com/rustyneuron01/Polymarket-Sports-Trading-Bot)
- `livetennisapi/polymarket-tennis` (observe-only: Gamma tennis market discovery matched to live scores, server and break point): 271★ but **230 forks** (an unusual fork-to-star ratio), created 2026-08-18, MIT. It is the repo of a data vendor (livetennisapi) — [GitHub](https://github.com/livetennisapi/polymarket-tennis)
- `sterlingcrispin/nothing-ever-happens` ("buys 'No' on all non-sports markets. For entertainment only, mostly a meme"): 980★, CC0 — [GitHub](https://github.com/sterlingcrispin/nothing-ever-happens)
- `aulekator/Polymarket-BTC-15-Minute-Trading-Bot`: 589★ — [GitHub](https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot)

**All-in-one AI / agent platforms (marketing-heavy)**
- `alsk1992/CloddsBot` ("1000+ markets" across Polymarket, Kalshi, Binance, Hyperliquid and Solana DEXs, plus an "agent commerce protocol"): 2,903★, last push 2026-10-02, MIT — [GitHub](https://github.com/alsk1992/CloddsBot)
- `braedonsaunders/homerun` (25+ strategies, backtesting, paper and live): 197★, last push 2026-08-21, AGPL-3.0 — [GitHub](https://github.com/braedonsaunders/homerun)
- `HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits` (Rust; copy trading "production-ready"; the description repeats "Polymarket trading bot" keywords): 471★, last push 2026-09-07, MIT — [GitHub](https://github.com/HarrierOnChain/Prediction-Markets-Trading-Bot-Toolkits)
- `YichengYang-Ethan/oracle3-prediction-market-agent` ("fee-adjusted no-arbitrage violations across related event contracts", MCP server, 600+ tests): 350★, last push 2026-10-07, Apache-2.0 — [GitHub](https://github.com/YichengYang-Ethan/oracle3-prediction-market-agent)

**Cross-venue arbitrage (Polymarket ↔ Kalshi)**
- `taetaehoho/poly-kalshi-arb` (Rust): 446★, last push 2025-12-21, **no license**. The rule is "Best YES ask (platform A) + Best NO ask (platform B) < $1.00". Kalshi's fee is modelled as `ceil(0.07 × contracts × price × (1-price))`, and Polymarket's fee is set to zero. Team-code mappings (EPL, NBA, ...) drive matching. Legs execute concurrently, and a circuit breaker enforces position, daily-loss and error caps. `DRY_RUN=1` is the default. It uses a Polygon/USDC wallet, so it targets the international CLOB; the README does not discuss geography. No P&L (grade E) — [GitHub](https://github.com/taetaehoho/poly-kalshi-arb)
- `ImMike/polymarket-arbitrage` (Python, "watches 10,000+ markets"): 288★, last push 2025-12-09, no license — [GitHub](https://github.com/ImMike/polymarket-arbitrage)
- `CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot` (BTC 1-hour markets, described as "risk-free"): 251★, last push 2026-05-09, MIT — [GitHub](https://github.com/CarlosIbCu/polymarket-kalshi-btc-arbitrage-bot)
- `realfishsam/prediction-market-arbitrage-bot` (built on pmxt): 184★ — [GitHub](https://github.com/realfishsam/prediction-market-arbitrage-bot)
- `meloner3/poly-kalshi-sports-bot` (Rust, Chinese README, "cross-platform sports arbitrage"): 143★ with 58 forks — [GitHub](https://github.com/meloner3/poly-kalshi-sports-bot)
- `uselayer/layer-spread-bot` and `uselayer-sdk` (covers Kalshi and **Polymarket US**, paper by default, fee math per venue): 1★ each, created 2026-10 — [GitHub](https://github.com/uselayer/uselayer-sdk)
- `MatchWire-Win/matchwire-sdk`: a mapping-only feed that gives one row per game across Kalshi, Polymarket US, Polymarket International and Predict.fun — [GitHub](https://github.com/MatchWire-Win/matchwire-sdk)

### Inferences
- For a Python engine that targets US-legal venues, the official, maintained trading clients are `polymarket-us` (Polymarket US) and `kalshi_python_sync`/`_async`, or a client generated from Kalshi's OpenAPI spec. For the international CLOB (geoblocked for US IPs), the official clients are `polymarket-client` (py-sdk) and `py-clob-client-v2`. The repo's CLAUDE.md requirement to use `polymarket-client` and never `py-clob-client` matches the archive notice.
- Most community "arb" repos model only the Polymarket international CLOB. Few model Polymarket US fees or its order semantics. `uselayer` and `synpath` claim Polymarket US support but are days or weeks old, so they cannot be considered credible yet.
- `pushed_at` dates show that many high-star repos were created in 2025-26. Star counts in this niche track hype more than quality; see Q4 on farming.

### Gaps
- Exact last-commit dates on default branches could not be fetched (commit endpoints were blocked). `pushed_at` is used as a proxy.
- Source repos for the official Kalshi SDKs are not linked from Kalshi's docs and were not found under github.com/Kalshi.
- `JeffSackmann/tennis_atp` metadata did not come back from the search API in this session.

---

## Q2. Which sports-modelling repos are credible, and do any publish out-of-sample results against closing lines?

### Takeaway
The credible, maintained sports-modelling code is mainly libraries and data tools: `penaltyblog`, `georgedouzas/sports-betting`, `nfelo`, `pybaseball`, `nflfastR` and FiveThirtyEight's Elo code. **None of the high-star repos checked publish an out-of-sample record against closing lines in their README.** The most popular "betting model" repo (kyleskom NBA, 1,734★) reports no accuracy, ROI or closing-line comparison at all. The only closing-line study found is a 0-star repo that reports a negative result.

### Cited Findings
- `martineastwood/penaltyblog` (Python football analytics: Poisson, Dixon-Coles and similar goal models, Elo and pi-ratings, ranked probability score, Opta/StatsBomb scraping, "bet smarter"): 230★, last push 2026-10-05, MIT — [GitHub](https://github.com/martineastwood/penaltyblog)
- `georgedouzas/sports-betting` ("Collection of sports betting AI tools", scikit-learn style backtesting of betting strategies): 810★, last push 2026-09-24, MIT — [GitHub](https://github.com/georgedouzas/sports-betting)
- `opisthokonta/goalmodel` (R goal models): 116★, last push 2024-03-30, no license detected — [GitHub](https://github.com/opisthokonta/goalmodel)
- `greerreNFL/nfelo` ("a power ranking, prediction, and betting model for the NFL"): 62★, last push 2026-10-07 (active), no license detected. The README publishes **no** out-of-sample results, ATS record, Brier score or Vegas comparison. It points to nfeloapp.com/analysis and says documentation "will be updated... in the near future" — [GitHub](https://github.com/greerreNFL/nfelo)
- `fivethirtyeight/nfl-elo-game` (data and code for FiveThirtyEight's NFL Elo): 350★, last push 2023-05-02, MIT — [GitHub](https://github.com/fivethirtyeight/nfl-elo-game)
- `kyleskom/NBA-Machine-Learning-Sports-Betting`: 1,734★, last push 2026-09-12, no license file visible. It pulls team stats from NBA endpoints (2007-08 onward) and odds from SBR, and offers EV and Kelly sizing. The README gives **no** accuracy figures, ROI, calibration, or comparison against closing lines or vig (grade E) — [GitHub](https://github.com/kyleskom/NBA-Machine-Learning-Sports-Betting)
- `jldbc/pybaseball` (Statcast, Baseball Reference and FanGraphs scraping): 1,730★, last push 2026-01-04, MIT, 138 open issues — [GitHub](https://github.com/jldbc/pybaseball)
- `nflverse/nflfastR` (NFL play-by-play): 547★, last push 2026-10-07, license "Other" — [GitHub](https://github.com/nflverse/nflfastR)
- `dashee87/blogScripts` (well-known football Poisson and Dixon-Coles blog notebooks): 386★, last push 2022-12-23, MIT — [GitHub](https://github.com/dashee87/blogScripts)
- `AnishKhetani/football-model`: 0★. It is a "walk-forward Elo vs the closing line" study of English leagues that "Concluded negative result" — [GitHub](https://github.com/AnishKhetani/football-model)
- `charlesmalafosse/sports-betting-customloss` (a profit-aware custom loss for neural-network classifiers; the author runs BetSentiment.com): 96★ — [GitHub](https://github.com/charlesmalafosse/sports-betting-customloss)
- Small repos mention CLV tracking but have no results: `scottdmorris/edgefinder` (MLB, soccer and UFC Elo/Poisson against FanDuel lines, with CLV tracking) — [GitHub](https://github.com/scottdmorris/edgefinder). `gaurigupta23/Sports_Betting_Market_Maker` is an Elo+Poisson two-sided quoting simulator "backtested on 1,700 Premier League matches" — [GitHub](https://github.com/gaurigupta23/Sports_Betting_Market_Maker)
- Market-level evidence relevant to sports on prediction exchanges: across 72.1M Kalshi trades (Jun 2021–Nov 2025), **sports** shows a 2.23 pp maker-taker return gap over 43.6M trades. Across all categories, makers earn +1.12% and takers -1.12% per trade. The author attributes maker gains to "providing liquidity to biased taker flow, not from better forecasting" — [Becker, jbecker.dev](https://jbecker.dev/research/prediction-market-microstructure)

### Inferences
- No open-source sports model has a public, audited out-of-sample record against closing lines. The existing engine's own walk-forward and CLV tracking is therefore stricter than anything found publicly. Public repos are best mined for data plumbing (pybaseball, nflfastR) and model components (penaltyblog's Dixon-Coles and RPS, FiveThirtyEight's Elo details), not for proven edge.
- The single closing-line study found reports a negative result. Becker's Kalshi data suggests that in sports the structural edge sits with liquidity providers rather than forecasters. This supports maker-first execution over pure prediction-based taking.

### Gaps
- nfeloapp.com/analysis was not fetched, so nfelo may publish ATS or closing-line results off-repo.
- Tennis-specific open models (beyond Sackmann data) with closing-line results were not found in this pass.

---

## Q3. What market-making implementations exist that suit binary contracts (inventory, quote skew, cancel-on-move, adverse-selection controls)?

### Takeaway
Three binary-contract market makers are worth studying. `warproxxx/poly-maker` is the most complete: it skews quotes by inventory, widens spreads for volatility and toxicity, switches between regimes, and has a heartbeat dead-man switch and a daily-loss kill switch. `rodlaf/KalshiMarketMaker` is a straightforward Avellaneda-Stoikov implementation on Kalshi. `kachence/polymm` is a candid post-mortem showing that hedge-leg failure and slow cancel-on-move drove losses. Polymarket's official `poly-market-maker` (Bands/AMM) is a simple, stale reference. Generic Avellaneda-Stoikov libraries exist, but they assume continuous prices rather than [0,1] bounded payoffs; `bs-p` claims an Avellaneda-Stoikov variant in logit space.

### Cited Findings
- **warproxxx/poly-maker** (MIT): maker-only on Polymarket CLOB V2.
  - Discovers markets via Gamma and ranks them by reward and rebate income against volatility and spread risk.
  - Takes WebSocket books and offers a paper mode.
  - **Inventory skew**: with excess YES, it lowers the YES bid and raises the NO bid. It cuts size near a soft cap, then pulls the adding side.
  - Posts paired YES and NO bids around a fair-value estimate, with spreads that widen with volatility and **toxicity (adverse selection)**.
  - Moves through **regimes**: quiet, trending, event (quotes pulled), reduce-only and halted.
  - Quotes inside the liquidity-rewards band in the quiet regime.
  - Has a heartbeat dead-man switch, risk caps and a daily-loss kill switch.
  - Python 3.12+, SQLite state — [GitHub](https://github.com/warproxxx/poly-maker)
- **rodlaf/KalshiMarketMaker** (MIT): Avellaneda-Stoikov with a reservation price, asymmetric quotes and sizes.
  - Risk aversion rises as inventory nears its limits.
  - Parameters: `gamma 0.2`, `k 1.5`, `sigma 0.001`, `T 28800`, `min_spread 0.02`, `max_position 3`, `dt 5.0`, `inventory_skew_factor 0.001`.
  - Market filter: 24h volume ≥500 and spread ≥2¢. Score = 0.35·volume + 0.65·spread; top 6 markets kept.
  - Global cap of 20 contracts, 3 per market. Resting orders are cancelled when a market is deselected.
  - Uses `/trade-api/v2` against `api.elections.kalshi.com` and `demo-api.kalshi.co` — [GitHub](https://github.com/rodlaf/KalshiMarketMaker)
- **kachence/polymm** (MIT): de-vigs bookmaker odds into fair value.
  - Posts "a cent above the best bid" only when edge ≥ `min_edge` (5% default).
  - When one side fills, it buys the opposite outcome so the pair costs < $1.
  - Unhedged "residual" positions lost money, which the author attributes to adverse selection.
  - An EDGE_LOST event cancels resting orders when fair value moves, but "was not fast enough" — [GitHub](https://github.com/kachence/polymm)
- **Polymarket/poly-market-maker** (MIT, last push 2024-07-05): Bands and AMM strategies on a 30s sync loop (midpoint → target orders → diff → cancel/place), cancel-all on SIGTERM — [GitHub](https://github.com/Polymarket/poly-market-maker)
- **holypolyfoundation/bs-p** ("AVX-512 Polymarket market-making kernel (Logit Jump-Diffusion + Avellaneda-Stoikov in logit space)"): 160★, last push 2026-07-04, MIT — [GitHub](https://github.com/holypolyfoundation/bs-p)
- Generic Avellaneda-Stoikov references:
  - `fedecaccia/avellaneda-stoikov`: 732★, last push 2023-07-06, no license — [GitHub](https://github.com/fedecaccia/avellaneda-stoikov)
  - `javifalces/HFTFramework` (RL-tuned Avellaneda-Stoikov research): 307★ — [GitHub](https://github.com/javifalces/HFTFramework)
  - `joaquinbejar/market-maker-rs` (Rust Avellaneda-Stoikov library): 105★ — [GitHub](https://github.com/joaquinbejar/market-maker-rs)
  - `mdibo/Avellaneda-Stoikov` (paper replication): 155★ — [GitHub](https://github.com/mdibo/Avellaneda-Stoikov)
  - `im1235/ISAC` (soft actor-critic control of the Avellaneda-Stoikov risk aversion): 154★ — [GitHub](https://github.com/im1235/ISAC)
- `apostleoffinance/prediction-market-maker-bot` (Rust) and `prediction-market-maker` (Python), described as "adaptive pricing, inventory management, and risk controls" for binary contracts; the Python version was built for a quant assessment: 15★ and 4★ — [GitHub](https://github.com/apostleoffinance/prediction-market-maker-bot)
- Policy context: poly-maker's income model relies on Polymarket liquidity rewards (a daily pool split by qualifying quote share) and maker rebates. Rewards show diminishing returns as one's share grows — [poly-maker repo / search summary](https://github.com/warproxxx/poly-maker); [Polymarket docs](https://docs.polymarket.com/trading/market-making)

### Inferences
- Most portable to a Python engine:
  - poly-maker's regime machine (quiet / trending / event-pull / reduce-only / halted).
  - poly-maker's cross-outcome inventory skew (shade YES and NO bids jointly).
  - Spread widening driven by toxicity.
  - The heartbeat dead-man switch.
  - polymm's lesson: a hedge-or-cancel latency budget is the binding constraint, and partial-fill residuals are where adverse selection bites.
- Classic Avellaneda-Stoikov uses arithmetic Brownian motion on price. For bounded [0,1] contracts near resolution, a logit-space formulation (as `bs-p` claims) or a time-to-resolution-aware sigma is more appropriate. KalshiMarketMaker's constant `sigma 0.001` is likely too crude near expiry. This is an inference, not tested.
- None of these repos publishes a fill-level replay or order-book simulator validated against real queue position. A backtester would still need to be built or adapted.

### Gaps
- Hummingbot's Avellaneda strategy docs were not retrieved (the URL returned 404), so its parameterisation is not covered here.
- No open-source queue-position-aware L2 simulator specific to Kalshi or Polymarket was found. Becker's dataset has trades, not full L2 history. `synpath` advertises tick-level Kalshi book history but is unverified.

---

## Q4. Is there any repo with real, verifiable P&L? Which repos are marketing funnels or scams?

### Takeaway
**No repo in this survey has independently verified live P&L (grade A).** The best case is `kachence/polymm` (grade B): it publishes an arbitrage/residual breakdown (+$8,293 / −$3,184, ~+$5k net) and links a public Polymarket profile (@b00k13) that can be checked on-chain, but the claim was not verified here. The strongest evidence of profit is aggregate rather than repo-specific: Becker's 72.1M-trade Kalshi study finds makers +1.12% and takers −1.12% per trade. The Polymarket-bot space has a documented malware problem, including hijacked orgs, typosquatted npm dependencies, star and fork farming, and a probable SDK impersonation repo.

### Cited Findings

**Profitability evidence**
- `kachence/polymm` claims "Arbitrage: +$8,293; Directional residual: −$3,184; Net: about $5k" over a few months in early 2026 on sports and esports. No wallet address appears; the repo links the Polymarket profile @b00k13. It was retired because it "got too slow", the current Rust version is not included, and it was "built almost entirely with AI" (grade B) — [GitHub](https://github.com/kachence/polymm)
- `suislanchez/polymarket-kalshi-weather-bot` advertises "Highest profits $1.8k", but its disclaimer says it does not trade real money (grade D, ambiguous) — [GitHub](https://github.com/suislanchez/polymarket-kalshi-weather-bot)
- `warproxxx/poly-maker`: "Market making on Polymarket is competitive and can lose money"; no track record (grade E) — [GitHub](https://github.com/warproxxx/poly-maker)
- Aggregate data on Kalshi (Jun 2021–Nov 2025, 72.1M trades, $18.26B volume):
  - Makers +1.12% and takers −1.12% average excess return per trade.
  - 5¢ contracts won 4.18% of the time.
  - Takers lost on 80 of 99 price levels.
  - Through 2023, takers were ahead (+2.0%); the gap flipped to makers after the 2024 election.
  - Caveat: "takers" is a proxy for unsophisticated traders, and spreads are unobserved.
  - Source: [Becker, "The Microstructure of Wealth Transfer in Prediction Markets"](https://jbecker.dev/research/prediction-market-microstructure). The dataset is 36 GiB of Parquet on Cloudflare R2, MIT — [GitHub](https://github.com/Jon-Becker/prediction-market-analysis) (3,859★, last push 2026-10-04)
- An academic study of Polymarket (late 2022–Oct 2025) reportedly found ~70.8% of users net negative and the top 1% capturing most gains. **This is secondary**: it was reported via a web-search summary, and the primary paper was not retrieved — [search summary citing mexc/ingame coverage](https://www.mexc.com/en-NG/news/232595)

**Malware and scams (documented)**
- **Hijacked `dev-protocol` org** (StepSecurity, 2026-03-15):
  - `dev-protocol/polymarket-copytrading-bot-sport` plus 20+ variants.
  - Typosquatted npm dependencies: `ts-bign` (pulls the `levex-refa` stealer for `.env`, `id.json` and `config.toml`) and `big-nunber` (pulls `lint-builder`, whose postinstall chowns `~/.ssh` and opens port 22 via ufw).
  - The bot "works as advertised".
  - 366★/307 forks inflated by bot accounts; victims' warning issues were deleted.
  - C2 domains: `cloudflareguard.vercel.app` and `cloudflareinsights.vercel.app`.
  - Source: [StepSecurity](https://www.stepsecurity.io/blog/malicious-polymarket-bot-hides-in-hijacked-dev-protocol-github-org-and-steals-wallet-keys)
- **Nine malicious npm packages** from the account "polymarketdev" (May 2026) posed as Polymarket CLOB tools and exfiltrated private keys to a Cloudflare Worker; one was named `polymarket-claude-code` — [OSV MAL-2026-4211](https://osv.dev/vulnerability/MAL-2026-4211), [OSV MAL-2026-4215](https://osv.dev/vulnerability/MAL-2026-4215)
- Other npm stealers aimed at Polymarket wallets — [SafeDep](https://safedep.io/malicious-polymarket-npm-crypto-wallet-drainer/). A `clob-client-math` poisoned dependency and a SlowMist-flagged copy-trading bot were reported only in secondary coverage — [MEXC news](https://www.mexc.com/news/1187684)

**Repo-level red flags observed in this survey (grade X; suspicion, not confirmed malware)**
- **`dev-polymarket/clob-client-v2`**: 509★ and 291 forks, which is more than the official `Polymarket/clob-client-v2` (75★). The owner is a GitHub **User** account, not the Polymarket org. It was created 2026-05-04, the same day as Polymarket's py-sdk. Its README install line is `npm i @polymarkets/clob-client-v2` (plural "polymarkets"). This fits impersonation or typosquat patterns; do not use it — [GitHub](https://github.com/dev-polymarket/clob-client-v2), compare [official](https://github.com/Polymarket/clob-client-v2)
- **`lovePinesnop/polymarket-kalshi-arbitrage-bot`**: 130★ but **1,117 forks**, a description that repeats the word "polymarket" about 30 times, and no license — [GitHub](https://github.com/lovePinesnop/polymarket-kalshi-arbitrage-bot)
- **`radioman/polymarket-arbitrage-trading-bot`**: the repo was created in 2016 (an old repo repurposed) and has a keyword-stuffed description ("Polymarket trading bot" repeated) and a non-standard default branch "staging" — [GitHub](https://github.com/radioman/polymarket-arbitrage-trading-bot)
- **`1canhhoa/sports-betting-toolbox`**: 141★ but **872 forks** — [GitHub](https://github.com/1canhhoa/sports-betting-toolbox)
- `Cleverfuxaqo1668/Polymarket-Telegram-Bot`: 170★ and 135 forks, with an auto-generated-looking username — [GitHub](https://github.com/Cleverfuxaqo1668/Polymarket-Telegram-Bot)
- `thesoulcrancerdev/poly-trading-strategies`: 102★ and 102 forks, keyword-stuffed topics — [GitHub](https://github.com/thesoulcrancerdev/poly-trading-strategies)
- Vendor or marketing funnels (legitimate companies, but the repos promote their products):
  - `OctagonAI/kalshi-trading-bot-cli` — [GitHub](https://github.com/OctagonAI/kalshi-trading-bot-cli)
  - `livetennisapi/polymarket-tennis` (data vendor; 271★ and 230 forks) — [GitHub](https://github.com/livetennisapi/polymarket-tennis)
  - `Synpath-ai/synpath` — [GitHub](https://github.com/Synpath-ai/synpath)
  - `machina-sports/sports-skills` — [GitHub](https://github.com/machina-sports/sports-skills)
  - `kuest/prediction-market` (a "launch your own Polymarket-like" platform; 1,100★ and 801 forks) — [GitHub](https://github.com/kuest/prediction-market)

**Geoblock / KYC**
- No repo found states that its purpose is evading geoblocks or KYC. However, most Polymarket bots (poly-maker, polymm, poly-kalshi-arb and the copy-trading bots) target the **international** CLOB with a Polygon wallet and say nothing about jurisdiction. That CLOB is off-limits for order placement from US IPs, per the repo's own compliance notes. Only the `polymarket-us` SDK, `uselayer` and `synpath` explicitly target Polymarket US — [polymarket-us-python](https://github.com/Polymarket/polymarket-us-python), [uselayer-sdk](https://github.com/uselayer/uselayer-sdk), [taetaehoho/poly-kalshi-arb](https://github.com/taetaehoho/poly-kalshi-arb)

### Inferences
- Red-flag heuristics that can be applied mechanically:
  - fork count ≥ star count;
  - keyword-repeated descriptions;
  - an old account with recent Polymarket-only repos;
  - user-account "SDK" names that mimic official orgs;
  - npm packages with typosquatted names or postinstall scripts;
  - READMEs that ask for a private key in `.env` before `npm install`.

  Any third-party code should be vendored only after review, installed with `--ignore-scripts`, and never run with live keys.
- Given that no repo reaches grade A, "profitable bot" claims in this space should be treated as marketing by default. The best available proxy evidence is aggregate maker-versus-taker data (Becker) and on-chain profiles that can be checked (polymm's @b00k13), which someone would still need to audit.
- **Reusable components for the existing engine, ranked by credibility:**
  1. Official SDKs: `polymarket-us`, `polymarket-client`, `kalshi_python_async`, or a client generated from Kalshi's OpenAPI spec.
  2. The Becker Kalshi/Polymarket trade dataset (MIT) for price-level calibration and studies of longshot bias and maker edge.
  3. poly-maker's regime, skew and kill-switch design (MIT).
  4. KalshiMarketMaker's Avellaneda-Stoikov parameterisation as a baseline (MIT).
  5. polymm's de-vig plus hedge-leg logic and its post-mortem (MIT).
  6. penaltyblog, pybaseball and nflfastR for sports features and models.
  7. flumine's event-driven framework design, from a long-lived Betfair framework (MIT).

  Repos with no license (taetaehoho, dr-manhattan, fedecaccia, suislanchez, ImMike, Kalshi starter code) cannot legally be copied verbatim.

### Gaps
- The @b00k13 Polymarket profile and on-chain P&L were not checked; doing so needs a Polymarket data-API or on-chain lookup.
- The primary paper behind the "70.8% of Polymarket users net negative" figure was not retrieved.
- `dev-polymarket/clob-client-v2`'s `package.json` and npm publisher were not inspected, so whether it is malicious (not just impersonating) is unconfirmed.
- GitHub issue threads were not reviewed for user-reported P&L on poly-maker, KalshiMarketMaker or the arb bots.
