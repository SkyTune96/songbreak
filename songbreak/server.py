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


class FolderIn(BaseModel):
    name: str | None = None  # None = suggest one from the songs' analysis
    songs: list[str] = []
    llm_model: str | None = None
    ollama_host: str = "http://localhost:11434"


class AssignIn(BaseModel):
    folder_id: str | None


class PlaylistIn(BaseModel):
    songs: list[str]
    arc: str = "rise_resolve"
    name: str | None = None
    llm_model: str | None = None
    ollama_host: str = "http://localhost:11434"


class RenameIn(BaseModel):
    name: str


class ExportIn(BaseModel):
    dest: str
    stems: list[str]
    format: str = "wav"
    split_sections: bool = False
    include_original: bool = False


def llm_available(model: str | None, host: str, default: str | None) -> str | None:
    """The Ollama model to use, or None if Ollama isn't running or the model isn't pulled."""
    model = model or default
    try:
        names = [m["name"] for m in requests.get(f"{host}/api/tags", timeout=2).json().get("models", [])]
    except requests.RequestException:
        return None
    return model if model and any(n == model or n.split(":")[0] == model for n in names) else None


class AutoIn(BaseModel):
    llm_model: str | None = None
    ollama_host: str = "http://localhost:11434"


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
        title = title.replace("×", "x")  # mashup titles; × isn't allowed in song ids
        base = re.sub(r"\s+", " ", re.sub(r"[^\w\-. ()&',!]+", " ", title)).strip()[:80] or "song"
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

    def submit_playlist(self, body: PlaylistIn) -> dict:
        pid = uuid.uuid4().hex[:10]
        return self._enqueue({"kind": "playlist", "song_id": pid, "title": body.name or "Story playlist",
                              "source": "", "options": body.model_dump()})

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
                if job["kind"] == "automix":
                    from .automix import render
                    from .playlists import PlaylistStore
                    store = PlaylistStore(self.library.root)
                    pl = next(p for p in store.load() if p["id"] == job["song_id"])
                    mix = render(self.library.root, pl, self.library.root / "_mixes" / pl["id"],
                                 progress=lambda f, msg: progress("automix", f, msg))
                    pl["automix"] = mix
                    store.put(pl)
                elif job["kind"] == "playlist":
                    from .hardware import detect
                    from .playlists import PlaylistStore, build
                    o = job["options"]
                    model = llm_available(o.get("llm_model"), o["ollama_host"], detect().llm_model)
                    result = build(self.library.root, o["songs"], o["arc"], model, o["ollama_host"], o.get("name"),
                                   progress=lambda f, msg: progress("playlist", f, msg))
                    result["id"] = job["song_id"]
                    PlaylistStore(self.library.root).put(result)
                    job["title"] = result["name"]
                elif job["kind"] == "mashup":
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

    @app.post("/api/songs/{song_id}/export")
    def post_export(song_id: str, body: ExportIn):
        d = library.dir(song_id)
        dest = Path(body.dest)
        if not dest.is_dir():
            raise HTTPException(400, f"not a folder: {dest}")
        from .export import export
        return export(d, dest, body.stems, body.format, body.split_sections, body.include_original)

    practice_lock = threading.Lock()

    @app.post("/api/songs/{song_id}/practice")
    def post_practice(song_id: str, speed: float):
        """Pitch-preserving slowed (or sped-up) stems for practice, cached per speed."""
        if not 0.4 <= speed <= 1.5:
            raise HTTPException(400, "speed must be between 0.4 and 1.5")
        d = library.dir(song_id)
        a = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
        pct = int(round(speed * 100))
        out = d / "practice" / str(pct)
        stems = {}
        with practice_lock:  # one render at a time; a second request for the same speed hits the cache
            from .mix import stretch_shift
            from . import audio_io as aio
            for name, rel in (a.get("stems") or {}).items():
                dst = out / f"{name}.wav"
                if not dst.exists():
                    audio, sr = aio.read(d / rel)
                    y, _ = stretch_shift(audio, sr, speed, 0)
                    out.mkdir(parents=True, exist_ok=True)
                    aio.save(dst, y, sr)
                stems[name] = f"practice/{pct}/{name}.wav"
        return {"stems": stems, "speed": pct / 100}

    @app.post("/api/playlists/{pid}/automix")
    def post_automix(pid: str):
        pl = next((p for p in playlist_store.load() if p["id"] == pid), None)
        if not pl:
            raise HTTPException(404, "no such playlist")
        return worker._enqueue({"kind": "automix", "song_id": pid, "title": f"Auto-mix: {pl['name']}",
                                "source": "", "options": {}})

    @app.get("/api/playlists/{pid}/mix")
    def get_mix(pid: str):
        f = library.root / "_mixes" / pid / "mix.flac"
        if not f.exists():
            raise HTTPException(404, "not mixed yet")
        return FileResponse(f, media_type="audio/flac")

    @app.get("/api/songs/{song_id}/chords")
    def get_chords(song_id: str):
        d = library.dir(song_id)
        if not (d / "analysis.json").exists():
            raise HTTPException(404, "not analysed")
        from .chords import get
        return get(d)

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
        folders.forget(song_id)
        playlist_store.forget_song(song_id)
        hub.publish({"type": "library"})
        return {"ok": True}

    # ---------- playlists ----------
    from .playlists import PlaylistStore, arc_catalog
    playlist_store = PlaylistStore(library.root)

    @app.get("/api/arcs")
    def get_arcs():
        return arc_catalog()

    @app.get("/api/playlists")
    def get_playlists():
        items = playlist_store.load()
        for p in items:  # where each track's audio is, for playback (files can change after a re-analysis)
            for s in p["songs"]:
                d = library.root / s["id"]
                s["source_file"] = next((f.name for f in d.glob("source.*")), None) if d.exists() else None
        return items

    @app.post("/api/playlists")
    def post_playlist(body: PlaylistIn):
        return worker.submit_playlist(body)

    @app.patch("/api/playlists/{pid}")
    def patch_playlist(pid: str, body: RenameIn):
        try:
            p = playlist_store.rename(pid, body.name)
        except KeyError:
            raise HTTPException(404, "no such playlist")
        hub.publish({"type": "library"})
        return p

    @app.delete("/api/playlists/{pid}")
    def delete_playlist(pid: str):
        playlist_store.delete(pid)
        shutil.rmtree(library.root / "_mixes" / pid, ignore_errors=True)
        hub.publish({"type": "library"})
        return {"ok": True}

    # ---------- folders ----------
    from .folders import FolderStore, auto_groups, suggest_name
    folders = FolderStore(library.root)

    def analyses(ids: list[str]) -> list[dict]:
        out = []
        for sid in ids:
            a = library.dir(sid) / "analysis.json"
            if a.exists():
                out.append(json.loads(a.read_text(encoding="utf-8")))
        return out

    def llm_for(model: str | None, host: str) -> str | None:
        """The model to name folders with, or None if Ollama isn't up / the model isn't pulled."""
        return llm_available(model, host, (system.get("profile") or {}).get("llm_model"))

    @app.get("/api/folders")
    def get_folders():
        return folders.load()

    @app.post("/api/folders")
    def post_folder(body: FolderIn):
        name = body.name or suggest_name(analyses(body.songs), llm_for(body.llm_model, body.ollama_host),
                                         body.ollama_host)["name"]
        f = folders.create(name, body.songs)
        hub.publish({"type": "library"})
        return f

    @app.post("/api/folders/suggest")
    def post_suggest(body: FolderIn):
        return suggest_name(analyses(body.songs), llm_for(body.llm_model, body.ollama_host), body.ollama_host)

    @app.patch("/api/folders/{folder_id}")
    def patch_folder(folder_id: str, body: FolderIn):
        try:
            f = folders.rename(folder_id, body.name or "")
        except KeyError:
            raise HTTPException(404, "no such folder")
        hub.publish({"type": "library"})
        return f

    @app.delete("/api/folders/{folder_id}")
    def delete_folder(folder_id: str):
        folders.delete(folder_id)
        hub.publish({"type": "library"})
        return {"ok": True}

    @app.put("/api/songs/{song_id}/folder")
    def put_song_folder(song_id: str, body: AssignIn):
        library.dir(song_id)
        try:
            folders.assign(song_id, body.folder_id)
        except KeyError:
            raise HTTPException(404, "no such folder")
        hub.publish({"type": "library"})
        return {"ok": True}

    @app.post("/api/folders/auto")
    def post_auto(body: AutoIn):
        """File every unfiled song into folders named from their analysis."""
        assigned = folders.load()["assignments"]
        unfiled = [s["id"] for s in library.list() if s["id"] not in assigned]
        data = {sid: a for sid, a in zip(unfiled, analyses(unfiled))}
        model = llm_for(body.llm_model, body.ollama_host)
        created = []
        taken = [f["name"] for f in folders.load()["folders"]]
        for group in auto_groups(data):
            name = suggest_name([data[s] for s in group], model, body.ollama_host, avoid=taken)["name"]
            taken.append(name)
            created.append({**folders.create(name, group), "songs": group})
        hub.publish({"type": "library"})
        return {"created": created, "named_by": "llm" if model else "themes"}

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
