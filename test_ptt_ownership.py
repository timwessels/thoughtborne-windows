#!/usr/bin/env python3
"""Lifecycle tests for push-to-talk's ownership of a recording (#250).

    python3 test_ptt_ownership.py    # verify, exit non-zero on failure

`_ptt_owns_recording` is the app-side half of push-to-talk: the flag that says the
CURRENT recording belongs to the gesture, so releasing the trigger stops it. It is
set in `_ptt_start_recording` and cleared in `_ptt_stop_and_insert` -- and every
other way a recording can end (a stop hotkey, cancel, the device-loss abort) used to
leave it standing. Nothing cleared it afterwards either: the inert guard at the head
of `_ptt_tick` only engages for a recording push-to-talk does NOT own, and with the
stale flag the next one counted as owned. So releasing the trigger -- still physically
down right after `Ctrl+Alt+H` -- stopped a `Ctrl+Alt+W` recording and delivered its
text through the PTT insert route. The detector also stayed in RECORDING, so an
immediate re-gesture was absorbed tick by tick: no recording, no log line, nothing
to see.

Neither existing driver can see this. `test_ptt_detector.py` drives the pure state
machine, which knows nothing about recordings or ownership -- the flag does not exist
in `ptt_detector.py`. `test_stop_rebind_wiring.py` runs the real callbacks but its
charter is the derivations from the effective `HOTKEYS` (#152), not the ownership
lifecycle. So this driver takes that file's harness -- the real `ThoughtborneApp`
methods bound onto a fake `self`, with `thoughtborne` imported under faked Win32/GUI
modules -- and scripts `tb.is_vk_pressed` the way the other one scripts
`is_key_pressed`. Everything runs off Windows, with no display and no skips.

What it pins:

  - A recording ended through a foreign door releases ownership on the next tick,
    and the recording started after it survives the trigger release: it keeps
    running and no second processing thread is started behind the user's back.
  - The same tick also resets the detector, so an immediate re-gesture records
    again. That lane doubles as a behavioural proof of `reset()`'s deliberate
    asymmetry (it keeps `_prev_trigger`): the guard hands the machine back to IDLE
    with the trigger still down, and a phantom rising edge there would either start
    a recording too early or leave the real gesture one tap short.
  - The owned round-trip is unchanged: a gesture still starts a recording, the
    trigger release still stops it through the configured insert path. This is the
    anchor against a guard that overreaches -- `owns and not is_recording` must
    stay silent for the whole life of a recording PTT does own.

The fake app carries its own detector with tiny windows (min-hold and release tail
0.0, tap window an hour). `_ptt_tick` reads the real `time.monotonic()`, and both
short windows are `>=` comparisons, so they are satisfied on the very next tick
whatever the machine's speed, while the tap window cannot expire mid-case. Every
sequence below is therefore exact rather than timing-dependent, and no clock is
patched. The PTT settings sit on the fake as fixture values (trigger, insert mode)
so this checkout's `personal_settings.json` cannot decide which branch a lane takes.

Two things these lanes deliberately do not do. The foreign recording in lane 1 is
created by setting `is_recording` directly instead of driving `on_start_recording`:
what the tick sees is a recording it does not own, and how that recording came about
is invisible to it -- driving the real start would only pull the wedge guard and the
REC strip into the fixture. And only one door is exercised (`Ctrl+Alt+H`): the guard
reads `is_recording`, not which door closed, so cancel and the device-loss abort take
the identical path. What stays untested is the microsecond race INSIDE one tick --
head check passes, a stop hotkey lands on its own thread, STOP arrives -- which is
what the not-recording guard in `_ptt_stop_and_insert` is for; reaching it would need
real threads racing in the fixture, and it would prove nothing the guard's own
comment does not say.
"""
import contextlib
import ctypes
import logging
import shutil
import sys
import tempfile
import types
from pathlib import Path

# ---- Windows-only / third-party modules the import chain needs, faked exactly as in
# test_stop_rebind_wiring.py: audio_handler pulls in msvcrt/pyaudio, hotkey_manager and
# output_handler configure argtypes on ctypes.windll handles at import, output_handler
# imports keyboard/pyperclip/pyautogui. None of it is exercised here -- only enough to
# let the real methods run off Windows.
import subprocess  # noqa: E402,F401
import soundfile   # noqa: E402,F401

_fake_msvcrt = types.ModuleType("msvcrt")
_fake_msvcrt.locking = lambda *a, **k: None
_fake_msvcrt.LK_NBLCK = 0
_fake_msvcrt.LK_UNLCK = 0
sys.modules.setdefault("msvcrt", _fake_msvcrt)

_fake_pyaudio = types.ModuleType("pyaudio")
_fake_pyaudio.paInt16 = 16
_fake_pyaudio.get_sample_size = lambda fmt: 2
_fake_pyaudio.PyAudio = object
sys.modules.setdefault("pyaudio", _fake_pyaudio)


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

