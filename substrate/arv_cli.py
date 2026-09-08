"""ARV session runner (handoff milestone 3): real image pool, sealed
double-blind workflow, SQLite persistence of the CommitLedger.

New wiring only — arv.py / engine.py are untouched; this drives
`ARVProtocol` across separate CLI invocations by persisting trial state.

Blinding contract (unchanged from arv.py):
  * `open` seals the hidden image<->YES assignment BEFORE any human sees
    anything, and prints nothing but the session id.
  * `transcribe` seals the viewer's impressions before judging.
  * `judge` shows ONLY the two image paths (id-sorted, assignment-agnostic)
    plus the transcript; the call is computed through the sealed assignment
    inside the code path and sealed.
  * `resolve` reveals the outcome; the feedback image is shown unless the
    trial landed in the pre-registered ablation subset (QRNG-drawn at open
    with p=0.2 per PROTOCOL_v1).

Image pool: a directory of image files (jpg/png/gif/webp). An optional
`pool.json` manifest ({filename: [tags...]}) enables tag-orthogonal pair
selection; without it any two distinct images qualify.

Usage:
  python3 arv_cli.py open       --event-id KXHIGHNY-26SEP09-B79.5 --pool ./images
  python3 arv_cli.py transcribe --event-id ... --tags "water,angular,bright"
  python3 arv_cli.py judge      --event-id ...            # prints pair + transcript
  python3 arv_cli.py judge      --event-id ... --score-a 0.7 --score-b 0.2
  python3 arv_cli.py resolve    --event-id ... --outcome 1
  python3 arv_cli.py list | score
  python3 arv_cli.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from arv import ARVProtocol, ARVTrial, _u64  # noqa: E402
from engine import CommitLedger, TestMartingale, channel_prob  # noqa: E402

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
ABLATION_P = 0.2          # pre-registered feedback-ablation share (PROTOCOL_v1)
DELTA = 0.04              # committed channel mapping delta (PROTOCOL_v1)

PROTOCOL = {
    "workflow": "arv_cli", "delta": DELTA, "ablation_p": ABLATION_P,
    "gate": {"type": "e-process", "threshold": 20.0},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    t REAL NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    event_id TEXT PRIMARY KEY,
    ablation INTEGER NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class FileImageBank:
    """arv.ImageBank-compatible bank over a directory of real image files."""

    def __init__(self, pool_dir: str):
        self.pool_dir = pool_dir
        manifest = {}
        mpath = os.path.join(pool_dir, "pool.json")
        if os.path.exists(mpath):
            with open(mpath) as fh:
                manifest = json.load(fh)
        files = sorted(
            f for f in os.listdir(pool_dir)
            if f.lower().endswith(IMAGE_EXTS)
        )
        if len(files) < 2:
            raise SystemExit(f"pool {pool_dir!r} needs >= 2 image files")
        self.images = {f: frozenset(manifest.get(f, [])) for f in files}

    def orthogonal_pair(self, feed=os.urandom, max_overlap=1, tries=800):
        ids = list(self.images)
        for _ in range(tries):
            a = ids[_u64(feed) % len(ids)]
            b = ids[_u64(feed) % len(ids)]
            if a != b and len(self.images[a] & self.images[b]) <= max_overlap:
                return a, b
        raise RuntimeError("no orthogonal pair found")

    def path(self, image_id: str) -> str:
        return os.path.join(self.pool_dir, image_id)


class SqliteLedger(CommitLedger):
    """CommitLedger whose sealed records persist to SQLite."""

    def __init__(self, conn: sqlite3.Connection, protocol: dict):
        super().__init__(protocol)
        self.conn = conn
        row = conn.execute("SELECT value FROM meta WHERE key='protocol_hash'").fetchone()
        if row is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('protocol_hash', ?)",
                         (self.protocol_hash,))
            conn.commit()
        elif row[0] != self.protocol_hash:
            raise SystemExit(
                "protocol hash mismatch with existing ledger DB — parameters "
                "changed; use a fresh DB (evidence restarts per PROTOCOL_v1 §7)"
            )

    def seal(self, event_id, probs, t=None):
        h = super().seal(event_id, probs, t=t)
        rec = self.records[-1]
        self.conn.execute(
            "INSERT INTO ledger (event_id, payload, t, hash) VALUES (?,?,?,?)",
            (rec["event_id"], json.dumps(rec["probs"], sort_keys=True), rec["t"], h),
        )
        self.conn.commit()
        return h


class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def save(self, tr: ARVTrial, ablation: bool):
        self.conn.execute(
            "INSERT OR REPLACE INTO sessions (event_id, ablation, state) VALUES (?,?,?)",
            (tr.event_id, int(ablation), json.dumps(tr.__dict__)),
        )
        self.conn.commit()

    def load_all(self) -> dict:
        out = {}
        for eid, abl, state in self.conn.execute(
                "SELECT event_id, ablation, state FROM sessions"):
            d = json.loads(state)
            tr = ARVTrial(event_id=d["event_id"])
            tr.__dict__.update(d)
            out[eid] = (tr, bool(abl))
        return out


def _protocol(store: Store, bank, feedback=True) -> ARVProtocol:
    ledger = SqliteLedger(store.conn, PROTOCOL)
    proto = ARVProtocol(bank, ledger, feedback=feedback)
    proto.trials = {eid: tr for eid, (tr, _) in store.load_all().items()}
    return proto


def cmd_open(args):
    store = Store(args.db)
    if args.event_id in store.load_all():
        raise SystemExit(f"session {args.event_id!r} already exists")
    bank = FileImageBank(args.pool)
    proto = _protocol(store, bank)
    tr, _pair = proto.open_trial(args.event_id)
    ablation = args.ablation or (_u64(os.urandom) % 1000 < ABLATION_P * 1000)
    store.save(tr, ablation)
    # Deliberately print NOTHING about the pair or assignment.
    print(f"session {args.event_id} opened and sealed "
          f"(assignment {tr.assignment_hash[:12]}…). Viewer may now transcribe.")


def cmd_transcribe(args):
    store = Store(args.db)
    sessions = store.load_all()
    if args.event_id not in sessions:
        raise SystemExit("no such session")
    tr, abl = sessions[args.event_id]
    if tr.transcript_hash:
        raise SystemExit("transcript already sealed")
    bank = FileImageBank(args.pool)
    proto = _protocol(store, bank)
    tags = [t.strip().lower() for t in args.tags.split(",") if t.strip()]
    if not tags:
        raise SystemExit("empty transcript")
    h = proto.record_transcript(args.event_id, tags)
    store.save(proto.trials[args.event_id], abl)
    print(f"transcript sealed ({h[:12]}…). Hand the session to the blind judge.")


def cmd_judge(args):
    store = Store(args.db)
    sessions = store.load_all()
    if args.event_id not in sessions:
        raise SystemExit("no such session")
    tr, abl = sessions[args.event_id]
    if not tr.transcript_hash:
        raise SystemExit("transcript not sealed yet")
    if tr.judge_scores:
        raise SystemExit("already judged")
    bank = FileImageBank(args.pool)
    a_id, b_id = sorted([tr.img_yes, tr.img_no])   # id order, assignment-agnostic
    if args.score_a is None or args.score_b is None:
        print("JUDGE VIEW (blind — no event context, no assignment):")
        print(f"  image A: {bank.path(a_id)}")
        print(f"  image B: {bank.path(b_id)}")
        print(f"  transcript: {', '.join(tr.transcript)}")
        print("Re-run with --score-a S --score-b S (similarity of transcript to each).")
        return
    proto = _protocol(store, bank)
    call = proto.judge(args.event_id,
                       lambda _t, _ta, _tb: (args.score_a, args.score_b))
    store.save(proto.trials[args.event_id], abl)
    print(f"call sealed: {'YES' if call == 1 else 'NO'} "
          f"(channel prob vs 0.5 null: {channel_prob(0.5, call, DELTA):.2f})")


def cmd_resolve(args):
    store = Store(args.db)
    sessions = store.load_all()
    if args.event_id not in sessions:
        raise SystemExit("no such session")
    tr, abl = sessions[args.event_id]
    if not tr.judge_scores:
        raise SystemExit("not judged yet — resolve after a sealed call")
    if tr.outcome is not None:
        raise SystemExit("already resolved")
    bank = FileImageBank(args.pool)
    proto = _protocol(store, bank)
    proto.feedback = not abl
    tr = proto.resolve(args.event_id, args.outcome)
    store.save(tr, abl)
    print(f"resolved: outcome={args.outcome} hit={tr.hit}")
    if tr.feedback_img:
        print(f"FEEDBACK — show the viewer this image now: {bank.path(tr.feedback_img)}")
    else:
        print("ablation trial: no feedback shown (pre-registered subset)")


def cmd_list(args):
    store = Store(args.db)
    for eid, (tr, abl) in sorted(store.load_all().items()):
        stage = ("resolved" if tr.outcome is not None else
                 "judged" if tr.judge_scores else
                 "transcribed" if tr.transcript_hash else "open")
        extra = f" hit={tr.hit}" if tr.outcome is not None else ""
        print(f"{eid:40s} {stage:12s} ablation={abl}{extra}")


def cmd_score(args):
    store = Store(args.db)
    resolved = [(tr, abl) for tr, abl in store.load_all().values()
                if tr.outcome is not None]
    resolved.sort(key=lambda x: x[0].t_resolved)
    if not resolved:
        print("no resolved trials yet")
        return
    hits = sum(1 for tr, _ in resolved if tr.hit)
    tm = TestMartingale(threshold=20.0)
    for tr, _ in resolved:
        q = channel_prob(0.5, tr.call, DELTA)
        tm.update(q, 0.5, 1 if tr.outcome == 1 else 0)
    n = len(resolved)
    print(f"n={n} hits={hits} hit_rate={hits / n:.3f}")
    print(f"e-process vs coin null: E={tm.E:.3f} (max {max(tm.path):.3f}, "
          f"certify at >= 20) certified={tm.certified}")
    abls = [tr for tr, abl in resolved if abl]
    if abls:
        ah = sum(1 for tr in abls if tr.hit)
        print(f"ablation subset: n={len(abls)} hit_rate={ah / len(abls):.3f}")


def self_test() -> int:
    import tempfile

    fails = []

    def ck(name, cond):
        print(("PASS " if cond else "FAIL ") + name)
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        pool = os.path.join(tmp, "pool")
        os.makedirs(pool)
        for i in range(6):
            with open(os.path.join(pool, f"img{i}.png"), "wb") as fh:
                fh.write(b"\x89PNG\r\n" + bytes([i]))
        db = os.path.join(tmp, "arv.sqlite")

        ns = argparse.Namespace(db=db, pool=pool, event_id="EV1", ablation=False)
        cmd_open(ns)
        store = Store(db)
        tr, _ = store.load_all()["EV1"]
        ck("assignment sealed at open", len(tr.assignment_hash) == 64)
        ck("pair drawn from real files", tr.img_yes.endswith(".png")
           and tr.img_no.endswith(".png") and tr.img_yes != tr.img_no)
        n_ledger = store.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        ck("ledger row persisted", n_ledger == 1)

        cmd_transcribe(argparse.Namespace(db=db, pool=pool, event_id="EV1",
                                          tags="water, angular ,bright"))
        tr, _ = Store(db).load_all()["EV1"]
        ck("transcript sealed", len(tr.transcript_hash) == 64
           and tr.transcript == ["angular", "bright", "water"])

        cmd_judge(argparse.Namespace(db=db, pool=pool, event_id="EV1",
                                     score_a=0.9, score_b=0.1))
        tr, _ = Store(db).load_all()["EV1"]
        a_id = sorted([tr.img_yes, tr.img_no])[0]
        expected_call = +1 if a_id == tr.img_yes else -1
        ck("call routed through sealed assignment", tr.call == expected_call)

        cmd_resolve(argparse.Namespace(db=db, pool=pool, event_id="EV1", outcome=1))
        tr, _ = Store(db).load_all()["EV1"]
        ck("hit computed", tr.hit == (tr.call == 1))
        ck("feedback = actual-outcome image", tr.feedback_img == tr.img_yes)
        n_ledger = Store(db).conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        ck("3 sealed records (assign/transcript/call)", n_ledger == 3)

        try:
            cmd_resolve(argparse.Namespace(db=db, pool=pool, event_id="EV1", outcome=0))
            ck("double-resolve rejected", False)
        except SystemExit:
            ck("double-resolve rejected", True)

    print(("\n%d failure(s)" % len(fails)) if fails else "\nAll self-tests passed.")
    return 1 if fails else 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    for name in ("open", "transcribe", "judge", "resolve", "list", "score"):
        sp = sub.add_parser(name)
        sp.add_argument("--db", default="arv_sessions.sqlite")
        sp.add_argument("--pool", default="images")
        if name in ("open", "transcribe", "judge", "resolve"):
            sp.add_argument("--event-id", required=True)
        if name == "open":
            sp.add_argument("--ablation", action="store_true",
                            help="force this trial into the no-feedback arm")
        if name == "transcribe":
            sp.add_argument("--tags", required=True)
        if name == "judge":
            sp.add_argument("--score-a", type=float, default=None)
            sp.add_argument("--score-b", type=float, default=None)
        if name == "resolve":
            sp.add_argument("--outcome", type=int, choices=(0, 1), required=True)
    args = p.parse_args()
    if args.self_test:
        sys.exit(self_test())
    if not args.cmd:
        p.print_help()
        sys.exit(1)
    {"open": cmd_open, "transcribe": cmd_transcribe, "judge": cmd_judge,
     "resolve": cmd_resolve, "list": cmd_list, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
