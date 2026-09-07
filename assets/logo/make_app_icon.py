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

Frames up to 64 px are stored as plain 32-bit bitmaps, the larger ones as PNG (the
classic Windows layout). The bitmap frames are not a nicety: Tk's own ICO reader
(tkWinWm.c, still in 8.6.16) takes each frame's size from its bitmap header, so a
PNG frame reads as garbage, no frame ever matches 16 or 32, and the settings window
ends up with a smoothly scaled first frame instead of the crisp pixel art (verified
on Tk 8.6.12, 2026-09-07). Pillow's ICO writer is all-PNG or all-BMP, hence the
small writer below.
"""
import io
import re
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
import console_ui  # noqa: E402  -- the mark and the accent come from the console itself

OUT = HERE / "thoughtborne.ico"
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
GROUND = (0x24, 0x24, 0x24)          # neutral dark grey, equal RGB: no blue cast, not pure black
PNG_FROM = 96                        # frames this size and up are PNG-compressed, smaller ones bitmaps

_rgb = re.fullmatch(r"38;2;(\d+);(\d+);(\d+)", console_ui.ACCENT)
if not _rgb:
    sys.exit(f"console_ui.ACCENT is {console_ui.ACCENT!r}, not a 24-bit SGR colour -- "
             "the icon needs an RGB accent; pass one explicitly here if the console falls back to CYAN")
ACCENT = tuple(int(c) for c in _rgb.groups())


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


def bmp_entry(img):
    """A frame as the ICO's classic payload: BITMAPINFOHEADER (height doubled, as the
    format demands), bottom-up 32-bit BGRA rows, then the 1-bit AND mask (1 = see-through)."""
    w, h = img.size
    px = img.load()
    xor = bytearray()
    mask = bytearray()
    mask_row = ((w + 31) // 32) * 4
    for y in range(h - 1, -1, -1):
        row = bytearray(mask_row)
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))
            if a == 0:
                row[x // 8] |= 0x80 >> (x % 8)
        mask += row
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + xor + mask


def png_entry(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def write_ico(path, frames):
    entries = [(img, png_entry(img) if img.width >= PNG_FROM else bmp_entry(img)) for img in frames]
    out = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    offset = 6 + 16 * len(entries)
    for img, data in entries:           # directory: 256 is written as 0, as the format demands
        out += struct.pack("<BBBBHHII", img.width % 256, img.height % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _, data in entries:
        out += data
    path.write_bytes(out)


def main():
    frames = []
    for n in SIZES:
        img, (tw, th) = frame(n)
        frames.append(img)
        print(f"{n:>4} px  tile {tw}x{th}  {'png' if n >= PNG_FROM else 'bmp'}")
    write_ico(OUT, frames)
    print(f"wrote {OUT.relative_to(HERE.parent.parent)} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
