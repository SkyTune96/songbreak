"""Render the Songbreak logo (see app/src/renderer/src/components/Logo.tsx) to app/build/icon.png.

Pure numpy + zlib so it needs nothing beyond the analysis environment:
    python scripts/make_icon.py
"""
import struct
import zlib
from pathlib import Path

import numpy as np

SIZE, SS = 512, 4  # output size, supersampling factor
BG = (0x17, 0x19, 0x22)
BARS = [((0xFF, 0x5C, 0x8A), 9), ((0xFF, 0xA6, 0x40), 15), ((0x9D, 0x7B, 0xFF), 21),
        ((0x4F, 0xD1, 0x8B), 13), ((0x3C, 0xC8, 0xE8), 7)]


def rounded_rect(yy, xx, x, y, w, h, r):
    """Boolean mask of a rounded rectangle, in the logo's 32-unit coordinate space."""
    cx = np.clip(xx, x + r, x + w - r)
    cy = np.clip(yy, y + r, y + h - r)
    return (xx >= x) & (xx <= x + w) & (yy >= y) & (yy <= y + h) & ((xx - cx) ** 2 + (yy - cy) ** 2 <= r * r)


n = SIZE * SS
coords = (np.arange(n) + 0.5) / n * 32
yy, xx = np.meshgrid(coords, coords, indexing="ij")
rgba = np.zeros((n, n, 4), np.float32)
bg = rounded_rect(yy, xx, 0, 0, 32, 32, 8)
rgba[bg] = (*BG, 255)
for i, (color, h) in enumerate(BARS):
    rgba[rounded_rect(yy, xx, 5.5 + i * 4.5, 16 - h / 2, 3, h, 1.5)] = (*color, 255)

# Downsample with premultiplied alpha so the anti-aliased edges don't go dark.
premul = rgba.copy()
premul[..., :3] *= rgba[..., 3:4] / 255
small = premul.reshape(SIZE, SS, SIZE, SS, 4).mean(axis=(1, 3))
alpha = small[..., 3:4]
small[..., :3] = np.where(alpha > 0, small[..., :3] * 255 / np.maximum(alpha, 1e-6), 0)
img = np.clip(small, 0, 255).astype(np.uint8)


def chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


raw = b"".join(b"\x00" + row.tobytes() for row in img)
png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
       + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
out = Path(__file__).resolve().parent.parent / "app" / "build" / "icon.png"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(png)
print(f"wrote {out} ({len(png)} bytes)")
