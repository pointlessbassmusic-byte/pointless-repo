"""
SUBSTRATE ENGINE v0.1 — synthetic-year validation harness.
Simulates a season of sports + a year of weather-station markets, injects a channel
with effect size d, and runs the full pipeline: schedule -> seal -> resolve -> score
-> e-process -> fusion. Real data drops into the same Event schema via CSV.
"""
from __future__ import annotations
import math, json
import numpy as np
from engine import (Event, longshot_correct, fit_longshot, SimulatedChannel,
                    channel_prob, CommitLedger, ScoreBook, TestMartingale,
                    HedgeFusion, Scheduler, sigmoid, logit)

# ---------------------------------------------------------------- world generators

def gen_sports_year(rng, n_games=3200):
    """Bot-domain slate. True win prob per game; market shows favorite-longshot bias
    + noise; baseline (the bot) is decent but imperfect."""
    events = []
    for i in range(n_games):
        p_true = float(np.clip(rng.beta(5, 5), 0.05, 0.95))
        m = float(np.clip(sigmoid(0.0 + 1.25*logit(p_true) + rng.normal(0, .18)), .02, .98))
        b = float(np.clip(sigmoid(0.0 + 1.00*logit(p_true) + rng.normal(0, .22)), .02, .98))
        y = int(rng.random() < p_true)
        events.append(Event(f"S{i:05d}", "sports", i, i+1, m, b,
                            meta={"_true_outcome": y, "p_true": p_true}))
    return events

def gen_weather_year(rng, n_stations=5, days=365, lead=14):
    """Daily exceedance markets at 14-day lead. Temps: seasonal + AR(1) anomaly.
    At 14 days the anomaly is unforecastable -> honest baseline is climatology."""
    events = []
    phi, sig = 0.75, 2.6
    for s in range(n_stations):
        anom, thresh_bias = 0.0, rng.normal(0, .4)
        for d in range(days):
            seasonal = 10*math.sin(2*math.pi*(d-100)/365)
            anom = phi*anom + rng.normal(0, sig)
            temp = 18 + seasonal + anom
            thresh = 18 + seasonal + thresh_bias          # near-coinflip strikes
            sd14 = sig/math.sqrt(1-phi**2)                # stationary anomaly sd
            p_clim = 1 - 0.5*(1+math.erf((thresh-(18+seasonal))/(sd14*math.sqrt(2))))
            b = float(np.clip(p_clim + rng.normal(0, .02), .02, .98))
            m = float(np.clip(sigmoid(0.1 + 1.3*logit(b) + rng.normal(0, .15)), .02, .98))
            y = int(temp > thresh)
            events.append(Event(f"W{s}{d:03d}", "weather", d-lead, d, m, b,
                                meta={"_true_outcome": y}))
    return events

# ---------------------------------------------------------------- pipeline run

def run_world(seed, d_channel, n_select_weather=600, threshold=20.0):
    rng = np.random.default_rng(seed)
    sports = gen_sports_year(rng); weather = gen_weather_year(rng)

    # --- calibration epoch: fit longshot correction on PRIOR resolved season only
    prior = gen_sports_year(np.random.default_rng(seed+999))
    for e in prior: e.outcome = e.meta["_true_outcome"]
    a, b_ = fit_longshot(prior)

    protocol = {"p_band": [0.40, 0.60], "delta": 0.04, "threshold": threshold,
                "longshot_ab": [round(a,4), round(b_,4)], "kelly_fraction": 1.0,
                "arms": ["sports", "weather"], "channel_d_declared": "unknown"}
    ledger = CommitLedger(protocol)
    chan = SimulatedChannel(d_channel)
    out = {}

    for arm, events in (("sports", sports), ("weather", weather)):
        sched = Scheduler(p_band=tuple(protocol["p_band"]))
        nulls = [longshot_correct(e.market_prob, a, b_) for e in events]
        chosen = sched.select(events, nulls,
                              k=n_select_weather if arm=="weather" else None)
        mart = TestMartingale(threshold=threshold)
        fusion = HedgeFusion(["market", "baseline", "channel"], horizon=len(chosen))
        book = ScoreBook(["market", "baseline", "channel", "fusion"])
        for e in chosen:
            m_null = longshot_correct(e.market_prob, a, b_)
            call = chan.call(e, rng)
            q = channel_prob(m_null, call, protocol["delta"])
            probs = {"market": m_null, "baseline": e.baseline_prob, "channel": q}
            probs["fusion"] = fusion.predict(probs)
            ledger.seal(e.event_id, probs)
            e.outcome = e.meta["_true_outcome"]          # resolution
            mart.update(q, m_null, e.outcome)
            fusion.update({k: probs[k] for k in fusion.experts}, e.outcome)
            book.add(e, probs)
        out[arm] = {"mart": mart, "fusion": fusion, "book": book, "n": len(chosen)}
    out["ledger"] = ledger
    return out

