#!/usr/bin/env python3
"""The typed-insert cap is actually wired into both typed routes (#242, cites D-003).

`test_typed_cap.py` proves the *helper*: `cap_typed_text` gets the character
math right and builds an honest notice. What it cannot prove is that anything
still calls it -- and a cap nobody calls is exactly D-003's "Do not reintroduce:
an uncapped `keyboard.write()` on any typed route", happening in silence.
Replacing both call sites in `output_handler.py` with `text, False, len(text)`
passes every other driver in the ladder.

So this driver checks the *wiring*: it drives the REAL `OutputManager` and
asserts on what reaches `keyboard.write` on both typed routes --

  - the primary typed route in the output thread (a task with
    `use_clipboard=False`: the stop-hotkey typing path and the self-test), and
  - the clipboard path's fallback to typing after a failed paste.

Nothing is capped that fits, either: an ordinary transcript must arrive byte for
byte and be reported untruncated, which is what keeps a future "just cap
everything" from passing.

`output_handler` loads Win32 DLLs and imports keyboard/pyperclip/pyautogui, so
those are faked here (the pattern of `test_retry_marker_lifecycle.py`) and the
collaborators the two routes really use are swapped in as module globals of
`output_handler` afterwards. Everything below those fakes is the shipped code.

Background on the cap itself: `keyboard.write()` injects each character via
Win32 SendInput without pausing, and past an app-dependent break point the
target silently drops most of the rest while reporting success (spike #161,
decided in #7 / D-003).

    python3 test_typed_cap_wiring.py    # verify, exit non-zero on any violation
"""
import ctypes
import logging
import sys
import threading
import types

from typed_cap import cap_typed_text, TYPED_INSERT_CAP, TYPED_INSERT_CAP_NOTICE


# ---- import the Windows-only module off Windows ----------------------------
class _AnyCallable:
    def __call__(self, *a, **k):
        return 0
    def __getattr__(self, n):
        return _AnyCallable()
    def __setattr__(self, n, v):  # swallow the module-level argtypes/restype config
        pass


class _FakeDLLNamespace:
    def __getattr__(self, n):
        return _AnyCallable()


ctypes.windll = _FakeDLLNamespace()
ctypes.WinDLL = lambda *a, **k: _AnyCallable()

