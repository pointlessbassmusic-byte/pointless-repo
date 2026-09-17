#!/usr/bin/env python3
"""Milestone 4 — substrate dashboard: e-process wealth curves per arm, fusion
weights, and trial counts, rendered as one self-contained static HTML file.

Inputs are ingest.py-schema CSVs (one per arm — exactly what the bridges emit:
`sportsbot substrate-export` for the sports arm, the weather snapshot service's
export for the weather arm) plus, optionally, the ARV session sqlite.

Analysis discipline matches bot_backtest-style reporting: the longshot
correction is fit on the FIRST half of resolved events and everything is scored
on the second half only (fit-on-prior). A baseline column equal to the market
column is detected as the documented placeholder and excluded from experts.

Usage:
  python3 dashboard.py --arm sports=reports/bot_events_2026-09-08.csv \
                       --arm weather=data/weather_events.csv \
                       --arv data/arv_sessions.db \
                       --out reports/dashboard.html
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import (HedgeFusion, ScoreBook, TestMartingale, channel_prob,  # noqa: E402
                    fit_longshot, longshot_correct)
from ingest import load_events_csv  # noqa: E402

THRESHOLD = 20.0
DELTA = 0.04
# color follows the entity, everywhere (validated palette, slots 1-3)
COLORS = {"market": "var(--s1)", "market_longshot": "var(--s2)",
          "baseline": "var(--s3)", "channel": "var(--s1)"}
LABELS = {"market": "market vs coin", "market_longshot": "longshot vs market",
          "baseline": "baseline vs market"}


def analyze_arm(name: str, path: Path) -> dict:
    events = load_events_csv(path)
    resolved = sorted([e for e in events if e.outcome is not None],
                      key=lambda e: e.close_time)
    out = {"name": name, "n_total": len(events), "n_resolved": len(resolved),
           "n_open": len(events) - len(resolved), "curves": {}, "weights": None,
           "stats": {}, "notes": []}
    if len(resolved) < 20:
        out["notes"].append(f"only {len(resolved)} resolved events — curves need ≥20")
        return out

    half = len(resolved) // 2
    a, b = fit_longshot(resolved[:half])
    scored = resolved[half:]
    out["notes"].append(f"longshot fit on prior {half} events: a={a:.3f} b={b:.3f}; "
                        f"scored on the remaining {len(scored)}")

    same_as_market = all(abs(e.baseline_prob - e.market_prob) < 1e-9 for e in resolved)
    coin_standin = all(abs(e.baseline_prob - 0.5) < 1e-9 for e in resolved)
    placeholder = same_as_market or coin_standin
    experts = ["market", "market_longshot"] + ([] if placeholder else ["baseline"])
    if same_as_market:
        out["notes"].append("baseline column equals market (documented placeholder) — "
                            "excluded from experts")
    elif coin_standin:
        out["notes"].append("baseline column is the 0.5 no-skill stand-in (no climatology "
                            "model yet) — excluded from experts")

    marts = {"market": TestMartingale(THRESHOLD),          # market vs coin
             "market_longshot": TestMartingale(THRESHOLD)}  # correction vs raw market
    if not placeholder:
        marts["baseline"] = TestMartingale(THRESHOLD)       # baseline vs market null
    fusion = HedgeFusion(experts, horizon=len(scored))
    book = ScoreBook(experts)

    for e in scored:
        probs = {"market": e.market_prob,
                 "market_longshot": longshot_correct(e.market_prob, a, b)}
        if not placeholder:
            probs["baseline"] = e.baseline_prob
        book.add(e, probs)
        marts["market"].update(probs["market"], 0.5, e.outcome)
        marts["market_longshot"].update(probs["market_longshot"], probs["market"], e.outcome)
        if not placeholder:
            marts["baseline"].update(probs["baseline"], probs["market"], e.outcome)
        fusion.update(probs, e.outcome)

    out["curves"] = {k: m.path for k, m in marts.items()}
    out["weights"] = {ex: [w[ex] for w in fusion.history] for ex in experts}
    out["stats"] = {k: {"brier": v["brier"], "log": v["log"], "n": v["n"],
                        "final_E": marts[k].E if k in marts else None,
                        "certified": marts[k].certified if k in marts else None,
                        "weight": fusion.weights()[k]}
                    for k, v in book.table().items()}
    return out


def analyze_arv(db_path: Path) -> dict | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT call, outcome, ablation FROM trials"
                        " WHERE outcome IS NOT NULL ORDER BY t_resolved").fetchall()
    n_all = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    mart = TestMartingale(THRESHOLD)
    hits = 0
    for call, outcome, _ab in rows:
        mart.update(channel_prob(0.5, call, DELTA), 0.5, outcome)
        hits += int(call == (1 if outcome == 1 else -1))
    return {"name": "arv", "n_total": n_all, "n_resolved": len(rows),
            "n_open": n_all - len(rows), "hits": hits,
            "curves": {"channel": mart.path} if rows else {},
            "stats": {}, "weights": None,
            "notes": [f"hit rate {hits}/{len(rows)}" if rows else "no resolved trials yet"]}


# --------------------------------------------------------------- SVG rendering

W, H, ML, MR, MT, MB = 720, 240, 52, 132, 14, 26

def _staggered(entries: list[tuple[float, str]], min_gap: float = 14.0) -> list[float]:
    """Given desired label y positions, push overlapping ones apart."""
    order = sorted(range(len(entries)), key=lambda i: entries[i][0])
    ys = [entries[i][0] for i in order]
    for j in range(1, len(ys)):
        if ys[j] - ys[j - 1] < min_gap:
            ys[j] = ys[j - 1] + min_gap
    out = [0.0] * len(entries)
    for pos, i in enumerate(order):
        out[i] = ys[pos]
    return out



def _log_chart(curves: dict[str, list[float]], chart_id: str) -> str:
    """Wealth curves on a log-y axis with the E=20 certification rule line."""
    vals = [v for c in curves.values() for v in c if v > 0]
    lo = min(0.3, min(vals)) * 0.8
    hi = max(THRESHOLD * 2, max(vals)) * 1.3
    llo, lhi = math.log10(lo), math.log10(hi)
    n = max(len(c) for c in curves.values())

    def x(i): return ML + (W - ML - MR) * (i / max(n - 1, 1))
    def y(v): return MT + (H - MT - MB) * (1 - (math.log10(max(v, lo)) - llo) / (lhi - llo))

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" data-chart="{chart_id}">']
    # decade gridlines + labels
    for d in range(math.ceil(llo), math.floor(lhi) + 1):
        gy = y(10 ** d)
        parts.append(f'<line x1="{ML}" y1="{gy:.1f}" x2="{W-MR}" y2="{gy:.1f}" class="grid"/>'
                     f'<text x="{ML-6}" y="{gy+4:.1f}" class="tick" text-anchor="end">'
                     f'{10**d:g}</text>')
    ty = y(THRESHOLD)
    parts.append(f'<line x1="{ML}" y1="{ty:.1f}" x2="{W-MR}" y2="{ty:.1f}" class="rule"/>'
                 f'<text x="{W-MR+4}" y="{ty+4:.1f}" class="rulelabel">E=20 certify</text>')
    keys = list(curves)
    label_ys = _staggered([(y(curves[k][-1]), k) for k in keys])
    for key, ly in zip(keys, label_ys):
        curve = curves[key]
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(curve))
        color = COLORS.get(key, "var(--s1)")
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                     f'stroke-width="2" stroke-linejoin="round"/>')
        parts.append(f'<text x="{x(len(curve)-1)+5:.1f}" y="{ly+4:.1f}" '
                     f'class="serieslabel">'
                     f'<tspan fill="{color}">●</tspan> {LABELS.get(key, key)}</text>')
    parts.append(f'<text x="{ML}" y="{H-6}" class="tick">trial 0</text>'
                 f'<text x="{W-MR}" y="{H-6}" class="tick" text-anchor="end">trial {n-1}</text>')
    parts.append("</svg>")
    payload = {"series": {LABELS.get(k, k): [round(v, 4) for v in c]
                          for k, c in curves.items()}}
    return (f'<div class="chart" data-points=\'{json.dumps(payload)}\''
            f' data-ml="{ML}" data-mr="{MR}" data-n="{n}">' + "".join(parts) +
            '<div class="tip" hidden></div></div>')


def _weights_chart(weights: dict[str, list[float]]) -> str:
    n = max(len(c) for c in weights.values())

    def x(i): return ML + (W - ML - MR) * (i / max(n - 1, 1))
    def y(v): return MT + (H - MT - MB) * (1 - v)

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img">']
    for g in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = y(g)
        parts.append(f'<line x1="{ML}" y1="{gy:.1f}" x2="{W-MR}" y2="{gy:.1f}" class="grid"/>'
                     f'<text x="{ML-6}" y="{gy+4:.1f}" class="tick" text-anchor="end">{g:g}</text>')
    keys = list(weights)
    label_ys = _staggered([(y(weights[k][-1]), k) for k in keys])
    for key, ly in zip(keys, label_ys):
        curve = weights[key]
        color = COLORS.get(key, "var(--s1)")
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(curve))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<text x="{x(len(curve)-1)+5:.1f}" y="{ly+4:.1f}" '
                     f'class="serieslabel"><tspan fill="{color}">●</tspan> {key}</text>')
    parts.append(f'<text x="{ML}" y="{H-6}" class="tick">trial 0</text>'
                 f'<text x="{W-MR}" y="{H-6}" class="tick" text-anchor="end">trial {n-1}</text>')
    parts.append("</svg>")
    payload = {"series": {k: [round(v, 4) for v in c] for k, c in weights.items()}}
    return (f'<div class="chart" data-points=\'{json.dumps(payload)}\''
            f' data-ml="{ML}" data-mr="{MR}" data-n="{n}">' + "".join(parts) +
            '<div class="tip" hidden></div></div>')


def _tiles(arm: dict) -> str:
    t = [f'<div class="tile"><div class="k">{arm["n_resolved"]}</div><div class="l">resolved</div></div>',
         f'<div class="tile"><div class="k">{arm["n_open"]}</div><div class="l">open</div></div>']
    st = arm["stats"].get("market")
    if st:
        t.append(f'<div class="tile"><div class="k">{st["brier"]:.4f}</div>'
                 f'<div class="l">market Brier</div></div>')
        cert = "✔ certified" if st["certified"] else "not certified"
        e = st["final_E"]
        e_txt = f"{e:.2f}" if e < 1e4 else f"{e:.1e}".replace("e+0", "e").replace("e+", "e")
        t.append(f'<div class="tile"><div class="k">{e_txt}</div>'
                 f'<div class="l">E market-vs-coin · {cert}</div></div>')
    if arm.get("hits") is not None and arm["n_resolved"]:
        t.append(f'<div class="tile"><div class="k">{arm["hits"]}/{arm["n_resolved"]}</div>'
                 f'<div class="l">ARV hits</div></div>')
    return '<div class="tiles">' + "".join(t) + "</div>"


def _table(arm: dict) -> str:
    if not arm["stats"]:
        return ""
    rows = []
    for k, v in arm["stats"].items():
        e_txt = "" if v["final_E"] is None else f"{v['final_E']:.3f}"
        rows.append(f"<tr><td>{k}</td><td>{v['n']}</td><td>{v['brier']:.4f}</td>"
                    f"<td>{v['log']:.4f}</td><td>{e_txt}</td>"
                    f"<td>{v['weight']:.3f}</td></tr>")
    rows = "".join(rows)
    return ('<details><summary>data table</summary><table><thead><tr><th>expert</th>'
            '<th>n</th><th>Brier</th><th>log</th><th>final E</th><th>fusion w</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></details>')


STYLE = """
.viz-root { color-scheme: light;
  --surface-1:#fcfcfb; --text-primary:#0b0b0b; --text-secondary:#52514e;
  --grid:#e4e3df; --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a;
  background:var(--surface-1); color:var(--text-primary);
  font:14px/1.45 system-ui,sans-serif; margin:0 auto; max-width:880px; padding:20px; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) .viz-root {
  color-scheme: dark; --surface-1:#1a1a19; --text-primary:#ffffff;
  --text-secondary:#c3c2b7; --grid:#3a3a38; --s1:#3987e5; --s2:#d95926; --s3:#199e70; } }
:root[data-theme="dark"] .viz-root { color-scheme: dark; --surface-1:#1a1a19;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --grid:#3a3a38;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; }
body { margin:0; background:#fcfcfb; }
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) body { background:#1a1a19; } }
:root[data-theme="dark"] body { background:#1a1a19; }
h1 { font-size:20px; } h2 { font-size:16px; margin:26px 0 6px; }
.sub, .note { color:var(--text-secondary); font-size:12.5px; }
.tiles { display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; }
.tile { border:1px solid var(--grid); border-radius:8px; padding:8px 14px; min-width:96px; }
.tile .k { font-size:20px; font-weight:600; font-variant-numeric:tabular-nums; }
.tile .l { color:var(--text-secondary); font-size:12px; }
.chart { position:relative; margin:8px 0 2px; }
svg { width:100%; height:auto; display:block; }
.grid { stroke:var(--grid); stroke-width:1; }
.rule { stroke:var(--text-secondary); stroke-width:1; stroke-dasharray:5 4; }
.rulelabel, .serieslabel, .tick { font:11px system-ui,sans-serif; fill:var(--text-secondary); }
.serieslabel { fill:var(--text-primary); }
.tip { position:absolute; pointer-events:none; background:var(--surface-1);
  border:1px solid var(--grid); border-radius:6px; padding:5px 8px; font-size:12px;
  box-shadow:0 2px 8px rgba(0,0,0,.15); white-space:nowrap; z-index:2; }
table { border-collapse:collapse; font-size:12.5px; margin:6px 0; }
td, th { border:1px solid var(--grid); padding:4px 9px; text-align:right; }
th:first-child, td:first-child { text-align:left; }
details summary { cursor:pointer; color:var(--text-secondary); font-size:12.5px; }
"""

SCRIPT = """
document.querySelectorAll('.chart').forEach(function (el) {
  var data = JSON.parse(el.dataset.points), tip = el.querySelector('.tip');
  var n = +el.dataset.n, ml = +el.dataset.ml, mr = +el.dataset.mr, W = 720;
  el.addEventListener('mousemove', function (ev) {
    var r = el.getBoundingClientRect(), fx = (ev.clientX - r.left) / r.width * W;
    var i = Math.round((fx - ml) / (W - ml - mr) * (n - 1));
    if (i < 0 || i >= n) { tip.hidden = true; return; }
    var lines = ['trial ' + i];
    for (var k in data.series) {
      var v = data.series[k][i];
      if (v !== undefined) lines.push(k + ': ' + v);
    }
    tip.textContent = lines.join('  ·  ');
    tip.style.left = Math.min(ev.clientX - r.left + 12, r.width - 240) + 'px';
    tip.style.top = '8px'; tip.hidden = false;
  });
  el.addEventListener('mouseleave', function () { tip.hidden = true; });
});
"""


def render(arms: list[dict], arv: dict | None, out: Path, generated: str) -> None:
    body = [f'<div class="viz-root"><h1>Substrate dashboard</h1>'
            f'<div class="sub">generated {html.escape(generated)} · shadow mode · '
            f'certification threshold E ≥ {THRESHOLD:g} · delta {DELTA}</div>']
    for arm in arms:
        body.append(f'<h2>{html.escape(arm["name"])} arm</h2>')
        body.append(_tiles(arm))
        for note in arm["notes"]:
            body.append(f'<div class="note">{html.escape(note)}</div>')
        if arm["curves"]:
            body.append('<div class="sub">e-process wealth (log scale) — the null a '
                        'channel must beat stays near 1; real signal climbs</div>')
            body.append(_log_chart(arm["curves"], arm["name"]))
        if arm["weights"]:
            body.append('<div class="sub">Hedge fusion weights over trials</div>')
            body.append(_weights_chart(arm["weights"]))
        body.append(_table(arm))
    if arv:
        body.append('<h2>ARV arm</h2>')
        body.append(_tiles(arv))
        for note in arv["notes"]:
            body.append(f'<div class="note">{html.escape(note)}</div>')
        if arv["curves"]:
            body.append(_log_chart(arv["curves"], "arv"))
    body.append("</div>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("<!doctype html><meta charset='utf-8'>"
                   "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                   f"<title>Substrate dashboard</title><style>{STYLE}</style>"
                   + "".join(body) + f"<script>{SCRIPT}</script>")
    print(f"wrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", action="append", default=[],
                    help="name=path.csv (ingest schema); repeatable")
    ap.add_argument("--arv", default="", help="ARV sessions sqlite (optional)")
    ap.add_argument("--out", default="reports/dashboard.html")
    ap.add_argument("--generated", default="", help="timestamp label for the header")
    args = ap.parse_args()
    if not args.arm and not args.arv:
        sys.exit("nothing to draw — pass at least one --arm name=path.csv")

    arms = []
    for spec in args.arm:
        name, _, path = spec.partition("=")
        arms.append(analyze_arm(name, Path(path)))
    arv = analyze_arv(Path(args.arv)) if args.arv else None
    import time
    generated = args.generated or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    render(arms, arv, Path(args.out), generated)


if __name__ == "__main__":
    main()
