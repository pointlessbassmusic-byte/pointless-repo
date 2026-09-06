"""
WWWWWH CATEGORY MODELS v0.2
===========================
One dedicated, working, online-learning model per macro-category,
running against a richer simulated world. Same honest core as v0.1:
the only unreal ingredient is the injected substrate signal (eps),
so every model's skill is measurable against known ground truth.

Category -> model:
  WHO   [Echo]      graph-transition + regime mixture over 8 agents
  WHAT  [Echo]      regime-conditioned multinomial over 4 categories
  WHERE [Echo]      Wilson entropy/gravity model over 6 zones
                    (mass_z * exp(-beta * distance), masses + beta learned)
  WHEN  [Substrate] CADS duration decoder + capacity gate + e-ledger + sham
  WHY   [inference] causal-attribution classifier over 4 cause labels
  HOW   [inference] mechanism-path classifier over 3 pathways

The world emits ground-truth cause and pathway labels, so even WHY and
HOW are *scored*, while still being stamped inference-grade in output.
Chance Briers: WHO 0.875, WHAT 0.750, WHERE 0.833, WHEN 0.750,
WHY 0.750, HOW 0.667.

Only dependency: numpy.
"""

import numpy as np

N_AGENTS, N_CATS, N_ZONES, N_DUR = 8, 4, 6, 4
K_CHANNELS = 16
DUR_TEMPLATE = np.array([-1.5, -0.5, 0.5, 1.5])
THETA = np.array([0.05, 0.1, 0.2, 0.4])
CAUSES = ("maintenance", "contagion", "contention", "external")
PATHS = ("direct", "via_shared_dep", "via_neighbor")


# ----------------------------------------------------------------------
# World v2
# ----------------------------------------------------------------------
class World2:
    def __init__(self, rng, eps=0.0):
        self.rng, self.eps = rng, eps
        A = np.zeros((N_AGENTS, N_AGENTS))
        for i in range(N_AGENTS):
            A[i, (i + 1) % N_AGENTS] = A[i, (i - 1) % N_AGENTS] = 1
        A[0, 4] = A[4, 0] = 1
        A[2, 6] = A[6, 2] = 1
        self.adj = A
        self.home = rng.uniform(0, 10, (N_AGENTS, 2))   # public map data
        self.zones = rng.uniform(0, 10, (N_ZONES, 2))
        self.zmass = rng.uniform(0.5, 2.0, N_ZONES)
        self.h, self.prev_who = 0, 0

    def step(self):
        rng = self.rng
        if rng.random() < 0.12:
            self.h = int(rng.integers(0, 3))
        if rng.random() < 0.6:                      # WHO: network diffusion
            nbrs = np.where(self.adj[self.prev_who])[0]
            who = int(rng.choice(nbrs))
        else:                                       # or regime preference
            who = int((self.h * 3 + rng.integers(0, 3)) % N_AGENTS)
        pc = {0: [.6, .1, .1, .2], 1: [.1, .6, .1, .2],
              2: [.1, .2, .5, .2]}[self.h]
        cause = int(rng.choice(4, p=pc))
        pw = np.eye(4)[cause] * 0.55 + 0.45 / 4
        what = int(rng.choice(4, p=pw))
        d = np.linalg.norm(self.zones - self.home[who], axis=1)
        g = self.zmass * np.exp(-d / 3.0)
        where = int(rng.choice(N_ZONES, p=g / g.sum()))
        ph = {0: [.7, .2, .1], 1: [.2, .5, .3],
              2: [.3, .4, .3], 3: [.5, .3, .2]}[cause]
        how = int(rng.choice(3, p=ph))
        when = int(rng.integers(0, N_DUR))          # TRNG — substrate turf
        precursor = np.eye(3)[self.h] + rng.normal(0, 0.5, 3)
        truth = dict(WHO=who, WHAT=what, WHERE=where, WHEN=when,
                     WHY=cause, HOW=how, H=self.h, PREV=self.prev_who)
        self.prev_who = who
        return precursor, truth


# ----------------------------------------------------------------------
# Shared scoring mixin
# ----------------------------------------------------------------------
class Scored:
    def __init__(self, n):
        self.n_out, self.bsum, self.bn, self.hits = n, 0.0, 0, 0

    def score(self, p, true_idx):
        o = np.zeros(self.n_out); o[true_idx] = 1
        self.bsum += float(np.sum((p - o) ** 2)); self.bn += 1
        self.hits += int(np.argmax(p) == true_idx)

    @property
    def brier(self): return self.bsum / max(1, self.bn)
    @property
    def acc(self): return self.hits / max(1, self.bn)


