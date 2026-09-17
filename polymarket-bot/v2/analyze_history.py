#!/usr/bin/env python3
"""Analyze downloaded Polymarket sports history and emit tuned parameters.

Reads data/markets.csv + data/prices/*.csv (from history_downloader.py) and
produces:
    analysis_workbook.xlsx   ReadMe, Markets, Calibration, ShockReversion,
                             Momentum, Correlations, Regressions
    strategy_params.json     per-sport overrides the bot loads at startup

What each study answers:
  Calibration     — are prices honest probabilities, or is there favorite/
                    longshot bias worth leaning on? (logistic fit on logit(p))
  ShockReversion  — after a sharp k-minute move, does price mean-revert over
                    the next 1/3/5/10 min, and by how much? (drives the fade)
  Momentum        — do returns autocorrelate positively? (historically NO on
                    this venue per prior backtests; re-verified here)
  Regressions     — OLS of forward return on shock size, sport dummies,
                    and minutes-to-close; t-stats included
Costs note: the history endpoint gives mid/last only — you cannot measure
spread or queue position from it. Paper-trade fill data remains the final
arbiter before any live capital.

Usage:  python analyze_history.py [--data data] [--shock-window 5]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

SPORTS = ("tennis", "table_tennis", "mlb")
HORIZONS = (1, 3, 5, 10)          # minutes ahead
BUCKETS = np.arange(0.0, 1.05, 0.05)


def load(data_dir: Path) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    markets = pd.read_csv(data_dir / "markets.csv", dtype={"token_id": str})
    series: dict[str, pd.DataFrame] = {}
    pdir = data_dir / "prices"
    for _, row in markets.iterrows():
        f = pdir / f"{row.token_id}.csv"
        if not f.exists():
            continue
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if len(df) < 30:
            continue
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        df = df.set_index("ts").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df["price"] = df["price"].clip(0.001, 0.999)
        series[row.token_id] = df
    print(f"Loaded {len(series)} price series / {len(markets)} tokens")
    return markets, series


def calibration_study(markets: pd.DataFrame, series: dict) -> pd.DataFrame:
    """Implied vs realized win rate by price bucket, sampled hourly pre-close."""
    rows = []
    for _, m in markets.iterrows():
        s = series.get(m.token_id)
        if s is None or pd.isna(m.resolved_price):
            continue
        won = float(m.resolved_price) > 0.5
        sampled = s["price"].resample("1h").last().dropna()
        for p in sampled.iloc[:-1]:      # exclude the settled tail
            rows.append({"sport": m.sport, "price": p, "won": int(won)})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["bucket"] = pd.cut(df["price"], BUCKETS)
    out = (df.groupby(["sport", "bucket"], observed=True)
             .agg(n=("won", "size"), implied=("price", "mean"),
                  realized=("won", "mean")).reset_index())
    out["bias"] = out["realized"] - out["implied"]
    return out


def logistic_calibration(cal_rows: pd.DataFrame) -> pd.DataFrame:
    """Fit won ~ logit(price) per sport. slope≈1, intercept≈0 = well calibrated."""
    try:
        from scipy.optimize import minimize
    except Exception:
        return pd.DataFrame()
    results = []
    for sport, g in cal_rows.groupby("sport"):
        x = np.log(g["implied"].clip(0.01, 0.99) /
                   (1 - g["implied"].clip(0.01, 0.99)))
        y, w = g["realized"].values, g["n"].values

        def nll(theta):
            z = theta[0] + theta[1] * x
            p = 1 / (1 + np.exp(-z))
            p = np.clip(p, 1e-6, 1 - 1e-6)
            return -np.sum(w * (y * np.log(p) + (1 - y) * np.log(1 - p)))

        fit = minimize(nll, x0=[0.0, 1.0], method="Nelder-Mead")
        results.append({"sport": sport, "intercept": round(fit.x[0], 4),
                        "slope": round(fit.x[1], 4),
                        "read": "slope<1 → extremes underpriced; "
                                "slope>1 → favorites underpriced"})
    return pd.DataFrame(results)


def build_moves(markets: pd.DataFrame, series: dict, shock_window: int) -> pd.DataFrame:
    """Per-minute frame: shock over `shock_window` min + forward returns."""
    frames = []
    meta = markets.set_index("token_id")
    for tok, s in series.items():
        p = s["price"].resample("1min").last().ffill(limit=10).dropna()
        if len(p) < shock_window + max(HORIZONS) + 5:
            continue
        shock = p.diff(shock_window)
        sigma = shock.abs().ewm(alpha=0.1).mean()
        fwd = {f"fwd_{h}": p.shift(-h) - p for h in HORIZONS}
        end = pd.to_datetime(meta.loc[tok, "end_date"], utc=True, errors="coerce")
        mins_to_close = ((end - p.index).total_seconds() / 60.0
                         if pd.notna(end) else np.nan)
        df = pd.DataFrame({"shock": shock, "sigma": sigma, "price": p, **fwd})
        df["mins_to_close"] = mins_to_close
        df["sport"] = meta.loc[tok, "sport"]
        df["token"] = tok
        frames.append(df.dropna(subset=["shock", "sigma"]))
    return pd.concat(frames) if frames else pd.DataFrame()


def shock_reversion(moves: pd.DataFrame) -> pd.DataFrame:
    """Conditional forward returns after big shocks (|shock| >= z*sigma & abs floor)."""
    rows = []
    for sport, g in moves.groupby("sport"):
        for z in (2.0, 2.5, 3.0, 4.0):
            mask = (g["shock"].abs() >= np.maximum(0.03, z * g["sigma"])) & \
                   (g["price"].between(0.05, 0.95))
            shocked = g[mask]
            if len(shocked) < 25:
                continue
            sign = np.sign(shocked["shock"])
            row = {"sport": sport, "z": z, "n": len(shocked),
                   "mean_shock": round(shocked["shock"].abs().mean(), 4)}
            for h in HORIZONS:
                cont = (sign * shocked[f"fwd_{h}"]).dropna()
                # negative = reversion (forward move opposes the shock)
                row[f"cont_{h}m"] = round(cont.mean(), 5)
                row[f"revert_frac_{h}m"] = round(
                    -cont.mean() / max(shocked["shock"].abs().mean(), 1e-9), 3)
            rows.append(row)
    return pd.DataFrame(rows)


def momentum_autocorr(moves: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sport, g in moves.groupby("sport"):
        d = g[["token", "price"]].reset_index(drop=True)
        d["r"] = d.groupby("token")["price"].diff()
        d["r1"] = d.groupby("token")["r"].shift(1)
        joined = d.dropna(subset=["r", "r1"])
        if len(joined) < 100:
            continue
        rows.append({"sport": sport, "n": len(joined),
                     "lag1_autocorr": round(joined["r"].corr(joined["r1"]), 4),
                     "read": ">0 momentum, <0 reversion (1-min bars)"})
    return pd.DataFrame(rows)


def ols(y: np.ndarray, X: np.ndarray, names: list[str]) -> pd.DataFrame:
    X = np.column_stack([np.ones(len(X)), X])
    names = ["const"] + names
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = max(len(y) - X.shape[1], 1)
    sigma2 = resid @ resid / dof
    try:
        cov = sigma2 * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        se = np.full(len(beta), np.nan)
    return pd.DataFrame({"var": names, "coef": np.round(beta, 6),
                         "se": np.round(se, 6),
                         "t": np.round(beta / np.where(se == 0, np.nan, se), 2)})


def regressions(moves: pd.DataFrame) -> pd.DataFrame:
    g = moves.dropna(subset=["fwd_5", "mins_to_close"]).copy()
    g = g[g["price"].between(0.05, 0.95)]
    if len(g) < 500:
        return pd.DataFrame()
    g = g.sample(min(len(g), 400_000), random_state=7)
    X_cols, names = [g["shock"].values], ["shock_5m"]
    for s in ("table_tennis", "mlb"):
        X_cols.append((g["sport"] == s).astype(float).values)
        names.append(f"is_{s}")
    X_cols.append(np.log1p(g["mins_to_close"].clip(lower=0)).values)
    names.append("log_mins_to_close")
    out = ols(g["fwd_5"].values, np.column_stack(X_cols), names)
    out.insert(0, "model", "fwd_5m ~ shock + sport + time")
    return out


def correlations(moves: pd.DataFrame) -> pd.DataFrame:
    g = moves.dropna(subset=["fwd_1", "fwd_5"]).copy()
    g["abs_shock"] = g["shock"].abs()
    g["dist_from_half"] = (g["price"] - 0.5).abs()
    cols = ["shock", "abs_shock", "sigma", "price", "dist_from_half",
            "mins_to_close", "fwd_1", "fwd_3", "fwd_5", "fwd_10"]
    return g[cols].corr(numeric_only=True).round(4).reset_index()


def emit_params(shock_df: pd.DataFrame, out: Path) -> dict:
    """Turn the reversion table into conservative per-sport bot overrides."""
    params: dict[str, dict] = {}
    for sport in SPORTS:
        g = shock_df[shock_df.sport == sport] if len(shock_df) else pd.DataFrame()
        if len(g) == 0:
            continue
        # pick the z with the strongest 5-min reversion and decent sample
        g = g[g.n >= 50].sort_values("revert_frac_5m", ascending=False)
        if len(g) == 0:
            continue
        best = g.iloc[0]
        fade_ok = best["revert_frac_5m"] > 0.10
        params[sport] = {
            "fade_enabled": bool(fade_ok),
            "shock_z": float(best["z"]),
            "shock_min_abs": round(float(max(0.03, best["mean_shock"] * 0.6)), 3),
            "fade_tp_fraction": float(round(float(np.clip(best["revert_frac_5m"], 0.2, 0.6)), 2)),
            "_evidence": {"n": int(best["n"]),
                          "revert_frac_5m": float(best["revert_frac_5m"])},
        }
    out.write_text(json.dumps(params, indent=2))
    return params


README = [
    ["Sheet", "What it tells you"],
    ["Markets", "Every resolved market pulled, with final resolution"],
    ["Calibration", "Implied vs realized win rate by price bucket & sport"],
    ["Calib_Logistic", "won ~ logit(price): slope 1 / intercept 0 = fair prices"],
    ["ShockReversion", "After |move| >= z*sigma: forward drift; revert_frac>0 = fade edge"],
    ["Momentum", "Lag-1 autocorrelation of 1-min returns per sport"],
    ["Regressions", "OLS fwd_5m ~ shock + sport dummies + time-to-close (t-stats)"],
    ["Correlations", "Pairwise correlations of engineered features"],
    ["", ""],
    ["Caveat", "History = mid/last only. Spread, depth, queue position and fees are"],
    ["", "NOT in this data; paper-trading fills are the go-live arbiter."],
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--shock-window", type=int, default=5, help="minutes")
    ap.add_argument("--workbook", default="analysis_workbook.xlsx")
    ap.add_argument("--params-out", default="strategy_params.json")
    args = ap.parse_args()

    data_dir = Path(args.data)
    markets, series = load(data_dir)
    if not series:
        raise SystemExit("No price data. Run history_downloader.py first.")

    cal = calibration_study(markets, series)
    cal_log = logistic_calibration(cal) if len(cal) else pd.DataFrame()
    moves = build_moves(markets, series, args.shock_window)
    shock = shock_reversion(moves) if len(moves) else pd.DataFrame()
    mom = momentum_autocorr(moves) if len(moves) else pd.DataFrame()
    reg = regressions(moves) if len(moves) else pd.DataFrame()
    corr = correlations(moves) if len(moves) else pd.DataFrame()

    with pd.ExcelWriter(args.workbook, engine="openpyxl") as xl:
        pd.DataFrame(README[1:], columns=README[0]).to_excel(xl, sheet_name="ReadMe", index=False)
        markets.to_excel(xl, sheet_name="Markets", index=False)
        if len(cal):
            cal.assign(bucket=cal["bucket"].astype(str)).to_excel(xl, sheet_name="Calibration", index=False)
        if len(cal_log):
            cal_log.to_excel(xl, sheet_name="Calib_Logistic", index=False)
        if len(shock):
            shock.to_excel(xl, sheet_name="ShockReversion", index=False)
        if len(mom):
            mom.to_excel(xl, sheet_name="Momentum", index=False)
        if len(reg):
            reg.to_excel(xl, sheet_name="Regressions", index=False)
        if len(corr):
            corr.to_excel(xl, sheet_name="Correlations", index=False)
    print(f"Wrote {args.workbook}")

    if len(shock):
        params = emit_params(shock, Path(args.params_out))
        print(f"Wrote {args.params_out}: "
              f"{json.dumps({k: v.get('fade_enabled') for k, v in params.items()})}")
        print("\nHeadline (5-min horizon):")
        for _, r in shock.sort_values(["sport", "z"]).iterrows():
            print(f"  {r.sport:<13} z={r.z:.1f} n={int(r.n):>6} "
                  f"revert_frac_5m={r['revert_frac_5m']:+.3f}")
    else:
        print("Not enough shock events for the reversion study — "
              "increase --days or --max-markets.")


if __name__ == "__main__":
    main()
