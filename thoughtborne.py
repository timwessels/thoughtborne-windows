#!/usr/bin/env python3
"""
Thoughtborne Main Application (Windows version)

This is the main entry point for the voice-to-text application.
It orchestrates the audio recording, transcription, and text output
using hotkey controls.

The application uses:
- Multiple transcription APIs (Soniox v2/v4/Live, Groq)
- Soniox Live (WebSocket real-time streaming) as default API at startup
- Soniox for high-quality transcription
- Groq Whisper Large V3 Turbo (fast) and Large V3 (higher accuracy) transcription
- Parallel processing for multiple recordings
- Sequential output queue for maintaining order
- Clipboard or keyboard insertion options

Windows Adaptations:
- Uses Win32 RegisterHotKey API for event-driven hotkeys (survives sleep/wake)
- Hotkeys use Ctrl+Alt (instead of Cmd+Control on Mac)
- Separate recording loop thread for audio capture
"""

import os
import sys
import copy
import time
import queue
import shutil
import signal
import logging
import threading
import datetime
from pathlib import Path
from typing import List, Optional, NamedTuple, Tuple
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener

# Import our modules
import console_ui
from config import (
    LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    LOG_CONSOLE_QUEUE_MAX, FILE_ONLY,
    HOTKEYS, STATUS_UPDATE_INTERVAL, MAX_PARALLEL_TRANSCRIPTIONS,
    SCRIPT_DIR, DEFAULT_API, AVAILABLE_APIS, API_DISPLAY, ENGINE_TOKENS,
    SHORT_AUDIO_THRESHOLD, ARCHIVE_FOLDER, HISTORY_FOLDER,
    migrate_legacy_archives,
    PTT_ENABLED, PTT_TRIGGER_VK, PTT_INSERT,
    PTT_TAP_WINDOW_S, PTT_MIN_HOLD_S, PTT_RELEASE_TAIL_S,
    RECORDING_LOOP_STALE_SECONDS,
)
from hotkey_manager import HotkeyManager, is_key_pressed, is_vk_pressed, VK_RMENU
from ptt_detector import PttDetector, KeyboardSnapshot, PttAction
from audio_handler import (
    AudioRecorder, recover_partial_files,
    write_retry_marker, recover_salvaged_recordings,
    delete_retry_marker, delete_superseded_retry_markers,
    is_retry_marker_announced, mark_retry_marker_announced,
    _RECOVERED_ARCHIVE_RE,
)
from transcriber import (
    create_transcriber,
    engine_code,
    MissingAPIKeyError,
    SonioxLiveTranscriber,
    SonioxTranscriber,
    SonioxV4Transcriber,
    _EngineTag,
    _ErrorTag,
    _one_line_error,
)
from output_handler import OutputManager, TranscriptionTask


def _build_ptt_foreign_vks() -> frozenset:
    """Curated set of virtual-key codes that DISARM the push-to-talk gesture (#66).

    "Foreign" = any key the user might press as part of a real chord (Ctrl+C,
    Ctrl+S, ...) or that simply means "this is not a bare trigger tap". Polled
    one by one with GetAsyncKeyState during the brief arming window only --
    GetAsyncKeyState is live/async (it is the project's standby-safe primitive),
    unlike GetKeyboardState which lags on a thread that does not pump messages
    (the recording loop does not).

    The base set EXCLUDES the keys that must never count as foreign on any
    configuration: the combined and both side-specific Ctrl codes (0x11/0xA2/
    0xA3 -- a bare Left-Ctrl press also sets the combined bit) and Right-Alt
    (0xA5, handled separately as the AltGr blocker). The ACTIVE trigger VK is
    additionally removed per instance in _ptt_foreign_key_down(), so a bare
    trigger press never reads as its own foreign key -- this matters when the
    trigger is Left-Alt (0xA4), which is in this base set. Everything else --
    letters, digits, the other modifiers, OEM punctuation, space, the
    nav/function block -- disarms.
    """
    vks = set()
    vks.update(range(0x41, 0x5B))   # A-Z
    vks.update(range(0x30, 0x3A))   # 0-9
    vks.update({0x10, 0x5B, 0x5C})  # Shift (combined), Left/Right Win
    vks.add(0xA0); vks.add(0xA1)    # Left/Right Shift (side-specific)
    vks.add(0xA4)                   # Left-Alt (removed per-instance when it is the trigger)
    vks.add(0x20)                   # Space
    vks.update({0x08, 0x09, 0x0D, 0x1B})  # Backspace, Tab, Enter, Esc
    vks.update(range(0x21, 0x2F))   # PageUp/Down, End, Home, arrows, Ins, Del, ...
    vks.update(range(0x70, 0x88))   # F1-F24
    vks.update(range(0xBA, 0xC1))   # OEM ; = , - . / `
    vks.update(range(0xDB, 0xE0))   # OEM [ \ ] '
    # Never treat the Ctrl trio or Right-Alt as foreign (see docstring).
    vks.discard(0x11); vks.discard(0xA2); vks.discard(0xA3); vks.discard(0xA5)
    return frozenset(vks)


# Module-level so it is built once, not per tick.
_PTT_FOREIGN_VKS = _build_ptt_foreign_vks()


class _FailedRecording(NamedTuple):
    """Immutable record of a failed transcription, retryable via Ctrl+Alt+R (#24).

    Holds just enough to re-transcribe the archived MP3 as a fresh dictation:
    the deterministic archive path, the duration (to pick the V2/V4 fallback
    tier), and the origin timestamp for log correlation. Immutable so the slot
    can be compared by identity (see _resolve_failed_slot)."""
    archived_mp3_path: str
    duration: float
    origin_timestamp: str


def _describe_construction_failure(error: Exception) -> str:
    """One-line, human-readable reason for a failed transcriber construction (#40)."""
    if isinstance(error, MissingAPIKeyError):
        return f"{error.env_var} missing"
    return f"{type(error).__name__}: {error}"


class DroppingQueueHandler(QueueHandler):
    """QueueHandler that drops the newest record when the queue is full instead
    of letting the base emit() fall into handleError(), which writes a traceback
    to the (blockable) console stderr. That keeps the listener thread from ever
    blocking on console I/O even under a cmd Mark-Mode stall (#11). The file
    handler keeps every record regardless.

    prepare() additionally strips the exception/stack payload before the record
    is queued for the console (#117). QueueHandler.prepare() pre-formats every
    record through the handler's default Formatter, which appends the exception
    text and bakes the full traceback into record.msg (the file handler runs
    first and has already cached the rendered trace on exc_text), so the
    console's ConsoleFormatter -- which renders only a HH:MM:SS one-liner and
    never appends exception text -- would print the whole stack trace, styled
    red, burying the FAILED panel. Clearing the payload on a copy keeps the
    console to the one-liner while the independent file handler keeps full
    tracebacks, and leaves the original record intact for any other handler."""
    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            pass

    def prepare(self, record):
        record = copy.copy(record)
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return super().prepare(record)


class ConsoleFormatter(logging.Formatter):
    """Console rendering for the Cockpit design (#109). Records flagged
    raw_console (the pre-composed panels/strips/ticker) pass through verbatim.
    Every other console record becomes a receding dim ``HH:MM:SS  message``
    line: red+bold for errors (red stays error-exclusive), unstyled for
    warnings, dim for info. The FILE log keeps the full LOG_FORMAT (this
    formatter is only on the console handler)."""
    def format(self, record):
        if getattr(record, 'raw_console', False):
            return record.getMessage()
        line = f"{self.formatTime(record, '%H:%M:%S')}  {record.getMessage()}"
        if record.levelno >= logging.ERROR:
            return _style(line, _BOLD, _RED)
        if record.levelno >= logging.WARNING:
            return line
        return _style(line, _DIM)


class _ConsoleGateFilter(logging.Filter):
    """Keeps records flagged file_only off the console queue (#61/#109); the file
    handler on the 'Thoughtborne' root logger records them unchanged. Attached to
    the queue handler, so it governs every Thoughtborne.* child logger too (they
    propagate up) and never touches console_logger records (they carry no flag)."""
    def filter(self, record):
        return not getattr(record, 'file_only', False)


# ===== LOGGING SETUP =====
logger = logging.getLogger('Thoughtborne')
logger.setLevel(logging.DEBUG)

# Formatter for log entries
formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

# Rotating file handler (rotates at 10MB, keeps 3 backups)
# File gets ALL logs including DEBUG
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
    encoding='utf-8'
)
file_handler.setLevel(logging.DEBUG)  # File: everything
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Console output is wrapped behind a QueueHandler so a Windows cmd Mark/Quick-Edit
# selection can never block the hotkey-listener thread (#11). The listener thread
# only enqueues records (non-blocking); a dedicated daemon thread (QueueListener)
# drains the queue and writes to stderr. If cmd blocks the write, only that daemon
# stalls -- the listener and the synchronous file handler are unaffected.
# Order matters: this StreamHandler binds to the *current* sys.stderr (the real cmd
# stderr) and must be constructed BEFORE the StreamToLogger redirect below, or its
# emit() would recurse through the redirected stream.
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)  # Terminal: INFO, WARNING, ERROR only (no DEBUG)
# ConsoleFormatter renders its own dim HH:MM:SS line and never calls super().format(),
# so it takes no fmt/datefmt (the file handler keeps LOG_FORMAT above).
console_handler.setFormatter(ConsoleFormatter())

# Bounded queue: a full queue drops the newest console records (DroppingQueueHandler
# swallows queue.Full instead of routing to handleError) so the listener thread never
# blocks even when the drain stalls. The file handler keeps the complete record either
# way.
_console_log_queue: queue.Queue = queue.Queue(maxsize=LOG_CONSOLE_QUEUE_MAX)
_console_queue_handler = DroppingQueueHandler(_console_log_queue)
_console_queue_handler.setLevel(logging.INFO)  # Filter DEBUG before the queue
_console_queue_handler.addFilter(_ConsoleGateFilter())  # file_only records skip the console (#61/#109)
logger.addHandler(_console_queue_handler)

_console_queue_listener = QueueListener(
    _console_log_queue,
    console_handler,
    respect_handler_level=True,
)
_console_queue_listener.start()

# Console-only surface for the status block (#37): rides the same bounded
# queue as all console logging (#11 -- non-blocking from every thread,
# serialized by the single QueueListener), but never reaches the file log
# (propagate=False keeps it away from the parent's file handler). Per block,
# one DEBUG breadcrumb on the main logger carries the event into the file.
console_logger = logging.getLogger('Thoughtborne.console')
console_logger.setLevel(logging.INFO)
console_logger.propagate = False
console_logger.addHandler(_console_queue_handler)


# ===== STDOUT/STDERR REDIRECT TO LOG =====
class StreamToLogger:
    """
    Redirect stdout/stderr to logger while still showing in console.
    This captures ALL output including print() and external library warnings.
    """
    def __init__(self, logger, log_level=logging.INFO, original_stream=None,
                 prefix=''):
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ''
        self.original_stream = original_stream
        self.prefix = prefix  # greppable marker per logged line (e.g. "[stderr] ", #39)

    def write(self, buf):
        # Write to original stream (so it still shows in terminal)
        if self.original_stream:
            self.original_stream.write(buf)
            self.original_stream.flush()

        # Also log it
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, self.prefix + line.rstrip())

    def flush(self):
        if self.original_stream:
            self.original_stream.flush()


# Cleanup old log backup files
def cleanup_old_logs(log_file, max_age_days=30):
    """Delete log backup files older than max_age_days"""
    try:
        log_dir = log_file.parent
        log_name = log_file.name

        # Find all backup log files (e.g., thoughtborne.log.1, thoughtborne.log.2, etc.)
        import glob
        import time as time_module

        pattern = str(log_dir / f"{log_name}.*")
        backup_files = glob.glob(pattern)

        now = time_module.time()
        max_age_seconds = max_age_days * 24 * 3600

        deleted_count = 0
        for backup_file in backup_files:
            try:
                file_age = now - os.path.getmtime(backup_file)
                if file_age > max_age_seconds:
                    os.remove(backup_file)
                    deleted_count += 1
                    logger.debug(f"Deleted old log backup: {backup_file}")
            except Exception as e:
                logger.warning(f"Could not delete old log {backup_file}: {e}")

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old log backup file(s)")

    except Exception as e:
        logger.warning(f"Error during log cleanup: {e}")


# Redirect stdout and stderr to logger (while keeping console output).
# The redirect logs to a file-only child logger (#39): the raw passthrough to
# the real console is the single visible copy. Logging to the main logger used
# to classify every library stderr write as WARNING, which passed the console
# queue's INFO gate and printed each line a second time, formatted. Severity
# is now INFO with a greppable "[stderr] " prefix instead of the blanket
# WARNING; the file log still records every line.
original_stdout = sys.stdout
original_stderr = sys.stderr
_stdio_logger = logging.getLogger('Thoughtborne.stdio')
_stdio_logger.setLevel(logging.DEBUG)
_stdio_logger.propagate = False
_stdio_logger.addHandler(file_handler)
sys.stdout = StreamToLogger(_stdio_logger, logging.DEBUG, original_stdout)
sys.stderr = StreamToLogger(_stdio_logger, logging.INFO, original_stderr, prefix="[stderr] ")


# ===== STATUS BLOCK STYLING (#37) =====
def _enable_vt_mode() -> bool:
    """Enable ANSI escape processing on the attached console (Windows 10+).

    Returns True when status-block styling may use color/bold; False means
    plain text (redirected output, very old Windows, or no console). Uses
    ctypes SetConsoleMode directly -- no new dependency, no shell spawn."""
    try:
        if not original_stderr.isatty():  # console_handler writes to stderr
            return False
        if os.name != 'nt':
            return True
        import ctypes
        # Private WinDLL instance on purpose (same pattern as the #29
        # diagnostics): never touch the shared ctypes.windll function objects
        # that pyperclip/keyboard/pyautogui configure.
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        # Full signatures: without restype ctypes assumes c_int and would
        # truncate a 64-bit HANDLE.
        kernel32.GetStdHandle.restype = ctypes.c_void_p  # HANDLE
        kernel32.GetStdHandle.argtypes = (ctypes.c_uint32,)  # DWORD
        kernel32.GetConsoleMode.restype = ctypes.c_int  # BOOL
        kernel32.GetConsoleMode.argtypes = (ctypes.c_void_p,
                                            ctypes.POINTER(ctypes.c_uint32))
        kernel32.SetConsoleMode.restype = ctypes.c_int  # BOOL
        kernel32.SetConsoleMode.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(-12)  # STD_ERROR_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(kernel32.SetConsoleMode(
            handle, ctypes.c_uint32(mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)))
    except Exception:
        return False


