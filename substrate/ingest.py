"""Real-data ingestion. Drop bot logs / Kalshi exports into this CSV schema:

event_id,domain,close_time,resolve_time,market_prob,baseline_prob,outcome
KXHIGHNY-26AUG15,weather,1755200000,1755300000,0.47,0.44,1
NBA-BOS-MIA-0312,sports,1741800000,1741810000,0.55,0.58,0

- market_prob   : P(YES) implied at YOUR decision time (pre-close snapshot, not settle)
- baseline_prob : conventional model at same instant (bot forecast / GenCast blend /
                  climatology). Never computed with post-decision information.
- outcome       : 0/1 after resolution; leave blank for open events.
Channel calls (ARV/presentiment) are sealed separately via CommitLedger BEFORE
resolve_time; ManualChannel reads them back by event_id.
"""
import csv
from engine import Event

def load_events_csv(path):
    out = []
    with open(path) as f:
        for r in csv.DictReader(f):
            out.append(Event(
                event_id=r["event_id"], domain=r["domain"],
                close_time=float(r["close_time"]), resolve_time=float(r["resolve_time"]),
                market_prob=float(r["market_prob"]), baseline_prob=float(r["baseline_prob"]),
                outcome=(int(r["outcome"]) if r.get("outcome","") not in ("", None) else None)))
    return out
