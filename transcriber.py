"""
Transcriber Module

This module handles speech-to-text transcription using multiple APIs.
It provides a clean interface for transcribing audio files using
Modal (Parakeet-primeline), GROQ (Whisper Large V3 Turbo), Soniox,
or HuggingFace Inference Endpoints.

Classes:
    AbstractTranscriber: Base class for all transcriber implementations
    ModalParakeetTranscriber: Handles transcription using Modal-hosted parakeet-primeline
    GroqTranscriber: Handles transcription using GROQ API
    SonioxTranscriber: Handles transcription using Soniox API
    HuggingFaceTranscriber: Handles transcription using HuggingFace Inference Endpoints
"""

import os
import sys
import time
import logging
import threading
import requests
from abc import ABC, abstractmethod
from typing import Optional
from pathlib import Path

from groq import Groq

from config import (
    GROQ_MODEL, LANGUAGE, TEXT_ARCHIVE_FOLDER,
    GROQ_API_KEY, SONIOX_API_KEY, SONIOX_MODEL,
    SHORT_AUDIO_THRESHOLD,
    HUGGINGFACE_API_KEY, HUGGINGFACE_ENDPOINT_URL,
    HUGGINGFACE_SCALE_UP_TIMEOUT, HUGGINGFACE_REQUEST_TIMEOUT,
    MODAL_ENDPOINT_URL, MODAL_REQUEST_TIMEOUT,
    SONIOX_V4_API_BASE, SONIOX_V4_MODEL, SONIOX_V4_POLL_INTERVAL,
    SONIOX_V4_MAX_POLL_ATTEMPTS,
    SONIOX_WS_URL, SONIOX_RT_MODEL, SONIOX_LIVE_FINALIZE_DELAY,
    SONIOX_LIVE_FINALIZE_TIMEOUT,
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


class ModalParakeetTranscriber(AbstractTranscriber):
    """Handles transcription using Modal-hosted parakeet-primeline model.

    Parakeet-primeline: 600M parameter NeMo model, 4.11% WER on Tuda-De
    (spontaneous German). Hosted serverless on Modal.com with GPU memory
    snapshots for fast cold starts.
    """

    def __init__(self):
        """Initialize the transcriber with endpoint URL"""
        super().__init__()
        self.endpoint_url = self._get_endpoint_url()
        self.headers = {"Content-Type": "application/octet-stream"}
        logger.info(f"Modal Parakeet transcriber initialized with endpoint: {self.endpoint_url[:50]}...")
        logger.info(f"Request timeout: {MODAL_REQUEST_TIMEOUT}s")

    def _get_endpoint_url(self) -> str:
        """Get endpoint URL from environment"""
        if not MODAL_ENDPOINT_URL:
            logger.error("MODAL_ENDPOINT_URL not found in environment variables!")
            raise ValueError("MODAL_ENDPOINT_URL is required for Modal Parakeet transcriber")
        return MODAL_ENDPOINT_URL

    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return "Parakeet (deutsch)"

    def transcribe(self, audio_file_path: str, duration_seconds: float) -> str:
        """Transcribe an audio file using Modal parakeet-primeline endpoint.

        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds

        Returns:
            Transcribed text
        """
        logger.info(f"Starting Modal Parakeet transcription: {audio_file_path} (Duration: {duration_seconds:.1f}s)")

        try:
            start_time = time.time()

            with open(audio_file_path, "rb") as audio_file:
                audio_data = audio_file.read()

            response = requests.post(
                self.endpoint_url,
                headers=self.headers,
                data=audio_data,
                timeout=MODAL_REQUEST_TIMEOUT
            )

            if response.status_code != 200:
                logger.error(f"Modal API error: {response.status_code} - {response.text}")
                return ""

            result = response.json()
            elapsed = time.time() - start_time

            # Log server-side error if present (Modal returns HTTP 200 with error field)
            if result.get("error"):
                logger.error(f"Modal server error: {result['error']}")
                return ""

            text = result.get("text", "")
            text = text.strip()

            logger.info(f"Modal Parakeet transcription successful in {elapsed:.2f}s")
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")

            return text

        except requests.exceptions.Timeout:
            logger.error(f"Modal API timeout after {MODAL_REQUEST_TIMEOUT}s - "
                        f"endpoint may be cold starting")
            return ""
        except requests.exceptions.RequestException as e:
            logger.error(f"Modal API request error: {e}", exc_info=True)
            return ""
        except Exception as e:
            logger.error(f"Error during Modal Parakeet transcription: {e}", exc_info=True)
            return ""

    def test_transcription(self, test_file_path: str) -> Optional[str]:
        """Test transcription with a specific file.

        Args:
            test_file_path: Path to test audio file

        Returns:
            Transcribed text or None if failed
        """
        if not os.path.exists(test_file_path):
            logger.error(f"Test file not found: {test_file_path}")
            return None

        try:
            import soundfile as sf
            data, samplerate = sf.read(test_file_path)
            duration = len(data) / samplerate

            logger.info(f"Testing Modal Parakeet with file: {test_file_path} ({duration:.1f}s)")

            text = self.transcribe(test_file_path, duration)

            if text:
                logger.info(f"Modal Parakeet test transcription successful: {len(text)} chars")
                return text
            else:
                logger.warning("Modal Parakeet test transcription returned empty text")
                return None

        except Exception as e:
            logger.error(f"Modal Parakeet test transcription failed: {e}", exc_info=True)
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


class HuggingFaceTranscriber(AbstractTranscriber):
    """Handles transcription using HuggingFace Inference Endpoints

    Uses the primeline/whisper-large-v3-turbo-german model which achieves
    2.6% WER on German speech - significantly better than standard Whisper.
    """

    def __init__(self):
        """Initialize the transcriber with API key and endpoint URL"""
        super().__init__()
        self.api_key = self._get_api_key()
        self.endpoint_url = self._get_endpoint_url()
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "audio/mpeg",
            # X-Scale-Up-Timeout: Proxy holds request until endpoint is ready (cold start handling)
            # Without this header, requests during scale-up would fail with 503
            "X-Scale-Up-Timeout": str(HUGGINGFACE_SCALE_UP_TIMEOUT)
        }
        logger.info(f"HuggingFace transcriber initialized with endpoint: {self.endpoint_url[:50]}...")
        logger.info(f"Scale-up timeout: {HUGGINGFACE_SCALE_UP_TIMEOUT}s, Request timeout: {HUGGINGFACE_REQUEST_TIMEOUT}s")

    def _get_api_key(self) -> str:
        """Get API key from environment"""
        if not HUGGINGFACE_API_KEY:
            logger.error("HUGGINGFACE_API_KEY not found in environment variables!")
            raise ValueError("HUGGINGFACE_API_KEY is required for HuggingFace transcriber")

        logger.info("Using HuggingFace API key from environment")
        return HUGGINGFACE_API_KEY

    def _get_endpoint_url(self) -> str:
        """Get endpoint URL from environment"""
        if not HUGGINGFACE_ENDPOINT_URL:
            logger.error("HUGGINGFACE_ENDPOINT_URL not found in environment variables!")
            raise ValueError("HUGGINGFACE_ENDPOINT_URL is required for HuggingFace transcriber")

        return HUGGINGFACE_ENDPOINT_URL

    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return "Primeline Whisper (deutsch)"

    def transcribe(self, audio_file_path: str, duration_seconds: float) -> str:
        """
        Transcribe an audio file using HuggingFace Inference Endpoint

        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds

        Returns:
            Transcribed text
        """
        logger.info(f"Starting HuggingFace transcription: {audio_file_path} (Duration: {duration_seconds:.1f}s)")

        try:
            start_time = time.time()

            # Read audio file as binary
            with open(audio_file_path, "rb") as audio_file:
                audio_data = audio_file.read()

            # Send request to HuggingFace endpoint
            # Timeout includes potential scale-up time (cold start) + transcription time
            response = requests.post(
                self.endpoint_url,
                headers=self.headers,
                data=audio_data,
                timeout=HUGGINGFACE_REQUEST_TIMEOUT
            )

            # Check for errors
            if response.status_code != 200:
                logger.error(f"HuggingFace API error: {response.status_code} - {response.text}")
                return ""

            # Parse response
            result = response.json()
            elapsed = time.time() - start_time

            # Extract text from response
            # HuggingFace Whisper endpoints typically return {"text": "..."}
            if isinstance(result, dict):
                text = result.get("text", "")
            elif isinstance(result, str):
                text = result
            else:
                logger.warning(f"Unexpected response format: {type(result)}")
                text = str(result)

            text = text.strip()
            logger.info(f"HuggingFace transcription successful in {elapsed:.2f}s")
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")

            return text

        except requests.exceptions.Timeout:
            logger.error(f"HuggingFace API timeout after {HUGGINGFACE_REQUEST_TIMEOUT}s - "
                        f"endpoint may still be starting up (scale-up timeout was {HUGGINGFACE_SCALE_UP_TIMEOUT}s)")
            return ""
        except requests.exceptions.RequestException as e:
            logger.error(f"HuggingFace API request error: {e}", exc_info=True)
            return ""
        except Exception as e:
            logger.error(f"Error during HuggingFace transcription: {e}", exc_info=True)
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

            logger.info(f"Testing HuggingFace with file: {test_file_path} ({duration:.1f}s)")

            # Transcribe
            text = self.transcribe(test_file_path, duration)

            if text:
                logger.info(f"HuggingFace test transcription successful: {len(text)} chars")
                return text
            else:
                logger.warning("HuggingFace test transcription returned empty text")
                return None

        except Exception as e:
            logger.error(f"HuggingFace test transcription failed: {e}", exc_info=True)
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

    Threading model:
    - RecordingLoop thread (thoughtborne.py) calls send_audio_chunk() for each audio chunk
    - Internal receiver thread reads WebSocket responses and collects final tokens
    - transcribe() sends finalize and waits for receiver to complete

    Additional methods beyond AbstractTranscriber:
    - start_session(): Open WebSocket, send config, start receiver thread
    - send_audio_chunk(raw_data): Send PCM bytes during recording
    - cancel_session(): Close WebSocket immediately (on recording cancel)
    """

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

        # Drop diagnostic: WebSocket send latency tracking.
        # A blocking send (>100ms) is the direct indicator of TCP backpressure,
        # which in the current single-thread architecture stalls the recording
        # loop and causes mic drops. Reset on each new session.
        self._send_latency_max = 0.0
        self._send_latency_blocked_total = 0.0
        self._send_latency_blocked_count = 0

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

    def start_session(self) -> bool:
        """Open WebSocket connection and start receiver thread.

        Called from on_start_recording() when this transcriber is active.

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
                    "sample_rate": RATE,
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
                self._session_active = True

                # Reset send-latency diagnostic stats for the new session
                self._send_latency_max = 0.0
                self._send_latency_blocked_total = 0.0
                self._send_latency_blocked_count = 0

                # Start receiver thread
                self._receiver_thread = threading.Thread(
                    target=self._receiver_loop,
                    daemon=True,
                    name="SonioxLive-Receiver"
                )
                self._receiver_thread.start()

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
        """Send raw PCM audio bytes to the WebSocket.

        Called from recording_loop_thread() for each record_chunk().
        Thread-safe: called from RecordingLoop thread while receiver runs.

        Args:
            raw_data: Raw PCM bytes (16-bit signed LE, mono, 44100 Hz)
        """
        if not self._session_active or self._ws is None:
            return

        try:
            # Drop diagnostic: measure send duration. A blocking send (TCP
            # backpressure) is the direct cause of recording-loop stalls in
            # the current single-thread architecture.
            send_start = time.perf_counter()
            self._ws.send(raw_data)
            send_elapsed = time.perf_counter() - send_start

            if send_elapsed > self._send_latency_max:
                self._send_latency_max = send_elapsed
            if send_elapsed > 0.01:  # >10ms: track as "blocked"
                self._send_latency_blocked_total += send_elapsed
                self._send_latency_blocked_count += 1
            if send_elapsed > 0.1:  # >100ms: warn (likely TCP backpressure)
                logger.warning(
                    f"Slow WebSocket send: {send_elapsed*1000:.0f}ms "
                    f"(TCP backpressure - recording loop is blocked while this returns)"
                )
        except Exception as e:
            logger.error(f"Error sending audio chunk: {e}")

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

        if not self._session_active or self._ws is None:
            logger.warning("No active Soniox Live session to finalize")
            return ""

        start_time = time.time()

        try:
            # Step 1: Send silence before finalize (helps model accuracy)
            silence_samples = int(RATE * SONIOX_LIVE_FINALIZE_DELAY)
            silence_bytes = b'\x00' * (silence_samples * 2)  # 16-bit = 2 bytes per sample
            self._ws.send(silence_bytes)
            logger.debug(f"Sent {len(silence_bytes)} bytes of silence")

            # Step 2: Send finalize command
            self._ws.send(json.dumps({"type": "finalize"}))
            logger.debug("Sent finalize command")

            # Step 3: Send empty string (end-of-stream)
            self._ws.send("")
            logger.debug("Sent end-of-stream")

            # Step 4: Wait for receiver to finish
            if not self._result_ready.wait(timeout=SONIOX_LIVE_FINALIZE_TIMEOUT):
                logger.error(f"Soniox Live finalize timeout after {SONIOX_LIVE_FINALIZE_TIMEOUT}s")
                return ""

            # Step 5: Check for errors
            if self._session_error:
                logger.error(f"Soniox Live session had error: {self._session_error}")
                return ""

            # Step 6: Assemble text from final tokens
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
        """Close the WebSocket and clean up session state."""
        # Log send-latency diagnostics if any traffic was measured
        if self._send_latency_max > 0:
            logger.info(
                f"Session send-latency stats: "
                f"max={self._send_latency_max*1000:.0f}ms, "
                f"blocked-events(>10ms)={self._send_latency_blocked_count}, "
                f"total-blocked-time={self._send_latency_blocked_total:.2f}s"
            )

        self._session_active = False

        if self._ws is not None:
            try:
                self._ws.close()
                logger.debug("WebSocket closed")
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
            self._ws = None

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

            # Start session
            if not self.start_session():
                logger.error("Failed to start Soniox Live test session")
                return None

            # Stream audio in chunks (approximate real-time pacing)
            from config import CHUNK
            audio_bytes = data.tobytes()
            bytes_per_chunk = CHUNK * 2  # 16-bit = 2 bytes per sample

            for i in range(0, len(audio_bytes), bytes_per_chunk):
                chunk = audio_bytes[i:i + bytes_per_chunk]
                self.send_audio_chunk(chunk)
                time.sleep(CHUNK / RATE)  # Approximate real-time pacing

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
        api_name: Name of the API to use ('modal', 'huggingface', 'groq', 'soniox',
                  'soniox-v4', or 'soniox-live')

    Returns:
        Transcriber instance

    Raises:
        ValueError: If api_name is not supported
    """
    if api_name == "modal":
        return ModalParakeetTranscriber()
    elif api_name == "huggingface":
        return HuggingFaceTranscriber()
    elif api_name == "groq":
        return GroqTranscriber()
    elif api_name == "soniox":
        return SonioxTranscriber()
    elif api_name == "soniox-v4":
        return SonioxV4Transcriber()
    elif api_name == "soniox-live":
        return SonioxLiveTranscriber()
    else:
        raise ValueError(f"Unknown API: {api_name}. Supported: modal, huggingface, groq, soniox, soniox-v4, soniox-live")