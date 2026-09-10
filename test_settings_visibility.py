#!/usr/bin/env python3
"""Off-Windows verification of the settings-visibility helpers (#203) and, since #240,
of the settings app's `[SETTINGS]` log lane.

`settings_visibility` holds the six tkinter-free helpers pulled out of
`thoughtborne_settings.py` so they can be checked on plain Python, without a display or
Windows: three #203 cores -- the `wrap_length` formula and the two below -- plus, since
#240, three log-lane helpers described further down, one of which does IO. The two this
driver leads with:

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

A fourth, `test_language_toggle_gate_with_display`, drives the header radios' command path
-- the `lang_var` set plus `_on_lang`, what a click does -- over a CORRUPT
personal_settings.json and asserts the #239 gate end to end: the toggle leaves the
file byte-identical (the silent D-014 persist must not skeleton over hand-written blocks),
and after the file is repaired WHILE THE WINDOW IS OPEN the next toggle persists again --
which is both the dead guard against an over-broad gate and the proof that the corruption
is probed fresh per write rather than latched at load. It runs against a tempdir-patched
`config.SCRIPT_DIR`; unlike the read-only display checks above, this one WRITES.

A fifth, `test_reset_with_display`, drives the #282 reset through the real window: the
Machine Room button exists in the everyday dialog and not in the wizard, follows the
language switch, and sits behind a confirmation that really is the gate -- a declined
one leaves the file byte-identical, and over a CORRUPT personal_settings.json it asks
with the second body, the one that says the hand-written blocks are about to be lost
instead of promising they survive. A confirmed one puts the four managed keys back,
leaves the vocabulary and the .env alone and reaches the restart handshake (stubbed --
unstubbed it would write a real restart signal and destroy the root mid-test). Over
bytes it cannot decode at all -- an ANSI/cp1252 file, B1's case -- it stops before the
confirmation: nothing asked, nothing written, no restart, and (#291) the one error it
raises names the file and the failed READ rather than announcing a failure to save
something the user never asked it to save. And the restart freeze disables the button,
which the rail freeze alone does not reach. Like the #239 lane it WRITES and runs
against a tempdir-patched `config.SCRIPT_DIR`.

A sixth, `test_save_readfail_with_display`, drives the everyday Save through the real
window over files whose bytes cannot be read -- an ANSI/cp1252 `personal_settings.json`,
then an ANSI `.env` -- and asserts the other half of #291: the dialog names the read
failure and which of the two files it is about, no restart follows, and `.env` comes out
byte-identical although it is the file written FIRST (before the fix the freshly typed
key was already on disk while the dialog claimed the save had failed). Two controls
carry as much weight as the failures: the same broken `.env` with both key fields blank
-- a hotkey-only save, which `write_env` never touches the file for -- still goes
through and reaches the restart, and over an unreadable file the keyless confirmation is
not asked first and disappointed afterwards. Like the #239 lane it WRITES and runs
against a tempdir-patched `config.SCRIPT_DIR`.

A seventh, `test_tab_layout_with_display`, guards the sixth tab and the strip it sits in
(#281). Three of its four checks are one-liners against couplings the code can only
state in a comment: the notebook must carry as many pages as `_TAB_KEYS` has entries
(they are `zip`ped, and `zip` drops a surplus on either side silently -- a page or a
label would just vanish), `_tab_canvases` must stay index-parallel to `_tab_frames`
(#180, or the mouse wheel scrolls the wrong page), and the machine-room version line
must carry `config.VERSION_DISPLAY` -- the string the console masthead is handed, so
both name the version out of one fact and one procedure, not two (#302). It is built
empty and filled only out of `render_all`, so a dropped call leaves a blank gap on the
page that no other check notices (where `pyproject.toml` cannot be read the same check
demands the opposite: an empty line, not a guess). The fourth is the width: at the
MINIMUM window size, in both languages, the notebook must get at least the width it
asks for -- clam neither wraps nor scrolls a tab strip that does not fit, it squeezes
every tab and clips each label with no error anywhere. The width part needs a font from
`settings_theme.FAMILY_CHAIN` to mean anything and says so instead of failing when the
box has none.

An eighth, `test_empty_label_sweep_with_display`, generalizes that version line
into a guard for its whole class (#299): once the app is built, no widget that
carries text and is actually placed may be blank, save the four in `ALLOW_EMPTY`
that stay empty until an event fills them. 27 of the roughly 142 text elements are
built empty and filled only out of `render_all`, whose nine render paths can each
drop out on their own and leave a gap that raises nothing -- which is what #281
was. It sweeps the everyday dialog and the wizard (the surface a new user meets
first, built exactly once in the whole ladder before this), each as `__init__`
leaves it and again after a language switch, and carries two dead guards of its
own: the walk must reach at least as many text widgets as the language registry
holds, and every `ALLOW_EMPTY` entry must still name something that really is
blank. The tab labels and the window title are checked with it, since neither
lives in the widget tree the walk can see.

Since #240 the same module also owns the settings app's whole `[SETTINGS]` log lane, and
this driver owns its checks: `format_settings_line` (asserted BYTE-IDENTICAL to the
literal f-strings the startup and focus-existing lines used to be, so no consumer -- the
sandbox needle, a human grep -- sees a changed line), `format_error_block` (lead line
plus indented traceback, with a never-raise sweep, since it runs inside the crash path)
and the guarded `append_log_line` sink. Three AST guards on `thoughtborne_settings.py`
pin what those helpers are worth in the app: no `open(config.LOG_FILE ...)` literal is
left, `__init__` still hangs the handler on the root, and `main()`'s body is still one
`try` that logs and re-raises. Two more display-gated lanes drive the real thing: a
deliberate exception in an `after_idle` callback and one before `mainloop()`. Like the
#239 lane they WRITE, so both patch `config.LOG_FILE` to a tempdir and assert the
checkout's own `thoughtborne.log` came out byte-identical.

    python3 test_settings_visibility.py          # verify, exit non-zero on any violation
    python3 test_settings_visibility.py --show   # also print sample visible: lines
"""
import ast
import io
import json
import sys
import tempfile
from pathlib import Path

import settings_visibility as sv

APP_SRC = Path(__file__).resolve().parent / "thoughtborne_settings.py"

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


def test_format_settings_line_parity():
    # The #240 helper must render the two lines it replaced BYTE-IDENTICALLY: the
    # sandbox harness greps for `[SETTINGS] visible:`, the maintainer greps for the
    # others, and a silently reshaped line would break both without failing anything.
    # So build each line twice -- once through the helper, once through the literal
    # f-string that stood at the call site before #240 -- and compare.
    stamp = "2026-09-06 12:00:00"
    parts = ["spawn->entry=0.31s", "import=0.12s", "tk.Tk=0.05s", "size=0.01s",
             "construct=0.44s", "first-map=0.02s", "total=0.95s"]
    mode = "settings"
    legacy_startup = f"{stamp} [SETTINGS] startup: {' '.join(parts)} mode={mode}\n"
    helper_startup = sv.format_settings_line(stamp, "startup",
                                             f"{' '.join(parts)} mode={mode}")
    check(helper_startup == legacy_startup,
          f"the startup line changed shape: helper gave {helper_startup!r}, "
          f"the pre-#240 literal gave {legacy_startup!r}")

    outcome = "focused"
    legacy_focus = f"{stamp} [SETTINGS] focus-existing: {outcome}\n"
    check(sv.format_settings_line(stamp, "focus-existing", outcome) == legacy_focus,
          "the focus-existing line changed shape: helper gave "
          f"{sv.format_settings_line(stamp, 'focus-existing', outcome)!r}, the pre-#240 "
          f"literal gave {legacy_focus!r}")

    # A record is ONE greppable line: a body carrying newlines (an exception message,
    # an import warning quoting one) must not continue below as timestamp-less text
    # that reads like a record of its own.
    multi = sv.format_settings_line(stamp, "import-warning",
                                    "broken .env\nsecond line\r\nthird")
    check(multi.count("\n") == 1 and multi.endswith("\n"),
          f"a multi-line body was not flattened to one record: {multi!r}")
    check("\r" not in multi, f"a carriage return survived into the log line: {multi!r}")
    for token in ("broken", ".env", "second", "third"):
        check(token in multi, f"flattening dropped {token!r}: {multi!r}")


