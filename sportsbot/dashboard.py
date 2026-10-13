"""Trading dashboard: the sim book and the real book, side by side.

Self-contained HTML — inline CSS, hand-built SVG, no JavaScript, no CDN, no
web server. Open the file, or serve it over an SSH tunnel (the firewall stays
SSH-only on purpose; see deploy/DEPLOYMENT.md).

The sim/real switch is a VIEW switch. It shows you a different book; it cannot
start real trading. Live orders still require `mode: live` in config AND
`SPORTSBOT_LIVE=1` in the environment, both set deliberately on the host — a
button in a web page is exactly the wrong place for that decision, so this page
does not have one.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from sportsbot.bot.allocation import allocate, allocation_bankroll
from sportsbot.bot.portfolio import (
    RESULTS,
    allocate_arms,
    load_allocation,
    running_arms_for,
    summarise_for_dashboard,
)
from sportsbot.bot.gate import evidence_gate
from sportsbot.bot.ledger import ACCOUNTS, account_equity

ACCOUNT_LABELS = {"sim": "Sim money", "real": "Real money"}


def starting_balance(cfg: dict, account: str) -> float:
    accounts = cfg.get("accounts", {}) or {}
    row = accounts.get(account, {}) or {}
    if "starting_balance" in row:
        return float(row["starting_balance"])
    if account == "sim":
        return float(cfg.get("bankroll", {}).get("amount", 100.0))
    return 0.0


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


# Ratings filenames as `runner.load_models` writes them, and the key holding
# the rated entities in each.
_RATINGS_FILES = {
    "tennis": ("tennis.json", "overall"),
    "baseball": ("baseball.json", "elo"),
    "table_tennis": ("tabletennis.json", "elo"),
}


def ratings_provenance(cfg: dict) -> dict[str, bool]:
    """Which sports' ratings came from a market bootstrap rather than a real
    history. `sportsbot fit` records this in the ratings file, because the two
    are not interchangeable and the difference is invisible from the numbers
    alone."""
    import json
    import os

    d = cfg.get("storage", {}).get("ratings_dir", "data/ratings")
    out: dict[str, bool] = {}
    for sport, (fname, _key) in _RATINGS_FILES.items():
        try:
            with open(os.path.join(d, fname)) as fh:
                meta = json.load(fh).get("meta") or {}
        except (OSError, ValueError):
            meta = {}
        out[sport] = "bootstrap" in str(meta.get("source", ""))
    return out


def rated_counts(cfg: dict) -> dict[str, int]:
    """How many entities each sport actually has ratings for.

    A file existing is not the same as a sport being tradable: a fit that
    fetched nothing still writes `{"overall": {}, ...}`, which is exactly the
    state tennis is in on a host that cannot reach the Sackmann CSVs. Count
    the entities, not the file.
    """
    import json
    import os

    d = cfg.get("storage", {}).get("ratings_dir", "data/ratings")
    out: dict[str, int] = {}
    for sport, (fname, key) in _RATINGS_FILES.items():
        try:
            with open(os.path.join(d, fname)) as fh:
                out[sport] = len(json.load(fh).get(key) or {})
        except (OSError, ValueError):
            out[sport] = 0
    return out


# ----------------------------------------------------------------- charts
def _svg_equity(series: list[dict], start: float, width: int = 720,
                height: int = 220) -> str:
    """Equity curve against the starting balance. Returns a placeholder when
    there is not yet enough history to draw an honest line."""
    pts = [float(r["equity"]) for r in series if r.get("equity") is not None]
    if len(pts) < 2:
        return ('<div class="empty">No equity history yet — the curve starts '
                'once the bot has run a couple of cycles.</div>')
    pad_l, pad_r, pad_t, pad_b = 52, 12, 12, 24
    lo, hi = min(pts + [start]), max(pts + [start])
    if hi - lo < 1e-9:
        lo, hi = lo - 1.0, hi + 1.0
    span = hi - lo
    lo, hi = lo - span * 0.08, hi + span * 0.08
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b

    def x(i: int) -> float:
        return pad_l + (iw * i / max(1, len(pts) - 1))

    def y(v: float) -> float:
        return pad_t + ih * (1.0 - (v - lo) / (hi - lo))

    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(pts))
    area = f"{pad_l},{y(lo):.1f} {line} {x(len(pts) - 1):.1f},{y(lo):.1f}"
    # Axis precision follows the range: a book sitting flat at $100.00 must
    # not print "$100" five times over a two-dollar span.
    full = hi - lo
    dec = 0 if full >= 20 else 1 if full >= 2 else 2
    grid = []
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = lo + full * frac
        yy = y(v)
        grid.append(f'<line class="grid" x1="{pad_l}" y1="{yy:.1f}" '
                    f'x2="{width - pad_r}" y2="{yy:.1f}"/>')
        grid.append(f'<text class="axis" x="{pad_l - 8}" y="{yy + 4:.1f}" '
                    f'text-anchor="end">${v:,.{dec}f}</text>')
    base = (f'<line class="base" x1="{pad_l}" y1="{y(start):.1f}" '
            f'x2="{width - pad_r}" y2="{y(start):.1f}"/>')
    up = pts[-1] >= start
    cls = "up" if up else "down"
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="equity curve">'
            f'{"".join(grid)}{base}'
            f'<polygon class="area {cls}" points="{area}"/>'
            f'<polyline class="line {cls}" points="{line}"/>'
            f'<circle class="dot {cls}" cx="{x(len(pts) - 1):.1f}" '
            f'cy="{y(pts[-1]):.1f}" r="3.5"/></svg>')


def _bar(frac: float) -> str:
    pct = max(0.0, min(1.0, frac)) * 100.0
    return (f'<div class="bar"><span style="width:{pct:.1f}%"></span></div>')


# ----------------------------------------------------------------- panels
def _tiles(eq: dict) -> str:
    ret = eq["return_pct"]
    sign = "up" if ret > 0 else "down" if ret < 0 else "flat"
    items = [
        ("Equity", _money(eq["equity"]), f'from {_money(eq["starting_balance"])}'),
        ("Return", f'{ret:+.2f}%', sign),
        ("Realized", _money(eq["realized_pnl"]), "settled bets"),
        ("Unrealized", _money(eq["unrealized_pnl"]), "marked to last quote"),
        ("At risk", _money(eq["exposure"]), f'{eq["open_positions"]} open'),
        ("Settled", str(eq["settled_bets"]), "bets scored"),
    ]
    out = []
    for label, value, sub in items:
        cls = f" {sub}" if sub in ("up", "down", "flat") else ""
        note = "" if sub in ("up", "down", "flat") else f'<div class="sub">{_e(sub)}</div>'
        out.append(f'<div class="tile"><div class="label">{_e(label)}</div>'
                   f'<div class="value{cls}">{_e(value)}</div>{note}</div>')
    return f'<div class="tiles">{"".join(out)}</div>'


def _allocation(alloc: dict) -> str:
    rows = []
    for sport, s in alloc["sleeves"].items():
        state = ("active" if s["active"] else "idle")
        clv = ("—" if s.get("mean_clv") is None else f'{s["mean_clv"]:+.4f}')
        hand = (' <span class="pill">manual</span>' if s.get("manual") else "")
        rows.append(
            f'<tr class="{state}"><td><strong>{_e(sport)}</strong>{hand}'
            f'<div class="evidence">{_e(s["evidence"])}</div></td>'
            f'<td class="num">{s["weight"] * 100:.0f}%</td>'
            f'<td class="num">{_money(s["budget"])}</td>'
            f'<td class="num">{_e(clv)}</td>'
            f'<td><span class="pill">{_e(s["bound_by"])}</span></td></tr>')
    excl = "".join(
        f'<tr class="idle"><td><strong>{_e(k)}</strong>'
        f'<div class="evidence">{_e(v)}</div></td>'
        f'<td class="num">0%</td><td class="num">$0.00</td><td class="num">—</td>'
        f'<td><span class="pill">excluded</span></td></tr>'
        for k, v in alloc.get("excluded", {}).items())
    return (
        '<table class="grid-table"><thead><tr><th>Sleeve &amp; the evidence for it</th>'
        '<th class="num">Weight</th><th class="num">Budget</th>'
        '<th class="num">Mean CLV</th><th>Bound by</th></tr></thead>'
        f'<tbody>{"".join(rows)}{excl}</tbody></table>'
        f'<div class="foot">Allocated {_money(alloc["allocated"])} of '
        f'{_money(alloc["bankroll"])}; the rest stays uncommitted. Weights move '
        'with measured closing-line value, never with a losing streak. Manual '
        'control: <code>allocation.manual</code> / <code>allocation.paused</code> '
        'in <code>config/local.yaml</code>; the measurements and caps still '
        'apply on top.</div>')


def _ledger(entries: list[dict]) -> str:
    """The research record: every pre-registered test and its verdict."""
    if not entries:
        return '<div class="foot">No research ledger found (docs/research_ledger.json).</div>'
    rows = []
    for e in entries:
        v = str(e.get("verdict", "")).upper()
        cls = "ok" if v.startswith("PASS") else "down" if v.startswith("FAIL") else ""
        rows.append(
            f'<tr><td>{_e(e.get("date", ""))}</td>'
            f'<td><strong>{_e(e.get("test", ""))}</strong>'
            f'<div class="evidence">{_e(e.get("result", ""))}</div></td>'
            f'<td><span class="pill {cls}">{_e(v)}</span></td>'
            f'<td class="sub">{_e(e.get("doc", ""))}</td></tr>')
    n_fail = sum(1 for e in entries if str(e.get("verdict", "")).upper().startswith("FAIL"))
    return ('<table class="grid-table"><thead><tr><th>Date</th><th>Pre-registered test</th>'
            '<th>Verdict</th><th>Write-up</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
            f'<div class="foot">{len(entries)} tests, {n_fail} failed. A strategy is '
            'funded only after it passes here AND clears the go-live gate.</div>')


def _markouts(rep: dict) -> str:
    """Mean markout per fill by sport and horizon: the price move after our
    own fills, in cents per share, negative = picked off."""
    by_sport = rep.get("by_sport") or {}
    if not by_sport:
        return ('<div class="foot">No marked fills yet. Each fill is marked at '
                '+5 s, +60 s, +5 min and +30 min against the live book.</div>')
    horizons = sorted({h for v in by_sport.values() for h in v})
    head = "".join(f'<th class="num">+{h}s</th>' if h < 60 else
                   f'<th class="num">+{h // 60}m</th>' for h in horizons)
    rows = []
    for sport, cells in sorted(by_sport.items()):
        tds = []
        for h in horizons:
            c = cells.get(h)
            if not c or c["mean_cents"] is None:
                tds.append('<td class="num">—</td>')
                continue
            cls = "down" if c["mean_cents"] < 0 else "up"
            t = "" if c["t"] is None else f' <span class="sub">t {c["t"]:+.1f}</span>'
            tds.append(f'<td class="num"><span class="value {cls}">{c["mean_cents"]:+.2f}¢</span>'
                       f'<div class="sub">n {c["n"]}{t}</div></td>')
        rows.append(f'<tr><td><strong>{_e(sport)}</strong></td>{"".join(tds)}</tr>')
    return ('<table class="grid-table"><thead><tr><th>Sleeve</th>'
            f'{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f'<div class="foot">{rep.get("n_fills", 0)} fills marked. A fill that is '
            'under water at +5 s was picked off; the go-live bar needs these to '
            'average above the fee.</div>')


def load_ledger(path: str = "docs/research_ledger.json") -> list[dict]:
    import json
    import os

    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), path)
    for candidate in (path, here):
        try:
            with open(candidate) as fh:
                data = json.load(fh)
            return sorted(data.get("tests", []), key=lambda e: e.get("date", ""), reverse=True)
        except (OSError, ValueError):
            continue
    return []


# Reason prefixes -> the plain-English label the summary groups under.
_REASON_LABELS = (
    ("spread", "spread wider than the limit"),
    ("model uncertainty", "model not confident enough to price"),
    ("no two-sided book", "no two-sided book"),
    ("crossed or empty book", "crossed or empty book"),
    ("mid ", "price outside the tradable band"),
    ("risk veto", "vetoed by the risk layer"),
    ("order rejected", "rejected by the venue"),
)


def _reason_key(reason: str | None) -> str:
    """Collapse a reason to its shape so near-identical passes group."""
    import re
    text = (reason or "").strip()
    for prefix, label in _REASON_LABELS:
        if text.startswith(prefix):
            return label
    if "edge" in text:
        return "edge under the bar"
    # Fall back to the text with its numbers removed, so an unmapped reason
    # still groups instead of showing one row per market.
    stripped = re.sub(r"\s+", " ", re.sub(r"[-+]?\d*\.?\d+", "", text)).strip()
    return stripped or "no reason recorded"


def _decision_summary(rows: list[dict]) -> str:
    """Why the bot passed, in aggregate.

    On a near-efficient slate the individual rows all say the same few things;
    the shape of the passes is the actual information — it says which filter
    is binding right now, which is what you would change if you wanted more
    action (and why you probably should not).
    """
    import collections
    if not rows:
        return ""
    counts = collections.Counter(_reason_key(r.get("reason")) for r in rows
                                 if (r.get("action") or "") == "skip")
    bets = sum(1 for r in rows if (r.get("action") or "") == "bet")
    total = len(rows)
    chips = "".join(
        f'<div class="reason"><span class="n">{n}</span>'
        f'<span class="t">{_e(k)}</span></div>'
        for k, n in counts.most_common(5))
    return (f'<div class="summary"><div class="summary-head">'
            f'Last {total} decisions: <strong>{bets}</strong> bet, '
            f'<strong>{total - bets}</strong> passed</div>{chips}</div>')


def _decisions(rows: list[dict], show: int = 12) -> str:
    if not rows:
        return ('<div class="empty">No decisions recorded yet. Every scanned '
                'market lands here — the passes too, with the reason.</div>')
    out = []
    for r in rows[:show]:
        act = (r.get("action") or "").lower()
        ts = (r.get("ts") or "")[11:19]
        mp = r.get("model_prob")
        kp = r.get("market_prob")
        edge = r.get("edge")
        out.append(
            f'<tr class="act-{_e(act)}"><td class="mono">{_e(ts)}</td>'
            f'<td class="market">{_e((r.get("title") or r.get("market_id"))[:44])}'
            f'<div class="sub">{_e(r.get("sport") or "")}</div></td>'
            f'<td class="num">{"—" if mp is None else f"{mp:.3f}"}</td>'
            f'<td class="num">{"—" if kp is None else f"{kp:.3f}"}</td>'
            f'<td class="num">{"—" if edge is None else f"{edge:+.4f}"}</td>'
            f'<td class="num">{_money(r.get("stake")) if r.get("stake") else "—"}</td>'
            f'<td><span class="tag {_e(act)}">{_e(act or "?")}</span></td>'
            f'<td class="why">{_e(r.get("reason") or "")}</td></tr>')
    more = (f'<div class="foot">Showing the {min(show, len(rows))} most recent '
            f'of {len(rows)} kept. The full feed is the <code>decisions</code> '
            f'table in the bot database.</div>' if len(rows) > show else "")
    return (_decision_summary(rows)
            + '<table class="grid-table"><thead><tr><th>Time</th><th>Market</th>'
            '<th class="num">Model</th><th class="num">Market</th>'
            '<th class="num">Edge</th><th class="num">Stake</th>'
            '<th>Action</th><th>Why</th></tr></thead>'
            f'<tbody>{"".join(out)}</tbody></table>{more}')


def _arms(port: dict) -> str:
    """Per-arm evidence, gate and budget — the table the operator reads to
    see where money may go and why, and where the learning budget stands."""
    rows = []
    for r in summarise_for_dashboard(port):
        if r["budget"] > 0:
            state, pill = "ok", ("live" if r["gate_ready"] else
                                 "learning" if r["learn"] and not r["gate_ready"]
                                 else "paper" if port.get("mode") == "paper" else "manual")
        elif not r["enabled"]:
            state, pill = "idle", "off"
        elif not r["running"]:
            state, pill = "idle", "not running"
        else:
            state, pill = "pending", "unfunded"
        ci = ("—" if r["mean_clv"] is None else
              f'{r["mean_clv"]:+.4f}' + ("" if r["clv_lo"] is None else
                                         f' [{r["clv_lo"]:+.4f}, {r["clv_hi"]:+.4f}]'))
        manual = ("auto" if r["manual_weight"] is None else f'{r["manual_weight"]:.0%}')
        sharp = (f'{r["sharp_share"]:.0%} vs Pinnacle' if r["n_clv"] else "")
        brier = ("—" if r["brier"] is None else f'{r["brier"]:.4f}')
        rows.append(
            f'<tr class="{state}"><td><strong>{_e(r["arm"])}</strong>'
            f'<div class="evidence">{_e(r["source"])}</div></td>'
            f'<td class="num">{_money(r["budget"])}<div class="sub">{_e(manual)}</div></td>'
            f'<td class="num">{r["n_clv"]}<div class="sub">{r["n_settled"]} settled</div></td>'
            f'<td class="num">{_e(ci)}<div class="sub">{_e(sharp)}</div></td>'
            f'<td class="num">{_e(brier)}</td>'
            f'<td class="barcell">{_bar(r["gate_progress"])}</td>'
            f'<td><span class="pill">{_e(pill)}</span></td></tr>')
    learn = port.get("learning", {})
    learn_line = ""
    if learn.get("budget_usd"):
        learn_line = (f' Learning budget {_money(learn["budget_usd"])} across '
                      f'{len(learn.get("arms", []))} arm(s); 7-day realised '
                      f'{learn.get("loss_7d", 0.0):+.2f}; '
                      f'{"PAUSED" if learn.get("paused") else "active"} '
                      f'(stop at −{_money(learn.get("weekly_loss_stop_usd", 0.0))}).')
    return (
        '<table class="grid-table"><thead><tr><th>Arm &amp; why it has this budget</th>'
        '<th class="num">Budget<div class="sub">weight</div></th>'
        '<th class="num">CLV n</th><th class="num">Mean CLV [95% CI]</th>'
        '<th class="num">Brier</th><th>Gate</th><th></th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        f'<div class="foot">Allocated {_money(port["allocated"])} of the '
        f'{_money(port["total_cap"])} exposure cap on a {_money(port["bankroll"])} '
        f'bankroll ({_e(port.get("bankroll_mode", "config"))} mode).{_e(learn_line)} '
        f'Change weights, learning money and the bankroll mode in '
        f'<code>{_e(port.get("path", "config/allocation.yaml"))}</code>; the bot '
        f're-reads it every cycle. Budgets move with measured CLV intervals, '
        f'never with a losing streak.</div>')


def edge_bar_rows(store, account: str, days: int = 14) -> list[dict]:
    """Per-day, per-sport distance to the entry bar, newest last."""
    rows = []
    for key, by_sport in sorted(store.kv_prefix(f"edgebar:{account}:").items()):
        date = key.rsplit(":", 1)[-1]
        for sport, d in sorted((by_sport or {}).items()):
            rows.append({"date": date, "sport": sport, **d})
    dates = sorted({r["date"] for r in rows})[-days:]
    return [r for r in rows if r["date"] in dates]


def _svg_edge_bar(rows: list[dict], width: int = 720, height: int = 180) -> str:
    """Best net edge seen per day, one line per sport, against the bar."""
    dates = sorted({r["date"] for r in rows})
    sports = sorted({r["sport"] for r in rows if r.get("best") is not None})
    if len(dates) < 2 or not sports:
        return ""
    bar = max(float(r["bar"]) for r in rows)
    vals = [float(r["best"]) for r in rows if r.get("best") is not None] + [bar, 0.0]
    lo, hi = min(vals), max(vals)
    span = max(hi - lo, 0.02)
    lo, hi = lo - span * 0.1, hi + span * 0.1
    pad_l, pad_r, pad_t, pad_b = 52, 12, 12, 24
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b

    def x(i):
        return pad_l + iw * i / max(1, len(dates) - 1)

    def y(v):
        return pad_t + ih * (1.0 - (v - lo) / (hi - lo))

    grid = []
    for frac in (0.0, 0.5, 1.0):
        v = lo + (hi - lo) * frac
        grid.append(f'<line class="grid" x1="{pad_l}" y1="{y(v):.1f}" '
                    f'x2="{width - pad_r}" y2="{y(v):.1f}"/>'
                    f'<text class="axis" x="{pad_l - 8}" y="{y(v) + 4:.1f}" '
                    f'text-anchor="end">{v * 100:+.1f} pt</text>')
    base = (f'<line class="base" x1="{pad_l}" y1="{y(bar):.1f}" x2="{width - pad_r}" '
            f'y2="{y(bar):.1f}"/><text class="axis" x="{width - pad_r}" y="{y(bar) - 4:.1f}" '
            f'text-anchor="end">bar {bar * 100:.1f} pt</text>')
    lines = []
    for sport in sports:
        pts = []
        for i, d in enumerate(dates):
            hit = [r for r in rows if r["date"] == d and r["sport"] == sport and r.get("best") is not None]
            if hit:
                pts.append(f"{x(i):.1f},{y(float(hit[0]['best'])):.1f}")
        if len(pts) >= 2:
            lines.append(f'<polyline class="line edge" points="{" ".join(pts)}"/>')
        elif pts:
            cx, cy = pts[0].split(",")
            lines.append(f'<circle class="dot edge" cx="{cx}" cy="{cy}" r="3"/>')
    labels = (f'<text class="axis" x="{pad_l}" y="{height - 6}">{dates[0]}</text>'
              f'<text class="axis" x="{width - pad_r}" y="{height - 6}" '
              f'text-anchor="end">{dates[-1]}</text>')
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="best edge seen against the entry bar">{"".join(grid)}{base}'
            f'{"".join(lines)}{labels}</svg>')


def _edge_bar(rows: list[dict]) -> str:
    """Distance to the bar: the arbitrage desk's headline chart, carried
    over. A flat equity line says nothing; the gap between the best edge
    the slate offered and the bar says whether the bar or the market is
    what stands in the way."""
    if not rows:
        return ('<div class="empty">Nothing priced yet — this fills in once the bot '
                'has evaluated a slate.</div>')
    body = []
    for r in sorted(rows, key=lambda r: (r["date"], r["sport"]), reverse=True)[:21]:
        best = r.get("best")
        gap = (None if best is None else float(best) - float(r["bar"]))
        body.append(
            f'<tr><td class="mono">{_e(r["date"])}</td><td>{_e(r["sport"])}</td>'
            f'<td class="num">{r.get("priced", 0)}</td>'
            f'<td class="num">{"—" if best is None else f"{float(best) * 100:+.2f}"}</td>'
            f'<td class="num">{"—" if gap is None else f"{gap * 100:+.2f}"}</td>'
            f'<td class="num">{r.get("within_1pt", 0)}</td>'
            f'<td class="num">{r.get("spread_blocked", 0)}</td>'
            f'<td class="num">{r.get("bets", 0)}</td></tr>')
    return (_svg_edge_bar(rows)
            + '<table class="grid-table"><thead><tr><th>Day</th><th>Sport</th>'
              '<th class="num">Priced</th><th class="num">Best edge (pt)</th>'
              '<th class="num">To bar (pt)</th><th class="num">Within 1 pt</th>'
              '<th class="num">Spread-blocked</th><th class="num">Bets</th></tr></thead>'
              f'<tbody>{"".join(body)}</tbody></table>'
              '<div class="foot">Best net edge the strategy printed on any priced market '
              'that day (after fees and slippage) against the entry bar. A gap that '
              'stays negative while spread-blocked counts are low means the market, not '
              'the bar, is what stands in the way; many markets within a point of the bar '
              'mean the bar is binding and the fee floor decides.</div>')


def _results() -> str:
    rows = "".join(
        f'<tr><td class="num">{_e(n)}</td><td><strong>{_e(t)}</strong></td>'
        f'<td class="why">{_e(v)}</td></tr>' for n, t, v in RESULTS)
    return ('<table class="grid-table"><thead><tr><th class="num">#</th>'
            '<th>Measured</th><th>Verdict</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            '<div class="foot">Every line is a measurement on real prices after '
            'fees (docs/EDGE_VERDICT_2026-09-23.md). Nothing here is re-litigated '
            'without new data.</div>')


def _gate(gate: dict) -> str:
    rows = []
    for c in gate["criteria"]:
        mark = "ok" if c["ok"] else "pending"
        rows.append(
            f'<tr class="{mark}"><td>{_e(c["name"])}'
            f'<div class="sub">{_e(c["target"])}</div></td>'
            f'<td class="num">{_e(c["value"])}</td>'
            f'<td class="barcell">{_bar(c["progress"])}</td></tr>')
    verdict = ("All four met — real money is justified by the record."
               if gate["ready"] else
               "Not met. Until every row is green the real book stays empty.")
    return (f'<table class="grid-table"><tbody>{"".join(rows)}</tbody></table>'
            f'<div class="foot">{_e(verdict)}</div>')


def _real_notice(eq: dict) -> str:
    if eq["settled_bets"] or eq["open_positions"] or eq["starting_balance"]:
        return ""
    return (
        '<div class="notice"><strong>No real-money account connected.</strong>'
        '<p>Nothing here trades real money yet, and this page cannot turn it on. '
        'When the gate below is green and you want to fund it:</p>'
        '<ol><li>Put venue keys in <code>.env</code> on the host — never in '
        'config, never in the repo.</li>'
        '<li>Set <code>accounts.real.starting_balance</code> to what you have '
        'actually funded.</li>'
        '<li>Set <code>mode: live</code> in config <em>and</em> '
        '<code>SPORTSBOT_LIVE=1</code> in the environment. Both, deliberately, '
        'on the host.</li>'
        '<li>Run <code>sportsbot doctor</code> — it exits non-zero if the host '
        'is not ready.</li></ol>'
        '<p>Start at roughly 10% of intended bankroll.</p></div>')


# ------------------------------------------------------------------ build
def collect(cfg: dict, store) -> dict:
    """Everything the page renders, as plain data (also handy for tests)."""
    from sportsbot.bot.positions import category_report

    sports_cfg = cfg.get("sports", {})
    bank = cfg.get("bankroll", {})

    def report_for(mode: str) -> dict:
        """Per-account evidence. Sizing the real book off the paper book's CLV
        would let simulated fills decide how much real money moves."""
        return category_report(
            store.settled_bets(limit=1_000_000, mode=mode),
            cfg.get("adaptive", {}),
            float(bank.get("min_edge", 0.03)),
            float(bank.get("max_stake_per_market", 50.0)),
            {s: float(v["min_edge_override"]) for s, v in sports_cfg.items()
             if isinstance(v, dict) and "min_edge_override" in v},
            {s: float(v["max_stake_override"]) for s, v in sports_cfg.items()
             if isinstance(v, dict) and "max_stake_override" in v},
            float(bank.get("kelly_multiplier", 0.25)),
            float(cfg.get("risk", {}).get("max_drawdown", 250.0)),
        )

    reports = {acct: report_for(mode) for acct, mode in ACCOUNTS.items()}
    cat = reports["sim"]
    alloc_cfg = load_allocation(str(cfg.get("allocation", {}).get("path",
                                                                 "config/allocation.yaml")))
    try:
        running = running_arms_for(cfg)
    except ValueError:
        running = set()
    fees = store.get_kv("fees_verified")
    fees_ok = isinstance(fees, dict) and bool(fees.get("verified"))
    counts = rated_counts(cfg)
    ratings = {k: v > 0 for k, v in counts.items()}
    provisional = ratings_provenance(cfg)
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "mode": f'{cfg.get("mode", "paper")}/{cfg.get("exchange", "polymarket")}',
           "categories": cat, "rated": counts, "provisional": provisional,
           "accounts": {}}
    from sportsbot.bot.markout import markout_report

    for acct, mode in ACCOUNTS.items():
        start = starting_balance(cfg, acct)
        eq = account_equity(store, acct, start)
        out["accounts"][acct] = {
            "equity": eq,
            "series": store.equity_series(acct, limit=500),
            "decisions": store.recent_decisions(acct, limit=300),
            "edge_bar": edge_bar_rows(store, acct),
            "gate": evidence_gate(store, acct),
            "allocation": allocate(allocation_bankroll(cfg, eq["equity"]), cfg,
                                   reports[acct].get("by_sport", {}), ratings,
                                   provisional=provisional),
            "markouts": markout_report(store, mode=mode),
            "portfolio": allocate_arms(
                max(eq["equity"], 0.0) if alloc_cfg.bankroll_mode == "equity"
                else float(bank.get("amount", 100.0)),
                cfg, alloc_cfg,
                store.settled_bets(limit=1_000_000, mode=ACCOUNTS[acct]),
                running, fees_verified=fees_ok, mode=ACCOUNTS[acct]),
        }
    out["ledger"] = load_ledger()
    return out


def render(data: dict, refresh: int = 60, fragment: bool = False) -> str:
    """The page. `fragment=True` emits only <title>, <style> and the body
    content (no doctype/html/head/body, no auto-refresh) for hosts that wrap
    the page in their own document skeleton, such as a published artifact
    that is re-published by the daily routine rather than refreshed."""
    meta = (f'<meta http-equiv="refresh" content="{refresh}">'
            if refresh > 0 and not fragment else "")
    panels, switches = [], []
    for i, acct in enumerate(ACCOUNTS):
        a = data["accounts"][acct]
        checked = " checked" if i == 0 else ""
        switches.append(
            (f'<input class="acct-radio" type="radio" name="acct" '
             f'id="acct-{acct}"{checked}>',
             f'<label class="acct-label" for="acct-{acct}">'
             f'{_e(ACCOUNT_LABELS[acct])}</label>'))
        notice = _real_notice(a["equity"]) if acct == "real" else ""
        panels.append(
            f'<section class="panel panel-{acct}">'
            f'{notice}'
            f'{_tiles(a["equity"])}'
            f'<div class="card"><h2>Equity</h2>'
            f'{_svg_equity(a["series"], a["equity"]["starting_balance"])}</div>'
            f'<div class="card"><h2>Strategy arms: evidence, gate, budget</h2>'
            f'{_arms(a["portfolio"])}</div>'
            f'<div class="card"><h2>Sport sleeves (legacy view)</h2>'
            f'{_allocation(a["allocation"])}</div>'
            f'<div class="card"><h2>Fill quality (markout)</h2>'
            f'{_markouts(a.get("markouts", {}))}</div>'
            f'<div class="card"><h2>Decisions</h2>{_decisions(a["decisions"])}</div>'
            f'<div class="card"><h2>Distance to the bar</h2>{_edge_bar(a.get("edge_bar", []))}</div>'
            f'<div class="card"><h2>Go-live gate (account)</h2>{_gate(a["gate"])}</div>'
            f'<div class="card"><h2>Research ledger</h2>{_ledger(data.get("ledger", []))}</div>'
            f'<div class="card"><h2>What has been measured</h2>{_results()}</div>'
            f'</section>')
    body = f"""<div class="wrap">
