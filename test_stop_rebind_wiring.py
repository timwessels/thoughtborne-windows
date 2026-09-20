#!/usr/bin/env python3
"""Wiring tests for the rebind-aware insert guard, mis-trigger net and stop
debounce (#152).

    python3 test_stop_rebind_wiring.py    # verify, exit non-zero on failure

`test_hotkey_overrides.py` proves the two pure derivations (`combo_keys`,
`config.mistrigger_key_map`). What it cannot prove is that the app *uses* them --
the gap `test_typed_cap_wiring.py` closes for the typed cap. So this driver runs
the REAL hotkey callbacks against a fake `self`, importing `thoughtborne` with
faked Win32/GUI modules the way `test_retry_marker_lifecycle.py` does, with
`HOTKEYS` rebound per case and `is_key_pressed` scripted. Every assertion is on
what reaches a collaborator: the `wait_for_keys` handed to
`start_processing_thread` / `insert_last_transcript`, the `trigger_keys` on the
retry task, whether `insert_last_transcript` was called at all, and which handler
a mis-trigger picks.

What it pins:

  - The release-wait lists derive from the EFFECTIVE combo at all seven sites --
    unchanged under the shipped scheme (except the send flow, below), the full
    combo under a rebind, and a bare binding's single key, which is the case the
    literal lists used to make structurally inert.
  - The send flow now waits on its key token too, not the modifiers alone: a
    deliberate behaviour change of the shipped scheme (#152), pinned here so it
    is a decision rather than a drift.
  - The push-to-talk stop path waits on the CONFIGURED trigger -- the one wait
    list whose source is `push_to_talk.trigger` rather than a hotkey combo, and
    whose former literal `['ctrl']` looked right under the default trigger.
  - What each of the five stop flows DELIVERS (#330): the effective
    use_clipboard / auto_insert / send_after_insert -- negatives included -- and
    the transcriber_override / sidecar handover, measured against the callees'
    real signature defaults; the Y flow's missing wait list, missing
    insert-later branch and missing debounce slot; the push-to-talk stop end to
    end in all four insert modes.
  - Each stop action debounces only ITS own insert-last-text branch. The old two
    flags were shared across actions, so a stop with one hotkey swallowed
    another's insert for two seconds; the cross case is pinned as *working* now.
    The window expires on time alone, so no clearing thread is involved.
  - The mis-trigger net polls the effective keys, skips the ambiguous and the
    start-key entries (empty map under the shipped F-key preset), and runs the
    corrected handler end to end -- down to that handler's own derived wait list.
  - The D-029 toggle dispatch (#336): with a stop action on the start combo, the
    second press delivers what that action's own key delivers -- measured against
    a direct press of it -- and the third starts again, while the mis-trigger net
    still outranks the toggle and an unconfigured toggle changes nothing.
  - The two FILE_ONLY log lines that used to name shipped keys now name the
    effective combos (D-019).
  - A static guard that no literal `wait_for_keys=['...']` / `trigger_keys=['...']`
    can come back unnoticed.
"""
import contextlib
import ctypes
import inspect
import logging
import re
import shutil
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

# ---- Windows-only / third-party modules the import chain needs, faked exactly
# as in test_retry_marker_lifecycle.py: audio_handler pulls in msvcrt/pyaudio,
# hotkey_manager and output_handler configure argtypes on ctypes.windll handles
# at import, output_handler imports keyboard/pyperclip/pyautogui. None of it is
# exercised here -- only enough to let the real callbacks run off-Windows.
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

# transcriber imports `Groq` and `AuthenticationError` at module level. The real
# SDK is a test dependency (run_tests, CI), so prefer it and stub only the two
# names when it is absent -- no lane here touches groq either way.
try:
    import groq  # noqa: E402,F401
except ImportError:
    _fake_groq = types.ModuleType("groq")
    _fake_groq.Groq = type("Groq", (), {"__init__": lambda self, *a, **k: None})
    _fake_groq.AuthenticationError = type("AuthenticationError", (Exception,), {})
    sys.modules.setdefault("groq", _fake_groq)

import audio_handler as ah  # noqa: E402
import config  # noqa: E402
import settings_io  # noqa: E402

