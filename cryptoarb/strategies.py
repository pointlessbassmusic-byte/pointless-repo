"""Candidate arbitrage strategies. Every one prices NET of all known costs.

Design rule (from this repo's own falsification record, Jul 2026): a strategy
that looks profitable only because a cost was omitted is not a strategy. Each
detector therefore subtracts venue fees, the observed spread it must cross,
and any structural haircut BEFORE reporting an edge — and reports negative
edges too, because "why we did not trade" is the product here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Kraken/Coinbase quote USD; KuCoin quotes USDT. Treating them as one unit
# is a real (small) risk: USDT can trade off peg exactly when you need it.
CROSS_QUOTE_RISK_BPS = 2.0
# Cross-exchange legs cannot be transferred at trade time (chain latency),
# so this strategy is only executable against PRE-POSITIONED inventory on
# both venues — which is why it consumes twice the notional it earns on.
CROSS_NEEDS_INVENTORY = True


@dataclass
class Opportunity:
    strategy: str
    label: str
    gross_bps: float
    fee_bps: float
    other_cost_bps: float
    net_bps: float
    max_notional: float          # executable size at the observed top of book
    legs: list = field(default_factory=list)
    reason: str = ""

    @property
    def tradeable(self) -> bool:
        return self.net_bps > 0.0 and self.max_notional > 0.0


def cross_exchange(snapshot: dict, feebook, min_notional: float = 5.0) -> list:
    """Buy an asset on the cheap venue, sell on the rich one, same instant."""
    out = []
    for asset, tops in snapshot.items():
        best = None
        for bv, bt in tops.items():
            for sv, st in tops.items():
                if bv == sv:
                    continue
                gross = (st.bid - bt.ask) / bt.ask * 10_000
                fee = feebook.round_trip_bps(bv, sv)
                net = gross - fee - CROSS_QUOTE_RISK_BPS
                if best is None or net > best[0]:
                    best = (net, gross, fee, bv, sv, bt, st)
        if best is None:
            continue
        net, gross, fee, bv, sv, bt, st = best
        size = min(bt.ask_size or 0.0, st.bid_size or 0.0)
        notional = size * bt.ask
        out.append(Opportunity(
            strategy="cross_exchange",
            label=f"{asset}: {bv}->{sv}",
            gross_bps=gross, fee_bps=fee, other_cost_bps=CROSS_QUOTE_RISK_BPS,
            net_bps=net, max_notional=max(0.0, notional),
            legs=[{"venue": bv, "side": "buy", "price": bt.ask, "symbol": bt.symbol},
                  {"venue": sv, "side": "sell", "price": st.bid, "symbol": st.symbol}],
            reason=(f"gross {gross:+.1f}bps vs {fee:.0f}bps fees "
                    f"+{CROSS_QUOTE_RISK_BPS:.0f}bps USD/USDT risk"
                    + ("" if net > 0 else " -> fee wall")),
        ))
    return out


def triangular(tickers: dict, feebook, base: str = "USDT",
               bridge: str = "BTC", mids=None) -> list:
    """Single-venue 3-leg cycle: no transfers, no depeg risk, 3 taker fees."""
    mids = mids or ["ETH", "SOL", "XRP", "ADA", "LTC", "LINK", "AVAX", "DOT", "DOGE"]
    fee = feebook.taker_rate("kucoin")
    fee_bps = fee * 3 * 10_000
    out = []
    bridge_pair = tickers.get(f"{bridge}-{base}")
    if not bridge_pair:
        return out
    for mid in mids:
        a = tickers.get(f"{mid}-{base}")
        b = tickers.get(f"{mid}-{bridge}")
        if not a or not b:
            continue
        try:
            a_ask, a_bid = float(a["sell"]), float(a["buy"])
            b_ask, b_bid = float(b["sell"]), float(b["buy"])
            c_ask, c_bid = float(bridge_pair["sell"]), float(bridge_pair["buy"])
        except (TypeError, ValueError):
            continue
        if min(a_ask, a_bid, b_ask, b_bid, c_ask, c_bid) <= 0:
            continue
        # base -> mid -> bridge -> base
        fwd = (1.0 / a_ask) * (1 - fee) * b_bid * (1 - fee) * c_bid * (1 - fee)
        # base -> bridge -> mid -> base
        rev = (1.0 / c_ask) * (1 - fee) / b_ask * (1 - fee) * a_bid * (1 - fee)
        for net_mult, path in ((fwd, f"{base}->{mid}->{bridge}->{base}"),
                               (rev, f"{base}->{bridge}->{mid}->{base}")):
            net_bps = (net_mult - 1.0) * 10_000
            out.append(Opportunity(
                strategy="triangular", label=path,
                gross_bps=net_bps + fee_bps, fee_bps=fee_bps, other_cost_bps=0.0,
                net_bps=net_bps,
                # KuCoin level-1 sizes are per-pair; the binding leg is unknown
                # without full books, so cap conservatively at the config floor.
                max_notional=25.0,
                legs=[{"venue": "kucoin", "path": path}],
                reason=(f"3x{fee*10_000:.0f}bps taker = {fee_bps:.0f}bps hurdle"
                        + ("" if net_bps > 0 else " -> cycle does not clear it")),
            ))
    out.sort(key=lambda o: -o.net_bps)
    return out[:6]


def bundle(books: list, feebook) -> list:
    """Polymarket YES+NO < $1: buy both sides, hold to resolution, keep the
    difference. Mechanical — no forecast, no direction, settles at exactly $1.
    """
    fee_bps = feebook.taker_rate("polymarket") * 2 * 10_000
    out = []
    for b in books:
        asks = b.get("asks") or []
        if len(asks) != 2:
            continue
        total = sum(asks)
        gross_bps = (1.0 - total) * 10_000
        net_bps = gross_bps - fee_bps
        pairs = min(b.get("sizes") or [0.0, 0.0])
        out.append(Opportunity(
            strategy="bundle", label=b.get("question") or b.get("market_id", ""),
            gross_bps=gross_bps, fee_bps=fee_bps, other_cost_bps=0.0,
            net_bps=net_bps, max_notional=max(0.0, pairs * total),
            legs=[{"venue": "polymarket", "market_id": b.get("market_id"),
                   "yes_ask": asks[0], "no_ask": asks[1]}],
            reason=(f"YES {asks[0]:.3f} + NO {asks[1]:.3f} = {total:.3f}"
                    + (" -> pays $1" if net_bps > 0 else " (>= $1, no edge)")),
        ))
    out.sort(key=lambda o: -o.net_bps)
    return out[:8]


STRATEGY_KEYS = ("cross_exchange", "triangular", "bundle")
