#!/usr/bin/env python3
"""Off-Windows verification of the pure settings-visibility helpers (#203).

`settings_visibility` holds the two tkinter-free cores the #203 fix extracted from
`thoughtborne_settings.py` so they can be checked on plain Python, without a display
or Windows:

  - `scrollbar_should_show(lo, hi)`: the #180 scroll-canvas auto-hide decision. The
    bar shows exactly when the body overflows -- i.e. NOT when the whole content fits
    (top fraction 0, bottom fraction 1). This is the single source of the overflow
    rule the (Tk-near) idempotency guard flips on; a regression here would either hide
    a needed bar or, worse, re-introduce the storm the guard exists to stop.
  - `format_visible_line(...)`: the `[SETTINGS] visible:` log line built at first
    paint. Pure and fail-open -- a missing spawn stamp drops the timing fields, an
    unreadable best-effort probe renders as '?', and the shape mirrors the existing
    `[SETTINGS] startup:` line (stamp + space-joined parts + newline).

And, WITH a display (skipped cleanly without tkinter/a display, the normal WSL case),
a regression check against the REAL app that patches tkinter's geometry methods and
`_push_wraps` and builds + settles the window. It asserts two things:

  (a) the #180/#203 auto-hide idempotency: each scrollbar's `grid()`/`grid_remove()`
      action sequence strictly alternates -- never two identical actions in a row --
      because the `_bar_shown` latch flips a bar only on a real overflow-state change.
      This tests the latch directly and machine-independently; a churn *count* ceiling no
      longer works, since the wrap deferral (b) masks the storm the missing latch used to
      cause (dropping it now nudges the count by only a handful), and

  (b) the #203 wrap-deferral invariant: some label is wrapped AND every wraplength write
      goes through the coalesced `_push_wraps`, never directly on a label `<Configure>`.
      This is the platform-independent guard the real fix rests on. The regression -- a
      per-`<Configure>` immediate wraplength set -- feeds a wraplength->height->relayout
      loop whose write count balloons on X11 too (measured ~4500+ vs a handful with the
      fix), so this invariant trips at once. What does NOT reproduce under X11 is the
      *cost*: each wraplength change forces a full GDI text remeasurement only on
      Windows, which is what turns those writes into a multi-second stall. So the honest
      performance proof is the maintainer's Windows measurement (map->expose < 1s, the
      `[SETTINGS] visible:` line); this test guards the *shape* of the fix -- that
      wrapping stays deferred and coalesced -- which is what a future edit would break to
      bring the stall back. A dead-guard check confirms a genuine resize still re-wraps.

    python3 test_settings_visibility.py          # verify, exit non-zero on any violation
    python3 test_settings_visibility.py --show   # also print sample visible: lines
"""
import sys

import settings_visibility as sv

SHOW = "--show" in sys.argv

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def test_wrap_length():
    # width - margin, floored; the single formula every wrapping label goes through (#203).
    check(sv.wrap_length(658) == 650, f"658-8 should be 650, got {sv.wrap_length(658)}")
    check(sv.wrap_length(658, margin=8) == 650, "explicit margin 8 should match default")
    check(sv.wrap_length(100) == 120, "below floor+margin -> floor 120")
    check(sv.wrap_length(128) == 120, "exactly floor+margin -> floor 120")
    check(sv.wrap_length(129) == 121, "one past floor+margin -> 121")
    check(sv.wrap_length(658.0) == 650, "a float width is accepted (int-coerced)")
    check(sv.wrap_length(658, margin=0, floor=200) == 658, "margin/floor are honoured")


def test_scrollbar_should_show():
    # Fits exactly -> no bar. Both string (Tk passes strings) and float accepted.
    check(sv.scrollbar_should_show("0.0", "1.0") is False,
          "content that fills the viewport (0..1) should NOT show a bar")
    check(sv.scrollbar_should_show(0.0, 1.0) is False,
          "float 0..1 should NOT show a bar")
    # Overflow at the bottom (hi < 1) -> bar.
    check(sv.scrollbar_should_show("0.0", "0.5") is True,
          "content taller than the viewport (hi<1) should show a bar")
    # Scrolled down (lo > 0) -> bar, even if hi == 1.
    check(sv.scrollbar_should_show("0.4", "1.0") is True,
          "a scrolled-down slice (lo>0) should show a bar")
    # A slightly-over slice (hi just under 1) -> bar.
    check(sv.scrollbar_should_show("0.0", "0.999") is True,
          "a hair of overflow should still show a bar")
    # Degenerate: hi beyond 1 with lo at 0 is still "fits".
    check(sv.scrollbar_should_show("0.0", "1.0000001") is False,
          "hi>=1 with lo<=0 is 'fits' -> no bar")