# Throwaway dirs, both BEFORE `import thoughtborne`: that import attaches a
# RotatingFileHandler to config.LOG_FILE and creates the file where none exists,
# which in a fresh clone fakes the "tool is running" heartbeat a reader of
# thoughtborne.log goes by (#267). thoughtborne binds both values at import, so
# they have to move first, not after.
_LOG_DIR = Path(tempfile.mkdtemp(prefix="tb_stop_rebind_log_"))     # removed in main()
_ARCHIVE = Path(tempfile.mkdtemp(prefix="tb_stop_rebind_arch_"))    # removed in main()
config.LOG_FILE = _LOG_DIR / "thoughtborne.log"
config.ARCHIVE_FOLDER = _ARCHIVE
ah.ARCHIVE_FOLDER = _ARCHIVE
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

import thoughtborne as tb  # noqa: E402

# Undo thoughtborne's import-time global side effects: it swaps sys.stdout/stderr
# for logger wrappers and attaches handlers. Restore the streams, detach every
# handler, and re-assert the silence its module-level logging setup cleared.
sys.stdout = tb.original_stdout
sys.stderr = tb.original_stderr
for _name in ("Thoughtborne", "Thoughtborne.stdio", "Thoughtborne.console"):
    _lg = logging.getLogger(_name)
    for _h in list(_lg.handlers):
        _lg.removeHandler(_h)
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)
tb.ARCHIVE_FOLDER = _ARCHIVE

# The shipped scheme as an own fixture -- NOT config.HOTKEYS, which this
# checkout's personal_settings.json may already have overridden.
SHIPPED = dict(config.DEFAULT_HOTKEYS)


# ---- harness ---------------------------------------------------------------
@contextlib.contextmanager
def scheme(**overrides):
    """Run the body against the shipped scheme plus `overrides` as the effective
    HOTKEYS. Rebinding the module attribute is what a rebind looks like to every
    derivation under test -- they all read it at call time, which is the reason
    the map and the lists are built there and not at import."""
    previous = tb.HOTKEYS
    tb.HOTKEYS = dict(SHIPPED, **overrides)
    try:
        yield
    finally:
        tb.HOTKEYS = previous


@contextlib.contextmanager
def raw_scheme(hotkeys):
    """scheme(), but for a complete foreign scheme (e.g. the F-key preset)."""
    previous = tb.HOTKEYS
    tb.HOTKEYS = dict(hotkeys)
    try:
        yield
    finally:
        tb.HOTKEYS = previous


@contextlib.contextmanager
def pressed(*keys):
    """Script is_key_pressed: exactly `keys` read as physically down."""
    down = {k.lower() for k in keys}
    previous = tb.is_key_pressed
    tb.is_key_pressed = lambda name: name.lower() in down
    try:
        yield
    finally:
        tb.is_key_pressed = previous


@contextlib.contextmanager
def ptt_trigger(name):
    """Run the body with `name` as the configured push-to-talk trigger.
    `_ptt_insert_kwargs` reads the module global at call time, just as the
    HOTKEYS derivations read theirs, so rebinding the attribute is what a
    different `push_to_talk.trigger` looks like to it."""
    previous = tb.PTT_TRIGGER
    tb.PTT_TRIGGER = name
    try:
        yield
    finally:
        tb.PTT_TRIGGER = previous


class _Capture(logging.Handler):
    """Collects formatted messages at INFO and above."""
    def __init__(self):
        super().__init__(logging.INFO)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def captured_logs():
    """Yield a list that fills with the log messages emitted inside the body
    (the module keeps the 'Thoughtborne' logger at CRITICAL otherwise)."""
    lg = logging.getLogger("Thoughtborne")
    level, cap = lg.level, _Capture()
    lg.setLevel(logging.INFO)
    lg.addHandler(cap)
    try:
        yield cap.messages
    finally:
        lg.removeHandler(cap)
        lg.setLevel(level)


class _FakeAudio:
    def __init__(self, recording):
        self.is_recording = recording
        self.cancelled = False
        # A recognizable object rather than None, so the handover assertions can
        # ask for identity: a stop flow that drops `sidecar` would hand the
        # worker None and read as working (#330).
        self.sidecar_handle = object()

    def start_recording(self):
        self.is_recording = True
        return True

    def stop_recording(self):
        self.is_recording = False
        return ([], 1.0)

    def take_finished_sidecar(self):
        return self.sidecar_handle

    def cancel_recording(self):
        self.cancelled = True


class _FakeOutput:
    def __init__(self):
        self.inserts = []   # insert_last_transcript kwargs, in call order
        self.tasks = []

    def insert_last_transcript(self, **kwargs):
        self.inserts.append(kwargs)

    def add_task(self, task):
        self.tasks.append(task)

    def update_last_transcript(self, transcript):
        pass


