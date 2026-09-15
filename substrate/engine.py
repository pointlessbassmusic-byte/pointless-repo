"""
SUBSTRATE ENGINE v0.1 — core machinery
Layers: baseline | channel | commitment | scoring | e-process gate | fusion | scheduler
All probabilities are P(YES). All predictions must be sealed (committed) before resolution.
"""
from __future__ import annotations
import hashlib, json, os, math, time
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional
import numpy as np

# ---------------------------------------------------------------- events

@dataclass
class Event:
    event_id: str
    domain: str            # 'sports' | 'weather' | 'kalshi_nk' | ...
    close_time: float      # decision deadline (epoch or sim-day)
    resolve_time: float
    market_prob: float     # raw market-implied P(YES) at decision time
    baseline_prob: float   # conventional model P(YES) (bot / climatology / GenCast blend)
    outcome: Optional[int] = None   # 0/1, None until resolved
    meta: dict = field(default_factory=dict)

# ---------------------------------------------------------------- baselines

def logit(p): p = np.clip(p, 1e-6, 1-1e-6); return np.log(p/(1-p))
def sigmoid(x): return 1/(1+np.exp(-x))

def longshot_correct(market_prob, a=0.0, b=0.85):
    """Favorite-longshot bias correction: shrink extremes via committed (a,b).
    b<1 pulls tails in; fit (a,b) on PAST resolved data only, freeze in protocol."""
    return float(sigmoid(a + b*logit(np.asarray(market_prob))))

def fit_longshot(resolved_events):
    """Fit (a,b) by logistic regression of outcome on logit(market_prob). Simple IRLS."""
    x = np.array([logit(e.market_prob) for e in resolved_events])
    y = np.array([e.outcome for e in resolved_events], dtype=float)
    a, b = 0.0, 1.0
    for _ in range(50):
        p = sigmoid(a + b*x); w = p*(1-p) + 1e-9
        g = np.array([np.sum(y-p), np.sum((y-p)*x)])
        H = np.array([[np.sum(w), np.sum(w*x)], [np.sum(w*x), np.sum(w*x*x)]])
        try: da, db = np.linalg.solve(H, g)
        except np.linalg.LinAlgError: break
        a += da; b += db
        if abs(da)+abs(db) < 1e-8: break
    return a, b

# ---------------------------------------------------------------- channel interface

class Channel:
    """Anything that emits a directional call on a sealed trial: ARV judge output,
    presentiment classifier, hardware readout. Must be causally blind to outcome."""
    name = "abstract"
    def call(self, event: Event, rng: np.random.Generator) -> int:
        raise NotImplementedError  # +1 => YES, -1 => NO

class SimulatedChannel(Channel):
    """Validation-only channel with injected effect size d (Cohen). d=0 -> pure noise.
    Two-alternative accuracy = Phi(d/sqrt(2))."""
    def __init__(self, d=0.0):
        self.d = d; self.name = f"sim_channel(d={d})"
        self.acc = 0.5 if d == 0 else float(0.5*(1+math.erf((d/math.sqrt(2))/math.sqrt(2))))
    def call(self, event, rng):
        truth = +1 if event.meta["_true_outcome"] == 1 else -1
        return truth if rng.random() < self.acc else -truth

class ManualChannel(Channel):
    """Real-use channel: reads sealed calls from a ledger keyed by event_id."""
    name = "manual"
    def __init__(self, calls: dict): self.calls = calls
    def call(self, event, rng): return int(self.calls[event.event_id])

def channel_prob(null_prob, call, delta=0.04):
    """Committed rule mapping a directional call onto the null prob."""
    return float(np.clip(null_prob + delta*call, 0.02, 0.98))

# ---------------------------------------------------------------- commitment (seal-before-resolve)

class CommitLedger:
    """SHA-256 commit-reveal. Protocol params hashed once; every prediction sealed
    pre-outcome. This artifact is simultaneously Seife pre-registration and the
    Newcomb policy commitment."""
    def __init__(self, protocol: dict, path=None):
        self.protocol = protocol
        self.protocol_hash = hashlib.sha256(
            json.dumps(protocol, sort_keys=True).encode()).hexdigest()
        self.records, self.path = [], path
    def seal(self, event_id, probs: dict, t=None):
        rec = {"event_id": event_id, "probs": probs, "t": t or time.time()}
        rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()
        self.records.append(rec); return rec["hash"]
    def dump(self):
        blob = {"protocol": self.protocol, "protocol_hash": self.protocol_hash,
                "records": self.records}
        if self.path: open(self.path, "w").write(json.dumps(blob, indent=1))
        return blob

