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
import time
import wave
import logging
import datetime
import threading
import pyaudio
import soundfile as sf
import numpy as np
from pathlib import Path
from typing import List, Tuple, Optional

from config import (
    CHUNK, FORMAT, CHANNELS, RATE,
    ARCHIVE_FOLDER, SCRIPT_DIR,
    AUDIO_TRIM_END_MS, AUDIO_SILENCE_PADDING_MS
)

logger = logging.getLogger('Thoughtborne.AudioHandler')


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
        self.last_device_index = None  # Track which device was used

        # Drop diagnostic: wallclock start time of current recording (set in start_recording)
        self._recording_start_time = None

        # Drop diagnostic: track consecutive exact-silence chunks to detect mic drops
        # (PyAudio returns all-zero samples when the audio device stalls, e.g. BT
        # profile switch or recording-loop stall on blocking _ws.send)
        self._silence_chunks_in_row = 0
        self._silence_logged_flag = False

        # NOTE: PyAudio is NOT initialized here anymore!
        # It will be initialized on-demand when recording starts (see _ensure_pyaudio_ready())
        self._ensure_directories()

    def _ensure_directories(self):
        """Create archive directories if they don't exist"""
        ARCHIVE_FOLDER.mkdir(exist_ok=True)
        logger.info(f"Archive folder ready: {ARCHIVE_FOLDER}")

    def _ensure_pyaudio_ready(self) -> bool:
        """
        Ensure PyAudio is initialized with current default device

        This method reinitializes PyAudio before each recording to detect
        device changes (e.g., headset connected/disconnected).

        Returns:
            True if successful, False otherwise
        """
        try:
            # Terminate old PyAudio instance if exists
            if self.p is not None:
                try:
                    self.p.terminate()
                    logger.debug("Old PyAudio instance terminated")
                except Exception as e:
                    logger.warning(f"Error terminating old PyAudio: {e}")

            # Initialize new PyAudio instance
            self.p = pyaudio.PyAudio()
            logger.info("PyAudio reinitialized to detect current default device")

            # Get and log current default input device
            try:
                default_device = self.p.get_default_input_device_info()
                device_name = default_device.get('name', 'Unknown')
                device_index = default_device.get('index', -1)

                # Check if device changed
                if self.last_device_index is not None and self.last_device_index != device_index:
                    logger.info(f"Audio device changed! Now using: [{device_index}] {device_name}")
                    print(f"Audio device changed to: {device_name}")
                else:
                    logger.info(f"Using audio input device: [{device_index}] {device_name}")

                self.last_device_index = device_index

            except Exception as e:
                logger.warning(f"Could not get default input device info: {e}")

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
            logger.info("Opening audio stream...")
            self.stream = self.p.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            self.stream_is_open = True
            logger.info("Audio stream opened successfully")
            return True
        except Exception as e:
            logger.error(f"Error opening audio stream: {e}", exc_info=True)
            return False

    def _close_stream(self):
        """
        Close the audio stream

        This is called after recording ends (stop or cancel).
        This releases the headset from "headset mode".

        Uses a lock to prevent race conditions with concurrent stream reads.
        """
        with self._stream_lock:
            if not self.stream_is_open:
                logger.debug("Audio stream already closed")
                return

            if self.stream:
                try:
                    logger.info("Closing audio stream...")
                    self.stream.stop_stream()
                    self.stream.close()
                    self.stream = None
                    self.stream_is_open = False
                    logger.info("Audio stream closed successfully")
                except Exception as e:
                    logger.error(f"Error closing audio stream: {e}")

    def _reinitialize_stream(self):
        """
        Reinitialize the audio stream (e.g., when device disconnects/reconnects)

        This is only called during an active recording when stream errors occur.
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
                logger.info(f"Default input device: {default_input.get('name')}")
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

    def start_recording(self) -> bool:
        """
        Start recording audio

        Opens the audio stream (if not already open) and begins recording.

        Returns:
            True if recording started successfully, False if stream couldn't be opened
        """
        logger.debug(f"start_recording() called - Current state: recording={self.recording}, stream_is_open={self.stream_is_open}")

        # Reinitialize PyAudio to detect current default device (e.g., headset connected/disconnected)
        if not self._ensure_pyaudio_ready():
            logger.error("Failed to initialize PyAudio for recording")
            return False

        # Open stream if not already open (lazy initialization)
        if not self._open_stream():
            logger.error("Failed to open audio stream for recording")
            return False

        logger.debug("About to set self.recording = True")
        self.recording = True
        self.frames = []
        self.stream_error_count = 0  # Reset error count for new recording

        # Reset drop diagnostic state.
        # _recording_start_time is set on the first successful audio chunk
        # in record_chunk(), NOT here — this excludes PyAudio init and BT
        # warm-up latency from the gap measurement, so a non-zero gap really
        # means audio was lost during recording, not just setup overhead.
        self._recording_start_time = None
        self._silence_chunks_in_row = 0
        self._silence_logged_flag = False

        logger.info(f"Recording started - self.recording is now {self.recording}, frames cleared, stream_is_open={self.stream_is_open}")
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
        duration = self.get_audio_duration(self.frames)
        logger.info(f"Recording stopped. Duration: {duration:.1f}s, Chunks: {len(self.frames)}")

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

        # Close stream after recording (releases headset from "headset mode")
        self._close_stream()

        logger.debug(f"stop_recording() completed - self.recording is now {self.recording}, stream_is_open={self.stream_is_open}")
        return self.frames.copy(), duration

    def cancel_recording(self):
        """
        Cancel current recording

        Closes the audio stream after cancellation.
        """
        self.recording = False
        self.frames = []
        logger.info("Recording cancelled")

        # Close stream after cancellation (releases headset from "headset mode")
        self._close_stream()

    def record_chunk(self) -> bool:
        """
        Record a single chunk of audio

        Returns:
            True if recording should continue, False otherwise
        """
        if not self.recording:
            logger.debug(f"record_chunk() called but self.recording is FALSE (stream_is_open: {self.stream_is_open})")
            return False

        # Use lock to prevent race condition with _close_stream()
        # This ensures the stream can't be closed while we're reading from it
        with self._stream_lock:
            # Double-check stream is still open (could have been closed by another thread)
            if not self.stream_is_open or self.stream is None:
                logger.debug(f"Stream closed during recording, stopping chunk recording")
                return False

            try:
                data = self.stream.read(CHUNK, exception_on_overflow=False)
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

                return True
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
                            logger.info("Stream reinitialized successfully, resuming recording")
                            self.recording = was_recording
                            # Don't return False, let it try again next cycle
                            return True
                        else:
                            logger.error("Failed to reinitialize stream, stopping recording")
                            return False

                # For other errors or if we haven't hit the error threshold yet
                return True  # Keep trying

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
        bytes_per_sample = self.p.get_sample_size(pyaudio.paInt16) * CHANNELS
        total_samples = total_frames / bytes_per_sample
        duration = total_samples / RATE
        return duration

    def _preprocess_audio_frames(self, frames: List[bytes]) -> List[bytes]:
        """
        Preprocess audio frames before saving:
        1. Trim the last N milliseconds (removes hotkey click sounds)
        2. Add silence padding at the end (helps API detect end of speech)

        This reduces transcription hallucinations caused by keyboard click sounds
        at the end of recordings.

        Args:
            frames: Raw audio frame bytes from recording

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
            if len(audio_data) > trim_samples + min_samples_to_keep:
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
            logger.info(f"Audio preprocessed: {original_duration_ms:.0f}ms -> {final_duration_ms:.0f}ms "
                       f"(trimmed {AUDIO_TRIM_END_MS}ms, added {AUDIO_SILENCE_PADDING_MS}ms silence)")

            # Convert back to bytes and return as single-element list
            return [audio_data.tobytes()]

        except Exception as e:
            logger.error(f"Error preprocessing audio: {e}", exc_info=True)
            # Return original frames on error
            return frames

    def save_recording(self, frames: List[bytes], timestamp: str) -> Tuple[str, str]:
        """
        Save recording to WAV and MP3 files

        Args:
            frames: Audio frames to save
            timestamp: Timestamp for filename

        Returns:
            Tuple of (wav_path, mp3_path)
        """
        # Preprocess audio (trim end, add silence)
        processed_frames = self._preprocess_audio_frames(frames)

        # Create WAV file
        wav_filename = f"output_{timestamp}.wav"
        wav_path = Path(wav_filename)

        wf = wave.open(str(wav_path), 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(self.p.get_sample_size(pyaudio.paInt16))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(processed_frames))
        wf.close()
        logger.info(f"WAV file created: {wav_filename}")

        # Convert to MP3
        data, samplerate = sf.read(str(wav_path))
        mp3_filename = wav_filename.replace('.wav', '.mp3')
        mp3_path = Path(mp3_filename)
        sf.write(str(mp3_path), data, samplerate, format='mp3')
        logger.info(f"MP3 file created: {mp3_filename}")

        # Archive MP3
        archive_path = ARCHIVE_FOLDER / f"voice_{timestamp}.mp3"
        sf.write(str(archive_path), data, samplerate, format='mp3')
        logger.info(f"Archived as: {archive_path}")

        return str(wav_path), str(mp3_path)

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
        logger.info("Cleaning up audio resources...")

        # Close stream if still open
        if self.stream_is_open:
            self._close_stream()

        # Terminate PyAudio
        if self.p:
            try:
                self.p.terminate()
                logger.info("PyAudio terminated")
            except Exception as e:
                logger.error(f"Error terminating PyAudio: {e}")

    @property
    def is_recording(self) -> bool:
        """Check if currently recording"""
        return self.recording
