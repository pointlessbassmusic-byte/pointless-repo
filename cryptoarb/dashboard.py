"""Self-contained HTML dashboard. No frameworks, no CDN, inline SVG.

The headline chart is deliberately "net edge vs breakeven", not an equity
curve: with no opportunity clearing costs, an equity curve is a flat line
that tells you nothing, while distance-to-breakeven tells you exactly how
close each strategy is to becoming viable and what would have to change.
"""

from __future__ import annotations

import html

import allocator as alloc_mod
from engine import live_gate

SERIES = [("bundle", "Polymarket bundle", "--series-1"),
          ("triangular", "KuCoin triangular", "--series-2"),
          ("cross_exchange", "Cross-exchange spot", "--series-3")]

CSS = """
:root{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f4f3f0;--line:#e2e1dc;
 --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#77766f;
 --series-1:#2a78d6;--series-2:#eb6834;--series-3:#1baf7a;
 --good:#0ca30c;--warning:#fab219;--critical:#d03b3b}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 color-scheme:dark;--surface-1:#1a1a19;--surface-2:#232322;--line:#383835;
 --text-primary:#fff;--text-secondary:#c3c2b7;--text-muted:#94938a;
 --series-1:#3987e5;--series-2:#d95926;--series-3:#199e70}}
:root[data-theme="dark"]{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#232322;
 --line:#383835;--text-primary:#fff;--text-secondary:#c3c2b7;--text-muted:#94938a;
 --series-1:#3987e5;--series-2:#d95926;--series-3:#199e70}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-1);color:var(--text-primary);
 font:14px/1.5 -apple-system,BlinkMacSystemFont,system-ui,sans-serif;padding:24px}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:19px;margin:0 0 2px;letter-spacing:-0.01em}
.sub{color:var(--text-secondary);font-size:12.5px;margin-bottom:18px}
.card{background:var(--surface-2);border:1px solid var(--line);border-radius:10px;
 padding:16px 18px;margin-bottom:14px}
h2{font-size:14px;margin:0 0 12px;color:var(--text-primary)}
.row{display:flex;flex-wrap:wrap;gap:14px}
.row>*{flex:1 1 300px;min-width:0}
.hero{font-size:46px;font-weight:600;letter-spacing:-0.02em;line-height:1.05}
.heronote{color:var(--text-secondary);font-size:12.5px;margin-top:4px}
.tiles{display:flex;flex-wrap:wrap;gap:10px}
.tile{flex:1 1 150px;background:var(--surface-1);border:1px solid var(--line);
 border-radius:8px;padding:10px 12px;min-width:0}
.tlabel{font-size:11px;color:var(--text-secondary)}
.tvalue{font-size:21px;font-weight:600;margin:2px 0;letter-spacing:-0.01em}
.tnote{font-size:11px;color:var(--text-muted)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{border-bottom:1px solid var(--line);padding:6px 9px;text-align:right;
 white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}
th{color:var(--text-secondary);font-weight:600;font-size:11px;
 text-transform:uppercase;letter-spacing:.03em}
.mono{font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;font-size:11px;
 font-weight:600;border:1px solid var(--line)}
.on{background:var(--series-1);color:#fff;border-color:transparent}
.off{color:var(--text-secondary)}
.skip{color:var(--text-muted)}
.fill{color:var(--good);font-weight:600}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;margin-bottom:6px}
.legend span{display:flex;align-items:center;gap:6px;color:var(--text-secondary)}
.sw{width:11px;height:11px;border-radius:3px;display:inline-block}
.bar{height:9px;border-radius:99px;background:var(--surface-1);
 border:1px solid var(--line);overflow:hidden}
.bar i{display:block;height:100%}
.gate li{margin:3px 0;color:var(--text-secondary);font-size:12.5px}
.scroll{overflow-x:auto}
svg{display:block;max-width:100%}
.tt{position:fixed;pointer-events:none;background:var(--surface-1);
 border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:12px;
 box-shadow:0 4px 14px rgba(0,0,0,.14);opacity:0;transition:opacity .1s}
"""


