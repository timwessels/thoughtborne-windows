# Thoughtborne logo assets

The mark is a night-blue disc crossed by an organic channel of negative
space — a stream bed seen from above: the bed that gives the flow its shape
(idle), the flow when something runs through it (recording). The wordmark
sets the silent final **e** apart in gray: spoken, *thoughtborn/thoughtborne*
are identical — only writing it down decides.

Two marks, two jobs (D-016): this round *flow mark* is the umbrella brand —
GitHub avatar, lockup, anything that speaks for the project as a whole. The
app itself wears the *pixel mark*, the 7×6 half-block glyph from the console
masthead (`console_ui.LOGO_MARK_A5`): the website favicon, and on Windows
`thoughtborne.ico` — the one app-icon file behind the Start-menu shortcut,
the taskbar, *Settings > Installed apps* and the settings window.

## Files

| File | Use |
|---|---|
| `thoughtborne-mark.svg` | The mark, brand navy on transparent — master |
| `thoughtborne-mark-white.svg` | White mark for dark backgrounds |
| `thoughtborne-mark-mono.svg` | Black mono variant (print, single-color) |
| `thoughtborne-wordmark.svg` | Wordmark for light backgrounds |
| `thoughtborne-wordmark-dark.svg` | Wordmark for dark backgrounds |
| `thoughtborne-wordmark-mono.svg` | Black wordmark, silent e in gray |
| `thoughtborne-wordmark-mono-solid.svg` | Strictly one color (engraving, 1-bit contexts) |
| `thoughtborne-pixel-lockup.svg` / `-dark.svg` | The console masthead's half-block lockup as crisp pixel rects (mark in the console accent, wordmark in navy / off-white), light/dark — README header since 2026-09; generated from `console_ui.LOGO_MARK_A5` + the wordmark art, keep in sync with `console_ui.py` and the site hero |
| `thoughtborne-lockup.svg` / `-dark.svg` | Mark + wordmark (Inter), light/dark — the former README header |
| `thoughtborne.ico` | The Windows app icon (D-016): the pixel mark in the console accent on a hard-cornered `#242424` tile — the mark plus one ring of ground pixels (9×8), ten integer-scaled frames 16–256 px, no anti-aliasing. Generated, never hand-edited |
| `make_app_icon.py` | Rebuilds `thoughtborne.ico` from `console_ui.LOGO_MARK_A5` and `console_ui.ACCENT` (needs Pillow; build-time only) — run it when the mark or the accent changes |
| `png/` | Raster exports of the masters (mark 16–512, wordmark/lockup 1024) |
| `thoughtborne-avatar-512.png` | GitHub avatar upload — flattened on white so the channel reads in every theme |
| `thoughtborne-social-preview.png` | GitHub social preview, 1280×640 |

## Palette

| Color | Hex | Role |
|---|---|---|
| Brand navy | `#0E202E` | The mark, wordmark text on light |
| Silent-e gray (light bg) | `#8A939C` | Final e in the light wordmark |
| Silent-e gray (dark bg) | `#71828F` | Final e in the dark wordmark |
| Mono gray | `#777777` | Final e in the mono wordmark |
| White | `#FFFFFF` | Mark/wordmark on dark |
| Console accent | `#59C2FF` | The pixel mark — console masthead, site favicon on dark, the app icon (`console_ui.ACCENT`) |
| App-icon tile | `#242424` | Ground of `thoughtborne.ico`: neutral dark grey, equal RGB, no blue cast |

Typography: Inter Medium (SIL OFL), +2.5% em tracking, lowercase — converted
to paths, no font files needed. The channel in the mark is true negative
space: whatever sits behind the logo shows through.