def test_format_visible_line_full():
    line = sv.format_visible_line(
        "2026-08-16 12:00:00", 0.05, 0.42, 1, "Y", "800x860+100+50", "settings")
    check(line.endswith("\n"), f"line must be newline-terminated: {line!r}")
    check(line.count("\n") == 1, f"line must be a single line: {line!r}")
    body = line.rstrip("\n")
    check(body.startswith("2026-08-16 12:00:00 [SETTINGS] visible: "),
          f"line prefix/tag wrong: {body!r}")
    for token in ("map->expose=0.05s", "total=0.42s", "viewable=1",
                  "foreground=Y", "rect=800x860+100+50", "mode=settings"):
        check(token in body, f"missing field {token!r} in: {body!r}")


def test_format_visible_line_failopen():
    # No spawn stamp -> map->expose and total both drop (mirrors the startup line
    # dropping its spawn-derived fields); probes unreadable -> '?', never a crash.
    line = sv.format_visible_line(
        "2026-08-16 12:00:00", None, None, None, None, None, "firstrun")
    body = line.rstrip("\n")
    check("map->expose=" not in body,
          f"map->expose must be absent when its delta is None: {body!r}")
    check("total=" not in body,
          f"total must be absent when its delta is None: {body!r}")
    check("viewable=?" in body, f"None viewable should render '?': {body!r}")
    check("foreground=?" in body, f"None foreground should render '?': {body!r}")
    check("rect=?" in body, f"None rect should render '?': {body!r}")
    check("mode=firstrun" in body, f"mode must always render: {body!r}")


def test_format_visible_line_partial():
    # Timings present, probes missing (the realistic off-foreground case): the
    # trustworthy timing core stays, the best-effort fields degrade to '?'.
    line = sv.format_visible_line(
        "2026-08-16 12:00:00", 0.0, 12.34, 0, "N", None, "settings")
    body = line.rstrip("\n")
    check("map->expose=0.00s" in body, f"zero delta must still render: {body!r}")
    check("total=12.34s" in body, f"total must render: {body!r}")
    check("viewable=0" in body, f"viewable=0 must render as 0, not '?': {body!r}")
    check("foreground=N" in body, f"foreground=N must render: {body!r}")
    check("rect=?" in body, f"missing rect should render '?': {body!r}")


