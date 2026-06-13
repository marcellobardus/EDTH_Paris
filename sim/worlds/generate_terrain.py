"""
Generate terrain assets for the Gazebo world (pure Python stdlib — no extra deps).

Outputs into the same directory's assets/ folder:
  heightmap.png      — 513×513 greyscale elevation map  (0-255 → 0-50 m)
  grass_diffuse.png  — 64×64 RGB grass-green diffuse texture
  flat_normal.png    — 64×64 RGB flat normal map (straight up: 128, 128, 255)

Run once at Docker build time; regenerate any time you want different terrain.
"""

import math
import struct
import zlib
from pathlib import Path

# ── PNG helpers ───────────────────────────────────────────────────────────────

def _chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _grey_png(w: int, h: int, pixels: list) -> bytes:
    rows = b"".join(b"\x00" + bytes(pixels[y * w : (y + 1) * w]) for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)  # 8-bit greyscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(rows, 6))
        + _chunk(b"IEND", b"")
    )


def _rgb_png(w: int, h: int, pixels: list) -> bytes:
    rows = b""
    for y in range(h):
        row = b""
        for r, g, b in pixels[y * w : (y + 1) * w]:
            row += bytes([r, g, b])
        rows += b"\x00" + row
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(rows, 6))
        + _chunk(b"IEND", b"")
    )


# ── Heightmap: 513×513 greyscale elevation ────────────────────────────────────

def _heightmap(size: int = 513) -> list:
    """Gentle rolling Paris-basin hills via weighted sum of sine harmonics."""
    pixels = []
    for y in range(size):
        for x in range(size):
            fx, fy = x / size, y / size
            h = (
                0.38 * math.sin(fx * math.pi * 2.3) * math.sin(fy * math.pi * 1.9)
                + 0.28 * math.sin(fx * math.pi * 4.1 + 0.9) * math.cos(fy * math.pi * 3.7 + 1.3)
                + 0.18 * math.cos(fx * math.pi * 7.5 + 2.1) * math.sin(fy * math.pi * 6.3 - 0.5)
                + 0.10 * math.sin(fx * math.pi * 13.1 + 1.6) * math.cos(fy * math.pi * 11.9 + 0.7)
                + 0.06 * math.cos(fx * math.pi * 21.7 - 0.6) * math.sin(fy * math.pi * 18.4 + 2.0)
            )
            # Normalise [-1, 1] → [0.05, 0.55] — mostly low elevation
            h = 0.05 + (h + 1.0) / 2.0 * 0.50
            pixels.append(max(0, min(255, round(h * 255))))
    return pixels


# ── Grass diffuse: 64×64 procedural green texture ─────────────────────────────

def _grass_diffuse(size: int = 64) -> list:
    pixels = []
    for y in range(size):
        for x in range(size):
            noise = math.sin(x * 3.7 + y * 5.3) * 8 + math.sin(x * 11.1 - y * 7.2) * 6
            r = max(0, min(255, 72 + round(noise * 0.5)))
            g = max(0, min(255, 115 + round(noise)))
            b = max(0, min(255, 42 + round(noise * 0.3)))
            pixels.append((r, g, b))
    return pixels


# ── Flat normal map: 64×64 straight-up normals (0,0,1) → RGB(128,128,255) ────

def _flat_normal(size: int = 64) -> list:
    return [(128, 128, 255)] * (size * size)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    out = Path(__file__).parent / "assets"
    out.mkdir(exist_ok=True)

    hm = _heightmap(513)
    (out / "heightmap.png").write_bytes(_grey_png(513, 513, hm))
    print(f"heightmap.png      written  (513×513, raw {min(hm)}–{max(hm)}, ≈ 0–50 m elevation)")

    gd = _grass_diffuse(64)
    (out / "grass_diffuse.png").write_bytes(_rgb_png(64, 64, gd))
    print("grass_diffuse.png  written  (64×64 green diffuse)")

    fn = _flat_normal(64)
    (out / "flat_normal.png").write_bytes(_rgb_png(64, 64, fn))
    print("flat_normal.png    written  (64×64 straight-up normal map)")


if __name__ == "__main__":
    main()