def test_format_error_block():
    # The crash record itself (#240): a lead line in the existing [SETTINGS] shape that
    # stands alone even if log rotation tears the block apart, then the traceback with
    # every continuation line indented -- so no continuation can be mistaken for a
    # record of its own, and a timestamp grep stays clean.
    def _raiser():
        raise ValueError("Grüße from the callback\nwith a second line")

    try:
        _raiser()
    except ValueError:
        block = sv.format_error_block("2026-09-06 12:00:00", "callback", *sys.exc_info())

    lines = block.split("\n")[:-1]          # the block ends with exactly one newline
    check(block.endswith("\n") and not block.endswith("\n\n"),
          f"the block must end with exactly one newline: {block[-40:]!r}")
    check("\r" not in block, "no carriage returns in the log block")
    check(lines[0].startswith("2026-09-06 12:00:00 [SETTINGS] error: callback: "
                              "ValueError: "),
          f"lead line has the wrong shape: {lines[0]!r}")
    check("Grüße" in lines[0], f"the message must survive into the lead: {lines[0]!r}")
    check(len(lines) > 1, "no traceback lines were rendered")
    for ln in lines[1:]:
        check(ln.startswith("    "),
              f"a traceback continuation line is not indented: {ln!r}")
    check(any("_raiser" in ln for ln in lines[1:]),
          f"the raising function is missing from the traceback: {block!r}")
    check(any("Traceback (most recent call last)" in ln for ln in lines[1:]),
          f"no traceback header in the block: {block!r}")
    # The full multi-line message is still there, in the traceback -- the lead only
    # flattens its copy of it.
    check("with a second line" in block,
          f"the message's second line is missing from the block: {block!r}")


def test_format_error_block_never_raises():
    # It runs inside the crash path, where a second exception would be the end of the
    # story -- so it must return a string for anything at all, the fallback branch
    # included.
    class Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("__str__ refuses")

    cases = [
        ("no exception at all", (None, None, None)),
        ("not an exception", ("boom", object(), 42)),
        ("type without traceback", (ValueError, ValueError("plain"), None)),
    ]
    try:
        raise Unprintable()
    except Unprintable:
        cases.append(("__str__ raises", sys.exc_info()))

    for label, (exc, val, tb) in cases:
        try:
            out = sv.format_error_block("2026-09-06 12:00:00", "callback", exc, val, tb)
        except Exception as e:
            failures.append(f"format_error_block raised on {label}: "
                            f"{type(e).__name__}: {e}")
            continue
        check(isinstance(out, str) and out.endswith("\n"),
              f"{label}: expected a newline-terminated string, got {out!r}")
        check(out.startswith("2026-09-06 12:00:00 [SETTINGS] error: callback: "),
              f"{label}: the lead must keep its shape: {out.splitlines()[:1]}")
    # The fallback branch really is reachable and really is taken (the tb=42 case).
    fallback = sv.format_error_block("2026-09-06 12:00:00", "main", "boom", object(), 42)
    check("unformattable exception" in fallback,
          f"a hopeless input should render the fallback line, got {fallback!r}")


def test_append_log_line():
    # The one sink every settings-side log write goes through: it appends, and it
    # NEVER raises -- instrumentation must not cost the app, least of all in the crash
    # lane. It takes the path as a parameter (no config import), which is what lets
    # this run against a tempdir.
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        log = tmp / "thoughtborne.log"
        check(sv.append_log_line(log, "first\n") is True, "a fresh file should be written")
        check(sv.append_log_line(log, "zweite Grüße\n") is True, "a second write appends")
        check(log.read_text(encoding="utf-8") == "first\nzweite Grüße\n",
              f"appends must accumulate utf-8-correctly: "
              f"{log.read_text(encoding='utf-8')!r}")

        # A lone surrogate (what a mangled Tcl message can carry) costs a character,
        # never the line -- that is what errors='backslashreplace' buys.
        check(sv.append_log_line(log, "surrogate \ud800 here\n") is True,
              "an unencodable character must not cost the line")
        check("surrogate" in log.read_text(encoding="utf-8"),
              "the surrogate line is missing from the log")

        # Every plausible fault returns False instead of raising.
        for label, path in (("missing parent directory", tmp / "nope" / "t.log"),
                            ("parent is a file", log / "t.log"),
                            ("path is a directory", tmp)):
            try:
                got = sv.append_log_line(path, "x\n")
            except Exception as e:
                failures.append(f"append_log_line raised on {label}: "
                                f"{type(e).__name__}: {e}")
                continue
            check(got is False, f"{label}: expected False, got {got!r}")


def _app_tree(prefix):
    """thoughtborne_settings.py as a syntax tree, or None after recording a failure.
    The app imports tkinter at module level and cannot be imported from this ladder, so
    its call sites are checked on the source -- as a tree rather than as text, the idiom
    of test_settings_io.py's guards, so reflowing a call proves nothing while a real
    regression still goes red."""
    try:
        return ast.parse(APP_SRC.read_text(encoding="utf-8"))
    except Exception as e:
        failures.append(f"{prefix}: could not parse thoughtborne_settings.py: "
                        f"{type(e).__name__}: {e}")
        return None


def _method(tree, class_name, method_name, prefix):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == method_name:
                    return n
    failures.append(f"{prefix}: {class_name}.{method_name} not found -- it was renamed "
                    f"and this guard no longer guards anything")
    return None


def _function(tree, name, prefix):
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    failures.append(f"{prefix}: module-level {name}() not found -- this guard no longer "
                    f"guards anything")
    return None


def _calls_to(node, name):
    """Every `<something>.name(...)` call inside one syntax tree."""
    return [c for c in ast.walk(node) if isinstance(c, ast.Call)
            and getattr(c.func, "attr", None) == name]


