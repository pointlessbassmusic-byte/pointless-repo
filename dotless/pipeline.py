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
