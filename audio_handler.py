"""
Audio Handler Module

This module handles all audio-related operations including:
- Recording audio from microphone
- Saving audio files in various formats
- Managing audio archives
- Calculating audio duration
- Lazy stream initialization (opens stream only when recording)
- Device change detection (e.g., headset connected/disconnected)

Classes:
    AudioRecorder: Main class for audio recording and processing
"""

import os
import re
import time
import wave
import msvcrt
import logging
import datetime
import threading
import pyaudio
import soundfile as sf
import numpy as np
from pathlib import Path
from typing import List, NamedTuple, Tuple, Optional

from config import (
    CHUNK, FORMAT, CHANNELS, RATE,
    ARCHIVE_FOLDER, SCRIPT_DIR,
    AUDIO_TRIM_END_MS, AUDIO_SILENCE_PADDING_MS,
    SIDECAR_FLUSH_SECONDS, FILE_ONLY,
    AUDIO_READ_POLL_SECONDS, AUDIO_STALL_TIMEOUT_SECONDS,
    AUDIO_CLOSE_LOCK_TIMEOUT, AUDIO_OPEN_TIMEOUT_SECONDS
)

logger = logging.getLogger('Thoughtborne.AudioHandler')


# ===== Crash-safety sidecar (#49) =====
# During recording, a per-session observer thread mirrors the captured frames
# into a raw-PCM .partial file in the archive folder. The sidecar is pure crash
# insurance: every in-process path (stop, cancel, exit salvage, device-loss
# endgame) saves from RAM via save_recording() exactly as before and only
# deletes the sidecar; it is read solely by the startup recovery, i.e. when the
# process died before it could save. Raw PCM with the parameters in the file
# name (voice_<start-ts>_r<rate>c<channels>s16le.partial) keeps every byte
# prefix decodable after a kill -- a WAV header's size fields would always be
# wrong in the only situation the file is ever read.

_SIDECAR_NAME_RE = re.compile(
    r"^voice_(?P<ts>\d{8}_\d{6}_\d{3})_r(?P<rate>\d+)c(?P<channels>\d+)s16le\.partial$"
)


class SidecarHandle(NamedTuple):
    """Lightweight handle to a stopped (or stopping) session's sidecar file,
    passed to whichever code path gets to decide its fate."""
    path: Path
    closed_event: threading.Event

    def discard(self):
        """Delete the sidecar once the writer has released the file handle.
        Windows cannot delete open files -- hence the event handshake."""
        try:
            if not self.closed_event.wait(timeout=2.0):
                logger.warning(f"Sidecar writer still busy, not deleting: {self.path}")
                return
            self.path.unlink(missing_ok=True)
            logger.debug(f"Sidecar removed: {self.path}")
        except Exception as e:
            logger.warning(f"Could not remove sidecar {self.path}: {e}")


class _SidecarWriter:
    """Per-recording observer thread that appends newly captured frames to the
    sidecar file every SIDECAR_FLUSH_SECONDS (#49 layer 2).

    The capture hot path is untouched by construction: the writer holds the
    session's frames-list reference and observes it by index (list.append /
    len / slice are consistent under the CPython GIL; indices below len() are
    append-only until the session ends). Disk stalls block only this thread --
    frames keep growing in RAM exactly as without this layer. On any I/O error
    it warns once, gives up for this session, and the recording continues
    RAM-only; a rescue path must never endanger what it rescues.
    """

    def __init__(self, frames_ref: List[bytes], path: Path):
        self._frames_ref = frames_ref
        self.path = path
        self._fh = None
        self._failed = False
        self._stop_event = threading.Event()
        self.closed_event = threading.Event()

    def start(self) -> bool:
        """Open the sidecar file, take the writer lock, start the thread.

        Returns False (after exactly one WARNING) when the file cannot be
        created -- the session then runs RAM-only, today's behavior.
        """
        try:
            self._fh = open(self.path, 'wb')
            # One-byte lock at offset 0 marks the file as owned by a live
            # writer: a second instance's startup recovery probes it and
            # skips instead of stealing a sidecar that is still being
            # written. The OS releases the lock when this process dies, so
            # crash + immediate restart recovers without any wait.
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        except Exception as e:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
            logger.warning(f"Crash-safety file could not be written ({e}) "
                           f"-- recording continues unaffected (RAM only).")
            self.closed_event.set()
            return False
        thread = threading.Thread(target=self._writer_loop, name="SidecarWriter", daemon=True)
        thread.start()
        return True

    def request_stop(self):
        """Signal the writer to do a final flush, close and unlock. Idempotent."""
        self._stop_event.set()

    def _writer_loop(self):
        written = 0
        try:
            while not self._stop_event.wait(timeout=SIDECAR_FLUSH_SECONDS):
                written = self._flush_new_frames(written)
                if self._failed:
                    return
            self._flush_new_frames(written)  # final flush on stop
        finally:
            self._close_and_unlock()
            self.closed_event.set()

    def _flush_new_frames(self, written: int) -> int:
        frames = self._frames_ref
        n = len(frames)
        if n > written:
            try:
                self._fh.write(b''.join(frames[written:n]))
                self._fh.flush()
                os.fsync(self._fh.fileno())  # batch is BSOD-safe, not just kill-safe
            except Exception as e:
                self._failed = True
                logger.warning(f"Crash-safety file could not be written ({e}) "
                               f"-- recording continues unaffected (RAM only).")
                return written
        return n

    def _close_and_unlock(self):
        if self._fh is None:
            return
        try:
            try:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass  # the lock dies with the handle either way
            self._fh.close()
        except Exception as e:
            logger.debug(f"Error closing sidecar file {self.path}: {e}")
        self._fh = None


def _is_locked_by_live_writer(path: Path) -> bool:
    """Probe a sidecar's one-byte writer lock without blocking (#49).

    True means a live _SidecarWriter (typically a second Thoughtborne
    instance, which keeps running hotkey-less after RegisterHotKey fails and
    runs its recovery before that) still owns the file -- or it cannot be
    opened at all; either way recovery must leave it alone for now.
    """
    try:
        fh = open(path, 'r+b')
    except OSError:
        return True
    try:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return True
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass  # probe lock dies with the handle below
        return False
    finally:
        fh.close()


def _parse_sidecar_name(path: Path) -> Tuple[str, int, int]:
    """Extract (start_ts, rate, channels) from a sidecar file name.

    The parameters ride in the name so a sidecar that survived a config
    change (e.g. a RATE switch like #2) is still decoded correctly. An
    unparseable name falls back to the current config values and the file
    mtime with a WARNING -- recovering with possibly wrong parameters beats
    dropping the audio.
    """
    m = _SIDECAR_NAME_RE.match(path.name)
    if m:
        return m.group("ts"), int(m.group("rate")), int(m.group("channels"))
    logger.warning(f"Unexpected sidecar name {path.name} -- assuming current config "
                   f"audio format (r{RATE}c{CHANNELS}s16le) and mtime as timestamp")
    mtime_ts = datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y%m%d_%H%M%S')
    return f"{mtime_ts}_000", RATE, CHANNELS


# Recognizes a kill-recovered archive name (recover_partial_files, below) so a
# successful Ctrl+Alt+R retry can tag it with the producing engine (#98). The
# engine token is inserted before the _recovered marker; the double-recovery
# disambiguator (_<n>) is preserved. Anchored ^...$ so only an exact recovered
# name is ever renamed. Engine tokens carry no '_' (config.ENGINE_TOKENS), so
# '_recovered' is an unambiguous anchor.
_RECOVERED_ARCHIVE_RE = re.compile(
    r"^(?P<base>voice_\d{8}_\d{6}_\d{3})_recovered(?P<disamb>_\d+)?\.mp3$"
)


