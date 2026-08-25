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

And a second display-gated regression, `test_maximize_restore_with_display`, drives a
maximize -> restore width cycle (800 -> 3400 -> 800) on both the Overview and Provider
tabs and asserts the #216 content-vanish fix: after the restore the active tab's body
stays mapped, origin-anchored (scrollregion x1=0) and inside the viewport, rather than
parked off-view and unmapped. It fails on the unfixed code with the exact frozen-state
signature -- a stale, maximized-era scrollregion and a view clamped off the content.

A third, `test_verdict_wrap_with_display`, delivers every key-test verdict to both provider
cards and asserts the #231 fix: the verdict line is not clipped at the column edge (the
bug), has not collapsed to the wraplength floor (the trap the obvious fix falls into -- a
label packed without `fill="x"` gets its own requested width from pack, so wrapping feeds
back on itself), keeps a width that does not depend on its text at all (the mechanism
`fill="x"` provides), and really does wrap whenever the text outgrows the label. The #179
Soniox balance note on the same card is covered with it -- it shipped with the identical
fault. A dead guard on the cost side confirms a genuine width change still re-wraps, once
per settled width. All thresholds are relative (ratios and comparisons between two
measurements), never pixel constants.

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


def test_maximize_restore_with_display():
    # Only runs where a display exists (Xvfb on a CI/dev box); the normal WSL case has
    # no tkinter or no display and skips cleanly. Builds the REAL settings app and
    # reproduces the #216 freeze: a maximize -> restore geometry cycle on a WIDE screen
    # used to leave the active tab's content permanently blank. The scrollregion was fed
    # ONLY from the body's <Configure> echo, and the restore path silences that echo by
    # unmapping the off-view body (an unmapped item is never physically moved, so it fires
    # no <Configure>), freezing a stale, maximized-era region and a parked view. The fix
    # anchors the scrollregion at the canvas origin (x1=0) and refreshes it synchronously
    # in the canvas-resize handler, so the body stays mapped and centred. Both tab 0
    # (Overview) and tab 1 (Provider) were candidates; the fix is tab-agnostic, so both
    # are exercised. No method patching is needed -- the real geometry cascade runs.
    try:
        import tkinter as tk
    except Exception:
        print("  (skipped maximize-restore check: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped maximize-restore check: no display)")
        return

    try:
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped maximize-restore check: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    def settle():
        # Drain the multi-round geometry cascade (canvas -> body -> scrollregion ->
        # auto-hide -> canvas). A single update() is not enough across a big geometry step.
        for _ in range(6):
            root.update_idletasks()
            root.update()

    def window_item(c):
        # The canvas carries exactly one embedded window (the body frame); return
        # (item_id, body_widget) or (None, None).
        for item in c.find_all():
            if c.type(item) == "window":
                return item, root.nametowidget(c.itemcget(item, "window"))
        return None, None

    _showerror = ts.messagebox.showerror
    try:
        # __init__ can pop a MODAL showerror on an unreadable personal_settings.json; no
        # one dismisses it headless, so the test would hang. Neutralize it for the build.
        ts.messagebox.showerror = lambda *a, **k: None
        root.geometry("800x860")
        app = ts.SettingsApp(root, first_run=False)
        settle()

        for tab_idx in (0, 1):
            app.notebook.select(tab_idx)
            settle()
            # Maximize stand-in: a window far wider than the 800px restore, past the #216
            # threshold (the freeze needs the stale region and the restored viewport
            # horizontally disjoint after clamping -- ~2360px for an 800px window).
            root.geometry("3400x1500")
            settle()
            big_w = root.winfo_width()
            if big_w < 2400:
                # A WM-having dev box may clamp the geometry to the screen; bare Xvfb does
                # not. Without the wide step the freeze cannot form, so there is nothing to
                # assert -- skip cleanly rather than pass vacuously.
                print(f"  (skipped maximize-restore check: window reached only {big_w}px "
                      "< 2400px on the wide step; the freeze provably needs the width step)")
                return
            root.geometry("800x860")
            settle()

            c = app._tab_canvases[tab_idx]
            item, body = window_item(c)
            check(item is not None and body is not None,
                  f"tab {tab_idx}: no embedded window item found on the active canvas")
            if item is None:
                continue
            coords_x = int(c.coords(item)[0])
            item_w = int(c.itemcget(item, "width"))
            bbox = c.bbox("all")
            sr = c.cget("scrollregion")
            sr_parts = [int(float(v)) for v in str(sr).split()] if sr else []

            # The vanish itself: after restore the body must be MAPPED (the frozen state
            # left it unmapped and fully off-view).
            check(body.winfo_ismapped(),
                  f"tab {tab_idx}: body is NOT mapped after maximize->restore -- the #216 "
                  "content-vanish freeze (scrollregion never refreshed, body parked "
                  "off-view and unmapped)")
            # Physical x == recorded item x => the view origin is 0 => correct centring. In
            # the frozen state these diverge (measured ~1340 physical vs 33 recorded).
            check(body.winfo_x() == coords_x,
                  f"tab {tab_idx}: body physical x ({body.winfo_x()}) != recorded item x "
                  f"({coords_x}) -- the view is parked off the content origin (#216)")
            # The normalized scrollregion contract: x1 anchored at 0, y2 == bbox height.
            check(len(sr_parts) == 4 and sr_parts[0] == 0,
                  f"tab {tab_idx}: scrollregion x1 must be 0, got {sr!r}")
            check(bool(bbox) and len(sr_parts) == 4 and sr_parts[3] == bbox[3],
                  f"tab {tab_idx}: scrollregion y2 must equal bbox[3] "
                  f"({bbox[3] if bbox else None}), got scrollregion {sr!r}")
            # The body sits inside the viewport (its right edge within the canvas width).
            check(coords_x + item_w <= c.winfo_width(),
                  f"tab {tab_idx}: body right edge ({coords_x + item_w}) exceeds canvas "
                  f"width ({c.winfo_width()}) -- content pushed out of the viewport (#216)")
    finally:
        ts.messagebox.showerror = _showerror
        try:
            root.destroy()
        except Exception:
            pass


