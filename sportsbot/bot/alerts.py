"""Operator alerts: one webhook, a handful of events, never in the loop's way.

The arbitrage desk posts fills, halts and a daily digest to a Discord
webhook and the phone sees both desks in one channel. This module does
the same for sportsbot. `SPORTSBOT_DISCORD_WEBHOOK` (any Discord-style
webhook URL; `.env`, never config) turns it on; unset, every call is a
no-op. Posting failures are logged and swallowed: an alert must never
stop a cycle. Repeats of the same text inside `dedupe_seconds` are
dropped so a standing halt does not page every cycle.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

ENV_WEBHOOK = "SPORTSBOT_DISCORD_WEBHOOK"
_last_sent: dict[str, float] = {}


def webhook_url() -> Optional[str]:
    url = os.environ.get(ENV_WEBHOOK, "").strip()
    return url or None


def notify(kind: str, text: str, dedupe_seconds: float = 3600.0,
           http=None, now: Optional[float] = None) -> bool:
    """Post `text` tagged with `kind`. Returns True when a post was made.
    `http` is an object with `.post(url, json=..., timeout=...)` (httpx by
    default); injected by tests."""
    url = webhook_url()
    if not url:
        return False
    now = time.time() if now is None else now
    key = f"{kind}:{text}"
    last = _last_sent.get(key)
    if last is not None and now - last < dedupe_seconds:
        return False
    body = {"content": f"**sportsbot {kind}** — {text}"[:1900]}
    try:
        if http is None:
            import httpx
            http = httpx
        resp = http.post(url, json=body, timeout=10.0)
        status = getattr(resp, "status_code", 200)
        if status >= 400:
            log.warning("alert webhook returned %s", status)
            return False
    except Exception:  # noqa: BLE001 — telemetry never raises
        log.exception("alert webhook failed")
        return False
    _last_sent[key] = now
    return True


def reset() -> None:
    """Forget the dedupe memory (tests)."""
    _last_sent.clear()


def touch_today(store) -> dict:
    """Today's touch orders from the orders table: posted / filled / cancelled."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    out = {"posted": 0, "filled": 0, "canceled": 0}
    for o in store.all_orders():
        raw = o.get("raw") or {}
        if not str(raw.get("arm", "")).split("/")[1:2] == ["touch"]:
            continue
        if not str(raw.get("placed_at", o.get("ts", "")))[:10] == today:
            continue
        out["posted"] += 1
        if (o.get("filled") or 0) > 0:
            out["filled"] += 1
        if o.get("status") == "canceled":
            out["canceled"] += 1
    return out


def digest_text(store, account: str, mode: str, starting_balance: float,
                touch: dict | None = None, edge_rows: list[dict] | None = None,
                extra: list[str] | None = None) -> str:
    """The daily three lines: book, touch arm, distance to the bar."""
    from sportsbot.bot.ledger import account_equity

    eq = account_equity(store, account, starting_balance)
    settled = store.settled_bets(limit=1_000_000, mode=mode)
    clvs = []
    for b in settled:
        if b.get("closing_price") is None:
            continue
        close = float(b["closing_price"])
        side_close = close if b["side"] == "yes" else 1.0 - close
        clvs.append(side_close - float(b["entry_price"]))
    mean_clv = (sum(clvs) / len(clvs)) if clvs else None
    lines = [f"{account}/{mode}: equity ${eq['equity']:.2f}, realised ${eq['realized_pnl']:+.2f}, "
             f"{eq['open_positions']} open, {eq['settled_bets']} settled"
             + (f", mean CLV {mean_clv * 100:+.2f} pt on {len(clvs)}" if mean_clv is not None else "")]
    if touch:
        lines.append(f"touch today: posted {touch.get('posted', 0)}, filled "
                     f"{touch.get('filled', 0)}, cancelled {touch.get('canceled', 0)}")
    if edge_rows:
        parts = [f"{r['sport']} best {float(r['best']) * 100:+.2f} vs bar "
                 f"{float(r['bar']) * 100:.1f} pt ({r.get('priced', 0)} priced)"
                 for r in edge_rows if r.get("best") is not None]
        if parts:
            lines.append("distance to bar: " + "; ".join(parts))
    lines.extend(extra or [])
    return "\n".join(lines)
