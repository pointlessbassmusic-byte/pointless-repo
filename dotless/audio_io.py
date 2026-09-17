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
