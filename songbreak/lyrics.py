"""Stage 2: transcribe the isolated vocal stem with Whisper, word by word.

Only the stretches where the vocal stem is actually loud are sent to Whisper.
That stops it inventing lyrics over instrumental parts (its classic failure on music)
and gives natural line breaks for free.
"""
from __future__ import annotations

import re

import librosa
import numpy as np
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

from . import audio_io
from .hardware import Profile, release

SR = 16000
# What Whisper emits on breaths, bleed and silence (it was trained on YouTube captions).
_HALLUCINATIONS = {"thank you", "thanks for watching", "you", "bye", "please subscribe", "music", "♪", ""}
_HALLUCINATION_RE = re.compile(r"©|subtitle|transcri|amara\.org|caption|\bsubscribe\b|\[music\]|\(music\)", re.I)
_MAX_WORD = 1.5  # s; DTW alignment gives the first word of a clip all the silence before it


def _otsu(x: np.ndarray, bins: int = 128) -> float:
    """Threshold that best separates a two-humped histogram (here: bleed floor vs. voice)."""
    hist, edges = np.histogram(x, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    s0 = np.cumsum(hist * centers)
    m0 = s0 / np.maximum(w0, 1)
    m1 = (s0[-1] - s0) / np.maximum(w1, 1)
    return float(centers[np.argmax(w0 * w1 * (m0 - m1) ** 2)])


def vocal_regions(y: np.ndarray, merge_gap: float = 1.0,
                  min_len: float = 0.5, max_len: float = 28.0) -> list[tuple[float, float]]:
    hop = int(SR * 0.05)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop)[0]
    db = librosa.amplitude_to_db(rms + 1e-10, ref=np.max)
    # Separation leaves a bleed floor in the vocal stem whose level varies per song, so the cut is adaptive.
    active = db > np.clip(_otsu(db), -40.0, -15.0)

    def t(i: int) -> float:
        return i * hop / SR

    raw, start = [], None
    for i, a in enumerate(active):
        if a and start is None:
            start = i
        elif not a and start is not None:
            raw.append([t(start), t(i)])
            start = None
    if start is not None:
        raw.append([t(start), t(len(active))])

    merged: list[list[float]] = []
    for s, e in raw:
        if merged and s - merged[-1][1] < merge_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    # Whisper's window is 30 s, so long phrases are cut at their quietest moment.
    out = []
    for s, e in (r for r in merged if r[1] - r[0] >= min_len):
        while e - s > max_len:
            lo, hi = int((s + max_len * 0.6) / t(1)), int((s + max_len) / t(1))
            cut = t(lo + int(np.argmin(rms[lo:hi])))
            out.append((s, cut))
            s = cut
        out.append((s, e))
    duration = len(y) / SR
    return [(max(0.0, s - 0.2), min(duration, e + 0.2)) for s, e in out]


def _lines(words: list[dict]) -> list[dict]:
    lines: list[dict] = []
    for w in words:
        cur = lines[-1] if lines else None
        last = cur["words"][-1]["word"] if cur else ""
        if (cur is None or w["region"] != cur["region"] or w["start"] - cur["end"] > 0.7
                or last[-1:] in ".?!" or (last[-1:] == "," and len(cur["words"]) >= 4)
                or len(cur["words"]) >= 12):
            lines.append({"start": w["start"], "end": w["end"], "region": w["region"], "words": []})
            cur = lines[-1]
        cur["words"].append(w)
        cur["end"] = w["end"]
    for ln in lines:
        ln["text"] = " ".join(w["word"] for w in ln["words"])
        del ln["region"]
    return lines


def transcribe(vocals_path, profile: Profile, language: str | None = None, on_progress=None) -> dict:
    audio, sr = audio_io.read(vocals_path)
    y = librosa.resample(audio_io.to_mono(audio), orig_sr=sr, target_sr=SR)
    regions = vocal_regions(y)
    if not regions:
        return {"model": profile.whisper_model, "language": language or "auto", "lines": [], "text": ""}

    dtype = torch.float16 if profile.whisper_fp16 and profile.device == "cuda" else torch.float32
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        profile.whisper_model, dtype=dtype, low_cpu_mem_usage=True, use_safetensors=True
    ).to(profile.device)
    processor = AutoProcessor.from_pretrained(profile.whisper_model)
    asr = pipeline("automatic-speech-recognition", model=model, tokenizer=processor.tokenizer,
                   feature_extractor=processor.feature_extractor, dtype=dtype, device=profile.device)
    gen = {"task": "transcribe", **({"language": language} if language else {})}
    # Word timing keeps every cross-attention map: ~4-5 GB per clip in the batch for large-v3.
    bs = profile.whisper_batch
    results = []
    for b in range(0, len(regions), bs):
        clips = [{"raw": y[int(s * SR):int(e * SR)], "sampling_rate": SR} for s, e in regions[b:b + bs]]
        results.extend(asr(clips, batch_size=bs, return_timestamps="word", generate_kwargs=gen))
        if on_progress:
            on_progress(len(results) / len(regions))

    words = []
    for ri, ((s, _), r) in enumerate(zip(regions, results)):
        text = r["text"].strip()
        if text.lower().strip(" .!") in _HALLUCINATIONS or _HALLUCINATION_RE.search(text):
            continue
        for c in r.get("chunks", []):
            ws, we = c["timestamp"]
            if ws is None or not c["text"].strip():
                continue
            we = we if we is not None else ws + 0.3
            ws = max(ws, we - _MAX_WORD)
            words.append({"word": c["text"].strip(), "start": round(s + ws, 2), "end": round(s + we, 2),
                          "region": ri})

    del asr, model, processor
    release()
    lines = _lines(words)
    return {"model": profile.whisper_model, "language": language or "auto", "lines": lines,
            "text": "\n".join(ln["text"] for ln in lines)}


def to_lrc(lines: list[dict]) -> str:
    def stamp(t: float) -> str:
        return f"[{int(t // 60):02d}:{t % 60:05.2f}]"
    return "\n".join(f"{stamp(ln['start'])}{ln['text']}" for ln in lines) + "\n"
