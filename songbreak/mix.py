"""Automixer: which songs mix well together, and vocal-over-instrumental mashups.

Everything here works from what the pipeline already saved (tempo, key, beats, sections,
stems, lyrics, story themes), so no model runs again.

A mashup is written into the library as an ordinary song folder (stems, lyrics re-timed to
the new tempo, sections from the instrumental), so the app's mixer and karaoke views work on it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import librosa
import numpy as np

from . import audio_io

SR = 44100
_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_STOP = {"the", "a", "an", "of", "and", "to", "in", "on", "for", "with", "its", "is", "or", "as"}


# ---------- keys ----------

def parse_key(key: str | None) -> tuple[int, str] | None:
    """'A minor' -> (9, 'minor')."""
    m = re.match(r"([A-G]#?)\s+(major|minor)", key or "")
    return (_NOTES.index(m.group(1)), m.group(2)) if m else None


def camelot(key: str | None) -> str | None:
    """DJ notation: neighbours on the wheel (±1 number, or same number A/B) mix harmonically."""
    k = parse_key(key)
    if not k:
        return None
    pc, mode = k
    # C major = 8B, A minor = 8A; each fifth up is +1.
    n = ((pc * 7) % 12 + (8 if mode == "major" else 5)) % 12 or 12
    return f"{n}{'B' if mode == 'major' else 'A'}"


def _camelot_distance(a: str, b: str) -> int:
    na, la, nb, lb = int(a[:-1]), a[-1], int(b[:-1]), b[-1]
    d = min((na - nb) % 12, (nb - na) % 12)
    return d + (0 if la == lb else 1 if d == 0 else 2)


def semitone_shift(src_key: str | None, dst_key: str | None) -> int:
    """Smallest pitch shift (−6..+6) that puts src on dst's key or its relative major/minor."""
    s, d = parse_key(src_key), parse_key(dst_key)
    if not s or not d:
        return 0
    target = d[0] if s[1] == d[1] else (d[0] + (3 if d[1] == "minor" else -3)) % 12
    return int((target - s[0] + 6) % 12 - 6)


def _shift_key(key: str, n: int) -> str:
    pc, mode = parse_key(key)  # type: ignore[misc]
    return f"{_NOTES[(pc + n) % 12]} {mode}"


# ---------- compatibility ----------

def _tempo_ratio(src_bpm: float, dst_bpm: float) -> float:
    """Stretch factor for src to hit dst's tempo, allowing half/double time."""
    return min((dst_bpm / (src_bpm * m) for m in (0.5, 1, 2)), key=lambda r: abs(np.log(r)))


def _themes(a: dict) -> set[str]:
    llm = (a.get("story") or {}).get("llm") or {}
    words = " ".join(llm.get("themes") or []).lower()
    return {w for w in re.findall(r"[a-z']+", words) if w not in _STOP and len(w) > 2}


def compatibility(a: dict, b: dict) -> dict:
    sa, sb = a.get("structure") or {}, b.get("structure") or {}
    ratio = _tempo_ratio(sa.get("tempo_bpm", 120), sb.get("tempo_bpm", 120))
    tempo_score = float(np.exp(-abs(np.log(ratio)) / 0.06))  # 6% stretch ≈ 0.37
    ca, cb = camelot(sa.get("key")), camelot(sb.get("key"))
    key_dist = _camelot_distance(ca, cb) if ca and cb else 3
    key_score = {0: 1.0, 1: 0.85, 2: 0.55}.get(key_dist, 0.3)  # anything can be pitch-shifted, at a cost
    ea = np.mean([s["energy"] for s in sa.get("sections", [])] or [0.5])
    eb = np.mean([s["energy"] for s in sb.get("sections", [])] or [0.5])
    energy_score = 1 - abs(ea - eb)
    ta, tb = _themes(a), _themes(b)
    shared = sorted(ta & tb)
    theme_score = len(shared) / len(ta | tb) if ta and tb else 0.0
    score = 0.4 * tempo_score + 0.3 * key_score + 0.15 * energy_score + 0.15 * min(1.0, theme_score * 3)
    return {
        "score": round(score, 3),
        "tempo": {"from": sa.get("tempo_bpm"), "to": sb.get("tempo_bpm"), "stretch": round(ratio, 3),
                  "score": round(tempo_score, 2)},
        "key": {"from": sa.get("key"), "to": sb.get("key"), "camelot": [ca, cb], "distance": key_dist,
                "shift": semitone_shift(sa.get("key"), sb.get("key")), "score": key_score},
        "energy": round(float(energy_score), 2),
        "shared_themes": shared,
    }


