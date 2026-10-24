"""Export a song's stems (and extras) for use in a DAW or editor.

Writes into <dest>/<song title>/:
  Stems/<title> - <stem>.wav|flac    full-length, all starting at 0:00, so they line up anywhere
  Sections/NN <label>/...           optional: every stem cut per section
  <title>.rpp                        REAPER project: one track per stem, tempo, sections as regions
  <title>.scraft                     optional SoundCraft session (built with its CLI when installed)
  lyrics.lrc, chords.txt, sections.csv, info.txt
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import soundfile as sf

from . import audio_io


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name).strip().rstrip(".")[:100] or "song"


def _fmt_time(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:05.2f}"


def _rpp(title: str, tempo: float, duration: float, tracks: list[tuple[str, str, str]], sections: list[dict]) -> str:
    """A minimal REAPER project. tracks: (name, relative file, source type)."""
    lines = ['<REAPER_PROJECT 0.1 "6.0" 0', f"  TEMPO {tempo:.3f} 4 4"]
    for i, s in enumerate(sections, 1):  # regions: start and end markers sharing an index, flag 1
        lines.append(f'  MARKER {i} {s["start"]:.3f} "{s["label"]}" 1')
        lines.append(f'  MARKER {i} {s["end"]:.3f} "" 1')
    for name, rel, kind in tracks:
        lines += ["  <TRACK", f'    NAME "{name}"', "    <ITEM", "      POSITION 0", f"      LENGTH {duration:.3f}",
                  f'      NAME "{name}"', f"      <SOURCE {kind}", f'        FILE "{rel}"', "      >", "    >", "  >"]
    lines.append(">")
    return "\n".join(lines) + "\n"


# Stem colours, matching the app's mixer.
_COLORS = {"vocals": (255, 92, 138), "drums": (255, 166, 64), "bass": (157, 123, 255), "guitar": (79, 209, 139),
           "piano": (255, 216, 77), "other": (60, 200, 232), "original": (201, 204, 214)}


def soundcraft_cli() -> str | None:
    """SoundCraft's command-line tool, if SoundCraft is installed."""
    candidates = [os.environ.get("SONGBREAK_SOUNDCRAFT_CLI"), shutil.which("soundcraft-cli"),
                  str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "SoundCraft" / "soundcraft-cli.exe")]
    return next((c for c in candidates if c and Path(c).is_file()), None)


