#!/usr/bin/env bash
# .less remix server - full install, paste 1 of 1 (includes the melody layer).
set -e
mkdir -p ~/dotless-server && cd ~/dotless-server

cat > requirements.txt <<'DOTLESS_EOF'
fastapi>=0.110
uvicorn[standard]>=0.29
python-multipart>=0.0.9
pydantic>=2
yt-dlp
demucs>=4.0.1
librosa>=0.10
soundfile>=0.12
numpy
scipy
pyrubberband>=0.4
anthropic>=0.40
DOTLESS_EOF

cat > .env.example <<'DOTLESS_EOF'
# copy to .env and fill in
ANTHROPIC_API_KEY=            # planner: description -> recipe. Leave empty to use default recipes.
DOTLESS_MODEL=claude-sonnet-5
DOTLESS_API_KEY=change-me     # required as X-API-Key header by the app
DOTLESS_YT_COOKIES=           # optional: /root/cookies.txt exported from a logged-in browser, if YouTube bot-checks the VPS
DOTLESS_DEVICE=cpu            # cuda on a GPU box
DOTLESS_DEMUCS_MODEL=htdemucs # htdemucs_ft is better but ~4x slower
DOTLESS_EOF

cat > install.sh <<'DOTLESS_EOF'
#!/usr/bin/env bash
# One-time setup on Ubuntu 24.04 (run as root or with sudo). ~10 min on a small VPS.
set -euo pipefail
cd "$(dirname "$0")"
apt-get update -y
apt-get install -y ffmpeg rubberband-cli python3-venv python3-pip tmux
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
# CPU-only torch first, so demucs doesn't pull the multi-GB CUDA wheels
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
mkdir -p jobs
echo
echo "installed. next: cp .env.example .env  (add keys), then ./run.sh"
DOTLESS_EOF

cat > run.sh <<'DOTLESS_EOF'
#!/usr/bin/env bash
# Start (or restart) the API in a tmux session called "dotless".
cd "$(dirname "$0")"
[ -f .env ] && set -a && . ./.env && set +a
tmux kill-session -t dotless 2>/dev/null || true
tmux new -d -s dotless "cd $(pwd) && . .venv/bin/activate && [ -f .env ] && set -a && . ./.env && set +a; uvicorn app:app --host 0.0.0.0 --port 8000 2>&1 | tee -a server.log"
sleep 2
curl -s localhost:8000/health && echo && echo "running in tmux session 'dotless'  (tmux attach -t dotless)"
DOTLESS_EOF

cat > audio_io.py <<'DOTLESS_EOF'
"""Audio I/O helpers for the .less remix server.

read/write WAV (soundfile if installed, scipy fallback), mp3 + format conversion via ffmpeg.
"""
import subprocess
from math import gcd

import numpy as np

SR = 44100


def _resample(y, fs, sr):
    if fs == sr:
        return y
    from scipy.signal import resample_poly
    g = gcd(int(fs), int(sr))
    return resample_poly(y, sr // g, fs // g, axis=0).astype(np.float32)


def read_wav(path, sr=SR, mono=True):
    """Return (samples float32 in [-1,1], sr). Mono by default."""
    try:
        import soundfile as sf
        y, fs = sf.read(str(path), dtype="float32", always_2d=True)
    except ImportError:
        from scipy.io import wavfile
        fs, y = wavfile.read(str(path))
        if y.dtype.kind == "i":
            y = y.astype(np.float32) / float(2 ** (8 * y.dtype.itemsize - 1))
        elif y.dtype.kind == "u":
            y = (y.astype(np.float32) - 128.0) / 128.0
        else:
            y = y.astype(np.float32)
        if y.ndim == 1:
            y = y[:, None]
    y = _resample(y, fs, sr)
    if mono:
        y = y.mean(axis=1)
    return np.ascontiguousarray(y, dtype=np.float32), sr


def write_wav(path, y, sr=SR):
    y = np.clip(np.asarray(y, dtype=np.float32), -1.0, 1.0)
    try:
        import soundfile as sf
        sf.write(str(path), y, sr, subtype="PCM_16")
    except ImportError:
        from scipy.io import wavfile
        wavfile.write(str(path), sr, (y * 32767).astype(np.int16))


def to_mp3(wav_path, mp3_path, bitrate="192k"):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_path),
         "-codec:a", "libmp3lame", "-b:a", bitrate, str(mp3_path)],
        check=True,
    )


def any_to_wav(src, dst, sr=SR):
    """Convert any audio/video file to 44.1k stereo PCM WAV."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn",
         "-ac", "2", "-ar", str(sr), str(dst)],
        check=True,
    )
    return dst
DOTLESS_EOF

cat > analysis.py <<'DOTLESS_EOF'
"""Tempo, key and per-bar chord analysis.