# ---------------------------------------------------------------- scoring

def brier(p, y): return (p - y)**2
def logscore(p, y): p = min(max(p,1e-9),1-1e-9); return math.log(p if y==1 else 1-p)

class ScoreBook:
    def __init__(self, experts): self.experts = list(experts); self.rows = []
    def add(self, event, probs: dict):
        self.rows.append((event, {k: probs[k] for k in self.experts}))
    def table(self):
        out = {}
        for name in self.experts:
            bs = [brier(pr[name], e.outcome) for e, pr in self.rows]
            ls = [logscore(pr[name], e.outcome) for e, pr in self.rows]
            out[name] = {"n": len(bs), "brier": float(np.mean(bs)),
                         "log": float(np.mean(ls))}
        return out

# ---------------------------------------------------------------- e-process gate

class TestMartingale:
    """Anytime-valid evidence against the null forecaster. Per trial multiply by
    likelihood ratio  LR = q^y (1-q)^(1-y) / m^y (1-m)^(1-y)  — the wealth of a
    virtual Kelly bettor backing channel prob q against null prob m.
    E >= threshold certifies at alpha = 1/threshold, monitored continuously."""
    def __init__(self, threshold=20.0, fraction=1.0):
        self.logE, self.path, self.threshold, self.f = 0.0, [1.0], threshold, fraction
    def update(self, q, m, y):
        q = self.f*q + (1-self.f)*m       # fractional-Kelly shrink toward null
        lr = (q/m) if y==1 else ((1-q)/(1-m))
        self.logE += math.log(max(lr, 1e-12)); self.path.append(math.exp(self.logE))
        return self.path[-1]
    @property
    def E(self): return math.exp(self.logE)
    @property
    def certified(self): return max(self.path) >= self.threshold

# ---------------------------------------------------------------- fusion (Hedge)

class HedgeFusion:
    """Multiplicative-weights over experts on log-loss. Noise experts decay
    exponentially; skillful ones absorb weight. No faith required."""
    def __init__(self, experts, eta=None, horizon=1000):
        self.experts = list(experts)
        self.eta = eta or math.sqrt(8*math.log(len(self.experts))/max(horizon,1))
        self.logw = {e: 0.0 for e in self.experts}; self.history = []
    def weights(self):
        m = max(self.logw.values()); w = {e: math.exp(v-m) for e,v in self.logw.items()}
        s = sum(w.values()); return {e: v/s for e,v in w.items()}
    def predict(self, probs: dict):
        w = self.weights(); return float(sum(w[e]*probs[e] for e in self.experts))
    def update(self, probs: dict, y):
        for e in self.experts:
            self.logw[e] += self.eta * logscore(probs[e], y)
        self.history.append(self.weights())

# ---------------------------------------------------------------- QRNG scheduler

class Scheduler:
    """Selects which eligible events enter the trial and assigns target pairs.
    Entropy source: os.urandom by default; swap in photonic QRNG bytes via `feed`.
    Random *selection* kills cherry-picking; committed *filters* keep the regime clean."""
    def __init__(self, feed: Callable[[int], bytes] = os.urandom,
                 p_band=(0.40, 0.60), max_per_period=None):
        self.feed, self.p_band, self.cap = feed, p_band, max_per_period
    def _u(self):
        return int.from_bytes(self.feed(8), "big") / 2**64
    def eligible(self, e: Event, null_prob):
        return self.p_band[0] <= null_prob <= self.p_band[1]
    def select(self, events, null_probs, k=None):
        pool = [e for e, m in zip(events, null_probs) if self.eligible(e, m)]
        if k is None or k >= len(pool): chosen = pool
        else:
            idx = sorted(range(len(pool)), key=lambda i: self._u())[:k]
            chosen = [pool[i] for i in idx]
        for e in chosen:
            e.meta["target_pair"] = int.from_bytes(self.feed(4), "big")  # ARV image pair id
        return chosen
