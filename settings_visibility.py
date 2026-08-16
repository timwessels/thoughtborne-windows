"""Pure, stdlib-only helpers behind the settings app's visibility fix (#203).

Tkinter-free cores extracted from `thoughtborne_settings.py` so the core test ladder
can check them on plain Python off-Windows (that module imports tkinter at its top, so
nothing testable off-Windows may live there):

  - `wrap_length(width, margin, floor)` -- the one wraplength formula every wrapping
    label goes through in the app's coalesced wrap push, kept here so it is testable and
    single-sourced (#203).
  - `scrollbar_should_show(lo, hi)` -- the #180 scroll-canvas auto-hide decision:
    whether the vertical scrollbar should be visible for the given `yscrollcommand`
    fractions. The idempotency guard around it (flip the grid only on a real state
    change) is Tk-near and stays in the app; this pure predicate is the testable core
    and the single source of the "content overflows" rule.
  - `format_visible_line(...)` -- the `[SETTINGS] visible:` log line built at first
    `<Expose>` (the OS-paint signal): given already-computed timing deltas and a set
    of best-effort probe strings, return the exact one-line record. Kept free of
    tkinter/ctypes and of the probing itself, so the format is checkable off-Windows;
    the caller gathers the Windows-only probes and passes `None` for any it could not
    read, which renders as `?` here.

These are why the #203 wrap fix and its instrumentation are testable without a display
-- see `test_settings_visibility.py`.
"""


def wrap_length(width, margin=8, floor=120) -> int:
    """The wraplength (px) for a fill='x' label of realized width `width`: its own width
    less a small margin, floored so a very narrow column never collapses the text. The
    single formula every wrapping label goes through in the app's coalesced wrap push
    (#203), so no two call sites can drift. Pure; the caller supplies the width (the
    label's realized winfo_width from its last <Configure>)."""
    return max(int(width) - margin, floor)


def scrollbar_should_show(lo, hi) -> bool:
    """True when the body overflows the viewport, so the scrollbar should be shown.

    `lo`/`hi` are the two fractions Tk passes to a `yscrollcommand` (as strings): the
    top and bottom of the visible slice in [0, 1]. The bar is unnecessary exactly when
    the whole body fits -- top at 0 and bottom at 1 -- so it should show otherwise.
    Mirrors the original inline test `not (float(lo) <= 0.0 and float(hi) >= 1.0)`.
    """
    return not (float(lo) <= 0.0 and float(hi) >= 1.0)


def format_visible_line(stamp, map_to_expose, total, viewable, foreground, rect,
                        mode) -> str:
    """Build the newline-terminated `[SETTINGS] visible:` log line (#203).

    Pure and fail-open by construction, mirroring the `[SETTINGS] startup:` line's
    `parts`-join style:
      - `map_to_expose`, `total` are seconds (floats) or `None` when the cross-process
        spawn stamp was missing; a `None` delta drops its field entirely, exactly like
        the startup line drops its spawn-derived fields.
      - `viewable` (0/1), `foreground` ("Y"/"N"), `rect` ("WxH+X+Y") are best-effort
        probe results the caller gathered on Windows; any that could not be read is
        passed as `None` and renders as `?`, so the line never depends on them.
      - `mode` is "firstrun" or "settings".
    tkinter/ctypes-free so the format is verifiable off-Windows.
    """
    parts = []
    if map_to_expose is not None:
        parts.append(f"map->expose={map_to_expose:.2f}s")
    if total is not None:
        parts.append(f"total={total:.2f}s")
    parts.append(f"viewable={viewable if viewable is not None else '?'}")
    parts.append(f"foreground={foreground if foreground is not None else '?'}")
    parts.append(f"rect={rect if rect is not None else '?'}")
    parts.append(f"mode={mode}")
    return f"{stamp} [SETTINGS] visible: {' '.join(parts)}\n"