class _FakeTranscriber:
    """A file-capable engine that finds nothing: the retry worker's shortest
    path to the task it hands the OutputManager, which is what carries the
    trigger_keys under test."""
    is_live = False

    def get_name(self):
        return "fake"

    def transcribe(self, path, duration, error_sink=None):
        return ""


class _FakeApp:
    """The collaborator surface the real callbacks touch. The methods under test
    are the genuine ThoughtborneApp ones, bound as class attributes."""
    _show = tb.ThoughtborneApp._show
    _wait_keys = tb.ThoughtborneApp._wait_keys
    _stop_debounce_elapsed = tb.ThoughtborneApp._stop_debounce_elapsed
    _handle_mistrigger_during_recording = tb.ThoughtborneApp._handle_mistrigger_during_recording
    _stop_handlers = tb.ThoughtborneApp._stop_handlers
    on_start_recording = tb.ThoughtborneApp.on_start_recording
    _stop_prologue = tb.ThoughtborneApp._stop_prologue
    _stop_action = tb.ThoughtborneApp._stop_action
    on_stop_recording_keyboard = tb.ThoughtborneApp.on_stop_recording_keyboard
    on_stop_recording_clipboard = tb.ThoughtborneApp.on_stop_recording_clipboard
    on_stop_recording_send = tb.ThoughtborneApp.on_stop_recording_send
    on_stop_recording_no_insert = tb.ThoughtborneApp.on_stop_recording_no_insert
    on_cancel_recording = tb.ThoughtborneApp.on_cancel_recording
    _ptt_stop_and_insert = tb.ThoughtborneApp._ptt_stop_and_insert
    _ptt_insert_kwargs = tb.ThoughtborneApp._ptt_insert_kwargs
    retry_recording_thread = tb.ThoughtborneApp.retry_recording_thread
    _record_failed_slot = tb.ThoughtborneApp._record_failed_slot

    def __init__(self, recording=False):
        self.audio_recorder = _FakeAudio(recording)
        self.output_manager = _FakeOutput()
        self.transcriber = _FakeTranscriber()
        self._active_live_transcriber = None
        # What the start half of on_start_recording reads: a live recording loop
        # with a fresh tick, so the #128 wedge guard lets the start through.
        self._keyless = False
        self.recording_thread = types.SimpleNamespace(is_alive=lambda: True)
        self._recording_loop_last_tick = time.monotonic()
        self._stop_insert_debounce = {}
        self.processing_lock = threading.Lock()
        self.processing_counter = 0
        self._last_failed_lock = threading.Lock()
        self._last_failed = None
        self.starts = []   # start_processing_thread kwargs, in call order

    @property
    def inserts(self):
        """The insert_last_transcript calls, for readable assertions."""
        return self.output_manager.inserts

    # --- fakes -------------------------------------------------------------
    def start_processing_thread(self, frames, duration, **kwargs):
        self.starts.append(kwargs)
        return True

    def _emit_block(self, *a, **k):
        pass

    def _ticker(self, *a, **k):
        pass

    def get_unique_timestamp(self):
        return "20260919_120000_001"

    def _resolve_failed_slot(self, rec):
        pass


def _stop(app, action):
    """Fire the stop callback of `action` on `app`."""
    {'stop_recording_keyboard': _FakeApp.on_stop_recording_keyboard,
     'stop_recording_clipboard': _FakeApp.on_stop_recording_clipboard,
     'stop_recording_send': _FakeApp.on_stop_recording_send,
     'stop_recording_no_insert': _FakeApp.on_stop_recording_no_insert}[action](app)


def _ptt_kwargs(insert_mode):
    """The kwargs the PTT stop path hands start_processing_thread under a given
    `push_to_talk.insert` mode. The mode is set on the fake rather than read from
    config, so this checkout's personal_settings.json cannot decide which branch
    the lane exercises."""
    app = _FakeApp()
    app._ptt_insert = insert_mode
    return _FakeApp._ptt_insert_kwargs(app)


def _signature_defaults(func):
    """`func`'s parameter defaults as a dict, read from the real signature so a
    default flipped there reaches the pins below."""
    return {name: parameter.default
            for name, parameter in inspect.signature(func).parameters.items()
            if parameter.default is not inspect.Parameter.empty}


_SPT_DEFAULTS = _signature_defaults(tb.ThoughtborneApp.start_processing_thread)
_ILT_DEFAULTS = _signature_defaults(tb.OutputManager.insert_last_transcript)