def recover_partial_files() -> List[Tuple[str, float, str]]:
    """Convert leftover .partial sidecars into archive MP3s (#49 layer 3).

    Scans ARCHIVE_FOLDER for sidecars whose process died before it could save
    and converts each into voice_<start-ts>_recovered.mp3. Returns a list of
    (archive_path, duration_seconds, start_ts), oldest first. Sidecars still
    held by a live writer are skipped; a sidecar that fails conversion is
    renamed to .partial-unrecoverable (bytes kept for manual rescue) so a
    deterministically broken file cannot error-spam every future start.
    Needs no live PyAudio instance -- safe to run before any recording.

    Unlike save_recording's preprocessing this pads silence but does NOT trim
    the tail: the trim removes the stop-hotkey click, and a crash had no stop
    hotkey -- trimming would discard real dictation.
    """
    results = []
    for partial in sorted(ARCHIVE_FOLDER.glob("voice_*.partial")):
        if _is_locked_by_live_writer(partial):
            logger.info(f"Skipping {partial.name} (still being written by another "
                        f"instance or not accessible right now)")
            continue
        try:
            ts, rate, channels = _parse_sidecar_name(partial)
            raw = partial.read_bytes()
            frame_bytes = 2 * channels  # int16
            raw = raw[: len(raw) - (len(raw) % frame_bytes)]  # trim torn final sample
            if not raw:
                partial.unlink()  # empty husk: nothing to save
                logger.debug(f"Removed empty sidecar: {partial.name}")
                continue
            samples = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
            padding = np.zeros((int(rate * AUDIO_SILENCE_PADDING_MS / 1000), channels),
                               dtype=np.int16)
            samples = np.concatenate([samples, padding])
            target = ARCHIVE_FOLDER / f"voice_{ts}_recovered.mp3"
            n = 2
            while target.exists():  # double-recovery edge: never overwrite
                target = ARCHIVE_FOLDER / f"voice_{ts}_recovered_{n}.mp3"
                n += 1
            sf.write(str(target), samples, rate, format='mp3')
            duration = len(raw) / (rate * frame_bytes)
            partial.unlink()  # only after the MP3 exists
            results.append((str(target), duration, ts))
            # Persist across restarts (#114); best-effort. Unlike the clean-exit
            # path, the .partial is already consumed (unlink above), so a failed
            # marker write costs only the next-restart offer -- keeping the partial
            # would be wrong (the next start would recover it again as a duplicate
            # _recovered_2.mp3).
            write_retry_marker(target, duration)
            logger.info(f"Recovered unsaved recording: {target.name} ({duration:.1f}s)", extra=FILE_ONLY)
        except Exception as e:
            logger.error(f"Could not recover {partial.name}: {e}", exc_info=True)
            try:
                partial.rename(partial.with_suffix(".partial-unrecoverable"))
            except Exception:
                pass
    return results


# ===== Persistent retry marker (#106/#114) =====
# The single source of truth "saved but never successfully transcribed". Written
# next to the archive mp3 at every untranscribed-audio source -- an in-session
# transcription failure (#24), the hard-kill .partial recovery above, and a
# clean-exit salvage (#106) -- it re-arms the Ctrl+Alt+R retry slot on the next
# start. Persistent (#114): the reader KEEPS a valid marker, so the recording
# stays retryable across any number of restarts until it is retried successfully
# or a newer failure supersedes it; only then is the marker removed. The duration
# rides in the name (integer milliseconds) so startup needs no file read or MP3
# decode -- and on the 2nd+ restart the marker is the only surviving source of the
# duration (the .partial is long gone). The mp3 STEM keys the marker so both the
# bare voice_<ts>.mp3 and the kill-recovered voice_<ts>_recovered(_<n>)?.mp3
# round-trip; _d<ms> is an unambiguous suffix anchor ('d' is not a digit, so it
# never collides with the ts's trailing _<3 digits> nor the _recovered _<n>
# disambiguator). No engine token: the recording was never transcribed, so the
# retry picks the engine itself -- exactly like an in-session failed slot.
#
# Backward compatible: a legacy bare voice_<ts>_d<ms>.needsretry (#106) matches
# with rec=None and reconstructs voice_<ts>.mp3 unchanged -- no migration needed.

_RETRY_MARKER_NAME_RE = re.compile(
    r"^voice_(?P<ts>\d{8}_\d{6}_\d{3})(?P<rec>_recovered(?:_\d+)?)?_d(?P<ms>\d+)\.needsretry$"
)


def write_retry_marker(mp3_path, duration: float) -> bool:
    """Persist a 'saved but not transcribed' marker next to the archive mp3 (#106/#114).

    Keyed on the mp3 stem so both the bare voice_<ts>.mp3 and the kill-recovered
    voice_<ts>_recovered(_<n>)?.mp3 round-trip. Best-effort and never raises
    (stability #1): a marker failure never breaks recording/exit. Returns True only
    when the marker was actually written.
    """
    try:
        stem = Path(mp3_path).stem
        ms = max(0, int(round(duration * 1000)))
        path = ARCHIVE_FOLDER / f"{stem}_d{ms}.needsretry"
        path.touch()
        logger.debug(f"Retry marker written: {path.name}")
        return True
    except Exception as e:
        logger.warning(f"Could not write retry marker for {mp3_path}: {e}")
        return False


def recover_salvaged_recordings() -> List[Tuple[str, float, str]]:
    """Read every persistent 'saved but not transcribed' marker (#106/#114).

    Returns (archive_mp3_path, duration_seconds, origin_ts) tuples, oldest first,
    matching recover_partial_files()'s shape so both feed one arming/display path.

    Persistent (#114): a valid marker is KEPT on read, so the recording stays
    retryable across any number of restarts until it is retried successfully or a
    newer failure supersedes it. Superseded-marker pruning happens elsewhere
    (startup reconciliation and the retry-success path); orphans are removed here.
    A marker whose archive mp3 is gone (manually cleared) or whose name is
    unparseable is removed here without arming. Never raises for one bad marker.
    """
    results = []
    for marker in sorted(ARCHIVE_FOLDER.glob("voice_*.needsretry")):
        try:
            m = _RETRY_MARKER_NAME_RE.match(marker.name)
            if not m:
                logger.warning(f"Unrecognized retry marker, removing: {marker.name}")
                marker.unlink(missing_ok=True)
                continue
            ts = m.group("ts")
            duration = int(m.group("ms")) / 1000.0
            mp3 = ARCHIVE_FOLDER / f"voice_{ts}{m.group('rec') or ''}.mp3"
            if not mp3.exists():
                logger.warning(f"Retry marker without archive, removing: {marker.name}")
                marker.unlink(missing_ok=True)
                continue
            results.append((str(mp3), duration, ts))
            # Marker intentionally KEPT -- persistent truth (#114). Superseding and
            # orphan cleanup are the reconciliation points' job, not the reader's.
            logger.info(f"Pending recording re-armed for retry: {mp3.name} ({duration:.1f}s)", extra=FILE_ONLY)
        except Exception as e:
            logger.error(f"Could not process retry marker {marker.name}: {e}", exc_info=True)
    return results


def delete_retry_marker(mp3_path) -> None:
    """Best-effort: remove the retry marker for one recording, by mp3 stem (#114).

    Globs '<stem>_d*.needsretry' so it matches regardless of the encoded duration.
    The stems are disjoint by construction (voice_<ts>_d* cannot match
    voice_<ts>_recovered_d*, and voice_<ts>_recovered_d* cannot match
    voice_<ts>_recovered_2_d*), so this only ever hits the intended marker. Never
    raises: a failed delete leaves an orphan the next startup prunes."""
    try:
        stem = Path(mp3_path).stem
        for marker in ARCHIVE_FOLDER.glob(f"{stem}_d*.needsretry"):
            marker.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Could not delete retry marker for {mp3_path}: {e}")