def test_log_sink_source_guards():
    # What the helpers are worth depends on the app actually using them, and the GUI is
    # hands-on only -- so these three properties are pinned statically (#240).
    tree = _app_tree("log-sink guards")
    if tree is None:
        return

    # (1) Not a fourth open(config.LOG_FILE ...) literal -- none at all. Every
    # settings-side write goes through the guarded sink, which is also the only reason
    # the ladder can point the writes at a tempdir.
    stray = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            for arg in node.args:
                if any(isinstance(s, ast.Attribute) and s.attr == "LOG_FILE"
                       for s in ast.walk(arg)):
                    stray.append(getattr(node, "lineno", "?"))
    check(not stray,
          f"thoughtborne_settings.py still opens config.LOG_FILE directly (line(s) "
          f"{stray}) -- every settings-side log write must go through "
          "settings_visibility.append_log_line (#240)")

    # (2) The handler is hung on the Tk root in __init__, and the counter it reads is
    # initialized BEFORE that -- wiring first would leave a window in which the very
    # first callback exception dies on an AttributeError inside the crash handler.
    init = _method(tree, "SettingsApp", "__init__", "log-sink guards")
    if init is not None:
        wiring = [n for n in ast.walk(init) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Attribute)
                          and t.attr == "report_callback_exception"
                          for t in n.targets)]
        check(wiring,
              "SettingsApp.__init__ no longer assigns root.report_callback_exception -- "
              "callback exceptions go back to Tk's stderr default, i.e. DEVNULL (#240)")
        counter = [n for n in ast.walk(init) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Attribute) and t.attr == "_error_log_count"
                           for t in n.targets)]
        check(counter, "SettingsApp.__init__ never initializes _error_log_count -- the "
                       "flood cap the handler reads would raise inside the crash path")
        if wiring and counter:
            check(min(n.lineno for n in counter) < min(n.lineno for n in wiring),
                  "_error_log_count is initialized AFTER the handler is wired up -- a "
                  "callback exception in between would fault inside the handler (#240)")
    handler = _method(tree, "SettingsApp", "_report_callback_exception",
                      "log-sink guards")
    if handler is not None:
        check(_calls_to(handler, "format_error_block")
              and _calls_to(handler, "append_log_line"),
              "SettingsApp._report_callback_exception no longer writes a block through "
              "the sink -- the callback lane is silent again (#240)")

    # (3) main() is ONE try that logs and re-raises. A statement drifting out of the
    # wrap is a hole in exactly the lane #240 closed, so the guard pins the shape, not
    # just the presence of a try.
    fn = _function(tree, "main", "log-sink guards")
    if fn is None:
        return
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                       # a docstring may lead, nothing else
    check(len(body) == 1 and isinstance(body[0], ast.Try),
          f"main()'s body is not a single try/except ({len(body)} top-level "
          "statement(s)) -- whatever sits outside the wrap fails silently again (#240)")
    if not (len(body) == 1 and isinstance(body[0], ast.Try)):
        return
    wrap = body[0]
    broad = [h for h in wrap.handlers
             if isinstance(h.type, ast.Name) and h.type.id == "Exception"]
    check(broad, "main()'s wrap does not catch Exception -- and it must stay Exception, "
                 "not BaseException: the focus-existing sys.exit(0) is a SystemExit and "
                 "must pass through without an error: line (#240)")
    for h in broad:
        check(_calls_to(h, "format_error_block") and _calls_to(h, "append_log_line"),
              "main()'s except handler does not write the error block through the sink "
              "-- a failure before mainloop() is a silent no-op again (#240)")
        check(any(isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(h)),
              "main()'s except handler does not end in a bare `raise` -- re-raising is "
              "what keeps the console start's stderr traceback and non-zero exit (#240)")

    # (4) The import-warning replay: config collects those instead of logging them
    # (#206/#238), and its logger-based replay would land on stderr in this process.
    replays = [n for n in ast.walk(fn) if isinstance(n, ast.For)
               and isinstance(n.iter, ast.Attribute) and n.iter.attr == "IMPORT_WARNINGS"]
    check(replays,
          "main() no longer replays config.IMPORT_WARNINGS -- a broken .env or "
          "personal_settings.json leaves no trace in the log of the very window that "
          "repairs it (#240)")
    for loop in replays:
        check(_calls_to(loop, "append_log_line"),
              "the IMPORT_WARNINGS replay does not go through append_log_line -- "
              "config.replay_import_warnings() would end on stderr here, i.e. DEVNULL")


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


def test_tab_layout_with_display():
    # The six-tab layout (#281). Two index-parallel lists that must stay in step, and
    # the tab strip fitting the MINIMUM window width -- clam clips tab labels silently
    # rather than wrapping or scrolling them, so nothing but a measurement catches a
    # strip that outgrew its window. Both languages, since the labels differ.
    try:
        import tkinter as tk
    except Exception:
        print("  (skipped tab-layout check: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped tab-layout check: no display)")
        return

    try:
        import config
        import settings_theme
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped tab-layout check: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    _showerror = ts.messagebox.showerror
    try:
        # Same modal neutralization as the storm lane: __init__ pops a showerror over
        # an unreadable personal_settings.json, which nothing dismisses headless.
        ts.messagebox.showerror = lambda *a, **k: None
        ts._size_window(root)          # the real base geometry AND the real minsize
        app = ts.SettingsApp(root, first_run=False)
        root.update()

        check(len(app.notebook.tabs()) == len(ts._TAB_KEYS),
              f"{len(app.notebook.tabs())} notebook pages vs {len(ts._TAB_KEYS)} "
              "_TAB_KEYS -- the zip() in _build_ui drops the surplus silently, so a "
              "page or its label just disappears")
        check(len(app._tab_canvases) == len(app._tab_frames),
              f"{len(app._tab_canvases)} scroll canvases vs {len(app._tab_frames)} "
              "tab frames -- _tab_canvases is no longer index-parallel (#180) and the "
              "mouse wheel would scroll the wrong page")

        # The tab's headline promise: it names the running version in the very string
        # the console masthead is handed (#302). Not `config.VERSION`, which is how
        # this check read for #281 -- the bare number is a substring of the display
        # form, so that spelling stayed green whichever of the two the app rendered.
        # The label is BUILT empty and filled only by _render_machine_page out of
        # render_all, so a dropped call leaves a blank gap on the page that every other
        # check here still passes. Both worlds are asserted -- an unreadable
        # pyproject.toml (config.VERSION_DISPLAY None) must show nothing rather than a
        # guess.
        vtext = app.machine_version_lbl.cget("text")
        if config.VERSION_DISPLAY:
            check(config.VERSION_DISPLAY in vtext,
                  f"the machine-room version line reads {vtext!r} and does not carry "
                  f"config.VERSION_DISPLAY ({config.VERSION_DISPLAY!r}) -- either the "
                  "window no longer carries what the masthead is handed, or render_all "
                  "no longer fills the line and the tab shows a blank gap where the "
                  "version belongs (#281, #302)")
        else:
            check(vtext == "",
                  f"config.VERSION_DISPLAY is None (pyproject.toml unreadable) but the "
                  f"version line reads {vtext!r} -- an unreadable version must show "
                  "nothing")

        family = settings_theme._pick_family(root)
        if family not in settings_theme.FAMILY_CHAIN:
            print(f"  (skipped tab-strip width check: Tk fell back to {family!r}, "
                  f"none of {settings_theme.FAMILY_CHAIN} is installed -- a width "
                  "measured in it says nothing about the target system)")
            return
        min_w, min_h = root.wm_minsize()
        for lang in ("en", "de"):
            app.lang = lang
            app.lang_var.set(lang)
            app.render_all()           # no _on_lang: this lane must not write files
            root.geometry(f"{min_w}x{min_h}")
            root.update()
            nb = app.notebook
            check(nb.winfo_width() >= nb.winfo_reqwidth(),
                  f"[{lang}] at the minimum window width ({min_w}px) the notebook is "
                  f"{nb.winfo_width()}px wide but asks for {nb.winfo_reqwidth()}px -- "
                  "clam squeezes the tabs and clips every label, silently (#281)")
            if SHOW:
                print(f"    tab strip [{lang}]: {nb.winfo_reqwidth()}px asked, "
                      f"{nb.winfo_width()}px given at {min_w}x{min_h}")
    finally:
        ts.messagebox.showerror = _showerror
        try:
            root.destroy()
        except Exception:
            pass


# The display elements that are legitimately blank in the resting state, each with the
# reason it may be. The table is the point of the sweep below rather than its price:
# something that stays empty until an event fills it has to say so once, here, where the
# next reader finds it -- otherwise it is indistinguishable from a gap on the page.
ALLOW_EMPTY = {
    "_indicators['groq']":   "key verdict: blank until 'Test key' has run",
    "_indicators['soniox']": "key verdict: blank until 'Test key' has run",
    "_soniox_balance_note":  "#179 balance hint: only under a green Soniox verdict",
    "capture_lbl":           "hotkey-capture feedback: only while/after a capture",
}


