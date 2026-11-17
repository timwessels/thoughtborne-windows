#!/usr/bin/env python3
"""The clipboard insert, driven end to end against fakes: copy, Ctrl+V, retry, restore.

    python3 test_clipboard_insert.py    # verify, exit non-zero on failure

`_insert_text_via_clipboard` is the route nearly every dictation takes, and its success
path had no test: `test_typed_cap_wiring.py` enters it only to make `pyperclip.copy`
raise and reach the typed fallback, and nothing reached `_paste_via_hotkey` at all.
This driver runs both real methods and records, in order, what they do to the
clipboard, the keyboard and the target control.

What it pins:

  - The order: the transcript is copied, Ctrl+V is sent, and only then does the
    user's previous clipboard come back -- so the target receives the transcript.
  - One Ctrl+V for a paste that lands, also in a text control that processes it only
    after the first length read: such a control is given time, not a second paste.
  - A second Ctrl+V when a text control's length provably did not change, sent while
    the clipboard still holds the transcript (#29, #225) -- and no third when that one
    misses too, which the verdict reports as DID NOT LAND.
  - A modifier held past the wait leaves the clipboard and the keyboard untouched.

The cases assert on the copies and sends, on what the target ends up holding, on the
outcome and on the verdict line -- not on the waits in between, which are tuning
rather than contract. Every collaborator call still lands in one ordered event list
(`InsertRig.events`: sleeps, sampling windows, modifier polls and length reads
included), so a case about the timing can read it.

How the fakes work: `output_handler`'s `time` is a virtual clock whose `sleep` only
advances it, so no case sleeps, and the #29 sampling windows are recorded by label
instead of run. The target is a text control whose length grows by what the clipboard
held at the moment a Ctrl+V lands -- read at landing, as a real control reads it when
it processes the keystroke, so a clipboard restored too early shows up as the wrong
text in the target. A case says per Ctrl+V when it lands, or that it never does.

The Win32 diagnostics left in place (`_clipboard_diag`, `_describe_hwnd`) run against
the null DLL of `test_typed_cap_wiring.py`, where every call answers 0. The Enter of
the send variant is pressed by the output thread after this method returns and is
not part of this driver.
"""
import ctypes
import logging
import sys
import types


# ---- import the Windows-only module off Windows (as in test_typed_cap_wiring.py) ----
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

