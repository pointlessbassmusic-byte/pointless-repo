# maker/ — market-making research tools (paper-only)

Consolidated from the earlier VPS paste-scripts, verbatim (self-tests
included). These answer the one execution question the model-based bot
can't: **do resting maker orders on live tennis match markets actually
fill, and is spread + rebate capture net-positive after adverse
selection?**

- `tennis_maker_bot.py` — PAPER-ONLY maker quoting on live Polymarket
  tennis match markets, with full L2 order-book WebSocket capture to
  `l2_events.jsonl.gz` (book snapshots, depth deltas, trade prints, tick
  changes). Fill simulation is deliberately conservative: queue-ahead =
  displayed size at join; only real printed volume at our price (correct
  aggressor side) burns the queue; cancels never help us; repricing resets
  queue position. There is no live order path in this file and no keys are
  ever read.
- `l2_replay.py` — offline maker-parameter sweep against the recorded L2
  events: a PRE-REGISTERED 24-combo grid (min spread, take-profit,
  stop-loss, order TTL) run once per dataset with a 60/40 time-split
  fit/holdout. Honesty guards are part of the tool: believe holdout
  columns only, 30+ fills only, and positive results earn *more paper
  quoting* — never a live switch by themselves.

Usage:

```bash
pip install requests websockets       # or: pip install -e ".[maker]"
python3 maker/tennis_maker_bot.py --self-test
python3 maker/tennis_maker_bot.py     # paper session; records L2 as it runs
python3 maker/l2_replay.py l2_events.jsonl.gz
```

Fee/rebate model: sports taker rate 0.05 with p(1-p) shaping; maker rebate
modeled as `REBATE_RATE` (default 0.15) share of the equivalent taker fee —
set `REBATE_RATE=0` for the pessimistic view and verify live rebate
mechanics at docs.polymarket.com before trusting rebate-dependent
conclusions.

Relationship to `sportsbot/`: this is a *different edge hypothesis*
(microstructure, not prediction). The recorded L2 datasets are valuable to
both — replay results can calibrate `sportsbot`'s paper fill model and
slippage buffers.