for _mod in ("keyboard", "pyperclip", "pyautogui"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

# transcriber imports `Groq` and `AuthenticationError` at module level. The real SDK is
# a test dependency (run_tests, CI), so prefer it and stub only the two names when it is
# absent -- no lane here touches groq either way.
try:
    import groq  # noqa: E402,F401
except ImportError:
    _fake_groq = types.ModuleType("groq")
    _fake_groq.Groq = type("Groq", (), {"__init__": lambda self, *a, **k: None})
    _fake_groq.AuthenticationError = type("AuthenticationError", (Exception,), {})
    sys.modules.setdefault("groq", _fake_groq)

import audio_handler as ah  # noqa: E402
import config  # noqa: E402

# Throwaway dirs, both BEFORE `import thoughtborne`: that import attaches a
# RotatingFileHandler to config.LOG_FILE and creates the file where none exists, which
# in a fresh clone fakes the "tool is running" heartbeat a reader of thoughtborne.log
# goes by (#267). thoughtborne binds both values at import, so they have to move first,
# not after. The archive moves with them so no lane can ever reach the user's history.
_LOG_DIR = Path(tempfile.mkdtemp(prefix="tb_ptt_own_log_"))     # removed in main()
_ARCHIVE = Path(tempfile.mkdtemp(prefix="tb_ptt_own_arch_"))    # removed in main()
config.LOG_FILE = _LOG_DIR / "thoughtborne.log"
config.ARCHIVE_FOLDER = _ARCHIVE
ah.ARCHIVE_FOLDER = _ARCHIVE
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

import thoughtborne as tb  # noqa: E402
from ptt_detector import PttDetector  # noqa: E402

# Undo thoughtborne's import-time global side effects: it swaps sys.stdout/stderr for
# logger wrappers and attaches handlers. Restore the streams, detach every handler, and
# re-assert the silence its module-level logging setup cleared.
sys.stdout = tb.original_stdout
sys.stderr = tb.original_stderr
for _name in ("Thoughtborne", "Thoughtborne.stdio", "Thoughtborne.console"):
    _lg = logging.getLogger(_name)
    for _h in list(_lg.handlers):
        _lg.removeHandler(_h)
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)
tb.ARCHIVE_FOLDER = _ARCHIVE

TRIGGER_VK = 0xA2   # VK_LCONTROL, the shipped default trigger


# ---- harness ---------------------------------------------------------------
@contextlib.contextmanager
def vk_down(*vks):
    """Script is_vk_pressed: exactly `vks` read as physically down. The PTT paths read
    the module-level name at call time, so rebinding the attribute is what a keyboard
    looks like to them -- the same handle test_stop_rebind_wiring.py takes on
    is_key_pressed."""
    down = set(vks)
    previous = tb.is_vk_pressed
    tb.is_vk_pressed = lambda vk: vk in down
    try:
        yield
    finally:
        tb.is_vk_pressed = previous


class _FakeAudio:
    def __init__(self, recording=False):
        self.is_recording = recording

    def start_recording(self):
        self.is_recording = True
        return True

    def stop_recording(self):
        self.is_recording = False
        return ([], 1.0)

    def take_finished_sidecar(self):
        return None


class _FakeTranscriber:
    """A file engine: is_live False keeps the live-session branch of the PTT start out
    of the fixture, which has nothing to do with ownership."""
    is_live = False

    def get_name(self):
        return "fake"


class _FakeApp:
    """The collaborator surface the real methods touch. Everything under test is the
    genuine ThoughtborneApp method, bound as a class attribute."""
    _ptt_tick = tb.ThoughtborneApp._ptt_tick
    _ptt_start_recording = tb.ThoughtborneApp._ptt_start_recording
    _ptt_stop_and_insert = tb.ThoughtborneApp._ptt_stop_and_insert
    _ptt_insert_kwargs = tb.ThoughtborneApp._ptt_insert_kwargs
    _ptt_foreign_key_down = tb.ThoughtborneApp._ptt_foreign_key_down
    on_stop_recording_keyboard = tb.ThoughtborneApp.on_stop_recording_keyboard
    _show = tb.ThoughtborneApp._show
    _wait_keys = tb.ThoughtborneApp._wait_keys
    _stop_debounce_elapsed = tb.ThoughtborneApp._stop_debounce_elapsed

    def __init__(self):
        self.audio_recorder = _FakeAudio()
        self.transcriber = _FakeTranscriber()
        self._active_live_transcriber = None
        self._keyless = False
        self._stop_insert_debounce = {}
        self.starts = []   # start_processing_thread kwargs, in call order
        # A real detector with real rules, on windows small enough that one tick is one
        # step of the gesture whatever the machine's speed (see the module docstring).
        self._ptt = PttDetector(tap_window_s=3600.0, min_hold_s=0.0, release_tail_s=0.0)
        self._ptt_owns_recording = False
        self._ptt_trigger_vk = TRIGGER_VK
        self._ptt_insert = 'clipboard'
        self._ptt_foreign_vks = tb._PTT_FOREIGN_VKS - {TRIGGER_VK}

    def start_processing_thread(self, frames, duration, **kwargs):
        self.starts.append(kwargs)
        return True


def tick(app, *vks):
    """One recording-loop tick with exactly `vks` physically down."""
    with vk_down(*vks):
        _FakeApp._ptt_tick(app)


