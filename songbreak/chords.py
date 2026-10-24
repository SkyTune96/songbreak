"""Chord recognition from the separated backing stems.

Beat-synchronous chroma of the harmonic stems (no vocals, no drums) is matched against major and
minor triad templates, the bass stem votes for the root (so inversions and passing tones don't
flip the label), and a Viterbi pass with a sticky transition matrix stops chords flickering.
Quiet beats are "N" (no chord). Cached per song in chords.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import librosa
import numpy as np

from . import audio_io

SR = 22050
HOP = 512
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
LABELS = [n for n in NOTES] + [n + "m" for n in NOTES] + ["N"]


def _templates() -> np.ndarray:
    t = np.zeros((24, 12))
    for r in range(12):
        t[r, [r, (r + 4) % 12, (r + 7) % 12]] = [1.0, 0.8, 0.9]  # major: root, major third, fifth
        t[12 + r, [r, (r + 3) % 12, (r + 7) % 12]] = [1.0, 0.8, 0.9]  # minor: root, minor third, fifth
    return t / np.linalg.norm(t, axis=1, keepdims=True)


def _load(song_dir: Path, analysis: dict, names: tuple[str, ...]) -> np.ndarray | None:
    ys = []
    for n in names:
        rel = (analysis.get("stems") or {}).get(n)
        if rel and (song_dir / rel).exists():
            audio, sr = audio_io.read(song_dir / rel)
            ys.append(librosa.resample(audio_io.to_mono(audio), orig_sr=sr, target_sr=SR))
    if not ys:
        return None
    n = min(len(y) for y in ys)
    return sum(y[:n] for y in ys)


def detect(song_dir: Path, analysis: dict) -> dict:
    harmonic = _load(song_dir, analysis, ("other", "guitar", "piano", "bass"))
    bass = _load(song_dir, analysis, ("bass",))
    if harmonic is None:  # no stems: fall back to the mix's harmonic component
        src = next(song_dir.glob("source.*"))
        harmonic = librosa.effects.harmonic(audio_io.load(src, sr=SR, channels=1)[0])
    duration = len(harmonic) / SR

    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=SR, hop_length=HOP)
    bass_chroma = librosa.feature.chroma_cqt(y=bass, sr=SR, hop_length=HOP, fmin=librosa.note_to_hz("C1"),
                                             n_octaves=3) if bass is not None else None
    rms = librosa.feature.rms(y=harmonic, hop_length=HOP)[0]

    # One decision per beat. Fast tempos are usually double-time readings, where real chords last
    # far longer than a beat, so the grid step never drops below ~0.6 s (one beat at 100 BPM).
    beats = np.array((analysis.get("structure") or {}).get("beats") or [])
    if len(beats) > 4:
        step = float(np.median(np.diff(beats)))
        step *= int(np.ceil(0.55 / step))
        grid = np.concatenate([np.arange(beats[0], 0, -step)[::-1][:-1], np.arange(beats[0], duration, step)])
    else:
        step = 0.6
        grid = np.arange(0, duration, step)
    frames = librosa.util.fix_frames(librosa.time_to_frames(grid, sr=SR, hop_length=HOP), x_min=0, x_max=chroma.shape[1])
    times = librosa.frames_to_time(frames, sr=SR, hop_length=HOP)
    C = librosa.util.sync(chroma, frames, aggregate=np.median)
    E = librosa.util.sync(rms[None, :], frames, aggregate=np.mean)[0]

    Cn = C / (np.linalg.norm(C, axis=0, keepdims=True) + 1e-9)
    scores = _templates() @ Cn  # 24 x beats, cosine similarity
    if bass_chroma is not None:
        B = librosa.util.sync(bass_chroma, frames, aggregate=np.median)
        B = B / (B.max(axis=0, keepdims=True) + 1e-9)
        scores += 0.25 * np.vstack([B, B])  # the bass note backs the chord with that root
    quiet = E < 0.08 * E.max()
    no_chord = np.where(quiet, 1.2, 0.3)[None, :]  # beats with little harmonic energy prefer "N"
    scores = np.vstack([scores, no_chord])

    probs = np.exp(8 * scores)
    probs /= probs.sum(axis=0, keepdims=True)
    path = librosa.sequence.viterbi_discriminative(probs, librosa.sequence.transition_loop(25, 0.94))

    segments = []
    for i, s in enumerate(path):
        start, end = float(times[i]), float(times[i + 1]) if i + 1 < len(times) else duration
        if segments and segments[-1]["chord"] == LABELS[s]:
            segments[-1]["end"] = round(end, 3)
        else:
            segments.append({"start": round(start, 3), "end": round(end, 3), "chord": LABELS[s]})
    # Fold single-step blips into their neighbour.
    min_len = step * 1.01
    merged = []
    for seg in segments:
        if merged and (seg["end"] - seg["start"] < min_len or seg["chord"] == merged[-1]["chord"]):
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(seg)
    return {"segments": merged, "method": "triad templates + bass root + Viterbi (beat-synchronous)"}


def get(song_dir: Path, force: bool = False) -> dict:
    cache = song_dir / "chords.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    analysis = json.loads((song_dir / "analysis.json").read_text(encoding="utf-8"))
    result = detect(song_dir, analysis)
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result