def test_verdict_wrap_with_display():
    # Only runs where a display exists (Xvfb on a CI/dev box); the normal WSL case skips
    # cleanly. Builds the REAL settings app on the Provider tab and delivers each verdict
    # to both cards, guarding the #231 fix AND the trap the obvious fix falls into.
    #
    # #231: the verdict label was a plain, unregistered ttk.Label, so a full-sentence
    # verdict clipped at the column edge -- hiding exactly the reassuring "saving works
    # anyway" half. Registering it for wrapping alone is NOT enough: a label packed
    # without fill="x" gets its OWN requested width from pack, so every wraplength push
    # shrinks it, and (starting from empty text) it is pinned at the 120px floor as a
    # narrow vertical ribbon -- worse than the clipping it replaced, and invisible to the
    # #203 invariant test (those writes do go through _push_wraps). fill="x" pins the
    # width to the card, which makes that feedback structurally impossible.
    #
    # The thresholds are relative (ratios and comparisons between two measurements), never
    # pixel constants -- the absolute numbers depend on font and DPI.
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("  (skipped verdict-wrap check: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped verdict-wrap check: no display)")
        return

    try:
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped verdict-wrap check: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    KeyStatus = ts.KeyStatus
    writes = {}                       # id(widget) -> wraplength writes so far
    _configure = tk.Misc.configure

    def configure(self, cnf=None, **kw):
        keys = set(kw) | (set(cnf) if isinstance(cnf, dict) else set())
        if "wraplength" in keys:
            writes[id(self)] = writes.get(id(self), 0) + 1
        return _configure(self, cnf, **kw)

    def settle():
        # Drain the multi-round cascade: text -> label <Configure> -> idle wrap push.
        for _ in range(6):
            root.update_idletasks()
            root.update()

    def deliver(provider, status):
        app._test_state[provider] = status
        app._render_indicator(provider)
        settle()

    def natural_width(lbl):
        # What this text would need on ONE line: a throwaway twin in the same style,
        # without the wraplength the real label carries. Measured, not assumed, so the
        # "did it wrap" check below stays honest if a verdict string is ever reworded.
        probe = ttk.Label(lbl.master, style=str(lbl.cget("style")), text=lbl.cget("text"))
        try:
            return probe.winfo_reqwidth()
        finally:
            probe.destroy()

    def check_readable(lbl, what, short_w, short_h):
        # The first two guards are the two failure modes above; the third is the
        # mechanism itself -- a label whose width is pinned to its parcel cannot feed its
        # own wrapping, which is why the second can never come back. The last one is the
        # counter-check that the text really wrapped rather than being hidden, and it
        # only applies where the text actually outgrows the label.
        card_w = lbl.master.winfo_width()
        w, h, reqw = lbl.winfo_width(), lbl.winfo_height(), lbl.winfo_reqwidth()
        check(reqw <= w,
              f"{what}: the text wants {reqw}px but the label is {w}px wide -- a long "
              "verdict is being clipped at the column edge instead of wrapped (#231)")
        check(w >= 0.5 * card_w,
              f"{what}: the label collapsed to {w}px inside a {card_w}px card -- the "
              "wraplength feedback ran it down to the floor; it needs fill='x' so pack "
              "hands it the card's width instead of its own requested width (#231)")
        check(w == short_w,
              f"{what}: the label is {w}px wide for a long verdict but {short_w}px for a "
              "short one -- its width follows its content, so wrapping can feed back on "
              "itself (fill='x' pins it to the card, #231)")
        if natural_width(lbl) > w:
            check(h > short_h,
                  f"{what}: the text needs more than the label's {w}px yet stays "
                  f"{h}px tall, exactly like the short verdict -- it is not wrapping "
                  "onto a second line, it is being cut off (#231)")

    _showerror = ts.messagebox.showerror
    try:
        tk.Misc.configure = configure
        tk.Misc.config = configure
        # A modal showerror from __init__ (unreadable personal_settings.json) would hang
        # a headless run with nobody to dismiss it; neutralized for the build.
        ts.messagebox.showerror = lambda *a, **k: None

        root.geometry("900x860")
        app = ts.SettingsApp(root, first_run=False)
        app._goto_tab("provider.tab")
        settle()

        for provider in ("groq", "soniox"):
            ind = app._indicators[provider]
            deliver(provider, KeyStatus.VALID)      # the one short verdict = the baseline
            short_w, short_h = ind.winfo_width(), ind.winfo_height()
            for status in (KeyStatus.INVALID, KeyStatus.INCONCLUSIVE,
                           KeyStatus.UNREACHABLE):
                deliver(provider, status)
                check_readable(ind, f"{provider} verdict {status.name}", short_w, short_h)

        # The #179 Soniox balance note sits on the same card and had the identical
        # packing fault -- it shipped as a ~110px vertical ribbon. It only appears under
        # a green Soniox verdict, which is why no journey test ever caught it.
        note = app._soniox_balance_note
        deliver("soniox", KeyStatus.VALID)
        card_w = note.master.winfo_width()
        check(note.winfo_reqwidth() <= note.winfo_width(),
              f"soniox balance note: text wants {note.winfo_reqwidth()}px in a "
              f"{note.winfo_width()}px label -- clipped instead of wrapped (#179/#231)")
        check(note.winfo_width() >= 0.5 * card_w,
              f"soniox balance note: collapsed to {note.winfo_width()}px inside a "
              f"{card_w}px card -- it needs fill='x' like the verdict line (#179/#231)")

        # Dead-guard on the cost side: a genuine width change must re-wrap the verdict
        # label, and do so once per settled width -- not never (a label frozen at the
        # floor never re-wraps) and not per <Configure> (the #203 Windows stall).
        ind = app._indicators["groq"]
        deliver("groq", KeyStatus.INCONCLUSIVE)
        before = writes.get(id(ind), 0)
        for w in ("820x860", "900x860"):
            root.geometry(w)
            settle()
        rewraps = writes.get(id(ind), 0) - before
        check(1 <= rewraps <= 6,
              f"{rewraps} wraplength write(s) on the verdict label across two width "
              "changes -- 0 means it never re-wraps (frozen at the floor), many means "
              "it is re-measured per <Configure> instead of once per settled width (#203)")
    finally:
        tk.Misc.configure = _configure
        tk.Misc.config = _configure
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
    test_maximize_restore_with_display()
    test_verdict_wrap_with_display()

    if SHOW:
        _show()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: wrap_length formula, scrollbar auto-hide decision, the visible: line "
          "formatter (full / fail-open / partial), and (with a display) the auto-hide "
          "grid idempotency, wrap-deferral invariant, the #216 maximize->restore "
          "content-vanish guard, and the #231 verdict-line wrap all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
