# CLAUDE.md — Substrate/Echo Engine Handoff
Project handoff from a claude.ai chat ("the informational substrate") to Claude Code.
Read this fully before touching code. The chat built and validated everything below;
your job is real-world wiring, not redesign.

## What this project is
A two-engine prediction stack testing a fringe hypothesis with rigorous machinery:
- **Substrate Engine** (`engine.py`): certification instrument. QRNG scheduler ->
  commit-reveal ledger (SHA-256, seal-before-resolve) -> plug-in channel ->
  triple-null scoring (coin / longshot-corrected market / conventional baseline) ->
  anytime-valid e-process gate (certify at E>=20) -> Hedge fusion over experts.
- **Echo Engine** (`wwwwwh.py`): six working category models. WHO=online
  Bradley-Terry, WHAT=Dirichlet, WHEN=Weibull hazard, WHERE=2D KDE,
  WHY=LOO attribution (audit-only), HOW=Markov pathways.
- **ARV module** (`arv.py`): double-blind remote-viewing workflow; assignment,
  transcript, and call each sealed pre-resolution; feedback + 20% ablation subset.
- **Impact harness** (`impact.py`): consistency-layer falsifiable test
  (accuracy-vs-stake on exogenous events). Calibrated: FP 5.0%, power 93%.

Epistemic frame (do not drift from this): the anomalous "channel" is a HYPOTHESIS
the instrument measures, never an assumption. The engine's job is to let a real
signal certify and force noise to die. In validation: d=0 -> flat E, ~<=5% false
cert; d=0.2 -> certifies (sports arm median ~442 trials; ARV loop 56.3% hits,
E>>threshold). A gate that can say no is the product.

## Non-negotiable invariants
1. **Frozen protocol v1.0** (`PROTOCOL_v1.md`). Parameter hash
   `47e3c098c7c8aedc6f9fa12bcbc67ccd9ddd4e5000610b6483d3f74a78c8eb9b`.
   Committed params (delta=0.04, p-band [0.40,0.60], E>=20, nulls, ablation 20%,
   longshot fit-on-prior-frozen) may not change silently. Any change = PROTOCOL_v1.x,
   new hash, evidence restart unless analysis-orthogonal and logged.
2. **Shadow mode.** Nothing stakes money. Promotion (quarter-Kelly, capped,
   rho<=0.01, nobody-knows domains only) requires out-of-sample certification first.
   Do not wire live order placement.
3. **Seal-before-resolve.** Every prediction/call/assignment hashes into the ledger
   before outcome. Decision-time snapshots only; no backfilled probabilities.
4. **Exclusions.** No somebody-knows events (CPI/Fed/awards/FDA/earnings — leakage
   confound + legal risk). No outcome-reflexive domains (equities/crypto/elections)
   in certification trials.
5. **WHY module is audit-only.** Its outputs never route to calls or stakes.

## Current state
All modules run and are validated on synthetic worlds (see README.md for numbers).
The channel inside validation is SIMULATED. Real inputs are the missing piece.

## Milestones, in order
1. **Real-history backtest**: adapter from the user's Polymarket sports-bot logs to
   `ingest.py` schema (event_id, domain, close_time, resolve_time, market_prob at
   decision time, baseline_prob, outcome). Run market/baseline experts on last
   season; report ScoreBook + fusion weights. (Bot context: existing project,
   tennis/table-tennis/MLB slate.)
2. **Kalshi decision-time snapshot service**: poll public market data (respect ToS
   and rate limits), persist decision-time YES prices for weather dailies at max
   lead + seasonal series; settlement-station framing (e.g., Chicago = O'Hare NWS
   daily climate report).
3. **ARV session runner**: replace tag-set ImageBank with a real image pool;
   CLI or minimal local web UI for session -> transcript seal -> blind judge ->
   call; sqlite persistence of the CommitLedger.
4. **Live dashboard**: e-process wealth curves per arm, fusion weights, trial counts.
5. **Post-certification only**: enable impact-decay sweep and promotion logic.

## Commands
- `python3 backtest.py`   full synthetic-year validation + power study
- `python3 echo_demo.py`  all six Echo models + gate-integration demo
- `python3 impact.py`     ARV loop through gate + impact-decay sweep
Deps: numpy, matplotlib only. Original sandbox had no network; Claude Code does —
that is precisely why milestones 1-3 are now possible.

## Suggested first prompts for Claude Code
- "Read CLAUDE.md and README.md, then write the bot-log adapter for ingest.py
  against this CSV sample: <paste>"
- "Build the Kalshi snapshot poller for weather dailies with decision-time
  persistence to sqlite; shadow mode, read-only."
- "Build the ARV session CLI with real images and ledger persistence."
