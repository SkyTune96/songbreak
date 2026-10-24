"""Detect the machine and pick model sizes that fit it.

Every stage runs one at a time and frees the GPU before the next starts,
so each profile only has to fit the single biggest model, not all of them.
"""
from __future__ import annotations

import ctypes
import gc
import os
import sys
from dataclasses import dataclass, asdict

import torch


@dataclass
class Profile:
    name: str
    device: str
    gpu: str
    vram_gb: float
    ram_gb: float
    # Demucs: htdemucs_ft is a bag of 4 fine-tuned models (best 4-stem quality, ~4x slower);
    # htdemucs_6s adds guitar + piano stems.
    demucs_4stem: str
    demucs_shifts: int  # random time-shift passes averaged together; more = cleaner, linearly slower
    whisper_model: str
    whisper_fp16: bool
    whisper_batch: int
    tagger_batch: int
    llm_model: str  # Ollama tag; Ollama manages its own VRAM once torch has let go

    def to_dict(self) -> dict:
        return asdict(self)


def _ram_gb() -> float:
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys / 1024**3
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3


# (min VRAM GB, settings). Checked top-down; first match wins.
_TIERS = [
    (14, dict(name="high", demucs_4stem="htdemucs_ft", demucs_shifts=2,
              whisper_model="openai/whisper-large-v3", whisper_fp16=True, whisper_batch=1,
              tagger_batch=32, llm_model="qwen3:14b")),
    (8, dict(name="mid", demucs_4stem="htdemucs_ft", demucs_shifts=1,
             whisper_model="openai/whisper-large-v3-turbo", whisper_fp16=True, whisper_batch=1,
             tagger_batch=16, llm_model="qwen3:8b")),
    (4, dict(name="low", demucs_4stem="htdemucs", demucs_shifts=1,
             whisper_model="openai/whisper-large-v3-turbo", whisper_fp16=True, whisper_batch=1,
             tagger_batch=8, llm_model="qwen3:4b")),
]
_CPU = dict(name="cpu", demucs_4stem="htdemucs", demucs_shifts=1,
            whisper_model="openai/whisper-small", whisper_fp16=False, whisper_batch=1,
            tagger_batch=4, llm_model="qwen3:4b")


def detect(force: str | None = None) -> Profile:
    ram = _ram_gb()
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        vram = props.total_memory / 1024**3
        gpu, device = props.name, "cuda"
    else:
        vram, gpu, device = 0.0, "none", "cpu"

    settings = _CPU
    if force and force != "auto":
        settings = next((t for _, t in _TIERS if t["name"] == force), _CPU)
    elif device == "cuda":
        settings = next((t for min_vram, t in _TIERS if vram >= min_vram), _CPU)
    if settings is _CPU:
        device = "cpu"
    return Profile(device=device, gpu=gpu, vram_gb=round(vram, 1), ram_gb=round(ram, 1), **settings)


def release() -> None:
    """Hand VRAM back so the next stage (or Ollama) gets all of it. `del` the models first."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def peak_vram_gb() -> float:
    if not torch.cuda.is_available():
        return 0.0
    peak = torch.cuda.max_memory_allocated() / 1024**3
    torch.cuda.reset_peak_memory_stats()
    return round(peak, 2)
