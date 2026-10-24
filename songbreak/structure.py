"""Stage 3: tempo, key, sections (intro/verse/chorus/...), and which stems play where.

Sections come from beat-synchronous harmony + timbre features split by temporally
constrained clustering. Repeats are found by comparing both how sections sound and
what is sung in them, then named with songwriting heuristics. The story stage's LLM
gets the chance to correct those names, since it can read the lyrics properly.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

import librosa
import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.signal.windows import gaussian
from scipy.spatial.distance import cosine, squareform

from . import audio_io

SR = 22050
HOP = 512
_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Kessler key profiles.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def estimate_key(chroma: np.ndarray) -> tuple[str, float]:
    prof = chroma.mean(axis=1)
    scores = []
    for i in range(12):
        scores.append((np.corrcoef(np.roll(_MAJOR, i), prof)[0, 1], f"{_NOTES[i]} major"))
        scores.append((np.corrcoef(np.roll(_MINOR, i), prof)[0, 1], f"{_NOTES[i]} minor"))
    scores.sort(reverse=True)
    return scores[0][1], round(float(scores[0][0] - scores[1][0]), 3)


def _zscore(x: np.ndarray) -> np.ndarray:
    return (x - x.mean(axis=1, keepdims=True)) / (x.std(axis=1, keepdims=True) + 1e-8)


def _beat_grid(beat_t: np.ndarray, duration: float) -> np.ndarray:
    """Tracked beats, extended at the median tempo over drumless intros/outros the tracker skips."""
    if len(beat_t) < 4:
        return np.arange(0, duration, 0.5)
    step = float(np.median(np.diff(beat_t)))
    before = np.arange(beat_t[0] - step, step / 2, -step)[::-1]
    after = np.arange(beat_t[-1] + step, duration - step / 2, step)
    return np.concatenate([before, beat_t, after])


def _novelty(feats: np.ndarray, half: int) -> np.ndarray:
    """Foote checkerboard novelty on the cosine self-similarity matrix, scaled to 0..1."""
    n = feats.shape[1]
    X = librosa.util.normalize(feats, norm=2, axis=0)
    S = X.T @ X
    g = gaussian(2 * half, std=half / 2)
    quad = np.ones((half, half))
    K = np.outer(g, g) * np.block([[quad, -quad], [-quad, quad]])
    P = np.pad(S, half, mode="reflect")
    nov = np.array([(P[i:i + 2 * half, i:i + 2 * half] * K).sum() for i in range(n)])
    return np.maximum(nov, 0) / (nov.max() or 1)


def _section_starts(chroma: np.ndarray, timbre: np.ndarray, half: int = 16, min_beats: int = 12) -> list[int]:
    """Section starts (beat indices). A 4-bar kernel looks past bar-to-bar chord changes;
    timbre (instrumentation, density, loudness) marks sections more reliably than harmony."""
    n = chroma.shape[1]
    half = max(4, min(half, n // 4))
    nov = 0.8 * _novelty(timbre, half) + 0.2 * _novelty(chroma, half)
    peaks = librosa.util.peak_pick(nov, pre_max=half // 2, post_max=half // 2, pre_avg=2 * half,
                                   post_avg=2 * half, delta=0.05, wait=min_beats)
    return [0] + [int(p) for p in peaks if min_beats // 2 <= p <= n - min_beats // 2]


def _stem_activity(stems: dict[str, np.ndarray], sr: int, mix_peak_db: float):
    """Per stem: active-frame mask (10 Hz) and its share of the total stem energy."""
    hop_s = 0.1
    masks, energy = {}, {}
    for name, y in stems.items():
        hop = int(sr * hop_s)
        rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
        db = librosa.amplitude_to_db(rms + 1e-10)
        # Loud relative to itself AND not buried far under the mix (rules out separation bleed).
        masks[name] = (db > db.max() - 25) & (db > mix_peak_db - 40)
        energy[name] = float((rms ** 2).sum())
    total = sum(energy.values()) or 1.0
    return masks, {k: round(100 * v / total, 1) for k, v in energy.items()}, hop_s


def _name_sections(sections: list[dict]) -> None:
    letters = sorted({s["letter"] for s in sections})
    stats = {}
    for L in letters:
        occ = [s for s in sections if s["letter"] == L]
        sims = [SequenceMatcher(None, a["lyrics"], b["lyrics"]).ratio()
                for i, a in enumerate(occ) for b in occ[i + 1:] if a["lyrics"] and b["lyrics"]]
        stats[L] = dict(n=len(occ), energy=np.mean([s["energy"] for s in occ]),
                        vocal=np.mean([s["vocal"] for s in occ]), lyric_sim=np.mean(sims) if sims else 0.0)

    vocal_repeats = [L for L in letters if stats[L]["n"] >= 2 and stats[L]["vocal"] >= 0.5]
    chorus = max(vocal_repeats, key=lambda L: 2 * stats[L]["lyric_sim"] + stats[L]["energy"] + 0.2 * stats[L]["n"],
                 default=None)
    verse_candidates = [L for L in letters if L != chorus and stats[L]["vocal"] >= 0.5]
    verse = max(verse_candidates, key=lambda L: stats[L]["n"], default=None)
    first_chorus = next((i for i, s in enumerate(sections) if s["letter"] == chorus), len(sections))

    for i, s in enumerate(sections):
        nxt = sections[i + 1]["letter"] if i + 1 < len(sections) else None
        if not s["vocal"]:
            s["label"] = "Intro" if i == 0 else "Outro" if i == len(sections) - 1 else "Instrumental"
        elif s["letter"] == chorus:
            s["label"] = "Chorus"
        elif nxt == chorus and s["letter"] != verse and s["end"] - s["start"] < 25:
            s["label"] = "Pre-Chorus"
        elif stats[s["letter"]]["n"] == 1 and i > first_chorus:
            s["label"] = "Bridge"
        else:
            s["label"] = "Verse"
    counts: dict[str, int] = {}
    for s in sections:
        if s["label"] in ("Verse", "Chorus", "Pre-Chorus", "Instrumental"):
            counts[s["label"]] = counts.get(s["label"], 0) + 1
            s["label"] = f"{s['label']} {counts[s['label']]}"


def analyze(mix: np.ndarray, mix_sr: int, stem_paths: dict[str, Path] | None, lines: list[dict]) -> dict:
    y = librosa.resample(audio_io.to_mono(mix), orig_sr=mix_sr, target_sr=SR)
    duration = len(y) / SR
    stems: dict[str, np.ndarray] = {}
    for name, path in (stem_paths or {}).items():
        audio, stem_sr = audio_io.read(path)
        stems[name] = audio_io.to_mono(audio)
    # Sections are defined by the backing track; a vocal dropping out mid-verse shouldn't split it.
    if stems:
        backing = sum(v for k, v in stems.items() if k != "vocals")
        y_feat = librosa.resample(backing, orig_sr=stem_sr, target_sr=SR)[:len(y)]
    else:
        y_feat = y

    tempo, beats = librosa.beat.beat_track(y=y, sr=SR, hop_length=HOP)
    tempo = float(np.atleast_1d(tempo)[0])
    chroma = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y_feat), sr=SR, hop_length=HOP)
    key, key_conf = estimate_key(chroma)
    mfcc = librosa.feature.mfcc(y=y_feat, sr=SR, n_mfcc=13, hop_length=HOP)
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    rms_db = librosa.amplitude_to_db(rms + 1e-10)

    beat_t = librosa.frames_to_time(beats, sr=SR, hop_length=HOP)
    grid = librosa.time_to_frames(_beat_grid(beat_t, duration), sr=SR, hop_length=HOP)
    bounds = librosa.util.fix_frames(grid, x_min=0, x_max=chroma.shape[1])
    bound_t = librosa.frames_to_time(bounds, sr=SR, hop_length=HOP)
    chroma_b = librosa.util.sync(chroma, bounds, aggregate=np.median)
    mfcc_b = _zscore(librosa.util.sync(mfcc, bounds))
    feats = np.vstack([_zscore(chroma_b), 0.7 * mfcc_b])

    starts = _section_starts(chroma_b, mfcc_b)
    edges = [float(bound_t[i]) for i in starts] + [duration]
    spans = [[i0, i1, edges[n], edges[n + 1]] for n, (i0, i1) in
             enumerate(zip(starts, list(starts[1:]) + [feats.shape[1]]))]
    # Fold slivers (< 5 s) into the previous section.
    merged = []
    for sp in spans:
        if merged and sp[3] - sp[2] < 5:
            merged[-1][1], merged[-1][3] = sp[1], sp[3]
        else:
            merged.append(sp)
    if len(merged) > 1 and merged[0][3] - merged[0][2] < 5:
        merged[1][0], merged[1][2] = merged[0][0], merged[0][2]
        merged.pop(0)

    words = [w for ln in lines for w in ln["words"]]
    peak_db = float(rms_db.max())
    masks, shares, hop_s = _stem_activity(stems, stem_sr, peak_db) if stems else ({}, {}, 0.1)

    sections = []
    for i0, i1, t0, t1 in merged:
        f0, f1 = librosa.time_to_frames([t0, t1], sr=SR, hop_length=HOP)
        seg_words = [w["word"] for w in words if t0 <= (w["start"] + w["end"]) / 2 < t1]
        active = {}
        for name, m in masks.items():
            part = m[int(t0 / hop_s):max(int(t1 / hop_s), int(t0 / hop_s) + 1)]
            if part.size:
                active[name] = round(float(part.mean()), 2)
        vocal_frac = active.get("vocals", 0.0)
        sections.append({
            "start": round(t0, 2), "end": round(t1, 2),
            "feat": feats[:, i0:max(i1, i0 + 1)].mean(axis=1),
            "loudness_db": round(float(rms_db[f0:max(f1, f0 + 1)].mean() - peak_db), 1),
            "lyrics": " ".join(seg_words).lower(),
            "vocal": bool(len(seg_words) >= 3 or vocal_frac >= 0.3),
            "stems_active": sorted([n for n, frac in active.items() if frac >= 0.2]),
            "stem_activity": active,
        })

    lo = min(s["loudness_db"] for s in sections)
    hi = max(s["loudness_db"] for s in sections)
    for s in sections:
        s["energy"] = round((s["loudness_db"] - lo) / ((hi - lo) or 1), 2)

    # Group repeated sections: close in sound, or singing the same words.
    n = len(sections)
    D = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            a, b = sections[i], sections[j]
            d = cosine(a["feat"], b["feat"])
            if len(a["lyrics"]) > 15 and len(b["lyrics"]) > 15:
                d = min(d, 1 - SequenceMatcher(None, a["lyrics"], b["lyrics"]).ratio())
            if a["vocal"] != b["vocal"]:
                d = max(d, 1.0)
            D[i, j] = D[j, i] = d
    if n > 1:
        off = D[np.triu_indices(n, 1)]
        groups = fcluster(linkage(squareform(D, checks=False), "average"),
                          t=0.5 * float(np.median(off)), criterion="distance")
    else:
        groups = np.array([1])
    order: dict[int, str] = {}
    for s, g in zip(sections, groups):
        s["letter"] = order.setdefault(int(g), chr(ord("A") + len(order)))
        del s["feat"]
    _name_sections(sections)

    per_second = librosa.feature.rms(y=y, frame_length=SR, hop_length=SR)[0]
    curve = librosa.amplitude_to_db(per_second + 1e-10) - peak_db
    return {
        "duration": round(duration, 2),
        "tempo_bpm": round(tempo, 1),
        "beats": [round(float(t), 3) for t in beat_t],
        "key": key, "key_confidence": key_conf,
        "form_letters": "".join(s["letter"] for s in sections),
        "sections": sections,
        "stem_share_pct": shares,
        "loudness_curve_db": [round(float(v), 1) for v in curve],
    }