# setdefault, never assignment -- see test_typed_cap_wiring.py for why.
for _mod in ("keyboard", "pyperclip", "pyautogui"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

import output_handler as oh  # noqa: E402

TRANSCRIPT = "A short dictation that should arrive through Ctrl+V."
PREVIOUS = "what the user had copied"
EXISTING = 40                             # characters the control holds beforehand
FOCUS = 0x1234
TEXT_CONTROL = "Edit"                     # trusted: its length is the document's
BROWSER = "Chrome_RenderWidgetHostHWND"   # untrusted: its length is the window title's


# ---- harness ---------------------------------------------------------------
class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class InsertRig:
    """One clipboard insert against fakes, recorded in `events`.

    `lands` gives, per Ctrl+V in order, the seconds until the target processes it,
    or None for one it never processes; a Ctrl+V past the end of the list lands at
    once. `key_down(key)` answers the modifier poll; a case may replace it.
    """

    def __init__(self, target_class, lands=()):
        self.target_class = target_class
        self.lands = list(lands)
        self.key_down = lambda key: False
        self.now = 0.0
        self.events = []
        self.clipboard = [(self.now, PREVIOUS)]   # (time, content), oldest first
        self.landings = []                        # when each processed Ctrl+V landed
        self.log = []
        self.outcome = None

    # -- the collaborators, swapped in as module globals of output_handler --
    def _sleep(self, seconds):
        self.events.append(("sleep", seconds))
        self.now += seconds

    def _window(self, label, seconds):
        self.events.append(("window", label, seconds))
        self.now += seconds

    def _copy(self, text):
        self.events.append(("copy", text))
        self.clipboard.append((self.now, text))

    def _send(self, combo):
        self.events.append(("send", combo))
        if combo == "ctrl+v":
            delay = self.lands.pop(0) if self.lands else 0.0
            if delay is not None:
                self.landings.append(self.now + delay)

    def _write(self, text):
        self.events.append(("write", text))

    def _key(self, key):
        self.events.append(("key", key))
        return self.key_down(key)

    def _length(self, hwnd):
        length = EXISTING + len(self.received())
        self.events.append(("measure", length))
        return length

    # -- what the cases read --
    def received(self):
        """What the target holds by now: per landed Ctrl+V, the clipboard as it stood
        at that moment."""
        return "".join([content for t, content in self.clipboard if t <= at][-1]
                       for at in self.landings if at <= self.now)

    def actions(self):
        """The copies, sends and typed writes: the sequence itself, without the
        waits and reads around it."""
        return [e for e in self.events if e[0] in ("copy", "send", "write")]

    def verdict(self):
        lines = [m for m in self.log if m.startswith("[CLIPDIAG verdict]")]
        return lines[-1] if lines else ""

    def run(self, text=TRANSCRIPT):
        manager = oh.OutputManager()
        manager.stop()   # the method runs on this thread; the queue thread is not needed
        fakes = {
            "time": types.SimpleNamespace(sleep=self._sleep, time=lambda: self.now),
            "pyperclip": types.SimpleNamespace(copy=self._copy,
                                               paste=lambda: self.clipboard[-1][1]),
            "keyboard": types.SimpleNamespace(send=self._send, write=self._write),
            "is_key_pressed": self._key,
            "_diag_focus_window": lambda: FOCUS,
            "_window_class": lambda hwnd: self.target_class,
            "_diag_text_length": self._length,
            "_diag_sample_window": self._window,
        }
        saved = {name: getattr(oh, name) for name in fakes}
        capture, level = _Capture(), oh.logger.level
        oh.logger.addHandler(capture)
        oh.logger.setLevel(logging.DEBUG)
        for name, fake in fakes.items():
            setattr(oh, name, fake)
        try:
            self.outcome = manager._insert_text_via_clipboard(text)
        finally:
            for name, value in saved.items():
                setattr(oh, name, value)
            oh.logger.removeHandler(capture)
            oh.logger.setLevel(level)
        self.log = capture.messages
        return self


def assert_inserted(rig, sends):
    """Copied, pasted `sends` times, then the user's clipboard put back -- and
    reported as an insert, not as the typed fallback."""
    expected = [("copy", TRANSCRIPT)] + [("send", "ctrl+v")] * sends + [("copy", PREVIOUS)]
    assert rig.actions() == expected, f"expected {expected}, got {rig.actions()}"
    assert rig.outcome.success and not rig.outcome.typed_fallback, rig.outcome


# ======================================================================
# The order
# ======================================================================

def test_the_transcript_is_pasted_before_the_clipboard_is_restored():
    rig = InsertRig(BROWSER).run()
    assert_inserted(rig, sends=1)
    assert rig.received() == TRANSCRIPT, f"the target received {rig.received()!r}"
    assert "INCONCLUSIVE" in rig.verdict(), rig.verdict()


def test_a_paste_that_lands_is_sent_once():
    rig = InsertRig(TEXT_CONTROL).run()
    assert_inserted(rig, sends=1)
    assert rig.received() == TRANSCRIPT, f"the target received {rig.received()!r}"
    assert rig.verdict().endswith(": PASTE LANDED"), rig.verdict()


def test_a_slow_text_control_gets_time_not_a_second_paste():
    # Lands after the first length read and before the re-read that follows it: a
    # retry here would paste the transcript twice.
    rig = InsertRig(TEXT_CONTROL, lands=[0.2]).run()
    assert_inserted(rig, sends=1)
    assert rig.received() == TRANSCRIPT, f"the target received {rig.received()!r}"
    assert rig.verdict().endswith(": PASTE LANDED"), rig.verdict()


# ======================================================================
# The retry
# ======================================================================

def test_a_paste_that_did_not_land_is_sent_again_before_the_restore():
    rig = InsertRig(TEXT_CONTROL, lands=[None]).run()
    assert_inserted(rig, sends=2)
    assert rig.received() == TRANSCRIPT, f"the target received {rig.received()!r}"
    assert rig.verdict().endswith(": PASTE LANDED"), rig.verdict()


def test_a_retry_that_misses_too_is_reported_and_not_repeated():
    rig = InsertRig(TEXT_CONTROL, lands=[None, None]).run()
    assert_inserted(rig, sends=2)
    assert rig.received() == "", f"the target received {rig.received()!r}"
    assert rig.verdict().endswith(": PASTE DID NOT LAND"), rig.verdict()


# ======================================================================
# Held modifiers
# ======================================================================

def test_a_held_modifier_leaves_clipboard_and_keyboard_alone():
    rig = InsertRig(TEXT_CONTROL)
    rig.key_down = lambda key: key == "alt"
    rig.run()
    assert rig.actions() == [], f"acted with Alt held: {rig.actions()}"
    assert rig.outcome.success is False, rig.outcome
    assert rig.clipboard[-1][1] == PREVIOUS, "the user's clipboard was replaced"


CASES = [
    test_the_transcript_is_pasted_before_the_clipboard_is_restored,
    test_a_paste_that_lands_is_sent_once,
    test_a_slow_text_control_gets_time_not_a_second_paste,
    test_a_paste_that_did_not_land_is_sent_again_before_the_restore,
    test_a_retry_that_misses_too_is_reported_and_not_repeated,
    test_a_held_modifier_leaves_clipboard_and_keyboard_alone,
]


def main():
    failures = []
    for case in CASES:
        try:
            case()
            print(f"PASS  {case.__name__}")
        except AssertionError as e:
            failures.append(case.__name__)
            print(f"FAIL  {case.__name__}: {e}")
        except Exception as e:  # a crash is also a failure
            failures.append(case.__name__)
            print(f"ERROR {case.__name__}: {type(e).__name__}: {e}")

    if failures:
        print(f"\nFAIL: {len(failures)}/{len(CASES)} case(s) failed")
        return 1
    print(f"\nOK: all {len(CASES)} clipboard-insert cases pass "
          "(copy -> Ctrl+V -> restore, one retry only on a provable miss)")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