def _effective(kwargs, defaults=_SPT_DEFAULTS):
    """The full kwarg set the callee runs with: its signature defaults overlaid
    with what the flow passed. Whether a flow spells a default out or leaves it
    to the callee is invisible on the other side, and the two spellings are
    mixed today (the Y stop passes use_clipboard=False, the H stop does not), so
    the deliver pins measure the effective value rather than dict membership."""
    effective = dict(defaults)
    effective.update(kwargs)
    return effective


# ======================================================================
# The release-wait lists (spec point 1)
# ======================================================================

def test_stop_branch_waits_on_the_effective_combo():
    # Shipped scheme: byte-identical to the literals it replaces -- the
    # regression anchor for the two typed/clipboard flows.
    for action, expected in (('stop_recording_keyboard', ['ctrl', 'alt', 'h']),
                             ('stop_recording_clipboard', ['ctrl', 'alt', 'a'])):
        app = _FakeApp(recording=True)
        with scheme():
            _stop(app, action)
        assert app.starts and app.starts[-1]['wait_for_keys'] == expected, \
            (action, app.starts)
    # A rebind reaches it: the full combo, modifiers included.
    app = _FakeApp(recording=True)
    with scheme(stop_recording_keyboard='ctrl+shift+f10'):
        _stop(app, 'stop_recording_keyboard')
    assert app.starts[-1]['wait_for_keys'] == ['ctrl', 'shift', 'f10'], app.starts
    # A BARE binding -- the case the literal list made structurally inert
    # (waiting on ctrl+alt+a while the user holds F10 and nothing else).
    app = _FakeApp(recording=True)
    with scheme(stop_recording_clipboard='f10'):
        _stop(app, 'stop_recording_clipboard')
    assert app.starts[-1]['wait_for_keys'] == ['f10'], app.starts
    assert app.starts[-1]['use_clipboard'] is True, app.starts


def test_send_branch_waits_on_its_key_token_too():
    # The deliberate behaviour change of #152: the send flow used to wait on
    # ['ctrl', 'alt'] with no key token, so a still-held D could meet the Enter
    # that follows the paste. It now derives like every other stop action.
    app = _FakeApp(recording=True)
    with scheme():
        _stop(app, 'stop_recording_send')
    kwargs = app.starts[-1]
    assert kwargs['wait_for_keys'] == ['ctrl', 'alt', 'd'], kwargs
    assert kwargs['send_after_insert'] is True and kwargs['use_clipboard'] is True
    app = _FakeApp(recording=True)
    with scheme(stop_recording_send='shift+num3'):
        _stop(app, 'stop_recording_send')
    assert app.starts[-1]['wait_for_keys'] == ['shift', 'num3'], app.starts


def test_insert_later_branch_waits_on_the_effective_combo():
    # Not recording and no recent stop of its own -> the insert-last-text branch,
    # whose wait list is derived from the same action's combo.
    for action, override, expected in (
            ('stop_recording_keyboard', 'ctrl+alt+pageup', ['ctrl', 'alt', 'pageup']),
            ('stop_recording_clipboard', 'f10', ['f10']),
            ('stop_recording_send', 'ctrl+shift+f2', ['ctrl', 'shift', 'f2'])):
        app = _FakeApp(recording=False)
        with scheme(**{action: override}):
            _stop(app, action)
        assert len(app.inserts) == 1, (action, app.inserts)
        assert app.inserts[-1]['wait_for_keys'] == expected, (action, app.inserts)
    # The send flow keeps its two flags on this branch as well.
    assert app.inserts[-1]['send_after_insert'] is True, app.inserts
    assert app.inserts[-1]['use_clipboard'] is True, app.inserts


def test_retry_task_waits_on_the_effective_combo():
    # The seventh site: retry_recording_thread builds its TranscriptionTask
    # itself, so it never passed through start_processing_thread's kwargs.
    app = _FakeApp()
    rec = tb._FailedRecording(
        archived_mp3_path=str(_ARCHIVE / "voice_20260919_115900_001.mp3"),
        duration=1.0, origin_timestamp="20260919_115900_001")
    with scheme(retry_last_failed='ctrl+alt+f8'):
        _FakeApp.retry_recording_thread(app, rec, 7, _FakeTranscriber())
    task = app.output_manager.tasks[-1]
    assert task.trigger_keys == ['ctrl', 'alt', 'f8'], task.trigger_keys
    assert task.wait_for_key_release is True, "the retry task stopped waiting at all"


