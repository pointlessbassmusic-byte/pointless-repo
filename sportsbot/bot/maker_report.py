"""Read-out for the maker fill-share test (docs/MAKER_TEST.md).

Every order the executor places carries, in its `raw`: the arm that
posted it, the book it saw, when it was placed, when it first filled,
and -- once it left the book -- `tape_through`, the size the public tape
printed through its level while it rested (the paper venue's fill rule
applied after the fact). From that and the bets table this module
answers the three pre-registered questions:

1. fill share: real fills over what the tape rule would have credited,
   order by order and contract by contract;
2. CLV per fill against the venue close (and the sharp close when the
   harness has it), with a market-clustered bootstrap interval;
3. adverse selection by wait: CLV per fill for fills that arrived in
   under 30 s, 30-120 s, and later.

Paper orders carry both `tape_filled` (what the venue credited) and
`tape_through` (the shadow); they agree by construction, which the
report prints as a self-check. Nothing here changes any decision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sportsbot.bot.portfolio import parse_arm
from sportsbot.signals.sharp import cluster_bootstrap_mean

WAIT_BUCKETS = (("< 30 s", 0.0, 30.0), ("30–120 s", 30.0, 120.0), ("> 120 s", 120.0, float("inf")))
GATE_FILLS = 200


def _signal_of(arm: Optional[str]) -> Optional[str]:
    try:
        return parse_arm(arm)[1] if arm else None
    except ValueError:
        return None


def _side_close(close: Optional[float], side: str) -> Optional[float]:
    if close is None:
        return None
    return float(close) if side == "yes" else 1.0 - float(close)


def _ci(vals, clusters):
    return cluster_bootstrap_mean(list(vals), list(clusters), n_boot=1000, seed=11)


def _closes(bets: list[dict]) -> dict[str, dict]:
    closes: dict[str, dict] = {}
    for b in bets:
        if b.get("closing_price") is not None or b.get("sharp_closing_price") is not None:
            closes.setdefault(b["market_id"], {})
            if b.get("closing_price") is not None:
                closes[b["market_id"]]["venue"] = float(b["closing_price"])
            if b.get("sharp_closing_price") is not None:
                closes[b["market_id"]]["sharp"] = float(b["sharp_closing_price"])
    return closes


def _ts(iso: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except ValueError:
        return None


def _fill_rows(orders: list[dict], closes: dict[str, dict]) -> list[dict]:
    """One row per filled order: price, CLV at each close, wait, fill time."""
    rows = []
    for o in orders:
        if not (o["filled"] or 0) > 0:
            continue
        raw = o.get("raw") or {}
        c = closes.get(o["market_id"], {})
        venue = _side_close(c.get("venue"), o["side"])
        sharp = _side_close(c.get("sharp"), o["side"])
        wait = raw.get("rested_seconds")
        if wait is None and raw.get("first_fill_at") and raw.get("placed_at"):
            try:
                wait = (datetime.fromisoformat(raw["first_fill_at"])
                        - datetime.fromisoformat(raw["placed_at"])).total_seconds()
            except ValueError:
                wait = None
        rows.append({"market": o["market_id"], "price": float(o["price"]), "side": o["side"],
                     "venue_clv": None if venue is None else venue - float(o["price"]),
                     "sharp_clv": None if sharp is None else sharp - float(o["price"]),
                     "wait": None if wait is None else float(wait),
                     "filled_ts": _ts(raw.get("first_fill_at")) or _ts(raw.get("placed_at"))})
    return rows


def _bounds(shadowed: list[dict]) -> dict:
    out = {}
    for key, name in (("tape_through", "through"), ("tape_queue", "queue"), ("tape_strict", "strict")):
        rows = [o for o in shadowed if (o.get("raw") or {}).get(key) is not None]
        ref = sum(float(o["raw"][key]) for o in rows)
        real = sum(float(o["filled"] or 0) for o in rows)
        out[name] = {"orders": len(rows), "reference_contracts": round(ref, 2),
                     "real_contracts": round(real, 2),
                     "real_over_reference": (real / ref) if ref else None}
    return out


def fill_rows(store, signal: str = "touch", mode: Optional[str] = None) -> list[dict]:
    """Filled orders of one signal with their CLV, as the maker report
    grades them (other read-outs, e.g. the news-veto test, reuse this so
    the two can never disagree on what a fill's CLV was)."""
    orders = [o for o in store.all_orders() if _signal_of((o.get("raw") or {}).get("arm")) == signal]
    bets = [b for b in store.all_bets(mode) if _signal_of(b.get("arm")) == signal]
    return _fill_rows(orders, _closes(bets))


def maker_report(store, signal: str = "touch", mode: Optional[str] = None) -> dict:
    orders = [o for o in store.all_orders() if _signal_of((o.get("raw") or {}).get("arm")) == signal]
    bets = [b for b in store.all_bets(mode) if _signal_of(b.get("arm")) == signal]
    closes = _closes(bets)

    rep: dict = {"signal": signal, "orders": len(orders), "fills": 0}
    if not orders:
        rep["bets"] = len(bets)
        return rep

    posted = sum(float(o["size"] or 0) for o in orders)
    filled = sum(float(o["filled"] or 0) for o in orders)
    n_filled = sum(1 for o in orders if (o["filled"] or 0) > 0)
    shadowed = [o for o in orders if (o.get("raw") or {}).get("tape_through") is not None]
    tape_contracts = sum(float(o["raw"]["tape_through"]) for o in shadowed)
    tape_orders = sum(1 for o in shadowed if float(o["raw"]["tape_through"]) > 0)
    filled_shadowed = sum(float(o["filled"] or 0) for o in shadowed)
    n_filled_shadowed = sum(1 for o in shadowed if (o["filled"] or 0) > 0)
    self_check = sum(1 for o in shadowed
                     if o["raw"].get("tape_filled") is not None
                     and abs(float(o["raw"]["tape_filled"]) - float(o["raw"]["tape_through"])) > 1e-6)
    rep.update({
        "contracts_posted": round(posted, 2), "contracts_filled": round(filled, 2),
        "orders_filled": n_filled, "orders_shadowed": len(shadowed),
        "tape_would_fill_orders": tape_orders, "tape_would_fill_contracts": round(tape_contracts, 2),
        "fill_share_orders": (n_filled_shadowed / tape_orders) if tape_orders else None,
        "fill_share_contracts": (filled_shadowed / tape_contracts) if tape_contracts else None,
        "paper_self_check_mismatches": self_check,
        # Where real fills sit between the bounds (docs/EDGE_VERDICT Result
        # 15 addendum 3): `strict` = the whole level traded through (any
        # queue position fills), `queue` = at-or-through size beyond what
        # sat in front at posting. Real fills near `through` mean we were
        # at the front; near `queue` or `strict`, behind it.
        "bounds": _bounds(shadowed),
        "by_status": {},
        "orphaned": sum(1 for o in orders if (o.get("raw") or {}).get("orphaned")),
    })
    for o in orders:
        rep["by_status"][o["status"]] = rep["by_status"].get(o["status"], 0) + 1

    # CLV per filled order, by close, with wait buckets
    rows = _fill_rows(orders, closes)
    rep["fills"] = len(rows)
    graded = [r for r in rows if r["venue_clv"] is not None]
    rep["graded_venue"] = len(graded)
    rep["clv_venue"] = _ci([r["venue_clv"] for r in graded], [r["market"] for r in graded]) if graded else None
    sg = [r for r in rows if r["sharp_clv"] is not None]
    rep["graded_sharp"] = len(sg)
    rep["clv_sharp"] = _ci([r["sharp_clv"] for r in sg], [r["market"] for r in sg]) if sg else None
    rep["by_wait"] = {}
    for label, lo, hi in WAIT_BUCKETS:
        sel = [r for r in graded if r["wait"] is not None and lo <= r["wait"] < hi]
        rep["by_wait"][label] = {"n": len(sel),
                                 "clv": _ci([r["venue_clv"] for r in sel], [r["market"] for r in sel]) if sel else None}
    ci = rep["clv_venue"]
    rep["gate"] = {"fills": len(graded), "needed": GATE_FILLS,
                   "lower_bound_positive": bool(ci and ci.get("lo") is not None and ci["lo"] > 0)}
    rep["pairs"] = locked_pairs(orders)
    rep["realised_pnl"] = round(sum(float(b["pnl"] or 0) for b in bets if b.get("pnl") is not None), 2)
    rep["bets"] = len(bets)
    return rep


def locked_pairs(orders: list[dict]) -> dict:
    """Both sides filled on one market is a complete set: it pays $1 at
    settlement whoever wins, so those contracts earn 1 - (yes price + no
    price) regardless of outcome (the arbitrage desk's "bundle", executed
    as a maker). Contracts filled on one side only carry direction and
    are where adverse selection lives. Pairing is per market at the
    volume-weighted fill prices of each side."""
    by_market: dict[str, dict] = {}
    for o in orders:
        f = float(o.get("filled") or 0)
        if f <= 0:
            continue
        m = by_market.setdefault(o["market_id"], {"yes": [0.0, 0.0], "no": [0.0, 0.0]})
        side = m["yes"] if o["side"] == "yes" else m["no"]
        side[0] += f
        side[1] += f * float(o["price"])
    paired = naked_yes = naked_no = 0.0
    locked_dollars = 0.0
    markets_paired = 0
    edges = []
    for m in by_market.values():
        (qy, cy), (qn, cn) = m["yes"], m["no"]
        pq = min(qy, qn)
        naked_yes += qy - pq
        naked_no += qn - pq
        if pq > 0:
            markets_paired += 1
            edge = 1.0 - (cy / qy) - (cn / qn)      # per paired contract, before fees
            edges.append((edge, pq))
            paired += pq
            locked_dollars += edge * pq
    return {"markets": len(by_market), "markets_paired": markets_paired,
            "paired_contracts": round(paired, 2),
            "naked_yes_contracts": round(naked_yes, 2), "naked_no_contracts": round(naked_no, 2),
            "locked_edge_per_contract": (round(locked_dollars / paired, 4) if paired else None),
            "locked_dollars": round(locked_dollars, 4)}


def _fmt_ci(d: Optional[dict]) -> str:
    if not d:
        return "—"
    if d.get("lo") is None:
        return f"{d['mean']:+.4f} (n={d['n']})"
    return f"{d['mean']:+.4f} [{d['lo']:+.4f}, {d['hi']:+.4f}] n={d['n']} markets={d['clusters']}"


def _bounds_lines(b: dict) -> list[str]:
    if not b:
        return []
    out = ["  real fills vs the three references (1.0 = real fills match that rule):"]
    for name, label in (("through", "front of queue (upper)"), ("queue", "behind the queue"),
                        ("strict", "level swept (lower)")):
        d = b.get(name) or {}
        r = d.get("real_over_reference")
        out.append(f"    {label:24s} reference {d.get('reference_contracts', 0)} contracts "
                   f"on {d.get('orders', 0)} orders -> real/ref "
                   f"{'—' if r is None else f'{r:.2f}'}")
    return out


def format_maker_report(rep: dict) -> str:
    lines = [f"maker fill-share test, arm signal '{rep['signal']}': {rep['orders']} orders"]
    if not rep["orders"]:
        lines.append("  nothing posted yet")
        return "\n".join(lines)
    share_o = rep["fill_share_orders"]
    share_c = rep["fill_share_contracts"]
    lines += [
        f"  status: {rep['by_status']}"
        + (f"  (orphaned by a restart, no shadow, excluded from the share: {rep['orphaned']})"
           if rep.get("orphaned") else ""),
        f"  contracts posted {rep['contracts_posted']}, filled {rep['contracts_filled']} "
        f"({rep['orders_filled']} orders)",
        f"  tape shadow on {rep['orders_shadowed']} orders: the tape rule would have filled "
        f"{rep['tape_would_fill_orders']} orders / {rep['tape_would_fill_contracts']} contracts",
        f"  fill share: orders {'—' if share_o is None else f'{share_o:.1%}'}, "
        f"contracts {'—' if share_c is None else f'{share_c:.1%}'}"
        f"  (paper self-check mismatches: {rep['paper_self_check_mismatches']})",
        *_bounds_lines(rep.get("bounds") or {}),
        f"  CLV per fill vs venue close: {_fmt_ci(rep['clv_venue'])}",
        f"  CLV per fill vs sharp close: {_fmt_ci(rep['clv_sharp'])}",
    ]
    for label, d in rep["by_wait"].items():
        lines.append(f"    wait {label:9s} {_fmt_ci(d['clv'])}")
    pr = rep.get("pairs") or {}
    if pr.get("markets"):
        le = pr["locked_edge_per_contract"]
        lines.append(f"  locked pairs: {pr['markets_paired']} of {pr['markets']} markets with fills "
                     f"have both sides filled; {pr['paired_contracts']} contracts locked at "
                     f"{'—' if le is None else f'{le * 100:+.2f} pt'} per contract "
                     f"(${pr['locked_dollars']:+.4f} before fees, outcome-independent); naked "
                     f"yes {pr['naked_yes_contracts']} / no {pr['naked_no_contracts']} carry direction")
    g = rep["gate"]
    lines.append(f"  gate: {g['fills']}/{g['needed']} graded fills; interval lower bound "
                 f"{'above' if g['lower_bound_positive'] else 'not above'} zero; "
                 f"realised PnL {rep['realised_pnl']:+.2f} on {rep['bets']} bets")
    return "\n".join(lines)
