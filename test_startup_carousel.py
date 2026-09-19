#!/usr/bin/env python3
"""Wiring tests for the startup carousel's fall-through (#40, D-028).

    python3 test_startup_carousel.py    # verify, exit non-zero on failure

`test_engine_memory.py` pins the rotation itself (`carousel_from`) and
`test_console_ui.py` the key predicate the greyed lineup rows read. What neither
reaches is the loop that uses them: `ThoughtborneApp._create_startup_transcriber`,
which tries the resolved startup engine, skips every `MissingAPIKeyError` and
starts the first engine that constructs. D-028 rests on exactly that behaviour --
the settings window lets a user pin an engine that has no key, on purpose, because
the next start resolves the pin gracefully and visibly instead of refusing it. So
the resolution has to be pinned where it lives.

This driver runs the REAL method against a fake `self`, importing `thoughtborne`
with faked Win32/GUI modules the way `test_stop_rebind_wiring.py` does. Two
collaborators are replaced, both per case: `create_transcriber` raises the real
`MissingAPIKeyError` for every engine whose `.env` var a case declares keyless and
returns a sentinel otherwise, and `write_last_engine` is counted instead of
performed. Every assertion is on what the method returns or reaches -- the engine
it started, the transcriber it handed back, which engines it tried in which order,
whether the first-run wizard was launched, whether the engine memory was written,
and the line it logs on a fall-through, captured per run.

What it pins:

  - A pin or a memory on a keyless engine starts the first keyed engine in carousel
    order, from each of the four starting points, and tries exactly the engines up
    to it -- the "resolves at the next start through the carousel" half of D-028.
    The path is the same whichever of the two the start came from; what differs is
    the line left behind, which names what the user expected to open on -- `last
    used` for the Ctrl+Alt+L memory, `default` for a pin. Saying "default" for a
    remembered start would claim the default was tried when it never was (#193), so
    the wording is asserted rather than the source label carried along unchecked.
  - A start on an engine that HAS its key stays there and constructs exactly once,
    whether the engine came from `defaults.api` or from the Ctrl+Alt+L memory, and
    leaves no fall-through line at all. The fall-through is a remedy, not a route
    every start takes.
  - A start with no key at all ends open as the #200 shop window -- `(None, None)`,
    the first-run wizard launched once, no SETUP-NEEDED block -- rather than in the
    error branch, which is what makes a keyless pin survivable in the first place.
  - No case writes the engine memory (D-008): the fall-through is an outage, not a
    choice, and recording it would move the user's remembered engine behind their
    back.

Out of scope: the genuine non-key construction error, whose branch ends in
`input()` and `sys.exit(1)`. It is unchanged by D-028, and no case here produces a
non-key failure -- the prompt is shadowed below so a future mutation cannot wedge a
run on it either.
"""
import collections
import contextlib
import ctypes
import io
import logging
import shutil
import sys
import tempfile
import types
from pathlib import Path

# ---- Windows-only / third-party modules the import chain needs, faked exactly as
# in test_stop_rebind_wiring.py: audio_handler pulls in msvcrt/pyaudio,
# hotkey_manager and output_handler configure argtypes on ctypes.windll handles at
# import, output_handler imports keyboard/pyperclip/pyautogui. None of it is
# exercised here -- only enough to let the real method run off-Windows.
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

# transcriber imports `Groq` and `AuthenticationError` at module level. The real SDK
# is a test dependency (run_tests, CI), so prefer it and stub only the two names when
# it is absent -- no lane here constructs a real transcriber either way.
try:
    import groq  # noqa: E402,F401
except ImportError:
    _fake_groq = types.ModuleType("groq")
    _fake_groq.Groq = type("Groq", (), {"__init__": lambda self, *a, **k: None})
    _fake_groq.AuthenticationError = type("AuthenticationError", (Exception,), {})
    sys.modules.setdefault("groq", _fake_groq)

import audio_handler as ah  # noqa: E402
import config              # noqa: E402
import engine_memory       # noqa: E402