def delete_superseded_retry_markers(newest_ts: str) -> None:
    """Best-effort: drop every retry marker older than newest_ts (single-slot
    supersede, #24/#114).

    The ts is fixed-width, so lexicographic == chronological. A strict '<' never
    touches the newest marker itself. Audio is never deleted -- only the markers,
    so a superseded recording stops auto-arming but stays in the folder. Never
    raises."""
    try:
        for marker in ARCHIVE_FOLDER.glob("voice_*.needsretry"):
            m = _RETRY_MARKER_NAME_RE.match(marker.name)
            if m and m.group("ts") < newest_ts:
                marker.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Could not prune superseded retry markers: {e}")


def _repair_device_name(name: Optional[str]) -> Optional[str]:
    """Repair PyAudio device-name mojibake on non-English Windows (#113).

    PortAudio (>= v19.7) returns device names as UTF-8 bytes, but PyAudio
    decodes them with the ANSI code page (CP1252 on German Windows), so a
    "ö" (UTF-8 C3 B6) arrives as the CP1252 pair "Ã¶". Re-encoding as CP1252
    and decoding as UTF-8 reverses exactly that. Self-correcting: a pure-ASCII
    or already-correct name fails the re-encode/decode and is returned
    unchanged, so the repair can never double-apply; a non-str value (e.g. a
    missing 'name' key) passes through untouched instead of raising.
    """
    if not isinstance(name, str):
        return name
    try:
        return name.encode('cp1252').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


