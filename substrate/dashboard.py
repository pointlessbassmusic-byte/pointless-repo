"""Substrate dashboard (handoff milestone 4): e-process wealth curves per
arm, Hedge fusion weights, and trial counts, rendered to one self-contained
HTML file with inline SVG — no matplotlib, no web framework, nothing served
by default.

New wiring only — engine.py / arv.py / arv_cli.py are untouched. Inputs are
exactly what milestones 1-3 already produce:

  * ingest-schema CSVs (`sportsbot substrate-export`,
    `sportsbot weather-snapshot --export ...`) — one dashboard "arm" per
    `domain` column value; resolved rows feed the pipeline, open rows are
    counted as pending.
  * the ARV session SQLite DB written by `arv_cli.py` — scored as its own
    arm against the 0.5 coin null, feedback vs ablation split included.
    ARV calls whose event_id matches an ingested event also run as a
    channel expert against the market null in that event's arm.

Per arm the pipeline mirrors backtest.py on real data: the null forecaster
is the raw market probability (no fitted longshot correction — fitting on
the same data it scores would be leakage), the baseline expert is the bot /
climatology probability from the CSV, and a TestMartingale tracks the
anytime-valid evidence that the baseline beats the market (certify at
E >= 20, alpha = 1/20 per PROTOCOL_v1's gate). HedgeFusion runs over
{market, baseline}. Shadow mode: this file only reads and reports.

Usage:
  python3 dashboard.py --events data/substrate_events.csv \
                       --events data/weather_ingest.csv \
                       --arv-db arv_sessions.sqlite \
                       --out dashboard.html [--loop 300]
  python3 dashboard.py --self-test
"""
from __future__ import annotations

import argparse
import html
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arv_cli import DELTA, Store  # noqa: E402
from engine import HedgeFusion, ScoreBook, TestMartingale, channel_prob  # noqa: E402
from ingest import load_events_csv  # noqa: E402

THRESHOLD = 20.0
PALETTE = ["#2563eb", "#dc2626", "#059669", "#9333ea", "#ea580c"]


# ---------------------------------------------------------------- pipelines

def _clip(p, lo=0.02, hi=0.98):
    return min(max(float(p), lo), hi)


def run_arm(events, arv_calls=None):
    """Score one arm's resolved events in resolve-time order.

    Returns {"n", "n_channel", "mart", "mart_channel", "fusion", "book"}.
    `arv_calls` maps event_id -> +1/-1 sealed ARV call for the optional
    channel expert (evaluated on its own martingale vs the market null).
    """
    arv_calls = arv_calls or {}
    events = sorted(events, key=lambda e: e.resolve_time)
    mart = TestMartingale(threshold=THRESHOLD)
    mart_channel = TestMartingale(threshold=THRESHOLD)
    fusion = HedgeFusion(["market", "baseline"], horizon=max(len(events), 1))
    book = ScoreBook(["market", "baseline", "fusion"])
    n_channel = 0
    for e in events:
        m, b = _clip(e.market_prob), _clip(e.baseline_prob)
        probs = {"market": m, "baseline": b}
        probs["fusion"] = fusion.predict(probs)
        book.add(e, probs)
        mart.update(b, m, e.outcome)
        fusion.update({"market": m, "baseline": b}, e.outcome)
        if e.event_id in arv_calls:
            q = channel_prob(m, arv_calls[e.event_id], DELTA)
            mart_channel.update(q, m, e.outcome)
            n_channel += 1
    return {"n": len(events), "n_channel": n_channel, "mart": mart,
            "mart_channel": mart_channel, "fusion": fusion, "book": book}


def load_arv(db_path):
    """Resolved + pending ARV trials from the arv_cli SQLite store."""
    if not db_path or not os.path.exists(db_path):
        return None
    trials = Store(db_path).load_all()
    resolved = [(tr, abl) for tr, abl in trials.values()
                if tr.outcome is not None]
    resolved.sort(key=lambda x: x[0].t_resolved)
    mart = TestMartingale(threshold=THRESHOLD)
    for tr, _ in resolved:
        mart.update(channel_prob(0.5, tr.call, DELTA), 0.5,
                    1 if tr.outcome == 1 else 0)

    def hit_rate(rows):
        return (sum(1 for tr in rows if tr.hit) / len(rows)) if rows else None

    feed = [tr for tr, abl in resolved if not abl]
    abls = [tr for tr, abl in resolved if abl]
    calls = {tr.event_id: tr.call for tr, _ in resolved}
    return {"n_total": len(trials), "n_resolved": len(resolved), "mart": mart,
            "hit_rate": hit_rate([tr for tr, _ in resolved]),
            "feedback": (len(feed), hit_rate(feed)),
            "ablation": (len(abls), hit_rate(abls)), "calls": calls}


