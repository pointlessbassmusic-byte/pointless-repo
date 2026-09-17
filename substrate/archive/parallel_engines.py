"""
PARALLEL ENGINES v0.1
=====================
Substrate Engine (A) + Echo Engine (B), running side by side.

HONEST CORE
-----------
We cannot tap a real informational substrate from software. What we CAN
build today is the complete machine that would detect one — and validate
it end-to-end against synthetic ground truth. So this harness runs two
worlds:

  NULL world : retro-capacity eps = 0.  The e-process must stay flat.
  LIVE world : a tiny retro-signal (eps > 0) is injected into the
               simulated photon array.  The e-process must climb —
               and the sham channel (wired to nothing) must NOT.

If the machinery passes both checks, it detects exactly what is there
and nothing more. Graduating to reality = swapping SimulatedPhotonArray
for HardwareCADSChannel (stub below) and preregistering.

ARCHITECTURE
------------
  Engine A (Substrate): simulated 16-channel photon array, duration
      codebook (4 equiprobable durations, CADS-style templates),
      matched-filter decoder, toy Kuramoto coherence gate, sham bank,
      anytime-valid e-process ledger, DAT timing tap with the
      force-vs-selection z(n) discriminator.
  Engine B (Echo): hidden-regime world simulator with forward
      precursors, online Naive-Bayes forecaster for WHO/WHAT/WHERE,
      split-conformal prediction sets, Brier ledger.
  Fusion: per-slot gating (A owns WHEN in proportion to measured
      capacity; B owns the rest), divergence ledger, hard firewall
      (B never sees any object produced by A).

Only dependency: numpy.
"""

import numpy as np

# ----------------------------------------------------------------------
# Shared constants
# ----------------------------------------------------------------------
K_CHANNELS   = 16                      # photon channels (AMELIA-inspired)
DUR_TEMPLATE = np.array([-1.5, -0.5, 0.5, 1.5])   # 4 equiprobable durations
N_TRIALS     = 800
THETA_GRID   = np.array([0.05, 0.1, 0.2, 0.4])    # e-process betting grid

# ----------------------------------------------------------------------
# Anytime-valid e-process (test martingale)
# ----------------------------------------------------------------------
class EProcess:
    """Wealth of a bettor against H0: 'statistic y ~ N(0,1), no signal'.

    Each trial: e = mean_theta exp(theta*y - theta^2/2)  (valid e-value:
    E[e | H0] = 1). Wealth multiplies. Ville's inequality makes the
    running wealth legitimate to monitor continuously — no alpha
    spending, no peeking penalty.
    """
    def __init__(self):
        self.log_wealth = 0.0
        self.history = [0.0]

    def update(self, y):
        e = np.mean(np.exp(THETA_GRID * y - THETA_GRID**2 / 2.0))
        self.log_wealth += np.log(e)
        self.history.append(self.log_wealth)
        return self.log_wealth

    @property
    def log10_wealth(self):
        return self.log_wealth / np.log(10)


# ----------------------------------------------------------------------
# ENGINE A — Substrate
# ----------------------------------------------------------------------
class SimulatedPhotonArray:
    """Stand-in for a CADS rig. Pre-decision window of K channels.

    NULL world: counts are pure Poisson noise, independent of the
    future duration (chosen AFTER the window is recorded).
    LIVE world: channel means are nudged by eps * template[duration] —
    the injected retro-correlation whose capacity we know exactly.
    """
    def __init__(self, eps, lam=100.0, rng=None, sham=False):
        self.eps, self.lam, self.sham = eps, lam, rng or np.random.default_rng()
        self.rng = rng
        self.is_sham = sham

    def pre_decision_window(self, future_duration_idx):
        z = self.rng.standard_normal(K_CHANNELS)        # standardized counts
        if not self.is_sham and self.eps > 0:
            z = z + self.eps * DUR_TEMPLATE[future_duration_idx]
        return z


class HardwareCADSChannel:
    """GRADUATION PATH (stub). Replace SimulatedPhotonArray with this.

    def pre_decision_window(self, _):
        raw = serial.Serial('/dev/ttyUSB0').read(...)   # photon counts
        return standardize(raw)                          # -> shape (K,)

    Then: the duration must come from a hardware TRNG that fires only
    AFTER this window closes, the analysis pipeline hash and this file
    go into the preregistration, and predictions are sealed
    (e.g. OpenTimestamps) before each TRNG decision.
    """
    pass


