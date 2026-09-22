"""Account equity for the sim (paper) and real (live) books.

The staking bankroll in config is a static number — it never moves as the bot
wins or loses, and `PaperExchange.balance` lives only in the running process
and resets on restart. Neither can answer "what is the $100 worth right now",
so this module derives equity from what is actually persisted: settled PnL,
open stakes, and the last recorded quote for each open market.

    cash   = starting balance + realized PnL - stake tied up in open bets
    marked = open size valued at the last quote (YES at mid, NO at 1 - mid)
    equity = cash + marked

Marking to the last recorded quote means equity moves between settlements
instead of stepping only when a market resolves. It is a mark, not a
liquidation value: `positions.evaluate_exit` walks the real book when it
actually closes something.
"""

from __future__ import annotations

# Dashboard-facing account names and the `bets.mode` value each one reads.
ACCOUNTS = {"sim": "paper", "real": "live"}


def _mark_price(row: dict, snapshot: dict | None) -> float:
    """Value of one share at the last recorded quote, falling back to the
    entry price when the market has never been quoted since."""
    if snapshot is None or snapshot.get("bid") is None or snapshot.get("ask") is None:
        return float(row.get("entry_price") or 0.0)
    mid = (float(snapshot["bid"]) + float(snapshot["ask"])) / 2.0
    return mid if (row.get("side") or "").upper() == "YES" else 1.0 - mid


def account_equity(store, account: str, starting_balance: float) -> dict:
    """Current cash / exposure / equity for one account."""
    mode = ACCOUNTS.get(account, account)
    # Filter by mode in SQL, not after truncation: a Python-side filter over
    # the newest N rows silently drops this account's older bets once the
    # table passes N, and a realized-PnL total that loses rows is a permanent
    # divergence rather than a display glitch.
    realized = 0.0
    settled_n = 0
    for r in store.settled_bets(limit=1_000_000, mode=mode):
        realized += float(r.get("pnl") or 0.0)
        settled_n += 1

    open_stake = marked = 0.0
    open_n = 0
    for r in store.open_bets_for(mode):
        open_n += 1
        open_stake += float(r.get("stake") or 0.0)
        snap = store.last_snapshot(r["market_id"])
        marked += float(r.get("size") or 0.0) * _mark_price(r, snap)

    cash = starting_balance + realized - open_stake
    return {
        "account": account,
        "starting_balance": round(starting_balance, 2),
        "cash": round(cash, 2),
        "exposure": round(open_stake, 2),
        "marked_value": round(marked, 2),
        "equity": round(cash + marked, 2),
        "realized_pnl": round(realized, 2),
        "unrealized_pnl": round(marked - open_stake, 2),
        "return_pct": (round(100.0 * (cash + marked - starting_balance)
                             / starting_balance, 2) if starting_balance else 0.0),
        "open_positions": open_n,
        "settled_bets": settled_n,
    }


def snapshot_equity(store, account: str, starting_balance: float) -> dict:
    """Compute equity and persist one point of the curve."""
    eq = account_equity(store, account, starting_balance)
    store.record_equity(account, eq["cash"], eq["exposure"], eq["equity"],
                        eq["realized_pnl"], eq["open_positions"])
    return eq