def _fmt_money(v):
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _chart(series_data, width=1100, height=300):
    """Net edge (bps) per strategy over cycles, with the breakeven line."""
    ml, mr, mt, mb = 60, 205, 16, 34
    pw, ph = width - ml - mr, height - mt - mb
    pts = [v for s in series_data.values() for _, v in s]
    if not pts:
        return "<p class='sub'>No cycles recorded yet.</p>"
    lo, hi = min(pts + [0.0]), max(pts + [0.0])
    pad = max(1.0, (hi - lo) * 0.12)
    lo, hi = lo - pad, hi + pad
    n = max(2, max(len(s) for s in series_data.values()))

    def X(i):
        return ml + pw * (i / max(1, n - 1))

    def Y(v):
        return mt + ph * (1 - (v - lo) / (hi - lo))

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="Net edge in basis points per strategy over scan cycles">']
    step = (hi - lo) / 4
    for k in range(5):
        v = lo + step * k
        y = Y(v)
        out.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+pw}" y2="{y:.1f}" '
                   f'stroke="var(--line)" stroke-width="1"/>'
                   f'<text x="{ml-8}" y="{y+4:.1f}" text-anchor="end" '
                   f'font-size="10" fill="var(--text-muted)">{v:+.0f}</text>')
    y0 = Y(0.0)
    out.append(f'<line x1="{ml}" y1="{y0:.1f}" x2="{ml+pw}" y2="{y0:.1f}" '
               f'stroke="var(--text-secondary)" stroke-width="1.5" '
               f'stroke-dasharray="6,4"/>'
               f'<text x="{ml+6}" y="{y0-7:.1f}" font-size="11" '
               f'fill="var(--text-secondary)">breakeven after fees '
               f'— above this line an arb is real</text>')
    for key, label, var in SERIES:
        data = series_data.get(key) or []
        if not data:
            continue
        pth = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(data))
        out.append(f'<polyline points="{pth}" fill="none" stroke="var({var})" '
                   f'stroke-width="2" stroke-linejoin="round"/>')
        lx, lv = X(len(data) - 1), data[-1][1]
        out.append(f'<circle cx="{lx:.1f}" cy="{Y(lv):.1f}" r="4" '
                   f'fill="var({var})"/>')
        out.append(f'<text x="{lx+9:.1f}" y="{Y(lv)+4:.1f}" font-size="11.5" '
                   f'fill="var(--text-primary)">{html.escape(label)} '
                   f'<tspan fill="var(--text-secondary)">{lv:+.0f}</tspan></text>')
        for i, (_, v) in enumerate(data):
            out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="9" '
                       f'fill="transparent" class="hv" '
                       f'data-t="{html.escape(label)}: {v:+.1f} bps net">'
                       f'</circle>')
    out.append(f'<text x="{ml}" y="{height-8}" font-size="10.5" '
               f'fill="var(--text-muted)">scan cycles (oldest to newest)</text>')
    out.append("</svg>")
    return "".join(out)


def render(engine) -> str:
    st = engine.store
    realized = st.realized_by_strategy()
    caps = alloc_mod.allocations(engine.bankroll, realized)
    cycles = st.recent_cycles()
    decisions = st.recent_decisions(30)
    series_raw = st.best_net_series()
    series = {k: [(c, v) for c, v in v_] for k, v_ in series_raw.items()}
    gate = live_gate(engine.cfg)
    equity = engine.broker.equity
    pnl = engine.broker.realized_pnl
    last = cycles[-1] if cycles else {}
    best_now = max((d["net_bps"] for d in decisions), default=None)
    n_fills = len(engine.broker.fills)

    tiles = [
        ("Realized P&L", _fmt_money(pnl),
         f"{n_fills} fill{'' if n_fills == 1 else 's'} · fees always charged"),
        ("Opportunities scanned", str(last.get("n_opportunities", 0)),
         "this cycle, priced net of fees"),
        ("Cleared the fee wall", str(last.get("n_tradeable", 0)),
         "only these are ever executed"),
        ("Best net edge", "—" if best_now is None else f"{best_now:+.1f} bps",
         "needs > 0 to be a real arb"),
        ("Cycles run", str(len(cycles)), "on live venue data"),
    ]
    tiles_html = "".join(
        f"<div class='tile'><div class='tlabel'>{html.escape(a)}</div>"
        f"<div class='tvalue mono'>{html.escape(b)}</div>"
        f"<div class='tnote'>{html.escape(c)}</div></div>" for a, b, c in tiles)

    alloc_rows = []
    for key, label, var in SERIES:
        cap = caps.get(key, 0.0)
        pct = 0 if not engine.bankroll else cap / engine.bankroll * 100
        r = realized.get(key, 0.0) or 0.0
        alloc_rows.append(
            f"<tr><td><span class='sw' style='background:var({var})'></span> "
            f"{html.escape(label)}</td>"
            f"<td class='mono'>{_fmt_money(cap)}</td>"
            f"<td style='width:210px'><div class='bar'><i style='width:{pct:.0f}%;"
            f"background:var({var})'></i></div></td>"
            f"<td class='mono'>{_fmt_money(r)}</td></tr>")
    deployed = sum(f.notional for f in engine.broker.fills)
    # Caps are CEILINGS, not commitments: uncommitted cash is what
    # is not currently in a position.
    reserve = max(0.0, engine.broker.cash - deployed)

    dec_rows = "".join(
        f"<tr><td>{html.escape((d['label'] or '')[:64])}</td>"
        f"<td>{html.escape(d['strategy'])}</td>"
        f"<td class='mono'>{d['gross_bps']:+.1f}</td>"
        f"<td class='mono'>{d['fee_bps']:.0f}</td>"
        f"<td class='mono'>{d['net_bps']:+.1f}</td>"
        f"<td class='{'fill' if d['action']=='fill' else 'skip'}'>"
        f"{'● FILL' if d['action']=='fill' else '○ skip'}</td>"
        f"<td style='text-align:left;color:var(--text-secondary)'>"
        f"{html.escape((d['reason'] or '')[:72])}</td></tr>"
        for d in decisions) or "<tr><td colspan='7'>No decisions yet.</td></tr>"

    gate_items = "".join(f"<li>● {html.escape(b)}</li>" for b in gate["blocked_by"])
    legend = "".join(
        f"<span><i class='sw' style='background:var({v})'></i>{html.escape(name)}</span>"
        for _, name, v in SERIES)

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crypto arbitrage — paper desk</title><style>{CSS}</style></head><body>
<div class="wrap">
<h1>Crypto arbitrage desk</h1>
<div class="sub">Live books from Coinbase, Kraken, KuCoin and Polymarket ·
every edge priced net of fees · paper capital only</div>

