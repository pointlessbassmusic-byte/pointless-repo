# Data Sources and Infrastructure for Informational/Speed Edges in US Sports and Event-Contract Markets (as of Oct 2026)

Scope note: about 20 tool calls. Many claims about vendor latency and pricing come from vendor marketing or competitor comparison blogs, and they are labeled that way. Where I found no primary source, the item is listed under Gaps and not stated as fact.

## 1. Official / low-latency sports data (Sportradar, Genius, Stats Perform, SportsDataIO, league feeds)

### Takeaway
Official league data (Genius for the NFL, Sportradar for the NBA/NHL/MLB and others) arrives a few seconds after the live action and tens of seconds before TV and streams. That gap is the main "speed edge" in in-play sports. But the feeds are enterprise-priced, sold under betting-data licenses to sportsbooks and operators, and there is no independent, named-vendor latency benchmark. A small team should assume it cannot win on in-play speed against market makers who hold these feeds.

### Cited Findings
- Genius Sports (vendor claim, NFL): "we're at a latency of about four to six seconds [behind live on-field action] on our primary stream," which the vendor says is about 30–40 seconds ahead of television. — [Vixio](https://www.vixio.com/insights/gc-genius-sports-launches-live-betting-product-leagues-search-gaming-integrations)
- Only independent academic measurement found: Northwestern researchers got a feed from a "leading sports data feed provider" (not named). They timestamped events against archived video (OCR of the game clock) and found the data feed was systematically faster than live cable TV for NBA games. Publication date not confirmed (file named gi18, likely about 2018). — [Northwestern paper](https://networks.cs.northwestern.edu/website/publications/gi18.pdf)
- TV and stream delay benchmarks: traditional broadcast about 5–7 s behind live ([Dolby OptiView](https://optiview.dolby.com/resources/blog/sports/low-latency-sports-streaming/)). Phenix's Super Bowl study found over-the-air broadcasts averaged about 18 s behind the field and cable about 28 s ([Phenix](https://blog-stg.phenixrts.com/once-again-super-bowl-latency-numbers-are-staggering)). Fox at NAB 2025 put broadcast at 5–8 s ([Rethink Research](https://rethinkresearch.biz/?p=59630)). These measure different things (field vs screen), so they are not directly comparable.
- Exclusivity structure example (soccer): after the 2022 settlement of their litigation, Genius kept the exclusive right to low-latency official Football DataCo betting data through 2024, and Sportradar resold a sublicensed, delayed "Official FDC Secondary Feed." So "official" feeds come in latency tiers, and the primary low-latency tier is exclusive. — [Business Wire / Genius statement](https://www.businesswire.com/news/home/20221010005708/en/Genius-Sports-Statement-Following-Settlement-of-Litigation-with-Sportradar); [Covers](https://www.covers.com/industry/genius-sportradar-reach-litigation-settlement-october-11-2022)
- MLB Stats API is free (repo context: its terms permit personal/non-commercial use). Sackmann tennis data is CC BY-NC-SA (non-commercial). — repo `CLAUDE.md` compliance notes (internal; not independently re-verified this session)

### Inferences
- The edge window is roughly feed latency (about 2–6 s) vs TV or stream (about 10–40 s+). Anyone trading in-play on Kalshi or Polymarket US off a TV or stream is structurally behind market makers with official feeds. Small teams should avoid in-play sports or only act on slower-moving information.
- "Scraper" feeds (public scoreboards, league GameCast pages) likely sit between official feeds and TV. That is plausibly fine for pre-game work and dangerous for in-play.

### Gaps
- No public pricing found for Sportradar, Genius, Stats Perform/Opta or SportsDataIO enterprise betting feeds. I also found no current (2023–2026) independent latency measurement naming a vendor.
- I did not find license terms that explicitly allow or forbid using official betting data for trading on CFTC exchanges (vs licensed sportsbooks). This must be checked with each vendor's contract.
- I found no Sportradar latency figures.

## 2. Sharp odds and line-movement data (Pinnacle/Circa/Bookmaker via APIs)

### Takeaway
Pinnacle closed public API access in July 2025, so sharp-line data now comes through aggregators, at prices from about $30/month (polling, possibly delayed) to several thousand per month (enterprise push feeds). Push or WebSocket delivery matters more than headline latency. I found no rigorous public study showing that sharp-book moves predict Kalshi or Polymarket prices. The case rests on the well-known efficiency of Pinnacle closing lines, not on exchange-specific evidence.

### Cited Findings
- Pinnacle "shut down public access to its API in July 2025." Old endpoints return nothing unless you are a commercial partner. — [DEV Community](https://dev.to/ryankr/pinnacle-killed-its-public-api-heres-how-to-get-pinnacle-odds-in-2026-with-code-39pe)
- The Odds API (official pricing page): Free 500 credits/month; $30 for 20K; $59 for 100K; $119 for 5M; $249 for 15M. All sports, all markets and historical odds are included. Pinnacle is listed under EU bookmakers. — [the-odds-api.com](https://the-odds-api.com/)
  - Competitor claims: The Odds API's Pinnacle data is scraped from a public website, and its own docs note it "may incur a delay" ([DEV Community](https://dev.to/ryankr/pinnacle-killed-its-public-api-heres-how-to-get-pinnacle-odds-in-2026-with-code-39pe)). It is polling-only, and credits burn as markets × regions, with historical data costing 10× ([comparison blogs](https://oddspapi.io/blog/?p=2498)). Not verified against The Odds API docs.
- OpticOdds: no published pricing (quote form only). A competitor reports it starts at about $5,000/month ([oddspapi](https://oddspapi.io/blog/?p=2863)), while another source says "high hundreds per month" ([DEV Community](https://dev.to/ryankr/pinnacle-killed-its-public-api-heres-how-to-get-pinnacle-odds-in-2026-with-code-39pe)). The two conflict. Its marketing claims odds delivery in under 800 ms, with push, pull and queue (RabbitMQ) delivery ([opticodds.com](https://www.opticodds.com); [SportsGameOdds comparison](https://sportsgameodds.com/compare/opticodds)). Whether it carries Pinnacle is reported inconsistently.
- pinnapi (Pinnacle-only): $99–$229/month, with SSE, WebSocket and REST and a free tier of 100 REST requests/day. It claims 15–40 ms from a Pinnacle price change to the client handler (self-reported). — [DEV Community](https://dev.to/ryankr/pinnacle-killed-its-public-api-heres-how-to-get-pinnacle-odds-in-2026-with-code-39pe)
- SportsGameOdds: $99–$499/month. The free tier has 9 books with a 10-minute delay. Includes Pinnacle (vendor's own claim). — [SportsGameOdds](https://sportsgameodds.com/blog/comparing-odds-api-providers)
- Odds-API.io: £99–229/month, WebSocket, sub-100 ms claimed (vendor). OddsPapi: per-request pricing, WebSocket, Pinnacle on the free tier at 250 req/month. — [odds-api.io](https://odds-api.io/blog/best-odds-apis-2026); [OddsPapi latency page](https://oddspapi.io/coverage/latency)
- SharpAPI documents a Pinnacle "odds changed at" timestamp field, which is useful for measuring staleness yourself. — [SharpAPI docs](https://docs.sharpapi.io/en/concepts/pinnacle-odds-changed-at/)

### Inferences
- For a small team, the cheapest serious setup is a Pinnacle-only push feed (about $100–230/month) plus The Odds API for breadth or history (about $30–120/month). Enterprise feeds such as OpticOdds only pay off when trading in-play.
- The repo's existing "market blend" and The Odds API TTL cache (500 req/month free tier) match the low-cost tier. Moving to a push Pinnacle feed is the obvious upgrade if pre-game lag versus Kalshi or Polymarket US turns out to be measurable.

### Gaps
- I found no published 2023–2026 study measuring lead/lag between Pinnacle/Circa moves and Kalshi or Polymarket prices. This should be measured in-house by logging both with timestamps.
- Unabated and Circa/Bookmaker API access and pricing were not verified this session.
- OddsJam API pricing was not verified (only a competitor page was seen).

## 3. Injury / lineup / news feeds

### Takeaway
Scheduled league disclosures, such as NBA injury report windows, set when public information arrives. The informational edge clusters just after those releases. Trading on nonpublic injury information is prohibited on Kalshi and enforced. The legitimate edge is reacting faster to *public* releases (official reports, MLB lineups, beat-reporter posts) than exchange quotes adjust.

### Cited Findings
- NBA 2025-26 injury report rules: teams must designate status by 5 p.m. local time the day before a game. For the second game of a back-to-back, the deadline is 1 p.m. local on game day. Game-day reports are due 11 a.m.–1 p.m. local (8–10 a.m. for tip-offs at 5 p.m. or earlier), with continual updates. — [NBA official](https://official.nba.com/nba-injury-report-2025-26/)
- Kalshi launched NBA player props in November 2025. They were limited to about 50 players with a $10,000-per-trade cap per user, used the IC360 integrity monitor, and barred current and former NBA players, coaches and staff from trading. — [Front Office Sports](https://frontofficesports.com/?p=210182) (via search summary)
- Insider-information cases that show the risk: Jontay Porter (left games early so a betting ring could profit on props), Terry Rozier allegations, and federal charges against a former player for leaking LeBron James injury information. — [iGaming Business](https://igamingbusiness.com/sports-betting/kalshi-giannis-antetokounmpo-equity-deal-analysis/); [Gambling Insider](https://www.gamblinginsider.com/news/110196/giannis-antetokounmpo-kalshi-stake-conflict-concerns)
- Action Network (promotional source with a promo code) argues sportsbooks wait for the 5 p.m. and game-day reports before pricing props, which creates early windows on Kalshi. — [Action Network](https://www.actionnetwork.com/nba/how-to-trade-on-nba-early-before-sportsbooks-open)
- MLB probable pitchers and lineups are available through the free MLB Stats API, which the repo already uses for the starting-pitcher overlay (`sportsbot/engine/baseball.py`). — repo `CLAUDE.md` (internal)

### Inferences
- Practical build: poll the official NBA injury-report PDFs on their release schedule and MLB lineup endpoints around posting times, then compare against exchange quotes within seconds. That is a legal "public information speed" edge, and it is cheap.
- X/Twitter firehose access is likely the fastest news channel, but its cost (enterprise API) was not verified. The repo deliberately uses Bluesky for chatter instead (`sportsbot/signals/chatter.py`).

### Gaps
- No measured market-reaction speed (Kalshi or Polymarket US quote change after an injury report) was found.
- Rotowire, SportsDataIO news and X API enterprise pricing and latency were not verified.
- No source found on NFL inactives (90 minutes before kickoff) or tennis retirement feed latency.

## 4. Weather (ensembles, ASOS, Kalshi settlement)

### Takeaway
Kalshi weather volume is booming ($564M through July 2026, already more than all of 2025). On Aug 27, 2026, Kalshi announced The Weather Company (TWC) as its data source for verifying weather settlements. The exact scope is unclear: Kalshi's help center still names the NWS Daily Climate Report as the sole settlement source, and an Aug 17, 2026 CFTC filing "added an additional Source Agency" to the temperature template. **Traders must read the current rulebook for each contract before modelling settlement.**

### Cited Findings
- Kalshi and TWC partnership (announced Aug 27, 2026): Kalshi "will use The Weather Company's enterprise-grade weather data feeds as its trusted source for verifying weather hedging settlements." TWC "provides the authoritative observation data used to settle these markets with a consistent, documented methodology for each market type." No effective date was given, and the coverage does not say whether NWS reports are replaced. — [Artemis](https://artemis.bm/news/prediction-market-kalshi-partners-with-the-weather-company-for-weather-hedge-settlements/); [Claims Journal/Bloomberg](https://www.claimsjournal.com/news/national/2026/08/31/339851.htm); [Markets Media](https://marketsmedia.com/kalshi-partners-with-the-weather-company)
- TWC quality control (per coverage): before readings are used, they are compared against nearby stations and weather model analysis to filter physically implausible values. — [Claims Journal](https://www.claimsjournal.com/news/national/2026/08/31/339851.htm)
- Kalshi weather volume: $564M through July 2026, more than its full-year 2025 total. Weather and climate volume is up about 500% year on year. — [Claims Journal/WSJ via Bloomberg](https://amp.claimsjournal.com/news/national/2026/08/31/339851.htm)
- Kalshi help center (current at time of search): "NWS Daily Climate Report" is the settlement source, and apps like AccuWeather or Google do not determine outcomes. Contracts settle on the final climate report the next morning. During Daylight Saving Time, the high is recorded from 1:00 AM to 12:59 AM local the next day (because the report uses local standard time). Settlement can be delayed if the high is inconsistent with METAR 6-hour or 24-hour highs, or if the final value is below a preliminary one. — [Kalshi Help: Weather Markets](https://help.kalshi.com/markets/popular-markets/weather-markets)
- CFTC filings on Aug 17, 2026 and Sep 2, 2026 amend Kalshi's general temperature contract template. One describes "Added in an additional Source Agency." The agency's name could not be extracted (the PDF was not machine-readable here). — [CFTC filing 08172617937](https://www.cftc.gov/filings/orgrules/rules08172617937.pdf); [CFTC 09022622979](https://www.cftc.gov/filings/orgrules/rules09022622979.pdf)
- Older city rulebooks (Central Park NYC, Chicago Midway, Denver) define the underlying as the "Maximum" row of the NWS Daily Climate Report and ignore revisions after expiry. — [CFTC filing](https://www.cftc.gov/filings/ptc/ptc080521kexdcm003.pdf); [CFTC filing](https://cftc.gov/filings/ptc/ptc122121kexdcm034.pdf)

### Inferences
- If settlement moves to TWC quality-controlled observations, model edge shifts toward predicting the station observation (ASOS/METAR) and any QC adjustment, rather than the NWS CLI text. Discrepancies between TWC and the NWS CLI, and DST-window effects, are candidate edges but need rulebook confirmation.
- The intraday edge for daily-high markets likely comes from live station observations (METAR hourly plus 5-minute and 1-minute ASOS), which reveal the running maximum before the final report. This is public data, so it is legal to use.

### Gaps
- No source fetched this session for GEFS, ECMWF ENS, HRRR or NBM access and cost, or for 1-minute ASOS access (NOAA/NCEI, IEM). Background knowledge only: GEFS, HRRR and NBM are free from NOAA (NOMADS, AWS Open Data), and ECMWF open data has been free since 2024–25. **Verify before relying on this.**
- I could not confirm which Kalshi markets (daily high and low, rain) now settle on TWC, from what date, or whether the NWS CLI remains the fallback. Action: pull the current KXHIGH* rulebook from kalshi.com or read the Aug/Sep 2026 CFTC filings manually.
- No published evidence quantifying model-vs-market edge in Kalshi weather markets was found.

## 5. Economic-release markets (consensus vs nowcasts)

### Takeaway
The best evidence is the Fed's own FEDS 2026-010 paper. It finds Kalshi's headline-CPI forecasts beat Bloomberg consensus and match it on core CPI and unemployment, and that Kalshi had a perfect FOMC record the day before meetings. That implies these markets are already fairly efficient. I found no systematic evidence that the Cleveland Fed nowcast or GDPNow beats Kalshi prices.

### Cited Findings
- FEDS 2026-010, "Kalshi and the Rise of Macro Markets" (Diercks, Katz, Wright; released Feb 12, 2026, preliminary). Kalshi outperformed Bloomberg consensus on headline CPI and performed similarly on core CPI and unemployment. Kalshi-implied distributions are comparable to the Survey of Market Expectations and Bloomberg consensus. — [Federal Reserve](https://www.federalreserve.gov/econres/feds/kalshi-and-the-rise-of-macro-markets.htm); [Axios](https://www.axios.com/2026/02/19/kalshi-fed-prediction-markets)
- Kalshi had a "perfect forecast record" the day before FOMC meetings, beating fed funds futures. — [Yogonet summary](https://www.yogonet.com/international/news/2026/03/20/118189-federal-reserve-study-highlights-kalshi-as-emerging-tool-for-macroeconomic-forecasting); [Katten](https://quickreads.ext.katten.com/post/102mjw0/federal-reserve-researchers-find-prediction-markets-deliver-forecasting-value-com)
- A secondary summary cites headline-CPI mean absolute error of 0.063 (Kalshi) vs 0.081 (Bloomberg). Not verified against the paper. — [Motley Fool](https://fool.com/investing/2026/03/16/federal-reserve-research-kalshi-prediction-markets)
- A CEPR analysis raises concerns about pricing biases and suggests treating prices as indicators, not precise forecasts. — [iGaming Business](https://igamingbusiness.com/finance/federal-reserve-prediction-markets-paper-warsh/) (as summarized)
- Cleveland Fed nowcast critique: a 2023 opinion piece says it ran high during disinflation (and low when inflation was rising). This is not a formal test. — [Slope of Hope](https://slopeofhope.com/?p=244607)
- Trading guides claim CPI markets are "among the least efficient" and suggest trading when the nowcast diverges by 5% or more. They offer only a single hindsight example (Jan 2025) and no backtest. — [botforkalshi](https://www.botforkalshi.com/blog/kalshi-economic-indicators-trading)

### Inferences
- Simply copying the Cleveland Fed nowcast or GDPNow is unlikely to be a durable edge, since the market already beats consensus. Any edge more likely sits in thin tail brackets (stale quotes) and in fast reaction to the 8:30 ET release itself (which needs low-latency release access).

### Gaps
- No systematic backtest found comparing Kalshi CPI or payroll bracket prices against Cleveland Fed nowcast or GDPNow errors.
- Data-release latency infrastructure (BLS lockup and press feeds, low-latency news vendors) and its cost were not researched.

## 6. Infrastructure: Kalshi / Polymarket US APIs, hosting, latency, rate limits

### Takeaway
Kalshi offers REST, WebSocket and FIX (FIXT.1.1/FIX50SP2 over TLS). Rate tiers run from 20/10 reads/writes per second up to 400/400, and the top tiers are volume-gated. AWS PrivateLink is available from Premier tier and VPC peering at Prime. Third-party probes put Kalshi's origin in AWS us-east-2 (Ohio), so hosting a bot in us-east-2 is the practical co-location. There is no documented formal co-location program. I found no public infrastructure details for Polymarket US.

### Cited Findings
- Kalshi rate-limit tiers (official): Basic 20 reads/10 writes per second (on signup). Advanced 30/30 (API application form). Premier 100/100 (3.75% of monthly exchange volume plus technical-competency review). Prime 400/400 (7.5% of volume plus review). — [Kalshi docs: rate limits](https://docs.kalshi.com/getting_started/rate_limits)
- Kalshi FIX (margin): hosts `margin-mm.fix.elections.kalshi.com` (order entry) and `margin-marketdata.fix.elections.kalshi.com` (market data, port 8233). TLS 1.2+ is required and cipher suites follow AWS Network Load Balancer policies, which implies AWS hosting. One FIX connection per API key. FIX messages use the same token buckets as REST. Mass Cancel is limited to 1 per second. — [Kalshi docs: FIX connectivity](https://docs.kalshi.com/fix-margin/connectivity)
- Kalshi PrivateLink: "FIX traffic is routed entirely within the AWS backbone." Available at Premier tier and above via institutional@kalshi.com. VPC peering is available at Prime. — [Kalshi docs: FIX connectivity](https://docs.kalshi.com/fix-margin/connectivity)
- Hosting (third-party probe): Kalshi `api.elections.kalshi.com` sits behind AWS CloudFront with origin in AWS us-east-2 (Ohio). Polymarket's global CLOB sits behind Cloudflare with origin in AWS eu-west-2 (London). — [Glassnode latency monitor](https://latency.glassnode.com/prediction-markets/about)
- Conflicting claim: a VPS vendor (which sells Chicago servers) inferred Chicago from about 1.14 ms RTT (Feb 2026). This conflicts with the us-east-2 finding and comes from a commercially interested source. — [QuantVPS](https://www.quantvps.com/blog/kalshi-servers-location)
- Polymarket global CLOB limits: 9,000 requests per 10 s general, `/book` 1,500 per 10 s, POST /order 5,000 per 10 s burst and 48,000 per 10 minutes sustained. Limits are enforced by Cloudflare throttling (queue or delay, not reject). This applies to the global, non-US venue. — [Polymarket docs](https://docs.polymarket.com/api-reference/rate-limits)

### Inferences
- For a small team, Basic or Advanced Kalshi tiers (≤30 writes/s) are enough for pre-game and weather strategies. Speed races (in-play, data release) need Premier+ volume they cannot reach, so compete on models, not latency.
- Hosting a bot in AWS us-east-2 is the cheap approximation to co-location for Kalshi. The repo's Linode box (97.107.138.196) is not in AWS. Measure round-trip time before any latency-sensitive strategy.

### Gaps
- Polymarket US (QCEX-based, CFTC DCM) API docs, rate limits and hosting region were not found. Global Polymarket docs may not apply.
- I found no published Kalshi WebSocket-specific limits and no matching-engine location statement.
- No independent end-to-end latency measurements (order acknowledgement times) for either venue were found.

## 7. Legality: public information vs MNPI, courtsiding, in-play delays

### Takeaway
Kalshi bans trading on confidential information and actively enforces this (about 200 investigations, bans, 10× fines). The CFTC has said Commodity Exchange Act anti-fraud and insider rules (Rule 180.1) apply to prediction markets. Fast reaction to public data (official feeds, public reports, weather observations, nowcasts) is legitimate. Trading on nonpublic data (team insiders, leaked injuries, possibly courtsiding) is not. Exchange in-play delay policies were not found.

### Cited Findings
- Kalshi's Feb 2026 disclosure: about 200 investigations over the prior year, more than a dozen active cases. A candidate who traded about $200 on his own race received a 5-year ban and a penalty of 10× the trade. An employee of the media company behind a YouTube streaming market (about $4,000 traded on advance nonpublic information) received a 2-year suspension. — [A&O Shearman](https://www.lit-wc.aoshearman.com/siteFiles/54154/Two%20Insider%20Cases%20We%E2%80%99ve%20Recently%20Closed%20-%20Kalshi%20Article%20(Feb.%2025%202026).pdf); [Front Office Sports](https://frontofficesports.com/newsletter/asset-class-kalshi-cracks-down/)
- After Kalshi's referrals, the CFTC issued an advisory that prediction markets are subject to federal anti-fraud and anti-manipulation authority, including insider trading under the Commodity Exchange Act and Rule 180.1. — [Mondaq](https://webiis10.mondaq.com/unitedstates/gaming/1755922/sports-betting-meets-march-madness-federal-cftc-scrutiny-to-impact-college-sports)
- Kalshi fined and suspended three political candidates in April 2026 ([Finance Magnates](https://www.financemagnates.com/fintech/kalshi-fines-political-candidates-to-demonstrate-enforcement-standards/)) and banned and fined two traders for inside information in September 2026 ([Front Office Sports](https://frontofficesports.com/article/kalshi-bans-fines-2-traders-who-bet-on-inside-information/)).
- Kalshi explicitly bans trading on confidential information. Coverage says (global) Polymarket does not. — [iGaming Business](https://igamingbusiness.com/sports-betting/kalshi-giannis-antetokounmpo-equity-deal-analysis/)
- State-level risk to sports contracts: Massachusetts preliminary injunction (Jan 2026) and a Connecticut enforcement suit (Aug 2026). — [Front Office Sports](https://frontofficesports.com/?p=224695)
- Polymarket main-CLOB order placement is geoblocked for US IPs. US-legal venues are Kalshi and Polymarket US. — repo `CLAUDE.md` compliance notes (internal)

### Inferences
- Courtsiding (relaying live events faster than feeds) uses publicly observable information but likely breaches ticket terms and league integrity policies. Under Kalshi's broad rules it may be treated as a violation. Small teams should treat it as off-limits.
- Users in states with active injunctions (for example, Massachusetts) may be blocked from sports contracts regardless of data edge.

### Gaps
- I found no explicit Kalshi or Polymarket US policy text on courtsiding or in-play order delays (bet delays like sportsbooks' 5–10 s). Check the rulebooks.
- I found no Polymarket US insider-trading rulebook text (it is a CFTC DCM, so CEA Rule 180.1 should apply, but this is not confirmed).
