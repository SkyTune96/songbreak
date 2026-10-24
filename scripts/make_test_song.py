"""Synthesise a test song with known structure, for checking the pipeline end to end.

Intro (0-9.6 s) - Verse 1 - Chorus - Verse 2 - Chorus - Outro (86.4-96 s) at 100 BPM, in A minor,
with drums, bass, saw-pad chords and text-to-speech "vocals". Windows only (uses SAPI for the voice):

    powershell -File scripts/tts.ps1 -OutDir samples/tts
    python scripts/make_test_song.py samples/tts samples/test_song.wav [BPM] [TRANSPOSE_SEMITONES]

A second song at another tempo/key (e.g. `108 2` = 108 BPM in B minor) is handy for testing mashups.
"""
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

SR = 44100
BPM = float(sys.argv[3]) if len(sys.argv) > 3 else 100
TRANSPOSE = 2 ** ((float(sys.argv[4]) if len(sys.argv) > 4 else 0) / 12)
BEAT = 60 / BPM
BAR = 4 * BEAT
tts_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
rng = np.random.default_rng(0)


def tone(freq, dur, kind="saw", amp=0.2):
    t = np.arange(int(dur * SR)) / SR
    w = 2 * (t * freq % 1) - 1 if kind == "saw" else np.sin(2 * np.pi * freq * t)
    env = np.minimum(1, t / 0.01) * np.exp(-t * (1.5 if kind == "saw" else 4))
    return amp * w * env


def kick():
    t = np.arange(int(0.3 * SR)) / SR
    return 0.9 * np.sin(2 * np.pi * (50 + 100 * np.exp(-t * 30)) * t) * np.exp(-t * 12)


def snare():
    t = np.arange(int(0.2 * SR)) / SR
    return 0.4 * rng.standard_normal(t.size) * np.exp(-t * 20)


def hat():
    t = np.arange(int(0.05 * SR)) / SR
    return 0.12 * np.diff(rng.standard_normal(t.size + 1)) * np.exp(-t * 80)


def place(buf, clip, at):
    i = int(at * SR)
    j = min(len(buf), i + len(clip))
    buf[i:j] += clip[:j - i]


# (bass root, chord tones): Am - F - C - G for verses, F - G - Am - Am for the chorus
VERSE = [(220.0, [220, 261.6, 329.6]), (174.6, [174.6, 220, 261.6]), (130.8, [261.6, 329.6, 392]),
         (196.0, [196, 246.9, 293.7])]
CHORUS = [(174.6, [349.2, 440, 523.3]), (196.0, [392, 493.9, 587.3]), (220.0, [440, 523.3, 659.3]),
          (220.0, [440, 523.3, 659.3])]


def section(bars, chords, drums=True, loud=1.0):
    buf = np.zeros(int(bars * BAR * SR))
    for b in range(bars):
        root, chord = chords[b % 4]
        root, chord = root * TRANSPOSE, [f * TRANSPOSE for f in chord]
        t0 = b * BAR
        for f in chord:
            place(buf, tone(f, BAR, "saw", 0.06 * loud), t0)
        for q in range(4):
            place(buf, tone(root / 2, BEAT * 0.9, "sine", 0.35 * loud), t0 + q * BEAT)
            if drums:
                place(buf, (kick() if q in (0, 2) else snare()) * loud, t0 + q * BEAT)
                place(buf, hat() * loud, t0 + q * BEAT)
                place(buf, hat() * loud, t0 + (q + 0.5) * BEAT)
    return buf


def vocals(name, length):
    y, _ = librosa.load(tts_dir / f"{name}.wav", sr=SR)
    buf = np.zeros(length)
    place(buf, 0.8 * y / np.abs(y).max(), BAR * 0.5)
    return buf


plan = [(4, VERSE, False, 0.6, None), (8, VERSE, True, 0.8, "verse1"), (8, CHORUS, True, 1.2, "chorus"),
        (8, VERSE, True, 0.8, "verse2"), (8, CHORUS, True, 1.2, "chorus"), (4, VERSE, False, 0.5, None)]
song = []
for bars, chords, drums, loud, voc in plan:
    s = section(bars, chords, drums, loud)
    if voc:
        s += vocals(voc, len(s))
    song.append(s)
mix = np.concatenate(song)
mix = 0.9 * mix / np.abs(mix).max()
out.parent.mkdir(parents=True, exist_ok=True)
sf.write(out, np.stack([mix, mix], axis=1), SR)
print(f"wrote {out} ({len(mix) / SR:.1f} s)")
