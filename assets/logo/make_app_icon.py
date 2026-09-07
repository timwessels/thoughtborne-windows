#!/usr/bin/env python3
"""Build ``assets/logo/thoughtborne.ico`` -- the Windows app icon (D-016).

The icon is the console masthead's pixel mark (``console_ui.LOGO_MARK_A5``, half-block
art decoded to a 7x6 bitmap) in the console accent, on a hard-cornered tile of neutral
dark grey: the mark plus exactly one ring of ground pixels, a 9x8 grid, scaled by an
integer per frame so every pixel edge stays sharp. No anti-aliasing anywhere. Rebuild
whenever the mark or the accent changes -- never hand-edit or resample the .ico:

    pip install pillow            # build-time only, not a runtime dependency
    python assets/logo/make_app_icon.py

Frames: 16 20 24 32 40 48 64 96 128 256. At 16 and 24 px the full grid would fill
under 80 % of the canvas, so there the ring is one device pixel wide and the mark
takes the next integer scale (16x14, 23x20); every other frame is the true 9x8 grid.
"""
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
import console_ui  # noqa: E402  -- the mark and the accent come from the console itself

OUT = HERE / "thoughtborne.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
GROUND = (0x24, 0x24, 0x24)          # neutral dark grey, equal RGB: no blue cast, not pure black
ACCENT = tuple(int(c) for c in re.fullmatch(r"38;2;(\d+);(\d+);(\d+)", console_ui.ACCENT).groups())


def decode_mark(rows):
    """Half-block art -> bitmap: a text row is two pixel rows (upper `▀`, lower `▄`, both `█`)."""
    width = max(len(r) for r in rows)
    bitmap = []
    for r in rows:
        r = r.ljust(width)
        bitmap.append([ch in "█▀" for ch in r])
        bitmap.append([ch in "█▄" for ch in r])
    return bitmap


MARK = decode_mark(console_ui.LOGO_MARK_A5)
MW, MH = len(MARK[0]), len(MARK)      # 7 x 6


def frame(n):
    k = n // (MW + 2)                 # scale at which the 9-wide grid still fits
    ring = k
    if (MW + 2) * k < 0.8 * n:        # 16 and 24 px: one-device-pixel ring, mark one scale up
        k, ring = (n - 2) // MW, 1
    tw, th = MW * k + 2 * ring, MH * k + 2 * ring
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x0, y0 = (n - tw) // 2, (n - th) // 2
    draw.rectangle([x0, y0, x0 + tw - 1, y0 + th - 1], fill=GROUND + (255,))
    for y, row in enumerate(MARK):
        for x, on in enumerate(row):
            if on:
                px, py = x0 + ring + x * k, y0 + ring + y * k
                draw.rectangle([px, py, px + k - 1, py + k - 1], fill=ACCENT + (255,))
    return img, (tw, th)


def main():
    frames = []
    for n in sorted(SIZES, reverse=True):     # Pillow drops sizes larger than the base image
        img, (tw, th) = frame(n)
        frames.append(img)
        print(f"{n:>4} px  tile {tw}x{th}")
    frames[0].save(OUT, format="ICO", sizes=[(f.width, f.height) for f in frames], append_images=frames[1:])
    print("wrote", OUT.relative_to(HERE.parent.parent))


if __name__ == "__main__":
    main()