_ANSI_ENABLED = _enable_vt_mode()

# SGR codes for the few _style() call sites left in this module (the dim ticker
# and the console log formatter). The full palette is single-sourced in
# console_ui (#109); the panels/strips style themselves there. Only bold, red,
# and the new dim (the receding technical layer) are still applied here.
from console_ui import BOLD as _BOLD, RED as _RED, DIM as _DIM


def _style(text: str, *codes: str) -> str:
    """Wrap text in ANSI SGR codes; plain passthrough when styling is disabled."""
    if not _ANSI_ENABLED or not codes:
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


# Clean up old log backups on startup. Deferred to here (past the palette /
# _style / _ANSI_ENABLED above) because ConsoleFormatter.format() now references
# them (#109); if this logged an INFO before they existed the async listener
# thread could format that record and hit a NameError.
cleanup_old_logs(LOG_FILE, max_age_days=30)


# ===== AUDIO CAPTURE-THREAD PRIORITY (#72) =====
def _elevate_capture_thread_priority():
    """Raise the audio capture thread's scheduling priority so it keeps draining
    the mic under CPU load (#72). Windows-only, best-effort, never raises; logs
    one line naming the mechanism that took. Returns an opaque revert token.

    Three cooperating levers, all via ctypes, no admin rights:
      1. MMCSS 'Pro Audio' on THIS thread -- the standard Windows mechanism for
         glitch-free audio; lifts the calling (capture) thread into the audio
         scheduling band.
      2. Fallback SetThreadPriority(TIME_CRITICAL) if MMCSS is unavailable --
         saturates the thread to base priority 15 regardless of process class.
      3. If the whole process sits below Normal (the #72 field finding), lift it
         to ABOVE_NORMAL so the other threads (esp. the live websocket receiver)
         are no longer deprioritised. No-op when already at/above Normal.
    """
    if os.name != 'nt':
        return None

    mmcss_handle = None
    original_class = None
    mechanism = "none"
    detail = ""
    try:
        import ctypes
        # Private WinDLL instances on purpose (same pattern as _enable_vt_mode):
        # never touch the shared ctypes.windll objects other libs configure.
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        # Full signatures: without restype ctypes assumes c_int and would
        # truncate a 64-bit HANDLE.
        kernel32.GetCurrentThread.restype = ctypes.c_void_p   # pseudo-handle
        kernel32.GetCurrentThread.argtypes = ()
        kernel32.SetThreadPriority.restype = ctypes.c_int      # BOOL
        kernel32.SetThreadPriority.argtypes = (ctypes.c_void_p, ctypes.c_int)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p   # pseudo-handle
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetPriorityClass.restype = ctypes.c_uint32    # DWORD
        kernel32.GetPriorityClass.argtypes = (ctypes.c_void_p,)
        kernel32.SetPriorityClass.restype = ctypes.c_int       # BOOL
        kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)

        THREAD_PRIORITY_TIME_CRITICAL = 15
        IDLE_PRIORITY_CLASS = 0x00000040
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000

        # Lever 1: MMCSS 'Pro Audio', in its own guard so a missing avrt.dll
        # (exotic/stripped Windows) still lets the kernel32 fallback run.
        handle = None
        mmcss_err = 0
        try:
            avrt = ctypes.WinDLL('avrt', use_last_error=True)
            avrt.AvSetMmThreadCharacteristicsW.restype = ctypes.c_void_p   # HANDLE
            avrt.AvSetMmThreadCharacteristicsW.argtypes = (
                ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32))
            task_index = ctypes.c_uint32(0)   # in/out, must start at 0
            handle = avrt.AvSetMmThreadCharacteristicsW("Pro Audio",
                                                        ctypes.byref(task_index))
            if not handle:
                mmcss_err = ctypes.get_last_error()
        except OSError as e:
            mmcss_err = getattr(e, "winerror", 0) or -1

        if handle:
            mmcss_handle = handle
            mechanism = "MMCSS 'Pro Audio'"
        else:
            # Lever 2: raise this thread's priority directly.
            if kernel32.SetThreadPriority(kernel32.GetCurrentThread(),
                                          THREAD_PRIORITY_TIME_CRITICAL):
                mechanism = f"TIME_CRITICAL (MMCSS unavailable: err {mmcss_err})"
            else:
                mechanism = (f"none (MMCSS err {mmcss_err}, "
                             f"thread-priority err {ctypes.get_last_error()})")

        # Lever 3: undo a Below-Normal process handicap (#72 field finding). The
        # priority-class constants are NOT ordered by value (NORMAL 0x20 < IDLE
        # 0x40), so gate by explicit membership, never a numeric compare.
        proc = kernel32.GetCurrentProcess()
        current_class = kernel32.GetPriorityClass(proc)
        if current_class in (IDLE_PRIORITY_CLASS, BELOW_NORMAL_PRIORITY_CLASS):
            if kernel32.SetPriorityClass(proc, ABOVE_NORMAL_PRIORITY_CLASS):
                original_class = current_class
                detail = "; process class raised to ABOVE_NORMAL"
            else:
                detail = f"; process-class raise failed (err {ctypes.get_last_error()})"
        # else: already >= Normal -> leave it, nothing to revert.
    except Exception as e:
        # A priority tweak must never endanger the recording it protects.
        logger.warning(f"Capture-thread priority elevation skipped: {e}")
        return (mmcss_handle, original_class)

    logger.info(f"Capture-thread priority: {mechanism}{detail}", extra=FILE_ONLY)
    return (mmcss_handle, original_class)


def _restore_capture_thread_priority(token):
    """Best-effort revert of _elevate_capture_thread_priority (#72). The OS
    reclaims the MMCSS registration and the process class on exit, so this only
    matters for a rare in-process loop restart; it never raises. The thread's own
    TIME_CRITICAL bump is left as-is -- the thread dies right after."""
    if not token:
        return
    mmcss_handle, original_class = token
    if os.name != 'nt':
        return
    try:
        import ctypes
        if mmcss_handle:
            avrt = ctypes.WinDLL('avrt', use_last_error=True)
            avrt.AvRevertMmThreadCharacteristics.restype = ctypes.c_int   # BOOL
            avrt.AvRevertMmThreadCharacteristics.argtypes = (ctypes.c_void_p,)
            avrt.AvRevertMmThreadCharacteristics(mmcss_handle)
        if original_class:
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.GetCurrentProcess.argtypes = ()
            kernel32.SetPriorityClass.restype = ctypes.c_int
            kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), original_class)
    except Exception as e:
        logger.debug(f"Capture-thread priority revert skipped: {e}")


