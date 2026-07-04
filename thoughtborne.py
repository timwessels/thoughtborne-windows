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
import time
import queue
import signal
import logging
import threading
import datetime
from pathlib import Path
from typing import List, Optional, NamedTuple
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener

# Import our modules
from config import (
    LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    LOG_CONSOLE_QUEUE_MAX,
    HOTKEYS, STATUS_UPDATE_INTERVAL, MAX_PARALLEL_TRANSCRIPTIONS,
    SCRIPT_DIR, DEFAULT_API, AVAILABLE_APIS, API_DISPLAY,
    SHORT_AUDIO_THRESHOLD, ARCHIVE_FOLDER, HISTORY_FOLDER,
    migrate_legacy_archives,
    PTT_ENABLED, PTT_TRIGGER_VK, PTT_INSERT,
    PTT_TAP_WINDOW_S, PTT_MIN_HOLD_S, PTT_RELEASE_TAIL_S,
)
from hotkey_manager import HotkeyManager, is_key_pressed, is_vk_pressed, VK_RMENU
from ptt_detector import PttDetector, KeyboardSnapshot, PttAction
from audio_handler import AudioRecorder, recover_partial_files
from transcriber import (
    create_transcriber,
    MissingAPIKeyError,
    SonioxLiveTranscriber,
    SonioxTranscriber,
    SonioxV4Transcriber,
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
    handler keeps every record regardless."""
    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            pass


class ConsoleFormatter(logging.Formatter):
    """Standard log format for normal records; verbatim passthrough for records
    flagged raw_console (the pre-composed status blocks, #37). Normal log lines
    are formatted exactly as before -- only flagged records skip the prefix."""
    def format(self, record):
        if getattr(record, 'raw_console', False):
            return record.getMessage()
        return super().format(record)


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
console_handler.setFormatter(ConsoleFormatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))

# Bounded queue: a full queue drops the newest console records (DroppingQueueHandler
# swallows queue.Full instead of routing to handleError) so the listener thread never
# blocks even when the drain stalls. The file handler keeps the complete record either
# way.
_console_log_queue: queue.Queue = queue.Queue(maxsize=LOG_CONSOLE_QUEUE_MAX)
_console_queue_handler = DroppingQueueHandler(_console_log_queue)
_console_queue_handler.setLevel(logging.INFO)  # Filter DEBUG before the queue
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

# Clean up old log backups on startup
cleanup_old_logs(LOG_FILE, max_age_days=30)


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

# SGR codes for _style -- palette kept deliberately minimal (#37)
_BOLD = "1"
_RED = "31"
_GREEN = "32"
_CYAN = "36"

# Status-block separator: '-' on purpose, distinct from the '=' framing of
# event blocks (FALLBACK ACTIVE, no-API error) so the two stay tellable apart.
_SEP = "-" * 60


def _style(text: str, *codes: str) -> str:
    """Wrap text in ANSI SGR codes; plain passthrough when styling is disabled."""
    if not _ANSI_ENABLED or not codes:
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


class ThoughtborneApp:
    """Main application class for Thoughtborne (Windows version)"""

    def __init__(self):
        """Initialize the application"""
        logger.info("=" * 60)
        logger.info("Thoughtborne application starting (Windows version)...")
        logger.info(f"Python Version: {sys.version}")
        logger.info(f"Working directory: {os.getcwd()}")
        logger.info(f"Script directory: {SCRIPT_DIR}")

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

        logger.info(f"Configuration: Default API={DEFAULT_API}, Max parallel={MAX_PARALLEL_TRANSCRIPTIONS}")
        logger.info(f"Current transcriber: {self.transcriber.get_name()}")
        logger.info("Application initialized successfully")

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
        unretryable pointer."""
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
        logger.info(f"Retry slot armed: failed recording from {timestamp} (Ctrl+Alt+R to retry)")

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

        logger.info(f"[{thread_name}] Starting processing for sequence {sequence_number} with {transcriber.get_name()} (active: {current_count})")

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
            print(f"[Seq: {sequence_number}] Audio saved and archived")

            # Transcribe with the fixed transcriber
            print(f"[Seq: {sequence_number}] Transcribing with {transcriber.get_name()}...")
            transcript = transcriber.transcribe(mp3_path, duration)
            transcript = transcript.rstrip('\n')

            # Issue #1: empty live transcript -> file-based fallback on the
            # already-archived MP3. Restricted to SonioxLiveTranscriber: empty
            # results from V2/V4-async/Groq already mean the
            # file-based path was tried and failed, so a second pass would not
            # help. Runs before cleanup_temp_files so mp3_path still exists.
            if not transcript and isinstance(transcriber, SonioxLiveTranscriber):
                transcript = self._run_empty_transcript_fallback(
                    mp3_path=mp3_path,
                    duration=duration,
                    sequence_number=sequence_number,
                    thread_name=thread_name,
                )

            # Save transcript
            if transcript:
                transcriber.save_transcript(transcript, timestamp)
                self.output_manager.update_last_transcript(transcript)

                # Update task
                task.transcript = transcript
                task.is_complete = True
                task.use_clipboard = use_clipboard

                logger.info(f"[{thread_name}] Transcription for sequence {sequence_number} ready")
                print(f"[Seq: {sequence_number}] Transcription completed, waiting for output...")
            else:
                logger.warning(f"[{thread_name}] Empty transcription for sequence {sequence_number}")
                task.is_error = True
                task.is_complete = True
                self._record_failed_slot(timestamp, duration)

            # Add to output queue
            self.output_manager.add_task(task)

            # Cleanup temp files
            self.audio_recorder.cleanup_temp_files(wav_path, mp3_path)

        except Exception as e:
            logger.error(f"[{thread_name}] Error processing sequence {sequence_number}: {e}", exc_info=True)
            print(f"ERROR: [Seq: {sequence_number}] Processing error: {e}")

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
            logger.info(f"[{thread_name}] Processing for sequence {sequence_number} finished (active: {current_count})")

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

        logger.info(f"New processing thread started: {thread.name} (Seq: {sequence_number}) using {transcriber.get_name()}")
        return True

    def _run_empty_transcript_fallback(self, mp3_path: str, duration: float,
                                       sequence_number: int, thread_name: str) -> str:
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
            Transcript string (empty if every available fallback failed -- the
            caller then routes through the existing is_error path).
        """
        try_v2_first = duration < SHORT_AUDIO_THRESHOLD
        primary_label = "Soniox V2 (sync)" if try_v2_first else "Soniox V4 (async)"

        logger.info(
            f"[{thread_name}] Empty live transcript for sequence {sequence_number} "
            f"(duration: {duration:.1f}s) -- falling back to {primary_label}"
        )

        # Clearly framed console block so Tim sees the fallback kick in without
        # having to scan the log. Printed before the attempt so it is visible
        # even if the lazy init below raises.
        print("")
        print("=" * 60)
        print(f"[Seq: {sequence_number}] FALLBACK ACTIVE -- live transcript empty")
        print(f"  Duration: {duration:.1f}s -> {primary_label}")
        print(f"  (Class-B: live WebSocket likely disconnected mid-recording)")
        print("=" * 60)

        # Short recordings: try V2, fall through to V4 on failure / empty.
        # Long recordings: V4 is the only option (V2 has a 60 s hard limit).
        if try_v2_first:
            transcript = self._try_fallback(
                kind="v2",
                mp3_path=mp3_path,
                duration=duration,
                sequence_number=sequence_number,
                thread_name=thread_name,
            )
            if transcript:
                return transcript

            # V2 failed or returned empty. Spec #1 requires we still try V4
            # so that "Empty transcription" only surfaces when both fail (and
            # so the tool keeps working if V2 is ever shut down by Soniox).
            logger.info(
                f"[{thread_name}] V2 fallback unproductive for sequence "
                f"{sequence_number} -- falling through to Soniox V4 (async)"
            )
            print(f"[Seq: {sequence_number}] V2 unproductive -- falling through to Soniox V4 (async)")

        transcript = self._try_fallback(
            kind="v4",
            mp3_path=mp3_path,
            duration=duration,
            sequence_number=sequence_number,
            thread_name=thread_name,
        )

        if not transcript:
            # Every file-based API we tried also returned nothing on top of
            # the empty Live result. For short recordings that means Live +
            # V2 + V4 all failed (triple failure); for long recordings V2 is
            # skipped because of its 60 s hard limit, so it's Live + V4 only.
            stages = "Live + V2 + V4" if try_v2_first else "Live + V4"
            logger.error(
                f"[{thread_name}] All fallbacks exhausted for sequence "
                f"{sequence_number} ({stages} all empty / failed)"
            )
            print(f"[Seq: {sequence_number}] All fallbacks exhausted ({stages} all failed)")

        return transcript

    def _try_fallback(self, kind: str, mp3_path: str, duration: float,
                      sequence_number: int, thread_name: str) -> str:
        """Run a single fallback transcriber attempt. Helper for _run_empty_transcript_fallback.

        Lazily instantiates the requested transcriber (V2 or V4) under the init
        lock, then runs the transcription call outside the lock so parallel
        fallbacks don't serialize on the network call. Any exception is caught
        and logged -- this method always returns a string, never raises.

        Args:
            kind: "v2" or "v4".
            mp3_path: Path to the temp MP3.
            duration: Recording duration in seconds.
            sequence_number: For console / log correlation.
            thread_name: For log correlation.

        Returns:
            Transcript string, or "" if this attempt failed (exception) or
            returned empty.
        """
        label = "Soniox V2 (sync)" if kind == "v2" else "Soniox V4 (async)"

        try:
            with self._fallback_init_lock:
                if kind == "v2" and self._fallback_v2 is None:
                    self._fallback_v2 = SonioxTranscriber()
                    logger.info("Soniox V2 fallback transcriber initialized")
                elif kind == "v4" and self._fallback_v4 is None:
                    self._fallback_v4 = SonioxV4Transcriber()
                    logger.info("Soniox V4 fallback transcriber initialized")

            fallback = self._fallback_v2 if kind == "v2" else self._fallback_v4

            # SDK-less is an expected, configuration-side state: skip the V2
            # stage with one INFO line instead of letting _transcribe_v2_sync
            # raise into the ERROR-plus-traceback path below.
            if kind == "v2" and not fallback._v2_available:
                logger.info(
                    f"[{thread_name}] V2 fallback stage skipped for sequence "
                    f"{sequence_number} (Soniox SDK not installed)"
                )
                return ""

            start = time.time()
            if kind == "v2":
                # Raw V2 sync, without the slot's internal V4 fallback -- the
                # cascade does its own V2 -> V4 hop (incl. on empty results)
                # one level up (#31).
                transcript = fallback._transcribe_v2_sync(mp3_path, duration).rstrip('\n')
            else:
                transcript = fallback.transcribe(mp3_path, duration).rstrip('\n')
            elapsed = time.time() - start

            if transcript:
                logger.info(
                    f"[{thread_name}] Fallback ({label}) succeeded for "
                    f"sequence {sequence_number} in {elapsed:.2f}s "
                    f"({len(transcript)} chars)"
                )
                print(f"[Seq: {sequence_number}] Fallback succeeded "
                      f"({elapsed:.1f}s, {len(transcript)} chars)")
            else:
                logger.warning(
                    f"[{thread_name}] Fallback ({label}) returned empty for "
                    f"sequence {sequence_number} after {elapsed:.2f}s"
                )
                print(f"[Seq: {sequence_number}] Fallback ({label}) returned empty")

            return transcript

        except Exception as e:
            if kind == "v2" and SonioxTranscriber._is_auth_error(e):
                # The V4 stage that follows surfaces the single [AUTH] line
                # (#32); an ERROR here would just duplicate it on the console.
                logger.debug(
                    f"[{thread_name}] Fallback ({label}) auth failure for "
                    f"sequence {sequence_number}: {e}",
                    exc_info=True
                )
                return ""
            logger.error(
                f"[{thread_name}] Fallback ({label}) raised for "
                f"sequence {sequence_number}: {e}",
                exc_info=True
            )
            print(f"ERROR: [Seq: {sequence_number}] Fallback ({label}) failed: {e}")
            return ""

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
                # sees it -- emit the failure block directly (#37). Runs on the
                # listener thread: show_status_block only enqueues (#11).
                self.show_status_block(
                    'self-test-failed',
                    _style("FAILED Self-test failed -- no transcription received",
                           _BOLD, _RED),
                    action=(f"check your API key in .env and the log: {LOG_FILE.name}",
                            f"(.env is in {SCRIPT_DIR})"),
                )

            logger.info("Test completed")
        else:
            logger.error(f"Test file not found: {test_file}")
            logger.error("Place a file named 'test_audio.wav' or 'test_audio.mp3' in the script directory.")
            self.show_status_block(
                'self-test-failed',
                _style("FAILED Self-test failed -- no test audio file found",
                       _BOLD, _RED),
                action="place 'test_audio.wav' or 'test_audio.mp3' in the project folder, then retry",
            )

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
                logger.warning(f"Skipped {api_name} ({_describe_construction_failure(e)})")
                logger.debug(f"Construction failed for {api_name}: {e}", exc_info=True)
                continue

            if api_name != DEFAULT_API:
                missing = sorted({err.env_var for _, err in failures
                                  if isinstance(err, MissingAPIKeyError)})
                reason = (f"{' / '.join(missing)} missing" if missing
                          else f"default API '{DEFAULT_API}' unavailable")
                self._startup_fallback_note = (
                    f"{reason} -> started on {api_name} (default: {DEFAULT_API})")
                logger.warning(self._startup_fallback_note)
            return api_name, transcriber

        self._print_no_api_error_block(failures)
        print("Press Enter to exit...")
        input()
        sys.exit(1)

    @staticmethod
    def _print_no_api_error_block(failures):
        """Actionable console block when no transcription API is constructible (#40)."""
        by_key = {}   # env var -> [api names]
        other = []    # (api name, reason) for non-key construction failures
        for api_name, error in failures:
            if isinstance(error, MissingAPIKeyError):
                by_key.setdefault(error.env_var, []).append(api_name)
            else:
                other.append((api_name, f"{type(error).__name__}: {error}"))

        logger.error("No transcription API could be constructed -- tried: "
                     + ", ".join(api for api, _ in failures))
        print("")
        print("=" * 60)
        print("ERROR: No transcription API available")
        print("=" * 60)
        for env_var, apis in by_key.items():
            print(f"  {env_var} is missing  (needed for: {', '.join(apis)})")
        for api_name, reason in other:
            print(f"  {api_name} failed: {reason}")
        print("")
        print("  Thoughtborne needs at least one API key in the .env file")
        print("  in the project folder (copy .env.example to .env first):")
        print("      GROQ_API_KEY    - free tier, no payment needed")
        print("      SONIOX_API_KEY  - prepaid, best German accuracy")
        print("  Where to get the keys: see .env.example or the README.")
        print("  Then start Thoughtborne again.")
        print("=" * 60)

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
                    logger.warning(f"Skipped {next_api} ({_describe_construction_failure(e)})")
                    logger.debug(f"Construction failed for {next_api}: {e}", exc_info=True)
                    continue

                logger.info(f"Switching API from {self.current_api} to {next_api}")
                self.transcriber = new_transcriber
                self.current_api = next_api
                logger.info(f"Successfully switched to {self.transcriber.get_name()}")
                self.show_status_block(
                    'switched',
                    _style("SWITCHED", _BOLD, _CYAN)
                    + f" Now transcribing with: {self.transcriber.get_name()}",
                    show_lineup=True,
                )
                return

            # Full circle: no other entry is constructible -- stay put.
            missing = sorted({e.env_var for _, e in skipped
                              if isinstance(e, MissingAPIKeyError)})
            logger.error(f"No other API available -- staying on {self.transcriber.get_name()}")
            if missing:
                logger.error(f"Missing API key(s): {', '.join(missing)} -- add them to "
                             f".env (see README), then restart Thoughtborne.")
            self.show_status_block(
                'switch-failed',
                _style(f"FAILED No other API available -- staying on "
                       f"{self.transcriber.get_name()}", _BOLD, _RED),
                action="add the missing key(s) to .env (see README), then restart",
                show_lineup=True,
            )

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
            logger.info(f"Recording started ({hotkey_display})")
            logger.debug("on_start_recording: marker A - after info log line")
            logger.debug("on_start_recording: marker B - before loop-alive check")

            # DEBUG: Check if recording loop thread is alive
            if self.recording_thread and self.recording_thread.is_alive():
                logger.debug("Recording loop thread is ALIVE")
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
                logger.info("Processing in background...")
                logger.info(f"You can start a new recording with {start_hotkey_display}!")

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
                logger.info("Processing in background (clipboard mode)...")
                logger.info(f"You can start a new recording with {start_hotkey_display}!")

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
                logger.info("Processing in background (will send)...")
                logger.info(f"You can start a new recording with {start_hotkey_display}!")

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
                logger.info("Processing in background (no auto-insert)...")
                logger.info(f"Press A or D to insert later, or {start_hotkey_display} for new recording")

    def on_cancel_recording(self):
        """Callback for cancel recording"""
        if self.audio_recorder.is_recording:
            hotkey_display = self._format_hotkey(HOTKEYS['cancel_recording'][0])
            logger.info(f"Recording cancelled ({hotkey_display})")

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
        thread = threading.Thread(
            target=self.retry_recording_thread,
            args=(rec, sequence_number),
            name=f"Retry-{sequence_number}-{datetime.datetime.now().strftime('%H%M%S%f')}"
        )
        thread.daemon = True
        thread.start()
        self.active_threads.append(thread)

        logger.info(f"Retry started ({hotkey_display}) for recording from "
                    f"{rec.origin_timestamp} (Seq: {sequence_number})")

    def retry_recording_thread(self, rec: '_FailedRecording', sequence_number: int):
        """Worker: re-transcribe an archived MP3 via the file-fallback chain and
        insert at the cursor like a fresh dictation (Issue #24).

        Reuses _run_empty_transcript_fallback (duration-gated Soniox V2/V4 file
        path) regardless of which API originally failed -- a Live failure can't
        re-stream a file, so the retry is uniformly the file-capable chain.
        A successful retry resolves the slot; a failed retry keeps it retryable."""
        thread_name = threading.current_thread().name
        timestamp = self.get_unique_timestamp()

        with self.processing_lock:
            self.processing_counter += 1
            current_count = self.processing_counter
        logger.info(f"[{thread_name}] Retrying recording from {rec.origin_timestamp} "
                    f"as sequence {sequence_number} (active: {current_count})")

        # Mirror the keyboard stop hotkey: the user is holding Ctrl+Alt+R when
        # this fires, so the output worker must wait for those modifiers to
        # release before typing -- otherwise the insert misfires as shortcuts.
        task = TranscriptionTask(
            sequence_number=sequence_number,
            timestamp=timestamp,
            use_clipboard=False,
            auto_insert=True,
            wait_for_key_release=True,
            trigger_keys=['ctrl', 'alt', 'r'],
        )

        try:
            print(f"[Seq: {sequence_number}] Retrying archived recording from {rec.origin_timestamp}...")
            transcript = self._run_empty_transcript_fallback(
                mp3_path=rec.archived_mp3_path,
                duration=rec.duration,
                sequence_number=sequence_number,
                thread_name=thread_name,
            ).rstrip('\n')

            if transcript:
                # save_transcript is base-class file I/O, so any current
                # transcriber instance is fine -- it's not API-specific.
                self.transcriber.save_transcript(transcript, timestamp)
                self.output_manager.update_last_transcript(transcript)
                task.transcript = transcript
                task.is_complete = True
                self._resolve_failed_slot(rec)
                logger.info(f"[{thread_name}] Retry for sequence {sequence_number} ready")
                print(f"[Seq: {sequence_number}] Retry completed, waiting for output...")
            else:
                logger.warning(f"[{thread_name}] Retry still empty for sequence {sequence_number} "
                               f"-- recording from {rec.origin_timestamp} stays retryable")
                task.is_error = True
                task.is_complete = True
                # Failed retry: slot already points at rec, leave it retryable.

            self.output_manager.add_task(task)

        except Exception as e:
            logger.error(f"[{thread_name}] Error retrying sequence {sequence_number}: {e}", exc_info=True)
            print(f"ERROR: [Seq: {sequence_number}] Retry error: {e}")
            task.is_error = True
            task.is_complete = True
            self.output_manager.add_task(task)

        finally:
            with self.processing_lock:
                self.processing_counter -= 1
                current_count = self.processing_counter
            logger.info(f"[{thread_name}] Retry for sequence {sequence_number} finished (active: {current_count})")

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
        only (#11). No call in here blocks unbounded: cancel_session() has a
        hard join budget (~6 s worst case) and save_recording() is local file
        I/O plus MP3 encode.
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
            logger.warning(f"Recording was still running ({reason}) -- audio saved "
                           f"({duration:.1f}s, not transcribed): {archive}")
            if sidecar is not None:
                sidecar.discard()  # only after the archive write succeeded
            return archive
        except Exception as e:
            kept = (f" Partial audio kept for next-start recovery: {sidecar.path}"
                    if sidecar is not None else "")
            logger.error(f"Could not save the running recording ({reason}): {e}.{kept}")
            return None

    def stop_program(self):
        """Stop the program"""
        logger.info("Program exit requested")

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
            logger.info(f"Waiting for {len(self.active_threads)} active processing...")
            for thread in self.active_threads:
                if thread.is_alive():
                    thread.join(timeout=5)

    def status_display_thread(self):
        """Display status updates periodically"""
        while self.running:
            time.sleep(STATUS_UPDATE_INTERVAL)
            with self.processing_lock:
                if self.processing_counter > 0:
                    print(f"\n[STATUS] Active processing: {self.processing_counter}")

    def recording_loop_thread(self):
        """Separate thread for audio recording loop"""
        logger.info("Recording loop thread STARTED")
        loop_counter = 0
        last_log_time = 0

        while self.running:
            loop_counter += 1

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

        logger.info("Recording loop thread STOPPED")

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
            # ERROR level on purpose: this IS an error state and must stand out.
            logger.error(f"Audio captured so far was saved ({duration:.1f}s): {archive}")
            logger.error(f"Reconnect your microphone, then press "
                         f"{self._format_hotkey(HOTKEYS['retry_last_failed'])} to transcribe it.")
            self.show_status_block(
                'device-loss',
                _style(f"FAILED Microphone lost -- recording ended and saved "
                       f"({duration:.0f}s, not transcribed)", _BOLD, _RED),
                action=f"reconnect your microphone, then press "
                       f"{self._format_hotkey(HOTKEYS['retry_last_failed'])} to transcribe it",
            )
        except Exception as e:
            kept = (f" Partial audio kept for next-start recovery: {sidecar.path}"
                    if sidecar is not None else "")
            logger.error(f"Could not save the aborted recording: {e}.{kept}")

    def print_instructions(self):
        """Print the startup banner (main thread, before hotkeys -- print() is
        safe here). Deliberately slim since #37: the essentials (active API,
        hotkeys, history path, what to press now) repeat in every status block;
        the full hotkey reference lives in the README."""
        print(f"\n=== Thoughtborne running (Windows version) ===")
        print(f"Log file: {LOG_FILE}")
        print(f"Max parallel processing: {MAX_PARALLEL_TRANSCRIPTIONS}")
        print(f"Self-test: {self._format_hotkey(HOTKEYS['test_transcription'])} "
              f"transcribes 'test_audio.mp3'")
        print("\nCtrl+Alt+H sends message after transcription - perfect for chatbots!")
        print("     Use Y to process without inserting. Insert later with A or D.")
        print("\nCtrl+Alt+D uses clipboard for faster insertion!")
        print("Texts are always inserted in recording order!")
        print("=========================================\n")

    # ===== GLANCEABLE STATUS BLOCK (#37) =====

    def show_status_block(self, event, headline, action=(), show_lineup=False,
                          extra_lines=(), detail=""):
        """Compose and print the glanceable status block (#37).

        The block is ONE pre-composed string emitted through the console-only
        logger: it rides the same bounded queue as all console logging (#11 --
        non-blocking from every thread, serialized by the single QueueListener,
        and written in a single write() so concurrent prints can never land
        inside it). It never reaches the file log; one DEBUG breadcrumb on the
        main logger carries the event into the file instead. Never raises: a
        broken status block must never break an insert or a switch
        (stability #1).

        Args:
            event: Short event id for the file-log breadcrumb.
            headline: Block headline including its styled tag (OK/FAILED/
                      READY/SWITCHED), built by the caller via _style.
            action: "What now" line(s) -- a string or a sequence of strings;
                    follow-up lines align under the first.
            show_lineup: True renders the full API lineup (startup + switch);
                         False the one-line active-API summary.
            extra_lines: Lines right below the headline (startup notes).
            detail: Extra text for the breadcrumb (e.g. "seq=12 chars=184").
        """
        try:
            lines = ["", _SEP, " " + headline]
            lines.extend(" " + extra for extra in extra_lines)
            if isinstance(action, str):
                action = (action,)
            for i, action_line in enumerate(action):
                if i == 0:
                    lines.append(" " + _style("What now: " + action_line, _BOLD))
                else:
                    lines.append(" " + " " * len("What now: ") + action_line)
            lines.extend(self._compose_api_lines(show_lineup))
            lines.append(self._compose_hotkey_line())
            lines.append(f" History: {HISTORY_FOLDER}   "
                         f"(open: {self._format_hotkey(HOTKEYS['open_history'])})")
            lines.append(_SEP)
            console_logger.info("\n".join(lines), extra={'raw_console': True})
            logger.debug(f"Status block: event={event} api={self.current_api}"
                         + (f" {detail}" if detail else ""))
        except Exception as e:
            logger.debug(f"Status block suppressed ({event}): {e}")

    def _compose_api_lines(self, show_lineup):
        """API section of the status block: the full lineup (startup, switch)
        or a one-line summary of the active API. Labels and descriptors come
        from config.API_DISPLAY (#30), order from AVAILABLE_APIS; '(default)'
        follows config.DEFAULT_API at runtime."""
        switch_combo = self._format_hotkey(HOTKEYS['switch_api'])

        def display(api_name):
            entry = API_DISPLAY.get(api_name)
            if entry is None:  # custom config edit -- degrade, don't lose the block
                return api_name, ""
            return entry["label"], entry["descriptor"]

        if not show_lineup:
            label, descriptor = display(self.current_api)
            described = f"{label} - {descriptor}" if descriptor else label
            if self.current_api == DEFAULT_API:
                described += " (default)"
            left = f" API: {described}"
            return [left + " " * max(3, 55 - len(left)) + f"switch: {switch_combo}"]

        lines = [f" API (switch: {switch_combo}):"]
        width = max(len(display(api_name)[0]) for api_name in AVAILABLE_APIS)
        for api_name in AVAILABLE_APIS:
            label, descriptor = display(api_name)
            marker = ">" if api_name == self.current_api else " "
            line = f"   {marker} {label.ljust(width)}"
            if descriptor:
                line += f" - {descriptor}"
            if api_name == DEFAULT_API:
                line += "   (default)"
            if api_name == self.current_api:
                line = _style(line, _BOLD)
            lines.append(line)
        return lines

    def _compose_hotkey_line(self):
        """One-line core-hotkey summary for the status block, derived from
        config.HOTKEYS so remapped combos show the user's real keys. The shared
        modifier prefix is factored out ("Ctrl+Alt + W record | ...") when all
        listed combos use the same one; otherwise full combos are printed."""
        def split_combo(combo):
            mods, _, key = combo.rpartition('+')
            return mods, (key or combo)

        combos = {
            'record': HOTKEYS['start_recording'],
            'stop_kb': HOTKEYS['stop_recording_keyboard'],
            'stop_clip': HOTKEYS['stop_recording_clipboard'],
            'send': HOTKEYS['stop_recording_send'],
            'keep': HOTKEYS['stop_recording_no_insert'],
            'cancel': HOTKEYS['cancel_recording'][0],
            'exit': HOTKEYS['exit_program'][0],
        }
        prefixes = {split_combo(combo)[0] for combo in combos.values()}
        if len(prefixes) == 1 and '' not in prefixes:
            lead = f"{self._format_hotkey(prefixes.pop())} + "
            def show(combo):
                return self._format_hotkey(split_combo(combo)[1])
        else:
            lead = ""
            show = self._format_hotkey
        return (f" Hotkeys: {lead}"
                f"{show(combos['record'])} record | "
                f"{show(combos['stop_kb'])}/{show(combos['stop_clip'])} stop+insert | "
                f"{show(combos['send'])} insert+send | "
                f"{show(combos['keep'])} keep | "
                f"{show(combos['cancel'])} cancel | "
                f"{show(combos['exit'])} exit")

    def _on_output_event(self, event, kind=None, seq=None, chars=None, sent=False):
        """Map OutputManager completion events onto status blocks (#37).

        This is the on_task_complete callback (see OutputManager.__init__ for
        the contract). Runs on the OutputManager thread; the block emission
        only enqueues (#11). Defensive like show_status_block: never raises
        into the output loop.

        Events:
            'inserted' -- transcript inserted; sent=True for the H flow.
            'ready'    -- Y flow: processed, waiting for a manual insert.
            'failed'   -- kind='transcription' or kind='insertion'.
        """
        try:
            insert_combos = (
                f"{self._format_hotkey(HOTKEYS['stop_recording_keyboard'])} (type) or "
                f"{self._format_hotkey(HOTKEYS['stop_recording_clipboard'])} (paste)")
            # Negative sequence numbers are internal (immediate tasks: self-test,
            # insert-last) -- omit them from the user-facing headline.
            seq_known = seq is not None and seq >= 0

            if event == 'inserted':
                what = ("Inserted at the cursor + sent" if sent
                        else "Inserted at the cursor")
                seq_part = f"seq {seq}, " if seq_known else ""
                self.show_status_block(
                    'inserted',
                    _style("OK", _BOLD, _GREEN) + f" {what} ({seq_part}{chars} chars)",
                    detail=f"seq={seq} chars={chars} sent={sent}",
                )
            elif event == 'ready':
                self.show_status_block(
                    'ready',
                    _style("READY", _BOLD, _GREEN)
                    + f" Transcript waiting ({chars} chars) -- not inserted yet",
                    action=f"insert at the cursor with {insert_combos}",
                    detail=f"seq={seq} chars={chars}",
                )
            elif event == 'failed' and kind == 'insertion':
                seq_part = f" (seq {seq})" if seq_known else ""
                self.show_status_block(
                    'insert-failed',
                    _style(f"FAILED Could not insert{seq_part} -- the transcript is kept",
                           _BOLD, _RED),
                    action=f"insert the last transcript with {insert_combos}",
                    detail=f"seq={seq}",
                )
            elif event == 'failed':
                seq_part = f" (seq {seq})" if seq_known else ""
                self.show_status_block(
                    'transcription-failed',
                    _style(f"FAILED Transcription failed{seq_part} -- nothing was inserted",
                           _BOLD, _RED),
                    action=(f"retry this recording with "
                            f"{self._format_hotkey(HOTKEYS['retry_last_failed'])}",
                            "(if an [AUTH] line is shown above: fix the key in .env, "
                            "then restart)",
                            f"(.env is in {SCRIPT_DIR})"),
                    detail=f"seq={seq}",
                )
        except Exception as e:
            logger.debug(f"Status block dispatch failed ({event}): {e}")

    def _register_hotkeys(self) -> bool:
        """Register all hotkeys using Win32 RegisterHotKey API.

        Returns True when the hotkey listener is up; run() gates its
        success messaging on this."""
        logger.info("Registering hotkeys via RegisterHotKey...")

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
            logger.error("Failed to start HotkeyManager")
            print("ERROR: Could not register hotkeys. Another instance may be running.")
            return False

        logger.info("All hotkeys registered successfully via RegisterHotKey")
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

        # Print instructions
        self.print_instructions()

        # Convert sidecars left over from a crash (#49 layer 3). Placed
        # before hotkey registration on purpose: no recording can start
        # while this converts, the recovery lines sit at the bottom of the
        # startup block, and the retry slot is armed before the first
        # keypress is possible. print() is fine here (main thread, no
        # hotkeys yet). A recovery failure must never block the start.
        try:
            try:
                # Best-effort probe: a file vanishing between glob and stat
                # (e.g. a second instance recovering it) must only cost this
                # hint line, never the recovery below.
                if any(p.stat().st_size > 10 * 1024 * 1024
                       for p in ARCHIVE_FOLDER.glob("voice_*.partial")):
                    print("Recovering unsaved recording, this may take a moment...")
            except OSError:
                pass
            recovered = recover_partial_files()
        except Exception as e:
            logger.error(f"Startup recovery failed: {e}", exc_info=True)
            recovered = []
        if recovered:
            for path, duration, ts in recovered:
                print(f"RECOVERED: unsaved recording from "
                      f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]} "
                      f"({duration:.0f}s) -> {Path(path).name}")
            print(f"Recovered audio is in: {ARCHIVE_FOLDER}")
            # Arm the retry slot with the newest recovered file (single-slot
            # semantics of #24: any later real failure simply overwrites it).
            # Built directly instead of via _record_failed_slot, whose path
            # reconstruction doesn't know the _recovered naming.
            newest = recovered[-1]
            with self._last_failed_lock:
                self._last_failed = _FailedRecording(
                    archived_mp3_path=newest[0],
                    duration=newest[1],
                    origin_timestamp=newest[2],
                )
            print(f"Press {self._format_hotkey(HOTKEYS['retry_last_failed'])} "
                  f"to transcribe the recovered audio.")

        # Register hotkeys
        hotkeys_ok = self._register_hotkeys()

        if hotkeys_ok:
            print("Global hotkeys registered. Press any hotkey to begin.")

            # First glanceable status block (#37): READY headline, the optional
            # startup notes (#40 fallback, #49 recovery), the full API lineup,
            # the hotkey one-liner and the history path.
            extra_lines = []
            if self._startup_fallback_note:
                extra_lines.append(f"NOTE: {self._startup_fallback_note}")
            if recovered:
                retry_combo = self._format_hotkey(HOTKEYS['retry_last_failed'])
                if len(recovered) == 1:
                    extra_lines.append(f"RECOVERED: unsaved recording saved -- "
                                       f"transcribe it: {retry_combo}")
                else:
                    extra_lines.append(f"RECOVERED: {len(recovered)} unsaved recordings "
                                       f"saved -- transcribe the newest: {retry_combo}")
            self.show_status_block(
                'startup',
                _style("READY", _BOLD, _GREEN)
                + f" -- press {self._format_hotkey(HOTKEYS['start_recording'])} "
                  f"and start talking",
                show_lineup=True,
                extra_lines=extra_lines,
            )
        else:
            # No READY invitation after a failed registration -- the tool keeps
            # running without hotkeys (status quo), but the block must say so.
            self.show_status_block(
                'hotkeys-failed',
                _style("FAILED Hotkeys could not be registered -- the tool "
                       "cannot react to key presses", _BOLD, _RED),
                action="close any other running Thoughtborne instance, then restart",
            )

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
                    logger.info("HotkeyManager stopped")
                except Exception as e:
                    logger.warning(f"Error stopping HotkeyManager: {e}")

            self.cleanup()

    def cleanup(self):
        """Clean up resources"""
        logger.info("Cleaning up...")
        print("Cleaning up...")

        # Salvage an in-flight recording (#49): covers Ctrl+C / Ctrl+Break,
        # where this is the first shutdown code that runs. After an
        # exit-hotkey shutdown the idempotency flag makes it a no-op.
        self._salvage_active_recording("shutdown")

        # Stop output manager
        self.output_manager.stop()

        # Clean up audio resources
        self.audio_recorder.cleanup()

        logger.info("Program ended")
        logger.info("=" * 60)

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
    finally:
        print("Program ended")


if __name__ == "__main__":
    main()