# setdefault, never assignment: under pytest another driver may already have
# installed these stubs and imported output_handler against them. The #241 guard
# in test_retry_marker_lifecycle reads sys.modules["pyautogui"].FAILSAFE at run
# time -- swapping that module object out here would blank the flag and turn a
# green guard red for reasons that have nothing to do with the product.
for _mod in ("keyboard", "pyperclip", "pyautogui"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

# Silence before the import: the routes below log warnings/errors by design
# (a failing paste is a normal event here), and lastResort would print them.
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

import output_handler as oh  # noqa: E402

NOTICE = TYPED_INSERT_CAP_NOTICE.format(cap=TYPED_INSERT_CAP)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- stand-ins for the collaborators the two routes use --------------------
class _RecordingKeyboard:
    """Stands in for the `keyboard` module: records what would be typed."""

    def __init__(self):
        self.writes = []

    def write(self, text):
        self.writes.append(text)

    def send(self, combo):  # the paste path's Ctrl+V; not reached on these routes
        pass


class _RaisingPyperclip:
    """Forces the clipboard route into its typed fallback.

    `pyperclip.paste()` sits inside an inner try (a raise there is swallowed and
    only flags "non-text clipboard"), `pyperclip.copy()` does not -- so raising
    there is the deterministic way into the outer handler that owns the fallback
    call site, before any of the route's diagnostic sleeps.
    """

    def paste(self):
        return "previous clipboard"

    def copy(self, text):
        raise RuntimeError("clipboard unavailable (simulated)")


# ---- drivers for the two routes --------------------------------------------
def _drive_typed_route(transcript, timeout=5.0):
    """Push one task through the real output thread's typed route.

    Returns (writes, callback_kwargs); callback_kwargs is None when the
    'inserted' callback never fired.
    """
    kb = _RecordingKeyboard()
    oh.keyboard = kb
    captured = {}
    fired = threading.Event()

    def on_complete(**kw):
        if kw.get("event") == "inserted":
            captured.update(kw)
            fired.set()

    om = oh.OutputManager(on_task_complete_callback=on_complete)  # starts the thread
    try:
        om.add_task(oh.TranscriptionTask(
            sequence_number=om.get_next_sequence_number(),  # 0 == next_sequence_to_output
            timestamp="20260906_000000",
            transcript=transcript,
            is_complete=True,
            use_clipboard=False,
        ))
        arrived = fired.wait(timeout)
    finally:
        om.stop()
    return kb.writes, (captured if arrived else None)


def _drive_clipboard_fallback(text):
    """Run the clipboard route with a copy() that raises, so it types instead.

    The route is a plain method, so no queue is needed -- but the manager starts
    its thread in __init__, hence the stop() either way.
    """
    kb = _RecordingKeyboard()
    oh.keyboard = kb
    oh.pyperclip = _RaisingPyperclip()
    om = oh.OutputManager()
    try:
        outcome = om._insert_text_via_clipboard(text)
    finally:
        om.stop()
    return kb.writes, outcome


def _one_write(where, writes):
    """Exactly one keyboard.write happened; returns it, or None (with a
    recorded failure) so the caller can skip its payload assertions."""
    if len(writes) != 1:
        check(False, f"{where}: expected exactly 1 keyboard.write, got {len(writes)}")
        return None
    return writes[0]


def _check_capped_payload(where, typed, original):
    """The D-003 payload guarantees, identical on both routes."""
    check(len(typed) <= TYPED_INSERT_CAP,
          f"{where}: typed {len(typed)} chars, above the cap of {TYPED_INSERT_CAP} "
          f"(the uncapped keyboard.write D-003 forbids)")
    check(typed.endswith(NOTICE),
          f"{where}: the capped text does not end with the cap notice")
    check(typed == cap_typed_text(original)[0],
          f"{where}: what was typed is not what cap_typed_text() returns for it")


# ---- cases -----------------------------------------------------------------
def check_thread_typed_route_caps():
    """The primary typed route caps, and reports the cap to the console (#7)."""
    text = "x" * (3 * TYPED_INSERT_CAP)
    writes, cb = _drive_typed_route(text)
    typed = _one_write("typed route", writes)
    if typed is not None:
        _check_capped_payload("typed route", typed, text)
    if cb is None:
        check(False, "typed route: the 'inserted' callback never fired")
        return
    check(cb.get("mode") == "typing",
          f"typed route: callback mode is {cb.get('mode')!r}, expected 'typing'")
    check(cb.get("truncated") is True,
          "typed route: a capped insert was not reported as truncated")
    check(cb.get("cap") == TYPED_INSERT_CAP,
          f"typed route: callback cap is {cb.get('cap')!r}, expected {TYPED_INSERT_CAP}")
    check(cb.get("original_chars") == len(text),
          f"typed route: callback original_chars is {cb.get('original_chars')!r}, "
          f"expected {len(text)}")
    if typed is not None:
        check(cb.get("chars") == len(typed),
              f"typed route: callback chars is {cb.get('chars')!r}, "
              f"but {len(typed)} chars were typed")


def check_clipboard_fallback_caps():
    """The paste-failure fallback caps too, and says so in its outcome (#7)."""
    text = "y" * (3 * TYPED_INSERT_CAP)
    writes, outcome = _drive_clipboard_fallback(text)
    typed = _one_write("clipboard fallback", writes)
    if typed is not None:
        _check_capped_payload("clipboard fallback", typed, text)
    check(outcome.success is True,
          "clipboard fallback: a landed typed fallback must still report success")
    check(outcome.typed_fallback is True,
          "clipboard fallback: the outcome does not flag the typed fallback")
    check(outcome.truncated is True,
          "clipboard fallback: a capped fallback was not flagged truncated")
    check(outcome.original_chars == len(text),
          f"clipboard fallback: original_chars is {outcome.original_chars}, "
          f"expected {len(text)}")
    if typed is not None:
        check(outcome.typed_chars == len(typed),
              f"clipboard fallback: typed_chars is {outcome.typed_chars}, "
              f"but {len(typed)} chars were typed")


def check_short_text_reaches_both_routes_verbatim():
    """Not capping is part of the contract: everyday transcripts arrive whole
    and are reported untruncated -- on both routes."""
    text = "A short transcript, comfortably below the cap."

    writes, cb = _drive_typed_route(text)
    typed = _one_write("typed route (short)", writes)
    if typed is not None:
        check(typed == text, "typed route (short): the text was altered on the way")
    if cb is None:
        check(False, "typed route (short): the 'inserted' callback never fired")
    else:
        check(cb.get("truncated") is False,
              "typed route (short): an uncapped insert was reported as truncated")
        check(cb.get("chars") == len(text) and cb.get("original_chars") == len(text),
              f"typed route (short): callback lengths wrong "
              f"(chars={cb.get('chars')!r}, original_chars={cb.get('original_chars')!r}, "
              f"expected {len(text)})")

    writes, outcome = _drive_clipboard_fallback(text)
    typed = _one_write("clipboard fallback (short)", writes)
    if typed is not None:
        check(typed == text,
              "clipboard fallback (short): the text was altered on the way")
    check(outcome.truncated is False,
          "clipboard fallback (short): an uncapped fallback was flagged truncated")
    check(outcome.typed_chars == len(text) and outcome.original_chars == len(text),
          f"clipboard fallback (short): outcome lengths wrong "
          f"(typed_chars={outcome.typed_chars}, original_chars={outcome.original_chars}, "
          f"expected {len(text)})")


def check_exact_cap_boundary_on_both_routes():
    """Exactly at the cap is <= and therefore untouched -- the boundary the
    wiring has to inherit from the helper, not just the helper's own tests."""
    text = "z" * TYPED_INSERT_CAP

    writes, cb = _drive_typed_route(text)
    typed = _one_write("typed route (exact cap)", writes)
    if typed is not None:
        check(typed == text,
              f"typed route (exact cap): text of exactly {TYPED_INSERT_CAP} chars was altered")
    if cb is None:
        check(False, "typed route (exact cap): the 'inserted' callback never fired")
    else:
        check(cb.get("truncated") is False,
              "typed route (exact cap): text exactly at the cap was reported truncated")

    writes, outcome = _drive_clipboard_fallback(text)
    typed = _one_write("clipboard fallback (exact cap)", writes)
    if typed is not None:
        check(typed == text,
              f"clipboard fallback (exact cap): text of exactly {TYPED_INSERT_CAP} "
              f"chars was altered")
    check(outcome.truncated is False,
          "clipboard fallback (exact cap): text exactly at the cap was flagged truncated")


def main():
    # Save and restore what we swap on the shared module: under pytest every
    # driver runs in one process, and a raising pyperclip left behind would be a
    # booby trap for whoever imports output_handler next.
    saved = {name: getattr(oh, name) for name in ("keyboard", "pyperclip", "is_key_pressed")}
    # Both routes poll for still-held modifier keys before typing
    # (_ensure_no_modifiers_pressed). With no real Win32 underneath, answer
    # "nothing is down" so the checks never sit out its 2 s timeout.
    oh.is_key_pressed = lambda key: False
    try:
        check_thread_typed_route_caps()
        check_clipboard_fallback_caps()
        check_short_text_reaches_both_routes_verbatim()
        check_exact_cap_boundary_on_both_routes()
    finally:
        for name, value in saved.items():
            setattr(oh, name, value)

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: both typed routes (output thread + clipboard fallback) cap through "
          "cap_typed_text and report it honestly")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
