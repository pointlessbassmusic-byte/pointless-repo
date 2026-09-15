"""
IMPACT-DECAY HARNESS v0.1 — the consistency layer's falsifiable signature, as code.
Claim under test: on OUTCOME-EXOGENOUS events (weather), channel accuracy responds
to the notional stake ρ = stake/liquidity even though the stake cannot move the
outcome — 'paradox pressure'. Worlds:
  null                 acc(ρ) = 0.5                       (no channel)
  channel_flat         acc(ρ) = acc0                      (channel, no consistency physics)
  channel_consistency  acc(ρ) = 0.5 + (acc0-.5)e^{-γρ}    (channel + paradox pressure)
Harness measures hit-rate vs ρ with Wilson CIs and a permutation trend test.
Exogeneity kills the mechanical self-fulfillment confound by construction.
"""
from __future__ import annotations
import math
import numpy as np

def wilson(h, n, z=1.96):
    if n == 0: return (0,0,0)
    p = h/n; d = 1+z*z/n
    c = (p + z*z/(2*n))/d; hw = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/d
    return p, c-hw, c+hw

def acc_fn(world, rho, acc0=0.556, gamma=3.0):
    if world == "null": return 0.5
    if world == "channel_flat": return acc0
    return 0.5 + (acc0-0.5)*math.exp(-gamma*rho)

def run_sweep(world, rhos, n_per=900, seed=0, **kw):
    rng = np.random.default_rng(seed)
    rows = []
    flat_r, flat_h = [], []
    for rho in rhos:
        a = acc_fn(world, rho, **kw)
        hits = rng.random(n_per) < a
        rows.append((rho,)+wilson(int(hits.sum()), n_per))
        flat_r += [rho]*n_per; flat_h += hits.astype(int).tolist()
    return rows, np.array(flat_r), np.array(flat_h)

def trend_test(rho, hit, reps=3000, seed=1):
    """Permutation test on corr(rho, hit); one-sided (decay => negative)."""
    rng = np.random.default_rng(seed)
    rho_c = rho - rho.mean(); hit_c = hit - hit.mean()
    stat = float((rho_c*hit_c).mean()/(rho.std()*hit.std()+1e-12))
    null = []
    h = hit.copy()
    for _ in range(reps):
        rng.shuffle(h)
        null.append(float((rho_c*(h-h.mean())).mean()/(rho.std()*h.std()+1e-12)))
    p = (1+sum(1 for s in null if s <= stat))/(1+reps)
    return stat, p

if __name__ == "__main__":
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from engine import CommitLedger, TestMartingale, channel_prob
    from arv import ImageBank, ARVProtocol, SimulatedViewer, overlap_judge

    rng = np.random.default_rng(9)
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(12, 4.4))

    # ---------- panel 1: full ARV loop through the gate -----------------------
    for d, color in ((0.0, "#888888"), (0.2, "#4477aa")):
        bank = ImageBank()
        ledger = CommitLedger({"module": "arv", "d_sim": d, "delta": 0.04,
                               "feedback": True})
        proto = ARVProtocol(bank, ledger)
        viewer = SimulatedViewer(d)
        mart = TestMartingale(threshold=20)
        hits = 0
        N = 1600
        for i in range(N):
            eid = f"E{d}-{i:05d}"
            y = int(rng.random() < 0.5)                      # coin-flip market
            tr, _pair = proto.open_trial(eid)
            proto.record_transcript(eid, viewer.transcript(bank, tr, y, rng))
            call = proto.judge(eid, overlap_judge)
            m_null = 0.5
            q = channel_prob(m_null, call, 0.04)
            mart.update(q, m_null, y)
            tr = proto.resolve(eid, y); hits += int(tr.hit)
        axL.plot(mart.path, lw=1.6, color=color,
                 label=f"d={d}: hits={hits/N:.3f}, E={mart.E:.1f}"
                       + (" CERT" if mart.certified else ""))
    axL.axhline(20, ls="--", c="k", lw=1)
    axL.set_yscale("log"); axL.set_title("ARV loop (double-blind, sealed) through gate")
    axL.set_xlabel("sealed ARV trials"); axL.set_ylabel("evidence E (log)")
    axL.legend(fontsize=8); axL.grid(alpha=.3)

    # ---------- panel 2: impact-decay sweep, three worlds ---------------------
    rhos = [0.003, 0.01, 0.03, 0.1, 0.3, 1.0]
    styles = {"null": "#888888", "channel_flat": "#228833", "channel_consistency": "#cc3311"}
    print("IMPACT-DECAY TREND TESTS (negative stat + small p => decay detected)")
    for world, c in styles.items():
        rows, r_all, h_all = run_sweep(world, rhos, seed=hash(world) % 2**31)
        xs = [r[0] for r in rows]; ps = [r[1] for r in rows]
        lo = [r[1]-r[2] for r in rows]; hi = [r[3]-r[1] for r in rows]
        axR.errorbar(xs, ps, yerr=[lo, hi], fmt="o-", ms=4, lw=1.4, color=c,
                     label=world, capsize=2)
        stat, p = trend_test(r_all, h_all)
        print(f"  {world:22s} corr={stat:+.4f}  p={p:.4f}")
    axR.set_xscale("log"); axR.axhline(0.5, ls=":", c="k", lw=.8)
    axR.set_title("Impact-decay harness: accuracy vs stake ratio ρ")
    axR.set_xlabel("ρ = stake/liquidity (log)"); axR.set_ylabel("channel hit rate")
    axR.legend(fontsize=8); axR.grid(alpha=.3)

    fig.tight_layout(); fig.savefig("arv_impact.png", dpi=140)
    print("saved arv_impact.png")