# Throwaway dirs, both BEFORE `import thoughtborne`: that import attaches a
# RotatingFileHandler to config.LOG_FILE and creates the file where none exists,
# which fakes the "tool is running" heartbeat a reader of thoughtborne.log goes by
# (#267). thoughtborne binds both values at import, so they have to move first, not
# after. Nothing here writes a recording; the archive redirect is what keeps that
# true if the import chain ever grows a mkdir of its own.
_LOG_DIR = Path(tempfile.mkdtemp(prefix="tb_carousel_log_"))      # removed in main()
_ARCHIVE = Path(tempfile.mkdtemp(prefix="tb_carousel_arch_"))     # removed in main()
config.LOG_FILE = _LOG_DIR / "thoughtborne.log"
config.ARCHIVE_FOLDER = _ARCHIVE
config.HISTORY_FOLDER = _ARCHIVE
ah.ARCHIVE_FOLDER = _ARCHIVE
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

import thoughtborne as tb  # noqa: E402

# Undo thoughtborne's import-time global side effects: it swaps sys.stdout/stderr for
# logger wrappers and attaches handlers. Restore the streams, detach every handler,
# and re-assert the silence its module-level logging setup cleared -- the carousel
# logs its skips at WARNING, which would otherwise reach the last-resort handler.
sys.stdout = tb.original_stdout
sys.stderr = tb.original_stderr
for _name in ("Thoughtborne", "Thoughtborne.stdio", "Thoughtborne.console"):
    _lg = logging.getLogger(_name)
    for _h in list(_lg.handlers):
        _lg.removeHandler(_h)
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)
tb.ARCHIVE_FOLDER = _ARCHIVE
tb.HISTORY_FOLDER = _ARCHIVE
# No case reaches the non-key error branch, which ends in input() + sys.exit(1). A
# mutation under test can, though, and an unanswered prompt would hang the run
# instead of failing it -- so shadow it. _start below turns the exit into a failure.
tb.input = lambda *a, **k: ""

_STATE_PATH = _LOG_DIR / engine_memory.STATE_FILENAME

_Run = collections.namedtuple(
    "_Run", "api transcriber attempts writes launched error_blocks output logged")


