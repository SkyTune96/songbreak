"""Stage 1: split the mix into instrument stems with Demucs."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from demucs import apply as demucs_apply
from demucs.apply import apply_model
from demucs.pretrained import get_model

from . import audio_io
from .hardware import Profile, release

SR = 44100


class _ProgressShim:
    """Stands in for the `tqdm` module inside demucs.apply, turning its per-chunk loop into a 0..1 callback.
    The loop runs once per model in a bag and once per shift, so progress spans all passes."""

    def __init__(self, callback, passes: int):
        self.callback, self.passes, self.done = callback, passes, 0

    def tqdm(self, iterable, **_):
        items = list(iterable)
        for i, item in enumerate(items):
            yield item
            self.callback(min((self.done + (i + 1) / len(items)) / self.passes, 1.0))
        self.done += 1


def separate(mix: np.ndarray, profile: Profile, out_dir: Path, six_stems: bool = True,
             on_progress=None) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    model_name = "htdemucs_6s" if six_stems else profile.demucs_4stem
    model = get_model(model_name)
    model.eval()

    wav = torch.from_numpy(mix)
    # Demucs expects input normalised by the mono reference, and un-normalised after.
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    passes = len(getattr(model, "models", [model])) * max(profile.demucs_shifts, 1)
    original_tqdm = demucs_apply.tqdm
    demucs_apply.tqdm = _ProgressShim(on_progress or (lambda f: None), passes)
    try:
        with torch.inference_mode():
            sources = apply_model(model, ((wav - mean) / std)[None], device=profile.device,
                                  shifts=profile.demucs_shifts, split=True, overlap=0.25,
                                  progress=True)[0]
    finally:
        demucs_apply.tqdm = original_tqdm
    sources = sources * std + mean

    paths = {}
    for name, src in zip(model.sources, sources):
        p = out_dir / f"{name}.wav"
        audio_io.save(p, src.cpu().numpy(), SR)
        paths[name] = p
    del model, sources
    release()
    return paths


def load_existing(out_dir: Path) -> dict[str, Path] | None:
    """Reuse stems from an earlier run so re-analysing a song skips the slowest step."""
    found = {p.stem: p for p in out_dir.glob("*.wav")} if out_dir.exists() else {}
    return found if {"vocals", "drums", "bass", "other"} <= found.keys() else None
