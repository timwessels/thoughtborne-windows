#!/usr/bin/env python3
"""The wiring of the partial hotkey loss (#340), driven against the real code.

    python3 test_hotkey_shortfall_wiring.py   # verify, exit non-zero on failure

When another application already holds one of our combos -- KeePass's default
`Ctrl+Alt+A` auto-type hotkey is the case that surfaced this -- Windows fails
that one registration and hands us no way to ask who took it. The combo itself is
then the whole actionable truth, and it used to end in the log while the console
showed a bare count. `test_console_ui.py` owns what the new surfaces LOOK like;
what it cannot see is whether they are reached at all, and with what. That is
this driver: the seam from the failing RegisterHotKey through to the verdict, the
panel and the settings window.

What it pins:

  - The manager keeps what it could not take. `_register_all` already built the
    list and threw it away at startup (the caller runs on the listener thread and
    has nobody to return it to), so the attribute beside it is the whole data
    seam. It is rewritten on every run, which is what makes a #335 resume that
    wins a combo back -- or loses one in that window -- show up honestly.
  - The app's two readings of that list: which ACTIONS are dead (`_lost_actions`,
    which every key display goes through) and which COMBOS to name (`_lost_pairs`,
    the panel's own handover, deliberately not via `_show`). Including the D-029
    toggle partner, which never appears in the manager's list -- it has no
    registration of its own -- and is dead exactly when its partner is.
  - `_show` hands out the placeholder for a lost action and the real combo for
    every other one, before registration included: no manager yet means nothing
    is lost, which is the state the whole startup runs in.
  - `_announce_hotkey_state` itself, on all five shapes a start can take: the
    full house (READY, no window), the partial loss (verdict + placeholder +
    the panel naming each combo + exactly one settings spawn), the total loss
    (the red FAILED panel alone -- no masthead, and no window, which is the half
    of today's behaviour that must not drift), a keyless start with a stolen key
    (both yellow lines compose), and the shortfall the manager never got to
    attribute, which shows the verdict without an empty panel.
  - A static guard that the panel's handover keeps reading HOTKEYS rather than
    being "simplified" onto `_pairs` -- which would fill the one surface that
    names the lost combos with placeholders instead.

What stays hands-on: that Windows really refuses the combo another application
owns (error 1409), and that the settings window really comes up in front. The
2026-09-21 field case and daily use carry both from here.
"""
import ast
import contextlib
import ctypes
import logging
import shutil
import sys
import tempfile
import types
from pathlib import Path

# ---- Windows-only / third-party modules the import chain needs, faked exactly as
# in test_hotkey_suspend_wiring.py: audio_handler pulls in msvcrt/pyaudio,
# hotkey_manager and output_handler configure argtypes on ctypes.windll handles at
# import, output_handler imports keyboard/pyperclip/pyautogui. None of it is
# exercised here -- only enough to let the real methods run off Windows.
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

import config  # noqa: E402
import console_ui  # noqa: E402
import hotkey_manager  # noqa: E402
from hotkey_parse import format_combo  # noqa: E402

# A throwaway log dir BEFORE `import thoughtborne`: that import attaches a
# RotatingFileHandler to config.LOG_FILE and creates the file where none exists,
# which in a fresh clone fakes the "tool is running" heartbeat a reader of
# thoughtborne.log goes by (#267).
_LOG_DIR = Path(tempfile.mkdtemp(prefix="tb_shortfall_log_"))      # removed in main()
config.LOG_FILE = _LOG_DIR / "thoughtborne.log"
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

import thoughtborne as tb  # noqa: E402

# Undo thoughtborne's import-time global side effects: it swaps sys.stdout/stderr
# for logger wrappers and attaches handlers.
sys.stdout = tb.original_stdout
sys.stderr = tb.original_stderr
for _name in ("Thoughtborne", "Thoughtborne.stdio", "Thoughtborne.console"):
    _lg = logging.getLogger(_name)
    for _h in list(_lg.handlers):
        _lg.removeHandler(_h)
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

# The shipped scheme as an own fixture -- NOT config.HOTKEYS, which this
# checkout's personal_settings.json may already have overridden.
SHIPPED = dict(config.DEFAULT_HOTKEYS)
GLYPH = console_ui.LOST_KEY_GLYPH


# ---- harness ---------------------------------------------------------------
@contextlib.contextmanager
def scheme(**overrides):
    """Run the body against the shipped scheme plus `overrides` as the effective
    HOTKEYS. Rebinding the module attribute is what a rebind looks like to every
    derivation under test -- they all read it at call time."""
    previous = tb.HOTKEYS
    tb.HOTKEYS = dict(SHIPPED, **overrides)
    try:
        yield
    finally:
        tb.HOTKEYS = previous


