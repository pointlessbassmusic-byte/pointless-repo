# PROJECT: .less remix server (dotless)

_Master project file. Imported from chat-handoff drop 5 (2026-09-06) — a separate engine from
the trading projects: a music remix server. All files extracted from the phone-era paste
installers (originals in `paste-archive/`); every module compiles clean._

## What this is

YouTube link (or upload) → stems → text description → new track in a different subgenre.

```
POST /jobs {"youtube_url": ..., "description": "Sammy Virji style garage bootleg", "genre": "ukg"}
  yt-dlp → source.wav
  demucs (htdemucs, two-stem) → vocals.wav + no_vocals.wav
  analysis.py → bpm, key, per-bar chords (librosa + numpy fallbacks)
  planner.py → Claude (ANTHROPIC_API_KEY, default model claude-sonnet-5) turns the description
               into recipe.json; validated + clamped; per-genre defaults without a key
  engine.py → time-stretch/chop stems, synth drum/bass/stab bed, melody layer ducked
              under the kick, arrange sections → remix.wav/mp3
```

Genres wired in: `ukg` (132), `dnb` (174), `footwork` (160), `house` (126).
FastAPI app (`app.py`) with X-API-Key auth, background worker queue, per-job dirs.
Full usage, timings, and hazards (YouTube bot-checks on VPS IPs, App Store rule 5.2.3,
derivative-work rights): [`README.md`](README.md).

## Deploy

Target: Ubuntu VPS, `~/dotless-server`, tmux session `dotless`, port 8000.
`install.sh` (ffmpeg, rubberband, CPU torch, demucs) then `cp .env.example .env` (set
`ANTHROPIC_API_KEY`, `DOTLESS_API_KEY`) then `./run.sh`. On the 2-vCPU Linode, demucs runs
~1–3× song length; the README's upgrade path moves separation to a serverless GPU.

**Note:** the trading engines and this server share the Linode. demucs peaks at ~2–3 GB RAM
of the box's 4 GB — don't run a separation job while the trading bots are mid-session.

## State / next

- v0 renderer is deliberately simple CPU synthesis. Upgrade path (from README): real one-shot
  samples → sidechain → ACE-Step generative backing behind an env flag → per-stem output.
- `paste-archive/dotless_paste2_melody.sh` was the melody-layer hotfix; paste 1 already
  contains the final versions (verified identical), so it's provenance only.
