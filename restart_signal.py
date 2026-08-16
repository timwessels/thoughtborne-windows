"""Restart handshake between the settings app and the running tool (#202).

When the user saves settings while Thoughtborne is running, the settings app asks
the tool to restart so the changes take effect immediately instead of only on its
next manual start (D-002 keeps pickup start-based; this just performs the start).

Three primitives, all pure/stdlib and fail-safe -- the settings app imports this
module, so the D-005 stdlib-only import chain must hold:

  - the SIGNAL, a file beside the project files (`restart_request`, a sibling of
    runtime_state.json): its mere EXISTENCE is the message. `request_restart` writes
    it, `consume_restart_signal` deletes it. Consume is an atomic `os.remove`, so of
    two racers exactly one wins and only a real deletion counts as consumed -- the
    tool shuts down for the relaunch ONLY on a True consume, which is what keeps a
    stuck (undeletable) file from looping a restart forever.
  - the LIVENESS probe, `tool_is_running`: the tool's D-004 single-instance mutex
    NAME existing means the tool is up. Probed read-only with OpenMutexW -- never
    CreateMutexW (which would create/hold the name and block the tool's own next
    start), and the probe handle is closed after every open (a held handle would keep
    the name alive and make the settings app's post-signal wait unwinnable).

Every path is fail-safe: an unwritable dir, a vanished/locked file, or any probe
fault degrades to the pre-#202 status quo (no restart) rather than raising. The
tool's mutex name lives here as the single source of truth, hoisted from
thoughtborne.py so the settings-side probe can never drift from the name the tool
actually creates -- without the settings app importing thoughtborne.py.

Pure/stdlib so it imports and is tested off Windows -- see `test_restart_signal.py`.
"""
import os
from pathlib import Path

SIGNAL_FILENAME = "restart_request"          # sibling of runtime_state.json (spec)

# Single source of the tool's D-004 single-instance mutex name. Hoisted from
# thoughtborne.py (its _second_instance_running uses this constant) so the
# settings-side liveness probe and the tool's own guard can never disagree on the
# name -- a drift would silently break the probe. Changing it is a wire-format
# change between two processes, hence a constant with a test guard.
TOOL_MUTEX_NAME = "Thoughtborne-SingleInstance"

# Settings-side PRE-ACK wait budget (#202): after writing the signal, poll this long
# for the tool to CONSUME it (delete the file). Consuming is the tool's ACK that it
# saw the request and has committed to shutting down; not seeing it in time means the
# tool is absent or wedged, so the app times out honestly and promptly rather than
# hanging. The generous room for the tool's *actual* shutdown -- its mid-recording
# salvage -- is a SEPARATE post-ACK budget (RESTART_SHUTDOWN_GRACE_SECONDS below),
# counted only once the ACK lands, so a slow-but-healthy salvage is never cut off
# while an unresponsive tool still fails fast.
RESTART_WAIT_SECONDS = 10.0
POLL_INTERVAL_MS = 250                        # root.after cadence for that wait

# Settings-side POST-ACK grace budget (#202): the app switches to this the moment the
# signal file is gone (the tool's ACK). It must cover the tool's own clean shutdown,
# which salvages an in-flight recording before any teardown. 45s covers the recomputed
# worst case with reserve: ~4s salvage (stream close, #128) + ~6s cancel_session join +
# a per-thread stop_program join(timeout=5) for each stuck processing thread (two hung
# transcriptions = 10s) + the MP3 encode -- so a slow-but-healthy shutdown never trips
# the false "did not close" dialog. Only counted after the ACK, so it never delays the
# honest timeout for a tool that never responds; and tool_is_running ending the wait on
# the real exit means the full budget is only ever felt in a genuinely wedged shutdown.
# Generous >= the pre-ACK budget.
RESTART_SHUTDOWN_GRACE_SECONDS = 45.0