@contextlib.contextmanager
def win32_registrations(refuse):
    """Run the body with hotkey_manager's Win32 entry points faked: every
    RegisterHotKey succeeds except for the calls at the indexes in `refuse`,
    which report the 1409 failure a combo another application owns produces.
    `ctypes.get_last_error` comes with them -- it exists only on Windows, and the
    failure branch is the one place the module reads it."""
    calls = {"n": 0}

    def _register(hwnd, hotkey_id, modifiers, vk):
        index = calls["n"]
        calls["n"] += 1
        return 0 if index in refuse else 1

    saved = (hotkey_manager.RegisterHotKey, hotkey_manager.UnregisterHotKey)
    had_errno = hasattr(ctypes, "get_last_error")
    hotkey_manager.RegisterHotKey = _register
    hotkey_manager.UnregisterHotKey = lambda *a: 1
    ctypes.get_last_error = lambda: 1409       # "already registered by another app"
    try:
        yield
    finally:
        (hotkey_manager.RegisterHotKey, hotkey_manager.UnregisterHotKey) = saved
        if not had_errno:
            del ctypes.get_last_error


def manager_with(*failed, registered=11):
    """A stand-in for the hotkey manager as _announce_hotkey_state reads it: the
    failure list the real _register_all leaves behind, and the count the summary
    already goes by."""
    return types.SimpleNamespace(failed_registrations=list(failed),
                                 registered_count=registered)


class _FakeApp:
    """The collaborator surface the real methods touch. Everything under test is
    the genuine ThoughtborneApp method, bound as a class attribute."""
    _show = tb.ThoughtborneApp._show
    _pairs = tb.ThoughtborneApp._pairs
    _lost_actions = tb.ThoughtborneApp._lost_actions
    _lost_pairs = tb.ThoughtborneApp._lost_pairs
    _lineup_data = tb.ThoughtborneApp._lineup_data
    _announce_hotkey_state = tb.ThoughtborneApp._announce_hotkey_state

    def __init__(self, manager=None, *, keyless=False, current_api=None):
        self.hotkey_manager = manager
        self._keyless = keyless
        self.current_api = None if keyless else (current_api or config.DEFAULT_API)
        self.blocks = []     # (event, rendered lines, detail), in emission order
        self.spawns = 0

    def _emit_block(self, event, builder, detail=""):
        """The real one swallows a renderer fault into a one-line fallback
        (stability #1). Here the builder runs bare on purpose: a panel that
        cannot render is a failure of this driver, not a degraded console."""
        self.blocks.append((event, builder(ansi=True), detail))

    def _launch_settings_app(self, *extra_args):
        self.spawns += 1
        return True

    # --- readers -----------------------------------------------------------
    def events(self):
        return [event for event, _, _ in self.blocks]

    def text(self, event):
        """The SGR-free text of one emitted block."""
        import re
        lines = next(ls for ev, ls, _ in self.blocks if ev == event)
        return re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines))


# ======================================================================
# The manager side: where the failures stop being thrown away
# ======================================================================

def test_register_all_keeps_the_combos_it_could_not_take():
    hm = hotkey_manager.HotkeyManager()
    assert hm.failed_registrations == [], \
        "a fresh manager starts with a failure list that is not empty"
    hm.register("ctrl+alt+w", lambda: None, name="start_recording")
    hm.register("ctrl+alt+a", lambda: None, name="stop_recording_clipboard")
    hm.register("ctrl+alt+d", lambda: None, name="stop_recording_send")

    with win32_registrations({1}):        # the second combo is already owned
        returned = hm._register_all()
    want = [("ctrl+alt+a", "stop_recording_clipboard")]
    assert returned == want, f"_register_all returned {returned}"
    assert hm.failed_registrations == want, \
        f"the failures were not kept for the console: {hm.failed_registrations}"
    assert hm.registered_count == 2, hm.registered_count


def test_every_run_rewrites_the_list_so_a_resume_cannot_leave_it_stale():
    # The #335 resume registers the same set again, so the displays that read
    # this list have to follow it in BOTH directions: a combo won back (the other
    # application was closed) and one lost while we stood aside.
    hm = hotkey_manager.HotkeyManager()
    hm.register("ctrl+alt+w", lambda: None, name="start_recording")
    hm.register("ctrl+alt+a", lambda: None, name="stop_recording_clipboard")

    with win32_registrations({1}):
        hm._register_all()
    assert [n for _, n in hm.failed_registrations] == ["stop_recording_clipboard"]

    hm._hotkey_map.clear()                # what a suspend leaves behind
    with win32_registrations(set()):      # the other application is gone
        hm._register_all()
    assert hm.failed_registrations == [], \
        f"a clean re-registration left a stale failure: {hm.failed_registrations}"

    hm._hotkey_map.clear()
    with win32_registrations({0}):        # ... and another one takes W
        hm._register_all()
    assert [n for _, n in hm.failed_registrations] == ["start_recording"], \
        f"the new loss did not reach the list: {hm.failed_registrations}"