def test_storm_guards_with_display():
    # Only runs where a display exists (a CI/dev box with Xvfb); the normal WSL case
    # has no tkinter or no display and skips cleanly. Builds the REAL settings app and
    # checks (a) each scrollbar's auto-hide grid/remove sequence strictly alternates (the
    # #180/#203 _bar_shown latch) and (b) every wraplength write is deferred to the
    # coalesced _push_wraps (the #203 wrap fix), plus a dead-guard resize check.
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("  (skipped storm-guard check: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped storm-guard check: no display)")
        return

    try:
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped storm-guard check: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    # State: the per-scrollbar SEQUENCE of grid/grid_remove actions for the #180/#203
    # auto-hide idempotency check (the _bar_shown latch flips a bar only on a real
    # overflow-state change, so its actions must alternate -- never two of the same in a
    # row), plus wraplength writes classified by whether they happen INSIDE _push_wraps
    # (the #203 invariant), and scrollregion for the dead-guard resize check. Patched on
    # the classes + app, restored in finally.
    st = {"wraplength": 0, "scrollregion": 0, "in_push": False, "wrap_outside_push": 0}
    bar_seq = {}   # id(scrollbar) -> ["grid"/"remove", ...] in call order
    _grid = tk.Grid.grid_configure
    _grid_remove = tk.Grid.grid_remove
    _configure = tk.Misc.configure
    _push = ts.SettingsApp._push_wraps

    def grid_configure(self, cnf={}, **kw):
        if isinstance(self, ttk.Scrollbar):
            bar_seq.setdefault(id(self), []).append("grid")
        return _grid(self, cnf, **kw)

    def grid_remove(self):
        if isinstance(self, ttk.Scrollbar):
            bar_seq.setdefault(id(self), []).append("remove")
        return _grid_remove(self)

    def configure(self, cnf=None, **kw):
        keys = set(kw) | (set(cnf) if isinstance(cnf, dict) else set())
        if "wraplength" in keys:
            st["wraplength"] += 1
            if not st["in_push"]:
                st["wrap_outside_push"] += 1
        elif "scrollregion" in keys:
            st["scrollregion"] += 1
        return _configure(self, cnf, **kw)

    def push_wraps(self):
        st["in_push"] = True
        try:
            return _push(self)
        finally:
            st["in_push"] = False

    _showerror = ts.messagebox.showerror
    try:
        tk.Grid.grid_configure = grid_configure
        tk.Grid.grid = grid_configure
        tk.Grid.grid_remove = grid_remove
        tk.Misc.configure = configure
        tk.Misc.config = configure
        ts.SettingsApp._push_wraps = push_wraps
        # __init__ pops a MODAL messagebox.showerror if a present personal_settings.json
        # is unreadable/non-utf-8 (thoughtborne_settings.py, the load-error path); in a
        # headless run no one dismisses it and the test hangs. Neutralize it for the
        # construction (the only modal reachable from __init__), restored in finally.
        ts.messagebox.showerror = lambda *a, **k: None

        root.geometry("800x860")
        ts.SettingsApp(root, first_run=False)
        root.update()                 # drain the initial <Configure> cascade

        # (a) The #180/#203 auto-hide idempotency: the _bar_shown latch flips a
        # scrollbar's grid ONLY on a real overflow-state change, so each bar's action
        # sequence must strictly alternate grid/remove -- never two identical actions in
        # a row. This is machine-independent and tests the latch directly, unlike a churn
        # ceiling (which the wrap deferral now masks: dropping the latch nudges the count
        # by only a handful, so no fixed ceiling separates the two). Without the latch,
        # _autohide re-issues grid()/grid_remove() on every yscrollcommand callback ->
        # consecutive duplicates.
        dup = None
        for seq in bar_seq.values():
            for i in range(1, len(seq)):
                if seq[i] == seq[i - 1]:
                    dup = seq
                    break
            if dup:
                break
        check(dup is None,
              f"a scrollbar repeated a grid action back-to-back ({dup}) -- the #180/#203 "
              "_bar_shown auto-hide latch is missing, so it re-grids on every "
              "yscrollcommand callback instead of only on a real overflow-state change")

        # (b) The #203 wrap-deferral invariant, the platform-independent guard the real
        # fix rests on: some label WAS wrapped, and EVERY wraplength write went through
        # the coalesced _push_wraps -- never directly on a label <Configure>. Reverting
        # to a per-<Configure> wraplength set (the Windows stall) writes outside the push
        # and trips this. (The GDI *cost* of those writes is Windows-only; the maintainer
        # measures that -- see the docstring.)
        check(st["wraplength"] >= 1,
              "no wraplength was ever set -- the #203 wrap push is not running")
        check(st["wrap_outside_push"] == 0,
              f"{st['wrap_outside_push']} wraplength write(s) happened OUTSIDE "
              "_push_wraps -- wrapping must be deferred to the coalesced push, not set "
              "on a label <Configure> (the #203 Windows stall regression)")

        # (c) Dead-guard counter-check: a real resize must still re-wrap, or the deferral
        # has frozen the layout (which would pass (b) falsely with zero writes).
        before = dict(st)
        root.geometry("640x700")
        root.update()
        rewraps = st["wraplength"] - before["wraplength"]
        check(rewraps > 0,
              "a real window resize produced no wraplength writes -- the wrap push is "
              "dead (it must re-wrap on a genuine width change, #203)")
    finally:
        tk.Grid.grid_configure = _grid
        tk.Grid.grid = _grid
        tk.Grid.grid_remove = _grid_remove
        tk.Misc.configure = _configure
        tk.Misc.config = _configure
        ts.SettingsApp._push_wraps = _push
        ts.messagebox.showerror = _showerror
        try:
            root.destroy()
        except Exception:
            pass


def _show():
    print(sv.format_visible_line(
        "2026-08-16 12:00:00", 0.05, 0.42, 1, "Y", "800x860+100+50", "settings"), end="")
    print(sv.format_visible_line(
        "2026-08-16 12:00:00", None, None, None, None, None, "firstrun"), end="")


def main():
    test_wrap_length()
    test_scrollbar_should_show()
    test_format_visible_line_full()
    test_format_visible_line_failopen()
    test_format_visible_line_partial()
    test_storm_guards_with_display()

    if SHOW:
        _show()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: wrap_length formula, scrollbar auto-hide decision, the visible: line "
          "formatter (full / fail-open / partial), and (with a display) the auto-hide "
          "grid idempotency + wrap-deferral invariant all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
