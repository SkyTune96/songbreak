"""Local HTTP API that the desktop app talks to.

One worker thread runs analysis jobs one at a time (the GPU can only hold one stage's
models), and every state change is pushed to the app over Server-Sent Events.
Only binds to 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__
from .pipeline import WEIGHTS, Cancelled, Options, overall

_ID_RE = re.compile(r"^[\w\-. ()&',!]+$")
_LOG_LINES = 200


class JobIn(BaseModel):
    path: str
    options: dict = {}
    song_id: str | None = None  # set to re-analyse an existing song into the same folder


class MashupIn(BaseModel):
    vocals: str  # song id the vocal comes from
    instrumental: str  # song id the backing comes from
    semitones: int | None = None  # None = match keys automatically
    offset_bars: int = 0
    song_id: str | None = None  # set to re-render an existing mashup in place


class Hub:
    """Fans events out to every connected SSE client; safe to call from the worker thread."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.subs: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self._fan, event)

    def _fan(self, event: dict) -> None:
        for q in list(self.subs):
            q.put_nowait(event)


class Library:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def dir(self, song_id: str) -> Path:
        if not _ID_RE.match(song_id) or ".." in song_id:
            raise HTTPException(400, "bad song id")
        return self.root / song_id

    def new_id(self, title: str, source: Path) -> str:
        base = re.sub(r"[^\w\-. ()&',!]+", " ", title).strip()[:80] or "song"
        song_id, n = base, 2
        while (self.root / song_id).exists():
            a = self.root / song_id / "analysis.json"
            if a.exists() and json.loads(a.read_text(encoding="utf-8")).get("source") == str(source.resolve()):
                return song_id  # same file analysed before: reuse its folder and caches
            song_id, n = f"{base} ({n})", n + 1
        return song_id

    def summary(self, song_id: str) -> dict | None:
        a = self.root / song_id / "analysis.json"
        if not a.exists():
            return None
        d = json.loads(a.read_text(encoding="utf-8"))
        st, tags, story = d.get("structure") or {}, d.get("instruments") or {}, d.get("story") or {}
        return {
            "id": song_id, "title": d.get("title", song_id), "duration": st.get("duration"),
            "tempo": st.get("tempo_bpm"), "key": st.get("key"),
            "genre": [g for g, _ in (tags.get("song", {}).get("genre") or [])[:2]],
            "sections": len(st.get("sections", [])), "has_lyrics": bool((d.get("lyrics") or {}).get("lines")),
            "has_story": "llm" in story, "analyzed_at": d.get("analyzed_at"),
            "summary": (story.get("llm") or {}).get("one_line_summary"),
            "kind": d.get("kind", "song"),
        }

    def list(self) -> list[dict]:
        out = [s for d in self.root.iterdir() if d.is_dir() and (s := self.summary(d.name))]
        return sorted(out, key=lambda s: s.get("analyzed_at") or "", reverse=True)