Uses librosa's beat tracker for tempo when installed; everything else is plain numpy
so the module also works on a bare box.
"""
import numpy as np

from audio_io import read_wav, SR

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler key profiles
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

MAX_SECONDS = 200  # analyse at most ~3:20 of audio (keeps RAM small on a VPS)


def _stft_mag(y, n_fft=4096, hop=1024):
    win = np.hanning(n_fft).astype(np.float32)
    n = 1 + max(0, (len(y) - n_fft) // hop)
    mags = np.empty((n, n_fft // 2 + 1), dtype=np.float32)
    chunk = 512
    for i in range(0, n, chunk):  # chunked to keep memory flat
        idx = np.arange(n_fft)[None, :] + hop * np.arange(i, min(n, i + chunk))[:, None]
        mags[i:i + len(idx)] = np.abs(np.fft.rfft(y[idx] * win, axis=1))
    return mags


def _chroma(mag, sr, n_fft):
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    valid = (freqs >= 55) & (freqs <= 4000)
    midi = 69 + 12 * np.log2(freqs[valid] / 440.0)
    pc = np.round(midi).astype(int) % 12
    m = mag[:, valid]
    chroma = np.zeros((mag.shape[0], 12), dtype=np.float32)
    for k in range(12):
        chroma[:, k] = m[:, pc == k].sum(axis=1)
    return chroma


def estimate_tempo(y, sr=SR):
    try:
        import librosa
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        t = float(np.atleast_1d(tempo)[0])
        if t > 0:
            return t
    except Exception:
        pass
    # numpy fallback: autocorrelation of spectral flux, 60-200 BPM
    n_fft, hop = 2048, 512
    mag = _stft_mag(y, n_fft, hop)
    flux = np.maximum(np.diff(mag, axis=0), 0).sum(axis=1)
    flux = flux - flux.mean()
    if len(flux) < 64 or not np.any(flux):
        return 120.0
    ac = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    fps = sr / hop
    lo, hi = int(fps * 60 / 200), int(fps * 60 / 60)
    lag = lo + int(np.argmax(ac[lo:hi]))
    return float(60.0 * fps / lag)


def estimate_key(chroma_mean):
    best = (-2.0, 0, "major")
    for i in range(12):
        for name, prof in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = np.corrcoef(np.roll(prof, i), chroma_mean)[0, 1]
            if r > best[0]:
                best = (r, i, name)
    return NOTE_NAMES[best[1]], best[2]


def chords_per_bar(chroma, hop, sr, bpm):
    """Template-match a major/minor triad for every 4/4 bar."""
    bar_frames = (60.0 / bpm * 4) * sr / hop
    n_bars = max(1, int(chroma.shape[0] / bar_frames))
    templates = []
    for root in range(12):
        maj = np.zeros(12); maj[[root, (root + 4) % 12, (root + 7) % 12]] = 1
        mnr = np.zeros(12); mnr[[root, (root + 3) % 12, (root + 7) % 12]] = 1
        templates += [(root, "major", maj), (root, "minor", mnr)]
    out = []
    for b in range(n_bars):
        seg = chroma[int(b * bar_frames):int((b + 1) * bar_frames)]
        if len(seg) == 0:
            break
        v = seg.mean(axis=0)
        v = v / (np.linalg.norm(v) + 1e-9)
        root, qual, _ = max(templates, key=lambda t: float(v @ t[2]))
        out.append([NOTE_NAMES[root], qual])
    return out


def analyze(mix_path, instrumental_path=None):
    y, sr = read_wav(mix_path)
    y = y[: sr * MAX_SECONDS]
    bpm = estimate_tempo(y, sr)
    h, _ = read_wav(instrumental_path or mix_path)
    h = h[: sr * MAX_SECONDS]
    n_fft, hop = 4096, 1024
    chroma = _chroma(_stft_mag(h, n_fft, hop), sr, n_fft)
    root, mode = estimate_key(chroma.mean(axis=0))
    return {
        "bpm": round(bpm, 2),
        "key_root": root,
        "mode": mode,
        "chords": chords_per_bar(chroma, hop, sr, bpm),
        "duration_sec": round(len(y) / sr, 2),
    }
DOTLESS_EOF

cat > planner.py <<'DOTLESS_EOF'
"""Description -> remix recipe (JSON).

If ANTHROPIC_API_KEY is set, Claude turns the user's text into a recipe that the engine
renders. Without a key, a per-genre default recipe is used so the pipeline still runs.
"""
import copy
import json
import os
import re

GENRE_DEFAULTS = {
    "ukg": {
        "genre": "ukg", "bpm": 132, "swing": 0.58, "energy": 0.8,
        "drums": {"pattern": "two_step_shuffle", "hat_density": 1},
        "bass": {"style": "bounce"},
        "stabs": {"style": "organ"},
        "vocal": {"chop": "phrases", "stutter": 0.15, "pitch_shift": 2},
        "sections": [
            {"name": "intro", "bars": 8, "layers": ["hats", "vocal_chops", "stabs"]},
            {"name": "build", "bars": 8, "layers": ["drums", "stabs", "vocal_chops", "riser"]},
            {"name": "drop", "bars": 16, "layers": ["drums", "bass", "stabs", "melody", "vocal"]},
            {"name": "breakdown", "bars": 8, "layers": ["stabs", "melody", "vocal", "riser"]},
            {"name": "drop2", "bars": 16, "layers": ["drums", "bass", "stabs", "melody", "vocal_chops"]},
            {"name": "outro", "bars": 8, "layers": ["drums", "bass"]},
        ],
    },
    "dnb": {
        "genre": "dnb", "bpm": 174, "swing": 0.5, "energy": 0.85,
        "drums": {"pattern": "two_step_break", "hat_density": 2},
        "bass": {"style": "reese"},
        "stabs": {"style": "pad"},
        "vocal": {"chop": "phrases", "stutter": 0.1, "pitch_shift": 0},
        "sections": [
            {"name": "intro", "bars": 16, "layers": ["hats", "stabs", "vocal"]},
            {"name": "build", "bars": 8, "layers": ["drums", "stabs", "vocal_chops", "riser"]},
            {"name": "drop", "bars": 32, "layers": ["drums", "bass", "stabs", "melody", "vocal_chops"]},
            {"name": "breakdown", "bars": 16, "layers": ["stabs", "melody", "vocal", "riser"]},
            {"name": "drop2", "bars": 32, "layers": ["drums", "bass", "stabs", "melody", "vocal"]},
            {"name": "outro", "bars": 8, "layers": ["drums", "stabs"]},
        ],
    },
    "footwork": {
        "genre": "footwork", "bpm": 160, "swing": 0.5, "energy": 0.9,
        "drums": {"pattern": "tresillo_808", "hat_density": 2},
        "bass": {"style": "808"},
        "stabs": {"style": "stab"},
        "vocal": {"chop": "words", "stutter": 0.6, "pitch_shift": 0},
        "sections": [
            {"name": "intro", "bars": 8, "layers": ["vocal_chops", "inst_chops"]},
            {"name": "drop", "bars": 24, "layers": ["drums", "bass", "vocal_chops", "inst_chops"]},
            {"name": "breakdown", "bars": 8, "layers": ["vocal", "stabs", "riser"]},
            {"name": "drop2", "bars": 24, "layers": ["drums", "bass", "vocal_chops", "melody", "inst_chops"]},
            {"name": "outro", "bars": 8, "layers": ["drums", "vocal_chops"]},
        ],
    },
    "house": {
        "genre": "house", "bpm": 126, "swing": 0.54, "energy": 0.75,
        "drums": {"pattern": "four_floor", "hat_density": 1},
        "bass": {"style": "sub"},
        "stabs": {"style": "organ"},
        "vocal": {"chop": "phrases", "stutter": 0.05, "pitch_shift": 0},
        "sections": [
            {"name": "intro", "bars": 16, "layers": ["drums", "hats", "vocal_chops"]},
            {"name": "drop", "bars": 32, "layers": ["drums", "bass", "stabs", "melody", "vocal"]},
            {"name": "breakdown", "bars": 16, "layers": ["stabs", "melody", "vocal", "riser"]},
            {"name": "drop2", "bars": 32, "layers": ["drums", "bass", "stabs", "vocal_chops"]},
            {"name": "outro", "bars": 16, "layers": ["drums", "bass"]},
        ],
    },
}

ARTIST_HINTS = [
    ("rashad", "footwork"), ("spinn", "footwork"), ("juke", "footwork"), ("footwork", "footwork"),
    ("virji", "ukg"), ("interplanetary", "ukg"), ("garage", "ukg"), ("ukg", "ukg"), ("2-step", "ukg"),
    ("drum and bass", "dnb"), ("drum & bass", "dnb"), ("dnb", "dnb"), ("jungle", "dnb"), ("liquid", "dnb"),
    ("house", "house"), ("disco", "house"),
]

ALLOWED = {
    "genre": list(GENRE_DEFAULTS),
    "drums.pattern": ["two_step_shuffle", "two_step_break", "tresillo_808", "four_floor"],
    "bass.style": ["bounce", "reese", "808", "sub", "none"],
    "stabs.style": ["organ", "pad", "stab", "none"],
    "vocal.chop": ["phrases", "words", "none"],
    "layers": ["drums", "hats", "bass", "stabs", "inst_chops", "melody", "vocal", "vocal_chops", "riser"],
}

SYSTEM = """You are the arrangement planner for .less, a remix engine aimed at DJs and producers.
You receive a user's text description of the remix they want plus analysis of the source song
(tempo, key, per-bar chords). Reply with ONE JSON object and nothing else - no prose, no code fences.

