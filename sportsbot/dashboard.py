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

from sportsbot.bot.allocation import allocate
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
        rows.append(
            f'<tr class="{state}"><td><strong>{_e(sport)}</strong>'
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
        'with measured closing-line value, never with a losing streak.</div>')


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
    counts = rated_counts(cfg)
    ratings = {k: v > 0 for k, v in counts.items()}
    provisional = ratings_provenance(cfg)
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "mode": f'{cfg.get("mode", "paper")}/{cfg.get("exchange", "polymarket")}',
           "categories": cat, "rated": counts, "provisional": provisional,
           "accounts": {}}
    for acct in ACCOUNTS:
        start = starting_balance(cfg, acct)
        eq = account_equity(store, acct, start)
        out["accounts"][acct] = {
            "equity": eq,
            "series": store.equity_series(acct, limit=500),
            "decisions": store.recent_decisions(acct, limit=300),
            "gate": evidence_gate(store, acct),
            "allocation": allocate(max(eq["equity"], 0.0), cfg,
                                   reports[acct].get("by_sport", {}), ratings,
                                   provisional=provisional),
        }
    return out


def render(data: dict, refresh: int = 60) -> str:
    meta = (f'<meta http-equiv="refresh" content="{refresh}">'
            if refresh > 0 else "")
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
            f'<div class="card"><h2>Where the money is allowed to go</h2>'
            f'{_allocation(a["allocation"])}</div>'
            f'<div class="card"><h2>Decisions</h2>{_decisions(a["decisions"])}</div>'
            f'<div class="card"><h2>Go-live gate</h2>{_gate(a["gate"])}</div>'
            f'</section>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta}<title>sportsbot</title><style>{_CSS}</style></head>
<body><div class="wrap">
<header><div><h1>sportsbot</h1>
<div class="sub">paper-first prediction-market trading</div></div>
<div class="meta"><span class="pill mode">{_e(data["mode"])}</span>
<span class="sub">{_e(data["generated"])}</span></div></header>
{"".join(r for r, _ in switches)}
<div class="switch">{"".join(lbl for _, lbl in switches)}</div>
<div class="panels">{"".join(panels)}</div>
<footer>This page is a view, not a control. Live orders require
<code>mode: live</code> and <code>SPORTSBOT_LIVE=1</code> set on the host.</footer>
</div></body></html>"""


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


def build(cfg: dict, store, out_path: str, refresh: int = 60) -> dict:
    import os
    data = collect(cfg, store)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(render(data, refresh=refresh))
    return {"path": out_path,
            "sim_equity": data["accounts"]["sim"]["equity"]["equity"],
            "real_equity": data["accounts"]["real"]["equity"]["equity"],
            "decisions": len(data["accounts"]["sim"]["decisions"]),
            "gate_ready": data["accounts"]["sim"]["gate"]["ready"]}