class _Capture(logging.Handler):
    """The `Thoughtborne` logger's WARNING records for the length of one run.

    The carousel skips and lands silently as far as a return value goes; what it
    says about the start it did not take is a log line, and that line is the one
    place `_start_api_source` reaches. Without it a case could only assert the path
    -- which is identical for a pin and a memory -- and the source would be a label
    carried along unchecked.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


class _FakeApp:
    """Everything `_create_startup_transcriber` reads off its app, and nothing else.

    The method is taken from the real class, so what runs is the shipped loop rather
    than a copy of it; the fake only supplies the three collaborators it reaches --
    the resolved start engine with its source, the first-run spawn and the
    SETUP-NEEDED block.
    """

    _create_startup_transcriber = tb.ThoughtborneApp._create_startup_transcriber

    def __init__(self, start_api, source):
        self._start_api = start_api
        self._start_api_source = source
        # The real app carries this (it is what the Ctrl+Alt+L write site hands
        # write_last_engine). Present here so a fall-through that started recording
        # the outage would find it, and the memory lane below fails over the WRITE
        # rather than over a missing attribute.
        self._state_path = _STATE_PATH
        self.launched = 0
        self.error_blocks = []

    def _launch_first_run_settings(self):
        self.launched += 1
        return True

    def _print_no_api_error_block(self, failures):
        self.error_blocks.append(list(failures))


def _start(start_api, keyed, source="config"):
    """Run the REAL method for a start on `start_api` with `keyed` as the set of .env
    vars that hold a key; return everything the case can assert on.

    The factory raises the real `MissingAPIKeyError` -- the one exception the loop
    treats as "skip and try the next" -- so a case declares key state and nothing
    else. `write_last_engine` is counted rather than performed: the assertion is that
    it is never called, and a test that let it write would be pinning a file instead
    of a decision. The logger is lifted out of the module-level silence for exactly
    this call, with `propagate` off: the records land in the capture handler and
    nowhere else, which is what the silence was for.
    """
    attempts, writes = [], []

    def create(api_name):
        attempts.append(api_name)
        var = config.API_KEY_ENV[api_name]
        if var not in keyed:
            raise tb.MissingAPIKeyError(var, api_name)
        return ("transcriber", api_name)

    app = _FakeApp(start_api, source)
    real_create = tb.create_transcriber
    real_write = tb.engine_memory.write_last_engine
    tb.create_transcriber = create
    tb.engine_memory.write_last_engine = lambda *a, **k: (writes.append(a), True)[1]
    tb_logger = logging.getLogger("Thoughtborne")
    capture = _Capture()
    level, propagate = tb_logger.level, tb_logger.propagate
    tb_logger.addHandler(capture)
    tb_logger.setLevel(logging.WARNING)
    tb_logger.propagate = False
    out = io.StringIO()
    try:
        # The shop-window branch prints; keep it out of the driver's own output and
        # available to the case that cares.
        with contextlib.redirect_stdout(out):
            api, transcriber = app._create_startup_transcriber()
    except SystemExit as e:
        raise AssertionError(
            f"the start took the non-key error branch (SETUP-NEEDED, input(), "
            f"sys.exit) although every failure here is a missing key: code={e.code}, "
            f"attempts={attempts}") from e
    finally:
        tb.create_transcriber = real_create
        tb.engine_memory.write_last_engine = real_write
        tb_logger.removeHandler(capture)
        tb_logger.setLevel(level)
        tb_logger.propagate = propagate
    return _Run(api, transcriber, attempts, writes, app.launched,
                app.error_blocks, out.getvalue(), capture.messages)


def _landing_lines(run):
    """The fall-through line(s) out of one run's captured log -- what the loop says
    when it opened on an engine other than the one it was asked for. The `Skipped x
    (...)` lines beside it are the per-engine breadcrumbs and are not this."""
    return [m for m in run.logged if "-> started on" in m]


# ======================================================================
# The fall-through
# ======================================================================

def test_a_keyless_start_falls_through_to_the_first_keyed_engine():
    # The behaviour D-028 rests on: the settings window writes a pin on a keyless
    # engine verbatim, and the next start resolves it here. Each row starts on an
    # engine whose key is absent and holds the other provider's key, so the landing
    # spot is unambiguous -- and the attempt list is spelled out rather than derived
    # from carousel_from, which is the very order under test.
    #
    # The source alternates per row, and it is the log line that makes it observable:
    # the loop walks the identical path for a pin and a memory (`_start_api_source`
    # reaches nothing else), so the wording is where a row labelled "memory" either
    # earns its label or is a duplicate of its neighbour.
    for start_api, keyed, expected, attempts, source, intent in (
            ("soniox-live", {"GROQ_API_KEY"}, "groq-large",
             ["soniox-live", "soniox", "groq-large"], "config", "default"),
            ("soniox", {"GROQ_API_KEY"}, "groq-large", ["soniox", "groq-large"],
             "memory", "last used"),
            ("groq-large", {"SONIOX_API_KEY"}, "soniox-live",
             ["groq-large", "groq", "soniox-live"], "memory", "last used"),
            ("groq", {"SONIOX_API_KEY"}, "soniox-live", ["groq", "soniox-live"],
             "config", "default"),
    ):
        run = _start(start_api, keyed, source=source)
        assert run.api == expected, (
            f"a start pinned to the keyless '{start_api}' opened on {run.api!r}, not "
            f"on {expected!r} -- the first keyed engine in carousel order is what a "
            f"pin without a key resolves to (#40, D-028); attempts: {run.attempts}")
        assert run.transcriber == ("transcriber", expected), (
            f"the transcriber handed back is not the one built for {expected!r}: "
            f"{run.transcriber!r}")
        assert run.attempts == attempts, (
            f"a start on '{start_api}' tried {run.attempts}, not {attempts} -- every "
            "engine is tried once, in carousel order, until one constructs")
        assert run.launched == 0, (
            f"a start that found a keyed engine launched the first-run wizard "
            f"{run.launched} time(s) -- that belongs to the keyless start alone")
        assert run.error_blocks == [], (
            f"a skipped missing key printed the SETUP-NEEDED block: {run.error_blocks}")
        landing = _landing_lines(run)
        assert len(landing) == 1, (
            f"a start on '{start_api}' that landed on {expected!r} left {landing} -- a "
            "fall-through logs exactly one line naming what it opened on instead, the "
            "only trace of the substitution outside the greyed console row (#40/#200)")
        other = "default" if intent == "last used" else "last used"
        assert f"({intent}: {start_api})" in landing[0] and other not in landing[0], (
            f"a {source} start that fell through to {expected!r} logged {landing[0]!r} "
            f"-- it names what the user expected to open on, and the label says where "
            f"that expectation came from: a {source} start is a '{intent}' one, and "
            f"'{other}' would claim a source this start never resolved from (#193)")


def test_a_keyed_start_stays_where_it_was_asked_to():
    # The dead guard for the rows above: the carousel is a remedy, not a route every
    # start walks. A keyed start constructs exactly once and says nothing about a
    # fall-through, whichever source resolved it.
    for start_api, keyed, source in (
            ("soniox-live", {"SONIOX_API_KEY"}, "config"),
            ("groq", {"GROQ_API_KEY", "SONIOX_API_KEY"}, "config"),
            ("groq-large", {"GROQ_API_KEY"}, "memory"),
            ("soniox", {"SONIOX_API_KEY"}, "memory"),
    ):
        run = _start(start_api, keyed, source=source)
        assert run.api == start_api, (
            f"a start on the keyed '{start_api}' ({source}) moved to {run.api!r} -- an "
            "engine that constructs is where the tool opens")
        assert run.attempts == [start_api], (
            f"a keyed start tried {run.attempts} -- exactly one construction, no "
            "rotation past an engine that works")
        assert run.launched == 0 and run.error_blocks == [], (
            f"a keyed start reached the keyless lanes: launched={run.launched}, "
            f"blocks={run.error_blocks}")
        assert not _landing_lines(run), (
            f"a start that opened on the engine it was asked for ('{start_api}', "
            f"{source}) logged a fall-through anyway: {_landing_lines(run)} -- that "
            "line means the tool opened somewhere else than the user expected")


def test_a_start_without_any_key_stays_open_as_the_shop_window():
    # #200/#163, and the reason a keyless pin is survivable at all: with no key
    # anywhere the tool neither errors out nor exits, it comes up keyless behind the
    # wizard. Every engine is tried first -- that is what makes "all missing key"
    # true rather than assumed.
    run = _start("soniox-live", set())
    assert (run.api, run.transcriber) == (None, None), (
        f"a start with no key at all returned {(run.api, run.transcriber)!r} -- the "
        "keyless start stays open as the first-run shop window (#200)")
    assert run.attempts == ["soniox-live", "soniox", "groq-large", "groq"], (
        f"the keyless start tried {run.attempts} -- all four engines are tried once, "
        "in carousel order, before the shop window is the answer")
    assert run.launched == 1, (
        f"the first-run wizard was launched {run.launched} time(s) -- exactly once, "
        "best-effort: the tool stays open either way")
    assert run.error_blocks == [], (
        f"the keyless start printed the SETUP-NEEDED block: {run.error_blocks} -- that "
        "panel is reserved for a genuine non-key construction error")
    assert "No API key" in run.output, (
        f"the keyless start said nothing on the console: {run.output!r}")


def test_the_fall_through_is_never_remembered():
    # D-008: the memory records a deliberate switch, never the carousel's answer to
    # an outage. A fall-through that wrote it would move the user's remembered engine
    # behind their back -- and the next start would then open on the substitute as if
    # it had been chosen.
    for start_api, keyed, source in (
            ("soniox-live", {"GROQ_API_KEY"}, "config"),      # falls through
            ("groq", {"SONIOX_API_KEY"}, "memory"),           # falls through
            ("soniox-live", {"SONIOX_API_KEY"}, "config"),    # starts where asked
            ("soniox-live", set(), "config"),                 # nothing to start
    ):
        run = _start(start_api, keyed, source=source)
        assert run.writes == [], (
            f"a start on '{start_api}' ({source}) wrote the engine memory: "
            f"{run.writes} -- only a deliberate switch is recorded (D-008), never the "
            "startup carousel's fall-through")


CASES = [
    test_a_keyless_start_falls_through_to_the_first_keyed_engine,
    test_a_keyed_start_stays_where_it_was_asked_to,
    test_a_start_without_any_key_stays_open_as_the_shop_window,
    test_the_fall_through_is_never_remembered,
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
        print(f"\nOK: all {len(CASES)} startup-carousel cases pass -- the keyless "
              "fall-through from each of the four starting points, logged as the "
              "'default' or 'last used' start it was, the keyed start that constructs "
              "once, the all-keyless shop window, and the memory the fall-through "
              "never writes")
        return 0
    finally:
        # Both throwaway dirs are created at import and nothing reaches them past this
        # point, so a standalone run leaves no tb_carousel_* dir in /tmp.
        shutil.rmtree(_ARCHIVE, ignore_errors=True)
        shutil.rmtree(_LOG_DIR, ignore_errors=True)


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