class SubstrateEngine:
    def __init__(self, rng):
        self.rng   = rng
        self.eproc = EProcess()          # main array ledger
        self.sham_eproc = EProcess()     # sham bank ledger
        self.eps_hat = 0.0               # running capacity estimate
        self._y_sum, self._n = 0.0, 0
        self.brier_sum, self.brier_n = 0.0, 0

    # ---- prediction phase (BEFORE the duration exists) ----
    def predict_when(self, z):
        """Posterior over 4 duration buckets from the pre-decision window."""
        A = np.sqrt(K_CHANNELS) * z.mean()
        mu = np.sqrt(K_CHANNELS) * self.eps_hat * DUR_TEMPLATE
        loglik = -(A - mu) ** 2 / 2.0
        p = np.exp(loglik - loglik.max())
        p = p / p.sum()
        coherence = abs(np.mean(np.sign(z)))   # toy Kuramoto order parameter
        return p, coherence, A

    # ---- resolution phase (AFTER the duration is revealed) ----
    def resolve(self, A, z_sham, p_pred, true_idx):
        sign = np.sign(DUR_TEMPLATE[true_idx])
        y = A * sign                                   # ~N(0,1) under H0
        self.eproc.update(y)
        A_sham = np.sqrt(K_CHANNELS) * z_sham.mean()
        self.sham_eproc.update(A_sham * sign)
        # running capacity estimate (MLE, clipped at 0)
        self._y_sum += y; self._n += 1
        mean_abs_m = np.mean(np.abs(DUR_TEMPLATE))
        self.eps_hat = max(0.0, (self._y_sum / self._n) /
                           (np.sqrt(K_CHANNELS) * mean_abs_m))
        # Brier for the WHEN prediction
        o = np.zeros(4); o[true_idx] = 1.0
        self.brier_sum += np.sum((p_pred - o) ** 2); self.brier_n += 1

    @property
    def brier(self):
        return self.brier_sum / max(1, self.brier_n)


class TimingTap:
    """DAT module. A fair Bernoulli(0.5) stream is NEVER perturbed.

    'selection' mode: the trigger previews 3 candidate entry points and
        picks the one whose next LOOKAHEAD bits are richest (biased
        entry into an unperturbed stream = May–Utts selection).
    'force' mode: entry is random but post-trigger bits are biased by
        delta (a perturbation model, for contrast).
    Discriminator: z as a function of scoring-window n.
        force     -> z grows ~ sqrt(n)
        selection -> z does NOT grow with n (excess is front-loaded)
    """
    LOOKAHEAD = 16

    def __init__(self, rng, mode, strength):
        self.rng, self.mode, self.strength = rng, mode, strength

    def run(self, n_triggers=200, windows=(16, 64, 256)):
        results = {}
        for n in windows:
            excess = 0.0
            for _ in range(n_triggers):
                stream = (self.rng.random(n) < 0.5).astype(float)
                if self.mode == "selection" and self.strength > 0:
                    cands = [(self.rng.random(n) < 0.5).astype(float)
                             for _ in range(3)]
                    stream = max(cands,
                                 key=lambda s: s[: self.LOOKAHEAD].sum())
                elif self.mode == "force" and self.strength > 0:
                    stream = (self.rng.random(n)
                              < 0.5 + self.strength).astype(float)
                excess += stream.sum() - n * 0.5
            se = 0.5 * np.sqrt(n * n_triggers)
            results[n] = excess / se
        return results