# ---------------------------------------------------------------- inline SVG

def _ticks(lo, hi, n=5):
    if hi <= lo:
        hi = lo + 1.0
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def svg_line_chart(series, title, ylabel, ylog=False, hline=None,
                   width=640, height=300):
    """Minimal multi-series line chart. series = [(label, [float, ...])]."""
    ml, mr, mt, mb = 58, 12, 30, 34
    pw, ph = width - ml - mr, height - mt - mb
    tf = (lambda v: math.log10(max(v, 1e-12))) if ylog else float
    ys = [tf(v) for _, data in series for v in data]
    if hline is not None:
        ys.append(tf(hline))
    lo, hi = (min(ys), max(ys)) if ys else (0.0, 1.0)
    if hi - lo < 1e-9:
        lo, hi = lo - 0.5, hi + 0.5
    nmax = max((len(d) for _, d in series), default=1)

    def x(i):
        return ml + pw * (i / max(nmax - 1, 1))

    def y(v):
        return mt + ph * (1 - (tf(v) - lo) / (hi - lo))

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" '
             f'style="max-width:{width}px;width:100%;background:#fff">',
             f'<text x="{ml}" y="18" class="ct">{html.escape(title)}</text>']
    for tv in _ticks(lo, hi):
        py = mt + ph * (1 - (tv - lo) / (hi - lo))
        lab = f"{10 ** tv:.3g}" if ylog else f"{tv:.2f}"
        parts.append(f'<line x1="{ml}" y1="{py:.1f}" x2="{width - mr}" '
                     f'y2="{py:.1f}" class="grid"/>'
                     f'<text x="{ml - 6}" y="{py + 4:.1f}" class="tick" '
                     f'text-anchor="end">{lab}</text>')
    for i in range(0, nmax, max(nmax // 6, 1)):
        parts.append(f'<text x="{x(i):.1f}" y="{height - 12}" class="tick" '
                     f'text-anchor="middle">{i}</text>')
    if hline is not None:
        parts.append(f'<line x1="{ml}" y1="{y(hline):.1f}" x2="{width - mr}" '
                     f'y2="{y(hline):.1f}" stroke="#111" stroke-dasharray="5,4"/>')
    for k, (label, data) in enumerate(series):
        if not data:
            continue
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(data))
        c = PALETTE[k % len(PALETTE)]
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{c}" '
                     f'stroke-width="1.8"/>')
        parts.append(f'<text x="{width - mr - 4}" y="{mt + 14 + 15 * k}" '
                     f'text-anchor="end" class="tick" fill="{c}">'
                     f'{html.escape(label)}</text>')
    parts.append(f'<text x="14" y="{mt + ph / 2:.0f}" class="tick" '
                 f'transform="rotate(-90 14 {mt + ph / 2:.0f})" '
                 f'text-anchor="middle">{html.escape(ylabel)}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- HTML

_CSS = """
body{font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;
     background:#f6f7f9;color:#1a1d21;padding:20px}
h1{font-size:20px;margin:0 0 2px}h2{font-size:16px;margin:26px 0 8px}
.sub{color:#5a626d;font-size:12px;margin-bottom:14px}
.card{background:#fff;border:1px solid #e2e5ea;border-radius:8px;
      padding:14px 16px;margin:10px 0;max-width:1360px}
.row{display:flex;flex-wrap:wrap;gap:14px}.row>div{flex:1 1 420px;min-width:0}
table{border-collapse:collapse;font-size:13px;margin:6px 0}
th,td{border:1px solid #e2e5ea;padding:4px 10px;text-align:right}
th:first-child,td:first-child{text-align:left}
th{background:#eef1f5;font-weight:600}
.ok{color:#059669;font-weight:600}.no{color:#5a626d}
.ct{font-size:13px;font-weight:600;fill:#1a1d21}
.tick{font-size:10px;fill:#5a626d}.grid{stroke:#eef1f5}
"""


def _score_table(book):
    rows = ["<table><tr><th>expert</th><th>n</th><th>Brier</th>"
            "<th>mean log score</th></tr>"]
    for name, s in book.table().items():
        rows.append(f"<tr><td>{html.escape(name)}</td><td>{s['n']}</td>"
                    f"<td>{s['brier']:.4f}</td><td>{s['log']:.4f}</td></tr>")
    return "".join(rows) + "</table>"


def _gate_line(mart, label):
    cls, word = (("ok", "CERTIFIED") if mart.certified
                 else ("no", "not certified"))
    return (f"{label}: E = {mart.E:.3f} (max {max(mart.path):.3f}, "
            f"threshold {THRESHOLD:g}) — <span class='{cls}'>{word}</span>")


def render(arms, arv, pending, generated=None, refresh=0):
    """arms: {name: run_arm(...) result}; arv: load_arv(...) or None;
    pending: {arm_name: n_open}."""
    generated = generated or time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    meta = (f'<meta http-equiv="refresh" content="{int(refresh)}">'
            if refresh else "")
    out = [f"<!doctype html><html><head><meta charset='utf-8'>{meta}"
           f"<title>substrate dashboard</title><style>{_CSS}</style></head><body>",
           "<h1>Substrate dashboard</h1>",
           f"<div class='sub'>generated {html.escape(generated)} · shadow mode "
           f"(nothing here stakes money) · null = raw market prob · "
           f"gate: e-process, certify at E &ge; {THRESHOLD:g}</div>"]

    # trial counts
    out.append("<div class='card'><h2 style='margin-top:0'>Trial counts</h2>"
               "<table><tr><th>arm</th><th>resolved</th><th>open</th>"
               "<th>ARV-linked</th></tr>")
    for name, a in arms.items():
        out.append(f"<tr><td>{html.escape(name)}</td><td>{a['n']}</td>"
                   f"<td>{pending.get(name, 0)}</td><td>{a['n_channel']}</td></tr>")
    if arv:
        out.append(f"<tr><td>arv (vs coin)</td><td>{arv['n_resolved']}</td>"
                   f"<td>{arv['n_total'] - arv['n_resolved']}</td><td>—</td></tr>")
    out.append("</table></div>")

    if not arms and not arv:
        out.append("<div class='card'>No data yet — export events with "
                   "<code>sportsbot substrate-export</code> / "
                   "<code>sportsbot weather-snapshot --export</code>, or run "
                   "ARV sessions with <code>arv_cli.py</code>.</div>")

    for name, a in arms.items():
        wealth = [("baseline vs market", a["mart"].path)]
        if a["n_channel"]:
            wealth.append(("ARV channel vs market", a["mart_channel"].path))
        weights = [(e, [hrow[e] for hrow in a["fusion"].history])
                   for e in a["fusion"].experts]
        out.append(f"<div class='card'><h2 style='margin-top:0'>"
                   f"{html.escape(name)} arm</h2><div class='row'><div>"
                   + svg_line_chart(wealth, f"{name} — e-process wealth",
                                    "evidence E (log)", ylog=True,
                                    hline=THRESHOLD)
                   + "</div><div>"
                   + svg_line_chart(weights, f"{name} — Hedge fusion weights",
                                    "weight")
                   + "</div></div>"
                   + f"<p>{_gate_line(a['mart'], 'baseline vs market null')}"
                   + (f"<br>{_gate_line(a['mart_channel'], 'ARV channel vs market null')}"
                      if a["n_channel"] else "")
                   + "</p>" + _score_table(a["book"]) + "</div>")

    if arv:
        def _hr(v):
            return "—" if v is None else f"{v:.3f}"

        nf, hf = arv["feedback"]
        na, ha = arv["ablation"]
        out.append("<div class='card'><h2 style='margin-top:0'>ARV arm "
                   "(coin null)</h2><div class='row'><div>"
                   + svg_line_chart([("channel vs 0.5", arv["mart"].path)],
                                    "arv — e-process wealth",
                                    "evidence E (log)", ylog=True,
                                    hline=THRESHOLD)
                   + "</div><div><table><tr><th>subset</th><th>n</th>"
                   "<th>hit rate</th></tr>"
                   + f"<tr><td>all resolved</td><td>{arv['n_resolved']}</td>"
                     f"<td>{_hr(arv['hit_rate'])}</td></tr>"
                   + f"<tr><td>feedback</td><td>{nf}</td>"
                     f"<td>{_hr(hf)}</td></tr>"
                   + f"<tr><td>ablation (no feedback)</td><td>{na}</td>"
                     f"<td>{_hr(ha)}</td></tr>"
                   + "</table><p>"
                   + _gate_line(arv["mart"], "channel vs coin null")
                   + "</p></div></div></div>")

    out.append("</body></html>")
    return "".join(out)


# ---------------------------------------------------------------- build

def build(event_csvs, arv_db, out_path, refresh=0):
    """Load inputs, run the arms, write the HTML. Returns a summary dict."""
    events = []
    for path in event_csvs:
        events.extend(load_events_csv(path))
    arv = load_arv(arv_db)
    calls = arv["calls"] if arv else {}
    arms, pending = {}, {}
    for domain in sorted({e.domain for e in events}):
        rows = [e for e in events if e.domain == domain]
        resolved = [e for e in rows if e.outcome is not None]
        pending[domain] = len(rows) - len(resolved)
        if resolved:
            arms[domain] = run_arm(resolved, calls)
    html_doc = render(arms, arv, pending, refresh=refresh)
    with open(out_path, "w") as fh:
        fh.write(html_doc)
    return {"out": out_path,
            "arms": {k: {"n": v["n"], "open": pending.get(k, 0),
                         "E": round(v["mart"].E, 4),
                         "certified": v["mart"].certified}
                     for k, v in arms.items()},
            "arv": (None if arv is None else
                    {"n_resolved": arv["n_resolved"],
                     "E": round(arv["mart"].E, 4)})}


# ---------------------------------------------------------------- self-test

def self_test() -> int:
    import csv
    import random
    import tempfile

    import arv_cli

    fails = []

    def ck(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    rng = random.Random(11)
    with tempfile.TemporaryDirectory() as tmp:
        # --- synthetic ingest CSV: baseline knows the true prob, market is
        # noisy around it, so the baseline-vs-market martingale should grow.
        csv_path = os.path.join(tmp, "events.csv")
        with open(csv_path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["event_id", "domain", "close_time", "resolve_time",
                        "market_prob", "baseline_prob", "outcome"])
            for i in range(160):
                domain = "sports" if i % 2 == 0 else "weather"
                p = min(max(rng.gauss(0.5, 0.15), 0.05), 0.95)
                m = min(max(p + rng.gauss(0, 0.10), 0.02), 0.98)
                y = 1 if rng.random() < p else 0
                w.writerow([f"EV{i:03d}", domain, i, i + 1,
                            f"{m:.4f}", f"{p:.4f}", y])
            w.writerow(["EVOPEN1", "sports", 999, 1000, 0.5, 0.5, ""])
            w.writerow(["EVOPEN2", "weather", 999, 1000, 0.5, 0.5, ""])

        # --- ARV DB via the real arv_cli lifecycle, ids matching two events
        pool = os.path.join(tmp, "pool")
        os.makedirs(pool)
        for i in range(6):
            with open(os.path.join(pool, f"img{i}.png"), "wb") as fh:
                fh.write(b"\x89PNG\r\n" + bytes([i]))
        db = os.path.join(tmp, "arv.sqlite")
        for eid, outcome in (("EV000", 1), ("EV001", 0)):
            ns = dict(db=db, pool=pool, event_id=eid)
            arv_cli.cmd_open(argparse.Namespace(**ns, ablation=(eid == "EV001")))
            arv_cli.cmd_transcribe(argparse.Namespace(**ns, tags="water,bright"))
            arv_cli.cmd_judge(argparse.Namespace(**ns, score_a=0.8, score_b=0.1))
            arv_cli.cmd_resolve(argparse.Namespace(**ns, outcome=outcome))

        out_path = os.path.join(tmp, "dashboard.html")
        summary = build([csv_path], db, out_path, refresh=60)
        doc = open(out_path).read()

        ck("two arms scored", set(summary["arms"]) == {"sports", "weather"})
        ck("resolved counts", all(v["n"] == 80 for v in summary["arms"].values()))
        ck("open rows counted", all(v["open"] == 1
                                    for v in summary["arms"].values()))
        ck("arv resolved", summary["arv"]["n_resolved"] == 2)
        arm = run_arm([e for e in load_events_csv(csv_path)
                       if e.domain == "sports" and e.outcome is not None])
        ck("wealth path length = n+1", len(arm["mart"].path) == 81)
        ck("skilled baseline beats market on Brier",
           arm["book"].table()["baseline"]["brier"]
           < arm["book"].table()["market"]["brier"])
        ck("fusion weight moved toward baseline",
           arm["fusion"].weights()["baseline"] > 0.5)
        ck("html has all sections",
           all(s in doc for s in ("sports arm", "weather arm", "ARV arm",
                                  "Trial counts", "<svg", "refresh")))
        ck("certification threshold drawn", "stroke-dasharray" in doc)

        # empty inputs still render
        empty_out = os.path.join(tmp, "empty.html")
        s2 = build([], None, empty_out)
        ck("no-data build renders", s2["arms"] == {}
           and "No data yet" in open(empty_out).read())

    print(("\n%d failure(s)" % len(fails)) if fails else "\nAll self-tests passed.")
    return 1 if fails else 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--events", action="append", default=[],
                   help="ingest-schema CSV (repeatable)")
    p.add_argument("--arv-db", default="arv_sessions.sqlite",
                   help="arv_cli SQLite DB (skipped if missing)")
    p.add_argument("--out", default="dashboard.html")
    p.add_argument("--loop", type=int, default=0,
                   help="rebuild every N seconds (also sets HTML auto-refresh)")
    args = p.parse_args()
    if args.self_test:
        sys.exit(self_test())
    while True:
        print(build(args.events, args.arv_db, args.out, refresh=args.loop))
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