class Worker(threading.Thread):
    def __init__(self, library: Library, hub: Hub) -> None:
        super().__init__(daemon=True, name="songbreak-worker")
        self.library, self.hub = library, hub
        self.jobs: dict[str, dict] = {}
        self.pending: queue.Queue[str] = queue.Queue()
        self.lock = threading.Lock()

    def public(self, job: dict) -> dict:
        return {k: v for k, v in job.items() if k not in ("cancel", "log")}

    def emit(self, job: dict) -> None:
        self.hub.publish({"type": "job", "job": self.public(job)})

    def submit(self, body: JobIn) -> dict:
        src = Path(body.path)
        if not src.is_file():
            raise HTTPException(404, f"file not found: {src}")
        song_id = body.song_id or self.library.new_id(src.stem, src)
        self.library.dir(song_id)
        return self._enqueue({"kind": "analyze", "song_id": song_id, "title": src.stem, "source": str(src),
                              "options": body.options})

    def submit_mashup(self, body: MashupIn) -> dict:
        for sid in (body.vocals, body.instrumental):
            if not (self.library.dir(sid) / "analysis.json").exists():
                raise HTTPException(404, f"not analysed: {sid}")
        title = f"{body.vocals} × {body.instrumental}"
        song_id = body.song_id or self.library.new_id(title, Path(title))
        return self._enqueue({"kind": "mashup", "song_id": song_id, "title": title, "source": "",
                              "options": body.model_dump()})

    def _enqueue(self, fields: dict) -> dict:
        job = {"id": uuid.uuid4().hex[:12], **fields, "status": "queued", "stage": None, "stage_progress": 0.0,
               "progress": 0.0, "message": "Waiting in queue", "error": None,
               "created": time.time(), "started": None, "finished": None, "cancel": False, "log": []}
        with self.lock:
            self.jobs[job["id"]] = job
        self.pending.put(job["id"])
        self.emit(job)
        return self.public(job)

    def cancel(self, job_id: str) -> dict:
        job = self.jobs.get(job_id) or {}
        if not job:
            raise HTTPException(404, "no such job")
        job["cancel"] = True
        if job["status"] == "queued":
            job.update(status="cancelled", message="Cancelled", finished=time.time())
        else:
            job["message"] = "Cancelling after the current stage..."
        self.emit(job)
        return self.public(job)

    def run(self) -> None:
        while True:
            job = self.jobs[self.pending.get()]
            if job["status"] != "queued":
                continue
            job.update(status="running", started=time.time(), message="Starting")
            self.emit(job)
            last_emit = [0.0]

            def progress(stage: str, frac: float, msg: str) -> None:
                total = overall(stage, frac) if stage in WEIGHTS else frac  # mashups are a single stage
                job.update(stage=stage, stage_progress=round(frac, 3), progress=round(total, 3),
                           message=msg or job["message"])
                now = time.time()
                if now - last_emit[0] > 0.25 or frac in (0.0, 1.0):  # don't flood the UI from per-chunk callbacks
                    last_emit[0] = now
                    self.emit(job)

            def log(line: str) -> None:
                print(line, flush=True)
                job["log"] = (job["log"] + [line])[-_LOG_LINES:]

            try:
                if job["kind"] == "mashup":
                    from .mix import render_mashup
                    o = job["options"]
                    render_mashup(self.library.root, o["vocals"], o["instrumental"], job["song_id"],
                                  semitones=o.get("semitones"), offset_bars=o.get("offset_bars", 0),
                                  progress=lambda f, msg: progress("mashup", f, msg))
                else:
                    from .pipeline import run as run_pipeline
                    run_pipeline(Path(job["source"]), self.library.dir(job["song_id"]),
                                 Options.from_dict(job["options"]), progress=progress,
                                 cancelled=lambda: job["cancel"], log=log)
                job.update(status="done", progress=1.0, message="Done")
            except Cancelled:
                job.update(status="cancelled", message="Cancelled")
            except Exception as e:  # report every failure to the app instead of killing the worker
                import traceback
                log(traceback.format_exc())
                job.update(status="error", error=f"{type(e).__name__}: {e}", message="Failed")
            finally:
                from .hardware import release
                release()
            job["finished"] = time.time()
            self.emit(job)
            if job["status"] == "done":
                self.hub.publish({"type": "library"})


def _system_info() -> dict:
    import torch
    from .hardware import detect
    p = detect()
    return {"profile": p.to_dict(), "torch": torch.__version__, "cuda": torch.version.cuda,
            "cuda_available": torch.cuda.is_available()}