Schema (all fields required):
{"genre": <one of %(genre)s>, "bpm": <number>, "swing": <0.5-0.67>, "energy": <0-1>,
 "key_root": <note name>, "mode": <"major"|"minor">,
 "drums": {"pattern": <one of %(drums.pattern)s>, "hat_density": <1 = 8ths, 2 = 16ths>},
 "bass": {"style": <one of %(bass.style)s>},
 "stabs": {"style": <one of %(stabs.style)s>},
 "vocal": {"chop": <one of %(vocal.chop)s>, "stutter": <0-1 probability of retrigger stutters>,
           "pitch_shift": <semitones, -5..7>},
 "sections": [{"name": <str>, "bars": <int>, "layers": [<subset of %(layers)s>]}, ...],
 "notes": <one short sentence on the intent>}

House style vocabulary:
- DJ Rashad / DJ Spinn / juke / footwork -> genre footwork, 160 bpm, tresillo_808 drums, 808 bass,
  vocal chop "words" with high stutter (rapid retriggers), sparse stabs.
- Sammy Virji / Interplanetary Criminal / UK garage -> genre ukg, 130-136 bpm, two_step_shuffle with
  swing 0.56-0.62, bounce bass, organ stabs, pitched-up vocal chops (+2 to +4 semitones).
- drum and bass / jungle / liquid -> genre dnb, 170-176 bpm, two_step_break, reese bass, pad stabs.
- house / disco -> genre house, 122-128 bpm, four_floor, sub bass, organ stabs.
"melody" plays the source's own instrumental (its synths/melody/chords) continuously, high-pass
filtered above the new bass and ducked under the kick, in sync with "vocal". Use it whenever the
user asks to keep the original melody, synths, chords or instrumental. "inst_chops" instead
re-triggers beat slices of the instrumental on the grid.
Keep the source key (key_root / mode) unless the user asks to change it.
Total bars must be between 48 and 128. Sections named "drop*" are the high-energy parts and must
include drums and bass. Use "riser" as the last layer of a section that leads into a drop.
"""


def guess_genre(description, fallback="ukg"):
    d = (description or "").lower()
    for needle, genre in ARTIST_HINTS:
        if needle in d:
            return genre
    return fallback


def default_recipe(genre, analysis):
    r = copy.deepcopy(GENRE_DEFAULTS.get(genre, GENRE_DEFAULTS["ukg"]))
    r["key_root"] = analysis.get("key_root", "A")
    r["mode"] = analysis.get("mode", "minor")
    r["notes"] = "default recipe for %s" % r["genre"]
    return r


def _extract_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return m.group(0) if m else text


def _validate(recipe, base):
    """Fill gaps and clamp values so the engine never sees garbage."""
    out = copy.deepcopy(base)
    for k in ("genre", "bpm", "swing", "energy", "key_root", "mode", "notes"):
        if k in recipe:
            out[k] = recipe[k]
    if out["genre"] not in ALLOWED["genre"]:
        out["genre"] = base["genre"]
    out["bpm"] = float(min(200, max(60, out["bpm"])))
    out["swing"] = float(min(0.67, max(0.5, out.get("swing", 0.5))))
    out["energy"] = float(min(1.0, max(0.1, out.get("energy", 0.8))))
    for grp, key in (("drums", "pattern"), ("bass", "style"), ("stabs", "style"), ("vocal", "chop")):
        val = (recipe.get(grp) or {}).get(key)
        if val in ALLOWED["%s.%s" % (grp, key)]:
            out[grp][key] = val
    if "drums" in recipe and recipe["drums"].get("hat_density") in (1, 2):
        out["drums"]["hat_density"] = recipe["drums"]["hat_density"]
    if "vocal" in recipe:
        out["vocal"]["stutter"] = float(min(1, max(0, recipe["vocal"].get("stutter", out["vocal"]["stutter"]))))
        out["vocal"]["pitch_shift"] = int(min(7, max(-5, recipe["vocal"].get("pitch_shift", 0))))
    secs = []
    for s in recipe.get("sections") or []:
        try:
            bars = int(s["bars"])
            layers = [l for l in s.get("layers", []) if l in ALLOWED["layers"]]
            if bars > 0 and layers:
                secs.append({"name": str(s.get("name", "section")), "bars": bars, "layers": layers})
        except (KeyError, TypeError, ValueError):
            continue
    if secs and 16 <= sum(s["bars"] for s in secs) <= 160:
        out["sections"] = secs
    return out


def make_recipe(description, genre, analysis):
    genre = genre if genre in GENRE_DEFAULTS else guess_genre(description)
    base = default_recipe(genre, analysis)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        base["notes"] = "no ANTHROPIC_API_KEY set - used default %s recipe" % genre
        return base
    try:
        import anthropic
        client = anthropic.Anthropic()
        user = json.dumps({
            "description": description or "",
            "requested_genre": genre,
            "analysis": {k: analysis.get(k) for k in ("bpm", "key_root", "mode", "duration_sec")},
            "first_chords": (analysis.get("chords") or [])[:16],
        })
        msg = client.messages.create(
            model=os.environ.get("DOTLESS_MODEL", "claude-sonnet-5"),
            max_tokens=1500,
            system=SYSTEM % {k: json.dumps(v) for k, v in ALLOWED.items()},
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        data = json.loads(_extract_json(text))
        return _validate(data, base)
    except Exception as exc:  # never let the planner kill a job
        base["notes"] = "planner fallback (%s): default %s recipe" % (type(exc).__name__, genre)
        return base
DOTLESS_EOF

cat > engine.py <<'DOTLESS_EOF'
"""Procedural remix renderer (v0).

