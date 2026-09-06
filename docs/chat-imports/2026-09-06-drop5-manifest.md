# Drop 5 — 2026-09-06 — L2 maker lab + the .less remix server

- Date: 2026-09-06 ("see if any of these are useful" — all five were)
- Engine: polymarket-bot (maker research) + **dotless** (new project: music remix server)

| Upload | Contents | Verdict / landed at |
|---|---|---|
| `PASTE_TENNIS_MAKER.sh` | base64 installer of `tennis_maker_bot.py` (724 lines): paper-only maker quoting on live tennis with full L2 WS capture + conservative queue fill model → `l2_events.jsonl.gz` | **Useful** — decoded to `polymarket-bot/maker-lab/`; self-test passes. Installer → `archive/paste-era/` |
| `PASTE_L2_REPLAY.sh` | base64 installer of `l2_replay.py` (408 lines): offline maker-parameter sweep vs recorded L2, pre-registered 24-combo grid, 60/40 fit/holdout, honesty guards | **Useful** — decoded to `polymarket-bot/maker-lab/`; self-test passes. Installer → `archive/paste-era/` |
| `dotless_paste1.sh` | Full `.less` remix-server install: FastAPI app, yt-dlp fetch, demucs separation, tempo/key/chord analysis, Claude recipe planner, procedural renderer, README | **Useful** — 11 files extracted to `dotless/` (new project); all compile. Installer → `dotless/paste-archive/` |
| `dotless_paste2_melody.sh` | Melody-layer hotfix (engine.py + planner.py) | Redundant — byte-identical to paste 1's versions (verified); kept in `dotless/paste-archive/` for provenance |
| `engine.py` (standalone) | dotless renderer | Duplicate — byte-identical to paste 1's `engine.py`; dropped |

## Verification

- `tennis_maker_bot.py --self-test` and `l2_replay.py --self-test`: **all pass** on this machine.
- All extracted Python compiles (`py_compile`), dotless included.
- No embedded secrets: dotless `.env.example` ships empty keys / `change-me` placeholder;
  the maker bot reads no keys at all (no live order path exists in the file).

## Notes

- The maker lab supersedes `archive/optimal-jul2026/paper_quoter.py` (snapshot method) and is
  the working pattern for fv_bot tasks 6–7 (maker fill tracking, websocket books).
- If the VPS has an `l2_events.jsonl.gz` from a prior `makerbot` session, pull it — the
  dataset is valuable regardless of P&L.
- dotless deploys to the same Linode (`~/dotless-server`, tmux `dotless`, port 8000);
  demucs peaks ~2–3 GB of the box's 4 GB RAM — don't overlap with trading sessions.
