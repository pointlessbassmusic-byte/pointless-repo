"""The go-live gate from deploy/DEPLOYMENT.md, measured rather than asserted.

`doctor` answers "is this host configured to trade safely". This answers the
other half: "has the paper record earned the right to risk real money". Four
criteria, and the dashboard's real-money panel renders them so the answer is a
readout instead of a judgement call:

1. >= 200 settled paper bets (sample size);
2. positive mean CLV — closing-line value, not PnL, is the earliest honest
   signal that an edge is real;
3. rolling Brier under 0.25 (a coin scores 0.25);
4. fees verified on the venue with one tiny manual trade.

The fourth cannot be measured from the database — it is an operator
attestation, recorded with `sportsbot verify-fees` and shown as outstanding
until then, because silently treating an unmeasurable criterion as satisfied
is how a gate becomes decoration.
"""

from __future__ import annotations

from sportsbot.bot.ledger import ACCOUNTS

MIN_SETTLED_BETS = 200
MAX_BRIER = 0.25
FEES_VERIFIED_KEY = "fees_verified"


def evidence_gate(store, account: str = "sim") -> dict:
    """Measure the four criteria for one account's record."""
    from sportsbot.core.calibration import BetRecord, PerformanceTracker

    mode = ACCOUNTS.get(account, account)
    tracker = PerformanceTracker()
    for r in store.settled_bets(limit=10000):
        if (r.get("mode") or "paper") != mode:
            continue
        tracker.add(BetRecord(
            market_id=r["market_id"], side=r.get("side") or "YES",
            model_prob=r.get("model_prob") or 0.5,
            entry_price=r.get("entry_price") or 0.0,
            stake=r.get("stake") or 0.0,
            closing_price=r.get("closing_price"),
            outcome=r.get("outcome"), pnl=r.get("pnl")))
    s = tracker.summary()

    n_settled = int(s.get("n_settled", 0) or 0)
    mean_clv = s.get("mean_clv")
    brier = s.get("brier")
    fees = store.get_kv(FEES_VERIFIED_KEY)

    criteria = [
        {
            "name": "sample size",
            "target": f">= {MIN_SETTLED_BETS} settled bets",
            "value": f"{n_settled}",
            "ok": n_settled >= MIN_SETTLED_BETS,
            "progress": min(1.0, n_settled / MIN_SETTLED_BETS),
        },
        {
            "name": "mean CLV",
            "target": "> 0 (beating the closing line)",
            "value": "not measured yet" if mean_clv is None else f"{mean_clv:+.4f}",
            "ok": mean_clv is not None and mean_clv > 0,
            "progress": 0.0 if mean_clv is None else (1.0 if mean_clv > 0 else 0.0),
        },
        {
            "name": "rolling Brier",
            "target": f"< {MAX_BRIER} (a coin scores 0.25)",
            "value": "not measured yet" if brier is None else f"{brier:.4f}",
            "ok": brier is not None and brier < MAX_BRIER,
            "progress": 0.0 if brier is None else (1.0 if brier < MAX_BRIER else 0.0),
        },
        {
            "name": "fees verified",
            "target": "one tiny manual trade on the venue",
            "value": (f"verified {fees.get('at', '')[:10]}"
                      if isinstance(fees, dict) else "outstanding"),
            "ok": isinstance(fees, dict) and bool(fees.get("verified")),
            "progress": 1.0 if isinstance(fees, dict) and fees.get("verified") else 0.0,
        },
    ]
    return {
        "account": account,
        "ready": all(c["ok"] for c in criteria),
        "criteria": criteria,
        "summary": s,
    }