Takes the separated vocal + instrumental stems, time-stretches them to the target tempo,
chops them into phrases / beat slices, and re-triggers them over a synthesized
drum + bass + chord-stab bed built from a per-genre pattern. CPU only (numpy + scipy).

Layers a section can switch on:
  drums, hats, bass, stabs, inst_chops, vocal, vocal_chops, riser
"""
import numpy as np
from scipy.signal import butter, lfilter, resample_poly

from audio_io import SR

NOTE_INDEX = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}
FLATS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B", "Fb": "E"}

# 16-step patterns per bar (4/4). Steps 0,4,8,12 are the beats.
PATTERNS = {
    "two_step_shuffle": {"kick": [0, 6, 11], "snare": [4, 12], "clap": [12], "hat": [2, 6, 10, 14], "ohat": [14]},
    "two_step_break":   {"kick": [0, 10], "snare": [4, 12], "clap": [], "hat": [0, 2, 4, 6, 8, 10, 12, 14], "ohat": [6]},
    "tresillo_808":     {"kick": [0, 3, 6, 8, 11, 14], "snare": [4, 12], "clap": [12], "hat": list(range(0, 16, 2)), "ohat": []},
    "four_floor":       {"kick": [0, 4, 8, 12], "snare": [4, 12], "clap": [4, 12], "hat": [2, 6, 10, 14], "ohat": [2, 6, 10, 14]},
}
BASS_STEPS = {"bounce": [0, 3, 6, 10, 13], "sub": [0, 4, 8, 12], "reese": [0], "808": None}  # 808 follows the kick
STAB_STEPS = {"organ": [2, 6, 10, 14], "pad": [0], "stab": [0, 10]}
CHOP_STEPS = {"ukg": [0, 10], "dnb": [0], "footwork": [0, 6, 11], "house": [0, 8]}

_rng = np.random.default_rng(7)


# ----------------------------------------------------------------------------- dsp utils
def lowpass(x, cutoff, order=2):
    b, a = butter(order, min(cutoff, SR / 2 - 100) / (SR / 2))
    return lfilter(b, a, x).astype(np.float32)


def highpass(x, cutoff, order=2):
    b, a = butter(order, max(cutoff, 10) / (SR / 2), btype="high")
    return lfilter(b, a, x).astype(np.float32)


def bandpass(x, lo, hi):
    b, a = butter(2, [lo / (SR / 2), min(hi, SR / 2 - 100) / (SR / 2)], btype="band")
    return lfilter(b, a, x).astype(np.float32)


def fade(x, ms_in=3, ms_out=12):
    x = x.copy()
    a, r = int(SR * ms_in / 1000), int(SR * ms_out / 1000)
    if a and len(x) > a:
        x[:a] *= np.linspace(0, 1, a, dtype=np.float32)
    if r and len(x) > r:
        x[-r:] *= np.linspace(1, 0, r, dtype=np.float32)
    return x


def mix_at(buf, x, pos, gain=1.0):
    pos = int(pos)
    if pos >= len(buf) or len(x) == 0:
        return
    if pos < 0:
        x, pos = x[-pos:], 0
    n = min(len(x), len(buf) - pos)
    buf[pos:pos + n] += x[:n] * gain


def note_freq(name, octave):
    n = name.strip()
    n = n[0].upper() + n[1:].lower()
    n = FLATS.get(n, n)
    midi = 12 * (octave + 1) + NOTE_INDEX.get(n, 0)
    return 440.0 * 2 ** ((midi - 69) / 12)


def chord_freqs(root, quality, octave=3):
    r = note_freq(root, octave)
    third = 2 ** ((3 if quality == "minor" else 4) / 12)
    return [r, r * third, r * 2 ** (7 / 12)]


def _t(dur):
    return np.arange(int(SR * dur), dtype=np.float32) / SR


def saw(freq, t):
    return (2.0 * ((t * freq) % 1.0) - 1.0).astype(np.float32)


# ----------------------------------------------------------------------------- voices
def kick(dur=0.38, f0=170.0, f1=48.0, punch=1.0):
    t = _t(dur)
    f = f1 + (f0 - f1) * np.exp(-t * 38)
    phase = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(phase) * np.exp(-t * 8.5)
    return (np.tanh(x * (1.6 + punch)) * 0.9).astype(np.float32)


def snare(dur=0.2):
    t = _t(dur)
    noise = bandpass(_rng.standard_normal(len(t)).astype(np.float32), 300, 9000) * np.exp(-t * 20)
    tone = np.sin(2 * np.pi * 186 * t) * np.exp(-t * 32)
    return ((noise * 0.75 + tone * 0.6) * 0.8).astype(np.float32)


def clap(dur=0.25):
    t = _t(dur)
    out = np.zeros(len(t), dtype=np.float32)
    noise = bandpass(_rng.standard_normal(len(t)).astype(np.float32), 900, 7000)
    for k, off in enumerate((0, 0.011, 0.022)):
        env = np.exp(-(t - off) * (60 if k < 2 else 16)) * (t >= off)
        out += noise * env.astype(np.float32)
    return (out * 0.45).astype(np.float32)


def hat(dur=0.05, open_=False):
    dur = 0.28 if open_ else dur
    t = _t(dur)
    x = highpass(_rng.standard_normal(len(t)).astype(np.float32), 7000, 4)
    return (x * np.exp(-t * (7 if open_ else 55)) * 0.32).astype(np.float32)


def sub_note(freq, dur):
    t = _t(dur)
    x = np.sin(2 * np.pi * freq * t)
    return fade(np.tanh(x * 1.3).astype(np.float32) * 0.6, 4, 30)


def reese(freq, dur):
    t = _t(dur)
    x = saw(freq * 0.996, t) + saw(freq * 1.004, t) + 0.5 * saw(freq * 0.5, t)
    x = lowpass(x, 420, 4)
    return fade(np.tanh(x * 1.8).astype(np.float32) * 0.45, 8, 40)


def eight08(freq, dur=0.9):
    t = _t(dur)
    f = freq * (1 + 1.2 * np.exp(-t * 55))
    phase = 2 * np.pi * np.cumsum(f) / SR
    x = np.sin(phase) * np.exp(-t * 3.2)
    return (np.tanh(x * 2.2) * 0.62).astype(np.float32)


def organ_stab(freqs, dur=0.22):
    t = _t(dur)
    x = np.zeros(len(t), dtype=np.float32)
    for f in freqs:
        for h, g in ((1, 1.0), (2, 0.5), (3, 0.28), (4, 0.18)):
            x += np.sin(2 * np.pi * f * h * t) * g
    env = np.exp(-t * 9)
    return fade(lowpass(x * env, 5200) * 0.11, 2, 25)


def pad(freqs, dur):
    t = _t(dur)
    x = np.zeros(len(t), dtype=np.float32)
    for f in freqs:
        x += saw(f * 0.997, t) + saw(f * 1.003, t)
    n = len(t)
    a = max(1, int(n * 0.25))
    env = np.ones(n, dtype=np.float32)
    env[:a] = np.linspace(0, 1, a)
    env[-a:] *= np.linspace(1, 0, a)
    return (lowpass(x, 1300) * env * 0.07).astype(np.float32)


def short_stab(freqs, dur=0.16):
    t = _t(dur)
    x = np.zeros(len(t), dtype=np.float32)
    for f in freqs:
        x += saw(f, t) + saw(f * 2.001, t) * 0.4
    return fade(lowpass(x * np.exp(-t * 14), 3000) * 0.12, 2, 20)


def duck_env(n, positions, depth=0.45, dur=0.1):
    """Gain envelope that dips at each position and recovers over `dur` - a poor man's sidechain."""
    env = np.ones(n, dtype=np.float32)
    L = max(1, int(SR * dur))
    ramp = np.linspace(1.0 - depth, 1.0, L, dtype=np.float32)
    for p in positions:
        p = int(p)
        if 0 <= p < n:
            m = min(L, n - p)
            env[p:p + m] = np.minimum(env[p:p + m], ramp[:m])
    return env


