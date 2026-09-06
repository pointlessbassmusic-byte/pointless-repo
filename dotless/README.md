# .less remix server (v0)

YouTube link (or upload) → stems → text description → new track in a different subgenre.

```
POST /jobs {"youtube_url": "...", "description": "make it a Sammy Virji style garage bootleg", "genre": "ukg"}
   ↓ yt-dlp → source.wav
   ↓ demucs (htdemucs, two-stem) → vocals.wav + no_vocals.wav
   ↓ analysis.py → bpm, key, per-bar chords
   ↓ planner.py → Claude turns the description into recipe.json (falls back to per-genre defaults)
   ↓ engine.py  → time-stretch + chop vocals, re-trigger instrumental beat slices, synth drums/bass/stabs,
                   arrange intro/build/drop/breakdown/drop2/outro → remix.wav / remix.mp3
GET  /jobs/{id}            → status, stage, progress
GET  /jobs/{id}/files/remix.mp3   (also remix.wav, vocals.mp3, instrumental.mp3, recipe.json, analysis.json, pipeline.log)
```

Genres wired in: `ukg` (132), `dnb` (174), `footwork` (160), `house` (126). The description can also pick the genre
("DJ Rashad", "juke" → footwork; "Virji", "garage" → ukg; "jungle", "dnb" → dnb).

## Install (Ubuntu 24.04, run as root)

```bash
cd ~/dotless-server
bash install.sh          # ffmpeg, rubberband, venv, CPU torch, demucs, yt-dlp, librosa, anthropic
cp .env.example .env     # add ANTHROPIC_API_KEY + set DOTLESS_API_KEY
./run.sh                 # starts uvicorn on :8000 inside tmux session "dotless"
```

## Test from your phone (Termius) or anywhere

```bash
export K=change-me   # whatever you put in DOTLESS_API_KEY
# 1. create a job
curl -s -X POST localhost:8000/jobs -H "X-API-Key: $K" -H "Content-Type: application/json" \
  -d '{"youtube_url":"https://www.youtube.com/watch?v=VIDEO_ID","description":"footwork, DJ Rashad style, stutter the hook","genre":"footwork"}'
# → {"id":"ab12cd34ef56","status":"queued"}

# 2. poll
watch -n 5 "curl -s localhost:8000/jobs/ab12cd34ef56 -H 'X-API-Key: $K'"

# 3. grab the result
curl -s localhost:8000/jobs/ab12cd34ef56/files/remix.mp3 -H "X-API-Key: $K" -o remix.mp3
```

Upload instead of YouTube:

```bash
curl -s -X POST localhost:8000/jobs/upload -H "X-API-Key: $K" \
  -F file=@song.mp3 -F description="uk garage, pitched vocal chops" -F genre=ukg
```

## Timing on a small CPU VPS

- yt-dlp: 5–20 s. demucs htdemucs on CPU: ~1–3× the song length (a 4-min song ≈ 4–12 min on 2 vCPU), ~2–3 GB RAM
  peak (`--segment 7` is already set to keep it down). analysis + planner + render: ~10 s.
- Too slow for a product → run `separate()` on a serverless GPU (Modal / RunPod / Replicate) and keep the rest here.
  On a GPU demucs takes ~5–10 s per song; `DOTLESS_DEVICE=cuda` is enough if the box has one.

## Known hazards

- **YouTube on datacenter IPs**: YouTube bot-checks VPS IPs ("Sign in to confirm you're not a bot"). Export
  cookies from a logged-in browser to `/root/cookies.txt` and set `DOTLESS_YT_COOKIES`, or route yt-dlp through a
  residential proxy. Keep `pip install -U yt-dlp` current - extractors break every few weeks.
- **App Store**: keep all YouTube fetching server-side. Apple rule 5.2.3 rejects apps that download/convert media
  from third-party sources; the app only ever sees your API.
- **Rights**: a genre-flip of a commercial track is still a derivative work. Free tier = the user's own tracks /
  royalty-free; make that explicit in the UI.

## Where the sound gets better

`engine.py` is deliberately simple synthesis so the whole loop works on CPU today. Upgrade path, in order:

1. Drop real one-shots in `samples/` (kick/snare/hat/clap per genre) and load them instead of `kick()` etc.
2. Sidechain: duck bass + stabs under each kick (a 60 ms dip).
3. Generative backing: ACE-Step 1.5 (Apache-2.0) has a vocal-to-BGM / cover mode - feed `vocals.wav` + the
   recipe as a caption on a GPU box and mix its backing with the engine's chops. Add it as a second backend in
   `pipeline.py` behind an env flag.
4. Per-stem output (drums / bass / stabs / vocal) so the app can ship stems to DJs - that's the Plus tier.
