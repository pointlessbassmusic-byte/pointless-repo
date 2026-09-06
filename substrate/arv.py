"""
ARV MODULE v0.1 — Associative Remote Viewing session machinery.
Double-blind by construction:
  1. open_trial: QRNG picks an orthogonal image pair and a hidden assignment
     (which image <-> YES). Assignment is SEALED (hash) before any human sees anything.
  2. record_transcript: viewer's description is sealed before judging.
  3. judge: blind judge sees ONLY (transcript, imageA, imageB) — never the event,
     never the assignment — and scores similarity. Call = argmax mapped through
     the sealed assignment inside the code path, invisible to both humans.
  4. resolve: outcome revealed; feedback shows the image mapped to the ACTUAL
     outcome (the precognitive-loop target). `feedback=False` runs the ablation.
Real use: humans supply transcript tags + judge scores. Validation: simulated
viewer/judge with injected effect size d.
"""
from __future__ import annotations
import os, math, hashlib, json, time
from dataclasses import dataclass, field
import numpy as np

TAGS = ["water","fire","mountain","building","person","animal","vehicle","tree",
        "circle","angular","dark","bright","motion","still","open","enclosed",
        "cold","hot","natural","artificial","above","below","rough","smooth"]

def _u64(feed): return int.from_bytes(feed(8), "big")

class ImageBank:
    """Synthetic stand-in: images are tag-sets. Real deployment maps ids to files."""
    def __init__(self, n=240, tags_per=5, seed=3):
        rng = np.random.default_rng(seed)
        self.images = {f"IMG{i:04d}": frozenset(rng.choice(TAGS, tags_per, replace=False))
                       for i in range(n)}
    def orthogonal_pair(self, feed=os.urandom, max_overlap=1, tries=800):
        ids = list(self.images)
        for _ in range(tries):
            a = ids[_u64(feed) % len(ids)]; b = ids[_u64(feed) % len(ids)]
            if a != b and len(self.images[a] & self.images[b]) <= max_overlap:
                return a, b
        raise RuntimeError("no orthogonal pair found")

@dataclass
class ARVTrial:
    event_id: str
    img_yes: str = ""; img_no: str = ""     # populated at open, sealed immediately
    assignment_hash: str = ""
    transcript: list = field(default_factory=list)
    transcript_hash: str = ""
    judge_scores: dict = field(default_factory=dict)
    call: int = 0                            # +1 YES / -1 NO (set by code, not humans)
    outcome: int | None = None
    feedback_img: str | None = None
    hit: bool | None = None
    t_open: float = 0.0; t_judged: float = 0.0; t_resolved: float = 0.0

class ARVProtocol:
    def __init__(self, bank: ImageBank, ledger, feed=os.urandom, feedback=True):
        self.bank, self.ledger, self.feed, self.feedback = bank, ledger, feed, feedback
        self.trials = {}
    def open_trial(self, event_id):
        a, b = self.bank.orthogonal_pair(self.feed)
        bit = _u64(self.feed) & 1
        img_yes, img_no = (a, b) if bit == 0 else (b, a)
        tr = ARVTrial(event_id, img_yes, img_no, t_open=time.time())
        payload = {"event_id": event_id, "img_yes": img_yes, "img_no": img_no}
        tr.assignment_hash = self.ledger.seal(event_id + ":assign", payload)
        self.trials[event_id] = tr
        return tr, (a, b)                    # humans receive the UNORDERED pair only
    def record_transcript(self, event_id, tags):
        tr = self.trials[event_id]; tr.transcript = sorted(tags)
        tr.transcript_hash = self.ledger.seal(event_id + ":transcript",
                                              {"tags": tr.transcript})
        return tr.transcript_hash
    def judge(self, event_id, judge_fn):
        """judge_fn(transcript_tags, tagsA, tagsB) -> (scoreA, scoreB); blind."""
        tr = self.trials[event_id]
        A, B = sorted([tr.img_yes, tr.img_no])          # order-scrambled for judge
        sA, sB = judge_fn(tr.transcript, self.bank.images[A], self.bank.images[B])
        tr.judge_scores = {A: float(sA), B: float(sB)}; tr.t_judged = time.time()
        best = A if sA >= sB else B
        tr.call = +1 if best == tr.img_yes else -1
        self.ledger.seal(event_id + ":call", {"call": tr.call,
                                              "scores": tr.judge_scores})
        return tr.call
    def resolve(self, event_id, outcome: int):
        tr = self.trials[event_id]; tr.outcome = int(outcome)
        tr.feedback_img = tr.img_yes if outcome == 1 else tr.img_no
        tr.hit = (tr.call == (+1 if outcome == 1 else -1))
        tr.t_resolved = time.time()
        if not self.feedback: tr.feedback_img = None     # ablation arm
        return tr

# ---------------- simulated humans (validation only) --------------------------
class SimulatedViewer:
    """Effect-size d viewer: with acc=Phi(d/sqrt2) the transcript leans toward the
    image that WILL be shown at feedback (i.e., the actual-outcome image)."""
    def __init__(self, d=0.0, n_tags=4):
        self.acc = 0.5 if d == 0 else 0.5*(1+math.erf((d/math.sqrt(2))/math.sqrt(2)))
        self.n = n_tags
    def transcript(self, bank, tr, true_outcome, rng):
        target = tr.img_yes if true_outcome == 1 else tr.img_no
        other = tr.img_no if true_outcome == 1 else tr.img_yes
        source = target if rng.random() < self.acc else other
        pool = list(bank.images[source])
        noise = [t for t in TAGS if t not in pool]
        k = min(self.n - 1, len(pool))
        tags = list(rng.choice(pool, k, replace=False)) + \
               list(rng.choice(noise, self.n - k, replace=False))
        return tags

def overlap_judge(transcript, tagsA, tagsB):
    t = set(transcript)
    return len(t & tagsA), len(t & tagsB)
