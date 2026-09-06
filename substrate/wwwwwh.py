"""
ECHO ENGINE v0.1 — working models per WWWWWH category.
Echo decomposes any future-question into six primitives; each primitive has a
dedicated model emitting calibrated probabilities. Every output is Substrate-
compatible: it can be sealed, scored against nulls, gated by the e-process,
and entered as an expert in Hedge fusion.

WHO   -> BradleyTerryWho   : online paired-comparison ratings over candidates
WHAT  -> DirichletWhat     : outcome-type distribution with conjugate updates
WHEN  -> WeibullWhen       : hazard/survival timing model (DAT timing layer host)
WHERE -> KDEWhere          : 2D kernel density over historical event locations
WHY   -> AttributionWhy    : logistic attribution (explanatory, NOT predictive —
                             feeds the Seife confound audit, never the bet book)
HOW   -> MarkovHow         : pathway model over event-sequence tokens
"""
from __future__ import annotations
import math
from collections import defaultdict
import numpy as np

def _sigmoid(x): return 1/(1+np.exp(-x))

# ------------------------------------------------------------------ WHO
class BradleyTerryWho:
    """Online Elo/Bradley-Terry. predict(a,b) = P(a beats b); dist(cands) softmax."""
    def __init__(self, k=24.0, scale=400.0):
        self.r = defaultdict(lambda: 1500.0); self.k, self.s = k, scale
    def predict(self, a, b):
        return 1/(1+10**((self.r[b]-self.r[a])/self.s))
    def dist(self, cands, temp=None):
        t = temp or self.s
        z = np.array([self.r[c] for c in cands])/t
        w = np.exp(z - z.max()); return dict(zip(cands, w/w.sum()))
    def update(self, a, b, y):                      # y=1 if a won
        p = self.predict(a, b)
        self.r[a] += self.k*(y-p); self.r[b] -= self.k*(y-p)

# ------------------------------------------------------------------ WHAT
class DirichletWhat:
    """Outcome-type distribution. Conjugate: alpha counts; predictive = mean."""
    def __init__(self, categories, alpha0=1.0):
        self.alpha = {c: alpha0 for c in categories}
    def predict(self):
        s = sum(self.alpha.values()); return {c: a/s for c, a in self.alpha.items()}
    def update(self, category, w=1.0): self.alpha[category] += w

# ------------------------------------------------------------------ WHEN
class WeibullWhen:
    """Timing model. Fits Weibull(k, lam) to observed waits (right-censoring ok).
    P(event in [t0,t1] | survived to t0) drives DAT-style act-now/act-later calls."""
    def __init__(self): self.k, self.lam = 1.0, 1.0
    @staticmethod
    def _nll(k, lam, t, c):
        z = (t/lam)**k
        ll = np.sum(c*(np.log(k/lam) + (k-1)*np.log(t/lam)) - z)
        return -ll
    def fit(self, times, censored=None):
        t = np.asarray(times, float); c = np.ones_like(t) if censored is None \
            else 1-np.asarray(censored, float)     # c=1 observed, 0 censored
        best = (math.inf, 1.0, float(t.mean()))
        for k in np.linspace(0.3, 5.0, 120):
            lam = (np.sum(t**k)/max(c.sum(),1e-9))**(1/k)   # MLE lam given k
            v = self._nll(k, lam, t, c)
            if v < best[0]: best = (v, float(k), float(lam))
        _, self.k, self.lam = best; return self
    def survival(self, t): return math.exp(-((t/self.lam)**self.k))
    def window_prob(self, t0, t1):
        s0 = self.survival(t0)
        return 0.0 if s0 <= 0 else (s0 - self.survival(t1))/s0
    def hazard(self, t): return (self.k/self.lam)*((t/self.lam)**(self.k-1))

# ------------------------------------------------------------------ WHERE
class KDEWhere:
    """2D Gaussian KDE over historical points. region_prob via Monte Carlo."""
    def __init__(self, bw=None): self.pts, self.bw = None, bw
    def fit(self, pts):
        self.pts = np.asarray(pts, float)
        n = len(self.pts)
        self.bw = self.bw or float(n**(-1/6) * self.pts.std(axis=0).mean())  # Scott-ish
        return self
    def density(self, xy):
        d = self.pts[None,:,:] - np.asarray(xy,float)[:,None,:]
        k = np.exp(-(d**2).sum(-1)/(2*self.bw**2))/(2*math.pi*self.bw**2)
        return k.mean(1)
    def sample(self, n, rng):
        idx = rng.integers(0, len(self.pts), n)
        return self.pts[idx] + rng.normal(0, self.bw, (n,2))
    def region_prob(self, center, radius, n=20000, rng=None):
        rng = rng or np.random.default_rng(0)
        s = self.sample(n, rng)
        return float(((s-np.asarray(center))**2).sum(1) <= radius**2).__float__() \
               if n==1 else float((((s-np.asarray(center))**2).sum(1) <= radius**2).mean())

# ------------------------------------------------------------------ WHY
class AttributionWhy:
    """Logistic attribution over features. EXPLANATORY output only: ranks drivers
    by leave-one-out log-loss delta. Feeds the confound audit (Seife layer);
    protocol forbids routing WHY outputs to the bet book."""
    def __init__(self, names): self.names, self.w, self.b = list(names), None, 0.0
    def _fit(self, X, y, iters=300, lr=0.1):
        w = np.zeros(X.shape[1]); b = 0.0
        for _ in range(iters):
            p = _sigmoid(X@w + b); g = p - y
            w -= lr*(X.T@g/len(y) + 1e-3*w); b -= lr*g.mean()
        return w, b
    def fit(self, X, y):
        X, y = np.asarray(X,float), np.asarray(y,float)
        self.X, self.y = X, y
        self.w, self.b = self._fit(X, y); return self
    def _ll(self, X, y, w, b):
        p = np.clip(_sigmoid(X@w+b), 1e-9, 1-1e-9)
        return float(np.mean(y*np.log(p)+(1-y)*np.log(1-p)))
    def attribution(self):
        base = self._ll(self.X, self.y, self.w, self.b); out = {}
        for j, name in enumerate(self.names):
            Xr = np.delete(self.X, j, axis=1)
            wr, br = self._fit(Xr, self.y)
            out[name] = base - self._ll(Xr, self.y, wr, br)   # info the feature adds
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

# ------------------------------------------------------------------ HOW
class MarkovHow:
    """First-order Markov pathway model over event tokens; Laplace-smoothed."""
    def __init__(self, alpha=0.5):
        self.n = defaultdict(lambda: defaultdict(float)); self.alpha = alpha
        self.vocab = set()
    def update(self, seq):
        for a, b in zip(seq, seq[1:]):
            self.n[a][b] += 1; self.vocab |= {a, b}
    def next_dist(self, state):
        V = len(self.vocab) or 1
        row = self.n[state]; tot = sum(row.values()) + self.alpha*V
        return {s: (row.get(s,0)+self.alpha)/tot for s in self.vocab}
    def path_prob(self, seq):
        p = 1.0
        for a, b in zip(seq, seq[1:]): p *= self.next_dist(a)[b]
        return p
