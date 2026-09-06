"""
LOOKBACK TRAP DEMO
==================
Why the Substrate Engine cannot be validated on historical data.

Setup: 365 days of PURE NOISE "pre-decision photon windows" plus random
historical outcome labels (4 duration buckets). Then we play the
motivated-but-honest analyst, who has ordinary freedoms:

    40 frequency bins x 3 window lengths x 4 transforms
    x 6 outcome groupings x 10 day-filters  =  28,800 analyses

Every single analysis is individually defensible. We report:
  1. the best in-sample sigma found by scanning (the trap),
  2. the same frozen pipeline applied to a fresh year (the truth),
  3. a preregistered single analysis chosen before looking (the discipline).

Only dependency: numpy.
"""

import numpy as np

TRIED = 0
DAYS, SAMPLES = 365, 256
FREQS      = range(1, 41)
WINDOWS    = (64, 128, 256)
TRANSFORMS = ("raw", "diff", "zscore", "cumsum")
PAIRINGS   = (((0, 1), (2, 3)), ((0, 2), (1, 3)), ((0, 3), (1, 2)))
SIGNS      = (1, -1)


def make_year(seed):
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((DAYS, SAMPLES))      # noise "photon windows"
    labels = rng.integers(0, 4, DAYS)             # historical outcomes
    return X, labels, rng


def transform(X, name):
    if name == "raw":
        return X
    if name == "diff":
        return np.diff(X, axis=1)
    if name == "zscore":
        return (X - X.mean(1, keepdims=True)) / X.std(1, keepdims=True)
    if name == "cumsum":
        return np.cumsum(X, axis=1)


def spectra(X):
    out = {}
    for tr in TRANSFORMS:
        T = transform(X, tr)
        for w in WINDOWS:
            seg = T[:, : min(w, T.shape[1])]
            out[(tr, w)] = np.abs(np.fft.rfft(seg, axis=1))[:, 1:41]
    return out


def masks_for(X, rng):
    idx = np.arange(DAYS)
    daymean = np.abs(X.mean(1))
    thr = np.quantile(daymean, 0.95)
    half = np.zeros(DAYS, bool)
    half[rng.permutation(DAYS)[: DAYS // 2]] = True
    return {
        "all": idx >= 0,          "even": idx % 2 == 0,
        "odd": idx % 2 == 1,      "no7th": idx % 7 != 0,
        "first300": idx < 300,    "last300": idx >= 65,
        "weekday": idx % 7 < 5,   "calm": daymean < thr,
        "lunar": idx % 29 < 15,   "randhalf": half,
    }


def z_of(mags, labels, mask, f, pair, sign):
    x = mags[mask, f - 1]
    y = np.where(np.isin(labels[mask], pair[0]), 1.0, -1.0) * sign
    xc, yc = x - x.mean(), y - y.mean()
    denom = np.sqrt((xc @ xc) * (yc @ yc))
    if denom == 0:
        return 0.0
    return (xc @ yc) / denom * np.sqrt(len(x))


def scan(spec, labels, masks):
    global TRIED
    best_z, best_cfg, tried, running = -1.0, None, 0, []
    for tr in TRANSFORMS:
        for w in WINDOWS:
            mags = spec[(tr, w)]
            for mname, mask in masks.items():
                for pair in PAIRINGS:
                    for sign in SIGNS:
                        for f in range(1, mags.shape[1] + 1):
                            z = abs(z_of(mags, labels, mask, f, pair, sign))
                            tried += 1
                            if z > best_z:
                                best_z, best_cfg = z, (tr, w, mname, pair,
                                                       sign, f)
                            if tried % 900 == 0:
                                running.append((tried, best_z))
    TRIED = tried
    return best_z, best_cfg, running


if __name__ == "__main__":
    # ---- Year 1: "last year" -------------------------------------------
    X1, lab1, rng1 = make_year(2025)
    spec1, masks1 = spectra(X1), masks_for(X1, np.random.default_rng(1))

    best_z, cfg, running = scan(spec1, lab1, masks1)
    tr, w, mname, pair, sign, f = cfg

    # preregistered single analysis (chosen before looking at data)
    z_prereg = abs(z_of(spec1[("raw", 256)], lab1, masks1["all"],
                        7, PAIRINGS[0], 1))

    # ---- Year 2: fresh data, SAME frozen pipeline ----------------------
    X2, lab2, rng2 = make_year(777)
    spec2, masks2 = spectra(X2), masks_for(X2, np.random.default_rng(2))
    z_oos = abs(z_of(spec2[(tr, w)], lab2, masks2[mname], f, pair, sign))

    print("=== LOOKBACK TRAP (all data are pure noise) ===")
    print(f"analyses scanned                 : {TRIED}")
    print(f"best IN-SAMPLE sigma             : {best_z:.2f}")
    print(f"  best config: transform={tr}, window={w}, filter={mname},")
    print(f"               grouping={pair}, sign={sign:+d}, freq bin={f}")
    print(f"same pipeline OUT-OF-SAMPLE      : {z_oos:.2f}")
    print(f"PREREGISTERED analysis, in-sample: {z_prereg:.2f}")
    print("TRAJ " + ";".join(f"{t}:{z:.2f}" for t, z in running))