def create_app(library_root: Path) -> FastAPI:
    hub = Hub()
    library = Library(library_root)
    worker = Worker(library, hub)
    system: dict = {}

    @asynccontextmanager
    async def lifespan(_app):
        hub.loop = asyncio.get_running_loop()
        worker.start()
        # torch import + GPU probe take a few seconds; don't hold up startup for them.
        threading.Thread(target=lambda: system.update(_system_info()), daemon=True).start()
        yield

    app = FastAPI(title="songbreak", version=__version__, lifespan=lifespan)
    # The renderer is served from file:// or the Vite dev server; both are local.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "ready": bool(system)}

    @app.get("/api/system")
    def get_system(ollama_host: str = "http://localhost:11434"):
        try:
            r = requests.get(f"{ollama_host}/api/tags", timeout=2)
            ollama = {"running": True, "models": [m["name"] for m in r.json().get("models", [])]}
        except requests.RequestException:
            ollama = {"running": False, "models": []}
        return {**system, "ready": bool(system), "ollama": ollama, "library": str(library.root),
                "version": __version__}

    @app.get("/api/library")
    def get_library():
        return library.list()

    @app.get("/api/songs/{song_id}")
    def get_song(song_id: str):
        d = library.dir(song_id)
        a = d / "analysis.json"
        if not a.exists():
            raise HTTPException(404, "not analysed")
        data = json.loads(a.read_text(encoding="utf-8"))
        data.update(id=song_id, dir=str(d), source_file=next((p.name for p in d.glob("source.*")), None))
        return data

    @app.get("/api/songs/{song_id}/peaks")
    def get_peaks(song_id: str):
        d = library.dir(song_id)
        p = d / "peaks.json"
        if not p.exists():
            from .pipeline import write_peaks
            return write_peaks(d)
        return json.loads(p.read_text(encoding="utf-8"))

    @app.get("/api/songs/{song_id}/file/{rel:path}")
    def get_file(song_id: str, rel: str):
        d = library.dir(song_id).resolve()
        f = (d / rel).resolve()
        if d not in f.parents or not f.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(f)

    @app.delete("/api/songs/{song_id}")
    def delete_song(song_id: str):
        d = library.dir(song_id)
        if any(j["song_id"] == song_id and j["status"] in ("queued", "running") for j in worker.jobs.values()):
            raise HTTPException(409, "song is being analysed")
        if d.exists():
            shutil.rmtree(d)
        hub.publish({"type": "library"})
        return {"ok": True}

    @app.post("/api/jobs")
    def post_job(body: JobIn):
        return worker.submit(body)

    @app.get("/api/songs/{song_id}/matches")
    def get_matches(song_id: str):
        if not (library.dir(song_id) / "analysis.json").exists():
            raise HTTPException(404, "not analysed")
        from .mix import matches
        return matches(song_id, library.root)

    @app.post("/api/mashups")
    def post_mashup(body: MashupIn):
        return worker.submit_mashup(body)

    @app.get("/api/jobs")
    def get_jobs():
        return [worker.public(j) for j in sorted(worker.jobs.values(), key=lambda j: j["created"])]

    @app.get("/api/jobs/{job_id}/log")
    def get_job_log(job_id: str):
        job = worker.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "no such job")
        return {"log": job["log"]}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return worker.cancel(job_id)

    @app.get("/api/events")
    async def events():
        q: asyncio.Queue = asyncio.Queue()
        hub.subs.add(q)

        async def stream():
            try:
                yield f"data: {json.dumps({'type': 'hello', 'jobs': get_jobs()})}\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                hub.subs.discard(q)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _watch_parent(pid: int) -> None:
    """Exit when the desktop app goes away, so no orphaned server keeps holding the GPU."""
    import psutil
    while psutil.pid_exists(pid):
        time.sleep(2)
    os._exit(0)


def serve(library: Path, port: int = 0, parent_pid: int | None = None) -> None:
    import uvicorn
    port = port or _free_port()
    if parent_pid:
        threading.Thread(target=_watch_parent, args=(parent_pid,), daemon=True).start()
    # The app reads this line from stdout to learn where to connect.
    print(f"SONGBREAK_READY {json.dumps({'port': port, 'library': str(library)})}", flush=True)
    uvicorn.run(create_app(library), host="127.0.0.1", port=port, log_level="warning")