def riser(n_samples):
    t = np.arange(n_samples, dtype=np.float32) / SR
    noise = _rng.standard_normal(n_samples).astype(np.float32)
    # rising filter via 8 chunks with increasing cutoff
    out = np.zeros(n_samples, dtype=np.float32)
    k = 8
    for i in range(k):
        s, e = i * n_samples // k, (i + 1) * n_samples // k
        out[s:e] = lowpass(noise[s:e], 300 * (1.6 ** i))
    env = (t / t[-1]) ** 2.2 * 0.35
    return fade(out * env, 5, 5)


# ----------------------------------------------------------------------------- source material
def rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def time_stretch(y, ratio):
    """ratio > 1 = faster/shorter. Pitch-preserving when rubberband or librosa is available."""
    if abs(ratio - 1.0) < 0.01:
        return y
    try:
        import pyrubberband as pyrb
        return pyrb.time_stretch(y, SR, ratio).astype(np.float32)
    except Exception:
        pass
    try:
        import librosa
        return librosa.effects.time_stretch(y, rate=ratio).astype(np.float32)
    except Exception:
        pass
    # last resort: resample (changes pitch too)
    up, down = _ratio_to_fraction(1.0 / ratio)
    return resample_poly(y, up, down).astype(np.float32)


def _ratio_to_fraction(r, max_den=64):
    from fractions import Fraction
    f = Fraction(r).limit_denominator(max_den)
    return f.numerator, f.denominator


def pitch_shift(y, semitones):
    """Resampling pitch shift (changes length) - the classic sped-up chop trope."""
    if not semitones:
        return y
    up, down = _ratio_to_fraction(2 ** (-semitones / 12))
    return resample_poly(y, up, down).astype(np.float32)


def choose_stretch(src_bpm, target_bpm):
    """Pick a stretch ratio in a musical range (half/double-time when needed)."""
    if not src_bpm or src_bpm <= 0:
        return 1.0
    r = target_bpm / src_bpm
    while r > 1.42:
        r /= 2
    while r < 0.7:
        r *= 2
    return r


