"""Decode anything ffmpeg can read into float32 numpy, and write WAVs back out.

Uses the ffmpeg binary bundled with imageio-ffmpeg, so nothing has to be on PATH,
and skips torchaudio's I/O entirely (its backends keep changing between releases).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf


def load(path: str | Path, sr: int = 44100, channels: int = 2) -> np.ndarray:
    """Return audio shaped (channels, samples)."""
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path),
           "-f", "f32le", "-ac", str(channels), "-ar", str(sr), "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, channels).T.copy()


def to_mono(audio: np.ndarray) -> np.ndarray:
    return audio.mean(axis=0) if audio.ndim == 2 else audio


def save(path: str | Path, audio: np.ndarray, sr: int) -> None:
    sf.write(str(path), np.clip(audio, -1, 1).T, sr, subtype="PCM_24")


def read(path: str | Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data.T, sr