def test_ptt_branch_waits_on_the_configured_trigger():
    # The PTT stop has no hotkey combo behind it: its wait list names the trigger
    # the user configured. The literal ['ctrl'] it replaces (#152) waited on a key
    # the 'lalt' user never holds, leaving the Alt that IS still down unwatched --
    # and it read perfectly fine under the default 'lctrl', so only a pin on the
    # 'lalt' value keeps the derivation from silently reverting. Both Ctrl sides
    # expect the COMBINED name because is_key_pressed knows no side-specific one.
    for mode in ('clipboard', 'send', 'type'):
        for trigger, expected in (('lalt', ['alt']),
                                  ('lctrl', ['ctrl']),
                                  ('rctrl', ['ctrl'])):
            with ptt_trigger(trigger):
                kwargs = _ptt_kwargs(mode)
            assert kwargs['wait_for_keys'] == expected, (mode, trigger, kwargs)


# ======================================================================
# What each stop flow delivers (#330)
# ======================================================================

def test_deliver_matrix_of_the_four_stop_flows():
    # A stop action IS its deliver kwargs, and the negatives are half of that:
    # use_clipboard=False is what keeps the typed flow the documented
    # paste-blocked fallback route (D-019), and auto_insert / transcriber_override
    # / sidecar reached no driver at all before this one.
    expected = {
        'stop_recording_keyboard': dict(use_clipboard=False, auto_insert=True,
                                        send_after_insert=False),
        'stop_recording_clipboard': dict(use_clipboard=True, auto_insert=True,
                                         send_after_insert=False),
        'stop_recording_send': dict(use_clipboard=True, auto_insert=True,
                                    send_after_insert=True),
        # Process only -- and no wait list either: the worker reads
        # `wait_for_keys is not None` as "wait for a release", so a list here
        # would change the task, not just its looks.
        'stop_recording_no_insert': dict(use_clipboard=False, auto_insert=False,
                                         send_after_insert=False, wait_for_keys=None),
    }
    for action, flags in expected.items():
        app = _FakeApp(recording=True)
        live = _FakeTranscriber()
        app._active_live_transcriber = live
        with scheme():
            _stop(app, action)
        effective = _effective(app.starts[-1])
        for flag, value in flags.items():
            assert effective[flag] is value, (action, flag, effective)
        # The live handover, captured BEFORE the field is cleared: the session
        # that recorded is the one that transcribes. Losing it degrades
        # invisibly -- the worker falls back to the current engine, which is the
        # right one in every case but the mid-recording engine switch.
        assert effective['transcriber_override'] is live, (action, effective)
        assert app._active_live_transcriber is None, action
        assert effective['sidecar'] is app.audio_recorder.sidecar_handle, (action, effective)
    # Shape pin, and deliberately no more: with no live session the flows still
    # pass an override -- the current engine rather than None, which the worker
    # would resolve to the same object anyway.
    app = _FakeApp(recording=True)
    with scheme():
        _stop(app, 'stop_recording_keyboard')
    assert app.starts[-1]['transcriber_override'] is app.transcriber, app.starts


def test_no_insert_flow_never_inserts_later():
    # The Y flow's three omissions, which together are one user-visible rule:
    # pressing it outside a recording does nothing at all. A stop body that
    # handed Y the insert-last-text branch would type the last transcript into
    # whatever has focus. The empty debounce map is a state pin -- a slot
    # written but never read would be unobservable on its own, but an
    # unconditional slot plus an unconditional branch is exactly the pair that
    # gives Y an insert.
    app = _FakeApp(recording=True)
    with scheme():
        _stop(app, 'stop_recording_no_insert')     # stops the recording
        assert app._stop_insert_debounce == {}, app._stop_insert_debounce
        _stop(app, 'stop_recording_no_insert')     # ...and again, not recording now
        _stop(app, 'stop_recording_no_insert')
    assert app.inserts == [], app.inserts
    assert len(app.starts) == 1, app.starts


def test_insert_later_branch_delivers_its_mode_flags():
    # The second half of every inserting flow delivers the same way its stop
    # branch does. Pinned here: that H stays typed on this route too (D-019) and
    # that neither H nor A sends.
    expected = {
        'stop_recording_keyboard': dict(use_clipboard=False, send_after_insert=False),
        'stop_recording_clipboard': dict(use_clipboard=True, send_after_insert=False),
        'stop_recording_send': dict(use_clipboard=True, send_after_insert=True),
    }
    for action, flags in expected.items():
        app = _FakeApp(recording=False)
        with scheme():
            _stop(app, action)
        assert len(app.inserts) == 1, (action, app.inserts)
        effective = _effective(app.inserts[-1], _ILT_DEFAULTS)
        for flag, value in flags.items():
            assert effective[flag] is value, (action, flag, effective)


