#!/usr/bin/env python3
"""Off-Windows verification of the settings-app <-> tool handshakes (#202, #335).

`restart_signal` is pure/stdlib -- it imports nothing from the project and its one
Windows-only path (the mutex probe) imports ctypes lazily -- so the signal files'
round-trips, the atomic-consume race semantics, the fail-safe directions, and the
constants are all checked on plain Python against a temp directory. The invariants
that must never regress are pinned here:

  - NO shutdown without a successful consume: consume_restart_signal returns True
    ONLY when THIS call removed the file (a vanished or undeletable file -> False),
    so an unremovable signal can never loop a restart;
  - the mutex name is a single source shared with thoughtborne.py (a static source
    guard locks the hoist in), so the probe can never drift from the name the tool
    creates;
  - the #335 suspend pair keeps its two wire-format filenames, both of its writers
    stay self-describing and fail-safe, and `decide_hotkey_suspend` answers its full
    truth table -- above all that a vanished request outranks an expired failsafe
    (the normal end of a capture must not be read as an abandoned one) and that the
    failsafe is reported as its own case, because the caller has to take the request
    back before resuming.

    python3 test_restart_signal.py    # verify, exit non-zero on any violation
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path

import restart_signal as rs
import engine_memory as em
import settings_instance

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def test_path_contract(d):
    path = rs.signal_path(d)
    check(path.name == rs.SIGNAL_FILENAME, f"signal_path built {path.name}")
    check(path.parent == Path(d), "signal_path is not beside the given base dir")
    check(rs.SIGNAL_FILENAME == "restart_request",
          f"the signal filename changed to {rs.SIGNAL_FILENAME!r} -- the spec names it")
    # The spec's "a sibling of runtime_state.json" clause, locked in code.
    check(rs.signal_path(d).parent == em.state_path(d).parent,
          "the signal file is not a sibling of runtime_state.json")


def test_roundtrip(d):
    path = rs.signal_path(d)
    check(not path.exists(), "a fresh temp dir already had a signal file")

    check(rs.request_restart(path) is True, "request_restart did not report success")
    check(path.exists(), "request_restart did not leave the file on disk")
    raw = path.read_text(encoding="utf-8")
    check(raw.isascii(), "the signal file content is not pure ASCII")
    check(raw.endswith("\n"), "the signal file does not end with a newline")
    check(raw.strip() != "", "the signal file is empty (no self-describing content)")

    check(rs.consume_restart_signal(path) is True,
          "consume did not report removing the present file")
    check(not path.exists(), "consume left the file on disk")


def test_signal_present(d):
    """signal_present mirrors the file's existence and never raises. It is the #202
    ACK watch: True while the tool has not yet consumed the signal, False the instant
    it has (which stretches the settings app's wait to the post-ACK grace)."""
    path = rs.signal_path(d)
    check(rs.signal_present(path) is False, "signal_present of a missing file is not False")
    rs.request_restart(path)
    check(rs.signal_present(path) is True, "signal_present of a present file is not True")
    rs.consume_restart_signal(path)
    check(rs.signal_present(path) is False, "signal_present after consume is not False")
    # A fault in os.path.exists resolves to False (the generous "treat as ACKed"
    # direction), never raises.
    orig = os.path.exists
    def _boom(_p):
        raise OSError(5, "I/O error")
    rs.os.path.exists = _boom
    try:
        got = rs.signal_present(path)
    finally:
        rs.os.path.exists = orig
    check(got is False, "a fault in os.path.exists must resolve to False")


def test_consume_nothing_and_double(d):
    path = rs.signal_path(d)
    # Nothing there (the overwhelmingly common tick) -> False, no crash.
    check(rs.consume_restart_signal(path) is False,
          "consume of a missing file did not return False")
    # Write once, consume twice: the first wins, the second is a no-op False.
    rs.request_restart(path)
    check(rs.consume_restart_signal(path) is True, "first consume should win")
    check(rs.consume_restart_signal(path) is False,
          "a double consume returned True the second time -- would double-trigger")


def test_race_loser_view(d):
    """The file vanishes between ticks / a concurrent stale-clear won: os.remove
    raises FileNotFoundError -> False (the atomic-consume loser's view)."""
    path = rs.signal_path(d)
    rs.request_restart(path)
    orig = os.remove

    def _vanished(_p):
        raise FileNotFoundError(2, "No such file or directory")

    rs.os.remove = _vanished
    try:
        got = rs.consume_restart_signal(path)
    finally:
        rs.os.remove = orig
    check(got is False, "a FileNotFoundError from os.remove must resolve to False")


def test_undeletable_signal(d):
    """An existing-but-undeletable file (AV scanner, permissions): os.remove raises
    PermissionError -> False. This is the no-shutdown-without-consume guard against a
    restart loop -- 'not consumed' MUST mean 'no shutdown'."""
    path = rs.signal_path(d)
    rs.request_restart(path)
    orig = os.remove

    def _denied(_p):
        raise PermissionError(13, "Permission denied")

    rs.os.remove = _denied
    try:
        got = rs.consume_restart_signal(path)
    finally:
        rs.os.remove = orig
    check(got is False, "a PermissionError from os.remove must resolve to False "
                        "(else an unremovable signal loops a restart)")


def test_unwritable_directory(d):
    """Every writer into an unwritable directory: False, never raises -- for the #335
    request that is the fail-open of the arm (the field arms anyway, as before #335).
    Skipped as root (mode bits don't apply) and where the FS ignores chmod."""
    readonly = Path(d) / "readonly"
    readonly.mkdir()
    os.chmod(readonly, 0o500)
    if os.access(readonly, os.W_OK):
        print("  (skipped: the unwritable-directory case -- this FS/user ignores chmod)")
    else:
        writers = ((rs.request_restart, rs.signal_path, "request_restart"),
                   (rs.request_hotkeys_suspend, rs.suspend_request_path,
                    "request_hotkeys_suspend"),
                   (rs.acknowledge_hotkeys_suspended, rs.suspend_ack_path,
                    "acknowledge_hotkeys_suspended"))
        for writer, path_of, label in writers:
            try:
                got = writer(path_of(readonly))
                check(got is False, f"{label} into an unwritable dir did not report failure")
                check(not path_of(readonly).exists(),
                      f"{label} created a file in an unwritable dir")
            except Exception as e:
                failures.append(f"{label} raised on an unwritable dir: {type(e).__name__}: {e}")
    os.chmod(readonly, 0o700)


def test_suspend_path_contract(d):
    """The #335 pair's filenames are wire format between two processes, exactly like
    the restart signal's: a rename on one side silently un-pairs app and tool."""
    req = rs.suspend_request_path(d)
    ack = rs.suspend_ack_path(d)
    check(rs.SUSPEND_REQUEST_FILENAME == "hotkeys_suspend_request",
          f"the suspend request filename changed to {rs.SUSPEND_REQUEST_FILENAME!r}")
    check(rs.SUSPEND_ACK_FILENAME == "hotkeys_suspended",
          f"the suspend ACK filename changed to {rs.SUSPEND_ACK_FILENAME!r}")
    check(req.name == rs.SUSPEND_REQUEST_FILENAME, f"suspend_request_path built {req.name}")
    check(ack.name == rs.SUSPEND_ACK_FILENAME, f"suspend_ack_path built {ack.name}")
    check(len({req.name, ack.name, rs.SIGNAL_FILENAME}) == 3,
          "the three handshake files must have three distinct names -- a collision "
          "would let one message consume another")
    check(req.parent == Path(d) and ack.parent == Path(d),
          "the suspend files are not beside the given base dir")
    check(req.parent == em.state_path(d).parent,
          "the suspend files are not siblings of runtime_state.json")


def test_suspend_roundtrip(d):
    """Both writers: self-describing content, present, removable, removable once."""
    lanes = ((rs.suspend_request_path(d), rs.request_hotkeys_suspend, "suspend request"),
             (rs.suspend_ack_path(d), rs.acknowledge_hotkeys_suspended, "suspend ACK"))
    for path, writer, label in lanes:
        check(not path.exists(), f"a fresh temp dir already had a {label} file")
        check(writer(path) is True, f"the {label} writer did not report success")
        check(path.exists(), f"the {label} writer did not leave the file on disk")
        raw = path.read_text(encoding="utf-8")
        check(raw.isascii(), f"the {label} file content is not pure ASCII")
        check(raw.endswith("\n"), f"the {label} file does not end with a newline")
        check(raw.strip() != "", f"the {label} file is empty (no self-describing content)")
        check(rs.signal_present(path) is True, f"signal_present missed a present {label}")
        check(rs.clear_signal(path) is True, f"clear_signal did not remove the {label}")
        check(not path.exists(), f"clear_signal left the {label} on disk")
        check(rs.clear_signal(path) is False,
              f"a second clear of the {label} claimed to have removed it")


def test_clear_signal_reports_only_its_own_removal(d):
    """clear_signal is the no-flapping foundation: an undeletable request must read
    as False, so the tool's failsafe stays suspended and retries instead of resuming
    against a request that is still on disk (the mirror of #202's no-consume rule)."""
    path = rs.suspend_request_path(d)
    check(rs.clear_signal(path) is False, "clear_signal of a missing file did not return False")
    for exc in (PermissionError(13, "Permission denied"),
                FileNotFoundError(2, "No such file or directory")):
        rs.request_hotkeys_suspend(path)
        orig = os.remove

        def _raise(_p, _exc=exc):
            raise _exc

        rs.os.remove = _raise
        try:
            got = rs.clear_signal(path)
        finally:
            rs.os.remove = orig
        check(got is False, f"a {type(exc).__name__} from os.remove must resolve to False")
    rs.clear_signal(path)


def test_decide_hotkey_suspend():
    """The whole truth table of the tool's tick, including the two priorities the
    dispatch above it must not have to re-decide."""
    t = 30.0
    cases = (
        ((False, False, None, t), None, "nothing to do -- the common tick"),
        ((True, False, None, t), "suspend", "a fresh request releases the hotkeys"),
        ((False, True, 0.0, t), "resume", "the request is gone -- the capture ended"),
        ((False, True, t * 10, t), "resume",
         "a vanished request must outrank an expired failsafe"),
        ((True, True, t - 0.1, t), None, "still capturing, failsafe not due"),
        ((True, True, t, t), "timeout", "exactly at the failsafe"),
        ((True, True, t + 5.0, t), "timeout", "past the failsafe"),
        ((True, True, None, t), None, "no age known -> never a timeout"),
    )
    for args, expected, why in cases:
        got = rs.decide_hotkey_suspend(*args)
        check(got == expected,
              f"decide_hotkey_suspend{args} returned {got!r}, expected {expected!r} ({why})")


def test_probe_fail_open():
    """tool_is_running() degrades to False off Windows (and on any fault): the
    'uncertain means not running' direction, so a probe fault never invents a wait."""
    if os.name == "nt":
        print("  (skipped on Windows: the live mutex probe is exercised hands-on)")
        return
    check(rs.tool_is_running() is False,
          "off-Windows tool_is_running() must be False (fail-open to the pre-#202 status quo)")


def test_constants():
    # The D-004 wire-format value -- a rename must be a conscious act, and the tool's
    # own guard reads the same constant (test_source_guards proves that).
    check(rs.TOOL_MUTEX_NAME == "Thoughtborne-SingleInstance",
          f"TOOL_MUTEX_NAME changed to {rs.TOOL_MUTEX_NAME!r}")
    # D-009 distinctness, now guarded from this side too: the tool's mutex and the
    # settings app's must never collide (a shared name deadlocks the pair).
    check(rs.TOOL_MUTEX_NAME != settings_instance.SETTINGS_MUTEX_NAME,
          "the tool mutex name equals the settings mutex name -- D-009 distinctness broken")
    check(isinstance(rs.RESTART_WAIT_SECONDS, (int, float)) and rs.RESTART_WAIT_SECONDS > 0,
          "RESTART_WAIT_SECONDS must be a positive number")
    check(isinstance(rs.POLL_INTERVAL_MS, int) and rs.POLL_INTERVAL_MS > 0,
          "POLL_INTERVAL_MS must be a positive int")
    check(rs.RESTART_WAIT_SECONDS * 1000 > rs.POLL_INTERVAL_MS,
          "the wait budget must exceed one poll interval (else the loop can't poll)")
    # The #202 two-phase deadline: the post-ACK grace covers the tool's own shutdown
    # (a long mid-recording salvage), so it is generous and never shorter than the
    # pre-ACK budget the app already spent waiting for the ACK.
    check(isinstance(rs.RESTART_SHUTDOWN_GRACE_SECONDS, (int, float))
          and rs.RESTART_SHUTDOWN_GRACE_SECONDS > 0,
          "RESTART_SHUTDOWN_GRACE_SECONDS must be a positive number")
    check(rs.RESTART_SHUTDOWN_GRACE_SECONDS >= rs.RESTART_WAIT_SECONDS,
          "the post-ACK grace must be >= the pre-ACK budget (a healthy salvage gets "
          "at least as long as we already spent waiting for the ACK)")
    check(rs.RESTART_SHUTDOWN_GRACE_SECONDS * 1000 > rs.POLL_INTERVAL_MS,
          "the grace budget must exceed one poll interval")
    # The #335 budgets. Their own lane -- nothing here couples them to the #202 ones.
    check(isinstance(rs.SUSPEND_ACK_WAIT_SECONDS, (int, float))
          and rs.SUSPEND_ACK_WAIT_SECONDS > 0,
          "SUSPEND_ACK_WAIT_SECONDS must be a positive number")
    check(isinstance(rs.SUSPEND_POLL_INTERVAL_MS, int) and rs.SUSPEND_POLL_INTERVAL_MS > 0,
          "SUSPEND_POLL_INTERVAL_MS must be a positive int")
    check(rs.SUSPEND_ACK_WAIT_SECONDS * 1000 > rs.SUSPEND_POLL_INTERVAL_MS,
          "the arm budget must exceed one poll interval (else the wait can't poll)")
    check(isinstance(rs.SUSPEND_RESUME_TIMEOUT_SECONDS, (int, float))
          and rs.SUSPEND_RESUME_TIMEOUT_SECONDS > 0,
          "SUSPEND_RESUME_TIMEOUT_SECONDS must be a positive number")
    # The one relation that is load-bearing: the app gives up long before the tool's
    # failsafe, so a normal abort always ends the cycle the short way and the failsafe
    # can never fire into a capture the app still believes in.
    check(rs.SUSPEND_ACK_WAIT_SECONDS < rs.SUSPEND_RESUME_TIMEOUT_SECONDS,
          "the app's arm budget must be shorter than the tool's resume failsafe")


def test_source_guards():
    """Static guards on the two consumers, read as source (never imported -- they pull
    in Windows-only modules): the mutex-name hoist is USED, the old literal is GONE,
    the loop + startup both wire consume_restart_signal, and both ends of the #335
    pair are wired to these helpers rather than to logic of their own."""
    here = Path(__file__).resolve().parent
    try:
        src = (here / "thoughtborne.py").read_text(encoding="utf-8")
    except Exception as e:
        failures.append(f"could not read thoughtborne.py: {type(e).__name__}: {e}")
        return
    check("restart_signal.TOOL_MUTEX_NAME" in src,
          "thoughtborne.py no longer uses restart_signal.TOOL_MUTEX_NAME -- the hoist is unused")
    check('name = "Thoughtborne-SingleInstance"' not in src,
          "thoughtborne.py still hardcodes the mutex name literal -- the name can drift from the probe")
    check(src.count("consume_restart_signal") >= 2,
          "thoughtborne.py does not wire consume_restart_signal in both the startup "
          "guard and the recording loop (expected >= 2 references)")
    # #335, tool side: both paths are built here (__init__ and the startup cleanup),
    # and the tick delegates its decision instead of re-deriving it.
    check(src.count("suspend_request_path") >= 2,
          "thoughtborne.py does not wire suspend_request_path in both the loop state "
          "and the startup cleanup (expected >= 2 references)")
    check(src.count("suspend_ack_path") >= 2,
          "thoughtborne.py does not wire suspend_ack_path in both the listener "
          "callbacks and the startup cleanup (expected >= 2 references)")
    check("decide_hotkey_suspend" in src,
          "thoughtborne.py no longer calls decide_hotkey_suspend -- the tested "
          "decision has been replaced by logic of its own")
    # #335, app side: the request has exactly one writer, and it is the settings app.
    try:
        app_src = (here / "thoughtborne_settings.py").read_text(encoding="utf-8")
    except Exception as e:
        failures.append(f"could not read thoughtborne_settings.py: {type(e).__name__}: {e}")
        return
    check("request_hotkeys_suspend" in app_src,
          "thoughtborne_settings.py no longer asks for the hotkey suspend -- the "
          "capture field is back to competing with the tool's own hotkeys")


# These take a tempdir positionally and run via main(); they are not pytest items (#242).
for _helper in (test_path_contract, test_roundtrip, test_signal_present,
                test_consume_nothing_and_double, test_race_loser_view,
                test_undeletable_signal, test_unwritable_directory,
                test_suspend_path_contract, test_suspend_roundtrip,
                test_clear_signal_reports_only_its_own_removal):
    _helper.__test__ = False


def main():
    d = tempfile.mkdtemp(prefix="tb_restart_signal_")
    try:
        test_path_contract(d)
        test_roundtrip(d)
        test_signal_present(d)
        test_consume_nothing_and_double(d)
        test_race_loser_view(d)
        test_undeletable_signal(d)
        test_unwritable_directory(d)
        test_suspend_path_contract(d)
        test_suspend_roundtrip(d)
        test_clear_signal_reports_only_its_own_removal(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    test_probe_fail_open()
    test_decide_hotkey_suspend()
    test_constants()
    test_source_guards()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: both handshakes' round-trips, the atomic-consume race semantics, the "
          "fail-safe directions, the suspend decision table, the mutex-name hoist, "
          "and the constants all pass")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