# ======================================================================
# The app's two readings of that list
# ======================================================================

def test_show_marks_a_lost_action_and_leaves_every_other_combo_alone():
    app = _FakeApp(manager_with(("ctrl+alt+a", "stop_recording_clipboard")))
    with scheme():
        assert app._show("stop_recording_clipboard") == GLYPH, \
            "a stolen combo is still offered as pressable"
        assert app._show("start_recording") == format_combo(SHIPPED["start_recording"])
        shown = dict(app._pairs())
    assert shown["stop_recording_clipboard"] == GLYPH
    assert len([c for c in shown.values() if c == GLYPH]) == 1, \
        f"one loss marked more than one key: {shown}"


def test_no_manager_yet_means_nothing_is_lost():
    # The state the whole startup runs in -- every combo display before
    # registration, and every one in a checkout where registration never ran.
    app = _FakeApp(None)
    with scheme():
        assert app._lost_actions() == frozenset()
        assert app._lost_pairs() == []
        assert app._show("start_recording") == format_combo(SHIPPED["start_recording"])


def test_the_panel_names_the_configured_combos_in_canonical_order():
    app = _FakeApp(manager_with(("ctrl+alt+d", "stop_recording_send"),
                                ("ctrl+alt+a", "stop_recording_clipboard")))
    with scheme():
        pairs = app._lost_pairs()
    # The manager's order is the registration order of whatever failed; the panel
    # reads HOTKEYS, so it is canonical (D-019) whatever order the losses came in.
    assert pairs == [("stop_recording_clipboard", format_combo(SHIPPED["stop_recording_clipboard"])),
                     ("stop_recording_send", format_combo(SHIPPED["stop_recording_send"]))], pairs
    assert all(GLYPH not in combo for _, combo in pairs), \
        "the panel that names the lost combos is showing placeholders"


def test_a_lost_toggle_combo_kills_both_halves_of_the_pair():
    # D-029: the partner shares start_recording's registration (the plan drops it
    # by name), so only start_recording can ever appear in the manager's list --
    # while the partner's own HOTKEYS entry holds the very same, now dead, combo.
    with scheme(start_recording="pause", stop_recording_clipboard="pause"):
        app = _FakeApp(manager_with(("pause", "start_recording")))
        lost = app._lost_actions()
        assert lost == frozenset({"start_recording", "stop_recording_clipboard"}), \
            f"the toggle partner was left looking alive: {sorted(lost)}"
        assert app._show("stop_recording_clipboard") == GLYPH
        pairs = app._lost_pairs()
    assert [n for n, _ in pairs] == ["start_recording", "stop_recording_clipboard"], pairs
    assert len({c for _, c in pairs}) == 1, \
        f"the two rows of one shared combo do not name the same combo: {pairs}"


def test_a_lost_stop_does_not_drag_the_start_key_with_it():
    # The expansion is one-way: the partner has no registration to lose, so a
    # stop going down says nothing about the start combo.
    with scheme(start_recording="pause", stop_recording_clipboard="pause"):
        app = _FakeApp(manager_with(("ctrl+alt+h", "stop_recording_keyboard")))
        assert app._lost_actions() == frozenset({"stop_recording_keyboard"}), \
            sorted(app._lost_actions())


# ======================================================================
# _announce_hotkey_state: the five shapes a start can take
# ======================================================================

def test_a_full_house_is_the_masthead_it_always_was():
    app = _FakeApp(manager_with(registered=12))
    with scheme():
        app._announce_hotkey_state(True)
    assert app.events() == ["startup"], app.events()
    text = app.text("startup")
    assert "READY" in text, "the successful start lost its READY invitation"
    assert "SOME KEYS INACTIVE" not in text
    assert GLYPH not in text
    assert app.spawns == 0, "a clean start opened the settings app"


