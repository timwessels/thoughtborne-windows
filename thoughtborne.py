#!/usr/bin/env python3
"""
Thoughtborne Main Application (Windows version)

This is the main entry point for the voice-to-text application.
It orchestrates the audio recording, transcription, and text output
using hotkey controls.

The application uses:
- Multiple transcription APIs (Soniox v2/v4/Live, GROQ)
- Soniox Live (WebSocket real-time streaming) as default API at startup
- Soniox for high-quality transcription
- GROQ Whisper Large V3 Turbo for fast transcription
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
import logging
import threading
import datetime
from pathlib import Path
from typing import List, Optional
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener

# Import our modules
from config import (
    LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    LOG_CONSOLE_QUEUE_MAX,
    HOTKEYS, STATUS_UPDATE_INTERVAL, MAX_PARALLEL_TRANSCRIPTIONS,
    GROQ_MODEL, LANGUAGE, SCRIPT_DIR, DEFAULT_API, AVAILABLE_APIS,
    SHORT_AUDIO_THRESHOLD,
)
from hotkey_manager import HotkeyManager, is_key_pressed
from audio_handler import AudioRecorder
from transcriber import (
    create_transcriber,
    SonioxLiveTranscriber,
    SonioxTranscriber,
    SonioxV4Transcriber,
)
from output_handler import OutputManager, TranscriptionTask


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
console_handler.setFormatter(formatter)

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


# ===== STDOUT/STDERR REDIRECT TO LOG =====
class StreamToLogger:
    """
    Redirect stdout/stderr to logger while still showing in console.
    This captures ALL output including print() and external library warnings.
    """
    def __init__(self, logger, log_level=logging.INFO, original_stream=None):
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ''
        self.original_stream = original_stream

    def write(self, buf):
        # Write to original stream (so it still shows in terminal)
        if self.original_stream:
            self.original_stream.write(buf)
            self.original_stream.flush()

        # Also log it
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, line.rstrip())

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


# Redirect stdout and stderr to logger (while keeping console output)
original_stdout = sys.stdout
original_stderr = sys.stderr
sys.stdout = StreamToLogger(logger, logging.DEBUG, original_stdout)
sys.stderr = StreamToLogger(logger, logging.WARNING, original_stderr)

# Clean up old log backups on startup
cleanup_old_logs(LOG_FILE, max_age_days=30)


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
            self.current_api = DEFAULT_API
            self.transcriber = create_transcriber(self.current_api)
            self.output_manager = OutputManager(on_task_complete_callback=self.print_ready_status)
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

        # Hotkey manager (initialized in _register_hotkeys)
        self.hotkey_manager = None

        logger.info(f"Configuration: Default API={DEFAULT_API}, Max parallel={MAX_PARALLEL_TRANSCRIPTIONS}")
        logger.info(f"Current transcriber: {self.transcriber.get_name()}")
        logger.info("Application initialized successfully")

    def get_unique_timestamp(self) -> str:
        """Generate a unique timestamp with counter"""
        with self.timestamp_lock:
            self.timestamp_counter += 1
            return f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{self.timestamp_counter:03d}"

    def process_recording_thread(self, frames: List[bytes], duration: float,
                                sequence_number: int, timestamp: str,
                                use_clipboard: bool = False, auto_insert: bool = True,
                                send_after_insert: bool = False, wait_for_keys: List[str] = None,
                                transcriber=None):
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
                               transcriber_override=None) -> bool:
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
            args=(frames, duration, sequence_number, timestamp, use_clipboard, auto_insert, send_after_insert, wait_for_keys, transcriber),
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
          - duration < SHORT_AUDIO_THRESHOLD (58 s) -> SonioxTranscriber (V2
            sync, ~2-3 s for 30 s audio); if V2 fails (exception or empty
            result), fall through to SonioxV4Transcriber. This second hop also
            covers the case where V2 (legacy gRPC) is shut down by Soniox.
          - duration >= SHORT_AUDIO_THRESHOLD       -> SonioxV4Transcriber (V4
            async polling, ~10-60 s; only option past V2's 60 s hard limit).

        Note: this assumes SHORT_AUDIO_THRESHOLD < 60 s, because
        SonioxTranscriber.transcribe uses the same threshold internally to pick
        sync vs async, and Soniox's sync path has a 60 s hard limit. Raising
        SHORT_AUDIO_THRESHOLD above 60 would make V2 attempt the sync path on
        recordings it can't handle; the fallthrough to V4 would still recover
        the transcript, but the fast-path latency advertised above is gone.

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
        lock, then runs transcribe() outside the lock so parallel fallbacks
        don't serialize on the network call. Any exception is caught and logged
        -- this method always returns a string, never raises.

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

            start = time.time()
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

            logger.info("Test completed")
        else:
            logger.error(f"Test file not found: {test_file}")
            logger.error("Place a file named 'test_audio.wav' or 'test_audio.mp3' in the script directory.")

    def switch_api(self):
        """Switch between available transcription APIs"""
        try:
            # Determine next API
            current_index = AVAILABLE_APIS.index(self.current_api)
            next_index = (current_index + 1) % len(AVAILABLE_APIS)
            next_api = AVAILABLE_APIS[next_index]

            logger.info(f"Switching API from {self.current_api} to {next_api}")

            # Try to create new transcriber
            try:
                new_transcriber = create_transcriber(next_api)
                self.transcriber = new_transcriber
                self.current_api = next_api

                logger.info(f"Successfully switched to {self.transcriber.get_name()}")
                self.print_ready_status()

            except Exception as e:
                logger.error(f"Failed to switch to {next_api}: {e}")
                logger.info(f"Continuing with: {self.transcriber.get_name()}")

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
            self.just_finished_recording_a = True
            self.recording_finished_time_a = time.time()

            # Capture live transcriber reference before clearing
            recording_transcriber = self._active_live_transcriber or self.transcriber
            self._active_live_transcriber = None

            logger.info(f"Recording duration: {duration:.1f} seconds")

            # Start processing with wait for key release
            if self.start_processing_thread(frames, duration, wait_for_keys=['ctrl', 'alt', 'a'],
                                            transcriber_override=recording_transcriber):
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
            self.just_finished_recording_d = True
            self.recording_finished_time_d = time.time()

            # Capture live transcriber reference before clearing
            recording_transcriber = self._active_live_transcriber or self.transcriber
            self._active_live_transcriber = None

            logger.info(f"Recording duration: {duration:.1f} seconds")

            # Start processing with clipboard flag and wait for key release
            if self.start_processing_thread(frames, duration, use_clipboard=True, wait_for_keys=['ctrl', 'alt', 'd'],
                                            transcriber_override=recording_transcriber):
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
            self.just_finished_recording_d = True
            self.recording_finished_time_d = time.time()

            # Capture live transcriber reference before clearing
            recording_transcriber = self._active_live_transcriber or self.transcriber
            self._active_live_transcriber = None

            logger.info(f"Recording duration: {duration:.1f} seconds")

            # Start processing with clipboard AND send_after_insert, wait for key release
            if self.start_processing_thread(frames, duration, use_clipboard=True, send_after_insert=True,
                                            wait_for_keys=['ctrl', 'alt'],
                                            transcriber_override=recording_transcriber):
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

            # Capture live transcriber reference before clearing
            recording_transcriber = self._active_live_transcriber or self.transcriber
            self._active_live_transcriber = None

            logger.info(f"Recording duration: {duration:.1f} seconds")

            # Start processing WITHOUT auto-insert
            if self.start_processing_thread(frames, duration, use_clipboard=False, auto_insert=False,
                                            transcriber_override=recording_transcriber):
                logger.info("Processing in background (no auto-insert)...")
                logger.info(f"Press A or D to insert later, or {start_hotkey_display} for new recording")
                self.print_ready_status()

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
            self.print_ready_status()

    def on_test_transcription(self):
        """Callback for test transcription hotkey"""
        self.handle_test_transcription()

    def on_switch_api(self):
        """Callback for switch API hotkey"""
        self.switch_api()

    def on_exit_program(self):
        """Callback for exit program"""
        self.stop_program()

    def stop_program(self):
        """Stop the program"""
        logger.info("Program exit requested")

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

            # Reset just_finished flags after timeout
            if self.just_finished_recording_a and time.time() - self.recording_finished_time_a > 2:
                self.just_finished_recording_a = False

            if self.just_finished_recording_d and time.time() - self.recording_finished_time_d > 2:
                self.just_finished_recording_d = False

            time.sleep(0.01)  # Small delay to prevent high CPU usage

        logger.info("Recording loop thread STOPPED")

    def print_instructions(self):
        """Print usage instructions"""
        emoji = self._get_api_emoji()
        print(f"\n=== Thoughtborne running (Windows version) ===")
        print(f"Current API: {emoji} {self.transcriber.get_name()}")
        print(f"Available APIs: {', '.join(AVAILABLE_APIS)}")
        print(f"Log file: {LOG_FILE}")
        print(f"Max parallel processing: {MAX_PARALLEL_TRANSCRIPTIONS}")
        print("\nControls (Windows - using Ctrl+Alt):")
        print("- Ctrl+Alt+W: Start recording (can be pressed during processing)")
        print("- Ctrl+Alt+A: Stop recording / Insert last text (keyboard)")
        print("- Ctrl+Alt+D: Stop recording / Insert last text (clipboard)")
        print("- Ctrl+Alt+H: Stop recording / Insert & SEND (press Enter)")
        print("- Ctrl+Alt+Y: Stop recording / Process only (insert later with A/D)")
        print("- Ctrl+Alt+X: Cancel recording")
        print("- Ctrl+Alt+L: Switch transcription API")
        print("- Ctrl+Alt+Ü: TEST - Transcribe file 'test_audio.mp3'")
        print("- Ctrl+Alt+4: Exit program")
        print("\nCtrl+Alt+H sends message after transcription - perfect for chatbots!")
        print("     Use Y to process without inserting. Insert later with A or D.")
        print("\nCtrl+Alt+D uses clipboard for faster insertion!")
        print("Texts are always inserted in recording order!")
        print("\nAPI Models: [SONIOX] v2 precise | [SONv4] v4 async | [LIVE] v4 stream | [GROQ] fast")
        print("=========================================\n")

    def _get_api_emoji(self):
        """Get emoji for current API"""
        api_name = self.transcriber.get_name().lower()
        if 'v4' in api_name:
            return '[SONv4]'  # Soniox v4 Async REST
        elif 'live' in api_name:
            return '[LIVE]'  # Soniox Live WebSocket RT
        elif 'groq' in api_name:
            return '[GROQ]'  # Fast
        elif 'soniox' in api_name:
            return '[SONIOX]'  # Soniox v2 Legacy (precision)
        return ''

    def print_ready_status(self, is_ready_to_insert=False, char_count=0):
        """
        Print ready status with current API info

        Args:
            is_ready_to_insert: If True, this is a Y-task ready for insertion (show READY)
            char_count: Number of characters in the text (only for Y-task)
        """
        emoji = self._get_api_emoji()

        if is_ready_to_insert:
            # Y-Taste: Text verarbeitet, bereit zum Einfuegen - DEUTLICH mit READY
            logger.info(f"READY! Text processed ({char_count} chars) - Press A or D | {emoji} {self.transcriber.get_name()}")
        else:
            # Normales Einfuegen: Nur Modell-Status
            logger.info(f"{emoji} {self.transcriber.get_name()}")

    def _register_hotkeys(self):
        """Register all hotkeys using Win32 RegisterHotKey API"""
        logger.info("Registering hotkeys via RegisterHotKey...")

        self.hotkey_manager = HotkeyManager()

        # Single-value hotkeys
        single_hotkeys = {
            'start_recording': self.on_start_recording,
            'stop_recording_keyboard': self.on_stop_recording_keyboard,
            'stop_recording_clipboard': self.on_stop_recording_clipboard,
            'stop_recording_send': self.on_stop_recording_send,
            'stop_recording_no_insert': self.on_stop_recording_no_insert,
            'test_transcription': self.on_test_transcription,
            'switch_api': self.on_switch_api,
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
            return

        logger.info("All hotkeys registered successfully via RegisterHotKey")

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

        # Register hotkeys
        self._register_hotkeys()

        print("Global hotkeys registered. Press any hotkey to begin.")

        # Show current API prominently (same format as after API switch)
        self.print_ready_status()

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
    try:
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