# ---------------------------------------------------------------- power study

def power_study(d, reps=120, threshold=20.0, seed0=7000):
    """Detection rate + false-positive rate of the gate at effect size d."""
    hits = {"sports": 0, "weather": 0}; trials_to_cert = {"sports": [], "weather": []}
    for r in range(reps):
        w = run_world(seed0+r, d, threshold=threshold)
        for arm in ("sports", "weather"):
            m = w[arm]["mart"]
            if m.certified:
                hits[arm] += 1
                path = np.array(m.path)
                trials_to_cert[arm].append(int(np.argmax(path >= threshold)))
    return {arm: {"rate": hits[arm]/reps,
                  "median_trials": (int(np.median(trials_to_cert[arm]))
                                    if trials_to_cert[arm] else None)}
            for arm in hits}

if __name__ == "__main__":
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    null_w  = run_world(11, d_channel=0.0)
    eff_w   = run_world(11, d_channel=0.2)

    # ---- chart 1: e-process wealth, both worlds, both arms
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, arm in zip(axes, ("sports", "weather")):
        ax.plot(null_w[arm]["mart"].path, lw=1.6, label="null world (d=0)")
        ax.plot(eff_w[arm]["mart"].path, lw=1.6, label="substrate world (d=0.2)")
        ax.axhline(20, ls="--", c="k", lw=1, label="certification E=20")
        ax.set_yscale("log"); ax.set_title(f"{arm} arm — e-process wealth")
        ax.set_xlabel("sealed trials"); ax.grid(alpha=.3)
    axes[0].set_ylabel("evidence E (log)"); axes[0].legend(fontsize=8)
    fig.tight_layout(); fig.savefig("eprocess.png", dpi=140)

    # ---- chart 2: fusion weights in the effect world (sports arm)
    hist = eff_w["sports"]["fusion"].history
    fig2, ax = plt.subplots(figsize=(7.5, 4))
    for name in ("market", "baseline", "channel"):
        ax.plot([h[name] for h in hist], lw=1.6, label=name)
    ax.set_title("Hedge fusion weights — substrate world, sports arm")
    ax.set_xlabel("sealed trials"); ax.set_ylabel("weight"); ax.grid(alpha=.3); ax.legend()
    fig2.tight_layout(); fig2.savefig("fusion_weights.png", dpi=140)

    # ---- score tables + power
    report = {"null_world": {a: null_w[a]["book"].table() | {"E_final": null_w[a]["mart"].E}
                             for a in ("sports","weather")},
              "effect_world": {a: eff_w[a]["book"].table() | {"E_final": eff_w[a]["mart"].E}
                               for a in ("sports","weather")},
              "protocol_hash": eff_w["ledger"].protocol_hash}
    print(json.dumps(report, indent=1, default=float))

    print("\nPOWER STUDY (reps=120)")
    print(" d=0.0 :", power_study(0.0, reps=120))
    print(" d=0.2 :", power_study(0.2, reps=60))