def split_phrases(y, top_db=35, min_len=0.25, max_len=4.0, gap=0.15):
    """Split on silence -> list of (start, end) sample indices."""
    frame, hop = 2048, 512
    if len(y) < frame * 4:
        return []
    n = 1 + (len(y) - frame) // hop
    energy = np.empty(n, dtype=np.float32)
    for i in range(0, n, 1024):  # chunked to keep memory flat
        idx = np.arange(frame)[None, :] + hop * np.arange(i, min(n, i + 1024))[:, None]
        energy[i:i + len(idx)] = np.sqrt(np.mean(np.square(y[idx]), axis=1))
    ref = energy.max() + 1e-9
    active = 20 * np.log10(energy / ref + 1e-9) > -top_db
    out, start = [], None
    for i, a in enumerate(active):
        if a and start is None:
            start = i
        elif not a and start is not None:
            out.append([start * hop, i * hop + frame])
            start = None
    if start is not None:
        out.append([start * hop, len(y)])
    # merge short gaps
    merged = []
    for s, e in out:
        if merged and s - merged[-1][1] < gap * SR:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    phrases = []
    for s, e in merged:
        while e - s > max_len * SR:
            phrases.append((s, s + int(max_len * SR)))
            s += int(max_len * SR)
        if e - s >= min_len * SR:
            phrases.append((s, e))
    return phrases


def beat_slices(y, beat_len, top=8):
    """Cut into beat-length slices and return the `top` loudest ones."""
    n = len(y) // beat_len
    if n == 0:
        return []
    slices = [y[i * beat_len:(i + 1) * beat_len] for i in range(n)]
    slices.sort(key=rms, reverse=True)
    return slices[:top]


def stutter(x, step, reps=4, slice_frac=0.5):
    """Retrigger the first slice of x `reps` times at `step` spacing, then play x."""
    sl = fade(x[: max(1, int(step * slice_frac))], 1, 4)
    out = np.zeros(int(step * reps) + len(x), dtype=np.float32)
    for i in range(reps):
        mix_at(out, sl, i * step, 0.9)
    mix_at(out, x, step * reps)
    return out


