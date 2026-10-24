"""The analysis pipeline, shared by the CLI and the app's server.

`run()` reports progress through a callback as (stage, fraction 0..1, message), and checks
`cancelled()` between stages so a queued app job can be stopped without killing the process.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

STAGES = ["load", "stems", "lyrics", "structure", "instruments", "story", "peaks"]
# Rough share of wall time per stage on a GPU, so one bar can show the whole job.
WEIGHTS = {"load": 2, "stems": 30, "lyrics": 35, "structure": 8, "instruments": 6, "story": 17, "peaks": 2}
PEAK_BUCKETS = 2000

Progress = Callable[[str, float, str], None]


class Cancelled(Exception):
    pass


@dataclass
class Options:
    stems: int = 6
    skip: set[str] = field(default_factory=set)
    language: str | None = None
    llm: str = "ollama"
    llm_model: str | None = None
    ollama_host: str = "http://localhost:11434"
    force: bool = False
    profile: str = "auto"

    @classmethod
    def from_dict(cls, d: dict) -> "Options":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and v is not None}
        if "skip" in known:
            known["skip"] = set(known["skip"])
        return cls(**known)


def overall(stage: str, frac: float) -> float:
    done = sum(WEIGHTS[s] for s in STAGES[:STAGES.index(stage)])
    return (done + WEIGHTS[stage] * min(max(frac, 0.0), 1.0)) / sum(WEIGHTS.values())


def _jsonable(o):
    if isinstance(o, Path):
        return str(o)
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


def _peaks(audio: np.ndarray, buckets: int = PEAK_BUCKETS) -> list[float]:
    """Max absolute amplitude per bucket, for drawing waveforms without decoding audio."""
    y = np.abs(audio).max(axis=0) if audio.ndim == 2 else np.abs(audio)
    edges = np.linspace(0, len(y), buckets + 1).astype(int)
    return [round(float(y[a:b].max()) if b > a else 0.0, 3) for a, b in zip(edges[:-1], edges[1:])]


def write_peaks(out: Path, mix: np.ndarray | None = None) -> dict:
    from . import audio_io
    peaks = {}
    if mix is None and (src := next(out.glob("source.*"), None)):
        mix = audio_io.load(src, sr=22050, channels=1)
    if mix is not None:
        peaks["mix"] = _peaks(mix)
    for p in sorted((out / "stems").glob("*.wav")):
        audio, _ = audio_io.read(p)
        peaks[p.stem] = _peaks(audio)
    (out / "peaks.json").write_text(json.dumps(peaks), encoding="utf-8")
    return peaks


def run(src: Path, out: Path, opts: Options, progress: Progress | None = None,
        cancelled: Callable[[], bool] | None = None, log: Callable[[str], None] = print) -> dict:
    from . import audio_io, report
    from . import story as story_mod
    from .hardware import detect, peak_vram_gb

    progress = progress or (lambda stage, frac, msg: None)
    cancelled = cancelled or (lambda: False)
    out.mkdir(parents=True, exist_ok=True)
    profile = detect(opts.profile)
    log(f"[songbreak] {profile.gpu} | {profile.vram_gb} GB VRAM | {profile.ram_gb} GB RAM -> profile '{profile.name}'")

    result: dict = {"title": src.stem, "source": str(src.resolve()), "profile": profile.to_dict(),
                    "options": {**opts.__dict__, "skip": sorted(opts.skip)}, "timings": {},
                    "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def stage(name: str, fn, msg: str = ""):
        if cancelled():
            raise Cancelled()
        log(f"[songbreak] {name} ...")
        progress(name, 0.0, msg)
        t0 = time.perf_counter()
        value = fn()
        result["timings"][name] = {"seconds": round(time.perf_counter() - t0, 1), "peak_vram_gb": peak_vram_gb()}
        log(f"[songbreak] {name} done in {result['timings'][name]['seconds']} s")
        progress(name, 1.0, "done")
        return value

    # Keep a copy of the original next to the analysis so the app can play it back.
    local_src = out / f"source{src.suffix.lower()}"
    if not local_src.exists():
        shutil.copy2(src, local_src)
    mix = stage("load", lambda: audio_io.load(src, sr=44100), "Decoding audio")

    stem_paths = None
    if "stems" not in opts.skip:
        from . import stems
        stem_dir = out / "stems"
        stem_paths = None if opts.force else stems.load_existing(stem_dir)
        if stem_paths:
            log("[songbreak] reusing stems from a previous run")
            progress("stems", 1.0, "Reused from previous run")
        else:
            stem_paths = stage("stems", lambda: stems.separate(
                mix, profile, stem_dir, six_stems=opts.stems == 6,
                on_progress=lambda f: progress("stems", f, "Separating instruments")), "Loading Demucs")
        result["stems"] = {k: str(v.relative_to(out)) for k, v in stem_paths.items()}

    lines: list[dict] = []
    if "lyrics" not in opts.skip and stem_paths:
        from . import lyrics
        cache = out / "lyrics.json"
        if cache.exists() and not opts.force:
            log("[songbreak] reusing lyrics from a previous run")
            progress("lyrics", 1.0, "Reused from previous run")
            lyr = json.loads(cache.read_text(encoding="utf-8"))
        else:
            lyr = stage("lyrics", lambda: lyrics.transcribe(
                stem_paths["vocals"], profile, opts.language,
                on_progress=lambda f: progress("lyrics", f, "Transcribing vocals")), "Loading Whisper")
            cache.write_text(json.dumps(lyr, indent=1), encoding="utf-8")
        lines = lyr["lines"]
        result["lyrics"] = lyr
        (out / "lyrics.lrc").write_text(lyrics.to_lrc(lines), encoding="utf-8")
        (out / "lyrics.txt").write_text(lyr["text"] + "\n", encoding="utf-8")

    structure = None
    if "structure" not in opts.skip:
        from . import structure as structure_mod
        structure = stage("structure", lambda: structure_mod.analyze(mix, 44100, stem_paths, lines),
                          "Finding beats, key and sections")
        result["structure"] = structure

    tags = None
    if "instruments" not in opts.skip and structure:
        from . import instruments
        tags = stage("instruments", lambda: instruments.tag(
            mix, 44100, stem_paths, structure["sections"], profile,
            on_progress=lambda f: progress("instruments", f, "Naming instruments")), "Loading classifier")
        result["instruments"] = tags

    if structure:
        heur = story_mod.heuristics(structure, lines)
        story = {"heuristics": heur}
        prompt = story_mod.build_prompt(src.stem, structure, tags, lines, heur)
        (out / "story_prompt.md").write_text(prompt, encoding="utf-8")
        if "story" not in opts.skip and opts.llm == "ollama":
            available = story_mod.ollama_models(opts.ollama_host)
            model = opts.llm_model or profile.llm_model
            if available is None:
                story["note"] = ("Ollama isn't running, so only the heuristic story analysis ran. "
                                 f"Install Ollama and `ollama pull {model}`, or paste story_prompt.md into any chatbot.")
            elif not any(m == model or m.split(":")[0] == model for m in available):
                story["note"] = f"Ollama is running but `{model}` isn't pulled. Run `ollama pull {model}`."
            else:
                try:
                    story["llm"] = stage("story", lambda: story_mod.ask_ollama(prompt, model, opts.ollama_host),
                                         f"Asking {model}")
                    story["llm_model"] = model
                    for fix in story["llm"].get("section_labels", []):
                        i = fix.get("index")
                        if isinstance(i, int) and 0 <= i < len(structure["sections"]) and fix.get("label"):
                            structure["sections"][i]["label_auto"] = structure["sections"][i]["label"]
                            structure["sections"][i]["label"] = fix["label"]
                except Exception as e:  # a bad LLM reply shouldn't lose the rest of the analysis
                    story["note"] = f"LLM story step failed: {e}"
        result["story"] = story
        if story.get("note"):
            log(f"[songbreak] {story['note']}")

    def finish():
        write_peaks(out, mix[:, ::2])
        if structure:
            from .chords import detect
            (out / "chords.json").write_text(json.dumps(detect(out, result)), encoding="utf-8")

    stage("peaks", finish, "Drawing waveforms and chords")
    (out / "analysis.json").write_text(json.dumps(result, indent=2, default=_jsonable), encoding="utf-8")
    (out / "report.md").write_text(report.markdown(result), encoding="utf-8")
    log(f"[songbreak] wrote {out / 'report.md'}")
    return result
