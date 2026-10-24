"""Compare the stretch engines on a real vocal stem: speed, pitch accuracy, artefacts, plus files to listen to.

    python scripts/compare_engines.py <library dir> <vocal song id> <instrumental song id> <out dir>

Artefacts are measured with a round trip (stretch + shift, then undo both): a perfect engine returns
the original, so the log-spectral distance from it shows how much smearing/phasiness each one adds.
Writes vocals-only and full-mashup WAVs per engine for A/B listening.
"""
import json
import sys
import time
from pathlib import Path

import librosa
import numpy as np

from songbreak import audio_io
from songbreak.mix import ENGINES, _rubberband, _signalsmith, _tempo_ratio, semitone_shift, stretch_shift

SR = 44100
lib, vid, iid, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
out.mkdir(parents=True, exist_ok=True)
a = json.loads((lib / vid / "analysis.json").read_text(encoding="utf-8"))
b = json.loads((lib / iid / "analysis.json").read_text(encoding="utf-8"))
vocals, _ = audio_io.read(lib / vid / a["stems"]["vocals"])
backing = sum(audio_io.read(lib / iid / p)[0] for n, p in b["stems"].items() if n != "vocals")
rate = _tempo_ratio(a["structure"]["tempo_bpm"], b["structure"]["tempo_bpm"])
semi = semitone_shift(a["structure"]["key"], b["structure"]["key"])
print(f"stretch x{rate:.4f}, shift {semi:+d} st, vocal {vocals.shape[1] / SR:.1f}s\n")


def lsd(x: np.ndarray, y: np.ndarray) -> float:
    """Log-spectral distance in dB over frames where the reference is audible."""
    n = min(len(x), len(y))
    X = np.abs(librosa.stft(x[:n], n_fft=2048)) + 1e-6
    Y = np.abs(librosa.stft(y[:n], n_fft=2048)) + 1e-6
    loud = 20 * np.log10(X.max(axis=0)) > 20 * np.log10(X.max()) - 40
    d = (20 * np.log10(X) - 20 * np.log10(Y))[:, loud]
    return float(np.sqrt((d ** 2).mean(axis=0)).mean())


BPO = 144  # 12 bins per semitone ≈ 8 cents


def log_spectrum(y: np.ndarray) -> np.ndarray:
    """Average log-frequency spectrum over the loudest (sung) frames."""
    C = np.abs(librosa.cqt(y, sr=SR, fmin=80, n_bins=BPO * 5, bins_per_octave=BPO, hop_length=1024))
    loud = C.sum(axis=0) > np.percentile(C.sum(axis=0), 60)
    return np.log1p(C[:, loud].mean(axis=1))


def pitch_shift_cents(ref: np.ndarray, out: np.ndarray) -> float:
    """How far `out`'s spectrum sits from `ref`'s, by cross-correlating along log frequency."""
    a, b = ref - ref.mean(), out - out.mean()
    lags = range(-4 * 12, 4 * 12 + 1)
    best = max(lags, key=lambda k: float(np.dot(a[max(0, -k):len(a) - max(0, k)], b[max(0, k):len(b) - max(0, -k)])))
    return best * 1200 / BPO


ref_mono = audio_io.to_mono(vocals)
spec_ref = log_spectrum(ref_mono)
available = {"rubberband": bool(_rubberband()), "signalsmith": bool(_signalsmith()), "librosa": True}
rows = []
for eng in ENGINES:
    if not available[eng]:
        print(f"{eng:12s} not installed, skipped")
        continue
    t = time.perf_counter()
    y, used = stretch_shift(vocals, SR, rate, semi, engine=eng)
    secs = time.perf_counter() - t
    back, _ = stretch_shift(y, SR, 1 / rate, -semi, engine=eng)
    cents = pitch_shift_cents(spec_ref, log_spectrum(audio_io.to_mono(y))) - 100 * semi
    rows.append((eng, secs, cents, lsd(ref_mono, audio_io.to_mono(back)), y.shape[1] / SR))
    audio_io.save(out / f"vocals_{eng}.wav", y, SR)
    n = backing.shape[1]
    mix = backing.copy()
    m = min(n, y.shape[1])
    mix[:, :m] += y[:, :m]
    audio_io.save(out / f"mashup_{eng}.wav", mix * 0.98 / np.abs(mix).max(), SR)

print(f"{'engine':12s} {'time':>7s} {'pitch error':>12s} {'round-trip LSD':>15s} {'length':>8s}")
for eng, secs, cents, d, length in rows:
    print(f"{eng:12s} {secs:6.2f}s {cents:+10.1f} ct {d:12.2f} dB {length:7.2f}s")
print(f"\nexpected length {vocals.shape[1] / SR / rate:.2f}s · lower LSD = fewer artefacts · files in {out}")