def matches(song_id: str, library_root: Path) -> list[dict]:
    a = json.loads((library_root / song_id / "analysis.json").read_text(encoding="utf-8"))
    out = []
    for d in library_root.iterdir():
        f = d / "analysis.json"
        if d.name == song_id or not f.exists():
            continue
        b = json.loads(f.read_text(encoding="utf-8"))
        if b.get("kind") == "mashup":
            continue
        c = compatibility(a, b)
        c.update(id=d.name, title=b.get("title", d.name), has_vocals=bool((b.get("lyrics") or {}).get("lines")))
        out.append(c)
    return sorted(out, key=lambda c: -c["score"])


# ---------- rendering ----------

def _rubberband() -> str | None:
    return shutil.which("rubberband") or shutil.which("rubberband-r3")


def stretch_shift(audio: np.ndarray, sr: int, rate: float, semitones: int) -> tuple[np.ndarray, str]:
    """Time-stretch by `rate` (>1 = faster) and pitch-shift. Rubber Band if installed, else librosa."""
    if abs(rate - 1) < 1e-3 and semitones == 0:
        return audio, "none"
    rb = _rubberband()
    if rb:
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "in.wav", Path(tmp) / "out.wav"
            audio_io.save(src, audio, sr)
            # -t is a duration ratio; --fine is the R3 engine, best for vocals.
            subprocess.run([rb, "-q", "--fine", "-t", f"{1 / rate:.6f}", "-p", str(semitones), str(src), str(dst)],
                           check=True, capture_output=True)
            out, _ = audio_io.read(dst)
        return out, "rubberband"
    chans = []
    for ch in audio:
        y = librosa.effects.time_stretch(ch, rate=rate) if abs(rate - 1) >= 1e-3 else ch
        if semitones:
            y = librosa.effects.pitch_shift(y, sr=sr, n_steps=semitones)
        chans.append(y)
    n = min(len(c) for c in chans)
    return np.stack([c[:n] for c in chans]), "librosa"


def downbeats(analysis: dict, drums: np.ndarray | None) -> np.ndarray:
    """Every 4th beat, choosing the phase where the drums hit hardest (the kick usually lands on 1)."""
    beats = np.array((analysis.get("structure") or {}).get("beats") or [])
    if len(beats) < 8:
        return beats
    if drums is None:
        return beats[::4]
    low = librosa.feature.rms(y=drums, frame_length=2048, hop_length=512)[0]
    frames = np.clip(librosa.time_to_frames(beats, sr=SR, hop_length=512), 0, len(low) - 1)
    strength = [low[frames[p::4]].mean() for p in range(4)]
    return beats[int(np.argmax(strength))::4]


def _load_stem(song_dir: Path, analysis: dict, name: str) -> np.ndarray | None:
    rel = (analysis.get("stems") or {}).get(name)
    return audio_io.read(song_dir / rel)[0] if rel and (song_dir / rel).exists() else None


def _first_vocal_time(a: dict) -> float:
    lines = (a.get("lyrics") or {}).get("lines") or []
    return lines[0]["start"] if lines else 0.0


def _target_time(b: dict) -> float:
    """Where the vocal should come in over the instrumental: the first section after its intro."""
    secs = (b.get("structure") or {}).get("sections") or []
    for s in secs:
        if s.get("vocal") or (s is not secs[0] and not s["label"].startswith("Intro")):
            return s["start"]
    return secs[1]["start"] if len(secs) > 1 else 0.0


def _nearest(arr: np.ndarray, t: float, before: bool = False) -> float:
    if not len(arr):
        return t
    if before:
        prior = arr[arr <= t + 0.05]
        return float(prior[-1]) if len(prior) else float(arr[0])
    return float(arr[np.argmin(np.abs(arr - t))])