# ----------------------------------------------------------------------
# ENGINE B — Echo
# ----------------------------------------------------------------------
class EchoWorld:
    """Hidden-regime world. Regime h in {0,1,2} follows a sticky Markov
    chain and leaks a noisy forward PRECURSOR before each event.
    Events: WHO (6 agents), WHAT (3 categories), WHERE (4 zones),
    WHEN (duration bucket — pure TRNG, i.e. Echo-opaque by design).
    """
    WHO_GIVEN_H = np.array([[.4,.3,.1,.1,.05,.05],
                            [.05,.1,.4,.3,.1,.05],
                            [.05,.05,.1,.1,.3,.4]])
    WHAT_GIVEN_H = np.array([[.7,.2,.1],[.15,.7,.15],[.1,.2,.7]])
    ZONE_GIVEN_WHO = np.array([0,0,1,2,3,3])
    WHY_RULE = {0:"regime calm: routine maintenance cascade",
                1:"regime volatile: contagion via shared dependency",
                2:"regime stressed: resource contention spillover"}

    def __init__(self, rng):
        self.rng, self.h = rng, 0

    def step(self):
        if self.rng.random() < 0.15:
            self.h = self.rng.integers(0, 3)
        precursor = np.eye(3)[self.h] + self.rng.normal(0, 0.6, 3)
        who  = self.rng.choice(6, p=self.WHO_GIVEN_H[self.h])
        what = self.rng.choice(3, p=self.WHAT_GIVEN_H[self.h])
        where = self.ZONE_GIVEN_WHO[who]
        if self.rng.random() < 0.2:                     # geographic noise
            where = self.rng.integers(0, 4)
        when = self.rng.integers(0, 4)                  # TRNG — A's turf
        return precursor, dict(WHO=who, WHAT=what, WHERE=where,
                               WHEN=when, H=self.h)


class EchoEngine:
    """Online Naive-Bayes over the precursor + split-conformal sets.
    FIREWALL: this class must never receive any object from Engine A.
    """
    def __init__(self):
        self.counts = {s: np.ones((3, n)) for s, n in
                       [("WHO", 6), ("WHAT", 3), ("WHERE", 4)]}
        self.h_proto = np.eye(3)
        self.cal_scores = []
        self.brier = {s: [0.0, 0] for s in ("WHO", "WHAT", "WHERE", "WHEN")}

    def _p_h(self, precursor):
        d = ((precursor - self.h_proto) ** 2).sum(1)
        w = np.exp(-d / (2 * 0.6 ** 2))
        return w / w.sum()

    def predict(self, precursor):
        ph = self._p_h(precursor)
        out = {}
        for slot, table in self.counts.items():
            cond = table / table.sum(1, keepdims=True)
            out[slot] = ph @ cond
        out["WHEN"] = np.full(4, 0.25)      # Echo is honestly flat on WHEN
        out["WHY"]  = EchoWorld.WHY_RULE[int(np.argmax(ph))]
        out["HOW"]  = "path: precursor -> regime -> agent/zone exposure"
        return out, ph

    def conformal_set(self, p, alpha=0.10):
        if len(self.cal_scores) < 30:
            return list(range(len(p)))
        qhat = np.quantile(self.cal_scores, 1 - alpha)
        s = [c for c in range(len(p)) if 1 - p[c] <= qhat]
        return s or [int(np.argmax(p))]

    def resolve(self, pred, ph, truth):
        for slot in ("WHO", "WHAT", "WHERE", "WHEN"):
            p = pred[slot]; o = np.zeros(len(p)); o[truth[slot]] = 1
            self.brier[slot][0] += np.sum((p - o) ** 2)
            self.brier[slot][1] += 1
        self.cal_scores.append(1 - pred["WHO"][truth["WHO"]])
        for slot in ("WHO", "WHAT", "WHERE"):        # online learning
            self.counts[slot][truth["H"], truth[slot]] += 1

    def brier_of(self, slot):
        s, n = self.brier[slot]
        return s / max(1, n)


# ----------------------------------------------------------------------
# FUSION
# ----------------------------------------------------------------------
class Fusion:
    def __init__(self):
        self.divergences = 0

    def fuse_when(self, pA, pB, eps_hat):
        gate = min(1.0, 25.0 * max(0.0, eps_hat))   # capacity-earned trust
        fused = gate * pA + (1 - gate) * pB
        if np.abs(pA - pB).max() > 0.25:
            self.divergences += 1
        return fused, gate


