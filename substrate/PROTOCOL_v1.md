# SUBSTRATE SHADOW-TRIAL PROTOCOL — v1.0 (FROZEN)
**Freeze date:** 2026-09-04 · **Protocol hash (SHA-256 of parameter block):** `47e3c098c7c8aedc6f9fa12bcbc67ccd9ddd4e5000610b6483d3f74a78c8eb9b`

## 1. Hypotheses
- **H0:** No channel. Sealed calls carry zero information about future outcomes beyond
  the nulls; the e-process is a nonnegative supermartingale with E[E_t] <= 1.
- **H1:** A channel exists at or near d ~= 0.2 (two-choice accuracy ~55.6%,
  ~0.01 bits/trial), per the Mossbridge presentiment meta-analytic effect.

## 2. Registered predictions
1. **Primary:** If H1, the e-process crosses E >= 20 within ~600 effective trials
   (sports arm median ~450 in calibration). If H0, it stays near 1 (FP <= 5%, verified 3.3%/1.7% in sim).
2. **Secondary (post-certification only):** accuracy decays with stake ratio rho on
   exogenous events (paradox pressure). Harness calibrated: FP 5.0%, power 93% at gamma=3.
3. **Tertiary (post-certification only):** feedback-ablation subset shows reduced
   hit rate iff the feedback loop is load-bearing; somebody-knows vs nobody-knows
   comparison probes determination-status sensitivity.

## 3. Trial arms & targeting
{
  "sports": {
    "source": "bot slate",
    "p_band": [
      0.4,
      0.6
    ],
    "cap_per_year": null
  },
  "weather": {
    "source": "Kalshi dailies at max lead + seasonal past-wall",
    "framing": "binary exceedance at named settlement station",
    "cap_per_year": 600
  }
}
Exclusions: somebody-knows events (CPI, Fed, awards, FDA, earnings); outcome-reflexive domains (equities, crypto, elections).

## 4. Committed parameters
- Channel mapping: q = clip(m_null + delta*call, .02, .98) with delta = 0.04
- Nulls: coin; longshot-corrected market (a,b frozen from prior season); conventional baseline (bot / GenCast-climatology blend)
- Gate: e-process, certify at E >= 20 (anytime-valid alpha <= 0.05), continuous monitoring
- Longshot correction (a, b): fit on prior resolved season only, then frozen
- QRNG: photonic source when available; os.urandom fallback logged as such
- ARV workflow: assignment sealed pre-session; judge sees transcript+unordered pair only; seals on assignment, transcript, call;
  feedback ON with pre-registered 20% ablation subset

## 5. Stopping & promotion
Monitoring is anytime-valid; stop or continue at will with guarantees intact.
Promotion requires out-of-sample certification; then quarter-Kelly capped stakes,
nobody-knows domains only, impact budget rho <= 0.01. Impact-decay experiment
begins only after promotion. Nothing stakes money in shadow mode.

## 6. Integrity
Seal-before-resolve on every prediction (SHA-256 ledger). Market/baseline probs are
decision-time snapshots. Any post-hoc edit, unsealed call, or leaked assignment
voids the affected trials. WHY-module outputs are audit-only and never route to calls.

## 7. Amendment policy
This document is frozen. Any parameter change produces v1.x with its own hash;
accumulated evidence restarts unless the change is analysis-orthogonal and logged
in the amendment appendix.

## 8. Code correspondence
Every parameter above exists in running code (substrate_engine v0.3): engine.py
(gate, ledger, fusion, scheduler), arv.py (blinded workflow), impact.py (decay
harness), wwwwwh.py (Echo experts), ingest.py (real-data schema). Calibration
numbers in README.md.

---
**Document file hash (SHA-256):** `70768c8fc1f81717ee9cc0609cd1f7f938faa2f50de03ebf9894388a05affdfd`
