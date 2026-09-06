# Maker lab — live maker-viability measurement ($0 at risk)

Active research tooling for the one open thesis the retro/forward tests left standing:
**do resting maker orders on live tennis match markets actually fill, and is spread + rebate
capture net-positive after adverse selection?** Imported from chat-handoff drop 5 (2026-09-06);
both files pass their `--self-test`.

| File | What it does |
|---|---|
| `tennis_maker_bot.py` | **Paper-only** maker quoting on live tennis match markets with **full L2 order-book WebSocket capture** → `l2_events.jsonl.gz` (book snapshots, depth deltas, trade prints, tick changes). Conservative queue-position fill model: queue ahead = displayed size at join; only real printed trade volume at our price (correct aggressor side) works off the queue; cancels never help; repricing resets queue. Paper fills are a lower bound. No live order path exists in this file; no keys are read. Ran as tmux `makerbot` on the VPS. |
| `l2_replay.py` | Offline maker-parameter optimizer against the recorded L2 events. Data-quality report first (was fill opportunity even present?), then a **pre-registered 24-combo grid, run once per dataset**, 60/40 fit/holdout by time, fees + 15% rebate modeled. Honesty guards: believe holdout only, with 30+ fills; positive results earn *more paper quoting*, never live capital by themselves. |

This supersedes the snapshot-based `archive/optimal-jul2026/paper_quoter.py` (which couldn't
observe intra-interval fills). It's also the working pattern for fv_bot's known gaps: live maker
fill tracking (task 6) and websocket books (task 7) in [`../PROJECT.md`](../PROJECT.md).

Run (needs `requests`, `websockets`):

```bash
python3 tennis_maker_bot.py --self-test
python3 tennis_maker_bot.py                 # quote + record during a live tennis session
python3 l2_replay.py l2_events.jsonl.gz     # then replay once
```

If the VPS has an `l2_events.jsonl.gz` from a prior session, pull it — the dataset is valuable
regardless of P&L.
