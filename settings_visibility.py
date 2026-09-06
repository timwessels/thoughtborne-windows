"""Stdlib-only helpers behind the settings app's visibility fix (#203) and, since #240,
its whole `[SETTINGS]` log lane.

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
  - `format_settings_line(stamp, tag, body)` and `format_error_block(...)` -- the shared
    shape of every other `[SETTINGS]` record and, for an unhandled exception, its lead
    line plus indented traceback (#240).
  - `append_log_line(log_path, text)` -- the one guarded sink every settings-side log
    write goes through. "Pure" stops here: this one does IO. It takes the log path as a
    parameter rather than importing `config`, which is what lets the ladder point it at
    a tempdir; it stays tkinter-free like everything else here.

These are why the #203 wrap fix, its instrumentation and the #240 crash lane are
testable without a display -- see `test_settings_visibility.py`.
"""
import traceback


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


def format_settings_line(stamp, tag, body) -> str:
    """One `[SETTINGS]` record: `{stamp} [SETTINGS] {tag}: {body}` plus a newline (#240).

    The shape the startup, focus-existing and import-warning lines share, and the lead
    line of an error block; `format_visible_line` builds its own body layout above and
    bypasses this.

    The body's whitespace is collapsed to single spaces, because a record is one
    greppable line: an exception message or an import warning carrying a newline would
    otherwise continue below as text that reads like an independent, timestamp-less
    record. The bodies that existed before #240 are single-spaced already, so their
    lines stay byte-identical -- `test_settings_visibility.py` pins that against the
    literal f-strings they used to be.
    """
    return f"{stamp} [SETTINGS] {tag}: {' '.join(str(body).split())}\n"


def format_error_block(stamp, context, exc, val, tb) -> str:
    """An unhandled exception as an `[SETTINGS] error:` lead line plus its traceback,
    every continuation line indented four spaces (#240).

    `context` names the lane ("callback" or "main") and the lead carries type and
    message, so that one line still stands alone if the tool's log rotation tears the
    block apart. The indentation is what keeps a timestamp grep and the eye honest: a
    continuation can never be mistaken for a record of its own.

    Never raises -- it runs inside the crash path itself, where a second exception
    would be the end of the story -- and captures no locals: `traceback.format_exception`
    prints source lines and messages only, never values, which is what keeps API keys
    out of the log (never hand it `capture_locals`).
    """
    try:
        name = getattr(exc, "__name__", None) or type(val).__name__
        try:
            message = str(val)
        except Exception:
            # A __str__ that itself raises: format_exception below survives it (it
            # renders "<unprintable ...>"), only this lead needs its own guard.
            message = "(unprintable exception message)"
        lines = []
        for chunk in traceback.format_exception(exc, val, tb):
            lines.extend(chunk.splitlines())
        return (format_settings_line(stamp, "error", f"{context}: {name}: {message}")
                + "".join(f"    {ln}\n" for ln in lines))
    except Exception:
        return format_settings_line(stamp, "error",
                                    f"{context}: (unformattable exception)")


def append_log_line(log_path, text) -> bool:
    """Append one already-formatted record to the tool's log; return False on any
    fault instead of raising (#240).

    The single sink every settings-side log write goes through -- the startup and
    `visible:` timing lines, the focus-existing outcome, replayed import warnings and
    the crash blocks. Same mechanics as the three plain appends it replaces: a single
    open-append at a rare event, no RotatingFileHandler (this is a second process
    writing the tool's log, and a two-process rotation would race, D-009), so a line
    lost to the tool's own rotation mid-append stays the documented, harmless
    trade-off. `backslashreplace` so an exotic message -- a lone surrogate out of a
    Tcl error -- costs a character rather than the whole line.

    Never raising is the point: instrumentation must never break the app, and the
    crash lane cannot afford a second exception.
    """
    try:
        with open(log_path, "a", encoding="utf-8", errors="backslashreplace") as fh:
            fh.write(text)
        return True
    except Exception:
        return False