<header><div><h1>sportsbot</h1>
<div class="sub">paper-first prediction-market trading</div></div>
<div class="meta"><span class="pill mode">{_e(data["mode"])}</span>
<span class="sub">{_e(data["generated"])}</span></div></header>
{"".join(r for r, _ in switches)}
<div class="switch">{"".join(lbl for _, lbl in switches)}</div>
<div class="panels">{"".join(panels)}</div>
<footer>This page is a view, not a control. Live orders require
<code>mode: live</code> and <code>SPORTSBOT_LIVE=1</code> set on the host.</footer>
</div>"""
    if fragment:
        return f"<title>sportsbot board</title><style>{_CSS}</style>\n{body}"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta}<title>sportsbot board</title><style>{_CSS}</style></head>
<body>{body}</body></html>"""


_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#12151a;--muted:#697386;--line:#e3e6ea;
--up:#0a7f5c;--down:#c0392b;--accent:#2563eb;--chip:#eef1f5;}
:root:not([data-theme="light"]){@media(prefers-color-scheme:dark){
--bg:#0e1116;--card:#161a21;--ink:#e9edf3;--muted:#9aa4b2;--line:#252b34;
--up:#3ddc97;--down:#ff6b6b;--accent:#6ea8fe;--chip:#1e242d;}}
:root[data-theme="dark"]{--bg:#0e1116;--card:#161a21;--ink:#e9edf3;
--muted:#9aa4b2;--line:#252b34;--up:#3ddc97;--down:#ff6b6b;--accent:#6ea8fe;
--chip:#1e242d;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;}
.wrap{max-width:1040px;margin:0 auto;padding:24px 16px 48px}
header{display:flex;justify-content:space-between;align-items:flex-start;
gap:16px;flex-wrap:wrap;margin-bottom:20px}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
h2{font-size:14px;margin:0 0 12px;color:var(--muted);font-weight:600;
text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--muted);font-size:12px}
.meta{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;
background:var(--chip);color:var(--muted);font-size:11px;white-space:nowrap}
.pill.mode{color:var(--accent);font-weight:600}
.pill.ok{color:var(--up);font-weight:600}.pill.down{color:var(--down);font-weight:600}
.switch{display:inline-flex;background:var(--chip);border-radius:10px;
padding:3px;margin-bottom:18px}
.acct-radio{position:absolute;opacity:0;pointer-events:none}
.acct-label{padding:7px 18px;border-radius:8px;font-size:13px;font-weight:600;
color:var(--muted);cursor:pointer;user-select:none}
#acct-sim:checked ~ .switch label[for="acct-sim"],
#acct-real:checked ~ .switch label[for="acct-real"]{background:var(--card);
color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.14)}
#acct-sim:focus-visible ~ .switch label[for="acct-sim"],
#acct-real:focus-visible ~ .switch label[for="acct-real"]{
outline:2px solid var(--accent)}
.panel{display:none}
#acct-sim:checked ~ .panels .panel-sim{display:block}
#acct-real:checked ~ .panels .panel-real{display:block}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
gap:10px;margin-bottom:16px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:13px 15px}
.tile .label{font-size:11px;color:var(--muted);text-transform:uppercase;
letter-spacing:.06em}
.tile .value{font-size:22px;font-weight:650;margin-top:3px;
font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.value.up{color:var(--up)}.value.down{color:var(--down)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:18px;margin-bottom:16px}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--line);stroke-width:1}
.chart .base{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 4;opacity:.7}
.chart .axis{fill:var(--muted);font-size:10px}
.chart .line{fill:none;stroke-width:2;stroke-linejoin:round}
.chart .line.up{stroke:var(--up)}.chart .line.down{stroke:var(--down)}
.chart .area{opacity:.10}
.chart .area.up{fill:var(--up)}.chart .area.down{fill:var(--down)}
.chart .dot.up{fill:var(--up)}.chart .dot.down{fill:var(--down)}
.chart .line.edge{stroke:var(--accent)}.chart .dot.edge{fill:var(--accent)}
table.grid-table{width:100%;border-collapse:collapse;font-size:13px}
.grid-table th{text-align:left;font-size:11px;color:var(--muted);
text-transform:uppercase;letter-spacing:.05em;font-weight:600;
padding:0 8px 8px;border-bottom:1px solid var(--line)}
.grid-table td{padding:10px 8px;border-bottom:1px solid var(--line);
vertical-align:top}
.grid-table tr:last-child td{border-bottom:none}
.grid-table .num{text-align:right;font-variant-numeric:tabular-nums;
white-space:nowrap}
.grid-table tr.idle{opacity:.55}
.grid-table tr.ok td:first-child{box-shadow:inset 3px 0 0 var(--up)}
.grid-table tr.pending td:first-child{box-shadow:inset 3px 0 0 var(--line)}
.evidence{color:var(--muted);font-size:11.5px;margin-top:3px;max-width:52ch}
.market{max-width:28ch}
.why{color:var(--muted);font-size:12px;max-width:40ch}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
color:var(--muted);white-space:nowrap}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;
font-weight:600;background:var(--chip);color:var(--muted)}
.tag.bet{background:color-mix(in srgb,var(--up) 18%,transparent);color:var(--up)}
.tag.exit{background:color-mix(in srgb,var(--accent) 18%,transparent);
color:var(--accent)}
.bar{height:6px;border-radius:999px;background:var(--line);min-width:90px}
.bar span{display:block;height:100%;border-radius:999px;background:var(--accent)}
.barcell{width:120px;vertical-align:middle}
.empty{color:var(--muted);font-size:13px;padding:14px 0}
.summary{margin-bottom:14px}
.summary-head{font-size:13px;margin-bottom:9px}
.reason{display:flex;align-items:baseline;gap:9px;padding:4px 0;font-size:12.5px;
color:var(--muted)}
.reason .n{min-width:38px;text-align:right;font-variant-numeric:tabular-nums;
font-weight:650;color:var(--ink)}
.foot{color:var(--muted);font-size:12px;margin-top:12px;max-width:72ch}
.notice{background:var(--card);border:1px solid var(--line);
border-left:3px solid var(--accent);border-radius:12px;padding:16px 18px;
margin-bottom:16px;font-size:13px}
.notice p{margin:8px 0;color:var(--muted)}
.notice ol{margin:8px 0 0 18px;padding:0;color:var(--muted)}
.notice li{margin:4px 0}
code{background:var(--chip);padding:1px 5px;border-radius:4px;font-size:12px}
footer{color:var(--muted);font-size:12px;margin-top:24px;text-align:center}
@media(max-width:640px){.wrap{padding:16px}
.grid-table .why,.grid-table th:nth-child(4),.grid-table td:nth-child(4)
{display:none}}
"""


def build(cfg: dict, store, out_path: str, refresh: int = 60,
          fragment_path: str | None = None) -> dict:
    import os
    data = collect(cfg, store)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(render(data, refresh=refresh))
    if fragment_path:
        with open(fragment_path, "w") as fh:
            fh.write(render(data, fragment=True))
    return {"path": out_path,
            "sim_equity": data["accounts"]["sim"]["equity"]["equity"],
            "real_equity": data["accounts"]["real"]["equity"]["equity"],
            "decisions": len(data["accounts"]["sim"]["decisions"]),
            "gate_ready": data["accounts"]["sim"]["gate"]["ready"]}