class AudioRecorder:
    """Handles audio recording and file operations"""

    def __init__(self):
        """Initialize the audio recorder"""
        self.p = None
        self.stream = None
        self.stream_is_open = False  # Track whether stream is currently open
        self.recording = False
        self.frames = []
        self.stream_error_count = 0
        self.max_stream_errors = 5  # After 5 errors, try to reinitialize
        self._stream_lock = threading.Lock()  # Lock to prevent race conditions with stream access
        # Driver-wedge poison (#128): set when a stalled native audio call could
        # not be closed within its timeout. A poisoned pipeline is abandoned
        # untouched (leaked) and rebuilt on the next start -- nothing ever runs a
        # native stop/close/terminate on the wedged handles again.
        self._audio_poisoned = False
        self.last_device_index = None  # Track which device was used

        # Crash-safety sidecar state (#49 layer 2). The writer lives for one
        # recording; the counter disambiguates same-second session starts.
        self._sidecar_writer = None
        self._sidecar_counter = 0

        # Device-loss endgame state (#49 layer 4). record_chunk() pins the
        # dying session's frames AND its sidecar writer (so a new session
        # started in the abort window can never have its own writer stolen
        # by the abort handler) BEFORE raising the flag; the recording loop
        # consumes the flag and collects both via take_aborted_frames() /
        # take_aborted_sidecar(). Plain attributes are enough: the flag is
        # set and consumed on the recording-loop thread, and a deliberate
        # stop/cancel clears it only after _close_stream() has synchronized
        # with any in-flight error path (see stop_recording).
        self.recording_aborted = False
        self.aborted_frames = None
        self.aborted_writer = None

        # Drop diagnostic: wallclock start time of current recording (set in start_recording)
        self._recording_start_time = None

        # Drop diagnostic: track consecutive exact-silence chunks to detect mic drops
        # (PyAudio returns all-zero samples when the audio device stalls, e.g. BT
        # profile switch or recording-loop stall on blocking _ws.send)
        self._silence_chunks_in_row = 0
        self._silence_logged_flag = False

        # Drop diagnostic (Block 1.5): PyAudio read latency tracking.
        # A slow stream.read() (>100 ms; nominal ~64 ms at CHUNK=1024 / RATE=16000)
        # means the audio source did not deliver fresh samples in time — typically
        # a BT profile switch, driver hiccup, or PyAudio internal stall. Distinct
        # from send-latency (Block 1) which points at the network; read-latency
        # points at the audio source.
        self._read_latency_max = 0.0
        self._read_latency_slow_count = 0
        self._read_latency_slow_total = 0.0

        # Drop diagnostic (Block 1.5): recording-loop iteration gap.
        # Measures wallclock time BETWEEN consecutive record_chunk() returns,
        # i.e. the caller-side time (send_audio_chunk + sleep + any other work
        # in recording_loop_thread). Nominal ~33 ms (sleep 10 + send <1 + tiny
        # overhead). Gaps > 50 ms mean something blocked the loop OUTSIDE the
        # read — slow WebSocket send (Block 1 tracks those separately) or GIL
        # contention from the receiver thread.
        self._loop_iteration_max = 0.0
        self._loop_iteration_slow_count = 0
        self._loop_iteration_slow_total = 0.0
        self._last_iteration_end_time = None

        # NOTE: PyAudio is NOT initialized here anymore!
        # It will be initialized on-demand when recording starts (see _ensure_pyaudio_ready())
        self._ensure_directories()

    def _ensure_directories(self):
        """Create archive directories if they don't exist"""
        ARCHIVE_FOLDER.mkdir(parents=True, exist_ok=True)
        logger.info(f"Archive folder ready: {ARCHIVE_FOLDER}", extra=FILE_ONLY)

    def _ensure_pyaudio_ready(self) -> bool:
        """
        Ensure PyAudio is initialized with current default device

        This method reinitializes PyAudio before each recording to detect
        device changes (e.g., headset connected/disconnected).

        Returns:
            True if successful, False otherwise
        """
        logger.debug("_ensure_pyaudio_ready: entry")
        try:
            if self._audio_poisoned:
                # The previous stream/PyAudio wedged at driver level (#128). Drop
                # the handles WITHOUT terminate()/close(): on a wedged driver
                # those would block exactly as the stalled read did. Leak them
                # (the OS reclaims them on process exit) and build fresh --
                # clearing poison here is what re-enables recording.
                logger.warning("Rebuilding the audio pipeline after a wedged device (#128) "
                               "-- the previous handles are abandoned untouched.")
                self.stream = None
                self.p = None
                self.stream_is_open = False
                self._audio_poisoned = False
            # Terminate old PyAudio instance if exists
            elif self.p is not None:
                try:
                    logger.debug("_ensure_pyaudio_ready: about to terminate old PyAudio instance")
                    self.p.terminate()
                    logger.debug("Old PyAudio instance terminated")
                except Exception as e:
                    logger.warning(f"Error terminating old PyAudio: {e}")

            # Initialize new PyAudio instance
            logger.debug("_ensure_pyaudio_ready: about to call pyaudio.PyAudio()")
            self.p = pyaudio.PyAudio()
            logger.info("PyAudio reinitialized to detect current default device", extra=FILE_ONLY)

            # Get and log current default input device
            try:
                logger.debug("_ensure_pyaudio_ready: about to query default input device")
                default_device = self.p.get_default_input_device_info()
                device_name = _repair_device_name(default_device.get('name', 'Unknown'))
                device_index = default_device.get('index', -1)

                # Check if device changed
                if self.last_device_index is not None and self.last_device_index != device_index:
                    logger.info(f"Audio device changed! Now using: [{device_index}] {device_name}")
                else:
                    logger.info(f"Using audio input device: [{device_index}] {device_name}")

                self.last_device_index = device_index

            except Exception as e:
                logger.warning(f"Could not get default input device info: {e}")

            logger.debug("_ensure_pyaudio_ready: returning True")
            return True

        except Exception as e:
            logger.error(f"Error reinitializing PyAudio: {e}", exc_info=True)
            return False

    def _open_stream(self) -> bool:
        """
        Open the audio stream (lazy initialization)

        This is called when recording starts, not at script startup.
        This prevents the headset from being in "headset mode" all the time.

        Returns:
            True if stream opened successfully, False otherwise
        """
        if self.stream_is_open:
            logger.debug("Audio stream already open")
            return True

        try:
            logger.info("Opening audio stream...", extra=FILE_ONLY)
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            self.stream_is_open = True
            logger.info("Audio stream opened successfully", extra=FILE_ONLY)
            return True
        except Exception as e:
            logger.error(f"Error opening audio stream: {e}", exc_info=True)
            return False

    def _close_stream(self):
        """
        Close the audio stream

        This is called after recording ends (stop or cancel) and on cleanup.
        This releases the headset from "headset mode".

        Two-stage containment for a driver-wedged stream (#128). A stalled
        capture read holds _stream_lock inside a native PortAudio call, so a
        plain `with self._stream_lock:` here -- on the hotkey-listener thread
        (stop/cancel/exit) or MainThread (cleanup) -- would block every hotkey
        forever, the exact freeze issue #128 fixes. Instead:

          1. Acquire the lock with AUDIO_CLOSE_LOCK_TIMEOUT. On timeout the
             recording loop is wedged inside a native audio call holding the
             lock -- abandon the stream (poison + leak) and return; do NOT touch
             it, because stop_stream()/close() on a driver-wedged stream would
             block exactly as the read did (post-mortem: the force-kill reset
             the whole Bluetooth stack, so the wedge sat at driver level).
          2. On acquire, DETACH the stream object under the lock and release it,
             THEN run the native close on a throwaway worker (see
             _close_detached_stream). No critical thread ever runs the native
             close itself, so a close that wedges anyway cannot re-create the
             deadlock -- it is bounded by a join and, on timeout, poisoned.
        """
        if self._audio_poisoned:
            return  # already abandoned a wedged pipeline; never wait on it again

        if not self._stream_lock.acquire(timeout=AUDIO_CLOSE_LOCK_TIMEOUT):
            logger.error("Audio stream close timed out -- the audio device is wedged. "
                         "Abandoning the stream; hotkeys stay alive, the next "
                         "recording reinitializes the audio device.")
            self._poison()
            return

        # We hold the lock, so no read is in flight. Detach the stream object and
        # release the lock BEFORE the native close: with the object nulled and
        # stream_is_open False, record_chunk's in-lock guard can never reach this
        # handle again, so the close needs no lock -- and must not hold one.
        try:
            if not self.stream_is_open or self.stream is None:
                logger.debug("Audio stream already closed")
                return
            stream = self.stream
            self.stream = None
            self.stream_is_open = False
        finally:
            self._stream_lock.release()

        self._close_detached_stream(stream)

    def _close_detached_stream(self, stream):
        """Run the native stop_stream()/close() off the lock on a throwaway
        worker, bounded by a join timeout (#128). No critical thread ever runs
        the native close itself: on a driver-wedged stream it would block as long
        as the read did. The worker holds its own reference to the detached
        stream, so on a join timeout we poison and leave it orphaned in the
        wedged native call -- the OS reclaims the handle on process exit."""
        def _worker():
            try:
                logger.info("Closing audio stream...", extra=FILE_ONLY)
                stream.stop_stream()
                stream.close()
                logger.info("Audio stream closed successfully", extra=FILE_ONLY)
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")

        t = threading.Thread(target=_worker, name="AudioClose", daemon=True)
        t.start()
        t.join(timeout=AUDIO_CLOSE_LOCK_TIMEOUT)
        if t.is_alive():
            logger.error("Closing the audio stream is wedged -- the audio device "
                         "stopped responding. Abandoning the stream; the next "
                         "recording reinitializes the audio device.")
            self._poison()

    def _poison(self):
        """Mark the audio pipeline as driver-wedged and unusable (#128).

        Sets flags only -- it deliberately does NOT touch self.stream / self.p,
        because a wedged thread may still be inside a native call on them, and a
        stop/close/terminate on a wedged handle could block just as the read did.
        The handles are leaked on purpose; the OS reclaims them on process exit,
        and the next recording rebuilds the pipeline from scratch (see
        _ensure_pyaudio_ready, which drops a poisoned handle without terminate()).
        Writes only GIL-atomic bools, so it is safe to call while another thread
        holds the stream lock inside a wedged native call."""
        self._audio_poisoned = True
        self.recording = False
        self.stream_is_open = False

    def _reinitialize_stream(self):
        """
        Reinitialize the audio stream (e.g., when device disconnects/reconnects)

        This is only called during an active recording when stream errors occur.

        This path runs inside record_chunk()'s _stream_lock hold and issues the
        same wedge-prone native calls as the open path (stop/close/terminate/
        open), so it shares the driver-wedge risk (#128). It is deliberately NOT
        routed through _open_audio_pipeline_bounded(): if any of these calls
        wedges, the recording loop stays inside the lock, _close_stream() times
        out and poisons (Layer 2), and the next start's bounded open (Layer 3)
        rebuilds the pipeline -- so the wedge is contained without adding a
        second worker-thread hop to this rarely-taken error path.
        """
        logger.info("Attempting to reinitialize audio stream...")

        # Close existing stream if any
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
                self.stream = None
                self.stream_is_open = False
                logger.info("Closed existing stream")
            except Exception as e:
                logger.debug(f"Error closing stream during reinit: {e}")

        # Terminate and reinitialize PyAudio
        if self.p:
            try:
                self.p.terminate()
                logger.info("Terminated PyAudio")
            except Exception as e:
                logger.debug(f"Error terminating PyAudio during reinit: {e}")

        # Small delay to let the system settle
        import time
        time.sleep(0.5)

        try:
            # Reinitialize PyAudio
            self.p = pyaudio.PyAudio()
            logger.info("PyAudio reinitialized")

            # List current audio devices
            info = self.p.get_host_api_info_by_index(0)
            num_devices = info.get('deviceCount')
            logger.info(f"Found audio devices after reinit: {num_devices}")

            # Find default input device
            default_input = None
            try:
                default_input = self.p.get_default_input_device_info()
                logger.info(f"Default input device: {_repair_device_name(default_input.get('name'))}")
            except Exception as e:
                logger.warning(f"Could not get default input device: {e}")

            # Open new audio stream
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            self.stream_is_open = True
            logger.info("Audio stream reopened successfully")

            # Reset error count on successful reinit
            self.stream_error_count = 0
            return True

        except Exception as e:
            logger.error(f"Failed to reinitialize audio stream: {e}", exc_info=True)
            self.stream_is_open = False
            return False

    def _open_audio_pipeline_bounded(self) -> bool:
        """Run PyAudio init + stream open on a worker thread, bounded by a join
        timeout, so a driver-wedged Pa_Initialize/Pa_OpenStream can never hang
        the caller -- the hotkey listener on the Ctrl+Alt+W flow, or the
        recording loop on push-to-talk (#128 Layer 3).

        After a Layer 2 poison the previous start abandoned a wedged driver;
        opening it again may wedge the same way, and on the W-flow that would
        re-create the original all-hotkeys-dead freeze. On timeout the worker is
        leaked (still stuck in the native call), the pipeline is poisoned, and
        the next start rebuilds from scratch. This runs once per recording start,
        not at chunk cadence, so a worker hop is affordable here (unlike the read
        path, which uses non-blocking polling instead)."""
        result = {}

        def _worker():
            try:
                result['ok'] = self._ensure_pyaudio_ready() and self._open_stream()
            except Exception as e:  # never lose the reason
                result['error'] = e

        t = threading.Thread(target=_worker, name="AudioOpen", daemon=True)
        t.start()
        t.join(timeout=AUDIO_OPEN_TIMEOUT_SECONDS)
        if t.is_alive():
            logger.error("Opening the audio device timed out -- the device may be "
                         "wedged. This recording cannot start; the next attempt "
                         "reinitializes it.")
            self._poison()
            return False
        if 'error' in result:
            logger.error(f"Error opening audio pipeline: {result['error']}")
            return False
        return result.get('ok', False)

    def start_recording(self) -> bool:
        """
        Start recording audio

        Opens the audio stream (if not already open) and begins recording.

        Returns:
            True if recording started successfully, False if stream couldn't be opened
        """
        logger.debug(f"start_recording() called - Current state: recording={self.recording}, stream_is_open={self.stream_is_open}")

        # Reinitialize PyAudio to detect the current default device and open the
        # stream -- both bounded by a worker-thread timeout so a driver-wedged
        # init/open can never hang the caller (#128 Layer 3).
        if not self._open_audio_pipeline_bounded():
            logger.error("Failed to open audio pipeline for recording")
            return False

        logger.debug("About to set self.recording = True")
        self.recording = True
        self.frames = []
        self.stream_error_count = 0  # Reset error count for new recording

        # Reset device-loss endgame state and start the crash-safety sidecar
        # writer for the new session (#49). The writer gets the fresh frames
        # list reference; failure to create the sidecar degrades to RAM-only.
        self.recording_aborted = False
        if self.aborted_writer is not None:
            # Pathological timing: an unconsumed device-loss abort is being
            # overridden by a new session. Stop the old writer but keep its
            # .partial on disk -- the next start recovers that audio.
            self.aborted_writer.request_stop()
            self.aborted_writer = None
        # Writer pin cleared BEFORE the frames pin: if the recording loop is
        # mid-consume of a just-raised abort, an empty frames pin must imply
        # an empty writer pin -- otherwise its empty-abort path would
        # discard() the .partial this block just preserved.
        self.aborted_frames = None
        writer = _SidecarWriter(self.frames, self._next_sidecar_path())
        self._sidecar_writer = writer if writer.start() else None

        # Reset drop diagnostic state.
        # _recording_start_time is set on the first successful audio chunk
        # in record_chunk(), NOT here — this excludes PyAudio init and BT
        # warm-up latency from the gap measurement, so a non-zero gap really
        # means audio was lost during recording, not just setup overhead.
        self._recording_start_time = None
        self._silence_chunks_in_row = 0
        self._silence_logged_flag = False

        # Reset Block-1.5 latency diagnostics for the new recording.
        self._read_latency_max = 0.0
        self._read_latency_slow_count = 0
        self._read_latency_slow_total = 0.0
        self._loop_iteration_max = 0.0
        self._loop_iteration_slow_count = 0
        self._loop_iteration_slow_total = 0.0
        self._last_iteration_end_time = None

        logger.info(f"Recording started - self.recording is now {self.recording}, frames cleared, stream_is_open={self.stream_is_open}", extra=FILE_ONLY)
        return True

    def stop_recording(self) -> Tuple[List[bytes], float]:
        """
        Stop recording and return frames and duration

        Closes the audio stream after recording ends.

        Returns:
            Tuple of (frames, duration_in_seconds)
        """
        logger.debug(f"stop_recording() called - Current state: recording={self.recording}, frames={len(self.frames)}, stream_is_open={self.stream_is_open}")
        self.recording = False

        # Let the sidecar writer run its final flush while we wrap up the
        # diagnostics below -- by the time the handle's discard() is called,
        # the writer has usually closed the file already (#49).
        if self._sidecar_writer is not None:
            self._sidecar_writer.request_stop()

        duration = self.get_audio_duration(self.frames)
        logger.info(f"Recording stopped. Duration: {duration:.1f}s, Chunks: {len(self.frames)}", extra=FILE_ONLY)

        # Drop diagnostic: compare wallclock (from first received chunk) vs.
        # audio duration. With the start time anchored to the first chunk,
        # setup/warm-up overhead is excluded — a non-trivial gap now really
        # means audio was lost during the recording (TCP backpressure stalls
        # the recording loop, BT profile switch, etc.).
        if self._recording_start_time is not None:
            wallclock_duration = time.time() - self._recording_start_time
            gap = wallclock_duration - duration
            if gap > 0.3:
                logger.warning(
                    f"Audio gap detected: Wallclock={wallclock_duration:.2f}s "
                    f"(from first chunk), Audio={duration:.2f}s, Gap={gap:.2f}s "
                    f"(audio lost during recording — TCP backpressure or BT issue)"
                )
            else:
                logger.debug(f"Audio/wallclock gap normal: {gap:.3f}s")

        # If a silence run was still ongoing at stop, emit a closing entry
        if self._silence_logged_flag:
            silence_duration_ms = self._silence_chunks_in_row * (CHUNK / RATE) * 1000
            logger.warning(
                f"Audio drop ongoing at recording stop: ~{silence_duration_ms:.0f}ms exact silence"
            )

        # Block 1.5: log per-session read-latency and loop-iteration stats.
        # - Read-latency points at the audio source (PyAudio/BT/driver).
        # - Loop-iteration-gap points at the caller side (send-block, GIL,
        #   scheduling) — overlaps with Block 1's send-latency stats; a
        #   loop-gap that is larger than the send-block sum reveals
        #   non-send-related blocking.
        # Together with Block 1's send-latency stats this triangulates where
        # any audio loss originated.
        if self._read_latency_max > 0:
            logger.info(
                f"Session read-latency stats: "
                f"max={self._read_latency_max*1000:.0f}ms, "
                f"slow-reads(>100ms)={self._read_latency_slow_count}, "
                f"total-slow-read-time={self._read_latency_slow_total:.2f}s",
                extra=FILE_ONLY
            )
        if self._loop_iteration_max > 0:
            logger.info(
                f"Session loop-iteration stats: "
                f"max-gap={self._loop_iteration_max*1000:.0f}ms, "
                f"large-gaps(>50ms)={self._loop_iteration_slow_count}, "
                f"total-large-gap-time={self._loop_iteration_slow_total:.2f}s",
                extra=FILE_ONLY
            )

        # Close stream after recording (releases headset from "headset mode")
        self._close_stream()

        # A deliberate stop overrides a pending device-loss abort (#49): the
        # error path runs entirely under the stream lock, so once
        # _close_stream() has returned no abort can be raised for this
        # session anymore -- clearing here keeps the recording loop from
        # double-processing frames the caller is about to handle normally.
        # Residual race: an abort the loop consumed BEFORE this clear is
        # still processed -- at worst a duplicate archive, never a loss.
        # If the error path already pinned the writer, reclaim it so the
        # normal flow can hand it to the worker as usual.
        self.recording_aborted = False
        self.aborted_frames = None
        if self.aborted_writer is not None:
            self._sidecar_writer = self.aborted_writer
            self.aborted_writer = None

        logger.debug(f"stop_recording() completed - self.recording is now {self.recording}, stream_is_open={self.stream_is_open}")
        return self.frames.copy(), duration

    def cancel_recording(self):
        """
        Cancel current recording

        Closes the audio stream after cancellation.
        """
        self.recording = False
        self.frames = []
        logger.info("Recording cancelled", extra=FILE_ONLY)

        # Close stream after cancellation (releases headset from "headset mode")
        self._close_stream()

        # Cancel means "discard": override any pending device-loss abort
        # (same synchronization argument as in stop_recording) and delete
        # the crash-safety sidecar right away (#49) -- parity with the RAM
        # frames, which a cancel has always dropped for good.
        self.recording_aborted = False
        self.aborted_frames = None
        if self.aborted_writer is not None:
            self._sidecar_writer = self.aborted_writer
            self.aborted_writer = None
        handle = self.take_finished_sidecar()
        if handle is not None:
            handle.discard()

    def _next_sidecar_path(self) -> Path:
        """Build the sidecar path for a new session (#49).

        The recorder stamps its own start timestamp (same format as the
        app's archive timestamps, which are taken at STOP time) with a local
        counter for same-second starts. Collisions with normal archive names
        are structurally impossible: sidecar-derived names always carry the
        .partial / _recovered.mp3 suffix.
        """
        self._sidecar_counter += 1
        ts = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{self._sidecar_counter:03d}"
        return ARCHIVE_FOLDER / f"voice_{ts}_r{RATE}c{CHANNELS}s16le.partial"

    def take_finished_sidecar(self) -> Optional[SidecarHandle]:
        """Hand over the finished session's sidecar handle and clear it (#49).

        Idempotently signals the writer to stop (stop_recording usually has
        already). Single consumer by design -- the stop callbacks, cancel and
        the exit salvage each call it exactly once per session, right after
        stop_recording() on the same thread, so the handle cannot be
        double-assigned. The device-loss path uses take_aborted_sidecar()
        instead, which works on the endgame's pinned writer.
        """
        writer = self._sidecar_writer
        self._sidecar_writer = None
        if writer is None:
            return None
        writer.request_stop()
        return SidecarHandle(path=writer.path, closed_event=writer.closed_event)

    def take_aborted_frames(self) -> List[bytes]:
        """Hand over the frames pinned by the device-loss endgame (#49).

        Returns the dying session's frames list (pinned by record_chunk
        before it raised recording_aborted) and clears the pin, so a new
        session's frames can never be mixed up with the aborted ones.
        """
        frames = self.aborted_frames
        self.aborted_frames = None
        return frames if frames is not None else []

    def take_aborted_sidecar(self) -> Optional[SidecarHandle]:
        """Hand over the sidecar handle pinned by the device-loss endgame (#49).

        Counterpart of take_aborted_frames for the writer: record_chunk()
        pins the dying session's writer alongside the frames, so the abort
        handler can never grab the writer of a NEW session the user has
        already started. Stops the pinned writer -- on this path nothing
        else does.
        """
        writer = self.aborted_writer
        self.aborted_writer = None
        if writer is None:
            return None
        writer.request_stop()
        return SidecarHandle(path=writer.path, closed_event=writer.closed_event)

    def record_chunk(self) -> bool:
        """
        Record a single chunk of audio

        Returns:
            True if recording should continue, False otherwise
        """
        if not self.recording:
            logger.debug(f"record_chunk() called but self.recording is FALSE (stream_is_open: {self.stream_is_open})")
            return False

        # Drop diagnostic (Block 1.5): measure the loop-iteration gap — the
        # wallclock time since the previous record_chunk() return. This catches
        # blocks OUTSIDE the read (slow sends, GIL contention, scheduling).
        iteration_start = time.perf_counter()
        if self._last_iteration_end_time is not None:
            iteration_gap = iteration_start - self._last_iteration_end_time
            if iteration_gap > self._loop_iteration_max:
                self._loop_iteration_max = iteration_gap
            if iteration_gap > 0.05:  # > 50 ms (nominal ~33 ms)
                self._loop_iteration_slow_count += 1
                self._loop_iteration_slow_total += iteration_gap
                logger.debug(
                    f"Recording-loop iteration gap: {iteration_gap*1000:.0f}ms "
                    f"(time outside stream.read — likely send-block or scheduling)"
                )

        # Layer 1 (#128): the capture read must never block unbounded inside
        # _stream_lock. A stalled device used to pin the lock in a blocking
        # stream.read(), so the next stop/cancel/exit deadlocked behind it in
        # _close_stream() -- the whole-app freeze this fixes. Instead: poll
        # get_read_available() with the lock RELEASED between polls and only
        # issue the read once a full CHUNK is buffered, so it returns at once
        # and the lock is never held across a wait. _close_stream can then slip
        # in between polls and close cleanly. No full chunk within
        # AUDIO_STALL_TIMEOUT_SECONDS is treated as device loss and handed to
        # the #49 endgame (Layer 1b, _begin_stall_abort).
        #
        # read_start marks the start of the WAIT for this chunk; on the first
        # chunk it includes the BT/PyAudio warm-up (typically 0.5-1.0 s on the
        # Jabra, HFP profile switch), which is why the read-latency stats below
        # are anchored to _recording_start_time (None on the first read, set
        # just after it) so the first chunk is measured but excluded from stats.
        read_start = time.perf_counter()
        deadline = read_start + AUDIO_STALL_TIMEOUT_SECONDS
        data = None
        while data is None:
            with self._stream_lock:
                # Double-check the stream is still there: _close_stream may have
                # detached it between polls (or a concurrent Layer 2 close is
                # tearing it down).
                if not self.stream_is_open or self.stream is None:
                    if self.recording:
                        # Stream vanished while the session is still live -- treat
                        # it like the reinit-failure endgame below instead of
                        # leaving a zombie "recording" state (#49 layer 4). With
                        # recording already False this is just the normal race
                        # with a deliberate stop/cancel, not an abort.
                        logger.error("Audio stream closed unexpectedly during recording")
                        self.recording = False
                        self.aborted_frames = self.frames
                        self.aborted_writer = self._sidecar_writer
                        self._sidecar_writer = None
                        self.recording_aborted = True
                    else:
                        logger.debug(f"Stream closed during recording, stopping chunk recording")
                    return False

                try:
                    # Only read once a full CHUNK is buffered, so the read returns
                    # immediately -- it can no longer block the lock. get_read_available()
                    # is wrapped in the SAME try as read(): a poisoned/torn-down stream
                    # (a concurrent Layer 2 close) makes either call raise, and the
                    # handler below degrades gracefully instead of crashing the loop.
                    if self.stream.get_read_available() >= CHUNK:
                        data = self.stream.read(CHUNK, exception_on_overflow=False)
                except Exception as e:
                    error_str = str(e)
                    logger.error(f"Error reading audio stream: {e}")

                    # Check for specific stream errors that indicate device disconnection
                    # Note: Error codes may vary between Windows and Mac
                    # -9999 and -9988 are common PortAudio error codes
                    if "-9999" in error_str or "-9988" in error_str or "Input overflowed" in error_str:
                        self.stream_error_count += 1

                        # Try to reinitialize after several errors
                        if self.stream_error_count >= self.max_stream_errors:
                            logger.warning(f"Stream error count reached {self.stream_error_count}, attempting to reinitialize...")

                            # Stop recording temporarily
                            was_recording = self.recording
                            self.recording = False

                            # Try to reinitialize
                            if self._reinitialize_stream():
                                # Poison discipline (#128): a concurrent stop/cancel
                                # can abandon this session while the >2s reinit holds
                                # _stream_lock -- its _close_stream() runs into the
                                # lock-acquire timeout and poisons. The stop won (the
                                # user asked to stop), so the abandoned session belongs
                                # to that close: do NOT resurrect recording. Close the
                                # stream we just opened so the mic turns off -- it
                                # opened cleanly just now, so it is not driver-wedged
                                # and _close_detached_stream drains it on a bounded
                                # worker (leaking it would leave the headset live until
                                # the next start). Not via _close_stream(): that bails
                                # on the poison flag and would leak the handle.
                                if self._audio_poisoned:
                                    logger.warning("Reinit succeeded but a concurrent stop/cancel "
                                                   "poisoned the pipeline; honoring the stop instead "
                                                   "of resuming recording")
                                    self.recording = False
                                    stream = self.stream
                                    self.stream = None
                                    self.stream_is_open = False
                                    if stream is not None:
                                        self._close_detached_stream(stream)
                                    return False
                                logger.info("Stream reinitialized successfully, resuming recording")
                                self.recording = was_recording
                                # Don't return False, let it try again next cycle
                                return True
                            else:
                                logger.error("Failed to reinitialize stream, stopping recording")
                                # Device-loss endgame (#49 layer 4): pin this
                                # session's frames and sidecar writer for the
                                # recording loop's abort handler, THEN raise the
                                # flag (the handler must find them pinned). The
                                # writer keeps running until the handler stops it
                                # via take_aborted_sidecar(), so it can still
                                # flush RAM remains. Skipped when was_recording
                                # is False: a stop/cancel hotkey already ended
                                # the session and owns frames and writer.
                                if was_recording:
                                    self.aborted_frames = self.frames
                                    self.aborted_writer = self._sidecar_writer
                                    self._sidecar_writer = None
                                    self.recording_aborted = True
                                return False

                    # For other errors or if we haven't hit the error threshold yet
                    return True  # Keep trying

            # Lock RELEASED here between polls -> _close_stream can close cleanly.
            if data is None:
                if time.perf_counter() >= deadline:
                    return self._begin_stall_abort()  # Layer 1b: stall watchdog
                time.sleep(AUDIO_READ_POLL_SECONDS)

        # A full chunk was obtained (read returned at once). read_start->now is
        # the wait for this chunk, which is what "slow read" always measured.
        read_elapsed = time.perf_counter() - read_start
        if self._recording_start_time is not None:
            if read_elapsed > self._read_latency_max:
                self._read_latency_max = read_elapsed
            if read_elapsed > 0.1:  # > 100 ms (nominal ~64 ms)
                self._read_latency_slow_count += 1
                self._read_latency_slow_total += read_elapsed
                logger.debug(
                    f"Slow PyAudio read: {read_elapsed*1000:.0f}ms "
                    f"(audio source did not deliver — BT/driver/overflow)"
                )

        self.frames.append(data)

        # Mark the actual recording start on the first received chunk.
        # This excludes PyAudio init + BT warm-up from the gap metric
        # so that a non-zero gap is a clear sign of mid-recording loss.
        if self._recording_start_time is None:
            self._recording_start_time = time.time()

        # Reset error count on successful read
        if self.stream_error_count > 0:
            self.stream_error_count = 0
            logger.info("Audio stream recovered, reset error count")

        # Drop diagnostic: detect runs of exact-silence chunks.
        # A normal microphone produces small but non-zero noise floor;
        # exact all-zero samples mean PyAudio could not fetch fresh
        # data (recording-loop stall, BT profile switch, etc.).
        try:
            samples = np.frombuffer(data, dtype=np.int16)
            if samples.size > 0 and not np.any(samples):
                self._silence_chunks_in_row += 1
                silence_ms = self._silence_chunks_in_row * (CHUNK / RATE) * 1000
                if silence_ms >= 200 and not self._silence_logged_flag:
                    logger.warning(
                        f"Audio drop detected: exact silence ongoing >= {silence_ms:.0f}ms "
                        f"(possible mic stall or BT issue)"
                    )
                    self._silence_logged_flag = True
            else:
                if self._silence_logged_flag:
                    silence_ms = self._silence_chunks_in_row * (CHUNK / RATE) * 1000
                    logger.warning(
                        f"Audio drop ended: total exact-silence duration ~{silence_ms:.0f}ms"
                    )
                self._silence_chunks_in_row = 0
                self._silence_logged_flag = False
        except Exception as drop_diag_err:
            # Diagnostic must never break the recording loop
            logger.debug(f"Drop diagnostic error (non-fatal): {drop_diag_err}")

        # Block 1.5: mark iteration end-time for the next iteration's
        # gap measurement (set only on success — on failure paths we
        # want the next successful read to see the full elapsed time).
        self._last_iteration_end_time = time.perf_counter()

        return True

    def _begin_stall_abort(self) -> bool:
        """Stall watchdog fired (#128 Layer 1b): no full CHUNK became available
        within AUDIO_STALL_TIMEOUT_SECONDS. Hand the dying session to the
        recording loop's #49 device-loss endgame exactly like a failed reinit
        does (pin frames + sidecar writer, THEN raise recording_aborted), then
        stop this chunk.

        Only arms the endgame while the session is still live: a stop/cancel
        that already set recording=False owns the frames and writer, so the
        watchdog must not re-capture them -- the same discipline as the
        vanished-stream guard above and the was_recording gate on the reinit
        path. A stop/cancel that wins the race after this check costs at worst a
        duplicate archive (never a loss), the residual race stop_recording()
        already documents."""
        if not self.recording:
            return False
        logger.error(f"Audio stalled -- no frames for {AUDIO_STALL_TIMEOUT_SECONDS:.0f}s; "
                     f"ending the recording (the audio device stopped delivering).")
        self.recording = False
        self.aborted_frames = self.frames          # pin BEFORE raising the flag
        self.aborted_writer = self._sidecar_writer
        self._sidecar_writer = None
        # Release the still-open stalled stream so the microphone turns off --
        # every other recording-end path closes the stream, a stall must not
        # leak it open. Unlike the reinit-failure endgame (where
        # _reinitialize_stream already closed the stream), the watchdog fires
        # with the stream still open. Safe on this (recording-loop) thread: the
        # lock is free here (we are past the poll's `with`), and _close_stream
        # runs the native close on a bounded worker, poisoning if it wedges at
        # driver level -- so this never re-creates the deadlock.
        self._close_stream()
        self.recording_aborted = True
        return False

    def get_audio_duration(self, frames: List[bytes]) -> float:
        """
        Calculate audio duration in seconds

        Args:
            frames: List of audio frame bytes

        Returns:
            Duration in seconds
        """
        if not frames:
            return 0.0

        total_frames = len(b''.join(frames))
        bytes_per_sample = pyaudio.get_sample_size(pyaudio.paInt16) * CHANNELS
        total_samples = total_frames / bytes_per_sample
        duration = total_samples / RATE
        return duration

    def _preprocess_audio_frames(self, frames: List[bytes], trim_end: bool = True) -> List[bytes]:
        """
        Preprocess audio frames before saving:
        1. Trim the last N milliseconds (removes hotkey click sounds), unless
           trim_end is False -- for recordings no stop hotkey ended (#49)
        2. Add silence padding at the end (helps API detect end of speech)

        This reduces transcription hallucinations caused by keyboard click sounds
        at the end of recordings.

        Args:
            frames: Raw audio frame bytes from recording
            trim_end: Whether to trim AUDIO_TRIM_END_MS from the end

        Returns:
            Preprocessed audio frames as a single-element list containing all bytes
        """
        if not frames:
            return frames

        try:
            # Convert bytes to numpy array (16-bit signed integers, mono)
            audio_data = np.frombuffer(b''.join(frames), dtype=np.int16)
            original_samples = len(audio_data)
            original_duration_ms = (original_samples / RATE) * 1000

            # Calculate samples to trim and add
            trim_samples = int(RATE * AUDIO_TRIM_END_MS / 1000)
            silence_samples = int(RATE * AUDIO_SILENCE_PADDING_MS / 1000)

            # Only trim if we have enough audio (keep at least 500ms)
            min_samples_to_keep = int(RATE * 0.5)  # 500ms minimum
            if not trim_end:
                logger.debug("End trim skipped (recording ended without a stop hotkey)")
            elif len(audio_data) > trim_samples + min_samples_to_keep:
                audio_data = audio_data[:-trim_samples]
                logger.debug(f"Trimmed {AUDIO_TRIM_END_MS}ms ({trim_samples} samples) from end")
            else:
                logger.debug(f"Audio too short ({original_duration_ms:.0f}ms), skipping trim")

            # Add silence padding at the end
            silence = np.zeros(silence_samples, dtype=np.int16)
            audio_data = np.concatenate([audio_data, silence])
            logger.debug(f"Added {AUDIO_SILENCE_PADDING_MS}ms silence padding ({silence_samples} samples)")

            # Log preprocessing summary
            final_samples = len(audio_data)
            final_duration_ms = (final_samples / RATE) * 1000
            trim_note = f"trimmed {AUDIO_TRIM_END_MS}ms" if trim_end else "no end trim"
            logger.info(f"Audio preprocessed: {original_duration_ms:.0f}ms -> {final_duration_ms:.0f}ms "
                       f"({trim_note}, added {AUDIO_SILENCE_PADDING_MS}ms silence)", extra=FILE_ONLY)

            # Convert back to bytes and return as single-element list
            return [audio_data.tobytes()]

        except Exception as e:
            logger.error(f"Error preprocessing audio: {e}", exc_info=True)
            # Return original frames on error
            return frames

    def save_recording(self, frames: List[bytes], timestamp: str,
                       trim_end: bool = True) -> Tuple[str, str]:
        """
        Save recording to WAV and MP3 files

        Args:
            frames: Audio frames to save
            timestamp: Timestamp for filename
            trim_end: Trim the stop-hotkey click from the tail. False only for
                the device-loss salvage -- no hotkey ended that recording, so
                the last AUDIO_TRIM_END_MS are real dictation (#49).

        Returns:
            Tuple of (wav_path, mp3_path)
        """
        # Preprocess audio (trim end, add silence)
        processed_frames = self._preprocess_audio_frames(frames, trim_end=trim_end)

        # Create WAV file
        wav_filename = f"output_{timestamp}.wav"
        wav_path = Path(wav_filename)

        wf = wave.open(str(wav_path), 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(pyaudio.get_sample_size(pyaudio.paInt16))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(processed_frames))
        wf.close()
        logger.info(f"WAV file created: {wav_filename}", extra=FILE_ONLY)

        # Convert to MP3
        data, samplerate = sf.read(str(wav_path))
        mp3_filename = wav_filename.replace('.wav', '.mp3')
        mp3_path = Path(mp3_filename)
        sf.write(str(mp3_path), data, samplerate, format='mp3')
        logger.info(f"MP3 file created: {mp3_filename}", extra=FILE_ONLY)

        # Archive MP3
        archive_path = ARCHIVE_FOLDER / f"voice_{timestamp}.mp3"
        sf.write(str(archive_path), data, samplerate, format='mp3')
        logger.info(f"Archived as: {archive_path}", extra=FILE_ONLY)

        return str(wav_path), str(mp3_path)

    def tag_archive_with_engine(self, timestamp: str, engine: str) -> Optional[str]:
        """Rename the archived recording to carry the producing-engine token (#62).

        Called from the worker after a transcript is saved, so the archived
        voice_<ts>.mp3 becomes voice_<ts>_<engine>.mp3 and mirrors the token the
        transcript file already carries. Same-volume rename, so atomic on NTFS.

        Best-effort and never raises (stability is principle #1): an empty code,
        a missing source, or a locked file (e.g. an AV scanner still holding the
        just-written MP3) leaves the bare voice_<ts>.mp3 in place and only warns.
        The audio<->transcript pairing keys off the shared timestamp -- which the
        transcript carries too -- so a failed rename degrades only the audio name,
        never the pairing or the Ctrl+Alt+R retry slot.
        """
        if not engine:
            return None
        src = ARCHIVE_FOLDER / f"voice_{timestamp}.mp3"
        dst = ARCHIVE_FOLDER / f"voice_{timestamp}_{engine}.mp3"
        try:
            if not src.exists():
                logger.warning(f"Cannot tag archive -- source missing: {src}")
                return None
            src.rename(dst)
            logger.info(f"Archived recording tagged with engine: {dst.name}", extra=FILE_ONLY)
            return str(dst)
        except Exception as e:
            logger.warning(f"Could not tag archive {src.name} with engine '{engine}': {e}")
            return None

    def tag_recovered_archive_with_engine(self, recovered_path: str, engine: str) -> Optional[str]:
        """Tag a kill-recovered archive with the producing-engine token (#98).

        Path-based sibling of tag_archive_with_engine for the _recovered form
        (recover_partial_files). The actual file is voice_<ts>_recovered.mp3 (or
        _recovered_<n> on a double recovery) -- a name the timestamp-based tagger
        cannot reconstruct. Inserts the engine token right before the _recovered
        marker, so the result parallels a normally tagged file
        (voice_<ts>_<engine>_recovered.mp3) while keeping the recovery provenance
        readable. Same-volume rename, so atomic on NTFS.

        Best-effort and never raises (stability is principle #1): an empty code, a
        name that is not a _recovered form, a missing source, or a locked file
        (e.g. an AV scanner still holding the just-written MP3) leaves the file in
        place and only warns. The audio<->transcript pairing keys off the shared
        <ts> -- unchanged by this rename -- so a failed rename degrades only the
        audio name, never the pairing or the Ctrl+Alt+R retry slot.
        """
        if not engine:
            return None
        src = Path(recovered_path)
        m = _RECOVERED_ARCHIVE_RE.match(src.name)
        if not m:
            logger.warning(f"Cannot tag recovered archive -- unexpected name: {src.name}")
            return None
        dst = src.with_name(f"{m['base']}_{engine}_recovered{m['disamb'] or ''}.mp3")
        try:
            if not src.exists():
                logger.warning(f"Cannot tag recovered archive -- source missing: {src}")
                return None
            src.rename(dst)
            logger.info(f"Recovered recording tagged with engine: {dst.name}", extra=FILE_ONLY)
            return str(dst)
        except Exception as e:
            logger.warning(f"Could not tag recovered archive {src.name} with engine '{engine}': {e}")
            return None

    def cleanup_temp_files(self, wav_path: str, mp3_path: str):
        """Remove temporary audio files"""
        for path in [wav_path, mp3_path]:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.debug(f"Temporary file deleted: {path}")
            except Exception as e:
                logger.warning(f"Could not delete {path}: {e}")

    def cleanup(self):
        """Clean up audio resources"""
        logger.info("Cleaning up audio resources...", extra=FILE_ONLY)

        # A poisoned pipeline (#128) wedged at driver level: closing or
        # terminating it here could block the exit exactly as the read did.
        # Leave the handles untouched -- the OS reclaims them on process exit.
        if self._audio_poisoned:
            logger.info("Audio pipeline was abandoned (device wedged); skipping close/terminate.",
                        extra=FILE_ONLY)
            return

        # Close stream if still open
        if self.stream_is_open:
            self._close_stream()

        # Terminate PyAudio
        if self.p:
            try:
                self.p.terminate()
                logger.info("PyAudio terminated", extra=FILE_ONLY)
            except Exception as e:
                logger.error(f"Error terminating PyAudio: {e}")

    @property
    def is_recording(self) -> bool:
        """Check if currently recording"""
        return self.recording
