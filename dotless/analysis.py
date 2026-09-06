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
