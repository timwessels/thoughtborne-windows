"""
Transcriber Module

This module handles speech-to-text transcription using multiple APIs.
It provides a clean interface for transcribing audio files using
GROQ (Whisper Large V3 Turbo) and Soniox (V2 sync, V4 async, Live streaming).

Classes:
    AbstractTranscriber: Base class for all transcriber implementations
    GroqTranscriber: Handles transcription using GROQ API
    SonioxTranscriber: Handles transcription using Soniox API
"""

import os
import sys
import time
import logging
import threading
import queue
from abc import ABC, abstractmethod
from typing import Optional
from pathlib import Path

from groq import Groq, AuthenticationError

from config import (
    GROQ_MODEL, LANGUAGE, TEXT_ARCHIVE_FOLDER,
    GROQ_API_KEY, SONIOX_API_KEY, SONIOX_MODEL,
    SHORT_AUDIO_THRESHOLD,
    SONIOX_V4_API_BASE, SONIOX_V4_MODEL, SONIOX_V4_POLL_INTERVAL,
    SONIOX_V4_MAX_POLL_ATTEMPTS,
    SONIOX_WS_URL, SONIOX_RT_MODEL, SONIOX_LIVE_FINALIZE_DELAY,
    SONIOX_LIVE_FINALIZE_TIMEOUT,
    SONIOX_LIVE_QUEUE_MAX_CHUNKS, SONIOX_LIVE_SENDER_JOIN_TIMEOUT,
    SONIOX_LIVE_FINALIZE_DRAIN_TIMEOUT,
    SONIOX_LANGUAGE_HINTS, SONIOX_CONTEXT,
    RATE, CHANNELS,
)

logger = logging.getLogger('Thoughtborne.Transcriber')