<div class="card"><div class="row">
 <div>
  <div class="tlabel">Simulated equity</div>
  <div class="hero mono">{_fmt_money(equity)}</div>
  <div class="heronote">started at {_fmt_money(engine.bankroll)} ·
   {_fmt_money(deployed)} ever deployed · {_fmt_money(reserve)} uncommitted cash</div>
 </div>
 <div style="flex:0 0 260px">
  <div class="tlabel" style="margin-bottom:6px">Mode</div>
  <div><span class="pill on">● SIM — paper money</span>
   <span class="pill off">REAL — locked</span></div>
  <div class="tnote" style="margin-top:8px">Real money needs every item in
   the go-live gate below to clear. The browser cannot arm it.</div>
 </div>
</div></div>

<div class="card"><div class="tiles">{tiles_html}</div></div>

<div class="card">
 <h2>Distance to a real arbitrage</h2>
 <div class="legend">{legend}</div>
 {_chart(series)}
 <div class="tnote" style="margin-top:6px">Each line is the best opportunity
  that strategy found in that cycle, after subtracting venue fees and
  structural costs. A line below the dashed rule means the fee wall is
  bigger than the price dislocation — taking that trade loses money.</div>
</div>

<div class="card">
 <h2>Allocation of the {_fmt_money(engine.bankroll)}</h2>
 <div class="scroll"><table>
  <tr><th>Strategy</th><th>Cap (ceiling)</th><th>Share of bankroll</th><th>Realized</th></tr>
  {''.join(alloc_rows)}
  <tr><td><b>Uncommitted cash</b></td><td class="mono">{_fmt_money(reserve)}</td>
   <td></td><td class="mono">{_fmt_money(0)}</td></tr>
 </table></div>
 <div class="tnote" style="margin-top:8px">Caps come from a structural risk
  prior — a bundle settles at exactly $1 on one venue, triangular runs three
  legs on one venue, cross-exchange needs funded inventory on two venues plus
  USD/USDT risk — then shrink with measured losses and never grow past the
  prior. Capital is only committed when an edge clears costs.</div>
</div>

<div class="card">
 <h2>Decisions</h2>
 <div class="scroll"><table>
  <tr><th>Opportunity</th><th>Strategy</th><th>Gross bps</th><th>Fees bps</th>
   <th>Net bps</th><th>Action</th><th>Why</th></tr>
  {dec_rows}
 </table></div>
</div>

<div class="card gate">
 <h2>Go-live gate — what stands between this and real money</h2>
 <ul>{gate_items}</ul>
 <div class="tnote">Keys belong in <code>.env</code> only (gitignored), never
  in config or logs. Arming also requires a live executor, which is
  deliberately not written yet.</div>
</div>
</div>
<div class="tt" id="tt"></div>
<script>
const tt=document.getElementById('tt');
document.querySelectorAll('.hv').forEach(el=>{{
  el.addEventListener('mouseenter',e=>{{tt.textContent=el.dataset.t;tt.style.opacity=1;}});
  el.addEventListener('mousemove',e=>{{tt.style.left=(e.clientX+14)+'px';
    tt.style.top=(e.clientY-10)+'px';}});
  el.addEventListener('mouseleave',()=>{{tt.style.opacity=0;}});
}});
</script></body></html>"""


def render_to_file(engine, path: str) -> str:
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(render(engine))
    return path