def test_ptt_stop_delivers_the_handover_and_its_mode_kwargs():
    # The fifth stop path, run whole rather than through _ptt_insert_kwargs
    # alone: every insert mode delivers like its hotkey twin, the handover is
    # the same, and the two documented deviations hold -- the stop releases
    # ownership and touches no per-action debounce slot (#152). The gesture has
    # nothing to say about kwargs, so the method is called directly; the gesture
    # mechanics stay in test_ptt_ownership.py.
    expected = {
        'type': dict(use_clipboard=False, auto_insert=True, send_after_insert=False),
        'clipboard': dict(use_clipboard=True, auto_insert=True, send_after_insert=False),
        'send': dict(use_clipboard=True, auto_insert=True, send_after_insert=True),
        'no_insert': dict(use_clipboard=False, auto_insert=False,
                          send_after_insert=False, wait_for_keys=None),
    }
    for mode, flags in expected.items():
        app = _FakeApp(recording=True)
        app._ptt_insert = mode
        app._ptt_owns_recording = True
        live = _FakeTranscriber()
        app._active_live_transcriber = live
        with ptt_trigger('lctrl'):
            _FakeApp._ptt_stop_and_insert(app)
        effective = _effective(app.starts[-1])
        for flag, value in flags.items():
            assert effective[flag] is value, (mode, flag, effective)
        assert effective['transcriber_override'] is live, (mode, effective)
        assert app._active_live_transcriber is None, mode
        assert effective['sidecar'] is app.audio_recorder.sidecar_handle, (mode, effective)
        assert app._ptt_owns_recording is False, mode
        assert app._stop_insert_debounce == {}, (mode, app._stop_insert_debounce)


# ======================================================================
# The per-action debounce (spec point 3)
# ======================================================================

def test_stop_debounces_its_own_insert_later():
    # A stop press must not read as a deliberate insert-last-text press when the
    # finger lingers on the chord.
    app = _FakeApp(recording=True)
    with scheme():
        _stop(app, 'stop_recording_clipboard')     # stops the recording
        _stop(app, 'stop_recording_clipboard')     # immediately again
    assert app.inserts == [], app.inserts
    assert len(app.starts) == 1, app.starts


def test_stop_no_longer_swallows_another_actions_insert():
    # The user-visible fix: the two old flags were shared across actions, so a
    # stop with one hotkey muted another's insert for two seconds. A different
    # hotkey is never a bounce of this one.
    app = _FakeApp(recording=True)
    with scheme():
        _stop(app, 'stop_recording_clipboard')
        _stop(app, 'stop_recording_send')          # deliberate, right after
        _stop(app, 'stop_recording_keyboard')
    assert [i['wait_for_keys'] for i in app.inserts] == [['ctrl', 'alt', 'd'],
                                                         ['ctrl', 'alt', 'h']], \
        f"a stop swallowed another action's insert: {app.inserts}"
    # ...and the debounced action is still exactly the one that stopped.
    assert list(app._stop_insert_debounce) == ['stop_recording_clipboard'], \
        app._stop_insert_debounce


def test_debounce_window_expires_on_time_alone():
    # Backdating the slot is enough to reopen the branch: no clearing thread is
    # involved any more, so a wedged recording loop can no longer leave an
    # action's insert switched off until the next restart.
    app = _FakeApp(recording=True)
    with scheme():
        _stop(app, 'stop_recording_clipboard')
        _stop(app, 'stop_recording_clipboard')
        assert app.inserts == []
        app._stop_insert_debounce['stop_recording_clipboard'] = \
            time.time() - (tb.STOP_INSERT_DEBOUNCE_S + 1)
        _stop(app, 'stop_recording_clipboard')
    assert len(app.inserts) == 1, app.inserts


# ======================================================================
# The mis-trigger net (spec point 2)
# ======================================================================

def test_mistrigger_fires_the_rebound_key():
    # A rebound stop key held while the start hotkey fires: the net must poll the
    # effective key, and the corrected handler must run end to end -- down to its
    # own derived wait list.
    app = _FakeApp(recording=True)
    with scheme(stop_recording_send='ctrl+shift+f2'), pressed('f2'):
        assert _FakeApp._handle_mistrigger_during_recording(app) is True, \
            "the net did not see the rebound stop key -- it polls stale letters"
    kwargs = app.starts[-1]
    assert kwargs['send_after_insert'] is True
    assert kwargs['wait_for_keys'] == ['ctrl', 'shift', 'f2'], kwargs
    # Shipped scheme, unchanged behaviour: 'a' corrects to the clipboard stop.
    app = _FakeApp(recording=True)
    with scheme(), pressed('a'):
        assert _FakeApp._handle_mistrigger_during_recording(app) is True, \
            "the shipped 'a' no longer corrects to the clipboard stop"
    assert app.starts[-1]['wait_for_keys'] == ['ctrl', 'alt', 'a']
    assert app.starts[-1]['use_clipboard'] is True, app.starts
    # Cancel is reachable through the net too, and ends the recording without a
    # processing thread.
    app = _FakeApp(recording=True)
    with scheme(), pressed('x'):
        assert _FakeApp._handle_mistrigger_during_recording(app) is True, \
            "the shipped 'x' no longer corrects to cancel"
    assert app.audio_recorder.cancelled is True and app.starts == [], app.starts


