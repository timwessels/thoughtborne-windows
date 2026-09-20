#!/usr/bin/env python3
"""The glue of the #335 hotkey suspend, driven against the real code.

    python3 test_hotkey_suspend_wiring.py    # verify, exit non-zero on failure

While the settings app's capture field arms, the running tool releases its global
hotkeys so a combo it holds itself can reach the field. `test_restart_signal.py`
owns the signal files and the pure decision (`decide_hotkey_suspend`); what is left
over is the glue between that decision and the running tool -- and glue is what
rots unwatched (the #152 lesson). So this driver takes the harness of
`test_ptt_ownership.py` / `test_stop_rebind_wiring.py` -- `thoughtborne` imported
under faked Win32/GUI modules, the real method bound onto a fake `self` -- and runs
the genuine `ThoughtborneApp._hotkey_suspend_tick` against a scriptable stand-in
for the hotkey manager and a real request file in a temp directory.

What it pins, all of it invisible to the pure decision:

  - A tick acts once. A request that is still there on the next tick does not
    release a second time, and an idle tick touches nothing at all.
  - A post that fails leaves the state untouched, so the next tick tries again --
    in both directions. This is what makes the Win32 post allowed to fail: before
    the listener thread has a message queue it simply does, and the tick after it
    succeeds.
  - The failsafe takes the request back BEFORE it resumes (proven by looking at the
    disk from inside the resume call, not by reading the source): with the file
    still lying there, the very next tick would release the hotkeys again.
  - A request that cannot be deleted at all leaves the tool suspended and retrying,
    rather than flapping between released and registered every 150 ms -- the mirror
    of #202's "no shutdown without a successful consume".
  - No manager yet (the recording loop starts before hotkey registration) is a
    complete no-op, not an AttributeError in the thread that carries the W-flow.

Plus the invariants on the manager side that need no Windows to check: the two
private messages are distinct values that cannot collide with WM_HOTKEY or WM_QUIT
and are posted by the right method each (a swap would make a suspend re-register),
a post is refused while there is no listener thread to post to, and a completion
hook that throws is contained instead of taking the message pump down with it.

The last of them is an order, and it is checked the same way as the failsafe above
-- by looking at the disk from inside the call, not at the source. The real message
pump runs here with Win32 faked around it and the tool's own hooks hung on it, so
the ACK file is written and removed for real: it must be gone before the first
RegisterHotKey of a resume, because an ACK lying there next to a live combo is
exactly what would let the capture field arm on hotkeys the tool has taken back.

What stays hands-on: that `UnregisterHotKey` really frees the combo system-wide and
that the re-registration takes it back. The Win32 probe of 2026-09-20 confirmed both
on the real machine, and daily use carries them from here.
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
# test_ptt_ownership.py: audio_handler pulls in msvcrt/pyaudio, hotkey_manager and
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
# goes by (#267). thoughtborne binds both values at import, so they have to move first.
_LOG_DIR = Path(tempfile.mkdtemp(prefix="tb_suspend_log_"))     # removed in main()
_ARCHIVE = Path(tempfile.mkdtemp(prefix="tb_suspend_arch_"))    # removed in main()
config.LOG_FILE = _LOG_DIR / "thoughtborne.log"
config.ARCHIVE_FOLDER = _ARCHIVE
ah.ARCHIVE_FOLDER = _ARCHIVE
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

import hotkey_manager  # noqa: E402
import restart_signal as rs  # noqa: E402
import thoughtborne as tb  # noqa: E402

# Undo thoughtborne's import-time global side effects: it swaps sys.stdout/stderr for
# logger wrappers and attaches handlers. Restore the streams, detach every handler, and
# re-assert the silence its module-level logging setup cleared.
sys.stdout = tb.original_stdout
sys.stderr = tb.original_stderr
for _name in ("Thoughtborne", "Thoughtborne.stdio", "Thoughtborne.console",
              "Thoughtborne.HotkeyManager"):
    _lg = logging.getLogger(_name)
    for _h in list(_lg.handlers):
        _lg.removeHandler(_h)
    _lg.setLevel(logging.CRITICAL)
tb.ARCHIVE_FOLDER = _ARCHIVE

TIMEOUT = rs.SUSPEND_RESUME_TIMEOUT_SECONDS


# ---- harness ---------------------------------------------------------------
class _FakeManager:
    """The hotkey manager as the tick sees it: two post-only calls that report
    whether the message was queued. `on_resume` is the peephole for lane order --
    it runs inside resume(), so it can see the disk as the resume sees it."""

    def __init__(self, suspend_ok=True, resume_ok=True, on_resume=None):
        self.suspend_ok = suspend_ok
        self.resume_ok = resume_ok
        self.on_resume = on_resume
        self.calls = []

    def suspend(self):
        self.calls.append("suspend")
        return self.suspend_ok

    def resume(self):
        self.calls.append("resume")
        if self.on_resume is not None:
            self.on_resume()
        return self.resume_ok


class _FakeApp:
    """The collaborator surface the tick touches. The method under test is the real
    ThoughtborneApp one, bound as a class attribute."""
    _hotkey_suspend_tick = tb.ThoughtborneApp._hotkey_suspend_tick

    def __init__(self, base_dir, manager):
        self.hotkey_manager = manager
        self._suspend_request_path = rs.suspend_request_path(base_dir)
        self._suspend_ack_path = rs.suspend_ack_path(base_dir)
        self._hotkeys_suspended_since = None

    # the two moves the settings app makes, as the tool sees them on disk
    def request(self):
        rs.request_hotkeys_suspend(self._suspend_request_path)

    def withdraw(self):
        rs.clear_signal(self._suspend_request_path)


@contextlib.contextmanager
def app(manager=None, **kw):
    """A fake app on its own temp directory, cleaned up whatever the lane does."""
    d = tempfile.mkdtemp(prefix="tb_suspend_wiring_")
    try:
        yield _FakeApp(d, _FakeManager(**kw) if manager is None else manager)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def tick(a, now):
    _FakeApp._hotkey_suspend_tick(a, now)


# ======================================================================
# The normal cycle
# ======================================================================

def test_a_request_releases_once_and_a_withdrawal_takes_it_back():
    with app() as a:
        tick(a, 100.0)
        assert a.hotkey_manager.calls == [], "an idle tick posted something"
        assert a._hotkeys_suspended_since is None

        a.request()
        tick(a, 100.0)
        assert a.hotkey_manager.calls == ["suspend"], a.hotkey_manager.calls
        assert a._hotkeys_suspended_since == 100.0, "the suspend was not stamped"

        tick(a, 100.2)      # the request is still there: the capture is running
        assert a.hotkey_manager.calls == ["suspend"], \
            f"a standing request released a second time: {a.hotkey_manager.calls}"
        assert a._hotkeys_suspended_since == 100.0, "the stamp moved under a standing request"

        a.withdraw()
        tick(a, 100.4)
        assert a.hotkey_manager.calls == ["suspend", "resume"], a.hotkey_manager.calls
        assert a._hotkeys_suspended_since is None, "the tool still counts as suspended"

        tick(a, 100.6)
        assert a.hotkey_manager.calls == ["suspend", "resume"], \
            f"the finished cycle kept posting: {a.hotkey_manager.calls}"


# ======================================================================
# A post that does not get through
# ======================================================================

def test_a_refused_suspend_is_retried_instead_of_being_assumed():
    # The listener has no message queue yet (the loop runs before registration), so
    # the post is refused. Believing it anyway would leave the tool convinced its
    # hotkeys are gone while they are still live -- and never resuming them.
    with app(suspend_ok=False) as a:
        a.request()
        tick(a, 10.0)
        assert a._hotkeys_suspended_since is None, "a refused suspend was recorded as done"
        tick(a, 10.2)
        assert a.hotkey_manager.calls == ["suspend", "suspend"], \
            f"the refused suspend was not retried: {a.hotkey_manager.calls}"

        a.hotkey_manager.suspend_ok = True
        tick(a, 10.4)
        assert a._hotkeys_suspended_since == 10.4, "the retry that got through was not stamped"


def test_a_refused_resume_stays_suspended_and_retries():
    with app(resume_ok=False) as a:
        a.request()
        tick(a, 20.0)
        a.withdraw()
        tick(a, 20.2)
        assert a._hotkeys_suspended_since == 20.0, \
            "a refused resume dropped the state -- nothing would ever register again"
        tick(a, 20.4)
        assert a.hotkey_manager.calls.count("resume") == 2, \
            f"the refused resume was not retried: {a.hotkey_manager.calls}"

        a.hotkey_manager.resume_ok = True
        tick(a, 20.6)
        assert a._hotkeys_suspended_since is None, "the successful retry was not recorded"


# ======================================================================
# The one failsafe
# ======================================================================

def test_the_failsafe_withdraws_the_request_before_it_resumes():
    seen = {}
    with app() as a:
        a.hotkey_manager.on_resume = lambda: seen.setdefault(
            "request_on_disk", a._suspend_request_path.exists())
        a.request()
        tick(a, 0.0)
        assert a.hotkey_manager.calls == ["suspend"]

        tick(a, TIMEOUT - 0.1)
        assert a.hotkey_manager.calls == ["suspend"], "the failsafe fired too early"

        tick(a, TIMEOUT)
        assert a.hotkey_manager.calls == ["suspend", "resume"], a.hotkey_manager.calls
        assert seen.get("request_on_disk") is False, \
            "the failsafe resumed with the request still on disk -- the next tick " \
            "would release the hotkeys again"
        assert not a._suspend_request_path.exists(), "the abandoned request survived"
        assert a._hotkeys_suspended_since is None


def test_an_undeletable_request_keeps_the_tool_suspended_instead_of_flapping():
    import os

    with app() as a:
        a.request()
        tick(a, 0.0)

        original = os.remove

        def _denied(_p):
            raise PermissionError(13, "Permission denied")

        rs.os.remove = _denied
        try:
            tick(a, TIMEOUT + 1.0)
            tick(a, TIMEOUT + 2.0)
        finally:
            rs.os.remove = original

        assert a.hotkey_manager.calls == ["suspend"], \
            f"resumed against a request that is still on disk: {a.hotkey_manager.calls}"
        assert a._hotkeys_suspended_since == 0.0, "the state moved without a resume"

        tick(a, TIMEOUT + 3.0)      # the lock is gone: the retry gets through
        assert a.hotkey_manager.calls == ["suspend", "resume"], a.hotkey_manager.calls
        assert a._hotkeys_suspended_since is None


# ======================================================================
# Before there is anything to post to
# ======================================================================

def test_no_manager_yet_is_a_complete_no_op():
    # run() starts the recording loop before it registers the hotkeys, so the first
    # ticks of every start find hotkey_manager None. This runs inside the thread that
    # carries the whole W-flow: it must not raise, and it must not invent a state.
    with app(manager=None) as a:
        a.hotkey_manager = None
        a.request()
        tick(a, 1.0)
        tick(a, 1.0 + TIMEOUT + 1.0)
        assert a._hotkeys_suspended_since is None, "a tick without a manager stamped a suspend"
        assert a._suspend_request_path.exists(), \
            "a tick without a manager consumed the request nobody can act on"


# ======================================================================
# The manager side that needs no Windows
# ======================================================================

def test_the_two_private_messages_cannot_be_confused():
    values = (hotkey_manager.WM_APP_SUSPEND_HOTKEYS, hotkey_manager.WM_APP_RESUME_HOTKEYS)
    assert values[0] != values[1], "suspend and resume post the same message"
    for value in values:
        assert value >= hotkey_manager.WM_APP, f"{value:#06x} is below the WM_APP range"
        assert value not in (hotkey_manager.WM_HOTKEY, hotkey_manager.WM_QUIT), \
            f"{value:#06x} collides with a system message the pump already handles"


def test_suspend_and_resume_post_their_own_message():
    # A swap here would be quiet and awful: a suspend would register everything again
    # in the middle of a capture. The post is faked; the message value is the point.
    hm = hotkey_manager.HotkeyManager()
    posted = []
    original = hotkey_manager.PostThreadMessageW
    hotkey_manager.PostThreadMessageW = lambda tid, msg, w, l: posted.append((tid, msg)) or 1
    try:
        assert hm.suspend() is False, "posted into a listener thread that does not exist"
        assert hm.resume() is False, "posted into a listener thread that does not exist"
        assert posted == [], f"a post went out before the listener was ready: {posted}"

        hm._thread = object()
        hm._thread_id = 4711
        hm._started.set()
        assert hm.suspend() is True
        assert hm.resume() is True
    finally:
        hotkey_manager.PostThreadMessageW = original
    assert posted == [(4711, hotkey_manager.WM_APP_SUSPEND_HOTKEYS),
                      (4711, hotkey_manager.WM_APP_RESUME_HOTKEYS)], posted


def test_a_throwing_completion_hook_cannot_take_the_pump_down():
    hm = hotkey_manager.HotkeyManager()

    def _boom():
        raise RuntimeError("the ACK file is on a vanished network drive")

    hm._notify(_boom, "on_suspended")       # contained, not raised
    hm._notify(None, "on_resuming")         # an unset hook is simply nothing to do


def test_the_resume_clears_the_ack_before_the_first_registration():
    # The one ordering the ACK's whole meaning rests on, driven through the REAL
    # message pump with Win32 faked around it: the hooks are the tool's own (they
    # write and remove a real file), and the fake RegisterHotKey reports what the
    # disk looked like at the moment a combo came back. Registering first and
    # clearing after leaves a window -- milliseconds normally, forever if the
    # removal fails -- in which the ACK promises a release that is already over.
    d = Path(tempfile.mkdtemp(prefix="tb_suspend_pump_"))
    try:
        ack = rs.suspend_ack_path(d)
        hm = hotkey_manager.HotkeyManager()
        hm.register("ctrl+alt+w", lambda: None, name="start_recording")
        # verbatim the wiring thoughtborne.py hangs on the manager
        hm.on_suspended = lambda: rs.acknowledge_hotkeys_suspended(ack)
        hm.on_resuming = lambda: rs.clear_signal(ack)

        at_register = []        # ack.exists() as each registration goes out
        between = []            # ... and each time the pump asks for the next message
        script = [hotkey_manager.WM_APP_SUSPEND_HOTKEYS, hotkey_manager.WM_APP_RESUME_HOTKEYS]

        def _get_message(lp_msg, *_rest):
            # The pump asks for a message only once it has finished the previous one,
            # so this is the view of the disk between the two branches.
            between.append(ack.exists())
            if not script:
                return 0                    # WM_QUIT: leave the pump
            msg = ctypes.cast(lp_msg, ctypes.POINTER(ctypes.wintypes.MSG)).contents
            msg.message = script.pop(0)
            msg.wParam = 0
            return 1

        saved = (hotkey_manager.GetMessageW, hotkey_manager.RegisterHotKey,
                 hotkey_manager.UnregisterHotKey, hotkey_manager.GetCurrentThreadId)
        hotkey_manager.GetMessageW = _get_message
        hotkey_manager.RegisterHotKey = lambda *a: at_register.append(ack.exists()) or 1
        hotkey_manager.UnregisterHotKey = lambda *a: 1
        hotkey_manager.GetCurrentThreadId = lambda: 4711
        try:
            hm._listener_thread()           # runs right here; no thread involved
        finally:
            (hotkey_manager.GetMessageW, hotkey_manager.RegisterHotKey,
             hotkey_manager.UnregisterHotKey, hotkey_manager.GetCurrentThreadId) = saved

        assert between == [False, True, False], (
            "the ACK file did not follow the two branches (expected: absent at start, "
            f"present after the suspend, absent again after the resume): {between}")
        assert len(at_register) == 2, \
            f"expected two registrations (the start and the resume), got {len(at_register)}"
        assert not any(at_register), (
            "a hotkey was registered again while the ACK still lay on disk -- for that "
            "moment the settings app would arm its capture field on hotkeys the tool "
            "has already taken back")
        assert not ack.exists(), "the pump left an ACK behind"
    finally:
        shutil.rmtree(d, ignore_errors=True)


CASES = [
    test_a_request_releases_once_and_a_withdrawal_takes_it_back,
    test_a_refused_suspend_is_retried_instead_of_being_assumed,
    test_a_refused_resume_stays_suspended_and_retries,
    test_the_failsafe_withdraws_the_request_before_it_resumes,
    test_an_undeletable_request_keeps_the_tool_suspended_instead_of_flapping,
    test_no_manager_yet_is_a_complete_no_op,
    test_the_two_private_messages_cannot_be_confused,
    test_suspend_and_resume_post_their_own_message,
    test_a_throwing_completion_hook_cannot_take_the_pump_down,
    test_the_resume_clears_the_ack_before_the_first_registration,
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
        print(f"\nOK: all {len(CASES)} hotkey-suspend wiring cases pass")
        return 0
    finally:
        # Both throwaway dirs are created at import and nothing reaches them past this
        # point, so a standalone run leaves no tb_suspend_* dir in /tmp.
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