def regime_belief(precursor):
    d = ((precursor - np.eye(3)) ** 2).sum(1)
    w = np.exp(-d / (2 * 0.5 ** 2))
    return w / w.sum()


# ----------------------------------------------------------------------
# Category models
# ----------------------------------------------------------------------
class WhoModel(Scored):
    """Graph transitions + regime mixture."""
    def __init__(self):
        super().__init__(N_AGENTS)
        self.T = np.ones((N_AGENTS, N_AGENTS))
        self.C = np.ones((3, N_AGENTS))

    def predict(self, prev_who, ph):
        t = self.T[prev_who] / self.T[prev_who].sum()
        c = ph @ (self.C / self.C.sum(1, keepdims=True))
        return 0.6 * t + 0.4 * c

    def learn(self, truth):
        self.T[truth["PREV"], truth["WHO"]] += 1
        self.C[truth["H"], truth["WHO"]] += 1


class WhatModel(Scored):
    def __init__(self):
        super().__init__(N_CATS)
        self.W = np.ones((3, N_CATS))

    def predict(self, ph):
        return ph @ (self.W / self.W.sum(1, keepdims=True))

    def learn(self, truth):
        self.W[truth["H"], truth["WHAT"]] += 1


class WhereModel(Scored):
    """Wilson gravity: P(z|who) ~ mass_z * exp(-beta * d(home, z))."""
    def __init__(self, world):
        super().__init__(N_ZONES)
        self.home, self.zones = world.home, world.zones
        self.mass = np.ones(N_ZONES)
        self.beta = 0.25
        self.log = []

    def _p(self, who, beta, mass):
        d = np.linalg.norm(self.zones - self.home[who], axis=1)
        g = mass * np.exp(-beta * d)
        return g / g.sum()

    def predict(self, p_who):
        return sum(p_who[a] * self._p(a, self.beta, self.mass)
                   for a in range(N_AGENTS))

    def learn(self, truth):
        self.mass[truth["WHERE"]] += 0.05
        self.log.append((truth["WHO"], truth["WHERE"]))
        if len(self.log) % 300 == 0:                 # coarse beta refit
            best, bll = self.beta, -1e18
            for b in (0.15, 0.25, 0.33, 0.5):
                ll = sum(np.log(self._p(w, b, self.mass)[z] + 1e-12)
                         for w, z in self.log[-300:])
                if ll > bll: bll, best = ll, b
            self.beta = best


class WhenModel(Scored):
    """Substrate side: CADS decoder + e-ledger + sham + capacity gate."""
    def __init__(self, rng, eps):
        super().__init__(N_DUR)
        self.rng, self.eps = rng, eps
        self.logw = self.logw_sham = 0.0
        self.ysum, self.n, self.eps_hat = 0.0, 0, 0.0

    def window(self, dur, sham=False):
        z = self.rng.standard_normal(K_CHANNELS)
        if not sham and self.eps > 0:
            z = z + self.eps * DUR_TEMPLATE[dur]
        return z

    def predict(self, z):
        A = np.sqrt(K_CHANNELS) * z.mean()
        mu = np.sqrt(K_CHANNELS) * self.eps_hat * DUR_TEMPLATE
        ll = -(A - mu) ** 2 / 2
        p = np.exp(ll - ll.max()); p /= p.sum()
        return p, A

    def resolve(self, A, A_sham, true_dur):
        s = np.sign(DUR_TEMPLATE[true_dur])
        for attr, y in (("logw", A * s), ("logw_sham", A_sham * s)):
            e = np.mean(np.exp(THETA * y - THETA ** 2 / 2))
            setattr(self, attr, getattr(self, attr) + np.log(e))
        self.ysum += A * s; self.n += 1
        self.eps_hat = max(0.0, (self.ysum / self.n) /
                           (np.sqrt(K_CHANNELS) * np.mean(np.abs(DUR_TEMPLATE))))

    @property
    def gate(self):
        if self.logw < np.log(20):        # anytime-valid p<0.05 gate
            return 0.0
        return min(1.0, 25 * self.eps_hat)


class WhyModel(Scored):
    def __init__(self):
        super().__init__(4)
        self.PC = np.ones((3, 4))

    def predict(self, ph):
        return ph @ (self.PC / self.PC.sum(1, keepdims=True))

    def learn(self, truth):
        self.PC[truth["H"], truth["WHY"]] += 1