class AbstractTranscriber(ABC):
    """Abstract base class for all transcriber implementations"""
    
    def __init__(self):
        """Initialize the transcriber"""
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Create text archive directory if it doesn't exist"""
        TEXT_ARCHIVE_FOLDER.mkdir(exist_ok=True)
        logger.info(f"Text archive folder ready: {TEXT_ARCHIVE_FOLDER}")

    def _report_auth_failure(self, env_var: str, detail: str = "") -> None:
        """Emit one uniform, actionable console line for a rejected API key.

        The full stack trace is logged separately by the caller; this is the
        short, human-facing line. It goes through logger.error so it rides the
        same non-blocking console path as the rest of the module (no raw print
        from worker or receiver threads).
        """
        suffix = f" ({detail})" if detail else ""
        logger.error(
            f"[AUTH] {self.get_name()}: API key rejected{suffix}. "
            f"Check {env_var} in .env, then restart Thoughtborne."
        )

    @property
    def is_live(self) -> bool:
        """Whether this transcriber supports live audio streaming.

        Returns True if the transcriber needs audio chunks sent during recording
        rather than receiving a complete file after recording.
        """
        return False

    @abstractmethod
    def transcribe(self, audio_file_path: str, duration_seconds: float) -> str:
        """
        Transcribe an audio file
        
        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds
            
        Returns:
            Transcribed text
        """
        pass
    
    @abstractmethod
    def test_transcription(self, test_file_path: str) -> Optional[str]:
        """
        Test transcription with a specific file
        
        Args:
            test_file_path: Path to test audio file
            
        Returns:
            Transcribed text or None if failed
        """
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Get the name of this transcriber"""
        pass
    
    def save_transcript(self, text: str, timestamp: str) -> Optional[str]:
        """
        Save transcript to text archive
        
        Args:
            text: Transcribed text to save
            timestamp: Timestamp for filename
            
        Returns:
            Path to saved file or None if failed
        """
        if not text:
            return None
            
        try:
            filename = TEXT_ARCHIVE_FOLDER / f"text_{timestamp}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(text)
            
            logger.info(f"Text archived: {filename} ({len(text)} chars)")
            return str(filename)
            
        except Exception as e:
            logger.error(f"Failed to save transcript: {e}")
            return None


class GroqTranscriber(AbstractTranscriber):
    """Handles transcription using GROQ Whisper API"""
    
    def __init__(self):
        """Initialize the transcriber with API key"""
        super().__init__()
        self.api_key = self._get_api_key()
        self.client = None
        self._initialize_client()
    
    def _get_api_key(self) -> str:
        """Get API key from environment"""
        if not GROQ_API_KEY:
            logger.error("GROQ_API_KEY not found in environment variables!")
            raise ValueError("GROQ_API_KEY is required for GROQ transcriber")

        logger.info("Using GROQ API key from environment")
        return GROQ_API_KEY
    
    def _initialize_client(self):
        """Initialize GROQ client"""
        try:
            self.client = Groq(api_key=self.api_key)
            logger.info("GROQ client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize GROQ client: {e}")
            raise
    
    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return "GROQ (schnell)"
    
    def _clean_groq_hallucinations(self, transcript: str) -> str:
        """
        Remove common hallucination patterns from GROQ transcriptions

        GROQ (Whisper v3 Turbo) often adds "Vielen Dank" or similar phrases
        at the end of German transcriptions without context.
        Also removes incomplete word fragments similar to Soniox.
        """
        if not transcript:
            return transcript

        original = transcript

        # Phrase hallucinations (longer patterns, checked with rstrip)
        phrase_patterns = [
            "Vielen Dank für Ihre Aufmerksamkeit.",
            "Vielen Dank für Ihre Aufmerksamkeit",
            "Vielen Dank.",
            "Vielen Dank",
            "Danke.",
            "Danke",
            "Ich danke Ihnen.",
            "Ich danke Ihnen",
        ]

        # Check phrase patterns
        for pattern in phrase_patterns:
            if transcript.rstrip().endswith(pattern):
                cleaned = transcript.rstrip()[:-len(pattern)].rstrip()
                if cleaned and len(cleaned) > 10:
                    logger.debug(f"Removed GROQ hallucination: '{pattern}' at end")
                    logger.debug(f"Original ending: ...'{original[-50:] if len(original) > 50 else original}'")
                    logger.debug(f"Cleaned to: ...'{cleaned[-50:] if len(cleaned) > 50 else cleaned}'")
                    return cleaned

        # Word fragment hallucinations (with leading space)
        fragment_patterns = [
            " und",
            " Und",
            " Das",
            " In",
            " Also",
            " Für",
            " Oder",
        ]

        for pattern in fragment_patterns:
            if transcript.endswith(pattern):
                transcript = transcript[:-len(pattern)]
                logger.debug(f"Removed GROQ hallucination: '{pattern}' at end")
                break

        if transcript != original:
            logger.debug(f"Original ending: ...'{original[-30:]}'")
            logger.debug(f"Cleaned to: ...'{transcript[-30:]}'")

        return transcript
    
    def transcribe(self, audio_file_path: str, duration_seconds: float) -> str:
        """
        Transcribe an audio file using GROQ Whisper API
        
        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds
            
        Returns:
            Transcribed text
        """
        logger.info(f"Starting GROQ transcription: {audio_file_path} (Duration: {duration_seconds:.1f}s)")
        
        try:
            start_time = time.time()
            
            # Open and transcribe audio file
            with open(audio_file_path, "rb") as audio_file:
                transcription = self.client.audio.transcriptions.create(
                    file=audio_file,
                    model=GROQ_MODEL,
                    language=LANGUAGE,
                    response_format="text"
                )
            
            elapsed = time.time() - start_time
            logger.info(f"GROQ transcription successful in {elapsed:.2f}s")
            
            # GROQ returns text directly when response_format="text"
            text = transcription.strip()
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")
            
            # Clean hallucinations
            text = self._clean_groq_hallucinations(text)
            
            return text
            
        except AuthenticationError as e:
            # body['code'] is None in groq 0.29; detect expired vs invalid via str(e)
            detail = "expired" if "expired_api_key" in str(e) else "invalid"
            self._report_auth_failure("GROQ_API_KEY", detail)
            # DEBUG keeps the trace file-only so the [AUTH] line stands alone
            # on the console (#32)
            logger.debug(f"Error during GROQ transcription: {e}", exc_info=True)
            return ""
        except Exception as e:
            logger.error(f"Error during GROQ transcription: {e}", exc_info=True)
            return ""

    def test_transcription(self, test_file_path: str) -> Optional[str]:
        """
        Test transcription with a specific file
        
        Args:
            test_file_path: Path to test audio file
            
        Returns:
            Transcribed text or None if failed
        """
        if not os.path.exists(test_file_path):
            logger.error(f"Test file not found: {test_file_path}")
            return None
        
        try:
            # Get audio duration for logging
            import soundfile as sf
            data, samplerate = sf.read(test_file_path)
            duration = len(data) / samplerate
            
            logger.info(f"Testing GROQ with file: {test_file_path} ({duration:.1f}s)")
            
            # Transcribe
            text = self.transcribe(test_file_path, duration)
            
            if text:
                logger.info(f"GROQ test transcription successful: {len(text)} chars")
                return text
            else:
                logger.warning("GROQ test transcription returned empty text")
                return None
                
        except Exception as e:
            logger.error(f"GROQ test transcription failed: {e}", exc_info=True)
            return None


class SonioxTranscriber(AbstractTranscriber):
    """Handles transcription using Soniox API"""
    
    def __init__(self):
        """Initialize the transcriber with API key"""
        super().__init__()
        self.api_key = self._get_api_key()
        self._check_soniox_availability()
    
    def _get_api_key(self) -> str:
        """Get API key from environment"""
        if not SONIOX_API_KEY:
            logger.error("SONIOX_API_KEY not found in environment variables!")
            raise ValueError("SONIOX_API_KEY is required for Soniox transcriber")
        
        logger.info("Using Soniox API key from environment")
        return SONIOX_API_KEY
    
    def _check_soniox_availability(self):
        """Check if Soniox library is available"""
        try:
            from soniox.speech_service import SpeechClient
            from soniox.transcribe_file import transcribe_file_short, transcribe_file_async
            logger.info("Soniox library available")
        except ImportError:
            logger.error("Soniox library not installed!")
            logger.error("Please install with: pip install soniox")
            raise ImportError("Soniox library required but not installed")
    
    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return "Soniox (genau)"
    
    def _clean_transcript_hallucinations(self, transcript: str) -> str:
        """
        Remove known hallucination patterns from Soniox transcriptions

        The Soniox model (especially de_v2) often adds incomplete words/phrases
        at the end of transcriptions that weren't spoken.

        Common patterns (based on analysis of 5000+ transcripts):
        - " und" (101 occurrences) - most common!
        - " Das" (23 occurrences)
        - " Und" (original pattern)
        - " In", " Also", " Für" (rare but confirmed)
        """
        if not transcript:
            return transcript

        original = transcript

        # Hallucination patterns at the end (with leading space to avoid false positives)
        # Ordered by frequency
        hallucination_patterns = [
            " und",   # Most common (lowercase!)
            " Und",   # Original pattern (uppercase)
            " Das",   # Second most common
            " In",
            " Also",
            " Für",
            " Oder",
        ]

        # Check each pattern and remove if found at end
        for pattern in hallucination_patterns:
            if transcript.endswith(pattern):
                transcript = transcript[:-len(pattern)]
                logger.debug(f"Removed hallucination: '{pattern}' at end")
                break  # Only remove one pattern per transcript

        # Log if something was removed
        if transcript != original:
            logger.debug(f"Original ending: ...'{original[-30:]}'")
            logger.debug(f"Cleaned to: ...'{transcript[-30:]}'")

        return transcript
    
    def transcribe(self, audio_file_path: str, duration_seconds: float) -> str:
        """
        Transcribe an audio file using Soniox API
        
        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds
            
        Returns:
            Transcribed text
        """
        logger.info(f"Starting Soniox transcription: {audio_file_path} (Duration: {duration_seconds:.1f}s)")
        
        try:
            # Import Soniox modules
            import grpc
            from soniox.speech_service import SpeechClient
            from soniox.transcribe_file import transcribe_file_short, transcribe_file_async
            
            if duration_seconds < SHORT_AUDIO_THRESHOLD:
                logger.info(f"Using synchronous Soniox transcription")
                
                # Create new client for each transcription to avoid connection issues
                logger.debug("Creating new SpeechClient for synchronous transcription")
                client = SpeechClient()
                
                try:
                    start_time = time.time()
                    
                    result = transcribe_file_short(
                        audio_file_path,
                        client,
                        model=SONIOX_MODEL,
                    )
                    
                    elapsed = time.time() - start_time
                    logger.info(f"Soniox synchronous transcription successful in {elapsed:.2f}s")
                    
                    text = "".join(word.text for word in result.words)
                    logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")
                    
                    # Clean hallucinations
                    text = self._clean_transcript_hallucinations(text)
                    
                    return text.strip()
                    
                finally:
                    # Close client
                    try:
                        client.close()
                        logger.debug("Soniox synchronous client closed")
                    except Exception as e:
                        logger.warning(f"Error closing Soniox sync client: {e}")
                
            else:
                logger.info(f"Using asynchronous Soniox transcription")
                
                # Create new client for long transcriptions
                logger.debug("Creating new SpeechClient for asynchronous transcription")
                client = SpeechClient()
                
                try:
                    # Upload file
                    start_time = time.time()
                    file_id = transcribe_file_async(
                        audio_file_path,
                        client,
                        model=SONIOX_MODEL,
                        reference_name=f"voice_{time.strftime('%Y%m%d_%H%M%S')}"
                    )
                    upload_time = time.time() - start_time
                    logger.info(f"File uploaded in {upload_time:.2f}s, ID: {file_id}")
                    
                    # Check status
                    max_attempts = 300  # Max 5 minutes wait
                    attempt = 0
                    
                    while attempt < max_attempts:
                        try:
                            status = client.GetTranscribeAsyncStatus(file_id)
                            
                            # Status is a string
                            current_status = status.status if hasattr(status, 'status') else "UNKNOWN"
                            
                            if attempt % 10 == 0:  # Log every 10 seconds
                                logger.info(f"Status after {attempt}s: {current_status}")
                            
                            if current_status == "COMPLETED":
                                # Get result
                                logger.info("Transcription completed, fetching result...")
                                result = client.GetTranscribeAsyncResult(file_id)
                                
                                # Delete file
                                try:
                                    client.DeleteTranscribeAsyncFile(file_id)
                                    logger.info("Temporary file deleted from server")
                                except Exception as e:
                                    logger.warning(f"Could not delete file: {e}")
                                
                                total_time = time.time() - start_time
                                logger.info(f"Soniox async transcription successful in {total_time:.2f}s")
                                
                                text = "".join(word.text for word in result.words)
                                logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")
                                
                                # Clean hallucinations
                                text = self._clean_transcript_hallucinations(text)
                                
                                return text.strip()
                                
                            elif current_status == "FAILED":
                                error_msg = getattr(status, 'error_message', 'Unknown error')
                                logger.error(f"Transcription failed: {error_msg}")
                                
                                try:
                                    client.DeleteTranscribeAsyncFile(file_id)
                                except:
                                    pass
                                return ""
                            
                        except grpc.RpcError as e:
                            # Re-raise auth failures so the outer handler reports
                            # them once instead of looping for the full timeout.
                            if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                                raise
                            logger.error(f"Error checking status (attempt {attempt}): {e}", exc_info=True)
                        except Exception as e:
                            logger.error(f"Error checking status (attempt {attempt}): {e}", exc_info=True)

                        time.sleep(1)
                        attempt += 1
                    
                    logger.error(f"Timeout after {attempt} seconds for async transcription")
                    try:
                        client.DeleteTranscribeAsyncFile(file_id)
                    except:
                        pass
                    return ""
                    
                finally:
                    # Close client
                    try:
                        client.close()
                        logger.info("Soniox async client closed")
                    except Exception as e:
                        logger.warning(f"Error closing Soniox async client: {e}")
                
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                self._report_auth_failure("SONIOX_API_KEY")
                # DEBUG keeps the trace file-only so the [AUTH] line stands
                # alone on the console (#32)
                logger.debug(f"Error during Soniox transcription: {e}", exc_info=True)
            else:
                logger.error(f"Error during Soniox transcription: {e}", exc_info=True)
            return ""
        except Exception as e:
            logger.error(f"Error during Soniox transcription: {e}", exc_info=True)
            return ""

    def test_transcription(self, test_file_path: str) -> Optional[str]:
        """
        Test transcription with a specific file
        
        Args:
            test_file_path: Path to test audio file
            
        Returns:
            Transcribed text or None if failed
        """
        if not os.path.exists(test_file_path):
            logger.error(f"Test file not found: {test_file_path}")
            return None
        
        try:
            # Get audio duration
            import soundfile as sf
            data, samplerate = sf.read(test_file_path)
            duration = len(data) / samplerate
            
            logger.info(f"Testing Soniox with file: {test_file_path} ({duration:.1f}s)")
            
            # Transcribe
            text = self.transcribe(test_file_path, duration)
            
            if text:
                logger.info(f"Soniox test transcription successful: {len(text)} chars")
                return text
            else:
                logger.warning("Soniox test transcription returned empty text")
                return None
                
        except Exception as e:
            logger.error(f"Soniox test transcription failed: {e}", exc_info=True)
            return None


class SonioxV4Transcriber(AbstractTranscriber):
    """Handles transcription using Soniox v4 Async REST API.

    Workflow: Upload file → Create transcription → Poll status → Get result → Cleanup.
    Uses httpx for HTTP requests. No Soniox SDK needed.
    Context feature enabled for better recognition of domain terms and proper nouns.
    """

    def __init__(self):
        """Initialize the transcriber with API key"""
        super().__init__()
        if not SONIOX_API_KEY:
            raise ValueError("SONIOX_API_KEY is required for Soniox v4 transcriber")
        self.api_key = SONIOX_API_KEY
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
        logger.info(f"Soniox v4 transcriber initialized (model: {SONIOX_V4_MODEL})")
        if SONIOX_CONTEXT:
            logger.info(f"Context enabled: {len(SONIOX_CONTEXT.get('terms', []))} terms")

    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return "Soniox v4 (async)"

    def transcribe(self, audio_file_path: str, duration_seconds: float) -> str:
        """Transcribe an audio file using Soniox v4 Async REST API.

        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds

        Returns:
            Transcribed text
        """
        import httpx

        logger.info(f"Starting Soniox v4 transcription: {audio_file_path} "
                    f"(Duration: {duration_seconds:.1f}s)")

        file_id = None
        tx_id = None
        start_time = time.time()

        try:
            # Step 1: Upload file
            with open(audio_file_path, "rb") as f:
                resp = httpx.post(
                    f"{SONIOX_V4_API_BASE}/v1/files",
                    headers=self.headers,
                    files={"file": (os.path.basename(audio_file_path), f)},
                    timeout=60
                )
            resp.raise_for_status()
            file_id = resp.json()["id"]
            upload_time = time.time() - start_time
            logger.info(f"File uploaded in {upload_time:.2f}s, ID: {file_id}")

            # Step 2: Create transcription
            tx_config = {
                "model": SONIOX_V4_MODEL,
                "file_id": file_id,
                "language_hints": SONIOX_LANGUAGE_HINTS,
            }
            if SONIOX_CONTEXT:
                tx_config["context"] = SONIOX_CONTEXT

            resp = httpx.post(
                f"{SONIOX_V4_API_BASE}/v1/transcriptions",
                headers=self.headers,
                json=tx_config,
                timeout=30
            )
            resp.raise_for_status()
            tx_id = resp.json()["id"]
            logger.info(f"Transcription created, ID: {tx_id}")

            # Step 3: Poll until completed
            for attempt in range(SONIOX_V4_MAX_POLL_ATTEMPTS):
                resp = httpx.get(
                    f"{SONIOX_V4_API_BASE}/v1/transcriptions/{tx_id}",
                    headers=self.headers,
                    timeout=15
                )
                resp.raise_for_status()
                status = resp.json()["status"]

                if attempt % 10 == 0 and attempt > 0:
                    logger.debug(f"Polling attempt {attempt}: status={status}")

                if status == "completed":
                    logger.info(f"Transcription completed after {attempt} polls")
                    break
                elif status in ("error", "failed"):
                    error_msg = resp.json().get("error", "Unknown error")
                    logger.error(f"Soniox v4 transcription failed: {error_msg}")
                    return ""

                time.sleep(SONIOX_V4_POLL_INTERVAL)
            else:
                logger.error(f"Soniox v4 polling timeout after {SONIOX_V4_MAX_POLL_ATTEMPTS} attempts")
                return ""

            # Step 4: Get transcript
            resp = httpx.get(
                f"{SONIOX_V4_API_BASE}/v1/transcriptions/{tx_id}/transcript",
                headers=self.headers,
                timeout=15
            )
            resp.raise_for_status()
            text = resp.json().get("text", "").strip()

            elapsed = time.time() - start_time
            logger.info(f"Soniox v4 transcription successful in {elapsed:.2f}s")
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")

            return text

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._report_auth_failure("SONIOX_API_KEY")
                # DEBUG keeps the trace file-only so the [AUTH] line stands
                # alone on the console (#32)
                logger.debug(f"Error during Soniox v4 transcription: {e}", exc_info=True)
            else:
                logger.error(f"Error during Soniox v4 transcription: {e}", exc_info=True)
            return ""
        except Exception as e:
            logger.error(f"Error during Soniox v4 transcription: {e}", exc_info=True)
            return ""

        finally:
            # Cleanup: Always delete transcription and file from server
            import httpx as httpx_cleanup
            try:
                if tx_id:
                    httpx_cleanup.delete(
                        f"{SONIOX_V4_API_BASE}/v1/transcriptions/{tx_id}",
                        headers=self.headers, timeout=10
                    )
                    logger.debug(f"Transcription {tx_id} deleted from server")
            except Exception as e:
                logger.warning(f"Could not delete transcription: {e}")
            try:
                if file_id:
                    httpx_cleanup.delete(
                        f"{SONIOX_V4_API_BASE}/v1/files/{file_id}",
                        headers=self.headers, timeout=10
                    )
                    logger.debug(f"File {file_id} deleted from server")
            except Exception as e:
                logger.warning(f"Could not delete file: {e}")

    def test_transcription(self, test_file_path: str) -> Optional[str]:
        """Test transcription with a specific file"""
        if not os.path.exists(test_file_path):
            logger.error(f"Test file not found: {test_file_path}")
            return None

        try:
            import soundfile as sf
            data, samplerate = sf.read(test_file_path)
            duration = len(data) / samplerate

            logger.info(f"Testing Soniox v4 with file: {test_file_path} ({duration:.1f}s)")

            text = self.transcribe(test_file_path, duration)

            if text:
                logger.info(f"Soniox v4 test transcription successful: {len(text)} chars")
                return text
            else:
                logger.warning("Soniox v4 test transcription returned empty text")
                return None

        except Exception as e:
            logger.error(f"Soniox v4 test transcription failed: {e}", exc_info=True)
            return None


class SonioxLiveTranscriber(AbstractTranscriber):
    """Handles live transcription using Soniox v4 WebSocket RT API.

    Audio is streamed in real-time during recording via WebSocket. When recording
    stops, a finalize command is sent and the server returns the final, quality-
    reviewed transcript within milliseconds.

    Threading model (Block 2 — producer/consumer):
    - RecordingLoop thread (thoughtborne.py) calls send_audio_chunk() for each
      audio chunk. This is a non-blocking queue.put_nowait() — the actual
      WebSocket send happens in a dedicated sender thread, so TCP backpressure
      never stalls the recording loop or the audio capture.
    - Sender thread drains the queue and writes to the WebSocket.
    - Receiver thread reads WebSocket responses and collects final tokens.
    - transcribe() queues silence + finalize + EOS plus a drain sentinel, waits
      for the sender to reach the sentinel, then waits for the receiver.

    If the queue is full (heavy backpressure), newest chunks are dropped —
    the live transcript gets a gap, but the MP3 archive in audio_handler is
    unaffected because the recording loop stores frames independently of
    this queue.

    Additional methods beyond AbstractTranscriber:
    - start_session(): Open WebSocket, send config, start receiver + sender threads
    - send_audio_chunk(raw_data): Enqueue PCM bytes for the sender thread
    - cancel_session(): Stop sender thread, close WebSocket immediately
    """

    # Sentinels distinguishable from any audio bytes / JSON string the queue
    # can carry. Using object() guarantees identity-comparison (`is`) works.
    _STOP_SENTINEL = object()
    _FINALIZE_DRAIN_SENTINEL = object()

    def __init__(self):
        """Initialize the transcriber with API key"""
        super().__init__()
        if not SONIOX_API_KEY:
            raise ValueError("SONIOX_API_KEY is required for Soniox Live transcriber")
        self.api_key = SONIOX_API_KEY

        # Session state
        self._ws = None
        self._session_active = False
        self._receiver_thread = None
        self._final_tokens = []
        self._result_ready = threading.Event()
        self._session_lock = threading.Lock()
        self._session_error = None
        self._session_auth_error = False

        # Block 2: producer/consumer queue and sender thread.
        # Queue is instantiated fresh in start_session() so no stale items
        # carry over from a previous session.
        self._send_queue = None
        self._sender_thread = None
        self._sender_stop = threading.Event()
        self._finalize_drained = threading.Event()

        # Drop diagnostic: WebSocket send latency tracking. Block-1 fields,
        # now populated from the sender thread. A blocking send (>100ms) is
        # the direct indicator of TCP backpressure — but after Block 2 it
        # blocks only the sender thread, not the recording loop.
        # Reset on each new session.
        self._send_latency_max = 0.0
        self._send_latency_blocked_total = 0.0
        self._send_latency_blocked_count = 0

        # Block 2: queue-drop diagnostic. When TCP backpressure outlasts the
        # queue buffer, send_audio_chunk() drops new chunks. We log the
        # transition into and out of drop mode (instead of per-chunk WARNINGs
        # which would spam the log under sustained backpressure).
        self._drop_warned = False
        self._drop_start_time = None
        self._drop_count_current = 0
        self._drop_count_total = 0

        logger.info(f"Soniox Live transcriber initialized (model: {SONIOX_RT_MODEL})")
        if SONIOX_CONTEXT:
            logger.info(f"Context enabled: {len(SONIOX_CONTEXT.get('terms', []))} terms")

    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return "Soniox Live (stream)"

    @property
    def is_live(self) -> bool:
        """This transcriber supports live audio streaming."""
        return True

    def start_session(self, sample_rate_override: Optional[int] = None) -> bool:
        """Open WebSocket connection and start receiver thread.

        Called from on_start_recording() when this transcriber is active.

        Args:
            sample_rate_override: Sample rate to announce to Soniox. Defaults to
                config.RATE (the production live-capture rate). The file-replay
                self-test passes the test file's actual rate so Soniox decodes
                the bytes at the rate they were authored at (#12).

        Returns:
            True if session started successfully
        """
        import json

        with self._session_lock:
            if self._session_active:
                logger.warning("Live session already active, closing old one")
                self._close_session_internal()

            try:
                from websockets.sync.client import connect

                logger.info(f"Opening WebSocket connection to {SONIOX_WS_URL}...")
                self._ws = connect(SONIOX_WS_URL)

                # Send configuration
                config = {
                    "api_key": self.api_key,
                    "model": SONIOX_RT_MODEL,
                    "audio_format": "pcm_s16le",
                    "num_channels": CHANNELS,
                    "sample_rate": sample_rate_override if sample_rate_override is not None else RATE,
                    "language_hints": SONIOX_LANGUAGE_HINTS,
                    "enable_endpoint_detection": True,
                }
                if SONIOX_CONTEXT:
                    config["context"] = SONIOX_CONTEXT

                self._ws.send(json.dumps(config))
                logger.info("WebSocket config sent")

                # Reset state
                self._final_tokens = []
                self._result_ready.clear()
                self._session_error = None
                self._session_auth_error = False
                self._session_active = True

                # Reset send-latency diagnostic stats for the new session
                self._send_latency_max = 0.0
                self._send_latency_blocked_total = 0.0
                self._send_latency_blocked_count = 0

                # Reset queue-drop diagnostic stats for the new session
                self._drop_warned = False
                self._drop_start_time = None
                self._drop_count_current = 0
                self._drop_count_total = 0

                # Block 2: fresh queue + sender thread for this session.
                # A new Queue avoids any stale items carrying over from a
                # previous session that ended uncleanly.
                self._send_queue = queue.Queue(maxsize=SONIOX_LIVE_QUEUE_MAX_CHUNKS)
                self._sender_stop.clear()
                self._finalize_drained.clear()

                # Start receiver thread
                self._receiver_thread = threading.Thread(
                    target=self._receiver_loop,
                    daemon=True,
                    name="SonioxLive-Receiver"
                )
                self._receiver_thread.start()

                # Start sender thread (Block 2)
                self._sender_thread = threading.Thread(
                    target=self._sender_loop,
                    daemon=True,
                    name="SonioxLive-Sender"
                )
                self._sender_thread.start()

                logger.info("Soniox Live session started")
                return True

            except Exception as e:
                logger.error(f"Failed to start Soniox Live session: {e}", exc_info=True)
                self._session_active = False
                if self._ws is not None:
                    try:
                        self._ws.close()
                    except Exception:
                        pass
                self._ws = None
                return False

    def send_audio_chunk(self, raw_data: bytes):
        """Hand off an audio chunk to the sender thread via queue (non-blocking).

        Called from recording_loop_thread() for each record_chunk(). The actual
        WebSocket send happens in the sender thread, so this call returns
        immediately even when TCP backpressure stalls the send. That is the
        whole point of Block 2: keep the recording loop free of network I/O
        so PyAudio can drain its buffer without gaps.

        Drop behaviour: if the queue is full (sender can't keep up under heavy
        backpressure), the new chunk is dropped via put_nowait(). The MP3
        archive is unaffected because the recording loop stores frames in
        audio_handler independently of this queue; only the live transcript
        loses the dropped window.

        Args:
            raw_data: Raw PCM bytes (16-bit signed LE, mono, 16000 Hz)
        """
        # Hold a local reference so a concurrent _close_session_internal()
        # (which sets self._send_queue = None) can't turn this into an
        # AttributeError between the None-check and put_nowait.
        q = self._send_queue
        if not self._session_active or q is None:
            return

        try:
            q.put_nowait(raw_data)
            # If we were in drop-mode before, log the recovery.
            if self._drop_warned:
                drop_duration = time.time() - self._drop_start_time if self._drop_start_time else 0.0
                logger.info(
                    f"Queue recovered after dropping {self._drop_count_current} "
                    f"chunks over {drop_duration:.1f}s"
                )
                self._drop_warned = False
                self._drop_count_current = 0
                self._drop_start_time = None
        except queue.Full:
            # Sender can't drain fast enough — heavy backpressure.
            # Log WARNING only on transition into drop mode to avoid log spam
            # under sustained backpressure; per-chunk drops go to DEBUG.
            if not self._drop_warned:
                logger.warning(
                    "Send queue full, dropping new chunks "
                    "(live transcript will have a gap, MP3 archive unaffected)"
                )
                self._drop_warned = True
                self._drop_start_time = time.time()
                self._drop_count_current = 0
            self._drop_count_current += 1
            self._drop_count_total += 1
            logger.debug(
                f"Dropped chunk #{self._drop_count_current} in current drop window"
            )

    def _sender_loop(self):
        """Sender thread: drains the audio queue and writes to the WebSocket.

        Block 2 decouples this from the recording loop. TCP backpressure now
        blocks only this thread; the recording loop keeps reading PyAudio
        without gaps.

        Items in the queue can be:
        - bytes:  raw PCM audio chunk → _ws.send(bytes)
        - str:    JSON command (e.g. {"type":"finalize"}) or "" (EOS) → _ws.send(str)
        - _FINALIZE_DRAIN_SENTINEL: marker that everything before it has been
          sent — triggers the _finalize_drained event so transcribe() can move
          on to waiting for the receiver.
        - _STOP_SENTINEL: hard stop, exit immediately (sent by _close_session_internal).
        """
        logger.info("Soniox Live sender thread started")

        try:
            while not self._sender_stop.is_set():
                try:
                    item = self._send_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                # Drain marker — release any transcribe() that is waiting for
                # the queue to flush, then continue (don't exit).
                if item is self._FINALIZE_DRAIN_SENTINEL:
                    self._finalize_drained.set()
                    continue

                # Hard stop sentinel — exit immediately.
                if item is self._STOP_SENTINEL:
                    break

                # If the session has already been marked inactive (e.g. the
                # receiver detected a server-side disconnect), silently drop
                # remaining items. Don't break — sentinels still need to flow.
                if not self._session_active or self._ws is None:
                    continue

                try:
                    send_start = time.perf_counter()
                    self._ws.send(item)
                    send_elapsed = time.perf_counter() - send_start

                    if send_elapsed > self._send_latency_max:
                        self._send_latency_max = send_elapsed
                    if send_elapsed > 0.01:  # >10ms: track as "blocked"
                        self._send_latency_blocked_total += send_elapsed
                        self._send_latency_blocked_count += 1
                    if send_elapsed > 0.1:  # >100ms: warn
                        logger.warning(
                            f"Slow WebSocket send: {send_elapsed*1000:.0f}ms "
                            f"(sender-thread backpressure, recording loop unaffected)"
                        )
                except Exception as e:
                    if self._session_auth_error:
                        # Server closed the WS right after a 401; the [AUTH]
                        # line already covers it on the console (#32)
                        logger.debug(f"Error sending from Soniox Live sender thread: {e}")
                    else:
                        logger.error(f"Error sending from Soniox Live sender thread: {e}")
                    # Mark session inactive so send_audio_chunk() stops growing
                    # the queue. Analogous to the Block-1 receiver fix that
                    # stopped the recording-loop spam after a server disconnect.
                    self._session_active = False
                    break
        except Exception as e:
            logger.error(f"Soniox Live sender loop crashed: {e}", exc_info=True)
            self._session_active = False
        finally:
            # Always release any waiter on finalize-drain so transcribe()
            # doesn't hang forever if the sender exited before reaching the
            # sentinel (server-side disconnect, WS already closed, etc.).
            self._finalize_drained.set()
            remaining = self._send_queue.qsize() if self._send_queue is not None else 0
            logger.info(
                f"Soniox Live sender thread stopped "
                f"(queue size at exit: {remaining})"
            )

    def cancel_session(self):
        """Cancel the live session immediately.

        Called from on_cancel_recording().
        """
        with self._session_lock:
            logger.info("Cancelling Soniox Live session")
            self._close_session_internal()

    def _receiver_loop(self):
        """Receiver thread: reads WebSocket messages and collects final tokens.

        Runs until 'finished: true' is received or an error occurs.
        Sets _result_ready event when done.
        """
        import json

        logger.info("Soniox Live receiver thread started")

        try:
            while self._session_active and self._ws is not None:
                try:
                    raw_msg = self._ws.recv(timeout=25)
                except Exception as e:
                    if self._session_active:
                        logger.error(f"WebSocket recv error: {e}")
                        self._session_error = str(e)
                    break

                try:
                    msg = json.loads(raw_msg)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Non-JSON message received: {str(raw_msg)[:100]}")
                    continue

                # Check for error
                if msg.get("error_code"):
                    error_msg = msg.get("error_message", "Unknown error")
                    if msg.get("error_code") == 401:
                        self._report_auth_failure("SONIOX_API_KEY")
                        # Flag for finalization; DEBUG keeps the 401 detail
                        # file-only so the [AUTH] line stands alone on the
                        # console (#32)
                        self._session_auth_error = True
                        logger.debug(f"Soniox Live error: {msg['error_code']} - {error_msg}")
                    else:
                        logger.error(f"Soniox Live error: {msg['error_code']} - {error_msg}")
                    self._session_error = error_msg
                    break

                # Collect final tokens
                for token in msg.get("tokens", []):
                    if token.get("is_final"):
                        text = token.get("text", "")
                        if text not in ("<end>", "<fin>", ""):
                            self._final_tokens.append(text)
                        if text == "<fin>":
                            logger.info("Received <fin> token, finalization complete")

                # Check if finished
                if msg.get("finished"):
                    logger.info("Received 'finished' signal from server")
                    break

        except Exception as e:
            logger.error(f"Soniox Live receiver error: {e}", exc_info=True)
            self._session_error = str(e)
        finally:
            # Mark session inactive so send_audio_chunk() stops trying to
            # send on a dead WebSocket. Without this, every chunk in the
            # recording loop produced a fresh ERROR log entry after a
            # server-side disconnect, until the user pressed Stop.
            self._session_active = False
            self._result_ready.set()
            logger.info(f"Soniox Live receiver thread stopped "
                       f"({len(self._final_tokens)} final tokens collected)")

    def transcribe(self, audio_file_path: str, duration_seconds: float) -> str:
        """Finalize the live session and return the transcript.

        For the Live transcriber, this does NOT use audio_file_path.
        Instead it sends finalize to the WebSocket and waits for the result.
        The audio_file_path is only used for logging (the file is still saved
        by audio_handler for archival purposes).

        Args:
            audio_file_path: Path to archived audio file (for logging only)
            duration_seconds: Duration of the recording

        Returns:
            Transcribed text
        """
        import json

        logger.info(f"Soniox Live: finalizing session "
                    f"(duration: {duration_seconds:.1f}s, audio archived at: {audio_file_path})")

        if not self._session_active or self._ws is None or self._send_queue is None:
            logger.warning("No active Soniox Live session to finalize")
            # Block-1-Lücke fix: ensure stats are logged and threads cleaned
            # up even when finalize hits the early-return path (e.g. when the
            # 20-s Soniox idle timeout killed the session during recording).
            # _close_session_internal is idempotent and tolerates a dead session.
            self._close_session_internal()
            return ""

        start_time = time.time()

        try:
            # Step 1: queue silence before finalize (helps model accuracy)
            silence_samples = int(RATE * SONIOX_LIVE_FINALIZE_DELAY)
            silence_bytes = b'\x00' * (silence_samples * 2)  # 16-bit = 2 bytes per sample

            # We use put() with a short timeout instead of put_nowait() because
            # the finalize items MUST reach the sender; a queue that is briefly
            # full from prior backpressure should be waited on for up to 1 s.
            # If the sender is dead the puts will time out — _finalize_drained
            # will then time out below and the receiver wait will resolve via
            # _result_ready (which is set in the receiver's finally block).
            try:
                self._send_queue.put(silence_bytes, timeout=1.0)
                self._send_queue.put(json.dumps({"type": "finalize"}), timeout=1.0)
                self._send_queue.put("", timeout=1.0)
                logger.debug("Queued finalize sequence (silence + command + EOS)")
            except queue.Full:
                logger.warning(
                    "Could not enqueue finalize sequence within 1s "
                    "(sender thread may have stalled or died)"
                )

            # Step 2: queue drain sentinel and wait for sender to reach it.
            # When the sender hits the sentinel it sets _finalize_drained.
            # If the sender is already dead, its finally block also sets the
            # event so we don't hang here.
            self._finalize_drained.clear()
            try:
                self._send_queue.put(self._FINALIZE_DRAIN_SENTINEL, timeout=1.0)
            except queue.Full:
                logger.warning("Could not enqueue finalize drain sentinel within 1s")

            if not self._finalize_drained.wait(timeout=SONIOX_LIVE_FINALIZE_DRAIN_TIMEOUT):
                logger.warning(
                    f"Sender did not reach drain sentinel within "
                    f"{SONIOX_LIVE_FINALIZE_DRAIN_TIMEOUT}s — proceeding to wait for receiver"
                )

            # Step 3: Wait for receiver to finish
            if not self._result_ready.wait(timeout=SONIOX_LIVE_FINALIZE_TIMEOUT):
                logger.error(f"Soniox Live finalize timeout after {SONIOX_LIVE_FINALIZE_TIMEOUT}s")
                return ""

            # Step 4: Check for errors
            if self._session_error:
                if self._session_auth_error:
                    # Auth failure already surfaced as the [AUTH] console
                    # line; repeating it at ERROR would bury that line (#32)
                    logger.debug(f"Soniox Live session had error: {self._session_error}")
                else:
                    logger.error(f"Soniox Live session had error: {self._session_error}")
                return ""

            # Step 5: Assemble text from final tokens
            text = "".join(self._final_tokens).strip()

            elapsed = time.time() - start_time
            logger.info(f"Soniox Live transcription finalized in {elapsed:.2f}s")
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")

            return text

        except Exception as e:
            logger.error(f"Error during Soniox Live finalization: {e}", exc_info=True)
            return ""
        finally:
            self._close_session_internal()

    def _close_session_internal(self):
        """Close the WebSocket, stop sender thread, clean up session state.

        Idempotent: safe to call multiple times (e.g. on a session that has
        already been torn down by a server-side disconnect).
        """
        # Log send-latency diagnostics if any traffic was measured
        if self._send_latency_max > 0:
            logger.info(
                f"Session send-latency stats: "
                f"max={self._send_latency_max*1000:.0f}ms, "
                f"blocked-events(>10ms)={self._send_latency_blocked_count}, "
                f"total-blocked-time={self._send_latency_blocked_total:.2f}s"
            )

        # Block 2: log queue-drop diagnostics
        if self._drop_count_total > 0:
            logger.info(
                f"Session queue-drop stats: "
                f"total-chunks-dropped={self._drop_count_total} "
                f"(live transcript had gaps, MP3 archive unaffected)"
            )

        self._session_active = False

        # Close WebSocket FIRST so any in-flight _ws.send() in the sender
        # thread raises ConnectionClosed immediately and the sender breaks
        # out instead of waiting for a TCP timeout. In the normal-stop path
        # the sender has already finished (drained the queue) before we get
        # here, so this is just cleanup; in the cancel/disconnect path it
        # actively unblocks a stalled sender.
        if self._ws is not None:
            try:
                self._ws.close()
                logger.debug("WebSocket closed")
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
            self._ws = None

        # Signal sender to stop and wait for it. Also push a STOP sentinel so
        # the sender wakes up immediately even if it was idle in queue.get().
        self._sender_stop.set()
        if self._sender_thread is not None and self._sender_thread.is_alive():
            if self._send_queue is not None:
                try:
                    self._send_queue.put_nowait(self._STOP_SENTINEL)
                except queue.Full:
                    # Queue is full of stale items; the 0.5s get-timeout in
                    # the sender will still let it notice _sender_stop.
                    pass
            self._sender_thread.join(timeout=SONIOX_LIVE_SENDER_JOIN_TIMEOUT)
            if self._sender_thread.is_alive():
                logger.warning("Soniox Live sender thread did not stop in time")
        self._sender_thread = None
        self._send_queue = None

        # Wait for receiver thread to finish
        if self._receiver_thread is not None and self._receiver_thread.is_alive():
            self._receiver_thread.join(timeout=3)
            if self._receiver_thread.is_alive():
                logger.warning("Soniox Live receiver thread did not stop in time")
        self._receiver_thread = None

    def test_transcription(self, test_file_path: str) -> Optional[str]:
        """Test transcription by streaming a file chunk-by-chunk.

        Opens a live session, streams the test file with real-time pacing,
        then finalizes and returns the result.
        """
        if not os.path.exists(test_file_path):
            logger.error(f"Test file not found: {test_file_path}")
            return None

        try:
            import soundfile as sf
            data, samplerate = sf.read(test_file_path, dtype='int16')
            duration = len(data) / samplerate

            logger.info(f"Testing Soniox Live with file: {test_file_path} ({duration:.1f}s)")

            # Start session — announce the file's real rate so Soniox decodes
            # the bytes correctly even when it differs from config.RATE (#12).
            if not self.start_session(sample_rate_override=samplerate):
                logger.error("Failed to start Soniox Live test session")
                return None

            # Stream audio in chunks (approximate real-time pacing)
            from config import CHUNK
            audio_bytes = data.tobytes()
            bytes_per_chunk = CHUNK * 2  # 16-bit = 2 bytes per sample

            for i in range(0, len(audio_bytes), bytes_per_chunk):
                chunk = audio_bytes[i:i + bytes_per_chunk]
                self.send_audio_chunk(chunk)
                time.sleep(CHUNK / samplerate)  # Real-time pacing at the file's rate

            # Finalize
            text = self.transcribe(test_file_path, duration)

            if text:
                logger.info(f"Soniox Live test transcription successful: {len(text)} chars")
                return text
            else:
                logger.warning("Soniox Live test transcription returned empty text")
                return None

        except Exception as e:
            logger.error(f"Soniox Live test transcription failed: {e}", exc_info=True)
            self.cancel_session()
            return None


def create_transcriber(api_name: str) -> AbstractTranscriber:
    """
    Factory function to create transcriber instances

    Args:
        api_name: Name of the API to use ('soniox-live', 'soniox', 'groq', or 'soniox-v4')

    Returns:
        Transcriber instance

    Raises:
        ValueError: If api_name is not supported
    """
    if api_name == "groq":
        return GroqTranscriber()
    elif api_name == "soniox":
        return SonioxTranscriber()
    elif api_name == "soniox-v4":
        return SonioxV4Transcriber()
    elif api_name == "soniox-live":
        return SonioxLiveTranscriber()
    else:
        raise ValueError(f"Unknown API: {api_name}. Supported: soniox-live, soniox, groq, soniox-v4")