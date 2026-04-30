"""Generate PWA placeholder PNG icons using only the Python stdlib.

Produces solid indigo (#4f46e5) squares with a white "RFM" wordmark drawn
from a simple bitmap font. Run once; commit the PNGs.

Usage:  python scripts/generate_pwa_icons.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

# Brand colours
BG = (0x4F, 0x46, 0xE5)        # indigo-600
FG = (0xFF, 0xFF, 0xFF)         # white
MASKABLE_BG = (0x3F, 0x37, 0xC9)  # slightly darker for safe-zone visibility

# 5x7 bitmap font for the letters R, F, M (1 = filled)
GLYPHS = {
    "R": [
        "11110",
        "10001",
        "10001",
        "11110",
        "10100",
        "10010",
        "10001",
    ],
    "F": [
        "11111",
        "10000",
        "10000",
        "11110",
        "10000",
        "10000",
        "10000",
    ],
    "M": [
        "10001",
        "11011",
        "10101",
        "10001",
        "10001",
        "10001",
        "10001",
    ],
}


def make_pixels(size: int, bg: tuple[int, int, int], maskable: bool) -> list[list[tuple[int, int, int]]]:
    pixels = [[bg for _ in range(size)] for _ in range(size)]

    # Maskable icons need content within ~80% safe zone (10% padding each side).
    # Non-maskable can fill more.
    safe = 0.6 if maskable else 0.78
    text = "RFM"
    glyph_h = 7
    glyph_w = 5
    gap = 1  # cells between letters
    total_cells_w = len(text) * glyph_w + (len(text) - 1) * gap

    target_w = int(size * safe)
    cell = max(1, target_w // total_cells_w)
    pixel_w = total_cells_w * cell
    pixel_h = glyph_h * cell

    x0 = (size - pixel_w) // 2
    y0 = (size - pixel_h) // 2

    cx = x0
    for ch in text:
        glyph = GLYPHS[ch]
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == "1":
                    px = cx + gx * cell
                    py = y0 + gy * cell
                    for dy in range(cell):
                        for dx in range(cell):
                            if 0 <= px + dx < size and 0 <= py + dy < size:
                                pixels[py + dy][px + dx] = FG
        cx += (glyph_w + gap) * cell

    return pixels


def write_png(path: Path, pixels: list[list[tuple[int, int, int]]]) -> None:
    height = len(pixels)
    width = len(pixels[0])

    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type: None
        for r, g, b in row:
            raw.extend((r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(bytes(raw), 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "static" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    targets = [
        ("icon-192.png", 192, BG, False),
        ("icon-512.png", 512, BG, False),
        ("icon-maskable-192.png", 192, MASKABLE_BG, True),
        ("icon-maskable-512.png", 512, MASKABLE_BG, True),
    ]

    for name, size, bg, maskable in targets:
        pixels = make_pixels(size, bg, maskable)
        write_png(out_dir / name, pixels)
        print(f"wrote {out_dir / name}")


if __name__ == "__main__":
    main()