class HowModel(Scored):
    def __init__(self):
        super().__init__(3)
        self.PH = np.ones((4, 3))

    def predict(self, p_cause):
        return p_cause @ (self.PH / self.PH.sum(1, keepdims=True))

    def learn(self, truth):
        self.PH[truth["WHY"], truth["HOW"]] += 1


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------
def run(label, eps, seed, steps=1200):
    rng = np.random.default_rng(seed)
    world = World2(rng, eps)
    who, what = WhoModel(), WhatModel()
    where, when = WhereModel(world), WhenModel(rng, eps)
    why, how = WhyModel(), HowModel()
    fused_when = Scored(N_DUR)

    for t in range(steps):
        precursor, truth = world.step()
        ph = regime_belief(precursor)
        pWHO = who.predict(truth["PREV"], ph)
        pWHAT = what.predict(ph)
        pWHERE = where.predict(pWHO)
        z, z_sham = when.window(truth["WHEN"]), when.window(truth["WHEN"], True)
        pWHEN, A = when.predict(z)
        _, A_sham = when.predict(z_sham)
        pWHY = why.predict(ph)
        pHOW = how.predict(pWHY)
        g = when.gate
        pF = g * pWHEN + (1 - g) * np.full(N_DUR, 0.25)
        # score
        who.score(pWHO, truth["WHO"]); what.score(pWHAT, truth["WHAT"])
        where.score(pWHERE, truth["WHERE"]); when.score(pWHEN, truth["WHEN"])
        why.score(pWHY, truth["WHY"]); how.score(pHOW, truth["HOW"])
        fused_when.score(pF, truth["WHEN"])
        # learn / resolve
        for m in (who, what, where, why, how): m.learn(truth)
        when.resolve(A, A_sham, truth["WHEN"])

    chance = dict(WHO=0.875, WHAT=0.750, WHERE=0.833,
                  WHEN=0.750, WHY=0.750, HOW=0.667)
    print(f"\n=== {label} (eps={eps}, steps={steps}) ===")
    print(f"{'slot':6} {'brier':>7} {'chance':>7} {'top1':>6}")
    for name, m in (("WHO", who), ("WHAT", what), ("WHERE", where),
                    ("WHEN", when), ("WHY", why), ("HOW", how)):
        print(f"{name:6} {m.brier:7.3f} {chance[name]:7.3f} {m.acc:6.2%}")
    print(f"{'WHEN*':6} {fused_when.brier:7.3f} {chance['WHEN']:7.3f}"
          f"   (fused, gate={when.gate:.2f})")
    print(f"e-ledger log10: main {when.logw/np.log(10):+.2f}   "
          f"sham {when.logw_sham/np.log(10):+.2f}   "
          f"eps^ {when.eps_hat:.4f} (true {eps})")
    return dict(who=who, what=what, where=where, when=when,
                why=why, how=how, world=world)


def sample_sheet(m, rng):
    precursor, truth = m["world"].step()
    ph = regime_belief(precursor)
    pWHO = m["who"].predict(truth["PREV"], ph)
    pWHAT = m["what"].predict(ph)
    pWHERE = m["where"].predict(pWHO)
    z = m["when"].window(truth["WHEN"])
    pWHEN, _ = m["when"].predict(z)
    pWHY = m["why"].predict(ph)
    pHOW = m["how"].predict(pWHY)
    g = m["when"].gate
    print("\n=== SAMPLE WWWWWH SHEET (live-trained) ===")
    print(f"WHO   [Echo]  agent {int(np.argmax(pWHO))}  p={pWHO.max():.2f}")
    print(f"WHAT  [Echo]  cat {int(np.argmax(pWHAT))}   p={pWHAT.max():.2f}")
    print(f"WHERE [Echo]  zone {int(np.argmax(pWHERE))} p={pWHERE.max():.2f}")
    print(f"WHEN  [Substrate, gate={g:.2f}] bucket {int(np.argmax(pWHEN))}"
          f" p={pWHEN.max():.2f}")
    print(f"WHY   [inference] {CAUSES[int(np.argmax(pWHY))]} "
          f"p={pWHY.max():.2f}")
    print(f"HOW   [inference] {PATHS[int(np.argmax(pHOW))]} "
          f"p={pHOW.max():.2f}")
    print(f"truth: WHO={truth['WHO']} WHAT={truth['WHAT']} "
          f"WHERE={truth['WHERE']} WHEN={truth['WHEN']} "
          f"WHY={CAUSES[truth['WHY']]} HOW={PATHS[truth['HOW']]}")


if __name__ == "__main__":
    run("NULL", eps=0.00, seed=41)
    live = run("LIVE", eps=0.06, seed=42)
    sample_sheet(live, np.random.default_rng(9))
