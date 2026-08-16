#!/usr/bin/env python3
"""Off-Windows verification of the settings-app theme module (#155, D-010).

`settings_theme` is stdlib-only (tkinter/ttk/font), so it must import on plain
Python -- the D-005 system-Python rescue lane runs the app without a venv, and a
theme module that failed to import would break exactly that lane. What can be
checked without a display, and what needs one:

  - the module imports at all on plain Python (the rescue-lane guarantee);
  - every palette constant is a `#RRGGBB` string (a typo like a named colour or a
    3-digit hex would slip past py_compile but fail here);
  - the design meets WCAG on the two surfaces it defines: every text colour clears
    AA (>= 4.5:1) on both PAGE and CARD, and CONTROL_LINE -- the entry/combobox/
    button border, which IS the component boundary on a white-on-white field --
    clears the 3:1 non-text-contrast floor (WCAG 1.4.11). This is the guard that
    catches a future "let's lighten the grey a bit": the first muted candidate
    (#6B7784) really did measure 4.33 on the card and had to be darkened to pass;
  - the type ladder floor (SIZE_BODY >= 10, the one contested number) and that
    FAMILY_CHAIN is a non-empty tuple of families;
  - WITH a display (skipped cleanly without one, the normal WSL case): applying the
    theme really pins `clam`, and Style().lookup("TFrame", "background") == PAGE --
    the #180 invariant, since _scrollable_tab paints its scroll canvas from that
    lookup, so a page colour that stopped matching would show a grey band.

    python3 test_settings_theme.py    # verify, exit non-zero on any violation
"""
import re
import sys

import settings_theme as T

failures = []

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# The palette constants that must be #RRGGBB strings.
COLOUR_NAMES = (
    "INK", "INK_SOFT", "MUTED", "PAGE", "CARD", "CARD_HOVER", "TAB_BG", "LINE",
    "CONTROL_LINE", "FIELD", "SEL", "FOCUS", "WARN_BG", "WARN_LINE",
    "PRIMARY_BG", "PRIMARY_BG_HOVER", "PRIMARY_BG_ACTIVE", "PRIMARY_FG",
    "LINK_COLOR", "TEXT_COLOR", "GREEN", "RED", "GREY", "AMBER",
)

# Text colours that carry standalone text, each with the surface(s) they sit on.
# AA normal text needs 4.5:1; this is the accessibility gate the design commits to.
TEXT_ON = (
    ("INK", ("PAGE", "CARD")),
    ("INK_SOFT", ("PAGE", "CARD")),
    ("MUTED", ("PAGE", "CARD")),
    ("LINK_COLOR", ("PAGE", "CARD")),
    ("GREEN", ("PAGE", "CARD")),
    ("RED", ("PAGE", "CARD")),
    ("GREY", ("PAGE", "CARD")),
    ("AMBER", ("PAGE", "CARD", "WARN_BG")),   # the warn strip sits on WARN_BG
    ("PRIMARY_FG", ("PRIMARY_BG", "PRIMARY_BG_HOVER")),  # white on the navy button
)

AA_TEXT = 4.5
NON_TEXT = 3.0


def check(cond, msg):
    if not cond:
        failures.append(msg)


def _lin(channel):
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(fg, bg):
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_palette_is_rrggbb():
    for name in COLOUR_NAMES:
        value = getattr(T, name, None)
        check(isinstance(value, str) and bool(_HEX.match(value or "")),
              f"{name} is not a #RRGGBB string (got {value!r})")


def test_text_contrast_is_AA():
    for fg_name, surfaces in TEXT_ON:
        for bg_name in surfaces:
            ratio = _contrast(getattr(T, fg_name), getattr(T, bg_name))
            check(ratio >= AA_TEXT,
                  f"{fg_name} on {bg_name} = {ratio:.2f} < {AA_TEXT} (WCAG AA text)")


def test_control_line_meets_non_text_contrast():
    # The field border IS the component boundary on a white-on-white entry, so it
    # must clear WCAG 1.4.11's 3:1 -- deliberately darker than the decorative LINE.
    for bg_name in ("PAGE", "CARD"):
        ratio = _contrast(T.CONTROL_LINE, getattr(T, bg_name))
        check(ratio >= NON_TEXT,
              f"CONTROL_LINE on {bg_name} = {ratio:.2f} < {NON_TEXT} (WCAG 1.4.11)")


def test_type_ladder_floor():
    check(T.SIZE_BODY >= 10,
          f"SIZE_BODY is {T.SIZE_BODY}, below the 10 pt floor this pass committed to")
    # The ladder must be strictly ordered small < body < h2 < h1 < title.
    ladder = (T.SIZE_SMALL, T.SIZE_BODY, T.SIZE_H2, T.SIZE_H1, T.SIZE_TITLE)
    check(list(ladder) == sorted(ladder) and len(set(ladder)) == len(ladder),
          f"the type ladder is not strictly increasing: {ladder}")
    check(isinstance(T.FAMILY_CHAIN, tuple) and len(T.FAMILY_CHAIN) >= 1
          and all(isinstance(f, str) and f for f in T.FAMILY_CHAIN),
          "FAMILY_CHAIN must be a non-empty tuple of non-empty family strings")


def test_apply_theme_with_display():
    # Only runs where a display exists (a CI/dev box with Xvfb); the normal WSL case
    # has none and skips cleanly -- the point of the check is the #180 sync invariant
    # and that clam is really pinned, both verifiable only against a live root.
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("  (skipped display checks: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped display checks: no display)")
        return
    try:
        th = T.apply_theme(root)
        check(ttk.Style(root).theme_use() == "clam",
              "apply_theme did not pin the clam theme")
        bg = ttk.Style(root).lookup("TFrame", "background")
        check(bg == T.PAGE,
              f"TFrame background is {bg!r}, not PAGE {T.PAGE!r} -- the #180 canvas "
              "would show a grey band")
        # The measured content column must be a positive pixel width.
        check(isinstance(th.column_px(), int) and th.column_px() > 0,
              f"column_px() returned a non-positive width: {th.column_px()!r}")
        check(th.sp(0) == 1 and th.sp(10) >= 10,
              "sp() must floor at 1px and scale a token up by at least the DPI factor")
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def main():
    test_palette_is_rrggbb()
    test_text_contrast_is_AA()
    test_control_line_meets_non_text_contrast()
    test_type_ladder_floor()
    test_apply_theme_with_display()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: palette is #RRGGBB, every text colour clears AA on page + card, "
          "CONTROL_LINE clears 3:1, the type ladder floor holds, and (with a "
          "display) clam is pinned with TFrame == PAGE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