# Re-emitted on every write so a user who finds this file can tell what wrote it and
# that deleting it is harmless (the engine_memory / personal_settings.example house
# style). EXISTENCE is the signal, so the content is documentation only.
REQUEST_COMMENT = (
    "Written by the Thoughtborne settings app to ask a running Thoughtborne to "
    "restart so just-saved settings take effect. Safe to delete: the running tool "
    "removes it within about a second, and any leftover is cleared at the next start."
)


def signal_path(base_dir):
    """The signal file beside the project files (config.SCRIPT_DIR in production),
    a sibling of runtime_state.json."""
    return Path(base_dir) / SIGNAL_FILENAME


def request_restart(path) -> bool:
    """Create the signal file. Returns True only when it was written.

    A plain (non-atomic) write is deliberate: EXISTENCE is the signal, a torn write
    still signals, and the handle is closed immediately so the tool's consuming
    delete never races our own open handle. Best-effort and never raises -- an
    unwritable directory just yields False and the tool keeps running unchanged.
    """
    try:
        Path(path).write_text(REQUEST_COMMENT + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def consume_restart_signal(path) -> bool:
    """Delete-if-present. Returns True exactly when THIS call removed the file.

    `os.remove` is the atomic consume: of two racers exactly one succeeds, the
    loser's error resolves to False. A missing file (the overwhelmingly common tick,
    and a double consume) -> False. An existing-but-undeletable file (AV scanner,
    permissions) -> False too, on purpose: the tool shuts down only on a True
    consume, so "not consumed" must mean "no shutdown" -- otherwise an unremovable
    file would loop a restart. The file survives and the next tick retries. Never
    raises.
    """
    try:
        os.remove(path)
    except Exception:
        return False
    return True


def signal_present(path) -> bool:
    """Best-effort "is the signal file still there?". True iff it currently exists.

    The settings app watches this after writing the signal (#202): the instant it
    reads False the running tool has consumed (deleted) the file and committed to its
    clean shutdown -- the ACK that stretches the app's wait from the short pre-ACK
    budget to the generous post-ACK grace (a long mid-recording salvage). Any fault
    resolves to False and never raises -- the generous "treat as ACKed" direction, at
    worst granting a healthy-looking tool the longer grace, never cutting one off.
    """
    try:
        return os.path.exists(path)
    except Exception:
        return False


def tool_is_running() -> bool:
    """True iff the tool's D-004 single-instance mutex NAME currently exists, probed
    WITHOUT creating or holding it.

    OpenMutexW(SYNCHRONIZE, FALSE, TOOL_MUTEX_NAME):
      a handle       -> CloseHandle at once, return True (never hold the name alive),
      ACCESS_DENIED  -> True: the name EXISTS but is owned by a higher-integrity
                        instance (the D-004 elevated/normal pair) -- a nonexistent
                        name yields FILE_NOT_FOUND, never a denial,
      FILE_NOT_FOUND / anything else / off-Windows / any fault -> False.

    The False-on-uncertain direction is the fail-open choice: a false "not running"
    degrades to the pre-#202 status quo (plain save, no restart), whereas a false
    "running" would invent a pointless wait ending in a bogus timeout dialog.
    ctypes is imported lazily so the module still imports off Windows (D-005).
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        ERROR_FILE_NOT_FOUND = 2
        ERROR_ACCESS_DENIED = 5
        SYNCHRONIZE = 0x00100000

        kernel32.OpenMutexW.restype = wintypes.HANDLE
        kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        ctypes.set_last_error(0)
        handle = kernel32.OpenMutexW(SYNCHRONIZE, False, TOOL_MUTEX_NAME)
        err = ctypes.get_last_error()

        if handle:
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle(handle)    # a held handle would keep the name alive
            return True
        if err == ERROR_ACCESS_DENIED:
            return True                     # name exists, owned by an elevated tool
        return False                        # FILE_NOT_FOUND or any other -> not running
    except Exception:
        return False
