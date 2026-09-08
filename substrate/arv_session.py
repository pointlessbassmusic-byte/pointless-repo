#!/usr/bin/env python3
"""Milestone 3 — ARV session runner: real image pool, CLI workflow, sqlite ledger.

Wraps arv.py's double-blind protocol around a directory of REAL image files and
persists every trial + every sealed record to sqlite. The workflow and its
sealing order are unchanged from arv.py (analysis-orthogonal; committed params
delta=0.04 and 20% ablation reused from PROTOCOL v1.0):

  1. open       QRNG picks an (orthogonal-when-tagged) image pair and the hidden
                YES/NO assignment; assignment sealed BEFORE anyone sees anything;
                ~20% of trials are QRNG-marked ablation (no feedback) at open.
                The viewer is shown the two image paths UNORDERED.
  2. transcript the viewer's impressions (free text and/or tags) — sealed.
  3. judge      the blind judge sees ONLY the transcript + the two images in
                scrambled (alphabetical) order and scores each 0-10. The call is
                computed through the sealed assignment inside the code path and
                sealed. Nothing about the assignment is printed before resolve.
  4. resolve    outcome (0/1) recorded; hit computed; feedback image path shown
                unless the trial is in the ablation arm.

Image pool: a directory of .jpg/.jpeg/.png/.gif/.webp files. Optional tags.json
({"filename.jpg": ["water","dark",...]}) enables the orthogonal-pair constraint;
without it pairs are random-distinct (a warning is printed — orthogonality is
part of the validated design, so tag your pool when you can).

Usage:
  python3 arv_session.py open       --event-id KXHIGHNY-26SEP15-T85 --pool images/
  python3 arv_session.py transcript --event-id ... --text "cold, angular, water below"
  python3 arv_session.py judge      --event-id ...                # shows the pair
  python3 arv_session.py judge      --event-id ... --scores 7,2   # records + calls
  python3 arv_session.py resolve    --event-id ... --outcome 1
  python3 arv_session.py status
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from os import urandom
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import TestMartingale, channel_prob  # noqa: E402
from arv import _u64  # noqa: E402

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
PROTOCOL = {"version": "1.0-arv-session", "delta": 0.04, "ablation": 0.2,
            "pair_max_overlap": 1}

SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    event_id TEXT PRIMARY KEY,
    pool TEXT, img_yes TEXT, img_no TEXT,
    ablation INTEGER NOT NULL DEFAULT 0,
    assignment_hash TEXT,
    transcript TEXT, transcript_hash TEXT,
    judge_scores TEXT, call INTEGER,
    outcome INTEGER, hit INTEGER, feedback_img TEXT,
    t_open REAL, t_transcript REAL, t_judged REAL, t_resolved REAL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    event_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    t REAL NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('protocol', ?)",
                 (json.dumps(PROTOCOL, sort_keys=True),))
    conn.commit()
    stored = json.loads(conn.execute("SELECT v FROM meta WHERE k='protocol'").fetchone()[0])
    if stored != PROTOCOL:
        sys.exit("protocol mismatch with this database — do not mix protocol versions")
    return conn


def seal(conn: sqlite3.Connection, event_key: str, payload: dict) -> str:
    """sqlite-persisted CommitLedger.seal: same record shape and hash recipe."""
    import hashlib
    rec = {"event_id": event_key, "probs": payload, "t": time.time()}
    rec["hash"] = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()
    conn.execute("INSERT INTO ledger (event_key, payload, t, hash) VALUES (?,?,?,?)",
                 (event_key, json.dumps(payload, sort_keys=True), rec["t"], rec["hash"]))
    conn.commit()
    return rec["hash"]


class FileImageBank:
    """arv.ImageBank over real files. Tags from tags.json, else filename tokens."""

    def __init__(self, pool_dir: Path):
        self.pool = pool_dir
        files = sorted(p.name for p in pool_dir.iterdir()
                       if p.suffix.lower() in IMAGE_EXTS)
        if len(files) < 8:
            sys.exit(f"image pool {pool_dir} has {len(files)} images — need at least 8")
        tags_path = pool_dir / "tags.json"
        explicit = json.loads(tags_path.read_text()) if tags_path.exists() else {}
        self.images: dict[str, frozenset] = {}
        self.tagged = bool(explicit)
        for name in files:
            if name in explicit:
                self.images[name] = frozenset(str(t).lower() for t in explicit[name])
            else:
                self.images[name] = frozenset(
                    t for t in re.split(r"[^a-z0-9]+", Path(name).stem.lower()) if len(t) > 2)
        if not self.tagged:
            print("NOTE: no tags.json in the pool — pairs are random-distinct, not "
                  "orthogonality-constrained. Add tags.json to restore the validated design.")

    def orthogonal_pair(self, feed=urandom, max_overlap=1, tries=800):
        ids = list(self.images)
        for _ in range(tries):
            a, b = ids[_u64(feed) % len(ids)], ids[_u64(feed) % len(ids)]
            if a == b:
                continue
            if not self.tagged or len(self.images[a] & self.images[b]) <= max_overlap:
                return a, b
        raise RuntimeError("no orthogonal pair found — pool tags too similar")


def get_trial(conn, event_id: str) -> dict:
    row = conn.execute("SELECT * FROM trials WHERE event_id=?", (event_id,)).fetchone()
    if row is None:
        sys.exit(f"no trial {event_id} — run open first")
    cols = [d[0] for d in conn.execute("SELECT * FROM trials LIMIT 0").description]
    return dict(zip(cols, row))


def cmd_open(conn, args) -> None:
    if conn.execute("SELECT 1 FROM trials WHERE event_id=?", (args.event_id,)).fetchone():
        sys.exit(f"trial {args.event_id} already open (trials are immutable)")
    bank = FileImageBank(Path(args.pool))
    a, b = bank.orthogonal_pair(max_overlap=PROTOCOL["pair_max_overlap"])
    bit = _u64(urandom) & 1
    img_yes, img_no = (a, b) if bit == 0 else (b, a)
    ablation = int((_u64(urandom) % 1000) < PROTOCOL["ablation"] * 1000)
    h = seal(conn, args.event_id + ":assign",
             {"event_id": args.event_id, "img_yes": img_yes, "img_no": img_no,
              "ablation": ablation})
    conn.execute(
        "INSERT INTO trials (event_id, pool, img_yes, img_no, ablation,"
        " assignment_hash, t_open) VALUES (?,?,?,?,?,?,?)",
        (args.event_id, str(Path(args.pool).resolve()), img_yes, img_no,
         ablation, h, time.time()))
    conn.commit()
    print(f"trial {args.event_id} opened; assignment sealed ({h[:16]}…)")
    print("show the VIEWER these two images (unordered):")
    for name in sorted((a, b)):
        print(f"  {Path(args.pool).resolve() / name}")
    print("then: arv_session.py transcript --event-id", args.event_id, '--text "..."')


def cmd_transcript(conn, args) -> None:
    tr = get_trial(conn, args.event_id)
    if tr["transcript_hash"]:
        sys.exit("transcript already sealed (immutable)")
    text = (args.text or "").strip()
    tags = sorted({t.strip().lower() for t in (args.tags or "").split(",") if t.strip()})
    if not text and not tags:
        sys.exit("provide --text and/or --tags")
    h = seal(conn, args.event_id + ":transcript", {"text": text, "tags": tags})
    conn.execute("UPDATE trials SET transcript=?, transcript_hash=?, t_transcript=?"
                 " WHERE event_id=?",
                 (json.dumps({"text": text, "tags": tags}), h, time.time(), args.event_id))
    conn.commit()
    print(f"transcript sealed ({h[:16]}…). next: judge --event-id {args.event_id}")


def cmd_judge(conn, args) -> None:
    tr = get_trial(conn, args.event_id)
    if not tr["transcript_hash"]:
        sys.exit("seal the transcript before judging")
    if tr["call"] is not None:
        sys.exit("already judged (immutable)")
    A, B = sorted([tr["img_yes"], tr["img_no"]])       # scrambled order for the judge
    if not args.scores:
        t = json.loads(tr["transcript"])
        print("BLIND JUDGE: score each image 0-10 for similarity to this transcript —")
        print(f"  transcript: {t['text'] or ''} {t['tags'] or ''}")
        print(f"  image 1: {Path(tr['pool']) / A}")
        print(f"  image 2: {Path(tr['pool']) / B}")
        print(f"then: arv_session.py judge --event-id {args.event_id} --scores S1,S2")
        return
    try:
        s1, s2 = (float(x) for x in args.scores.split(","))
    except ValueError:
        sys.exit("--scores must be two numbers, e.g. 7,2")
    best = A if s1 >= s2 else B
    call = +1 if best == tr["img_yes"] else -1
    h = seal(conn, args.event_id + ":call",
             {"call": call, "scores": {A: s1, B: s2}})
    conn.execute("UPDATE trials SET judge_scores=?, call=?, t_judged=? WHERE event_id=?",
                 (json.dumps({A: s1, B: s2}), call, time.time(), args.event_id))
    conn.commit()
    print(f"call sealed ({h[:16]}…): {'YES' if call == 1 else 'NO'}")


def cmd_resolve(conn, args) -> None:
    tr = get_trial(conn, args.event_id)
    if tr["call"] is None:
        sys.exit("judge before resolving")
    if tr["outcome"] is not None:
        sys.exit("already resolved (immutable)")
    outcome = int(args.outcome)
    hit = int(tr["call"] == (+1 if outcome == 1 else -1))
    feedback = tr["img_yes"] if outcome == 1 else tr["img_no"]
    conn.execute("UPDATE trials SET outcome=?, hit=?, feedback_img=?, t_resolved=?"
                 " WHERE event_id=?",
                 (outcome, hit, None if tr["ablation"] else feedback,
                  time.time(), args.event_id))
    conn.commit()
    print(f"resolved: outcome={outcome}  {'HIT' if hit else 'miss'}")
    if tr["ablation"]:
        print("ablation trial — no feedback image is shown.")
    else:
        print(f"FEEDBACK — show the viewer this image now: {Path(tr['pool']) / feedback}")


def cmd_status(conn, _args) -> None:
    rows = conn.execute(
        "SELECT event_id, ablation, call, outcome, hit FROM trials ORDER BY t_open").fetchall()
    open_n = sum(1 for r in rows if r[3] is None)
    resolved = [r for r in rows if r[3] is not None]
    hits = sum(r[4] for r in resolved)
    print(f"trials: {len(rows)} total, {open_n} open, {len(resolved)} resolved, "
          f"{sum(r[1] for r in rows)} ablation")
    if resolved:
        mart = TestMartingale(threshold=20.0)
        for r in resolved:
            q = channel_prob(0.5, r[2], delta=PROTOCOL["delta"])
            mart.update(q, 0.5, r[3])
        print(f"hit rate: {hits}/{len(resolved)} = {hits / len(resolved):.3f}   "
              f"E-process vs coin (delta={PROTOCOL['delta']}): E={mart.E:.3f}  "
              f"certified={mart.certified}")
    n_led = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    print(f"ledger: {n_led} sealed records")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/arv_sessions.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("open"); p.add_argument("--event-id", required=True)
    p.add_argument("--pool", required=True, help="directory of image files")
    p = sub.add_parser("transcript"); p.add_argument("--event-id", required=True)
    p.add_argument("--text", default=""); p.add_argument("--tags", default="")
    p = sub.add_parser("judge"); p.add_argument("--event-id", required=True)
    p.add_argument("--scores", default="")
    p = sub.add_parser("resolve"); p.add_argument("--event-id", required=True)
    p.add_argument("--outcome", required=True, choices=["0", "1"])
    sub.add_parser("status")
    args = ap.parse_args()

    conn = open_db(Path(args.db))
    {"open": cmd_open, "transcript": cmd_transcript, "judge": cmd_judge,
     "resolve": cmd_resolve, "status": cmd_status}[args.cmd](conn, args)


if __name__ == "__main__":
    main()