def _widget_names(app):
    """Tk path -> the app attribute a widget is reachable under, so a failure can name
    `machine_version_lbl` instead of `.!notebook.!frame4.!label7` -- for the quarter
    of the tree that hangs on an attribute; the rest is named by Tk path and tab
    page, which still finds it in seconds. The five bookkeeping
    registries are skipped on purpose: they hold the SAME widgets a second time and,
    being assigned after the speaking single attributes, would otherwise overwrite every
    name with an anonymous `_wrap_labels[10]` (vars() keeps insertion order)."""
    import tkinter as tk
    bookkeeping = {"_wrap_labels", "_text_widgets", "_link_widgets",
                   "_tab_canvases", "_tab_frames"}
    names = {}
    for attr, value in vars(app).items():
        if attr in bookkeeping:
            continue
        if isinstance(value, tk.Misc):
            names[str(value)] = attr
        elif isinstance(value, dict):
            for key, widget in value.items():
                if isinstance(widget, tk.Misc):
                    names[str(widget)] = f"{attr}[{key!r}]"
        elif isinstance(value, (list, tuple)):
            for i, widget in enumerate(value):
                if isinstance(widget, tk.Misc):
                    names[str(widget)] = f"{attr}[{i}]"
    return names


def _sweep_empty_text(app, root, tab_map, where):
    """Walk one built window and record a failure per PLACED text-bearing widget that
    is blank and that ALLOW_EMPTY does not explain. Returns (text widgets seen, the
    ALLOW_EMPTY names that really were blank); the caller uses both as dead guards on
    the sweep itself."""
    import tkinter as tk
    names = _widget_names(app)
    seen = 0
    blanks = []

    def walk(widget, tab):
        nonlocal seen
        tab = tab_map.get(str(widget), tab)
        try:
            carries_text = "text" in widget.keys()
        except tk.TclError:
            carries_text = False
        if carries_text:
            seen += 1
            try:
                text = str(widget.cget("text"))
                # Unplaced is not on the page: a blank there is invisible and harmless
                # (warn_strip's resting state), and only pack/grid/place put it up.
                placed = widget.winfo_manager() != ""
            except tk.TclError:
                text, placed = "", False
            if placed and text.strip() == "":
                blanks.append((names.get(str(widget)), tab, widget))
        for child in widget.winfo_children():
            walk(child, tab)

    walk(root, None)

    allowed = []
    for name, tab, widget in blanks:
        if name in ALLOW_EMPTY:
            allowed.append(name)
            continue
        ident = repr(name) if name else (
            f"an unnamed {widget.winfo_class()} at {str(widget)!r}")
        page = f"on the {tab} page" if tab else "outside the tab pages"
        failures.append(
            f"[{where}] {ident} stands {page} carrying no text -- either the render "
            "path that fills it out of render_all is gone (the #281 fault: a blank gap "
            "on the page, no exception raised, nothing else red), or it is meant to "
            "stay blank until an event fills it and belongs in ALLOW_EMPTY with its "
            "reason")
    return seen, allowed


def test_empty_label_sweep_with_display():
    # The version-line check above, generalized to its whole class (#299). 27 of the
    # app's ~142 text elements are built empty and filled only out of render_all, and
    # before this lane exactly one of them was asserted. Each of the nine render paths
    # render_all calls can drop out on its own, and every one leaves the same signature
    # #281 had: a blank gap on the page, no exception, a green ladder. So after the app
    # is built, no PLACED widget carrying text may be blank -- except the ALLOW_EMPTY
    # entries, which stay blank until an event fills them.
    #
    # The sweep says nothing about wording, position or size; it only asks whether an
    # element standing on the page says anything at all. That is what lets it be this
    # broad without going falsely red -- and what it cannot see is a filled but WRONG
    # text, which is check_string_keys' half in test_settings_io.py.
    #
    # Two windows carry the four passes: the everyday dialog and the wizard -- the
    # surface a new user meets first, built exactly once in the whole ladder before
    # this and looked at only for the absence of the reset button. Each is swept as
    # __init__ leaves it (English, D-015) and again after a language switch through
    # render_all, the re-render path #281 broke. Read-only: no _on_lang, so nothing is
    # written, and config.SCRIPT_DIR points at a tempdir carrying a fixture .env, so
    # the surface cannot depend on the maintainer's own keys or settings file.
    try:
        import tkinter as tk
    except Exception:
        print("  (skipped empty-label sweep: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped empty-label sweep: no display)")
        return

    try:
        import config
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped empty-label sweep: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    _showerror = ts.messagebox.showerror
    _script_dir, _log_file = config.SCRIPT_DIR, config.LOG_FILE
    win = root                  # the display probe becomes the first mode's window
    matched = set()
    try:
        ts.messagebox.showerror = lambda *a, **k: None
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            config.SCRIPT_DIR = tmp
            config.LOG_FILE = tmp / "thoughtborne.log"
            (tmp / ".env").write_text(
                "GROQ_API_KEY=gsk_fixture\nSONIOX_API_KEY=fixture\n", encoding="utf-8")
            for mode in ("settings", "firstrun"):
                if win is None:
                    win = tk.Tk()
                ts._size_window(win)
                app = ts.SettingsApp(win, first_run=(mode == "firstrun"))
                win.update()
                tab_map = {str(frame): key
                           for frame, key in zip(app._tab_frames, ts._TAB_KEYS)}
                for lang in ("en", "de"):
                    if app.lang != lang:
                        app.lang = lang
                        app.lang_var.set(lang)
                        app.render_all()   # no _on_lang: this lane must not write files
                        win.update()
                    where = f"{mode}/{lang}"
                    seen, allowed = _sweep_empty_text(app, win, tab_map, where)
                    matched.update(allowed)
                    # Dead guard on the sweep: every registered widget is in the tree
                    # and carries text, so a walk that stopped walking -- and would
                    # pass vacuously -- cannot reach the registry's own count.
                    check(seen >= len(app._text_widgets),
                          f"[{where}] the sweep found {seen} text widgets, fewer than "
                          f"the {len(app._text_widgets)} the language registry alone "
                          "holds -- the walk no longer reaches the whole window")
                    # The tab labels live outside the widget tree, on the notebook,
                    # and render_all fills them from _TAB_KEYS like any other string.
                    for i in range(len(app.notebook.tabs())):
                        check(str(app.notebook.tab(i, "text")).strip() != "",
                              f"[{where}] notebook page {i} carries no tab label -- "
                              "the same fill going missing where the walk cannot see")
                    check(str(win.title()).strip() != "",
                          f"[{where}] the window carries no title -- D-009's "
                          "focus-existing remedy matches an open window on it")
                    if SHOW:
                        print(f"    {where}: {seen} text widgets, "
                              f"{len(allowed)} explained blanks")
                win.destroy()
                win = None
        # ... and the allowlist stays honest: an entry naming nothing blank any more
        # was renamed away or now fills itself, and silently licenses a future gap.
        stale = sorted(set(ALLOW_EMPTY) - matched)
        check(not stale,
              f"ALLOW_EMPTY still excuses {stale}, but no pass found those elements "
              "blank -- they were renamed, removed or now fill themselves, so the "
              "entry licenses nothing and belongs out of the table")
    finally:
        config.SCRIPT_DIR, config.LOG_FILE = _script_dir, _log_file
        ts.messagebox.showerror = _showerror
        if win is not None:
            try:
                win.destroy()
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
            # Maximize stand-in: a window far wider than the 800px this lane restores
            # to -- its own pinned geometry, not the app's size -- past the #216
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


