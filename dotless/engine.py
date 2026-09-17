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
