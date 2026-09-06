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
