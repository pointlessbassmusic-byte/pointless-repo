# SUBSTRATE ENGINE v0.1
Working implementation of the full stack designed in the substrate chat:
QRNG scheduler -> commit-reveal ledger -> channel interface -> triple-null scoring
-> anytime-valid e-process gate -> Hedge fusion.

## Modules
- engine.py    core machinery (all layers)
- backtest.py  synthetic-year validation + power study (run: python3 backtest.py)
- ingest.py    CSV schema for real historical data (bot logs, Kalshi exports)

## Validation results (synthetic year, seed-controlled)
Gate = e-process, certify at E>=20 (anytime-valid alpha<=0.05, Ville):
- Null world (d=0):    false certification 3.3% sports / 1.7% weather  [<=5% guaranteed]
- Substrate world (d=0.2): certification 100% sports (median 442 trials),
  68% weather within a 600-trial year (median 331)
- Fusion: Hedge weights migrate toward the channel expert only in the effect world.

## Design commitments baked in
- Longshot correction (a,b) fit on PRIOR season only, frozen in protocol hash
- p-band filter [0.40, 0.60]; QRNG selection kills cherry-picking
- Every prediction sealed (SHA-256) before resolution; protocol hash = pre-registration
  AND Newcomb policy commitment
- Channel is a plug-in: SimulatedChannel (validation only), ManualChannel (real ARV/
  presentiment calls). Hardware QRNG plugs into Scheduler(feed=...).
- Shadow mode by definition: nothing here stakes money. Promotion rule (quarter-Kelly,
  capped, nobody-knows domains only) applies ONLY after out-of-sample certification.

## ECHO ENGINE v0.1 (wwwwwh.py + echo_demo.py)
Working models per category: WHO=online Bradley-Terry/Elo, WHAT=Dirichlet outcome
types, WHEN=Weibull hazard (DAT timing host), WHERE=2D KDE region probs,
WHY=LOO attribution (audit-only, never bet book), HOW=Markov pathway model.
Demo validates parameter recovery per category; integration demo wraps the WHO
expert in the Substrate gate. Note: in the demo the gate correctly REFUSES to
certify Echo-WHO against the bias-corrected market (E=0.5) — the market sees the
same truth with less noise. A gate that can say no is the product.

## ARV + IMPACT MODULES (arv.py, impact.py)
- ARV: double-blind by construction; QRNG orthogonal image pairs; assignment,
  transcript, and call each sealed pre-resolution; feedback/ablation switch.
  Validation: d=0 -> hit 0.501, E=0.0; d=0.2 -> hit 0.563, E>>20 certified.
- Impact-decay harness (consistency-layer test on exogenous events):
  trend test calibrated (null FP rate 0.050), detects paradox-pressure decay
  with 93% power at gamma=3. Single-run p wobble is why certification uses
  e-processes; the trend test is a diagnostic, not the gate.