def render_mashup(library: Path, vocals_id: str, inst_id: str, out_id: str,
                  semitones: int | None = None, offset_bars: int = 0, progress=None) -> dict:
    """Vocals of `vocals_id` over the backing stems of `inst_id`, tempo- and key-matched."""
    progress = progress or (lambda f, msg: None)
    da, db = library / vocals_id, library / inst_id
    a = json.loads((da / "analysis.json").read_text(encoding="utf-8"))
    b = json.loads((db / "analysis.json").read_text(encoding="utf-8"))
    sa, sb = a["structure"], b["structure"]

    progress(0.05, "Loading stems")
    vocals = _load_stem(da, a, "vocals")
    if vocals is None:
        raise ValueError(f"{a['title']} has no vocal stem")
    backing = {n: s for n in ("drums", "bass", "guitar", "piano", "other")
               if (s := _load_stem(db, b, n)) is not None}
    if not backing:
        raise ValueError(f"{b['title']} has no instrumental stems")

    rate = _tempo_ratio(sa["tempo_bpm"], sb["tempo_bpm"])
    shift = semitone_shift(sa.get("key"), sb.get("key")) if semitones is None else semitones
    progress(0.15, f"Stretching vocals ×{rate:.3f}, shifting {shift:+d} semitones")
    vocals_t, engine = stretch_shift(vocals, SR, rate, shift)

    # Line the vocal entry's bar up with the downbeat where the instrumental's first real section starts.
    progress(0.7, "Aligning to the beat")
    da_beats = downbeats(a, audio_io.to_mono(_load_stem(da, a, "drums")) if "drums" in (a.get("stems") or {}) else None)
    db_beats = downbeats(b, audio_io.to_mono(backing["drums"]) if "drums" in backing else None)
    entry_bar_a = _nearest(da_beats, _first_vocal_time(a), before=True) / rate
    bar_len_b = 4 * 60 / sb["tempo_bpm"]
    target = _nearest(db_beats, _target_time(b)) + offset_bars * bar_len_b
    offset = target - entry_bar_a  # seconds to delay the stretched vocal by (negative = trim its start)

    n = max(len(s[0]) for s in backing.values())
    out_v = np.zeros((2, n), np.float32)
    src0, dst0 = (int(-offset * SR), 0) if offset < 0 else (0, int(offset * SR))
    m = max(0, min(vocals_t.shape[1] - src0, n - dst0))
    out_v[:, dst0:dst0 + m] = vocals_t[:, src0:src0 + m]

    progress(0.8, "Writing the mashup")
    out = library / out_id
    stem_dir = out / "stems"
    stem_dir.mkdir(parents=True, exist_ok=True)
    audio_io.save(stem_dir / "vocals.wav", out_v, SR)
    for name, s in backing.items():
        shutil.copy2(db / b["stems"][name], stem_dir / f"{name}.wav")
    mix = out_v.copy()
    for s in backing.values():
        mix[:, :s.shape[1]] += s
    mix *= 0.98 / max(1e-6, float(np.abs(mix).max()))
    for old in out.glob("source.*"):
        old.unlink()
    audio_io.save(out / "source.wav", mix, SR)

    def retime(t: float) -> float:
        return round(t / rate + offset, 2)

    lines = []
    for ln in (a.get("lyrics") or {}).get("lines") or []:
        if 0 <= retime(ln["start"]) < n / SR:
            lines.append({**ln, "start": retime(ln["start"]), "end": retime(ln["end"]),
                          "words": [{**w, "start": retime(w["start"]), "end": retime(w["end"])} for w in ln["words"]]})

    structure = json.loads(json.dumps(sb))  # sections and beats belong to the instrumental
    for s in structure["sections"]:
        s["lyrics"] = " ".join(ln["text"] for ln in lines if s["start"] <= ln["start"] < s["end"]).lower()
        s["vocal"] = bool(s["lyrics"])
        s["stems_active"] = sorted(set(s["stems_active"]) - {"vocals"} | ({"vocals"} if s["vocal"] else set()))

    result = {
        "kind": "mashup",
        "title": f"{a['title']} × {b['title']}",
        "source": str(out / "source.wav"),
        "profile": b.get("profile"),
        "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stems": {"vocals": "stems/vocals.wav", **{k: f"stems/{k}.wav" for k in backing}},
        "lyrics": {**(a.get("lyrics") or {}), "lines": lines, "text": "\n".join(l["text"] for l in lines)},
        "structure": {**structure, "key": sb.get("key"), "stem_share_pct": {}},
        "instruments": b.get("instruments"),
        "story": a.get("story"),  # the words, and so the story, come from the vocal song
        "mashup": {
            "vocals_from": {"id": vocals_id, "title": a["title"], "tempo": sa["tempo_bpm"], "key": sa.get("key")},
            "instrumental_from": {"id": inst_id, "title": b["title"], "tempo": sb["tempo_bpm"], "key": sb.get("key")},
            "stretch": round(rate, 4), "semitones": shift, "offset_bars": offset_bars,
            "vocal_offset_s": round(offset, 3), "engine": engine,
            "vocal_key_after": _shift_key(sa["key"], shift) if parse_key(sa.get("key")) else None,
        },
    }
    (out / "analysis.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    from .pipeline import write_peaks
    write_peaks(out, mix[:, ::2])
    progress(1.0, "Done")
    return result