# ----------------------------------------------------------------------
# HARNESS
# ----------------------------------------------------------------------
def run_world(label, eps, seed):
    rng   = np.random.default_rng(seed)
    array = SimulatedPhotonArray(eps, rng=rng)
    sham  = SimulatedPhotonArray(eps, rng=rng, sham=True)
    A, B  = SubstrateEngine(rng), EchoEngine()
    world, fusion = EchoWorld(rng), Fusion()
    fused_brier, traj = [0.0, 0], []

    for t in range(N_TRIALS):
        precursor, truth = world.step()
        z      = array.pre_decision_window(truth["WHEN"])   # BEFORE "TRNG"
        z_sham = sham.pre_decision_window(truth["WHEN"])
        pA, coher, Astat = A.predict_when(z)
        predB, ph        = B.predict(precursor)             # firewall: no A objects
        fused, gate      = fusion.fuse_when(pA, predB["WHEN"], A.eps_hat)
        # resolution
        A.resolve(Astat, z_sham, pA, truth["WHEN"])
        B.resolve(predB, ph, truth)
        o = np.zeros(4); o[truth["WHEN"]] = 1
        fused_brier[0] += np.sum((fused - o) ** 2); fused_brier[1] += 1
        if t % 25 == 0:
            traj.append((t, A.eproc.log_wealth / np.log(10),
                            A.sham_eproc.log_wealth / np.log(10)))

    tapS = TimingTap(rng, "selection", 0.5 if eps > 0 else 0.0).run()
    tapF = TimingTap(rng, "force",     0.02 if eps > 0 else 0.0).run()

    print(f"\n=== {label} (eps={eps}) ===")
    print(f"A  e-process   log10 wealth : {A.eproc.log10_wealth:8.2f}")
    print(f"A  sham bank   log10 wealth : {A.sham_eproc.log10_wealth:8.2f}")
    print(f"A  capacity estimate eps^   : {A.eps_hat:8.4f}  (true {eps})")
    print(f"WHEN Brier  A / B / fused   : {A.brier:.3f} / "
          f"{B.brier_of('WHEN'):.3f} / {fused_brier[0]/fused_brier[1]:.3f}"
          f"   (chance 0.750)")
    print(f"B   Brier  WHO/WHAT/WHERE   : {B.brier_of('WHO'):.3f} / "
          f"{B.brier_of('WHAT'):.3f} / {B.brier_of('WHERE'):.3f}")
    print(f"Fusion divergences logged   : {fusion.divergences}")
    print(f"Timing z(n)  selection      : "
          + "  ".join(f"n={n}:{z:+.2f}" for n, z in tapS.items()))
    print(f"Timing z(n)  force          : "
          + "  ".join(f"n={n}:{z:+.2f}" for n, z in tapF.items()))
    print("TRAJ " + label + " " +
          ";".join(f"{t}:{w:.2f}:{s:.2f}" for t, w, s in traj))
    return A, B


def demo_query(A, B, rng):
    """One WWWWWH answer sheet from the LIVE run's trained engines."""
    world = EchoWorld(rng)
    precursor, truth = world.step()
    z = SimulatedPhotonArray(0.06, rng=rng).pre_decision_window(truth["WHEN"])
    pA, coher, _ = A.predict_when(z)
    predB, _ = B.predict(precursor)
    fused, gate = Fusion().fuse_when(pA, predB["WHEN"], A.eps_hat)
    print("\n=== SAMPLE WWWWWH SHEET (live-trained engines) ===")
    print(f"WHO   [B]      : agent {int(np.argmax(predB['WHO']))} "
          f"p={predB['WHO'].max():.2f}  conformal set "
          f"{B.conformal_set(predB['WHO'])}")
    print(f"WHAT  [B]      : cat {int(np.argmax(predB['WHAT']))} "
          f"p={predB['WHAT'].max():.2f}")
    print(f"WHERE [B]      : zone {int(np.argmax(predB['WHERE']))} "
          f"p={predB['WHERE'].max():.2f}")
    print(f"WHEN  [A+B]    : bucket {int(np.argmax(fused))} "
          f"p={fused.max():.2f}  (gate on A = {gate:.2f}, "
          f"coherence r = {coher:.2f})")
    print(f"WHY   [B, inference-only] : {predB['WHY']}")
    print(f"HOW   [B, inference-only] : {predB['HOW']}")
    print(f"truth was: {truth}")


if __name__ == "__main__":
    run_world("NULL", eps=0.00, seed=11)
    A_live, B_live = run_world("LIVE", eps=0.06, seed=7)
    demo_query(A_live, B_live, np.random.default_rng(99))
