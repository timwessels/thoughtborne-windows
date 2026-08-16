#!/usr/bin/env python3
"""Off-Windows verification of the settings-app -> tool restart handshake (#202).

`restart_signal` is pure/stdlib -- it imports nothing from the project and its one
Windows-only path (the mutex probe) imports ctypes lazily -- so the signal file's
round-trip, the atomic-consume race semantics, the fail-safe directions, and the
constants are all checked on plain Python against a temp directory. The two
invariants that must never regress are pinned here:

  - NO shutdown without a successful consume: consume_restart_signal returns True
    ONLY when THIS call removed the file (a vanished or undeletable file -> False),
    so an unremovable signal can never loop a restart;
  - the mutex name is a single source shared with thoughtborne.py (a static source
    guard locks the hoist in), so the probe can never drift from the name the tool
    creates.

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
    """request_restart into an unwritable directory: False, never raises. Skipped as
    root (mode bits don't apply) and where the FS ignores chmod."""
    readonly = Path(d) / "readonly"
    readonly.mkdir()
    os.chmod(readonly, 0o500)
    if os.access(readonly, os.W_OK):
        print("  (skipped: the unwritable-directory case -- this FS/user ignores chmod)")
    else:
        try:
            got = rs.request_restart(rs.signal_path(readonly))
            check(got is False, "request_restart into an unwritable dir did not report failure")
            check(not rs.signal_path(readonly).exists(),
                  "request_restart created a file in an unwritable dir")
        except Exception as e:
            failures.append(f"request_restart raised on an unwritable dir: {type(e).__name__}: {e}")
    os.chmod(readonly, 0o700)


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


def test_source_guards():
    """Static guards on thoughtborne.py, read as source (never imported -- it pulls in
    Windows-only modules): the mutex-name hoist is USED, the old literal is GONE, and
    the loop + startup both wire consume_restart_signal."""
    src_path = Path(__file__).resolve().parent / "thoughtborne.py"
    try:
        src = src_path.read_text(encoding="utf-8")
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
    finally:
        shutil.rmtree(d, ignore_errors=True)
    test_probe_fail_open()
    test_constants()
    test_source_guards()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: signal round-trip, atomic-consume race semantics, the fail-safe "
          "directions, the mutex-name hoist, and the constants all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