def test_mistrigger_stays_silent_where_the_map_is_empty():
    # Nothing held -> nothing to correct.
    app = _FakeApp(recording=True)
    with scheme(), pressed():
        assert _FakeApp._handle_mistrigger_during_recording(app) is False, \
            "the net fired with no key held"
    # The start action's own key is exempt: it is down on every second press of
    # the start hotkey, so an entry on it would stop the recording every time.
    app = _FakeApp(recording=True)
    with scheme(cancel_recording='ctrl+w'), pressed('w'):
        assert _FakeApp._handle_mistrigger_during_recording(app) is False, \
            "an action on the start key's own token is not exempt"
    assert app.starts == [] and app.audio_recorder.cancelled is False
    # The shipped F-key preset: three candidates share f10 and two more sit on
    # the start key's f9, so the derived map is empty -- the net is inert there
    # rather than firing whichever action came first.
    for key in ('f9', 'f10'):
        app = _FakeApp(recording=True)
        with raw_scheme(settings_io.PRESET_FKEYS), pressed(key):
            assert _FakeApp._handle_mistrigger_during_recording(app) is False, key
        assert app.starts == [] and app.audio_recorder.cancelled is False


# ======================================================================
# The D-029 toggle dispatch (#336)
# ======================================================================

def test_toggle_second_press_stops_like_the_direct_key():
    # The acceptance case: one combo for the whole cycle. The second press must
    # deliver exactly what the partner's own key delivers -- which is provable
    # only because the deliver pins above measure that in the first place.
    with scheme(start_recording='pause', stop_recording_clipboard='pause'):
        reference = _FakeApp(recording=True)
        _stop(reference, 'stop_recording_clipboard')

        # The toggle key is physically DOWN at the stop moment -- the reality of
        # pressing it, so the scenario is the real one. It does not show which
        # path then calls the handler: the mis-trigger net firing on the
        # partner's token would reach the same handler with the same result. The
        # skip rule that keeps it out of the net is pinned by
        # test_hotkey_overrides.py's mis-trigger derivation cases.
        app = _FakeApp(recording=False)
        with pressed('pause'):
            _FakeApp.on_start_recording(app)
            assert app.audio_recorder.is_recording is True, "the first press did not start"
            assert app.starts == [], "the first press already delivered something"

            _FakeApp.on_start_recording(app)
            assert app.audio_recorder.is_recording is False, \
                "the second press of the toggle combo did not stop the recording"
            assert len(app.starts) == 1, app.starts
            # sidecar and transcriber_override are objects of the app that
            # produced them, so they are pinned per app rather than compared.
            handles = ('sidecar', 'transcriber_override')
            assert {k: v for k, v in app.starts[-1].items() if k not in handles} == \
                   {k: v for k, v in reference.starts[-1].items() if k not in handles}, \
                (app.starts[-1], reference.starts[-1])
            assert app.starts[-1]['sidecar'] is app.audio_recorder.sidecar_handle
            assert app.starts[-1]['transcriber_override'] is app.transcriber
            # The partner's own debounce slot is stamped, exactly as a direct
            # press would -- the toggle runs the handler, it does not imitate it.
            assert list(app._stop_insert_debounce) == ['stop_recording_clipboard'], \
                app._stop_insert_debounce

            # ...and the next press starts again: nothing debounces a start.
            _FakeApp.on_start_recording(app)
            assert app.audio_recorder.is_recording is True, \
                "the third press did not start the next recording"
            assert len(app.starts) == 1, app.starts


def test_toggle_yields_to_the_mistrigger_net():
    # Order pinned: a physically held OTHER stop key names the intended action
    # better than the toggle rule, so the net wins when both could fire.
    app = _FakeApp(recording=True)
    with scheme(start_recording='pause', stop_recording_clipboard='pause'):
        with pressed('pause', 'd'):
            _FakeApp.on_start_recording(app)
    assert len(app.starts) == 1, app.starts
    assert app.starts[-1]['send_after_insert'] is True, \
        f"the toggle partner fired although the send key was held: {app.starts[-1]}"