# ----------------------------------------------------------------------------- render
def render(recipe, vocals=None, instrumental=None, src_bpm=0.0, chords=None):
    bpm = float(recipe["bpm"])
    genre = recipe.get("genre", "ukg")
    bar = int(round(SR * 240.0 / bpm))
    beat = bar // 4
    step = bar / 16.0
    swing = float(recipe.get("swing", 0.5))
    energy = float(recipe.get("energy", 0.8))
    sections = recipe["sections"]
    total_bars = sum(int(s["bars"]) for s in sections)
    out = np.zeros(total_bars * bar + SR * 3, dtype=np.float32)

    # --- source material
    ratio = choose_stretch(src_bpm, bpm)
    voc = None
    if vocals is not None and rms(vocals) > 1e-4:
        voc = time_stretch(vocals, ratio)
    inst = time_stretch(instrumental, ratio) if instrumental is not None else None
    # "melody" bed: the source's own synths/melody, cleared above the new kick + bass
    mel = highpass(inst, 190, 4) if inst is not None and rms(inst) > 1e-4 else None
    phrases = split_phrases(voc) if voc is not None else []
    slices = beat_slices(inst, beat) if inst is not None else []
    vchop = recipe.get("vocal", {})
    chop_pitch = int(vchop.get("pitch_shift", 0))
    stutter_p = float(vchop.get("stutter", 0))
    chop_mode = vchop.get("chop", "phrases")

    # --- pre-rendered one-shots
    pat = PATTERNS.get(recipe["drums"]["pattern"], PATTERNS["two_step_shuffle"])
    hat_density = int(recipe["drums"].get("hat_density", 1))
    hat_steps = list(range(0, 16, 1 if hat_density >= 2 else 2)) if hat_density else pat["hat"]
    K, S, C, H, OH = kick(punch=energy), snare(), clap(), hat(), hat(open_=True)
    bass_style = recipe["bass"]["style"]
    stab_style = recipe["stabs"]["style"]

    def swung(st):
        """Sample offset of a 16th step inside the bar, with swing on the odd steps."""
        off = (swing - 0.5) * step if st % 2 else 0.0
        return st * step + off

    bar_idx, src_cursor = 0, 0
    for si, sec in enumerate(sections):
        layers = set(sec["layers"])
        nbars = int(sec["bars"])
        leads_to_drop = si + 1 < len(sections) and str(sections[si + 1]["name"]).startswith("drop")
        for b in range(nbars):
            start = bar_idx * bar
            if chords:
                # while the continuous vocal plays, follow the source song's own chord timeline
                if ("vocal" in layers or "melody" in layers) and src_bpm and (voc is not None or mel is not None):
                    src_bar = int(src_cursor * ratio / (SR * 240.0 / src_bpm))
                    root, quality = chords[min(src_bar, len(chords) - 1)]
                else:
                    root, quality = chords[bar_idx % len(chords)]
            else:
                root, quality = recipe["key_root"], recipe["mode"]
            root_hz = note_freq(root, 1)
            triad = chord_freqs(root, quality, 3)

            # drums / hats
            if "drums" in layers or "hats" in layers:
                for st in hat_steps:
                    mix_at(out, H, start + swung(st), 0.8 if st % 4 == 0 else 0.55)
                for st in pat["ohat"]:
                    mix_at(out, OH, start + swung(st), 0.5)
                if genre == "footwork" and bar_idx % 4 == 3:  # hat roll on the last beat
                    for i in range(8):
                        mix_at(out, H, start + 12 * step + i * step / 2, 0.35 + 0.08 * i)
            if "drums" in layers:
                for st in pat["kick"]:
                    mix_at(out, K, start + swung(st), 1.0)
                for st in pat["snare"]:
                    mix_at(out, S, start + swung(st), 0.9)
                for st in pat["clap"]:
                    mix_at(out, C, start + swung(st), 0.7)

            # bass
            if "bass" in layers and bass_style != "none":
                if bass_style == "808":
                    for st in pat["kick"]:
                        mix_at(out, eight08(root_hz), start + swung(st), 0.9)
                elif bass_style == "reese":
                    mix_at(out, reese(root_hz, bar / SR), start, 0.9)
                    mix_at(out, sub_note(root_hz, bar / SR), start, 0.5)
                else:
                    steps = BASS_STEPS[bass_style]
                    for i, st in enumerate(steps):
                        f = root_hz * (2 if (bass_style == "bounce" and i == 2) else 1)
                        dur = (2.5 if bass_style == "bounce" else 3.5) * step / SR
                        mix_at(out, sub_note(f, dur), start + swung(st), 0.95)

            # chord stabs
            if "stabs" in layers and stab_style != "none":
                if stab_style == "pad":
                    mix_at(out, pad(triad, bar / SR), start, 1.0)
                else:
                    voice = organ_stab if stab_style == "organ" else short_stab
                    for st in STAB_STEPS[stab_style]:
                        mix_at(out, voice(triad), start + swung(st), 1.0)

            # resampled instrumental hits (the source's own chords, re-triggered)
            if "inst_chops" in layers and slices:
                sl = fade(slices[(bar_idx * 3) % len(slices)], 2, 60)
                gate = int(min(len(sl), beat * 0.75))
                for st in CHOP_STEPS.get(genre, [0]):
                    mix_at(out, fade(sl[:gate], 2, 40), start + swung(st), 0.55)

            # continuous melody bed (the source's own synths, ducked under the new kick)
            if "melody" in layers and mel is not None and src_cursor < len(mel):
                seg = mel[src_cursor:src_cursor + bar].copy()
                if "drums" in layers:
                    seg *= duck_env(len(seg), [swung(st) for st in pat["kick"]])
                mix_at(out, fade(seg, 5, 5), start, 0.55)

            # continuous vocal (keeps the song's narrative order, stays in sync with the melody bed)
            if "vocal" in layers and voc is not None and src_cursor < len(voc):
                seg = voc[src_cursor:src_cursor + bar]
                mix_at(out, fade(seg, 5, 5), start, 0.9)

            if "vocal" in layers or "melody" in layers:
                src_cursor += bar

            # phrase / word chops, re-triggered on the grid
            if "vocal_chops" in layers and phrases and bar_idx % 2 == 0:
                s, e = phrases[(bar_idx // 2) % len(phrases)]
                ph = voc[s:e]
                if chop_mode == "words":
                    ph = ph[: int(min(len(ph), beat * 1.5))]
                ph = fade(pitch_shift(ph, chop_pitch), 3, 30)
                if stutter_p and _rng.random() < stutter_p:
                    ph = stutter(ph, step if genre != "footwork" else step / 2, reps=4 if genre != "footwork" else 8)
                ph = ph[: 2 * bar]
                mix_at(out, ph, start, 0.85)

            # riser into the next drop (last two bars of the section)
            if ("riser" in layers or leads_to_drop) and b == nbars - 2 and nbars >= 2:
                mix_at(out, riser(2 * bar), start, 1.0)

            bar_idx += 1

    # --- master: hp, gentle saturation, normalise
    out = highpass(out, 28)
    out = out / (float(np.max(np.abs(out))) + 1e-9)          # peak to 1.0
    out = np.tanh(out * 1.05) / np.tanh(1.05)                 # gentle glue, not a brickwall
    out = (out * 0.89).astype(np.float32)[: total_bars * bar + SR * 2]
    return out, {"stretch_ratio": round(ratio, 4), "phrases": len(phrases), "inst_slices": len(slices),
                 "bars": total_bars, "seconds": round(len(out) / SR, 1)}
DOTLESS_EOF

cat > pipeline.py <<'DOTLESS_EOF'
"""Job pipeline: fetch -> separate -> analyze -> plan -> render.

Each job lives in jobs/<id>/ with params.json (input) and job.json (status + outputs).
"""
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

import analysis
import engine
import planner
from audio_io import any_to_wav, read_wav, to_mp3, write_wav

STAGES = ["fetch", "separate", "analyze", "plan", "render", "done"]


def _update(job_dir: Path, **fields):
    p = job_dir / "job.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    data.update(fields)
    data["updated_at"] = time.time()
    p.write_text(json.dumps(data, indent=2))
    return data


def _run(cmd, log_path):
    """Run a subprocess, append its output to log_path, raise with the tail on failure."""
    with open(log_path, "a") as log:
        log.write("\n$ " + " ".join(str(c) for c in cmd) + "\n")
        proc = subprocess.run([str(c) for c in cmd], stdout=log, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        tail = Path(log_path).read_text()[-1500:]
        raise RuntimeError("command failed (%s): %s" % (cmd[0], tail))


def fetch_youtube(url, job_dir, log):
    raw = job_dir / "source_raw.%(ext)s"
    cmd = ["yt-dlp", "--no-playlist", "-f", "bestaudio/best", "-x", "--audio-format", "wav",
           "--audio-quality", "0", "-o", raw, url]
    cookies = os.environ.get("DOTLESS_YT_COOKIES")
    if cookies and Path(cookies).exists():
        cmd += ["--cookies", cookies]
    _run(cmd, log)
    raw_wav = job_dir / "source_raw.wav"
    if not raw_wav.exists():
        raise RuntimeError("yt-dlp finished but source_raw.wav is missing")
    title = ""
    try:
        title = subprocess.run(["yt-dlp", "--no-playlist", "--skip-download", "--print", "title", url],
                               capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        pass
    return raw_wav, title


def separate(source_wav: Path, job_dir: Path, log):
    """Demucs two-stem split (vocals / no_vocals). CPU by default; set DOTLESS_DEVICE=cuda on a GPU box."""
    out = job_dir / "sep"
    model = os.environ.get("DOTLESS_DEMUCS_MODEL", "htdemucs")
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", model,
           "-d", os.environ.get("DOTLESS_DEVICE", "cpu"), "--segment", "7", "-o", out, source_wav]
    _run(cmd, log)
    stem_dir = out / model / source_wav.stem
    voc, inst = stem_dir / "vocals.wav", stem_dir / "no_vocals.wav"
    if not (voc.exists() and inst.exists()):
        raise RuntimeError("demucs produced no stems in %s" % stem_dir)
    return voc, inst


def run_job(job_dir: Path):
    job_dir = Path(job_dir)
    params = json.loads((job_dir / "params.json").read_text())
    log = job_dir / "pipeline.log"
    try:
        _update(job_dir, status="running", stage="fetch", progress=5, error=None)
        if params.get("youtube_url"):
            raw, title = fetch_youtube(params["youtube_url"], job_dir, log)
        else:
            raw, title = job_dir / params["upload"], params.get("upload", "")
        source = any_to_wav(raw, job_dir / "source.wav")
        _update(job_dir, title=title, stage="separate", progress=15)

        vocals, inst = separate(Path(source), job_dir, log)
        _update(job_dir, stage="analyze", progress=60)

        info = analysis.analyze(source, inst)
        (job_dir / "analysis.json").write_text(json.dumps(info, indent=2))
        _update(job_dir, stage="plan", progress=70, analysis={k: info[k] for k in ("bpm", "key_root", "mode")})

        recipe = planner.make_recipe(params.get("description", ""), params.get("genre"), info)
        (job_dir / "recipe.json").write_text(json.dumps(recipe, indent=2))
        _update(job_dir, stage="render", progress=78, recipe_notes=recipe.get("notes"))

        voc, _ = read_wav(vocals)
        ins, _ = read_wav(inst)
        mix, stats = engine.render(recipe, voc, ins, info["bpm"], info.get("chords"))
        write_wav(job_dir / "remix.wav", mix)
        to_mp3(job_dir / "remix.wav", job_dir / "remix.mp3")
        to_mp3(vocals, job_dir / "vocals.mp3")
        to_mp3(inst, job_dir / "instrumental.mp3")
        _update(job_dir, status="done", stage="done", progress=100, render=stats,
                files=["remix.mp3", "remix.wav", "vocals.mp3", "instrumental.mp3", "recipe.json", "analysis.json"])
    except Exception as exc:
        with open(log, "a") as f:
            f.write(traceback.format_exc())
        _update(job_dir, status="error", error=str(exc)[:1500])
DOTLESS_EOF

cat > app.py <<'DOTLESS_EOF'
""".less remix server - HTTP API.

POST /jobs            {"youtube_url": "...", "description": "...", "genre": "ukg"|"dnb"|"footwork"|"house"|null}
POST /jobs/upload     multipart: file, description, genre
GET  /jobs/{id}       status json (queued|running|done|error, stage, progress, files)
GET  /jobs/{id}/files/{name}
GET  /health

Set DOTLESS_API_KEY to require an X-API-Key header on every call.
"""
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import pipeline

JOBS = Path(os.environ.get("DOTLESS_JOBS", "jobs")).resolve()
JOBS.mkdir(parents=True, exist_ok=True)
API_KEY = os.environ.get("DOTLESS_API_KEY")
ALLOWED_FILES = {"remix.mp3", "remix.wav", "vocals.mp3", "instrumental.mp3", "recipe.json", "analysis.json", "pipeline.log"}

app = FastAPI(title=".less remix server", version="0.1")
_queue: "queue.Queue[Path]" = queue.Queue()


def _worker():
    while True:
        job_dir = _queue.get()
        try:
            pipeline.run_job(job_dir)
        finally:
            _queue.task_done()


threading.Thread(target=_worker, daemon=True, name="dotless-worker").start()


def auth(x_api_key: Optional[str] = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="bad api key")


class JobIn(BaseModel):
    youtube_url: str
    description: str = ""
    genre: Optional[str] = None


def _create(params: dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    d = JOBS / job_id
    d.mkdir()
    (d / "params.json").write_text(json.dumps(params))
    (d / "job.json").write_text(json.dumps({"id": job_id, "status": "queued", "stage": "queued",
                                            "progress": 0, "created_at": time.time(),
                                            "queue_position": _queue.qsize() + 1}))
    _queue.put(d)
    return job_id


@app.get("/health")
def health():
    return {"ok": True, "queued": _queue.qsize()}


@app.post("/jobs", dependencies=[Depends(auth)])
def create_job(body: JobIn):
    job_id = _create(body.model_dump())
    return {"id": job_id, "status": "queued"}


@app.post("/jobs/upload", dependencies=[Depends(auth)])
async def create_upload(file: UploadFile = File(...), description: str = Form(""),
                        genre: Optional[str] = Form(None)):
    job_id = uuid.uuid4().hex[:12]
    d = JOBS / job_id
    d.mkdir()
    suffix = Path(file.filename or "upload.mp3").suffix or ".mp3"
    name = "upload" + suffix
    (d / name).write_bytes(await file.read())
    (d / "params.json").write_text(json.dumps({"upload": name, "description": description, "genre": genre}))
    (d / "job.json").write_text(json.dumps({"id": job_id, "status": "queued", "stage": "queued",
                                            "progress": 0, "created_at": time.time()}))
    _queue.put(d)
    return {"id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def get_job(job_id: str):
    p = JOBS / job_id / "job.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="no such job")
    return json.loads(p.read_text())


@app.get("/jobs/{job_id}/files/{name}", dependencies=[Depends(auth)])
def get_file(job_id: str, name: str):
    if name not in ALLOWED_FILES:
        raise HTTPException(status_code=404, detail="unknown file")
    p = JOBS / job_id / name
    if not p.exists():
        raise HTTPException(status_code=404, detail="not ready")
    return FileResponse(str(p), filename=name)
DOTLESS_EOF

cat > README.md <<'DOTLESS_EOF'
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
DOTLESS_EOF

chmod +x install.sh run.sh
echo
echo 'files written - running install.sh (~10 min)'
bash install.sh
echo
echo 'DONE. now:  cp .env.example .env && nano .env && ./run.sh'