def test_language_toggle_gate_with_display():
    # Only runs where a display exists (Xvfb on a CI/dev box); the normal WSL case skips
    # cleanly. The #239 gate end to end, through the REAL radios: test_settings_io.py
    # proves settings_io.write_ui_language protects a corrupt file and that
    # _persist_language calls it, but only the built app proves the two meet -- that a
    # click on the header radio really ends in the gated write, through _on_lang and
    # render_all, in the mode the user is actually in.
    #
    # This check WRITES, unlike every read-only display check above, so it must run
    # against a tempdir: config.SCRIPT_DIR is patched BEFORE the app is built and
    # restored in finally. Without that patch the app would load -- and this test would
    # overwrite -- the checkout's own personal_settings.json, which is the maintainer's
    # live file. thoughtborne_settings reads `config.SCRIPT_DIR` at call time (never
    # `from config import SCRIPT_DIR`), so patching the module attribute reaches both
    # the load in __init__ and the write in _persist_language.
    try:
        import tkinter as tk
    except Exception:
        print("  (skipped language-toggle-gate check: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped language-toggle-gate check: no display)")
        return

    try:
        import config
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped language-toggle-gate check: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    _showerror = ts.messagebox.showerror
    _script_dir = config.SCRIPT_DIR
    try:
        # A modal showerror from __init__ would hang a headless run with nobody to
        # dismiss it (the load-error path; the corrupt fixture below takes the strip
        # path, but the idiom costs nothing and keeps the test robust).
        ts.messagebox.showerror = lambda *a, **k: None

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            config.SCRIPT_DIR = tmp
            ps = tmp / "personal_settings.json"
            # Truncated but UTF-8-valid, carrying the two hand-written blocks that have
            # no GUI and exist only because someone typed them.
            ps.write_text('{\n  "vocabulary": {"terms": ["Grüße"]},\n'
                          '  "soniox_endpointing": {\n', encoding="utf-8")
            before = ps.read_bytes()

            root.geometry("900x860")
            app = ts.SettingsApp(root, first_run=False)
            root.update()
            check(app.lang == "en", f"the app should load English (D-015), got {app.lang}")
            check(app.warn_strip.winfo_manager() == "pack",
                  "a corrupt personal_settings.json did not raise the warning strip -- "
                  "the user gets no hint that the language will not be remembered")

            # The real toggle: what the header radio's command does.
            app.lang_var.set("de")
            app._on_lang()
            root.update()
            check(app.lang == "de", "the toggle did not switch the display language")
            check(ps.read_bytes() == before,
                  "a DE/EN toggle over a CORRUPT personal_settings.json rewrote the file "
                  "-- the silent D-014 persist skeletoned it and destroyed the "
                  "hand-written vocabulary / soniox_endpointing (#239)")
            check(sorted(p.name for p in tmp.iterdir()) == ["personal_settings.json"],
                  f"the gated toggle left files behind: "
                  f"{sorted(p.name for p in tmp.iterdir())}")

            # Repair the file WHILE THE WINDOW IS OPEN. The next toggle must persist
            # again: the dead guard against a gate that just blocks everything, and the
            # point of probing per write instead of latching a load-time flag.
            ps.write_text(json.dumps({"vocabulary": {"terms": ["keepme"]}},
                                     indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")
            app.lang_var.set("en")
            app._on_lang()
            root.update()
            data = json.loads(ps.read_text(encoding="utf-8"))
            check(data.get("ui", {}).get("language") == "en",
                  f"after the file was repaired the toggle did not persist the language "
                  f"({data.get('ui')}) -- the gate must re-probe per write, not latch")
            check(data.get("vocabulary", {}).get("terms") == ["keepme"],
                  "the persisting toggle clobbered the repaired file's vocabulary")
    finally:
        config.SCRIPT_DIR = _script_dir
        ts.messagebox.showerror = _showerror
        try:
            root.destroy()
        except Exception:
            pass


def test_reset_with_display():
    # Only runs where a display exists (Xvfb on a CI/dev box); the normal WSL case skips
    # cleanly. The #282 reset through the REAL app: test_settings_io.py proves the write
    # contract and pins the call site on the syntax tree, but only the built window
    # shows that the button exists in the mode it is meant for, that the confirmation is
    # really the gate in front of it, and that the dialog tells the truth over a corrupt
    # file -- the promise "your vocabulary stays" is the one the app cannot keep there.
    #
    # WRITES, like the #239 lane above, so config.SCRIPT_DIR is patched to a tempdir
    # BEFORE the app is built and restored in finally; otherwise this check would reset
    # the maintainer's own personal_settings.json.
    try:
        import tkinter as tk
    except Exception:
        print("  (skipped reset check: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped reset check: no display)")
        return
    try:
        import config
        import settings_strings as sstr
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped reset check: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    _showerror = ts.messagebox.showerror
    _askyesno = ts.messagebox.askyesno
    _script_dir = config.SCRIPT_DIR
    root2 = None
    try:
        # Both are modal and would hang a headless run with nobody to dismiss them.
        # showerror is recorded rather than dropped: the undecodable lane below has to
        # read the title it comes up under (#291).
        errors = []     # (title, body) of every error dialog raised in this lane
        ts.messagebox.showerror = lambda title, body, *a, **k: errors.append((title, body))
        shown = []      # the body text each confirmation was asked with

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            config.SCRIPT_DIR = tmp
            ps = tmp / "personal_settings.json"
            ps.write_text(json.dumps(
                {"vocabulary": {"terms": ["Grüße"]},
                 "hotkeys": {"start_recording": "ctrl+alt+p"},
                 "defaults": {"api": "groq"}}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            env = tmp / ".env"
            env.write_text("GROQ_API_KEY=gsk_secret\n", encoding="utf-8")
            env_before = env.read_bytes()

            root.geometry("900x860")
            app = ts.SettingsApp(root, first_run=False)
            root.update()
            check(app.reset_btn is not None,
                  "the everyday settings dialog has no reset button -- #282 builds it "
                  "on the Machine Room tab")
            if app.reset_btn is None:
                return
            check(app.reset_btn.cget("text") == sstr.t("btn.reset_defaults", "en"),
                  f"the reset button does not carry btn.reset_defaults: "
                  f"{app.reset_btn.cget('text')!r}")
            # It re-renders with the window's language, i.e. it really hangs in the
            # text registry rather than carrying a text set once at build time.
            app.lang = "de"
            app.render_all()
            root.update()
            check(app.reset_btn.cget("text") == sstr.t("btn.reset_defaults", "de"),
                  "the reset button did not follow the language switch -- it is not "
                  "registered for re-render")
            app.lang = "en"
            app.render_all()
            root.update()

            # Declining is the gate: the file must come out byte-identical, and the
            # dialog must have asked with the ordinary body (a readable file keeps
            # every hand-written block, which is what that text promises).
            ts.messagebox.askyesno = lambda title, msg, **k: (shown.append(msg), False)[1]
            before = ps.read_bytes()
            app._reset_to_defaults()
            check(ps.read_bytes() == before,
                  "a DECLINED reset still rewrote personal_settings.json -- the "
                  "confirmation is the only thing between a mis-click and the file")
            check(shown[-1:] == [sstr.t("dlg.reset.body", "en")],
                  f"the confirmation over a healthy file used the wrong body: "
                  f"{shown[-1:]!r}")

            # Over a CORRUPT file the same reset takes D-002's warn-then-overwrite
            # branch, so the hand-written blocks really are lost -- and the dialog has
            # to say that instead of promising they survive. The way out stays open;
            # it just may not lie in the moment the user clicks.
            ps.write_text('{\n  "vocabulary": {"terms": ["Grüße"]},\n', encoding="utf-8")
            broken = ps.read_bytes()
            app._reset_to_defaults()
            check(ps.read_bytes() == broken,
                  "a declined reset over a corrupt file still rewrote it")
            check(shown[-1:] == [sstr.t("dlg.reset.body_corrupt", "en")],
                  f"the confirmation over a CORRUPT personal_settings.json promised "
                  f"the hand-written blocks would survive, which is exactly the case "
                  f"where they do not: {shown[-1:]!r}")
            ps.write_text(json.dumps(
                {"vocabulary": {"terms": ["Grüße"]},
                 "hotkeys": {"start_recording": "ctrl+alt+p"},
                 "defaults": {"api": "groq"}}, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")

            # Confirming: the four managed facts land, the hand-written vocabulary and
            # the .env do not move, and the restart handshake is really triggered. The
            # handshake itself MUST be stubbed -- unstubbed it writes a real restart
            # signal, shells out on Windows and destroys the root mid-test.
            calls = []
            ts.messagebox.askyesno = lambda title, msg, **k: (shown.append(msg), True)[1]
            app._restart_and_relaunch = lambda: calls.append(True)
            app._reset_to_defaults()
            data = json.loads(ps.read_text(encoding="utf-8"))
            check(not [k for k in data.get("hotkeys", {}) if not k.startswith("_")]
                  and "api" not in data.get("defaults", {})
                  and data.get("ui", {}).get("language") == "en"
                  and data.get("push_to_talk", {}).get("enabled") is False,
                  f"the confirmed reset did not put all four managed keys back at "
                  f"their shipped value: {data}")
            check(data.get("vocabulary", {}).get("terms") == ["Grüße"],
                  f"the reset destroyed the hand-written vocabulary: {data.get('vocabulary')}")
            check(env.read_bytes() == env_before,
                  "the reset wrote the .env -- the API keys are the user's data and "
                  "the reset never goes near the code that writes them (D-020)")
            check(calls == [True],
                  "the confirmed reset did not trigger the restart -- pickup is "
                  "start-based, so the defaults would sit on disk unapplied (#271)")

            # Bytes that cannot be DECODED at all -- the real case is an ANSI/cp1252
            # personal_settings.json whose German vocabulary is intact, just in the
            # wrong encoding (D-002's B1) -- abort the reset before the confirmation:
            # the write would fail on the same read, so nothing is asked, nothing is
            # written and no restart is triggered. Only the error dialog appears
            # (stubbed out above, like every modal in this lane).
            ps.write_bytes('{"vocabulary": {"terms": ["Grüße"]}}\n'.encode("cp1252"))
            undecodable = ps.read_bytes()
            asked, restarts, dialogs = len(shown), len(calls), len(errors)
            app._reset_to_defaults()
            check(ps.read_bytes() == undecodable,
                  "a reset over an undecodable personal_settings.json rewrote it -- "
                  "the read that guards the write failed, so the vocabulary in there "
                  "is still rescuable and must not be skeletoned over (B1)")
            check(len(shown) == asked,
                  "the reset asked for confirmation over a file it cannot read -- the "
                  "question would promise an outcome the write cannot deliver")
            check(len(calls) == restarts,
                  "the reset restarted Thoughtborne after failing to read the file -- "
                  "nothing was written, so there is nothing for a start to pick up")
            # ... and what it says is the #291 half of this issue: the failure is a
            # READ that failed, named as one and naming the file, not "Saving failed"
            # over an action the user never asked to save.
            check(len(errors) == dialogs + 1,
                  f"the reset over an unreadable file raised {len(errors) - dialogs} "
                  f"error dialogs, expected exactly one")
            check(errors[-1:] and errors[-1][0] == sstr.t("dlg.readfail.title", "en"),
                  f"the reset reported a file it cannot READ under the wrong title -- "
                  f"after a click on Reset, 'Saving failed' names neither the failure "
                  f"nor the action (#291): {errors[-1:]!r}")
            check(errors[-1:] and "personal_settings.json" in errors[-1][1],
                  f"the dialog does not name the file that could not be read: "
                  f"{errors[-1:]!r}")

            # The restart freeze reaches the button. It sits on a tab, so the rail
            # freeze does not cover it, and a second click during the responsive wait
            # would start a second handshake (#282).
            app._set_rail_waiting()
            root.update()
            check(str(app.reset_btn.cget("state")) == "disabled",
                  "the reset button stays clickable during the restart wait -- a "
                  "second click would write a second restart signal")

            # The wizard has nothing to reset, and it is where an unsaved API key sits
            # in a field the reset would not write, so the control is not built there.
            root.destroy()
            root2 = tk.Tk()
            root2.geometry("900x860")
            app2 = ts.SettingsApp(root2, first_run=True)
            root2.update()
            check(app2.reset_btn is None,
                  "the first-run wizard built the reset button -- a first run has "
                  "nothing to reset, and the restart would drop the key being typed")
    finally:
        config.SCRIPT_DIR = _script_dir
        ts.messagebox.showerror = _showerror
        ts.messagebox.askyesno = _askyesno
        for r in (root, root2):
            try:
                if r is not None:
                    r.destroy()
            except Exception:
                pass


def test_save_readfail_with_display():
    # Only runs where a display exists (Xvfb on a CI/dev box); the normal WSL case skips
    # cleanly. The save half of #291 through the REAL app: test_settings_io.py proves
    # what the pre-flight decides and pins its call site on the syntax tree, but only
    # the built window shows that the two meet -- that a click on Save over bytes the
    # app cannot read really ends in the read-failure dialog, naming the file, and that
    # .env is still untouched when it does. That last part is the substance of the fix:
    # .env is written FIRST, so before #291 an unreadable personal_settings.json left
    # the newly typed key on disk behind a dialog claiming the save had failed.
    #
    # WRITES, like the #239 and #282 lanes above, so config.SCRIPT_DIR is patched to a
    # tempdir BEFORE the app is built and restored in finally; otherwise this check
    # would save over the maintainer's own files.
    try:
        import tkinter as tk
    except Exception:
        print("  (skipped save-readfail check: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped save-readfail check: no display)")
        return
    try:
        import config
        import settings_strings as sstr
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped save-readfail check: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    _showerror = ts.messagebox.showerror
    _askyesno = ts.messagebox.askyesno
    _script_dir = config.SCRIPT_DIR
    try:
        # Both are modal and would hang a headless run; both are also evidence here,
        # so they record instead of vanishing. Every confirmation is answered yes --
        # what must not happen is being ASKED about a save that cannot happen at all.
        errors = []     # (title, body) of every error dialog
        asked = []      # the body of every confirmation
        ts.messagebox.showerror = lambda title, body, *a, **k: errors.append((title, body))
        ts.messagebox.askyesno = lambda title, msg, **k: (asked.append(msg), True)[1]

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            config.SCRIPT_DIR = tmp
            ps = tmp / "personal_settings.json"
            healthy = json.dumps({"vocabulary": {"terms": ["Grüße"]}},
                                 indent=2, ensure_ascii=False) + "\n"
            ps.write_text(healthy, encoding="utf-8")
            # No key stored yet, so the keyless confirmation is live -- which is what
            # makes case D's "nothing was asked" mean something.
            env = tmp / ".env"
            env.write_text("# no key yet\n", encoding="utf-8")
            env_before = env.read_bytes()

            root.geometry("900x860")
            app = ts.SettingsApp(root, first_run=False)
            root.update()
            restarts = []
            # Unstubbed the handshake writes a real restart signal and destroys the
            # root mid-test (the #282 lane's reason too).
            app._restart_and_relaunch = lambda: restarts.append(True)

            # A -- personal_settings.json in bytes the app cannot decode (an ANSI file
            # whose German vocabulary is intact, B1's case), with a key freshly typed
            # into the field. The save has to stop at the pre-flight.
            ps.write_bytes('{"vocabulary": {"terms": ["Grüße"]}}\n'.encode("cp1252"))
            undecodable = ps.read_bytes()
            app.groq_var.set("gsk_typed_now")
            app._save()
            check(ps.read_bytes() == undecodable,
                  "the save rewrote an undecodable personal_settings.json -- the "
                  "vocabulary in there is still rescuable (B1, D-002)")
            check(env.read_bytes() == env_before,
                  "the save wrote the typed key into .env and only THEN found out that "
                  "personal_settings.json cannot be read -- the dialog would be saying "
                  "nothing was changed over a file that already changed (#291)")
            check(not restarts, "the save restarted Thoughtborne after aborting")
            check(errors[-1:] and errors[-1][0] == sstr.t("dlg.readfail.title", "en"),
                  f"the read failure came up under the wrong title: {errors[-1:]!r}")
            check(errors[-1:] and "personal_settings.json" in errors[-1][1],
                  f"the dialog does not name the file that could not be read, so the "
                  f"user cannot tell which of the two to repair: {errors[-1:]!r}")

            # B -- the other file: a readable personal_settings.json, an ANSI .env, and
            # a key to write into it. Same dialog, the other name.
            ps.write_text(healthy, encoding="utf-8")
            env.write_bytes("# Umlaut-Kommentar: Präfix\n".encode("cp1252"))
            env_ansi = env.read_bytes()
            dialogs = len(errors)
            app._save()
            check(len(errors) == dialogs + 1,
                  f"the save over an unreadable .env raised {len(errors) - dialogs} "
                  f"error dialogs, expected exactly one")
            check(errors[-1:] and errors[-1][0] == sstr.t("dlg.readfail.title", "en")
                  and ".env" in errors[-1][1],
                  f"an unreadable .env was not reported as itself: {errors[-1:]!r}")
            check(env.read_bytes() == env_ansi and ps.read_bytes() == healthy.encode("utf-8"),
                  "the aborted save touched a file anyway")
            check(not restarts, "the save restarted Thoughtborne after aborting")

            # C -- CONTROL: the same broken .env, but both key fields blank, which is
            # what a hotkey-only save looks like. write_env is a no-op there, so this
            # save never touches the file and must go through -- a pre-flight that
            # blocks it would break a save that works today.
            app.groq_var.set("")
            app.soniox_var.set("")
            dialogs, questions = len(errors), len(asked)
            app._save()
            check(len(errors) == dialogs,
                  f"a save that does not write .env at all was blocked over it: "
                  f"{errors[dialogs:]!r}")
            check(len(asked) == questions + 1,
                  "the keyless confirmation did not fire on the save that went "
                  "through -- this lane no longer proves the pre-flight let it past")
            check(restarts == [True],
                  "the save that went through did not reach the restart handshake")
            check(env.read_bytes() == env_ansi,
                  "the save rewrote the .env it had nothing to write to")
            check(json.loads(ps.read_text(encoding="utf-8"))
                  .get("vocabulary", {}).get("terms") == ["Grüße"],
                  "the save that went through did not keep the hand-written vocabulary")

            # D -- the order: over a file that cannot be read, the user is not asked
            # first and told afterwards. Both fields are still blank and no key is
            # stored, so the keyless confirmation WOULD fire -- it fired in C -- and
            # here it must not, because there is nothing to ask about a save that
            # cannot happen.
            ps.write_bytes(undecodable)
            dialogs, questions = len(errors), len(asked)
            app._save()
            check(len(asked) == questions,
                  "the save asked its keyless confirmation before finding out that it "
                  "cannot write at all -- the user confirms, and is then told it did "
                  "not happen (#291)")
            check(len(errors) == dialogs + 1
                  and errors[-1][0] == sstr.t("dlg.readfail.title", "en"),
                  f"the save over an unreadable file did not report the read failure: "
                  f"{errors[dialogs:]!r}")
            check(ps.read_bytes() == undecodable,
                  "the save rewrote the undecodable file")
    finally:
        config.SCRIPT_DIR = _script_dir
        ts.messagebox.showerror = _showerror
        ts.messagebox.askyesno = _askyesno
        try:
            root.destroy()
        except Exception:
            pass


def test_callback_error_log_with_display():
    # Only runs where a display exists (Xvfb on a CI/dev box); the normal WSL case skips
    # cleanly. Acceptance bullet 1 of #240, against the REAL wiring: an exception raised
    # inside a widget callback must land in thoughtborne.log with its traceback, and the
    # window must stay usable afterwards. Nothing about that can be proven from the
    # helpers alone -- it hangs on __init__ pointing Tk's report_callback_exception at
    # the handler, and on Tk's own callback boundary swallowing the exception.
    #
    # This check WRITES: config.LOG_FILE is patched to a tempdir before the app is
    # built and restored in finally, and the checkout's own log is compared byte for
    # byte at the end. (config.LOG_FILE is computed from SCRIPT_DIR at import, so
    # patching SCRIPT_DIR -- the #239 lane's idiom -- would NOT move the log.)
    try:
        import tkinter as tk
    except Exception:
        print("  (skipped callback-error-log check: tkinter unavailable)")
        return
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  (skipped callback-error-log check: no display)")
        return

    try:
        import config
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped callback-error-log check: cannot import the app: {e})")
        try:
            root.destroy()
        except Exception:
            pass
        return

    _showerror = ts.messagebox.showerror
    _log_file = config.LOG_FILE
    _stderr = sys.stderr
    checkout_log = Path(_log_file)
    checkout_before = checkout_log.read_bytes() if checkout_log.exists() else None
    try:
        # A modal showerror from __init__ (unreadable personal_settings.json) would hang
        # a headless run with nobody to dismiss it; neutralized for the build.
        ts.messagebox.showerror = lambda *a, **k: None

        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "thoughtborne.log"
            config.LOG_FILE = log

            root.geometry("800x860")
            ts.SettingsApp(root, first_run=False)
            root.update()

            state = {"benign": False}

            def boom():
                raise ValueError("deliberate-240-callback")

            def benign():
                state["benign"] = True

            # The stock handler the app delegates to prints to stderr; captured here so
            # a passing run stays quiet AND the delegation itself becomes checkable.
            captured = io.StringIO()
            escaped = None
            sys.stderr = captured
            try:
                root.after_idle(boom)
                root.update()
            except BaseException as e:     # the point of the check: none should escape
                escaped = e
            finally:
                sys.stderr = _stderr

            check(escaped is None,
                  f"the callback exception escaped update() as "
                  f"{type(escaped).__name__ if escaped else None} -- the handler must "
                  "consume it, not re-raise into the mainloop (#240)")
            text = log.read_text(encoding="utf-8") if log.exists() else ""
            lines = text.splitlines()
            leads = [ln for ln in lines
                     if "[SETTINGS] error: callback: ValueError: "
                        "deliberate-240-callback" in ln]
            check(leads,
                  f"no [SETTINGS] error: callback: block reached the log -- the "
                  f"exception was discarded exactly as before #240. Log said: {text!r}")
            check(any(ln.startswith("    ") and "Traceback (most recent call last)" in ln
                      for ln in lines),
                  f"the block carries no indented traceback: {text!r}")
            check(any(ln.startswith("    ") and "boom" in ln for ln in lines),
                  f"the raising callback is not named in the traceback: {text!r}")
            check("ValueError" in captured.getvalue()
                  and "deliberate-240-callback" in captured.getvalue(),
                  "the handler did not delegate to Tk's stock handler -- a console or "
                  f"Xvfb start loses the stderr traceback it prints today (#240). "
                  f"stderr held: {captured.getvalue()!r}")

            # The window stays usable: Tk consumed the exception at the callback
            # boundary and keeps dispatching. A second, benign callback proves it.
            root.after_idle(benign)
            root.update()
            check(state["benign"],
                  "a later callback no longer runs -- the window died with the "
                  "exception instead of staying usable (#240)")
            check(bool(root.winfo_exists()),
                  "the window no longer exists after a callback exception (#240)")

            # The flood cap: a callback that throws once usually throws every tick, and
            # these appends bypass the tool's log rotation. After the cap one
            # suppression line closes the lane.
            cap = ts._ERROR_LOG_CAP
            sys.stderr = io.StringIO()
            try:
                for _ in range(cap + 2):
                    root.after_idle(boom)
                    root.update()
            finally:
                sys.stderr = _stderr
            text = log.read_text(encoding="utf-8") if log.exists() else ""
            blocks = text.count("[SETTINGS] error: callback: ValueError: "
                                "deliberate-240-callback")
            check(blocks == cap,
                  f"{blocks} callback blocks were written for {cap + 3} exceptions -- "
                  f"the cap of {cap} did not hold (#240)")
            check(text.count(f"further callback errors suppressed after {cap}") == 1,
                  f"expected exactly one suppression notice after the cap: {text!r}")
    finally:
        sys.stderr = _stderr
        config.LOG_FILE = _log_file
        ts.messagebox.showerror = _showerror
        try:
            root.destroy()
        except Exception:
            pass
    after = checkout_log.read_bytes() if checkout_log.exists() else None
    check(after == checkout_before,
          "this check wrote to the checkout's own thoughtborne.log -- the "
          "config.LOG_FILE patch did not hold, and a test must never touch the "
          "maintainer's log")


def test_main_error_log_with_display():
    # Only runs where a display exists; skips cleanly otherwise. Acceptance bullet 2 of
    # #240 and the only automated proof of it: a failure BEFORE mainloop() must be
    # recorded rather than vanishing. _size_window is replaced by a throwing marker (it
    # sits after tk.Tk() and before the app is constructed, so the real startup path
    # runs up to it), and main() is called for real. Off-Windows
    # settings_instance.create_instance_mutex() fails open to (None, False), so the
    # focus-existing branch cannot fire; on a box where it can, the lane skips.
    #
    # Writes, like the lane above: config.LOG_FILE goes to a tempdir, restored in
    # finally, and the checkout's log is compared byte for byte afterwards.
    try:
        import tkinter as tk
    except Exception:
        print("  (skipped main-error-log check: tkinter unavailable)")
        return
    try:
        probe = tk.Tk()
    except tk.TclError:
        print("  (skipped main-error-log check: no display)")
        return
    probe.destroy()                 # main() builds its own root

    try:
        import config
        import thoughtborne_settings as ts
    except Exception as e:
        print(f"  (skipped main-error-log check: cannot import the app: {e})")
        return

    _size_window = ts._size_window
    _log_file = config.LOG_FILE
    _warnings = config.IMPORT_WARNINGS
    _argv = sys.argv
    checkout_log = Path(_log_file)
    checkout_before = checkout_log.read_bytes() if checkout_log.exists() else None
    try:
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "thoughtborne.log"
            config.LOG_FILE = log
            # The import-warning replay rides the same sink (#240): a broken .env or
            # personal_settings.json must leave its trace in the log of the window that
            # repairs it, ABOVE any error block from the same start.
            config.IMPORT_WARNINGS = ["deliberate-240 broken .env"]

            def boom(root):
                raise RuntimeError("deliberate-240-main")

            ts._size_window = boom
            sys.argv = ["thoughtborne_settings.py"]

            escaped = None
            try:
                ts.main()
            except BaseException as e:     # classified below (SystemExit -> skip)
                escaped = e

            if isinstance(escaped, SystemExit):
                print("  (skipped main-error-log check: main() took the "
                      "focus-existing exit -- a settings window is already running)")
                return
            check(isinstance(escaped, RuntimeError)
                  and "deliberate-240-main" in str(escaped),
                  f"main() did not re-raise the failure ({escaped!r}) -- re-raising is "
                  "what keeps a console start's stderr traceback and non-zero exit "
                  "(#240)")
            text = log.read_text(encoding="utf-8") if log.exists() else ""
            lines = text.splitlines()
            check(any("[SETTINGS] error: main: RuntimeError: deliberate-240-main" in ln
                      for ln in lines),
                  f"a failure before mainloop() left no [SETTINGS] error: main: line -- "
                  f"it is a silent no-op again, right after the tool logged 'Opened the "
                  f"settings app' (#240). Log said: {text!r}")
            check(any(ln.startswith("    ") and "boom" in ln for ln in lines),
                  f"the block carries no indented traceback naming the failure: {text!r}")
            warn = [i for i, ln in enumerate(lines)
                    if "[SETTINGS] import-warning: deliberate-240 broken .env" in ln]
            err = [i for i, ln in enumerate(lines) if "[SETTINGS] error: main:" in ln]
            check(warn,
                  f"config.IMPORT_WARNINGS was not replayed into the log -- the repair "
                  f"surface's own trail does not name what needs repairing: {text!r}")
            check(warn and err and warn[0] < err[0],
                  "the import warning must stand ABOVE the error block of the same "
                  f"start -- that is the reading order the replay is placed for: {text!r}")
    finally:
        sys.argv = _argv
        config.IMPORT_WARNINGS = _warnings
        config.LOG_FILE = _log_file
        ts._size_window = _size_window
        # main() failed with its root already built; leaving it behind would upset any
        # later check that expects a clean interpreter.
        try:
            import tkinter
            if tkinter._default_root is not None:
                tkinter._default_root.destroy()
        except Exception:
            pass
    after = checkout_log.read_bytes() if checkout_log.exists() else None
    check(after == checkout_before,
          "this check wrote to the checkout's own thoughtborne.log -- the "
          "config.LOG_FILE patch did not hold, and a test must never touch the "
          "maintainer's log")


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
    test_format_settings_line_parity()
    test_format_error_block()
    test_format_error_block_never_raises()
    test_append_log_line()
    test_log_sink_source_guards()
    test_storm_guards_with_display()
    test_tab_layout_with_display()
    test_empty_label_sweep_with_display()
    test_maximize_restore_with_display()
    test_verdict_wrap_with_display()
    test_language_toggle_gate_with_display()
    test_reset_with_display()
    test_save_readfail_with_display()
    test_callback_error_log_with_display()
    test_main_error_log_with_display()

    if SHOW:
        _show()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: wrap_length formula, scrollbar auto-hide decision, the visible: line "
          "formatter (full / fail-open / partial), the #240 log sink (line parity with "
          "the pre-#240 literals, error block, never-raise sweep, guarded append) with "
          "its source guards, and (with a display) the auto-hide grid idempotency, "
          "wrap-deferral invariant, the #281 six-tab layout with its version line and "
          "strip width, the #299 empty-text sweep over both modes and "
          "languages, the #216 maximize->restore content-vanish guard, the #231 "
          "verdict-line wrap, the #239 language-toggle gate, the #282 reset control "
          "with its confirmation gate, the #291 read-failure dialog on both save "
          "paths with .env left untouched, and the #240 callback / pre-mainloop "
          "crash logging all pass")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