def ptt_start(app):
    """Drive the double-tap-and-hold to START through the real tick."""
    tick(app)                 # baseline: trigger up
    tick(app, TRIGGER_VK)     # tap 1 down   -> TAP_HELD
    tick(app)                 # tap 1 up     -> ARMED
    tick(app, TRIGGER_VK)     # tap 2 down   -> HOLD_PENDING
    tick(app, TRIGGER_VK)     # min-hold met -> START -> recording, owned
    assert app.audio_recorder.is_recording and app._ptt_owns_recording, \
        "the fixture's own gesture did not start an owned recording"


# ======================================================================
# The foreign door
# ======================================================================

def test_foreign_stop_releases_ownership_and_spares_the_next_recording():
    # The acceptance case of #250: PTT records, Ctrl+Alt+H ends it on the hotkey
    # thread while the trigger stays physically down, and the user starts the next
    # recording without ever releasing.
    app = _FakeApp()
    ptt_start(app)
    _FakeApp.on_stop_recording_keyboard(app)
    assert not app.audio_recorder.is_recording, "the stop hotkey did not end the recording"
    assert len(app.starts) == 1, app.starts       # H's own processing, nothing else

    tick(app, TRIGGER_VK)                         # next tick, trigger still held
    assert app._ptt_owns_recording is False, \
        "stale PTT ownership survived a tick after the foreign stop (#250)"

    app.audio_recorder.is_recording = True        # a Ctrl+Alt+W recording, not PTT's
    tick(app, TRIGGER_VK)                         # still holding the trigger...
    tick(app)                                     # ...and releasing it
    tick(app)                                     # a full release, tail included
    assert app.audio_recorder.is_recording, \
        "the trigger release stopped a recording PTT does not own (#250)"
    assert len(app.starts) == 1, \
        f"a PTT stop fired for a foreign recording: {app.starts}"


def test_immediate_regesture_after_a_foreign_stop_records_again():
    # The half the flag alone would not fix: the detector is left in RECORDING, and a
    # re-gesture started right away is absorbed tick by tick -- every press cancels the
    # pending stop, every release re-arms it, and nothing ever starts.
    app = _FakeApp()
    ptt_start(app)
    _FakeApp.on_stop_recording_keyboard(app)
    tick(app, TRIGGER_VK)      # the guard tick: let go of the flag, reset the detector

    tick(app)                  # release -- a falling edge IDLE must ignore
    tick(app, TRIGGER_VK)      # tap 1
    tick(app)                  # tap 1 up  -> ARMED
    tick(app, TRIGGER_VK)      # tap 2     -> HOLD_PENDING
    tick(app, TRIGGER_VK)      # min-hold met -> START
    assert app.audio_recorder.is_recording, \
        "the re-gesture was swallowed by a detector stuck in RECORDING (#250)"
    assert app._ptt_owns_recording is True, "the new recording is not owned by PTT"


# ======================================================================
# The regression anchor
# ======================================================================

def test_owned_roundtrip_still_stops_on_trigger_release():
    # The guard must stay silent for the whole life of a recording PTT does own: the
    # gesture starts it, the trigger release stops it through the configured path.
    app = _FakeApp()
    ptt_start(app)
    tick(app)      # release -> the tail starts
    tick(app)      # tail met -> STOP -> _ptt_stop_and_insert
    assert not app.audio_recorder.is_recording, "the trigger release no longer stops"
    assert app._ptt_owns_recording is False, "ownership outlived its own stop"
    assert len(app.starts) == 1, app.starts
    assert app.starts[-1].get('use_clipboard') is True, \
        f"the PTT stop left its configured insert path: {app.starts[-1]}"


CASES = [
    test_foreign_stop_releases_ownership_and_spares_the_next_recording,
    test_immediate_regesture_after_a_foreign_stop_records_again,
    test_owned_roundtrip_still_stops_on_trigger_release,
]


def main():
    failures = []
    try:
        for case in CASES:
            try:
                case()
                print(f"PASS  {case.__name__}")
            except AssertionError as e:
                failures.append((case.__name__, str(e)))
                print(f"FAIL  {case.__name__}: {e}")
            except Exception as e:  # a crash is also a failure
                failures.append((case.__name__, f"{type(e).__name__}: {e}"))
                print(f"ERROR {case.__name__}: {type(e).__name__}: {e}")

        if failures:
            print(f"\nFAIL: {len(failures)}/{len(CASES)} case(s) failed")
            return 1
        print(f"\nOK: all {len(CASES)} push-to-talk ownership cases pass")
        return 0
    finally:
        # Both throwaway dirs are created at import and nothing reaches them past this
        # point, so a standalone run leaves no tb_ptt_own_* dir in /tmp.
        shutil.rmtree(_ARCHIVE, ignore_errors=True)
        shutil.rmtree(_LOG_DIR, ignore_errors=True)


def test_all():
    """The pytest entry point (#242). main()'s finally removes the shared archive dir;
    re-create it so collection order can never strand a later case."""
    try:
        assert main() == 0
    finally:
        _ARCHIVE.mkdir(exist_ok=True)


if __name__ == "__main__":
    sys.exit(main())
