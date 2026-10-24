"""Render a playlist as one continuous DJ-style mix.

Each transition overlaps the outgoing song's ending with the incoming song's intro, on downbeats:

* beat-matched (tempos within 8%): the incoming song's first bars are time-stretched to the
  outgoing tempo so the beat grids lock; its melody fades in over the first half, then the bass
  swaps on the middle downbeat and its drums come in, while the outgoing song fades out.
  After the overlap the incoming song returns to its own tempo.
* breakdown (tempos too far apart to blend): the outgoing drums and bass drop out, the incoming
  melody fades in over two bars, and its drums land on the next downbeat.

Works per stem, so the swaps are clean. Output: FLAC plus a cue list of where each song starts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from . import audio_io
from .mix import _tempo_ratio, downbeats, stretch_shift

SR = 44100
MAX_STRETCH = 1.08
BLEND_BARS, BREAK_BARS = 8, 2


def _ramp(t: np.ndarray, t0: float, t1: float, up: bool) -> np.ndarray:
    """Equal-power ramp over [t0, t1] (output seconds)."""
    x = np.clip((t - t0) / max(t1 - t0, 1e-3), 0, 1)
    return np.sin(x * np.pi / 2) if up else np.cos(x * np.pi / 2)


def _group(stem: str) -> str:
    return stem if stem in ("drums", "bass") else "rest"


def _env_in(tr: dict, group: str, t: np.ndarray) -> np.ndarray:
    if tr["kind"] == "blend":
        if group == "rest":
            return _ramp(t, tr["t0"], tr["mid"], True)
        if group == "bass":
            return _ramp(t, tr["mid"], tr["mid"] + tr["beat"], True)
        return _ramp(t, tr["mid"], tr["mid"] + 4 * tr["beat"], True)
    if group == "rest":
        return _ramp(t, tr["t0"], tr["t1"], True)
    return _ramp(t, tr["t1"], tr["t1"] + tr["beat"] / 2, True)


def _env_out(tr: dict, group: str, t: np.ndarray) -> np.ndarray:
    if tr["kind"] == "blend":
        if group == "bass":
            return _ramp(t, tr["mid"], tr["mid"] + tr["beat"], False)
        return _ramp(t, tr["mid"], tr["t1"], False)
    if group == "rest":
        return _ramp(t, tr["t0"], tr["t1"], False)
    return _ramp(t, tr["t0"], tr["t0"] + tr["beat"] / 2, False)


def render(library: Path, playlist: dict, out_dir: Path, progress=None) -> dict:
    progress = progress or (lambda f, m: None)
    songs = [s for s in playlist["songs"] if (library / s["id"] / "analysis.json").exists()]
    if len(songs) < 2:
        raise ValueError("an auto-mix needs at least 2 songs")
    analyses = [json.loads((library / s["id"] / "analysis.json").read_text(encoding="utf-8")) for s in songs]
    est = sum(a["structure"]["duration"] for a in analyses) + 120
    out = np.zeros((2, int(est * SR)), np.float32)
    cues, transitions = [], []
    prev = None  # what the next transition needs to know about the outgoing song
    end = 0.0

    for i, (s, a) in enumerate(zip(songs, analyses)):
        progress(0.05 + 0.85 * i / len(songs), f"Mixing {i + 1}/{len(songs)}: {a['title']}")
        d = library / s["id"]
        stems = {n: audio_io.read(d / rel)[0] for n, rel in (a.get("stems") or {}).items() if (d / rel).exists()}
        if not stems:
            stems = {"rest": audio_io.load(next(d.glob("source.*")), sr=SR)}
        n_src = max(x.shape[1] for x in stems.values())
        dur = n_src / SR
        tempo = a["structure"]["tempo_bpm"]
        dbs = downbeats(a, audio_io.to_mono(stems["drums"]) if "drums" in stems else None)
        db0 = float(dbs[0]) if len(dbs) else 0.0

        # ---- placement: where sample 0 lands, and whether the head is stretched
        rate, head_src, tr_in = 1.0, 0.0, None
        if prev is None:
            start = 0.0
            cues.append({"index": i, "id": s["id"], "title": a["title"], "start": 0.0})
        else:
            tr_in = prev["transition"]
            if tr_in["kind"] == "blend":
                rate = _tempo_ratio(tempo, prev["tempo"])  # play our head at the outgoing tempo
                start = tr_in["t0"] - db0 / rate  # our first downbeat lands on the overlap's first downbeat
                head_src = min(dur, db0 + (tr_in["t1"] - tr_in["t0"] + 2 * tr_in["bar"]) * rate)
            else:
                # Our first downbeat starts the breakdown (melody only); our drums enter two bars later.
                start = tr_in["t0"] - db0
            cue = tr_in["mid"] if tr_in["kind"] == "blend" else tr_in["t0"]
            cues.append({"index": i, "id": s["id"], "title": a["title"], "start": round(cue, 2)})
            transitions.append({"from": prev["title"], "to": a["title"], "kind": tr_in["kind"],
                                "stretch": round(rate, 4), "at": round(tr_in["t0"], 2),
                                "tempo": [round(prev["tempo"], 1), round(tempo, 1)]})

        def out_time(t_src: float) -> float:
            return start + (t_src / rate if t_src < head_src else head_src / rate + (t_src - head_src))

        # ---- outgoing transition, chosen from this song's own ending and the next song's tempo
        tr_out = None
        if i + 1 < len(songs):
            nxt_tempo = analyses[i + 1]["structure"]["tempo_bpm"]
            ratio = _tempo_ratio(nxt_tempo, tempo)
            kind = "blend" if max(ratio, 1 / ratio) <= MAX_STRETCH else "break"
            bar = 4 * 60 / tempo
            length = (BLEND_BARS if kind == "blend" else BREAK_BARS) * bar
            secs = a["structure"]["sections"]
            outro = secs[-1]["start"] if secs and not secs[-1].get("vocal") else dur - length - 2 * bar
            limit = dur - length - 0.25
            cands = [float(x) for x in dbs if head_src <= x <= min(outro, limit)] or \
                    [float(x) for x in dbs if head_src <= x <= limit] or [max(head_src, limit)]
            t0_src = cands[-1]
            t0 = out_time(t0_src)
            tr_out = {"kind": kind, "t0": t0, "t1": t0 + length, "mid": t0 + length / 2, "bar": bar, "beat": bar / 4}

        # ---- render every stem through the time map and its envelopes
        for name, audio in stems.items():
            if rate != 1.0 and head_src > 0:
                h = int(head_src * SR)
                head, _ = stretch_shift(audio[:, :h], SR, rate, 0)
                body = np.concatenate([head, audio[:, h:]], axis=1)
                seam = head.shape[1]
                xf = min(int(0.03 * SR), seam, body.shape[1] - seam)  # 30 ms smoothing at the tempo seam
                if xf > 0:
                    w = np.linspace(0, 1, xf, dtype=np.float32)
                    body[:, seam:seam + xf] = body[:, seam:seam + xf] * w + body[:, seam - xf:seam] * (1 - w)
            else:
                body = audio
            t = start + np.arange(body.shape[1]) / SR
            g = np.ones(body.shape[1], np.float32)
            if tr_in:
                g *= _env_in(tr_in, _group(name), t)
            if tr_out:
                g *= _env_out(tr_out, _group(name), t)
                keep = t < tr_out["t1"] + 0.05  # nothing of this song after its fade-out
                g *= keep
            i0 = int(round(start * SR))
            src0 = max(0, -i0)
            i0 = max(0, i0)
            n = min(body.shape[1] - src0, out.shape[1] - i0)
            if n > 0:
                out[:, i0:i0 + n] += body[:, src0:src0 + n] * g[src0:src0 + n]
        end = max(end, tr_out["t1"] if tr_out else out_time(dur))
        prev = {"tempo": tempo, "title": a["title"], "transition": tr_out}

    progress(0.93, "Writing the mix")
    out = out[:, :int(end * SR) + SR]
    out *= 0.89 / max(1e-6, float(np.abs(out).max()))  # peak at -1 dBFS
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "mix.flac"
    sf.write(str(path), out.T, SR, subtype="PCM_16")
    progress(1.0, "Done")
    return {"file": str(path), "duration": round(out.shape[1] / SR, 2), "cues": cues, "transitions": transitions,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