def build_soundcraft(out: Path, title: str, tracks: list[tuple[str, Path]], tempo: float, sections: list[dict],
                     sample_rate: int) -> Path:
    """Build a SoundCraft session with the CLI: one coloured track per stem at 0:00, the tempo, and every
    section as a named range. SoundCraft copies the audio into "Audio Files" next to the session."""
    cli = soundcraft_cli()
    if not cli:
        raise FileNotFoundError("SoundCraft isn't installed (soundcraft-cli.exe not found)")
    lines = [f"session.new {json.dumps({'name': title, 'sample_rate': sample_rate})}",
             f"event.tempo {json.dumps({'bpm': round(tempo, 3)})}"]
    for name, path in tracks:
        label = name.capitalize()
        lines.append(f"file.import_audio {json.dumps({'path': path.as_posix(), 'at': 0})}")
        lines.append(f"track.rename {json.dumps({'track': path.stem, 'name': label})}")
        if name in _COLORS:
            lines.append(f"track.color {json.dumps({'tracks': [label], 'color': list(_COLORS[name])})}")
    for s in sections:
        lines.append("markers.add " + json.dumps({"name": s["label"], "start": {"seconds": s["start"]},
                                                  "end": {"seconds": s["end"]}, "kind": "selection"}))
    script = out / "soundcraft_build.txt"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    session = out / f"{title}.scraft"
    r = subprocess.run([cli, "script", str(script), "--save", str(session)], capture_output=True, text=True,
                       timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not session.exists():
        raise RuntimeError(f"SoundCraft couldn't build the session: {(r.stderr or r.stdout).strip()[-400:]}")
    script.unlink()
    return session


def export(song_dir: Path, dest: Path, stems: list[str], fmt: str = "wav", split_sections: bool = False,
           include_original: bool = False, soundcraft: bool = False, progress=None) -> dict:
    progress = progress or (lambda f, m: None)
    a = json.loads((song_dir / "analysis.json").read_text(encoding="utf-8"))
    title = _safe(a.get("title", song_dir.name))
    out, n = dest / title, 2
    while out.exists():
        out, n = dest / f"{title} ({n})", n + 1
    (out / "Stems").mkdir(parents=True)
    ext, subtype, kind = ("flac", "PCM_24", "FLAC") if fmt == "flac" else ("wav", "PCM_24", "WAVE")
    st = a.get("structure") or {}
    sections = st.get("sections") or []

    available = a.get("stems") or {}
    chosen = [s for s in stems if s in available]
    tracks, written = [], []
    for i, name in enumerate(chosen):
        progress(0.1 + 0.6 * i / max(len(chosen), 1), f"Writing {name}")
        audio, sr = audio_io.read(song_dir / available[name])
        rel = f"Stems/{title} - {name}.{ext}"
        sf.write(str(out / rel), audio.T, sr, subtype=subtype)
        tracks.append((name, rel, kind))
        written.append(rel)
        if split_sections:
            for k, sec in enumerate(sections, 1):
                sdir = out / "Sections" / _safe(f"{k:02d} {sec['label']}")
                sdir.mkdir(parents=True, exist_ok=True)
                seg = audio[:, int(sec["start"] * sr):int(sec["end"] * sr)]
                sf.write(str(sdir / f"{_safe(sec['label'])} - {name}.{ext}"), seg.T, sr, subtype=subtype)

    if include_original and (src := next(song_dir.glob("source.*"), None)):
        rel = f"Stems/{title} - original{src.suffix.lower()}"
        shutil.copy2(src, out / rel)
        written.append(rel)
        if src.suffix.lower() in (".wav", ".flac"):
            tracks.append(("original", rel, "WAVE" if src.suffix.lower() == ".wav" else "FLAC"))

    progress(0.8, "Writing lyrics, chords and the project")
    lines = (a.get("lyrics") or {}).get("lines") or []
    if lines:
        (out / "lyrics.lrc").write_text("".join(f"[{int(l['start'] // 60):02d}:{l['start'] % 60:05.2f}]{l['text']}\n"
                                                for l in lines), encoding="utf-8")
    if a.get("structure"):
        from .chords import get as get_chords  # detects and caches them if the song hasn't been opened since
        segs = [c for c in get_chords(song_dir)["segments"] if c["chord"] != "N"]
        (out / "chords.txt").write_text("".join(f"{_fmt_time(c['start'])}\t{c['chord']}\n" for c in segs), encoding="utf-8")
    if sections:
        with open(out / "sections.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["#", "section", "start_s", "end_s"])
            for k, s in enumerate(sections, 1):
                w.writerow([k, s["label"], f"{s['start']:.3f}", f"{s['end']:.3f}"])
    (out / "info.txt").write_text(
        f"{a.get('title')}\nTempo: {st.get('tempo_bpm')} BPM\nKey: {st.get('key')}\nDuration: {_fmt_time(st.get('duration', 0))}\n"
        f"Stems: {', '.join(chosen) or 'none'}\nAll files start at 0:00 and line up when dropped into any editor.\n"
        "Exported by Songbreak (https://github.com/SkyTune96/songbreak)\n", encoding="utf-8")
    project = None
    if tracks:
        project = out / f"{title}.rpp"
        project.write_text(_rpp(title, st.get("tempo_bpm") or 120, st.get("duration") or 0, tracks, sections), encoding="utf-8")
    session, soundcraft_error = None, None
    if soundcraft and tracks:
        progress(0.9, "Building the SoundCraft session")
        sr = sf.info(str(out / tracks[0][1])).samplerate
        try:
            session = build_soundcraft(out, title, [(n, out / rel) for n, rel, _ in tracks],
                                       st.get("tempo_bpm") or 120, sections, sr)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:  # the rest of the export still stands
            soundcraft_error = str(e)
    progress(1.0, "Done")
    return {"folder": str(out), "project": str(project) if project else None, "files": written,
            "sections": len(sections) if split_sections else 0,
            "soundcraft": str(session) if session else None, "soundcraft_error": soundcraft_error}