class ThoughtborneApp:
    """Main application class for Thoughtborne (Windows version)"""

    def __init__(self):
        """Initialize the application"""
        # Startup wall is file-only (#61/#109); only the one "starting" line
        # stays as a dim console breadcrumb ahead of the masthead.
        logger.info("=" * 60, extra=FILE_ONLY)
        logger.info("Thoughtborne application starting (Windows version)...")
        logger.info(f"Python Version: {sys.version}", extra=FILE_ONLY)
        logger.info(f"Working directory: {os.getcwd()}", extra=FILE_ONLY)
        logger.info(f"Script directory: {SCRIPT_DIR}", extra=FILE_ONLY)

        # Initialize components
        try:
            self.audio_recorder = AudioRecorder()
            self._startup_fallback_note = None  # set when startup lands off DEFAULT_API (#40)
            self.current_api, self.transcriber = self._create_startup_transcriber()
            self.output_manager = OutputManager(on_task_complete_callback=self._on_output_event)
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}", exc_info=True)
            print(f"ERROR: Initialization error: {e}")
            print("Press Enter to exit...")
            input()
            sys.exit(1)

        # State management
        self.active_threads: List[threading.Thread] = []
        self.processing_counter = 0
        self.processing_lock = threading.Lock()

        # Hotkey state flags
        self.just_finished_recording_a = False
        self.just_finished_recording_d = False
        self.recording_finished_time_a = 0
        self.recording_finished_time_d = 0

        # Timestamp counter for unique IDs
        self.timestamp_counter = 0
        self.timestamp_lock = threading.Lock()

        # Control flag for main loop
        self.running = True

        # Recording loop thread
        self.recording_thread = None
        # Liveness tick for the wedge guard (#128): recording_loop_thread stamps
        # this monotonic timestamp every iteration; on_start_recording treats a
        # stale tick as a wedged loop (is_alive() but pinned in a native audio
        # call) and refuses to start a silent recording. Seeded now so the guard
        # never reads an unset attribute before the loop's first tick.
        self._recording_loop_last_tick = time.monotonic()

        # Live transcriber reference (for sending audio chunks during recording)
        self._active_live_transcriber = None

        # Fallback transcribers for empty Soniox Live transcripts (Issue #1).
        # Lazy singletons so we don't pay the SDK / env-var probe cost when the
        # fallback never fires. The init lock guards the "check + create + assign"
        # race that opens when several Class-B disconnects hit at once: without
        # it, three concurrent worker threads could each construct a new
        # transcriber, of which only one wins the slot. The lock protects
        # creation only; the transcriber.transcribe() calls themselves are
        # thread-safe (V2 opens a fresh SpeechClient per call, V4 uses a fresh
        # httpx request per call) and run outside the lock.
        self._fallback_v2: Optional[SonioxTranscriber] = None
        self._fallback_v4: Optional[SonioxV4Transcriber] = None
        self._fallback_init_lock = threading.Lock()

        # Retry slot (Issue #24): a single reference to the most recent recording
        # whose transcription failed inside the software, so Ctrl+Alt+R can
        # re-transcribe its archived MP3. Written from worker threads at the two
        # failure sites in process_recording_thread / retry_recording_thread,
        # read from the listener thread in on_retry_last_failed. Held in memory
        # only (no persistence across restarts). The lock guards a single
        # reference swap; never held across transcribe work.
        self._last_failed: Optional[_FailedRecording] = None
        self._last_failed_lock = threading.Lock()

        # Exit-salvage state (#49 layer 1): flag + lock make the salvage
        # idempotent across its callers -- the exit hotkey on the listener
        # thread and cleanup() on the main thread (Ctrl+C / Ctrl+Break) can
        # both fire for one shutdown.
        self._salvage_done = False
        self._salvage_lock = threading.Lock()

        # Hotkey manager (initialized in _register_hotkeys)
        self.hotkey_manager = None

        # Push-to-talk (#66): opt-in, DEFAULT OFF. The detector is a pure state
        # machine fed a Win32 keyboard snapshot from the recording loop thread;
        # _ptt_owns_recording records that the CURRENT recording was started by
        # PTT (vs Ctrl+Alt+W), so only a trigger release stops it -- A/D/H/Y do
        # not. While disabled (_ptt is None) the running app never calls into any
        # PTT code, which is the stability guarantee: shipping the feature cannot
        # change the existing flow for anyone who has not opted in.
        self._ptt = None                  # PttDetector when enabled, else None
        # No lock guards the recording state across the two start paths, and none
        # is needed. The detector reaches START only on a BARE trigger (no Alt, no
        # foreign key) -- but firing Ctrl+Alt+W requires Alt held, which vetoes the
        # gesture, so the detector can never be mid-START at the instant a W chord
        # registers. The two start paths are thus mutually exclusive by the gesture
        # rules, not merely by timing. Beyond that: PTT lives solely on the
        # recording-loop thread (serializes against itself), starts only when not
        # is_recording, stops only a recording it owns, resets to inert every tick
        # a non-owned recording is active, and both start/stop re-check is_recording.
        self._ptt_owns_recording = False  # True from PTT start until its stop
        self._ptt_trigger_vk = PTT_TRIGGER_VK
        self._ptt_insert = PTT_INSERT
        # Per-instance foreign-key set: the base curated set minus the active
        # trigger VK, so a bare trigger press never reads as its own foreign key
        # (load-bearing when the trigger is Left-Alt, which is in the base set).
        self._ptt_foreign_vks = _PTT_FOREIGN_VKS - {PTT_TRIGGER_VK}
        if PTT_ENABLED:
            self._ptt = PttDetector(PTT_TAP_WINDOW_S, PTT_MIN_HOLD_S, PTT_RELEASE_TAIL_S)
            logger.info(
                f"Push-to-talk ENABLED (#66): trigger_vk=0x{PTT_TRIGGER_VK:02X}, "
                f"insert={PTT_INSERT}, tap_window={PTT_TAP_WINDOW_S}s, "
                f"min_hold={PTT_MIN_HOLD_S}s, release_tail={PTT_RELEASE_TAIL_S}s")

        logger.info(f"Configuration: Default API={DEFAULT_API}, Max parallel={MAX_PARALLEL_TRANSCRIPTIONS}",
                    extra=FILE_ONLY)
        logger.info(f"Current transcriber: {self.transcriber.get_name()}", extra=FILE_ONLY)
        logger.info("Application initialized successfully", extra=FILE_ONLY)

    def get_unique_timestamp(self) -> str:
        """Generate a unique timestamp with counter"""
        with self.timestamp_lock:
            self.timestamp_counter += 1
            return f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.timestamp_counter:03d}"

    def _record_failed_slot(self, timestamp: str, duration: float):
        """Mark a recording as the most recent failed transcription (Issue #24).

        Called from worker threads at the two failure sites. Reconstructs the
        deterministic archive path (audio_handler.save_recording writes
        ARCHIVE_FOLDER / voice_<ts>.mp3) and stores it in the retry slot --
        newest failure wins. Skips when the archive is missing: an exception
        before save_recording leaves nothing to retry, so do not park an
        unretryable pointer. Also drops a persistent .needsretry marker (#114)
        so the slot survives restarts, best-effort after the in-memory arm."""
        archived_mp3 = ARCHIVE_FOLDER / f"voice_{timestamp}.mp3"
        if not archived_mp3.exists():
            logger.warning(f"Not arming retry slot -- archive missing for {timestamp}: {archived_mp3}")
            return
        rec = _FailedRecording(
            archived_mp3_path=str(archived_mp3),
            duration=duration,
            origin_timestamp=timestamp,
        )
        with self._last_failed_lock:
            self._last_failed = rec
        logger.info(f"Retry slot armed: failed recording from {timestamp} (Ctrl+Alt+R to retry)",
                    extra=FILE_ONLY)
        # Persist for cross-restart retry (#114): the in-memory arm above is the
        # critical path; this marker is a best-effort add after it, so a marker
        # failure never affects the current session's retry. Written outside
        # _last_failed_lock (the lock guards only the reference swap).
        write_retry_marker(archived_mp3, duration)

    def _resolve_failed_slot(self, rec: '_FailedRecording'):
        """Clear the retry slot after a successful retry of rec (Issue #24).

        Identity check: only clear if the slot still points at the recording we
        just retried. A different recording may have failed and overwritten the
        slot while this (long) retry ran -- that newer failure must survive."""
        with self._last_failed_lock:
            if self._last_failed is rec:
                self._last_failed = None

    def process_recording_thread(self, frames: List[bytes], duration: float,
                                sequence_number: int, timestamp: str,
                                use_clipboard: bool = False, auto_insert: bool = True,
                                send_after_insert: bool = False, wait_for_keys: List[str] = None,
                                transcriber=None, sidecar=None):
        """Process a recording in a separate thread"""
        thread_name = threading.current_thread().name

        # Use the transcriber that was active when recording started
        if transcriber is None:
            transcriber = self.transcriber

        # Increment processing counter
        with self.processing_lock:
            self.processing_counter += 1
            current_count = self.processing_counter

        logger.info(f"[{thread_name}] Starting processing for sequence {sequence_number} with {transcriber.get_name()} (active: {current_count})", extra=FILE_ONLY)

        # Create task
        task = TranscriptionTask(
            sequence_number=sequence_number,
            timestamp=timestamp,
            use_clipboard=use_clipboard,
            auto_insert=auto_insert,
            send_after_insert=send_after_insert,
            wait_for_key_release=(wait_for_keys is not None),
            trigger_keys=wait_for_keys
        )

        try:
            # Save recording
            wav_path, mp3_path = self.audio_recorder.save_recording(frames, timestamp)
            # Archive copy exists -- the crash-safety sidecar has done its job
            # (#49). On the exception path below it is deliberately NOT
            # discarded, so the next start recovers what this save lost.
            if sidecar is not None:
                sidecar.discard()
            self._ticker(f"[Seq: {sequence_number}] Audio saved and archived")

            # Transcribe with the fixed transcriber, and resolve the engine that
            # actually produced the text (#62). The hybrid 'soniox' slot chooses
            # V2 sync or V4 async per recording, so it reports through a per-call
            # sink; every other slot maps straight from its type.
            self._ticker(f"[Seq: {sequence_number}] Transcribing with {transcriber.get_name()}...")
            if isinstance(transcriber, SonioxTranscriber):
                self._ticker(f"[Seq: {sequence_number}] {transcriber.engine_choice_line(duration)}")
                engine_tag = _EngineTag()
                transcript = transcriber.transcribe(mp3_path, duration, engine_sink=engine_tag)
                engine = engine_tag.code
            else:
                transcript = transcriber.transcribe(mp3_path, duration)
                engine = engine_code(transcriber)
            transcript = transcript.rstrip('\n')

            # Issue #1: empty live transcript -> file-based fallback on the
            # already-archived MP3. Restricted to SonioxLiveTranscriber: empty
            # results from V2/V4-async/Groq already mean the
            # file-based path was tried and failed, so a second pass would not
            # help. Runs before cleanup_temp_files so mp3_path still exists.
            in_session_any_error = False
            if not transcript and isinstance(transcriber, SonioxLiveTranscriber):
                transcript, fallback_engine, in_session_any_error = self._run_empty_transcript_fallback(
                    mp3_path=mp3_path,
                    duration=duration,
                    sequence_number=sequence_number,
                    thread_name=thread_name,
                )
                if transcript:
                    engine = fallback_engine

            # #133: an empty result is only an honest "no speech" verdict when the
            # Soniox Live file-fallback chain actually ran end to end AND every stage
            # came back clean (no transport/API error) -- the one place a silent
            # recording is verifiable. A non-live engine (soniox/groq) returning empty
            # is ambiguous: it swallows transport/API errors internally (transcriber.py
            # returns "" on both auth and generic failures), so empty could be a real
            # outage. Such a result stays the conservative FAILED + retryable path.
            no_speech_verdict = (
                isinstance(transcriber, SonioxLiveTranscriber) and not in_session_any_error)

            # Save transcript, then tag the archived recording with the same
            # engine token so the audio<->transcript pair shows the engine (#62).
            if transcript:
                transcriber.save_transcript(transcript, timestamp, engine=engine)
                self.audio_recorder.tag_archive_with_engine(timestamp, engine)
                self.output_manager.update_last_transcript(transcript)

                # Update task
                task.transcript = transcript
                task.is_complete = True
                task.use_clipboard = use_clipboard

                logger.info(f"[{thread_name}] Transcription for sequence {sequence_number} ready", extra=FILE_ONLY)
                self._ticker(f"[Seq: {sequence_number}] Transcription completed, waiting for output...")
            elif no_speech_verdict:
                # #133: the live chain ran and every stage came back clean-but-empty --
                # the recording held no speech. A retry cannot help, so a genuinely
                # silent dictation writes NO marker (killing #134's nag at the root):
                # say so honestly (the calm 'no speech' panel) and arm no retry.
                logger.info(f"[{thread_name}] No speech in sequence {sequence_number} "
                            f"-- honest verdict, no retry marker", extra=FILE_ONLY)
                task.no_speech = True
                task.is_complete = True
            else:
                # Empty without a clean live chain: either the live chain hit a
                # transport/API error, or a non-live engine came back empty (and those
                # cannot tell silence from a swallowed outage). Stay conservative -- the
                # error task drives the FAILED panel via the OutputManager (#109) and
                # arms the retry slot + persistent marker (#114/#24), so a switch-and-
                # retry can route around an outage, exactly as before #133.
                logger.warning(f"[{thread_name}] Empty transcription for sequence {sequence_number} "
                               f"-- staying retryable", extra=FILE_ONLY)
                task.is_error = True
                task.is_complete = True
                self._record_failed_slot(timestamp, duration)

            # Add to output queue
            self.output_manager.add_task(task)

            # Cleanup temp files
            self.audio_recorder.cleanup_temp_files(wav_path, mp3_path)

        except Exception as e:
            logger.error(f"[{thread_name}] Error processing sequence {sequence_number}: {e}", exc_info=True)
            # console shows the dim red ERROR log line; the FAILED panel follows

            # Mark task as error
            task.is_error = True
            task.is_complete = True
            self._record_failed_slot(timestamp, duration)
            self.output_manager.add_task(task)

        finally:
            # Decrement processing counter
            with self.processing_lock:
                self.processing_counter -= 1
                current_count = self.processing_counter
            logger.info(f"[{thread_name}] Processing for sequence {sequence_number} finished (active: {current_count})", extra=FILE_ONLY)

    def start_processing_thread(self, frames: List[bytes], duration: float,
                               use_clipboard: bool = False, auto_insert: bool = True,
                               send_after_insert: bool = False, wait_for_keys: List[str] = None,
                               transcriber_override=None, sidecar=None) -> bool:
        """
        Start a new processing thread

        Args:
            frames: Audio frames
            duration: Recording duration
            use_clipboard: Use clipboard for insertion (vs keyboard typing)
            auto_insert: Automatically insert text after transcription (False = save for later)
            send_after_insert: Press Enter after inserting (for sending messages)
            transcriber_override: Use this transcriber instead of self.transcriber
                                  (needed for live transcribers that hold session state)
            sidecar: SidecarHandle of this recording's crash-safety file (#49);
                     the worker discards it once the archive MP3 exists. On a
                     False return (parallel limit reached) the handle stays
                     undiscarded on purpose -- the .partial is then the only
                     surviving copy and the next start recovers it.
        """
        # Clean up finished threads
        self.active_threads = [t for t in self.active_threads if t.is_alive()]

        # Check limit
        if len(self.active_threads) >= MAX_PARALLEL_TRANSCRIPTIONS:
            logger.warning(f"Maximum parallel transcriptions reached ({MAX_PARALLEL_TRANSCRIPTIONS}) -- please wait")
            return False

        # Get sequence number and timestamp
        sequence_number = self.output_manager.get_next_sequence_number()
        timestamp = self.get_unique_timestamp()

        # Use override transcriber (e.g. live transcriber with active session) or current
        transcriber = transcriber_override if transcriber_override is not None else self.transcriber

        # Start new thread with the selected transcriber
        thread = threading.Thread(
            target=self.process_recording_thread,
            args=(frames, duration, sequence_number, timestamp, use_clipboard, auto_insert, send_after_insert, wait_for_keys, transcriber, sidecar),
            name=f"Transcription-{sequence_number}-{datetime.datetime.now().strftime('%H%M%S%f')}"
        )
        thread.daemon = True
        thread.start()
        self.active_threads.append(thread)

        logger.info(f"New processing thread started: {thread.name} (Seq: {sequence_number}) using {transcriber.get_name()}",
                    extra=FILE_ONLY)
        return True

    def _run_empty_transcript_fallback(self, mp3_path: str, duration: float,
                                       sequence_number: int, thread_name: str) -> Tuple[str, str, bool]:
        """Fall back to a file-based Soniox API when SonioxLive returned empty.

        Triggered from process_recording_thread when SonioxLiveTranscriber yields
        an empty transcript -- typically a Class-B failure: the WebSocket was
        closed by the server (e.g. 1011 keepalive ping timeout under sustained
        TCP backpressure) before the stop hotkey, so the live transcript is
        empty even though the MP3 is fully archived. Issue #1.

        Choice of fallback API depends on recording duration:
          - duration < SHORT_AUDIO_THRESHOLD (58 s) -> raw Soniox V2 sync
            (SonioxTranscriber._transcribe_v2_sync, ~2-3 s for 30 s audio); if
            V2 fails (exception or empty result), fall through to
            SonioxV4Transcriber. This second hop also covers the case where V2
            (legacy gRPC) is shut down by Soniox.
          - duration >= SHORT_AUDIO_THRESHOLD       -> SonioxV4Transcriber (V4
            async polling, ~10-60 s; only option past V2's 60 s hard limit).

        Since #31 the 'soniox' slot runs the same V2 -> V4 fallback internally
        (on exceptions only); the cascade therefore calls the raw V2 primitive
        so its own V2 -> V4 hop -- which also covers empty results -- stays in
        one place up here.

        Note: this assumes SHORT_AUDIO_THRESHOLD < 60 s, because the V2 stage
        runs the sync path unconditionally and Soniox's sync API has a 60 s
        hard limit. Raising SHORT_AUDIO_THRESHOLD above 60 would make V2
        attempt the sync path on recordings it can't handle; the fallthrough
        to V4 would still recover the transcript, but the fast-path latency
        advertised above is gone.

        All fallback transcribers are lazily instantiated on first use and
        cached as singletons on the app instance. The fallback is not
        interruptible: Ctrl+Alt+X is a no-op here because audio recording has
        already stopped.

        Args:
            mp3_path: Path to the (still-existing) temp MP3.
            duration: Recording duration in seconds.
            sequence_number: For console / log correlation.
            thread_name: For log correlation.

        Returns:
            (transcript, engine, any_error) -- the transcript plus the token of
            the engine that produced it (ENGINE_TOKENS["soniox_v2"] or
            ["soniox_v4"], #62), plus a flag that is meaningful only when the
            transcript is empty: any_error is True when at least one stage failed
            with a transport/API error, and False when every stage that ran came
            back clean-but-empty. The caller uses it to tell a real outage (keep
            the retry marker) from a genuinely silent recording ("no speech", a
            final verdict -- #133). On success any_error is False and ignored;
            ("", "", False) is a clean all-empty, ("", "", True) an outage.
        """
        try_v2_first = duration < SHORT_AUDIO_THRESHOLD
        primary_label = "Soniox V2 (sync)" if try_v2_first else "Soniox V4 (async)"

        # Technical engine labels stay in the file log (file-only, unchanged);
        # the console gets one calm dim ticker line (#109). Emitted before the
        # attempt so it shows even if the lazy init below raises.
        logger.info(
            f"[{thread_name}] Empty live transcript for sequence {sequence_number} "
            f"(duration: {duration:.1f}s) -- falling back to {primary_label}",
            extra=FILE_ONLY
        )
        self._ticker(f"[Seq: {sequence_number}] Live came back empty -- "
                     f"retrying from the saved audio file...")

        # any_error aggregates whether any stage hit a real transport/API error;
        # it only matters when the final transcript is empty (#133).
        any_error = False

        # Short recordings: try V2, fall through to V4 on failure / empty.
        # Long recordings: V4 is the only option (V2 has a 60 s hard limit).
        if try_v2_first:
            transcript, errored = self._try_fallback(
                kind="v2",
                mp3_path=mp3_path,
                duration=duration,
                sequence_number=sequence_number,
                thread_name=thread_name,
            )
            if transcript:
                return transcript, ENGINE_TOKENS["soniox_v2"], False
            any_error = any_error or errored

            # V2 failed or returned empty. Spec #1 requires we still try V4
            # so that "Empty transcription" only surfaces when both fail (and
            # so the tool keeps working if V2 is ever shut down by Soniox).
            logger.info(
                f"[{thread_name}] V2 fallback unproductive for sequence "
                f"{sequence_number} -- falling through to Soniox V4 (async)",
                extra=FILE_ONLY
            )
            self._ticker(f"[Seq: {sequence_number}] Still empty -- trying the second file engine...")

        transcript, errored = self._try_fallback(
            kind="v4",
            mp3_path=mp3_path,
            duration=duration,
            sequence_number=sequence_number,
            thread_name=thread_name,
        )
        any_error = any_error or errored

        if not transcript:
            # Every file-based API we tried also returned nothing on top of
            # the empty Live result. For short recordings that means Live +
            # V2 + V4 all failed (triple failure); for long recordings V2 is
            # skipped because of its 60 s hard limit, so it's Live + V4 only.
            stages = "Live + V2 + V4" if try_v2_first else "Live + V4"
            # The exact engine stages stay in the file log (file-only); the
            # console gets the de-technicalized red exhausted line (#109), which
            # reads as part of the FAILED panel that follows.
            logger.error(
                f"[{thread_name}] All fallbacks exhausted for sequence "
                f"{sequence_number} ({stages} all empty / failed)",
                extra=FILE_ONLY
            )
            self._ticker(f"[Seq: {sequence_number}] All fallbacks exhausted "
                         f"(live + file engines all failed)", error=True)

        return transcript, (ENGINE_TOKENS["soniox_v4"] if transcript else ""), any_error

    def _try_fallback(self, kind: str, mp3_path: str, duration: float,
                      sequence_number: int, thread_name: str) -> Tuple[str, bool]:
        """Run a single fallback transcriber attempt. Helper for _run_empty_transcript_fallback.

        Lazily instantiates the requested transcriber (V2 or V4) under the init
        lock, then runs the transcription call outside the lock so parallel
        fallbacks don't serialize on the network call. Any exception is caught
        and logged -- this method always returns, never raises.

        Args:
            kind: "v2" or "v4".
            mp3_path: Path to the temp MP3.
            duration: Recording duration in seconds.
            sequence_number: For console / log correlation.
            thread_name: For log correlation.

        Returns:
            (transcript, errored). transcript is "" when this attempt failed or
            returned empty. errored is True only for a real transport/API failure
            (auth error, any raised exception, or a failure the V4 stage reported
            through its error sink -- #141) -- the signal #133 needs to tell
            "engine ran clean and found no speech" (errored=False, a final verdict)
            apart from "the engine could not be reached" (errored=True, keep the
            retry marker). A clean empty result and the SDK-not-installed skip (a
            configuration state, not an outage) both report errored=False.
        """
        # This label reaches the console via the "raised" ERROR below, so its V4
        # half is generation-neutral (#124); V2/sync stays technical (legacy gRPC).
        label = "Soniox V2 (sync)" if kind == "v2" else "Soniox async"

        try:
            with self._fallback_init_lock:
                if kind == "v2" and self._fallback_v2 is None:
                    self._fallback_v2 = SonioxTranscriber()
                    logger.info("Soniox V2 fallback transcriber initialized", extra=FILE_ONLY)
                elif kind == "v4" and self._fallback_v4 is None:
                    self._fallback_v4 = SonioxV4Transcriber()
                    logger.info("Soniox V4 fallback transcriber initialized", extra=FILE_ONLY)

            fallback = self._fallback_v2 if kind == "v2" else self._fallback_v4

            # SDK-less is an expected, configuration-side state: skip the V2
            # stage with one INFO line instead of letting _transcribe_v2_sync
            # raise into the ERROR-plus-traceback path below.
            if kind == "v2" and not fallback._v2_available:
                logger.info(
                    f"[{thread_name}] V2 fallback stage skipped for sequence "
                    f"{sequence_number} (Soniox SDK not installed)"
                )
                return "", False  # a config state, not a transport error (#133)

            start = time.time()
            errored = False
            if kind == "v2":
                # Raw V2 sync, without the slot's internal V4 fallback -- the
                # cascade does its own V2 -> V4 hop (incl. on empty results)
                # one level up (#31).
                transcript = fallback._transcribe_v2_sync(mp3_path, duration).rstrip('\n')
            else:
                # V4 swallows its transport/API errors and returns "" (its slot
                # contract); the sink is how a real outage still reaches this
                # chain's any_error aggregation instead of masquerading as a
                # clean empty (#141).
                error_tag = _ErrorTag()
                transcript = fallback.transcribe(
                    mp3_path, duration, error_sink=error_tag).rstrip('\n')
                errored = error_tag.errored
            elapsed = time.time() - start

            if transcript:
                logger.info(
                    f"[{thread_name}] Fallback ({label}) succeeded for "
                    f"sequence {sequence_number} in {elapsed:.2f}s "
                    f"({len(transcript)} chars)",
                    extra=FILE_ONLY
                )
                self._ticker(f"[Seq: {sequence_number}] File engine succeeded "
                             f"({elapsed:.1f}s, {len(transcript)} chars)")
            else:
                # The per-stage empty note (with its technical engine label) stays
                # file-only; the higher-level ticker narrates the cascade (#109).
                logger.warning(
                    f"[{thread_name}] Fallback ({label}) returned empty for "
                    f"sequence {sequence_number} after {elapsed:.2f}s",
                    extra=FILE_ONLY
                )

            # V2 clean run stays False; V4 reports a real outage through the
            # sink so it reaches the chain's any_error aggregation (#141).
            return transcript, errored

        except Exception as e:
            if kind == "v2" and SonioxTranscriber._is_auth_error(e):
                # The V4 stage that follows surfaces the single [AUTH] line
                # (#32); an ERROR here would just duplicate it on the console.
                logger.debug(
                    f"[{thread_name}] Fallback ({label}) auth failure for "
                    f"sequence {sequence_number}: {e}",
                    exc_info=True
                )
                return "", True  # a broken key is a real failure -> keep the marker
            logger.error(
                f"[{thread_name}] Fallback ({label}) raised for "
                f"sequence {sequence_number}: {_one_line_error(e)}",
                exc_info=True
            )
            return "", True

    def handle_test_transcription(self):
        """Handle test transcription request"""
        # Try WAV first, then MP3
        test_file = SCRIPT_DIR / "test_audio.wav"
        if not test_file.exists():
            test_file = SCRIPT_DIR / "test_audio.mp3"

        logger.info("TEST MODE activated")

        if test_file.exists():
            logger.info(f"Testing with file: {test_file}")

            # Test transcription
            result = self.transcriber.test_transcription(str(test_file))

            if result:
                self.output_manager.update_last_transcript(result)
                preview = result[:200] + "..." if len(result) > 200 else result
                logger.info(f"Test transcription successful ({len(result)} chars): {preview}")

                # Add to output queue with negative sequence number
                test_task = TranscriptionTask(
                    sequence_number=self.output_manager.get_next_immediate_sequence_number(),
                    timestamp=self.get_unique_timestamp() + "_TEST",
                    transcript=result,
                    is_complete=True,
                    is_immediate=True
                )
                self.output_manager.add_task(test_task)

                logger.info("Test text will be inserted...")
            else:
                logger.warning("Test: no transcription received")
                # No task exists on this path, so the OutputManager funnel never
                # sees it -- emit the failure panel directly (#109). Runs on the
                # listener thread: _emit_block only enqueues (#11).
                self._emit_block(
                    'self-test-failed',
                    lambda ansi, compact: console_ui.render_selftest_failed(
                        "self-test failed -- no transcription received",
                        ("check your API key in .env,",
                         f"then see {LOG_FILE.name} for details"),
                        ansi=ansi, compact=compact))

            logger.info("Test completed")
        else:
            logger.error(f"Test file not found: {test_file}")
            logger.error("Place a file named 'test_audio.wav' or 'test_audio.mp3' in the script directory.")
            self._emit_block(
                'self-test-failed',
                lambda ansi, compact: console_ui.render_selftest_failed(
                    "self-test failed -- no test audio file found",
                    ("place test_audio.wav or test_audio.mp3",
                     "in the project folder, then retry"),
                    ansi=ansi, compact=compact))

    def _create_startup_transcriber(self):
        """Construct the startup transcriber, falling through the carousel (#40).

        Tries DEFAULT_API first, then the remaining AVAILABLE_APIS entries in
        carousel order. Returns (api_name, transcriber). When no entry is
        constructible (typically a newcomer without any key), prints an
        actionable error block and exits cleanly -- API keys are read once at
        import (config.py), so a restart after editing .env is required either
        way. Runs on the main thread before hotkeys exist, so print() is safe.
        """
        try:
            start_index = AVAILABLE_APIS.index(DEFAULT_API)
            candidates = [AVAILABLE_APIS[(start_index + step) % len(AVAILABLE_APIS)]
                          for step in range(len(AVAILABLE_APIS))]
        except ValueError:
            # DEFAULT_API was edited to something unknown (config.py): try it
            # first anyway -- the factory's "Unknown API" error then shows up
            # as a skip line naming it -- and fall through to the real entries.
            candidates = [DEFAULT_API] + list(AVAILABLE_APIS)

        failures = []  # (api_name, exception) in attempt order
        for api_name in candidates:
            try:
                transcriber = create_transcriber(api_name)
            except Exception as e:
                failures.append((api_name, e))
                # Slot-IDs stay file-only (#44/#109): the console user learns of a
                # fallback via the masthead NOTE, or of an all-failed start via the
                # SETUP-NEEDED panel -- both name the missing env vars in labels.
                logger.warning(f"Skipped {api_name} ({_describe_construction_failure(e)})",
                               extra=FILE_ONLY)
                logger.debug(f"Construction failed for {api_name}: {e}", exc_info=True)
                continue

            if api_name != DEFAULT_API:
                missing = sorted({err.env_var for _, err in failures
                                  if isinstance(err, MissingAPIKeyError)})
                reason = (f"{' / '.join(missing)} missing" if missing
                          else f"default API '{DEFAULT_API}' unavailable")
                # File log keeps the slot-id form (greppable, unchanged); the
                # masthead NOTE shows display labels (#109). Both file-only --
                # the panel is the console surface.
                logger.warning(f"{reason} -> started on {api_name} (default: {DEFAULT_API})",
                               extra=FILE_ONLY)
                started = API_DISPLAY.get(api_name, {}).get("label", api_name)
                default_label = API_DISPLAY.get(DEFAULT_API, {}).get("label", DEFAULT_API)
                self._startup_fallback_note = (
                    f"{reason} -> started on {started} (default: {default_label})")
            return api_name, transcriber

        self._print_no_api_error_block(failures)
        print("Press Enter to exit...")
        input()
        sys.exit(1)

    @staticmethod
    def _print_no_api_error_block(failures):
        """Actionable SETUP-NEEDED panel when no transcription API is
        constructible (#40/#109). Printed, not queued: it runs on the main
        thread before any thread starts, and the 'Press Enter to exit' prompt
        below must appear strictly after it (queue ordering could not guarantee
        that against the input() prompt)."""
        missing = {}   # env var -> [slot ids]
        other = []     # (slot id, reason) for non-key construction failures
        for api_name, error in failures:
            if isinstance(error, MissingAPIKeyError):
                missing.setdefault(error.env_var, []).append(api_name)
            else:
                other.append((api_name, f"{type(error).__name__}: {error}"))

        logger.error("No transcription API could be constructed -- tried: "
                     + ", ".join(api for api, _ in failures), extra=FILE_ONLY)
        try:
            compact = shutil.get_terminal_size((80, 25)).columns < console_ui.COMPACT_THRESHOLD
            lines = console_ui.render_noapi_panel(
                list(missing.items()), other, str(SCRIPT_DIR),
                ansi=_ANSI_ENABLED, compact=compact)
            print("\n" + "\n".join(lines))
        except Exception as e:
            logger.debug(f"No-API panel render failed: {e}")
            print("\nERROR: No transcription API available -- add a key to .env "
                  "(copy .env.example to .env, see the README).")

    def switch_api(self):
        """Switch to the next constructible transcription API (#40).

        Skips entries whose transcriber construction fails (typically a
        missing API key) with one console line per skip. If the loop comes
        full circle, stays on the current transcriber and names the missing
        keys. Runs on the hotkey-listener thread: console output must go
        through logger.* (#11), never print().
        """
        try:
            current_index = AVAILABLE_APIS.index(self.current_api)
            skipped = []  # (api_name, exception) in attempt order

            for step in range(1, len(AVAILABLE_APIS)):
                next_api = AVAILABLE_APIS[(current_index + step) % len(AVAILABLE_APIS)]
                try:
                    new_transcriber = create_transcriber(next_api)
                except Exception as e:
                    skipped.append((next_api, e))
                    # Slot-IDs stay file-only (#44/#109): a successful switch shows
                    # the display label via the SWITCHED panel, a full-circle failure
                    # names the missing env vars in the FAILED panel below.
                    logger.warning(f"Skipped {next_api} ({_describe_construction_failure(e)})",
                                   extra=FILE_ONLY)
                    logger.debug(f"Construction failed for {next_api}: {e}", exc_info=True)
                    continue

                old_label = self.transcriber.get_name()
                # Slot-IDs stay file-only; the console dim ticker speaks display
                # labels, the panel is the surface (#44/#109).
                logger.info(f"Switching API from {self.current_api} to {next_api}", extra=FILE_ONLY)
                self.transcriber = new_transcriber
                self.current_api = next_api
                new_label = self.transcriber.get_name()
                logger.info(f"Successfully switched to {new_label}", extra=FILE_ONLY)
                self._ticker(f"switch: {old_label} -> {new_label}")
                switch_key = self._format_hotkey(HOTKEYS['switch_api'])
                self._emit_block(
                    'switched',
                    lambda ansi, compact: console_ui.render_switched_panel(
                        new_label, self._lineup_data(), switch_key,
                        ansi=ansi, compact=compact))
                return

            # Full circle: no other entry is constructible -- stay put.
            missing = sorted({e.env_var for _, e in skipped
                              if isinstance(e, MissingAPIKeyError)})
            current_label = self.transcriber.get_name()
            logger.error(f"No other API available -- staying on {current_label}", extra=FILE_ONLY)
            if missing:
                logger.error(f"Missing API key(s): {', '.join(missing)} -- add them to "
                             f".env (see README), then restart Thoughtborne.", extra=FILE_ONLY)
            switch_key = self._format_hotkey(HOTKEYS['switch_api'])
            self._emit_block(
                'switch-failed',
                lambda ansi, compact: console_ui.render_switch_failed(
                    current_label, self._lineup_data(), switch_key, missing,
                    ansi=ansi, compact=compact))

        except Exception as e:
            logger.error(f"Error in API switch: {e}", exc_info=True)

    # ===== HOTKEY CALLBACKS =====

    def _format_hotkey(self, hotkey_str):
        """Format hotkey string for display (e.g., 'ctrl+alt+w' -> 'Ctrl+Alt+W')"""
        parts = hotkey_str.split('+')
        formatted_parts = [p.capitalize() for p in parts]
        return '+'.join(formatted_parts)

    def _handle_mistrigger_during_recording(self) -> bool:
        """
        Detect and handle mis-triggers of start_recording while already recording.

        With RegisterHotKey this should be less likely than with the keyboard
        library's WH_KEYBOARD_LL hook, but kept as a safety net. Uses
        GetAsyncKeyState via is_key_pressed() to check physical key state.

        Returns:
            True if a mis-trigger was detected and handled, False otherwise.
        """
        # Map of keys to their corresponding actions
        key_to_action = {
            'a': ('stop_recording_keyboard', self.on_stop_recording_keyboard),
            'd': ('stop_recording_clipboard', self.on_stop_recording_clipboard),
            'h': ('stop_recording_send', self.on_stop_recording_send),
            'y': ('stop_recording_no_insert', self.on_stop_recording_no_insert),
            'x': ('cancel_recording', self.on_cancel_recording),
        }

        for key_char, (action_name, action_func) in key_to_action.items():
            if is_key_pressed(key_char):
                logger.warning(f"Mis-trigger detected: start_recording triggered but '{key_char.upper()}' is pressed")
                logger.info(f"Correcting to {action_name}")
                action_func()
                return True

        return False

    def on_start_recording(self):
        """Callback for start recording hotkey"""
        if not self.audio_recorder.is_recording:
            hotkey_display = self._format_hotkey(HOTKEYS['start_recording'])
            logger.info(f"Recording started ({hotkey_display})", extra=FILE_ONLY)
            logger.debug("on_start_recording: marker A - after info log line")
            logger.debug("on_start_recording: marker B - before loop-alive check")

            # DEBUG: Check if recording loop thread is alive
            if self.recording_thread and self.recording_thread.is_alive():
                logger.debug("Recording loop thread is ALIVE")
                # Alive is not enough: a native audio call (e.g. get_read_available)
                # can wedge inside record_chunk() and pin the recording loop there
                # forever, holding _stream_lock, while Layer 2 keeps the hotkeys
                # alive (#128). Such a thread is is_alive() yet never captures
                # another chunk, so starting here would produce a silent
                # "recording". A frozen liveness tick is the tell -- refuse to
                # start and point the user at a restart. (Only the W-flow needs
                # this: push-to-talk runs on the recording loop itself, so a
                # wedged loop never fires PTT in the first place.)
                tick_age = time.monotonic() - self._recording_loop_last_tick
                if tick_age > RECORDING_LOOP_STALE_SECONDS:
                    logger.error(f"Recording loop unresponsive: last tick {tick_age:.0f}s ago "
                                 f"(threshold {RECORDING_LOOP_STALE_SECONDS:.0f}s)", extra=FILE_ONLY)
                    logger.error("The recording loop is not responding (wedged audio driver) -- please restart Thoughtborne.")
                    return
            else:
                logger.error("Recording loop thread is DEAD!")
                logger.error("Recording loop thread has died. Please restart the application.")
                return

            logger.debug("on_start_recording: marker C - before audio_recorder.start_recording()")
            # Start recording (this also opens the audio stream)
            if not self.audio_recorder.start_recording():
                logger.error("Failed to start recording - audio stream could not be opened")
                logger.error("Could not open audio stream. Check audio device connection.")
                return
            logger.debug("on_start_recording: marker D - audio_recorder.start_recording() returned OK")

            # REC strip once the mic is actually open (#109): shows the stop
            # options the user needs right now. Only the W-flow gets it -- PTT
            # stops on a trigger release, so those stop hints would be wrong.
            self._emit_block(
                'recording',
                lambda ansi, compact: console_ui.render_rec_strip(
                    self._key_letter('stop_recording_keyboard'),
                    self._key_letter('stop_recording_clipboard'),
                    self._key_letter('stop_recording_send'),
                    self._key_letter('stop_recording_no_insert'),
                    self._key_letter('cancel_recording'),
                    self._key_prefix(),
                    ansi=ansi, compact=compact))

            # Start live streaming session if transcriber supports it
            if self.transcriber.is_live:
                self._active_live_transcriber = self.transcriber
                if not self._active_live_transcriber.start_session():
                    logger.error("Failed to start live streaming session")
                    logger.warning("Live session failed to start")
                    self._active_live_transcriber = None
            logger.debug("on_start_recording: marker E - callback complete, returning to listener message pump")
        else:
            # Already recording - check if this is a mis-trigger (keyboard library bug)
            # where a stop hotkey was pressed but start_recording was triggered instead
            if self._handle_mistrigger_during_recording():
                return  # Mis-trigger was handled, correct action executed

            # No mis-trigger detected - user might have accidentally pressed W again
            logger.debug("start_recording ignored - already recording (no mis-trigger detected)")

    def on_stop_recording_keyboard(self):
        """Callback for stop recording / insert last text (keyboard mode)"""
        hotkey_display = self._format_hotkey(HOTKEYS['stop_recording_keyboard'])
        start_hotkey_display = self._format_hotkey(HOTKEYS['start_recording'])

        if self.audio_recorder.is_recording:
            # Stop recording
            logger.info(f"Recording stopped ({hotkey_display})")

            frames, duration = self.audio_recorder.stop_recording()
            sidecar = self.audio_recorder.take_finished_sidecar()
            self.just_finished_recording_a = True
            self.recording_finished_time_a = time.time()

            # Capture live transcriber reference before clearing
            recording_transcriber = self._active_live_transcriber or self.transcriber
            self._active_live_transcriber = None

            logger.info(f"Recording duration: {duration:.1f} seconds")

            # Start processing with wait for key release
            if self.start_processing_thread(frames, duration, wait_for_keys=['ctrl', 'alt', 'a'],
                                            transcriber_override=recording_transcriber,
                                            sidecar=sidecar):
                logger.info("Processing in background...", extra=FILE_ONLY)
                logger.info(f"You can start a new recording with {start_hotkey_display}!", extra=FILE_ONLY)

        elif not self.just_finished_recording_a:
            # Insert last text
            logger.debug(f"{hotkey_display} pressed - inserting last text (keyboard mode)")
            self.output_manager.insert_last_transcript(wait_for_keys=['ctrl', 'alt', 'a'])

    def on_stop_recording_clipboard(self):
        """Callback for stop recording / insert last text (clipboard mode)"""
        hotkey_display = self._format_hotkey(HOTKEYS['stop_recording_clipboard'])
        start_hotkey_display = self._format_hotkey(HOTKEYS['start_recording'])

        if self.audio_recorder.is_recording:
            # Stop recording (clipboard mode)
            logger.info(f"Recording stopped ({hotkey_display} - clipboard mode)")

            frames, duration = self.audio_recorder.stop_recording()
            sidecar = self.audio_recorder.take_finished_sidecar()
            self.just_finished_recording_d = True
            self.recording_finished_time_d = time.time()

            # Capture live transcriber reference before clearing
            recording_transcriber = self._active_live_transcriber or self.transcriber
            self._active_live_transcriber = None

            logger.info(f"Recording duration: {duration:.1f} seconds")

            # Start processing with clipboard flag and wait for key release
            if self.start_processing_thread(frames, duration, use_clipboard=True, wait_for_keys=['ctrl', 'alt', 'd'],
                                            transcriber_override=recording_transcriber,
                                            sidecar=sidecar):
                logger.info("Processing in background (clipboard mode)...", extra=FILE_ONLY)
                logger.info(f"You can start a new recording with {start_hotkey_display}!", extra=FILE_ONLY)

        elif not self.just_finished_recording_d:
            # Insert last text via clipboard
            logger.debug(f"{hotkey_display} pressed - inserting last text (clipboard mode)")
            self.output_manager.insert_last_transcript(use_clipboard=True, wait_for_keys=['ctrl', 'alt', 'd'])

    def on_stop_recording_send(self):
        """
        Callback for stop recording and send (insert + press Enter)

        Uses Ctrl+Alt+H (H for "Hit Enter" / "Hand off").
        Perfect for sending messages to chatbots/Claude Code.
        """
        hotkey_display = self._format_hotkey(HOTKEYS['stop_recording_send'])
        start_hotkey_display = self._format_hotkey(HOTKEYS['start_recording'])

        if self.audio_recorder.is_recording:
            # Stop recording
            logger.info(f"Recording stopped ({hotkey_display}) - will send after transcription")

            frames, duration = self.audio_recorder.stop_recording()
            sidecar = self.audio_recorder.take_finished_sidecar()
            self.just_finished_recording_d = True
            self.recording_finished_time_d = time.time()

            # Capture live transcriber reference before clearing
            recording_transcriber = self._active_live_transcriber or self.transcriber
            self._active_live_transcriber = None

            logger.info(f"Recording duration: {duration:.1f} seconds")

            # Start processing with clipboard AND send_after_insert, wait for key release
            if self.start_processing_thread(frames, duration, use_clipboard=True, send_after_insert=True,
                                            wait_for_keys=['ctrl', 'alt'],
                                            transcriber_override=recording_transcriber,
                                            sidecar=sidecar):
                logger.info("Processing in background (will send)...", extra=FILE_ONLY)
                logger.info(f"You can start a new recording with {start_hotkey_display}!", extra=FILE_ONLY)

        elif not self.just_finished_recording_d:
            # Insert last text and send
            logger.debug(f"{hotkey_display} pressed - inserting last text and sending")
            # Insert via clipboard and press Enter afterwards
            self.output_manager.insert_last_transcript(use_clipboard=True, wait_for_keys=['ctrl', 'alt'], send_after_insert=True)

    def on_stop_recording_no_insert(self):
        """
        Callback for stop recording without automatic insertion (process only)

        Note: Uses Y key on German QWERTZ keyboards.
        """
        hotkey_display = self._format_hotkey(HOTKEYS['stop_recording_no_insert'])
        start_hotkey_display = self._format_hotkey(HOTKEYS['start_recording'])

        if self.audio_recorder.is_recording:
            # Stop recording
            logger.info(f"Recording stopped ({hotkey_display}) - process only, no auto-insert")

            frames, duration = self.audio_recorder.stop_recording()
            sidecar = self.audio_recorder.take_finished_sidecar()

            # Capture live transcriber reference before clearing
            recording_transcriber = self._active_live_transcriber or self.transcriber
            self._active_live_transcriber = None

            logger.info(f"Recording duration: {duration:.1f} seconds")

            # Start processing WITHOUT auto-insert
            if self.start_processing_thread(frames, duration, use_clipboard=False, auto_insert=False,
                                            transcriber_override=recording_transcriber,
                                            sidecar=sidecar):
                logger.info("Processing in background (no auto-insert)...", extra=FILE_ONLY)
                logger.info(f"Press A or D to insert later, or {start_hotkey_display} for new recording",
                            extra=FILE_ONLY)

    def on_cancel_recording(self):
        """Callback for cancel recording"""
        if self.audio_recorder.is_recording:
            hotkey_display = self._format_hotkey(HOTKEYS['cancel_recording'][0])
            logger.info(f"Recording cancelled ({hotkey_display})", extra=FILE_ONLY)
            self._emit_block(
                'cancelled',
                lambda ansi, compact: console_ui.render_cancelled_strip(
                    ansi=ansi, compact=compact))

            # Cancel live session if active
            if self._active_live_transcriber is not None:
                self._active_live_transcriber.cancel_session()
                self._active_live_transcriber = None

            self.audio_recorder.cancel_recording()

    # ===== Push-to-talk (#66) =====
    # Opt-in double-tap-and-hold gesture, driven from the recording loop thread.
    # These methods only ever CALL the same public audio_recorder /
    # start_processing_thread entry points the W-flow callbacks use; they do not
    # touch any existing callback. All of this is dead code unless PTT is enabled
    # (self._ptt is None otherwise), so the default-off feature cannot affect the
    # existing flow. The polling design is also structurally immune to the
    # self-injection recursion that plagues low-level keyboard hooks: there are no
    # key events to recurse, only physical-state reads.

    def _ptt_tick(self):
        """One push-to-talk detector step, run on the recording loop thread.

        PTT stays inert while a recording is active that it does NOT own (a
        Ctrl+Alt+W session): the detector is reset and we bail, so PTT can never
        start a second session or steal an in-flight W recording. When PTT owns
        the recording, the detector drives the stop on trigger release.
        """
        if self._ptt is None:
            return

        if self.audio_recorder.is_recording and not self._ptt_owns_recording:
            # A foreign (W-owned) recording is in progress -> stay inert.
            self._ptt.reset()
            return

        trig = is_vk_pressed(self._ptt_trigger_vk)
        blocker = is_vk_pressed(VK_RMENU)
        # The foreign-key scan only matters while arming; skip it in the steady
        # states (IDLE / RECORDING) to keep the per-tick cost negligible.
        foreign = self._ptt_foreign_key_down() if self._ptt.needs_foreign_scan() else False

        action = self._ptt.update(
            KeyboardSnapshot(trig, blocker, foreign), time.monotonic())

        if action is PttAction.START:
            self._ptt_start_recording()
        elif action is PttAction.STOP:
            self._ptt_stop_and_insert()

    def _ptt_foreign_key_down(self) -> bool:
        """True if any curated key other than the trigger / Ctrl pair / Right-Alt
        is physically down (#66). Bounded poll via GetAsyncKeyState, called only
        during the arming window. This is what keeps Ctrl+C -> Ctrl+V and other
        chords from ever firing PTT: the content key disarms the gesture."""
        for vk in self._ptt_foreign_vks:
            if is_vk_pressed(vk):
                return True
        return False

    def _ptt_start_recording(self):
        """Start a recording owned by push-to-talk. Mirrors on_start_recording's
        sequence minus the loop-thread liveness check (we ARE on that thread, so
        it is alive by definition)."""
        if self.audio_recorder.is_recording:
            return  # belt-and-suspenders: never start a second session
        logger.info("Recording started (push-to-talk)")
        if not self.audio_recorder.start_recording():
            logger.error("Failed to start recording (push-to-talk) - audio stream could not be opened")
            self._ptt.reset()
            return
        self._ptt_owns_recording = True

        # Start live streaming session if the transcriber supports it (same as W).
        if self.transcriber.is_live:
            self._active_live_transcriber = self.transcriber
            if not self._active_live_transcriber.start_session():
                logger.error("Failed to start live streaming session (push-to-talk)")
                self._active_live_transcriber = None

    def _ptt_stop_and_insert(self):
        """Stop a PTT-owned recording and start processing, using the configured
        insert path. Mirrors the stop-hotkey callbacks. Does NOT set the
        just_finished_recording_a/d flags: those guard the A/D HOTKEYS from
        double-firing a stop-then-insert on the same chord; PTT's stop is a key
        RELEASE that cannot double-fire a stop hotkey, so the flags are
        irrelevant and left untouched to avoid perturbing the W-flow's state."""
        if not self.audio_recorder.is_recording:
            # A stop hotkey (or cancel) already ended this recording through its
            # own door -- just clear ownership and no-op.
            self._ptt_owns_recording = False
            return
        logger.info("Recording stopped (push-to-talk)")

        frames, duration = self.audio_recorder.stop_recording()
        sidecar = self.audio_recorder.take_finished_sidecar()

        recording_transcriber = self._active_live_transcriber or self.transcriber
        self._active_live_transcriber = None
        self._ptt_owns_recording = False

        logger.info(f"Recording duration: {duration:.1f} seconds")

        kwargs = self._ptt_insert_kwargs()
        if self.start_processing_thread(frames, duration,
                                        transcriber_override=recording_transcriber,
                                        sidecar=sidecar, **kwargs):
            logger.info("Processing in background (push-to-talk)...")

    def _ptt_insert_kwargs(self) -> dict:
        """Map the configured PTT insert path to start_processing_thread kwargs,
        mirroring the four stop callbacks. wait_for_keys=['ctrl'] only guards
        against a too-fast trigger re-press -- at PTT stop the trigger is already
        released, so the wait loop clears within a tick (no added latency)."""
        trig = ['ctrl']
        if self._ptt_insert == 'clipboard':
            return dict(use_clipboard=True, wait_for_keys=trig)
        if self._ptt_insert == 'send':
            return dict(use_clipboard=True, send_after_insert=True, wait_for_keys=trig)
        if self._ptt_insert == 'no_insert':
            return dict(use_clipboard=False, auto_insert=False)
        return dict(wait_for_keys=trig)  # 'type' (fallback), mirrors the A hotkey

    def on_retry_last_failed(self):
        """Callback for retry last failed transcription (Ctrl+Alt+R, Issue #24).

        Runs on the listener thread, so it must only read the slot and spawn a
        worker -- never transcribe inline (that would freeze Stop/Cancel/Exit).
        A user cancel never reaches a worker, so it can never become the retry
        target; no cancel-guard needed here."""
        hotkey_display = self._format_hotkey(HOTKEYS['retry_last_failed'])

        with self._last_failed_lock:
            rec = self._last_failed  # atomic local copy of the reference
        if rec is None:
            logger.info(f"{hotkey_display} pressed but no failed transcription to retry")
            return
        if not Path(rec.archived_mp3_path).exists():
            # Archive gone (manually cleared, never written). Leave the slot
            # as-is so a repeated R just no-ops rather than silently forgetting.
            logger.warning(f"Retry target missing on disk: {rec.archived_mp3_path}")
            return

        # Mirror start_processing_thread's bookkeeping on the same thread.
        self.active_threads = [t for t in self.active_threads if t.is_alive()]
        if len(self.active_threads) >= MAX_PARALLEL_TRANSCRIPTIONS:
            logger.warning(f"Maximum parallel transcriptions reached ({MAX_PARALLEL_TRANSCRIPTIONS}) "
                           f"-- retry deferred, press R again")
            return

        sequence_number = self.output_manager.get_next_sequence_number()
        # Snapshot the selected engine here on the listener thread -- where
        # switch_api also runs -- so the retry runs on one stable engine
        # reference and can't race a mid-flight Ctrl+Alt+L. Mirrors how
        # start_processing_thread snapshots self.transcriber at its call site (#107).
        transcriber = self.transcriber
        thread = threading.Thread(
            target=self.retry_recording_thread,
            args=(rec, sequence_number, transcriber),
            name=f"Retry-{sequence_number}-{datetime.datetime.now().strftime('%H%M%S%f')}"
        )
        thread.daemon = True
        thread.start()
        self.active_threads.append(thread)

        logger.info(f"Retry started ({hotkey_display}) for recording from "
                    f"{rec.origin_timestamp} (Seq: {sequence_number})", extra=FILE_ONLY)

    def retry_recording_thread(self, rec: '_FailedRecording', sequence_number: int, transcriber):
        """Worker: re-transcribe an archived MP3 and insert at the cursor like a
        fresh dictation (Issue #24).

        Honors the currently selected engine when it is file-capable -- the
        'soniox' upload slot or either Groq slot (#107): switching engine with
        Ctrl+Alt+L and retrying is the escape hatch when one API is broken. Only
        when the selection is Soniox Live does it fall back to the fixed
        duration-gated Soniox V2/V4 file chain (_run_empty_transcript_fallback),
        since a live session can't re-stream an archived file. A successful retry
        resolves the slot; a failed retry keeps it retryable, so a switch-and-retry
        routes around an outage."""
        thread_name = threading.current_thread().name
        timestamp = self.get_unique_timestamp()

        with self.processing_lock:
            self.processing_counter += 1
            current_count = self.processing_counter
        logger.info(f"[{thread_name}] Retrying recording from {rec.origin_timestamp} "
                    f"as sequence {sequence_number} (active: {current_count})", extra=FILE_ONLY)

        # Insert like Ctrl+Alt+D: clipboard is the project default, typing the
        # fallback for apps that block paste. The user is still holding
        # Ctrl+Alt+R when this fires, so wait_for_key_release below defers the
        # paste until those keys clear -- otherwise the Ctrl+V collides with the
        # still-held modifiers, the same guard the D path relies on.
        task = TranscriptionTask(
            sequence_number=sequence_number,
            timestamp=timestamp,
            use_clipboard=True,
            auto_insert=True,
            wait_for_key_release=True,
            trigger_keys=['ctrl', 'alt', 'r'],
        )

        try:
            self._ticker(f"[Seq: {sequence_number}] Retrying archived recording from {rec.origin_timestamp}...")
            retry_any_error = False  # only the live branch runs the error-aware chain
            if transcriber.is_live:
                # A live session can't re-stream a file, so re-transcribe through
                # the fixed duration-gated Soniox V2/V4 file chain, exactly as
                # before; its FALLBACK ACTIVE block names the engine.
                transcript, engine, retry_any_error = self._run_empty_transcript_fallback(
                    mp3_path=rec.archived_mp3_path,
                    duration=rec.duration,
                    sequence_number=sequence_number,
                    thread_name=thread_name,
                )
            else:
                # Honor the selected file-capable engine (#107). Mirror
                # process_recording_thread so the hybrid 'soniox' slot reports its
                # per-recording V2/V4 choice through the sink -- engine_code would
                # mistag a V4 recording as Son-v2 (its defensive default).
                self._ticker(f"[Seq: {sequence_number}] Retrying via {transcriber.get_name()}...")
                if isinstance(transcriber, SonioxTranscriber):
                    self._ticker(f"[Seq: {sequence_number}] {transcriber.engine_choice_line(rec.duration)}")
                    engine_tag = _EngineTag()
                    transcript = transcriber.transcribe(
                        rec.archived_mp3_path, rec.duration, engine_sink=engine_tag)
                    engine = engine_tag.code
                else:
                    transcript = transcriber.transcribe(rec.archived_mp3_path, rec.duration)
                    engine = engine_code(transcriber)
            transcript = transcript.rstrip('\n')

            # #133: only the Soniox Live file chain (transcriber.is_live) can verify
            # "no speech" -- it ran end to end with no transport/API error. A non-live
            # engine (the file-capable soniox/groq slot chosen via Ctrl+Alt+L) that
            # returns empty is ambiguous: it swallows transport/API errors internally,
            # so empty could be a broken API. Such a retry stays conservative and
            # retryable, so switch-and-retry remains the escape hatch out of an outage.
            no_speech_verdict = transcriber.is_live and not retry_any_error

            if transcript:
                # Mirror a normal successful transcription (#91): save under the
                # recording's ORIGIN timestamp so the audio<->transcript pair
                # re-forms by timestamp -- restoring the #62 invariant the #24
                # retry path used to break. save_transcript is base-class file
                # I/O, so self.transcriber (whatever instance is current) is
                # fine -- it's not API-specific.
                self.transcriber.save_transcript(transcript, rec.origin_timestamp, engine=engine)
                # Tag the archived audio with the producing engine. Two shapes
                # reach here: the bare voice_<ts>.mp3 (a normal failed slot, or a
                # #106 clean-exit salvage) is tagged by the timestamp-based
                # rename; a kill-recovered voice_<ts>_recovered.mp3
                # (audio_handler.recover_partial_files) is tagged path-based,
                # inserting the token before the _recovered marker (#98). Any
                # other shape is left untouched, as before. Both taggers are
                # best-effort/never-raise: a failed rename degrades only the
                # audio name, the pair still re-forms by the shared <ts> via the
                # transcript saved above.
                archived_name = Path(rec.archived_mp3_path).name
                if archived_name == f"voice_{rec.origin_timestamp}.mp3":
                    self.audio_recorder.tag_archive_with_engine(rec.origin_timestamp, engine)
                elif _RECOVERED_ARCHIVE_RE.match(archived_name):
                    self.audio_recorder.tag_recovered_archive_with_engine(rec.archived_mp3_path, engine)
                # else: an unrecognized archive name -- skip tagging, as before.
                self.output_manager.update_last_transcript(transcript)
                task.transcript = transcript
                task.is_complete = True
                self._resolve_failed_slot(rec)
                # Retire the persistent marker(s) (#114). Keys on the PRE-tag stem
                # (rec.archived_mp3_path) -- the marker was written under that name,
                # even though the engine tagging just above may have renamed the mp3.
                # delete_superseded_retry_markers additionally drops every marker
                # older than rec, so an already-superseded recording cannot resurrect
                # on a later start ("newest wins", #24). Both best-effort, run on the
                # worker thread and outside _last_failed_lock -- rec was transcribed,
                # so its marker must go regardless of the in-memory slot's identity.
                delete_retry_marker(rec.archived_mp3_path)
                delete_superseded_retry_markers(rec.origin_timestamp)
                logger.info(f"[{thread_name}] Retry for sequence {sequence_number} ready", extra=FILE_ONLY)
                self._ticker(f"[Seq: {sequence_number}] Retry completed, waiting for output...")
            elif no_speech_verdict:
                # #133: the live chain ran and every stage came back clean-but-empty --
                # no speech, a final verdict a retry cannot change. Drop the marker (a
                # second Ctrl+Alt+R then finds nothing to retry) and clear the in-memory
                # slot, keep the audio in history, and say so honestly (the calm 'no
                # speech' panel) instead of a misleading FAILED + retry hint.
                logger.info(f"[{thread_name}] Retry empty (no speech) for sequence {sequence_number} "
                            f"-- marker cleared, audio kept", extra=FILE_ONLY)
                delete_retry_marker(rec.archived_mp3_path)
                self._resolve_failed_slot(rec)
                task.no_speech = True
                task.is_complete = True
            else:
                # Empty without a clean live chain: the live chain hit a transport/API
                # error, OR a non-live engine came back empty (indistinguishable from a
                # swallowed outage). Keep it retryable so switch-and-retry stays the
                # escape hatch (#133 acceptance / the engine-switch rescue path), AND
                # reset the announcement to one more panel (#134 F-1): delete + fresh
                # write leaves a single, un-announced marker -- the delete also clears
                # any prior _seen flavor, which would otherwise keep the panel
                # suppressed. Net marker persistence is unchanged; only the _seen bit
                # is reset. The FAILED panel drives via the OutputManager (#109).
                logger.warning(f"[{thread_name}] Retry failed for sequence {sequence_number} "
                               f"-- recording from {rec.origin_timestamp} stays retryable",
                               extra=FILE_ONLY)
                delete_retry_marker(rec.archived_mp3_path)
                write_retry_marker(rec.archived_mp3_path, rec.duration)
                task.is_error = True
                task.is_complete = True
                # Failed retry: the in-memory slot already points at rec, leave it retryable.

            self.output_manager.add_task(task)

        except Exception as e:
            logger.error(f"[{thread_name}] Error retrying sequence {sequence_number}: {e}", exc_info=True)
            # console shows the dim red ERROR log line; the FAILED panel follows. A
            # raised transport error keeps the recording retryable and resets the
            # announcement to one more panel (#134 F-1), mirroring the empty-with-
            # error branch above -- net marker persistence unchanged, only the _seen
            # bit is reset (delete clears any prior flavor, then a fresh un-announced
            # write). Both best-effort/never-raise; rec.archived_mp3_path existence
            # was checked in on_retry_last_failed.
            delete_retry_marker(rec.archived_mp3_path)
            write_retry_marker(rec.archived_mp3_path, rec.duration)
            task.is_error = True
            task.is_complete = True
            self.output_manager.add_task(task)

        finally:
            with self.processing_lock:
                self.processing_counter -= 1
                current_count = self.processing_counter
            logger.info(f"[{thread_name}] Retry for sequence {sequence_number} finished (active: {current_count})", extra=FILE_ONLY)

    def on_test_transcription(self):
        """Callback for test transcription hotkey"""
        self.handle_test_transcription()

    def on_switch_api(self):
        """Callback for switch API hotkey"""
        self.switch_api()

    def on_open_history(self):
        """Open the unified history folder in Explorer (#50).

        Runs on the listener thread -> logger.* only (#11). os.startfile is
        non-blocking. The folder is (re)created first so the hotkey also works
        if the user deleted it mid-session.
        """
        try:
            HISTORY_FOLDER.mkdir(parents=True, exist_ok=True)
            os.startfile(str(HISTORY_FOLDER))
            logger.info(f"Opened history folder: {HISTORY_FOLDER}")
        except OSError as e:
            logger.error(f"Could not open the history folder {HISTORY_FOLDER}: {e}")

    def on_exit_program(self):
        """Callback for exit program"""
        self.stop_program()

    def _salvage_active_recording(self, reason: str) -> Optional[str]:
        """Stop an in-flight recording and persist it via the normal archive
        path, WITHOUT transcription (#49 layer 1). Returns the archive path,
        or None when there was nothing to salvage or the save failed.

        Thread-safe and idempotent: callable from the listener thread (exit
        hotkey) and the main thread (cleanup after Ctrl+C / Ctrl+Break); the
        flag keeps a second caller from double-saving. Runs on the listener
        thread in the hotkey case, so console output goes through logger.*
        only (#11). No call in here blocks unbounded: stop_recording() closes
        the stream on a bounded #128 budget (~4 s worst case on a driver-wedged
        device: 2 s lock-acquire + 2 s close-join), cancel_session() has a hard
        join budget (~6 s worst case), and save_recording() is local file I/O
        plus MP3 encode.
        """
        with self._salvage_lock:
            if self._salvage_done or not self.audio_recorder.is_recording:
                return None
            self._salvage_done = True

        # Frames first (stop capture, sidecar writer gets its stop signal),
        # then tear down the live session -- minimizes the time the audio
        # lives in RAM only.
        frames, duration = self.audio_recorder.stop_recording()
        sidecar = self.audio_recorder.take_finished_sidecar()
        live = self._active_live_transcriber
        self._active_live_transcriber = None
        if live is not None:
            live.cancel_session()

        if not frames:
            if sidecar is not None:
                sidecar.discard()
            return None

        timestamp = self.get_unique_timestamp()
        try:
            wav_path, mp3_path = self.audio_recorder.save_recording(frames, timestamp)
            self.audio_recorder.cleanup_temp_files(wav_path, mp3_path)
            archive = str(ARCHIVE_FOLDER / f"voice_{timestamp}.mp3")
            # File keeps the full salvage detail (file-only); the console gets the
            # visible SAVED strip below so the user leaving mid-recording learns
            # nothing was lost and how to continue (#106/#109).
            logger.warning(f"Recording was still running ({reason}) -- audio saved "
                           f"({duration:.1f}s, not transcribed): {archive}", extra=FILE_ONLY)
            retry_key = self._format_hotkey(HOTKEYS['retry_last_failed'])
            self._emit_block(
                'exit-saved',
                lambda ansi, compact: console_ui.render_saved_strip(
                    duration, retry_key, ansi=ansi, compact=compact))
            # Arm the next start's retry offer (#106). A clean exit leaves no
            # .partial (unlike a hard kill), so without this marker startup
            # recovery finds nothing and Ctrl+Alt+R reports "nothing to retry".
            # Written AFTER save_recording (the archive exists) but BEFORE the
            # sidecar is discarded: if the marker cannot be written, keep the
            # sidecar so recover_partial_files() still rescues the audio next
            # start -- the audio is never left un-recoverable.
            marker_ok = write_retry_marker(archive, duration)
            if sidecar is not None:
                if marker_ok:
                    sidecar.discard()  # only after the archive write AND marker
                else:
                    logger.warning(f"Retry marker not written -- keeping crash-safety "
                                   f"sidecar for next-start recovery: {sidecar.path}",
                                   extra=FILE_ONLY)
            return archive
        except Exception as e:
            kept = (f" Partial audio kept for next-start recovery: {sidecar.path}"
                    if sidecar is not None else "")
            logger.error(f"Could not save the running recording ({reason}): {e}.{kept}")
            return None

    def stop_program(self):
        """Stop the program"""
        logger.info("Program exit requested", extra=FILE_ONLY)

        # Salvage an in-flight recording before any teardown (#49): exiting
        # mid-recording used to discard the audio -- the original incident.
        self._salvage_active_recording("exit hotkey")

        # Set running flag to false
        self.running = False

        # Stop hotkey manager first (unregisters all hotkeys)
        if self.hotkey_manager:
            self.hotkey_manager.stop()

        # Stop output manager
        self.output_manager.stop()

        # Wait for active threads
        if self.active_threads:
            logger.info(f"Waiting for {len(self.active_threads)} active processing...", extra=FILE_ONLY)
            for thread in self.active_threads:
                if thread.is_alive():
                    thread.join(timeout=5)

    def status_display_thread(self):
        """Display status updates periodically"""
        while self.running:
            time.sleep(STATUS_UPDATE_INTERVAL)
            with self.processing_lock:
                if self.processing_counter > 0:
                    self._ticker(f"[STATUS] Active processing: {self.processing_counter}")

    def recording_loop_thread(self):
        """Separate thread for audio recording loop"""
        logger.info("Recording loop thread STARTED", extra=FILE_ONLY)
        # Boost THIS thread's scheduling priority so capture keeps draining the
        # mic under CPU load (#72). Best-effort: any failure degrades to default
        # priority and never touches capture -- same guard philosophy as the
        # PTT/device-loss paths below. Must run on this thread: MMCSS
        # characterises the caller.
        _priority_token = _elevate_capture_thread_priority()
        loop_counter = 0
        last_log_time = 0

        while self.running:
            loop_counter += 1

            # Wedge-guard liveness tick (#128): stamp every iteration so a loop
            # pinned in a wedged native audio call (record_chunk) stops updating
            # it, letting on_start_recording detect the freeze. monotonic() so a
            # wall-clock jump can neither fake a wedge nor mask one.
            self._recording_loop_last_tick = time.monotonic()

            # Push-to-talk gesture step (#66). Placed before the audio branch so a
            # START this tick begins capturing on this very iteration. No-op when
            # PTT is disabled. Guarded like the device-loss abort below: a buggy
            # PTT path must degrade to "PTT off", never kill the recording loop
            # (which would take the whole W-flow down -- VISION principle #1).
            try:
                self._ptt_tick()
            except Exception as ptt_err:
                logger.error(f"PTT tick failed, disabling push-to-talk: {ptt_err}",
                             exc_info=True)
                self._ptt = None

            # Log every 60 seconds for debugging (reduced from 5s to minimize log spam)
            current_time = time.time()
            if current_time - last_log_time > 60.0:
                logger.debug(f"Recording loop alive - Counter: {loop_counter}, is_recording: {self.audio_recorder.is_recording}")
                last_log_time = current_time

            # Process audio chunks while recording
            if self.audio_recorder.is_recording:
                self.audio_recorder.record_chunk()

                # Send last chunk to live transcriber if active
                if (self._active_live_transcriber is not None
                        and self.audio_recorder.frames):
                    self._active_live_transcriber.send_audio_chunk(
                        self.audio_recorder.frames[-1]
                    )

            elif self.audio_recorder.recording_aborted:
                # Device-loss endgame (#49 layer 4): record_chunk() gave up
                # (mic gone, reinit failed). Consume the flag once and turn
                # the former zombie state into a saved recording.
                self.audio_recorder.recording_aborted = False
                try:
                    self._handle_recording_abort()
                except Exception as abort_err:
                    # Abort handling must never break the recording loop --
                    # without it the app cannot record again until restart.
                    logger.error(f"Device-loss abort handling failed: {abort_err}",
                                 exc_info=True)

            # Reset just_finished flags after timeout
            if self.just_finished_recording_a and time.time() - self.recording_finished_time_a > 2:
                self.just_finished_recording_a = False

            if self.just_finished_recording_d and time.time() - self.recording_finished_time_d > 2:
                self.just_finished_recording_d = False

            time.sleep(0.01)  # Small delay to prevent high CPU usage

        _restore_capture_thread_priority(_priority_token)
        logger.info("Recording loop thread STOPPED", extra=FILE_ONLY)

    def _handle_recording_abort(self):
        """Handle the device-loss endgame on the recording-loop thread (#49).

        Before this layer, a recording whose microphone died for good left a
        zombie state: is_recording False with the frames still in RAM, the
        next stop hotkey typing the PREVIOUS transcript and the next start
        wiping the frames. Now: end the live session, persist the captured
        audio via a short-lived worker (so the loop is free again if the user
        immediately starts a new recording), and arm the retry slot.

        Console output via logger.* -- this thread's pace matters whenever a
        new recording is already running.
        """
        logger.error("Microphone connection lost and could not be recovered -- recording ended.")

        # Don't touch a NEW session's live state: if the user already started
        # the next recording, _active_live_transcriber belongs to it; the old
        # session dies via Soniox's idle timeout / the next start_session().
        if not self.audio_recorder.is_recording:
            live = self._active_live_transcriber
            self._active_live_transcriber = None
            if live is not None:
                live.cancel_session()

        frames = self.audio_recorder.take_aborted_frames()
        sidecar = self.audio_recorder.take_aborted_sidecar()
        duration = self.audio_recorder.get_audio_duration(frames)

        if not frames:
            if sidecar is not None:
                sidecar.discard()
            logger.error("No audio had been captured yet.")
            return

        thread = threading.Thread(
            target=self._salvage_aborted_recording_thread,
            args=(frames, duration, sidecar),
            name=f"Salvage-{datetime.datetime.now().strftime('%H%M%S%f')}"
        )
        thread.daemon = True
        thread.start()
        self.active_threads.append(thread)  # so stop_program joins it on exit

    def _salvage_aborted_recording_thread(self, frames: List[bytes], duration: float, sidecar):
        """Worker for the device-loss endgame (#49): persist the salvaged
        frames via the normal archive path, drop the sidecar once archived,
        arm the retry slot and say plainly how to continue."""
        timestamp = self.get_unique_timestamp()
        try:
            # trim_end=False: no stop hotkey ended this recording, so there is
            # no click to cut -- the tail is real dictation (the startup
            # recovery skips the trim for the same reason).
            wav_path, mp3_path = self.audio_recorder.save_recording(frames, timestamp,
                                                                    trim_end=False)
            self.audio_recorder.cleanup_temp_files(wav_path, mp3_path)
            if sidecar is not None:
                sidecar.discard()
            archive = ARCHIVE_FOLDER / f"voice_{timestamp}.mp3"
            self._record_failed_slot(timestamp, duration)
            retry_key = self._format_hotkey(HOTKEYS['retry_last_failed'])
            # ERROR level on purpose: this IS an error state (file log unchanged);
            # the FAILED panel below is the console surface (#109), so these two
            # detail lines are file-only.
            logger.error(f"Audio captured so far was saved ({duration:.1f}s): {archive}",
                         extra=FILE_ONLY)
            logger.error(f"Reconnect your microphone, then press {retry_key} to transcribe it.",
                         extra=FILE_ONLY)
            self._emit_block(
                'device-loss',
                lambda ansi, compact: console_ui.render_device_loss(
                    duration, retry_key, self.transcriber.get_name(),
                    self._footer_keys(retry=True), self._key_prefix(),
                    ansi=ansi, compact=compact))
        except Exception as e:
            kept = (f" Partial audio kept for next-start recovery: {sidecar.path}"
                    if sidecar is not None else "")
            logger.error(f"Could not save the aborted recording: {e}.{kept}")

    # ===== COCKPIT CONSOLE (#109) =====

    def _emit_block(self, event, builder, detail=""):
        """Render and emit one designed console block (#109). Same contract as
        the old show_status_block (#37/#11): one pre-composed string through the
        console-only logger (serialized by the single QueueListener, atomic
        write, never the file log), one DEBUG breadcrumb to the file, never
        raises. `builder(ansi, compact)` returns the ready-to-print lines.

        On a renderer fault a minimal one-line fallback still reaches the console
        -- in the Cockpit many former plain-text log lines are file-only, so a
        silent swallow could drop an entire FAILED panel (stability #1)."""
        try:
            compact = shutil.get_terminal_size((80, 25)).columns < console_ui.COMPACT_THRESHOLD
            lines = builder(ansi=_ANSI_ENABLED, compact=compact)
            console_logger.info("\n".join([""] + lines), extra={'raw_console': True})
            logger.debug(f"Status block: event={event} api={self.current_api}"
                         + (f" {detail}" if detail else ""))
        except Exception as e:
            logger.debug(f"Status block suppressed ({event}): {e}")
            try:
                console_logger.info(f"[{event}] -- display failed, details in {LOG_FILE.name}",
                                    extra={'raw_console': True})
            except Exception:
                pass

    def _ticker(self, msg, error=False):
        """One [Seq:]-style progress line: dim on the console (red for the
        exhausted error state), a DEBUG copy to the file log. Replaces the raw
        print()s so designed blocks can never be torn apart mid-string (#11)."""
        try:
            console_logger.info(_style(msg, _BOLD, _RED) if error else _style(msg, _DIM),
                                extra={'raw_console': True})
            logger.debug(msg)
        except Exception:
            pass

    def _key_letter(self, name):
        """The bare display key of a hotkey (e.g. 'ctrl+alt+w' -> 'W'), remap-safe."""
        combo = HOTKEYS[name]
        combo = combo[0] if isinstance(combo, list) else combo
        return combo.rpartition('+')[2].capitalize()

    def _lineup_data(self):
        """MODEL lineup rows for the renderer: (label, descriptor, is_current,
        is_default) in AVAILABLE_APIS order (labels/descriptors from #30)."""
        def entry(a):
            e = API_DISPLAY.get(a)
            return (e["label"], e["descriptor"]) if e else (a, "")
        return [(*entry(a), a == self.current_api, a == DEFAULT_API)
                for a in AVAILABLE_APIS]

    def _keys_grid_data(self):
        """The 11 KEYS-grid letters (console_ui.KEY_ACTIONS order) plus the
        shared modifier prefix, from config.HOTKEYS. Degrades to full combos
        with prefix=None if the config ever mixes prefixes (edge, documented)."""
        order = ['start_recording', 'stop_recording_keyboard', 'stop_recording_clipboard',
                 'stop_recording_send', 'stop_recording_no_insert', 'cancel_recording',
                 'retry_last_failed', 'switch_api', 'open_history', 'test_transcription',
                 'exit_program']
        combos = [HOTKEYS[n][0] if isinstance(HOTKEYS[n], list) else HOTKEYS[n]
                  for n in order]
        prefixes = {c.rpartition('+')[0] for c in combos}
        if len(prefixes) == 1 and '' not in prefixes:
            return ([c.rpartition('+')[2].capitalize() for c in combos],
                    self._format_hotkey(prefixes.pop()))
        return [self._format_hotkey(c) for c in combos], None

    def _key_prefix(self):
        """Shared modifier prefix of all hotkeys ('Ctrl+Alt'), or None if the
        config mixes prefixes (documented edge). Drives the once-per-box lead on
        the strip/panel key lines (#115)."""
        return self._keys_grid_data()[1]

    def _footer_keys(self, retry=False):
        """The bottom action-strip key hints (letter, word). The retry variant
        replaces '6 history' with 'R retry' (error panels)."""
        rec, mdl, quit_ = (self._key_letter('start_recording'),
                           self._key_letter('switch_api'),
                           self._key_letter('exit_program'))
        if retry:
            return [(rec, 'record'), (self._key_letter('retry_last_failed'), 'retry'),
                    (mdl, 'model'), (quit_, 'quit')]
        return [(rec, 'record'), (self._key_letter('open_history'), 'history'),
                (mdl, 'model'), (quit_, 'quit')]

    def _on_output_event(self, event, kind=None, seq=None, chars=None, sent=False):
        """Map OutputManager completion events onto Cockpit strips/panels (#109).

        This is the on_task_complete callback (see OutputManager.__init__ for
        the contract). Runs on the OutputManager thread; the block emission
        only enqueues (#11), never raises into the output loop.

        Events:
            'inserted'  -- transcript inserted (OK strip); sent=True for the H flow.
            'ready'     -- Y flow: processed, waiting for a manual insert (WAITING).
            'failed'    -- kind='transcription' (FAILED panel) or 'insertion'.
            'no_speech' -- clean-but-empty on every engine (NO SPEECH panel, #133).
        """
        try:
            model = self.transcriber.get_name()
            type_key = self._key_letter('stop_recording_keyboard')
            paste_key = self._key_letter('stop_recording_clipboard')
            retry_key = self._format_hotkey(HOTKEYS['retry_last_failed'])
            # Negative sequence numbers are internal (immediate tasks: self-test,
            # insert-last) -- omit them from the user-facing strip.
            seq_shown = seq if (seq is not None and seq >= 0) else None

            if event == 'inserted':
                self._emit_block(
                    'inserted',
                    lambda ansi, compact: console_ui.render_ok_strip(
                        seq_shown, chars, sent, model, self._footer_keys(),
                        self._key_prefix(), ansi=ansi, compact=compact),
                    detail=f"seq={seq} chars={chars} sent={sent}")
            elif event == 'ready':
                self._emit_block(
                    'ready',
                    lambda ansi, compact: console_ui.render_waiting_strip(
                        seq_shown, chars, type_key, paste_key, self._key_prefix(),
                        ansi=ansi, compact=compact),
                    detail=f"seq={seq} chars={chars}")
            elif event == 'failed' and kind == 'insertion':
                self._emit_block(
                    'insert-failed',
                    lambda ansi, compact: console_ui.render_insert_failed(
                        seq_shown, type_key, paste_key, model, self._footer_keys(),
                        self._key_prefix(), ansi=ansi, compact=compact),
                    detail=f"seq={seq}")
            elif event == 'failed':
                self._emit_block(
                    'transcription-failed',
                    lambda ansi, compact: console_ui.render_transcription_failed(
                        seq_shown, retry_key, str(SCRIPT_DIR), model,
                        self._footer_keys(retry=True), self._key_prefix(),
                        ansi=ansi, compact=compact),
                    detail=f"seq={seq}")
            elif event == 'no_speech':
                # #133: a benign 'no speech found' verdict, not a failure -- no retry
                # hint, no hotkey. The recording is kept in history; a retry cannot help.
                self._emit_block(
                    'no-speech',
                    lambda ansi, compact: console_ui.render_no_speech(
                        ansi=ansi, compact=compact),
                    detail=f"seq={seq}")
        except Exception as e:
            logger.debug(f"Status block dispatch failed ({event}): {e}")

    def _show_recovery_block(self, pending, newest_clean_exit, hotkeys_ok):
        """Prominent startup notice for salvaged recordings (#78, #106, #109, #114).

        Several origins feed this block through the merged retry pipeline: a hard
        kill mid-recording leaves a .partial sidecar that startup recovery (#49)
        converts to an archived MP3, and the persistent .needsretry marker (#114)
        re-arms anything else saved but never transcribed -- a clean-exit salvage
        (#106), an in-session transcription failure (#24), or a device-loss
        salvage. All arm the retry hotkey; newest_clean_exit is True when the
        newest (R-targeted) entry is NOT a kill-recovered file (derived from its
        mp3 stem at the merge site, not list identity), so the panel tells a
        hard-kill rescue apart from a plain "saved but not transcribed". Rendered
        as the yellow-lamp RECOVERED panel, emitted last so it sits at the bottom
        of the scrollback. Never raises."""
        try:
            retry_key = self._format_hotkey(HOTKEYS['retry_last_failed'])
            _, newest_dur, newest_ts = pending[-1]
            when = (f"{newest_ts[:4]}-{newest_ts[4:6]}-{newest_ts[6:8]} "
                    f"{newest_ts[9:11]}:{newest_ts[11:13]}")
            n = len(pending)
            self._emit_block(
                'recovered',
                lambda ansi, compact: console_ui.render_recovered_panel(
                    when, newest_dur, newest_clean_exit, hotkeys_ok,
                    str(ARCHIVE_FOLDER), retry_key, ansi=ansi, compact=compact),
                detail=f"count={n} newest={newest_ts} clean_exit={newest_clean_exit}")
        except Exception as e:
            logger.debug(f"Recovery status block suppressed: {e}")

    def _register_hotkeys(self) -> bool:
        """Register all hotkeys using Win32 RegisterHotKey API.

        Returns True when the hotkey listener is up; run() gates its
        success messaging on this."""
        logger.info("Registering hotkeys via RegisterHotKey...", extra=FILE_ONLY)

        self.hotkey_manager = HotkeyManager()

        # Single-value hotkeys
        single_hotkeys = {
            'start_recording': self.on_start_recording,
            'stop_recording_keyboard': self.on_stop_recording_keyboard,
            'stop_recording_clipboard': self.on_stop_recording_clipboard,
            'stop_recording_send': self.on_stop_recording_send,
            'stop_recording_no_insert': self.on_stop_recording_no_insert,
            'retry_last_failed': self.on_retry_last_failed,
            'test_transcription': self.on_test_transcription,
            'switch_api': self.on_switch_api,
            'open_history': self.on_open_history,
        }

        for hotkey_name, callback in single_hotkeys.items():
            hotkey_str = HOTKEYS[hotkey_name]
            self.hotkey_manager.register(hotkey_str, callback, name=hotkey_name)

        # List-value hotkeys (cancel_recording, exit_program)
        for cancel_hotkey in HOTKEYS['cancel_recording']:
            self.hotkey_manager.register(cancel_hotkey, self.on_cancel_recording, name='cancel_recording')

        for exit_hotkey in HOTKEYS['exit_program']:
            self.hotkey_manager.register(exit_hotkey, self.on_exit_program, name='exit_program')

        # Start the listener thread (blocks until registration is done)
        if not self.hotkey_manager.start():
            logger.error("Failed to start HotkeyManager", extra=FILE_ONLY)  # FAILED panel is the surface
            return False

        # File log keeps the full per-key wall (file-only) and the greppable
        # success line (AGENTS heartbeat); the console gets one dim summary, or a
        # visible WARNING when some keys were lost to another app (#61/#109).
        logger.info("All hotkeys registered successfully via RegisterHotKey", extra=FILE_ONLY)
        registered = self.hotkey_manager.registered_count
        expected = self.hotkey_manager.expected_count
        summary = f"hotkeys: {registered}/{expected} registered -- full log: {LOG_FILE.name}"
        if registered < expected:
            logger.warning(summary)
        else:
            logger.info(summary)
        return True

    def run(self):
        """Main application loop"""
        # Start status thread
        status_thread = threading.Thread(
            target=self.status_display_thread,
            daemon=True,
            name="StatusDisplay"
        )
        status_thread.start()

        # Start recording loop thread
        self.recording_thread = threading.Thread(
            target=self.recording_loop_thread,
            daemon=True,
            name="RecordingLoop"
        )
        self.recording_thread.start()

        # Convert sidecars left over from a crash (#49 layer 3). Placed
        # before hotkey registration on purpose: no recording can start while
        # this converts, and the retry slot is armed below before the first
        # keypress is possible. The user-facing announcement is deferred to a
        # prominent block after READY (#78) so it can't be scrolled off; only
        # the progress print below stays here (main thread, no hotkeys yet).
        # A recovery failure must never block the start.
        try:
            try:
                # Best-effort probe: a file vanishing between glob and stat
                # (e.g. a second instance recovering it) must only cost this
                # hint line, never the recovery below.
                if any(p.stat().st_size > 10 * 1024 * 1024
                       for p in ARCHIVE_FOLDER.glob("voice_*.partial")):
                    self._ticker("Recovering unsaved recording, this may take a moment...")
            except OSError:
                pass
            recovered = recover_partial_files()
        except Exception as e:
            logger.error(f"Startup recovery failed: {e}", exc_info=True)
            recovered = []
        # Clean-exit salvages (#106) join the same retry pipeline as hard-kill
        # recoveries -- both are audio saved but not transcribed. Own try/except
        # so a fault in one source can never suppress the other.
        try:
            salvaged = recover_salvaged_recordings()
        except Exception as e:
            logger.error(f"Clean-exit salvage recovery failed: {e}", exc_info=True)
            salvaged = []
        # Merge both untranscribed-audio sources into one retry pipeline, sorted
        # by timestamp (fixed-width, so lexicographic == chronological). De-dup by
        # archive path (#114): a freshly kill-recovered file appears BOTH in the
        # recover_partial_files() return AND -- via the marker that recovery just
        # wrote -- in the recover_salvaged_recordings() read of this same start.
        # The recovered entry precedes its marker echo (stable sort), so the exact
        # duration is the one kept.
        merged = sorted(recovered + salvaged, key=lambda t: t[2])
        pending, seen = [], set()
        for entry in merged:
            if entry[0] in seen:
                continue
            seen.add(entry[0])
            pending.append(entry)
        # Wording (#106): a kill-recovered _recovered stem reads "rescued after a
        # hard kill", everything else "saved but not transcribed". Derived from the
        # stem (not list identity) so it stays correct on the 2nd+ restart, when a
        # kill file is re-armed from its persistent marker rather than a .partial.
        # "Everything else" now spans clean-exit, in-session transcription failure
        # and device-loss salvage (#114); the marker carries no origin, so the
        # panel's detail line is deliberately origin-neutral (just when/duration).
        newest_clean_exit = bool(pending) and not _RECOVERED_ARCHIVE_RE.match(
            Path(pending[-1][0]).name)
        newest_announced = False   # #134: hoisted into scope for the panel guard below
        if pending:
            # Arm the retry slot with the newest pending file (single-slot
            # semantics of #24: any later real failure simply overwrites it).
            # Built directly instead of via _record_failed_slot, whose path
            # reconstruction doesn't know the _recovered naming. Display is
            # deferred to _show_recovery_block after hotkey registration (#78);
            # arming stays here, before the first keypress is possible.
            newest = pending[-1]
            # #134: has this newest recording's RECOVERED panel already been shown on
            # an earlier start? An announced (_seen) marker re-arms Ctrl+Alt+R silently
            # instead of drawing the panel again. Probed before the supersede prune --
            # which never touches the newest marker anyway (strict '<'), so before or
            # after is equivalent; before is clearer.
            newest_announced = is_retry_marker_announced(newest[0])
            with self._last_failed_lock:
                self._last_failed = _FailedRecording(
                    archived_mp3_path=newest[0],
                    duration=newest[1],
                    origin_timestamp=newest[2],
                )
            # Single-slot supersede (#24/#114): keep only the newest marker so an
            # older recording can't resurrect on a later start. Audio is never
            # deleted -- only the older markers.
            delete_superseded_retry_markers(newest[2])

        # Register hotkeys
        hotkeys_ok = self._register_hotkeys()

        if hotkeys_ok:
            # First Cockpit block (#109): the masthead -- wordmark, READY, the
            # optional #40 fallback note, the MODEL lineup, the KEYS grid, the
            # history edge. Recovery gets its own prominent panel below (#78).
            keys, key_prefix = self._keys_grid_data()
            lineup = self._lineup_data()
            note = self._startup_fallback_note
            self._emit_block(
                'startup',
                lambda ansi, compact: console_ui.render_masthead(
                    lineup, keys, key_prefix, str(HISTORY_FOLDER),
                    # bare letters: Ctrl+Alt is established once on the READY line
                    # (#115). open_key survives only in the compact history line.
                    self._key_letter('open_history'),
                    self._key_letter('switch_api'),
                    self._format_hotkey(HOTKEYS['start_recording']),
                    note=note, with_wordmark=True,
                    logo_lines=console_ui.ACTIVE_LOGO_MARK,
                    ansi=ansi, compact=compact))
        else:
            # No READY invitation after a failed registration -- the tool keeps
            # running without hotkeys (status quo), but the panel must say so.
            self._emit_block(
                'hotkeys-failed',
                lambda ansi, compact: console_ui.render_hotkeys_failed(
                    ansi=ansi, compact=compact))

        # Recovery notice as its own prominent block, emitted last so it sits at
        # the bottom of the scrollback below READY and can't be scrolled off
        # (#78 -- the plain line above it was overlooked in the #59 test). The
        # retry slot was already armed above; this is display only. Reached from
        # both hotkey paths, so a failed registration still announces the rescue.
        # Shown only the first start after a failure (#134): declining to recover
        # is valid, so a still-present marker must not nag on every later start.
        if pending and not newest_announced:
            self._show_recovery_block(pending, newest_clean_exit, hotkeys_ok)
            # Mark this recording announced so the panel appears exactly once; every
            # later start arms Ctrl+Alt+R silently from the still-present marker.
            # Marking AFTER the display means even a swallowed renderer fault counts
            # as announced -- better than re-nagging on a persistent render fault
            # (the panel is already best-effort; _show_recovery_block never raises).
            mark_retry_marker_announced(pending[-1][0])

        try:
            # Main loop - wait until running flag is set to False
            while self.running:
                time.sleep(0.1)

        except Exception as e:
            logger.critical(f"Unexpected error in main loop: {e}", exc_info=True)
            print(f"\nCRITICAL ERROR: {e}")
            print(f"Details in log file: {LOG_FILE}")

        finally:
            # Stop hotkey manager
            if hasattr(self, 'hotkey_manager') and self.hotkey_manager:
                try:
                    self.hotkey_manager.stop()
                    logger.info("HotkeyManager stopped", extra=FILE_ONLY)
                except Exception as e:
                    logger.warning(f"Error stopping HotkeyManager: {e}")

            self.cleanup()

    def cleanup(self):
        """Clean up resources"""
        logger.info("Cleaning up...")   # one dim console line (#109)

        # Salvage an in-flight recording (#49): covers Ctrl+C / Ctrl+Break,
        # where this is the first shutdown code that runs. After an
        # exit-hotkey shutdown the idempotency flag makes it a no-op.
        self._salvage_active_recording("shutdown")

        # Stop output manager
        self.output_manager.stop()

        # Clean up audio resources
        self.audio_recorder.cleanup()

        logger.info("Program ended")   # dim console + last real file line (AGENTS heartbeat)
        logger.info("=" * 60, extra=FILE_ONLY)

        # Drain and stop the console QueueListener last, so the two lines above
        # still reach the terminal. stop() enqueues a sentinel via put_nowait; if
        # cmd Mark-Mode has filled the queue at exit this raises queue.Full -- swallow
        # it, the daemon listener dies with the process and the file log is already
        # complete.
        try:
            _console_queue_listener.stop()
        except Exception:
            pass


def main():
    """Main entry point"""
    # Map Ctrl+Break to KeyboardInterrupt instead of instant process death so
    # the run()-finally -> cleanup() path can salvage an active recording
    # (#49). SIGBREAK exists on Windows only, hence the AttributeError guard.
    try:
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    except AttributeError:
        pass

    # "Program ended" is emitted once by cleanup() (dim console + file, the
    # AGENTS heartbeat), which run()'s finally reaches on every normal and
    # Ctrl+C/Ctrl+Break exit -- so no print here (that was the #10 doubling).
    # The except branches only run after cleanup() has stopped the listener, so
    # their remaining feedback is print() (the only working console channel).
    try:
        migrate_legacy_archives()  # legacy voice_archive/ + text_archive/ -> history/ (#50)
        app = ThoughtborneApp()
        app.run()
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
        print("\nProgram interrupted")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        print(f"\nFATAL ERROR: {e}")
        print(f"Details in log file: {LOG_FILE}")


if __name__ == "__main__":
    main()