def test_a_partial_loss_keeps_the_masthead_names_the_combo_and_opens_settings():
    app = _FakeApp(manager_with(("ctrl+alt+a", "stop_recording_clipboard")))
    with scheme():
        app._announce_hotkey_state(False)
        combo = format_combo(SHIPPED["stop_recording_clipboard"])
    assert app.events() == ["startup", "hotkeys-stolen"], app.events()

    startup = app.text("startup")
    assert "SOME KEYS INACTIVE" in startup, "no verdict on the shortfall masthead"
    assert "READY" not in startup, "a shortfall still claims READY (D-004)"
    assert GLYPH in startup, "the lost key is not marked in the grid"
    assert "History:" in startup, "the masthead lost its orientation zones"

    stolen = app.text("hotkeys-stolen")
    assert combo in stolen, f"the panel does not name {combo}: {stolen}"
    assert console_ui.KEY_LABELS["stop_recording_clipboard"] in stolen
    assert GLYPH not in stolen, "the panel that names the combo shows a placeholder"

    detail = next(d for ev, _, d in app.blocks if ev == "hotkeys-stolen")
    assert "stop_recording_clipboard" in detail, f"log breadcrumb: {detail!r}"
    assert app.spawns == 1, f"settings spawns: {app.spawns}"


def test_a_total_loss_stays_exactly_what_it_was():
    # 0/N is almost always a second instance, which D-004's mutex refuses long
    # before this runs. No masthead, and no window opening on top of an instance
    # that is not the one holding the keys.
    app = _FakeApp(manager_with(("ctrl+alt+w", "start_recording"), registered=0))
    with scheme():
        app._announce_hotkey_state(False)
    assert app.events() == ["hotkeys-failed"], app.events()
    assert app.spawns == 0, "the total-loss panel opened the settings app"


def test_a_keyless_start_with_a_stolen_key_says_both_things():
    app = _FakeApp(manager_with(("ctrl+alt+a", "stop_recording_clipboard")),
                   keyless=True)
    with scheme():
        app._announce_hotkey_state(False)
    startup = app.text("startup")
    assert "SOME KEYS INACTIVE" in startup, startup
    assert "enter an API key in Settings" in startup, \
        "the #200 shop-window guidance fell out of the shortfall masthead"
    assert GLYPH in startup
    assert app.spawns == 1


def test_a_shortfall_with_nothing_attributed_shows_no_empty_panel():
    # The start() timeout: the count can already be short while the failure list
    # is still empty (the listener never finished attributing). The verdict is
    # true and says so; a headless panel would say nothing at all.
    app = _FakeApp(manager_with(registered=11))
    with scheme():
        app._announce_hotkey_state(False)
    assert app.events() == ["startup"], app.events()
    assert "SOME KEYS INACTIVE" in app.text("startup")
    assert app.spawns == 1, "the one remedy was skipped on an unattributed loss"


# ======================================================================
# Static drift guard
# ======================================================================

def test_the_panel_handover_still_reads_the_configured_combos():
    # _lost_pairs looks like a redundant twin of _pairs and is the one handover
    # that must NOT go through _show: routed there, the single surface that names
    # the lost combos would fill with placeholders and the tool would state the
    # problem without ever saying which key it is about.
    source = (Path(__file__).resolve().parent / "thoughtborne.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "_lost_pairs"), None)
    assert fn is not None, "thoughtborne.py: _lost_pairs is gone -- the stolen-keys " \
                           "panel has no checked path to the real combos left (#340)"
    called = {n.func.attr for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    assert "_show" not in called, \
        "_lost_pairs routes through _show -- the panel would name placeholders (#340)"
    assert "format_combo" in names and "HOTKEYS" in names, \
        "_lost_pairs no longer formats the configured combos out of HOTKEYS"


CASES = [
    test_register_all_keeps_the_combos_it_could_not_take,
    test_every_run_rewrites_the_list_so_a_resume_cannot_leave_it_stale,
    test_show_marks_a_lost_action_and_leaves_every_other_combo_alone,
    test_no_manager_yet_means_nothing_is_lost,
    test_the_panel_names_the_configured_combos_in_canonical_order,
    test_a_lost_toggle_combo_kills_both_halves_of_the_pair,
    test_a_lost_stop_does_not_drag_the_start_key_with_it,
    test_a_full_house_is_the_masthead_it_always_was,
    test_a_partial_loss_keeps_the_masthead_names_the_combo_and_opens_settings,
    test_a_total_loss_stays_exactly_what_it_was,
    test_a_keyless_start_with_a_stolen_key_says_both_things,
    test_a_shortfall_with_nothing_attributed_shows_no_empty_panel,
    test_the_panel_handover_still_reads_the_configured_combos,
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
        print(f"\nOK: all {len(CASES)} hotkey-shortfall-wiring cases pass")
        return 0
    finally:
        shutil.rmtree(_LOG_DIR, ignore_errors=True)


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
