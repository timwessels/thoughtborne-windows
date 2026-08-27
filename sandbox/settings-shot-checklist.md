# Settings-window screenshot checklist (#191)

After the end-to-end self-test, the in-sandbox driver presses `Ctrl+Alt+G`, waits
for the settings window to paint, and captures the whole sandbox desktop as
`screen-settings-<HHMMSS>.png` in that run's `out-<timestamp>/` folder. The
harness cannot grade a picture, so this file is the contract for whoever does --
a person, or a vision model handed the PNG together with this checklist.

The item is **reported, not gating**: the install verdict in `RESULT.txt` stays
PASS/PARTIAL/FAIL on install + hotkeys + self-test alone. Until the verdict file
below exists, the run is honest about the visual item being pending.

## What to answer with

Write the answer as `SETTINGS-SHOT.txt` next to the screenshot, in the same
`out-<timestamp>/` folder (that folder is gitignored, like the screenshot).

```
SETTINGS-SHOT: PASS | FAIL | UNSURE
1: ok
2: problem - one sentence saying what is wrong
...
```

Line 1 is one of the three verdict tokens: `PASS` when every item is ok, `FAIL`
when any item is a real problem, `UNSURE` when the picture cannot settle it (too
small to read, the window is off-screen, the capture is empty). Then one line per
numbered item below: `ok`, or `problem - ` plus one sentence.

## The items

1. **The settings window is there and in front.** It is present, fully on screen
   (no part cut off by a screen edge), and in front of everything else. The log
   line `[SETTINGS] visible: ... foreground=Y ...` in `RESULT.txt` answers the
   "frontmost" half by machine; the picture settles the rest.
2. **No stray extra console window** besides the Cockpit console the tool itself
   runs in. A second, empty console beside the settings window is the #227 class
   of regression (the launcher trampoline allocating a fresh console).
3. **No clipped text anywhere.** No label cut off at a card or column edge, no
   ellipsis where prose is meant to be, no line running under the window border
   or under another widget. This is the #218 / #231 class: a German string is
   longer than its English twin and the first place it shows is a screenshot.
4. **No overlapping widgets.** Buttons, the footer row, and the tab strip are
   fully visible and do not sit on top of each other.
5. **Nothing obviously misrendered.** No black or grey unpainted regions, no
   missing card frames, no scrollbar stuck across the content.

## Notes

- The captures are full-desktop PNGs of a large virtual screen (several MB).
  Downscale or convert to JPEG before handing one to a vision model with an
  upload size limit; do not crop away the desktop, since items 1 and 2 are about
  what else is on it.
- The sandbox runs the tool in its shipped default language. A wrong-language UI
  is not a defect for this checklist; unreadable or clipped text is.
