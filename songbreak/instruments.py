"""Stage 4: name the actual instruments, genre and mood, per section.

Demucs only knows vocals/drums/bass/guitar/piano/"other". An AudioSet classifier
(AST, 527 sound classes) run over each stem tells us what is inside "other"
(strings? synth? brass?), what kind of singing the vocal is, and the genre/mood of the mix.
"""
from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
import torch
from transformers import ASTFeatureExtractor, ASTForAudioClassification

from . import audio_io
from .hardware import Profile, release

MODEL = "MIT/ast-finetuned-audioset-10-10-0.4593"
SR = 16000
WIN, HOP = 10.0, 5.0

_MOODS = {"Happy music", "Funny music", "Sad music", "Tender music", "Exciting music", "Angry music", "Scary music"}
_VOCAL = {"Singing", "Male singing", "Female singing", "Child singing", "Synthetic singing", "Rapping",
          "Humming", "Choir", "Yodeling", "Chant", "Whispering", "Beatboxing", "A capella"}
_INSTRUMENT_WORDS = (
    "guitar", "bass", "piano", "organ", "synth", "keyboard", "drum", "snare", "hi-hat", "cymbal", "percussion",
    "violin", "fiddle", "cello", "string", "orchestra", "trumpet", "trombone", "horn", "brass", "saxophone",
    "flute", "clarinet", "woodwind", "harmonica", "accordion", "banjo", "mandolin", "ukulele", "sitar", "harp",
    "marimba", "xylophone", "glockenspiel", "vibraphone", "steelpan", "tabla", "bagpipe", "didgeridoo",
    "theremin", "sampler", "harpsichord", "tambourine", "cowbell", "gong", "timpani", "bell", "zither",
    "scratching", "mallet", "rattle", "maraca", "wood block", "shofar", "oboe", "bassoon", "tuba",
)
_NOT_GENRE = {"Music", "Musical instrument", "Background music", "Theme music", "Jingle (music)", "Soundtrack music",
              "Video game music", "Christmas music", "Dance music", "Wedding music", "Lullaby", "Music for children"}
_GENRES_EXTRA = {"Heavy metal", "Punk rock", "Grunge", "Progressive rock", "Rock and roll", "Psychedelic rock",
                 "Rhythm and blues", "Reggae", "Country", "Bluegrass", "Funk", "Jazz", "Disco", "Opera", "Techno",
                 "Dubstep", "Drum and bass", "Electronica", "Electronic dance music", "Trance music", "Blues",
                 "Flamenco", "Afrobeat", "Ska", "Salsa music", "Carnatic music", "New-age music"}


def _kind(label: str) -> str | None:
    if label in _MOODS:
        return "mood"
    if label in _VOCAL:
        return "vocal"
    low = label.lower()
    if any(w in low for w in _INSTRUMENT_WORDS) and "music" not in low:
        return "instrument"
    if label not in _NOT_GENRE and (label in _GENRES_EXTRA or label.endswith(" music")):
        return "genre"
    return None


def _windows(duration: float) -> list[tuple[float, float]]:
    starts = np.arange(0, max(duration - WIN, 0) + 1e-6, HOP)
    return [(float(s), float(min(s + WIN, duration))) for s in starts] or [(0.0, duration)]


def tag(mix: np.ndarray, mix_sr: int, stem_paths: dict[str, Path] | None, sections: list[dict],
        profile: Profile, on_progress=None) -> dict:
    fe = ASTFeatureExtractor.from_pretrained(MODEL)
    model = ASTForAudioClassification.from_pretrained(MODEL).to(profile.device).eval()
    labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
    kinds = [_kind(lb) for lb in labels]
    use_fp16 = profile.device == "cuda"

    def run(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        wins = _windows(len(y) / SR)
        clips = [y[int(s * SR):int(e * SR)] for s, e in wins]
        out = []
        for b in range(0, len(clips), profile.tagger_batch):
            inputs = fe(clips[b:b + profile.tagger_batch], sampling_rate=SR, return_tensors="pt").to(profile.device)
            with torch.inference_mode(), torch.autocast(profile.device, enabled=use_fp16):
                out.append(torch.sigmoid(model(**inputs).logits).float().cpu().numpy())
        centers = np.array([(s + e) / 2 for s, e in wins])
        return np.concatenate(out), centers

    sources = {"mix": librosa.resample(audio_io.to_mono(mix), orig_sr=mix_sr, target_sr=SR)}
    for name, path in (stem_paths or {}).items():
        audio, sr = audio_io.read(path)
        sources[name] = librosa.resample(audio_io.to_mono(audio), orig_sr=sr, target_sr=SR)
    probs = {}
    for i, (name, y) in enumerate(sources.items()):
        probs[name] = run(y)
        if on_progress:
            on_progress((i + 1) / len(sources))

    def top(p: np.ndarray, kind: str, thresh: float, k: int = 5) -> list[tuple[str, float]]:
        idx = [i for i in np.argsort(-p) if kinds[i] == kind and p[i] >= thresh][:k]
        return [(labels[i], round(float(p[i]), 3)) for i in idx]

    # Which classifier output we trust for which question.
    wanted = {"mix": [("genre", 0.05), ("mood", 0.05), ("instrument", 0.15)],
              "vocals": [("vocal", 0.1)],
              "drums": [("instrument", 0.1)], "bass": [("instrument", 0.1)],
              "other": [("instrument", 0.1)], "guitar": [("instrument", 0.1)], "piano": [("instrument", 0.1)]}

    per_section = []
    for sec in sections:
        found: dict[str, dict[str, float]] = {"genre": {}, "mood": {}, "instrument": {}, "vocal": {}}
        for src, (p, centers) in probs.items():
            if src != "mix" and src not in sec.get("stems_active", [src]):
                continue  # don't tag bleed in a stem that's silent here
            mask = (centers >= sec["start"]) & (centers < sec["end"])
            if not mask.any():  # section shorter than the window hop: use the nearest window
                mask[np.argmin(np.abs(centers - (sec["start"] + sec["end"]) / 2))] = True
            mean = p[mask].mean(axis=0)
            for kind, thresh in wanted.get(src, []):
                for lb, v in top(mean, kind, thresh):
                    found[kind][lb] = max(found[kind].get(lb, 0), v)
        per_section.append({k: sorted(v.items(), key=lambda x: -x[1]) for k, v in found.items()})

    song = {}
    mix_mean = probs["mix"][0].mean(axis=0)
    song["genre"] = top(mix_mean, "genre", 0.03)
    song["mood"] = top(mix_mean, "mood", 0.03)
    inst: dict[str, float] = {}
    for sec in per_section:
        for lb, v in sec["instrument"]:
            inst[lb] = max(inst.get(lb, 0), v)
    song["instruments"] = sorted(inst.items(), key=lambda x: -x[1])
    vocal: dict[str, float] = {}
    for sec in per_section:
        for lb, v in sec["vocal"]:
            vocal[lb] = max(vocal.get(lb, 0), v)
    song["vocal_style"] = sorted(vocal.items(), key=lambda x: -x[1])

    del model, fe
    release()
    return {"model": MODEL, "song": song, "sections": per_section}