def test_no_toggle_second_press_still_ignored():
    # The zero-change guarantee at the dispatch site: with no pair configured a
    # second press of the start combo does what it always did -- nothing.
    app = _FakeApp(recording=True)
    with scheme(), pressed('w'):
        _FakeApp.on_start_recording(app)
    assert app.audio_recorder.is_recording is True, \
        "a second press stopped the recording without a toggle configured"
    assert app.starts == [] and app.inserts == [], (app.starts, app.inserts)
    assert app.audio_recorder.cancelled is False


# ======================================================================
# The two FILE_ONLY log lines (spec point 4)
# ======================================================================

def test_log_lines_name_the_effective_combos():
    # D-019: prose names the effective combo. Both lines used to spell shipped
    # keys ("Press A or H to insert later", "(Ctrl+Alt+R to retry)").
    app = _FakeApp(recording=True)
    with scheme(stop_recording_clipboard='f10', stop_recording_keyboard='ctrl+alt+pageup'):
        with captured_logs() as messages:
            _stop(app, 'stop_recording_no_insert')
    later = [m for m in messages if 'insert later' in m]
    assert len(later) == 1, messages
    assert 'F10' in later[0] and 'Ctrl+Alt+PgUp' in later[0], later[0]
    assert ' A or H ' not in later[0], later[0]

    mp3 = _ARCHIVE / "voice_20260919_115800_001.mp3"
    mp3.touch()
    try:
        with scheme(retry_last_failed='shift+f8'):
            with captured_logs() as messages:
                _FakeApp._record_failed_slot(app, "20260919_115800_001", 1.0)
        armed = [m for m in messages if 'Retry slot armed' in m]
        assert len(armed) == 1, messages
        assert 'Shift+F8 to retry' in armed[0], armed[0]
    finally:
        for leftover in _ARCHIVE.iterdir():
            leftover.unlink()


# ======================================================================
# Static drift guard
# ======================================================================

def test_no_literal_wait_lists_left_in_the_app():
    # Cheap counterpart to the lanes above: a literal list would look harmless
    # and work perfectly under the shipped scheme, which is exactly how #152
    # survived for so long.
    source = (Path(__file__).resolve().parent / "thoughtborne.py").read_text(encoding="utf-8")
    hits = re.findall(r"(?:wait_for_keys|trigger_keys)\s*=\s*\[\s*['\"].*", source)
    assert hits == [], ("literal release-wait list(s) in thoughtborne.py -- derive "
                        "them from HOTKEYS instead (#152): " + "; ".join(hits))


CASES = [
    test_stop_branch_waits_on_the_effective_combo,
    test_send_branch_waits_on_its_key_token_too,
    test_insert_later_branch_waits_on_the_effective_combo,
    test_retry_task_waits_on_the_effective_combo,
    test_ptt_branch_waits_on_the_configured_trigger,
    test_deliver_matrix_of_the_four_stop_flows,
    test_no_insert_flow_never_inserts_later,
    test_insert_later_branch_delivers_its_mode_flags,
    test_ptt_stop_delivers_the_handover_and_its_mode_kwargs,
    test_stop_debounces_its_own_insert_later,
    test_stop_no_longer_swallows_another_actions_insert,
    test_debounce_window_expires_on_time_alone,
    test_mistrigger_fires_the_rebound_key,
    test_mistrigger_stays_silent_where_the_map_is_empty,
    test_toggle_second_press_stops_like_the_direct_key,
    test_toggle_yields_to_the_mistrigger_net,
    test_no_toggle_second_press_still_ignored,
    test_log_lines_name_the_effective_combos,
    test_no_literal_wait_lists_left_in_the_app,
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
        print(f"\nOK: all {len(CASES)} stop-rebind-wiring cases pass")
        return 0
    finally:
        # Both throwaway dirs are created at import and nothing reaches them past
        # this point, so a standalone run leaves no tb_stop_rebind_* dir in /tmp.
        shutil.rmtree(_ARCHIVE, ignore_errors=True)
        shutil.rmtree(_LOG_DIR, ignore_errors=True)


def test_all():
    """The pytest entry point (#242). main()'s finally removes the shared archive
    dir; re-create it so collection order can never strand a later case."""
    try:
        assert main() == 0
    finally:
        _ARCHIVE.mkdir(exist_ok=True)


if __name__ == "__main__":
    sys.exit(main())
