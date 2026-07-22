"""
Transcriber Module

This module handles speech-to-text transcription using multiple APIs.
It provides a clean interface for transcribing audio files using
Groq (Whisper Large V3 / Large V3 Turbo) and Soniox (V2 sync, V4 async, Live streaming).

Classes:
    AbstractTranscriber: Base class for all transcriber implementations
    GroqTranscriber: Handles transcription using Groq API
    SonioxTranscriber: Handles transcription using Soniox API
"""

import os
import re
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
    GROQ_MODEL, GROQ_LARGE_MODEL, LANGUAGE, TEXT_ARCHIVE_FOLDER,
    GROQ_API_KEY, SONIOX_API_KEY, SONIOX_MODEL, API_DISPLAY, ENGINE_TOKENS,
    SHORT_AUDIO_THRESHOLD, SONIOX_V2_CONTEXT_BOOST,
    SONIOX_ASYNC_API_BASE, SONIOX_ASYNC_MODEL, SONIOX_ASYNC_POLL_INTERVAL,
    SONIOX_ASYNC_MAX_POLL_ATTEMPTS,
    SONIOX_WS_URL, SONIOX_RT_MODEL, SONIOX_LIVE_FINALIZE_DELAY,
    SONIOX_LIVE_FINALIZE_TIMEOUT,
    SONIOX_LIVE_QUEUE_MAX_CHUNKS, SONIOX_LIVE_SENDER_JOIN_TIMEOUT,
    SONIOX_LIVE_FINALIZE_DRAIN_TIMEOUT,
    SONIOX_LANGUAGE_HINTS, SONIOX_CONTEXT, soniox_live_endpointing_params,
    RATE, CHANNELS, FILE_ONLY,
)

logger = logging.getLogger('Thoughtborne.Transcriber')


class MissingAPIKeyError(ValueError):
    """Raised at transcriber construction when the required API key env var
    is not set (#40). Subclasses ValueError so existing handlers keep working;
    carries the env var name so callers (carousel skip, startup fallback) can
    say precisely which key is missing."""

    def __init__(self, env_var: str, transcriber_label: str):
        self.env_var = env_var
        super().__init__(f"{env_var} is required for {transcriber_label}")


class _EngineTag:
    """Mutable one-shot holder so SonioxTranscriber.transcribe can report which
    engine (V2 sync vs. V4 async) actually produced the text (#62), without
    changing the ABC's `transcribe() -> str` contract. The worker allocates one
    per call and reads .code afterwards, so it is inherently thread-safe across
    the parallel transcriptions -- unlike a mutable attribute on the shared
    transcriber singleton."""
    __slots__ = ("code",)

    def __init__(self):
        self.code = None


class _ErrorTag:
    """Mutable one-shot holder so an engine's transcribe can report that the call
    failed with a transport/API error rather than completing clean-but-empty
    (#141), without changing the ABC's `transcribe() -> str` contract. Every
    engine sets it on its error paths since #138, so a clean-but-empty result is
    told apart from an outage on every slot -- what generalizes the honest
    no-speech verdict beyond Soniox Live.

    Same shape and thread-safety rationale as _EngineTag above: the caller
    allocates one per call and reads .errored / .reason afterwards, so it is safe
    across the parallel transcriptions -- unlike a mutable attribute on the
    shared transcriber singleton.

    reason is a coarse category for #159's panel, meaningful only when errored:
    "auth" | "no-connection" | "rate-limited" | "service-error" (unknown errors
    default to "service-error"); None on a clean run."""
    __slots__ = ("errored", "reason")

    def __init__(self):
        self.errored = False
        self.reason = None


def _one_line_error(error: BaseException) -> str:
    """Collapse an exception to one console-safe line (#124).

    Some exceptions render str() as a multi-line block -- notably gRPC's
    _InactiveRpcError from the Soniox V2 sync path, whose str() spans status /
    details / debug_error_string across ~8 lines. Interpolated straight into a
    console log message, that reintroduces the multi-line console flood #117
    removed for tracebacks, pushing the FAILED panel off screen. Callers keep
    exc_info=True, so thoughtborne.log still records the full exception; this is
    only what the receding console one-liner shows.

    A gRPC RpcError exposes code()/details() (duck-typed so the optional grpc
    import is never forced here): we surface the status code plus the first line
    of its details. Everything else collapses to the exception class name plus
    the first non-empty line of str().
    """
    code = getattr(error, "code", None)
    details = getattr(error, "details", None)
    if callable(code) and callable(details):
        try:
            status = code().name
            detail_lines = (details() or "").strip().splitlines()
            first = detail_lines[0].strip() if detail_lines else ""
            return f"{status}: {first}" if first else status
        except Exception:
            pass
    text = str(error).strip()
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return f"{type(error).__name__}: {first}" if first else type(error).__name__


# ---- coarse error categories for the per-call sink (#138) ------------------
# Each maps a provider-specific failure onto one of the four _ErrorTag reasons
# (auth / no-connection / rate-limited / service-error). Unknown -> service-error.

# Class names, not isinstance, so neither grpc nor httpx is force-imported here
# and a partially-stubbed httpx (as in the tests) is never attribute-probed. A
# builtin ConnectionError/TimeoutError from a raw socket layer maps too.
_CONNECTION_ERROR_NAMES = frozenset({
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "PoolTimeout", "TimeoutException", "RemoteProtocolError",
    "ConnectionError", "TimeoutError",
})


def _grpc_error_reason(e) -> str:
    """gRPC RpcError -> coarse reason (#138). Duck-typed so the optional grpc
    import is never forced; a non-gRPC exception falls through to service-error."""
    code = getattr(e, "code", None)
    name = ""
    if callable(code):
        try:
            name = code().name
        except Exception:
            name = ""
    return {
        "UNAUTHENTICATED": "auth",
        "UNAVAILABLE": "no-connection",
        "DEADLINE_EXCEEDED": "no-connection",
        "RESOURCE_EXHAUSTED": "rate-limited",
    }.get(name, "service-error")


def _groq_error_reason(e) -> str:
    """Groq SDK exception -> coarse reason (#138). The import is lazy so the
    module never depends on these symbols at import time."""
    from groq import (AuthenticationError, APIConnectionError, APITimeoutError,
                      RateLimitError, APIStatusError)
    if isinstance(e, AuthenticationError):
        return "auth"
    if isinstance(e, (APIConnectionError, APITimeoutError)):
        return "no-connection"
    if isinstance(e, RateLimitError):
        return "rate-limited"
    if isinstance(e, APIStatusError):
        sc = getattr(e, "status_code", None)
        if sc == 401:
            return "auth"
        if sc == 429:
            return "rate-limited"
        return "service-error"     # 5xx and other 4xx incl. 402 (credits)
    return "service-error"


def _http_status_reason(status_code) -> str:
    """Soniox V4 httpx status code -> coarse reason (#138)."""
    if status_code == 401:
        return "auth"
    if status_code == 429:
        return "rate-limited"
    return "service-error"         # 5xx / 402 / other 4xx


def _httpx_exc_reason(e) -> str:
    """A generic exception raised in the Soniox V4 REST path -> coarse reason
    (#138). A connect/timeout error (httpx's or the builtin) is no-connection;
    everything else is service-error."""
    return "no-connection" if type(e).__name__ in _CONNECTION_ERROR_NAMES else "service-error"


class AbstractTranscriber(ABC):
    """Abstract base class for all transcriber implementations"""
    
    def __init__(self):
        """Initialize the transcriber"""
        self._ensure_directories()
    
    def _ensure_directories(self):
        """Create text archive directory if it doesn't exist"""
        TEXT_ARCHIVE_FOLDER.mkdir(parents=True, exist_ok=True)
        logger.info(f"Text archive folder ready: {TEXT_ARCHIVE_FOLDER}", extra=FILE_ONLY)

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
    
    def save_transcript(self, text: str, timestamp: str, engine: Optional[str] = None) -> Optional[str]:
        """
        Save transcript to text archive

        Args:
            text: Transcribed text to save
            timestamp: Timestamp for filename
            engine: Producing-engine token (#62), appended as
                text_<ts>_<engine>.txt so the archive shows which engine made
                the text. None or empty keeps the legacy text_<ts>.txt name --
                a byte-identical, defensive default.

        Returns:
            Path to saved file or None if failed
        """
        if not text:
            return None

        try:
            stem = f"text_{timestamp}_{engine}" if engine else f"text_{timestamp}"
            filename = TEXT_ARCHIVE_FOLDER / f"{stem}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(text)
            
            logger.info(f"Text archived: {filename} ({len(text)} chars)", extra=FILE_ONLY)
            return str(filename)
            
        except Exception as e:
            logger.error(f"Failed to save transcript: {e}")
            return None

    # Marks that end a sentence, so a capitalized filler right after one is
    # genuinely sentence-initial. ":" is included on purpose: the model
    # capitalizes a filler after a colon when it treats what follows as a new
    # sentence, and keeping that move leaves Live output unchanged (#97
    # measurement: 2 such cases, all currently capitalized). Widening this set
    # can never add a wrong capital -- the move is already gated on the filler
    # itself being uppercase (a strict subset of the pre-#97 always-move).
    _SENTENCE_END = frozenset(".!?…:")
    # Closing quotes/brackets skipped through on the backward look: they can sit
    # between a sentence-ender and the next sentence's first token
    # (SATZ." Ähm ...), so they must not hide the sentence start. Live emits the
    # straight " ; the rest are defensive and semantically transparent.
    _CAP_TRANSPARENT = frozenset('"' + "'" + '”’»)]}')

    def _at_sentence_start(self, text: str, idx: int) -> bool:
        """True if position idx begins a new sentence: scanning left past
        whitespace and transparent closing quotes/brackets reaches a
        sentence-ending mark (. ! ? …), a colon, or the start of the text."""
        j = idx - 1
        while j >= 0 and (text[j].isspace() or text[j] in self._CAP_TRANSPARENT):
            j -= 1
        return j < 0 or text[j] in self._SENTENCE_END

    def _quoted_spans(self, text: str) -> list[tuple[int, int]]:
        """Index pairs (open, close) of straight-double-quote spans, matched
        positionally (1st-2nd, 3rd-4th, ...). A lone unpaired quote is dropped:
        it protects nothing (#101), so a stray '"' can never switch the filler
        filter off for everything that follows it."""
        quotes = [i for i, ch in enumerate(text) if ch == '"']
        return [(quotes[i], quotes[i + 1]) for i in range(0, len(quotes) - 1, 2)]

    def _remove_spoken_fillers(self, transcript: str) -> str:
        """Remove spoken hesitation fillers ("ähm"/"äh") that some engines
        transcribe verbatim (#31; generalized to every engine in #97).

        Additions-only counterpart to the V2/Groq end-artifact filters (which
        stay untouched). Origin: the #31 quality gate
        (_research/2026-06_soniox-v2async-vs-v4-quality/) found 172 inline
        fillers on the Soniox V4 async path, exclusively the forms "ähm"/"äh".
        A larger Soniox Live sample (#97) confirmed the same two forms and
        nothing else, delimited almost always by a following comma, rarely a
        period (the older V4 corpus also showed the three-dot ellipsis).
        Removal drops the filler plus its immediately following delimiter and
        spacing, re-capitalizes the next word when a sentence-initial
        capitalized filler preceded a lowercase one, and trims a comma left
        dangling at the very end. The capital is moved only at a genuine
        sentence start (see _at_sentence_start), so a filler capitalized
        mid-clause can never push a wrong capital onto the following word.
        Fillers the user deliberately quotes are exempt: a filler whose match
        falls inside a balanced pair of straight double quotes ("...") is left
        verbatim (#101), so 'Er sagte "ähm".' keeps its quoted word instead of
        collapsing to '""'. Quote pairs are taken positionally (1st-2nd,
        3rd-4th, ...); a lone unpaired quote protects nothing, so a stray quote
        character cannot silently switch the filter off for the rest of the
        transcript.
        Deliberately not filtered: any other form -- "eh"/"hm"/"naja" are real
        words or meaning-bearing particles, not hesitation noise.
        """
        if not transcript:
            return transcript

        original = transcript
        # Word-boundary matching is load-bearing: umlauts are \w in Python's
        # unicode re, so "ähm" inside "Lähmung" or "äh" inside "ähnlich"
        # cannot match.
        filler_re = re.compile(r"\b(?:ähm|äh)\b", re.IGNORECASE)
        # Delimiter the model attaches to the filler itself (corpus-exact:
        # comma, three-dot ellipsis, or period) plus the gluing whitespace.
        trail_re = re.compile(r"(?:\.{3}|[.,])?[ \t]*")
        quoted_spans = self._quoted_spans(transcript)

        out = []
        pos = 0
        removed = 0
        ends_at_eos = False
        cap_pending = False
        for m in filler_re.finditer(transcript):
            if m.start() < pos:
                continue
            if any(a < m.start() < b for a, b in quoted_spans):
                # Filler inside a deliberate quote -- leave it verbatim (#101).
                continue
            if transcript[pos:m.start()]:
                # Real text in between -- a pending capitalization from an
                # earlier filler chain cannot reach across it.
                cap_pending = False
            end = trail_re.match(transcript, m.end()).end()
            out.append(transcript[pos:m.start()])
            removed += 1
            logger.debug(f"Removed spoken filler: '{transcript[m.start():end]}' at {m.start()}")
            cap_pending = cap_pending or (
                m.group(0)[0].isupper()
                and self._at_sentence_start(transcript, m.start())
            )
            if (cap_pending and end < len(transcript) and transcript[end].islower()
                    and not filler_re.match(transcript, end)):
                # The sentence-initial filler (or filler chain, "Äh, ähm, ...")
                # carried the capitalization -- move it to the word that now
                # starts the sentence. When another filler follows directly,
                # defer the move until the chain ends, so the flip never eats
                # the next filler's first letter.
                out.append(transcript[end].upper())
                end += 1
                cap_pending = False
            pos = end
            ends_at_eos = pos >= len(transcript)
        if not removed:
            return transcript

        out.append(transcript[pos:])
        text = "".join(out)
        if ends_at_eos:
            # A filler removed at the very end can leave ", " dangling.
            text = text.rstrip()
            if text.endswith(","):
                text = text[:-1].rstrip()

        logger.debug(f"Removed {removed} spoken filler(s): {len(original)} -> {len(text)} chars")
        return text


class GroqTranscriber(AbstractTranscriber):
    """Handles transcription using Groq Whisper API.

    Serves both Whisper variants ('groq' = Large V3 Turbo, 'groq-large' =
    full Large V3, #36): same endpoint, same auth, same hallucination
    artifact class — so one class parameterized by model instead of a subclass.
    """

    def __init__(self, model: str = GROQ_MODEL, display_name: str = API_DISPLAY["groq"]["label"]):
        """Initialize the transcriber with API key"""
        super().__init__()
        self.model = model
        self.display_name = display_name
        self.api_key = self._get_api_key()
        self.client = None
        self._initialize_client()
    
    def _get_api_key(self) -> str:
        """Get API key from environment"""
        if not GROQ_API_KEY:
            logger.debug("GROQ_API_KEY not found in environment variables!")
            raise MissingAPIKeyError("GROQ_API_KEY", "Groq transcriber")

        logger.info("Using Groq API key from environment", extra=FILE_ONLY)
        return GROQ_API_KEY
    
    def _initialize_client(self):
        """Initialize Groq client"""
        try:
            self.client = Groq(api_key=self.api_key)
            logger.info("Groq client initialized successfully", extra=FILE_ONLY)
        except Exception as e:
            logger.error(f"Failed to initialize Groq client: {e}")
            raise
    
    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return self.display_name
    
    def _clean_groq_hallucinations(self, transcript: str) -> str:
        """
        Remove common hallucination patterns from Groq transcriptions

        Groq (Whisper v3 Turbo) often adds "Vielen Dank" or similar phrases
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
                    logger.debug(f"Removed Groq hallucination: '{pattern}' at end")
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
                logger.debug(f"Removed Groq hallucination: '{pattern}' at end")
                break

        if transcript != original:
            logger.debug(f"Original ending: ...'{original[-30:]}'")
            logger.debug(f"Cleaned to: ...'{transcript[-30:]}'")

        return transcript
    
    def transcribe(self, audio_file_path: str, duration_seconds: float, *,
                   error_sink: Optional['_ErrorTag'] = None) -> str:
        """
        Transcribe an audio file using Groq Whisper API

        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds
            error_sink: Optional _ErrorTag the caller reads afterwards to learn
                that this call failed with a transport/API error instead of
                completing clean-but-empty (#138). Set on every error path with a
                coarse reason; never set on a completed run, however empty.

        Returns:
            Transcribed text
        """
        logger.info(f"Starting Groq transcription ({self.model}): {audio_file_path} (Duration: {duration_seconds:.1f}s)", extra=FILE_ONLY)
        
        try:
            start_time = time.time()
            
            # Open and transcribe audio file
            with open(audio_file_path, "rb") as audio_file:
                transcription = self.client.audio.transcriptions.create(
                    file=audio_file,
                    model=self.model,
                    language=LANGUAGE,
                    response_format="text"
                )
            
            elapsed = time.time() - start_time
            logger.info(f"Groq transcription successful in {elapsed:.2f}s", extra=FILE_ONLY)
            
            # Groq returns text directly when response_format="text"
            text = transcription.strip()
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")
            
            # Clean hallucinations
            text = self._clean_groq_hallucinations(text)
            # Clean fillers
            text = self._remove_spoken_fillers(text)

            return text
            
        except AuthenticationError as e:
            # Groq nests the error code under body['error']['code'], so top-level
            # body['code'] is None; match str(e) instead -- server behavior, not SDK version.
            detail = "expired" if "expired_api_key" in str(e) else "invalid"
            self._report_auth_failure("GROQ_API_KEY", detail)
            # DEBUG keeps the trace file-only so the [AUTH] line stands alone
            # on the console (#32)
            logger.debug(f"Error during Groq transcription: {e}", exc_info=True)
            if error_sink is not None:
                error_sink.errored, error_sink.reason = True, "auth"
            return ""
        except Exception as e:
            logger.error(f"Error during Groq transcription: {e}", exc_info=True)
            if error_sink is not None:
                error_sink.errored, error_sink.reason = True, _groq_error_reason(e)
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
            
            logger.info(f"Testing {self.get_name()} with file: {test_file_path} ({duration:.1f}s)")
            
            # Transcribe
            text = self.transcribe(test_file_path, duration)
            
            if text:
                logger.info(f"Groq test transcription successful: {len(text)} chars", extra=FILE_ONLY)
                return text
            else:
                logger.warning("Groq test transcription returned empty text")
                return None
                
        except Exception as e:
            logger.error(f"Groq test transcription failed: {e}", exc_info=True)
            return None


def _soniox_engine_choice_line(v2_available: bool, duration_seconds: float) -> str:
    """One terse, console-safe line naming the Soniox file engine this slot will
    run for a duration_seconds recording, plus the reason (#125).

    Pure and side-effect-free so it is unit-testable without an API key or a
    constructed transcriber (same rationale as _terms_to_speech_context). A
    read-only mirror of the transcribe() decision (v2_available and
    duration_seconds < SHORT_AUDIO_THRESHOLD); the caller emits it on the dim
    ticker at decision time so the otherwise-silent V2-sync-vs-async pick becomes
    visible with its "why". The shown second count is truncated (int()), never
    rounded, so a sub-threshold duration can never read a contradictory
    "58 s -> under 58 s".
    """
    secs = int(duration_seconds)  # truncate toward zero -- see docstring
    if not v2_available:
        return f"audio {secs} s -> SDK unavailable -> Soniox async"
    if duration_seconds < SHORT_AUDIO_THRESHOLD:
        return f"audio {secs} s -> under {SHORT_AUDIO_THRESHOLD} s -> Soniox V2 (sync)"
    return f"audio {secs} s -> {SHORT_AUDIO_THRESHOLD} s or longer -> Soniox async"


class SonioxTranscriber(AbstractTranscriber):
    """Soniox file-upload slot: V2 sync for short recordings with automatic
    V4 async REST fallback; V4 async REST for long recordings (#31)."""

    def __init__(self):
        """Initialize the transcriber with API key"""
        super().__init__()
        self.api_key = self._get_api_key()
        # Module-internal contract: thoughtborne.py's _try_fallback reads this
        # flag to skip the empty-live cascade's V2 stage when the SDK is missing.
        self._v2_available = self._check_soniox_availability()
        # Eager init is safe: the V4 constructor only builds a header dict,
        # and _get_api_key() above already raised if the key is missing.
        self._v4 = SonioxV4Transcriber()
        # V2 SpeechContext is constant per session -> build it once here and
        # reuse it on every _transcribe_v2_sync call (#73). None means "send no
        # context" (no SDK, no personal_settings.json, or no usable terms), in
        # which case V2 sync stays byte-identical to pre-#73. Never fatal: a
        # broken vocabulary must not break slot construction or transcription.
        self._v2_speech_context = None
        if self._v2_available:
            try:
                self._v2_speech_context = self._terms_to_speech_context(
                    (SONIOX_CONTEXT or {}).get("terms")
                )
            except Exception as e:
                logger.warning(f"Soniox V2 speech context disabled (build failed): {e}")
            if self._v2_speech_context is not None:
                n = len(self._v2_speech_context.entries[0].phrases)
                logger.info(f"Soniox V2 sync context enabled: {n} terms", extra=FILE_ONLY)

    def _get_api_key(self) -> str:
        """Get API key from environment"""
        if not SONIOX_API_KEY:
            logger.debug("SONIOX_API_KEY not found in environment variables!")
            raise MissingAPIKeyError("SONIOX_API_KEY", "Soniox transcriber")
        
        logger.info("Using Soniox API key from environment", extra=FILE_ONLY)
        return SONIOX_API_KEY
    
    def _check_soniox_availability(self) -> bool:
        """Probe for the legacy Soniox 1.x SDK (V2 sync path).

        A missing SDK is no longer fatal (#31): the slot then serves every
        recording via the V4 async REST engine -- slower start-to-text,
        but functional.
        """
        try:
            from soniox.speech_service import SpeechClient
            from soniox.transcribe_file import transcribe_file_short
            logger.info("Soniox library available", extra=FILE_ONLY)
            return True
        except ImportError:
            logger.warning(
                "Soniox SDK not installed -- 'soniox' uses V4 async REST for "
                "all recordings (install the 'soniox' package for the fast "
                "sync path)"
            )
            return False
    
    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return API_DISPLAY["soniox"]["label"]

    def engine_choice_line(self, duration_seconds: float) -> str:
        """Ticker line naming the file engine transcribe() will pick for this
        duration and why (#125). Read-only; changes nothing, decides nothing."""
        return _soniox_engine_choice_line(self._v2_available, duration_seconds)

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
    
    @staticmethod
    def _terms_to_speech_context(terms):
        """Translate vocabulary phrase strings into a V2 SpeechContext, or None (#73).

        Pure and side-effect-free so it can be unit-tested without an API key or a
        constructed transcriber. Lazy-imports soniox so nothing soniox-typed sits on
        the module-import path -- a missing SDK must stay non-fatal (#31). The legacy
        gRPC SDK requires a genuine SpeechContext protobuf (transcribe_file_short
        asserts isinstance), unlike the v4/Live REST paths that pass the raw
        SONIOX_CONTEXT dict straight through. Non-string / empty entries are dropped:
        the protobuf phrases field is a repeated string and would raise TypeError on a
        non-str element; an empty or all-invalid list yields None (send no context).
        """
        phrases = [t for t in (terms or []) if isinstance(t, str) and t]
        if not phrases:
            return None
        from soniox.speech_service import SpeechContext, SpeechContextEntry
        return SpeechContext(
            entries=[SpeechContextEntry(phrases=phrases, boost=SONIOX_V2_CONTEXT_BOOST)]
        )

    def _transcribe_v2_sync(self, audio_file_path: str, duration_seconds: float) -> str:
        """V2 sync attempt (gRPC, transcribe_file_short). Raises on any failure.

        Module-internal contract: also called directly by the empty-live
        fallback cascade in thoughtborne.py, which needs the raw V2 result
        without the slot's V4 fallback wrapped around it (#31).
        """
        if not self._v2_available:
            raise RuntimeError("Soniox SDK not installed")

        from soniox.speech_service import SpeechClient
        from soniox.transcribe_file import transcribe_file_short

        logger.info("Using synchronous Soniox transcription", extra=FILE_ONLY)

        # Create new client for each transcription to avoid connection issues
        logger.debug("Creating new SpeechClient for synchronous transcription")
        client = SpeechClient()

        try:
            start_time = time.time()

            result = transcribe_file_short(
                audio_file_path,
                client,
                model=SONIOX_MODEL,
                speech_context=self._v2_speech_context,  # None -> byte-identical to pre-#73
            )

            elapsed = time.time() - start_time
            logger.info(f"Soniox synchronous transcription successful in {elapsed:.2f}s", extra=FILE_ONLY)

            text = "".join(word.text for word in result.words)
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")

            # Clean hallucinations
            text = self._clean_transcript_hallucinations(text)
            # Clean fillers
            text = self._remove_spoken_fillers(text)

            return text.strip()

        finally:
            # Close client
            try:
                client.close()
                logger.debug("Soniox synchronous client closed")
            except Exception as e:
                logger.warning(f"Error closing Soniox sync client: {e}")

    @staticmethod
    def _is_auth_error(e: Exception) -> bool:
        """True if e is a gRPC UNAUTHENTICATED error from the V2 SDK."""
        try:
            import grpc
        except ImportError:
            return False
        return isinstance(e, grpc.RpcError) and e.code() == grpc.StatusCode.UNAUTHENTICATED

    def transcribe(self, audio_file_path: str, duration_seconds: float, *,
                   engine_sink: Optional['_EngineTag'] = None,
                   error_sink: Optional['_ErrorTag'] = None) -> str:
        """Transcribe an audio file via the hybrid V2-sync/V4-async slot (#31).

        Recordings under SHORT_AUDIO_THRESHOLD run V2 sync exactly as before
        and fall back to V4 async REST when V2 raises (except on auth errors).
        Long recordings, and every recording when the V2 SDK is missing, go
        straight to V4 async REST.

        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds
            engine_sink: Optional _EngineTag the caller reads afterwards to learn
                which engine won this call (#62) -- ENGINE_TOKENS["soniox_v2"] on
                the V2 sync path, ENGINE_TOKENS["soniox_v4"] whenever V4 async
                produced the text (long recording, missing SDK, or V2-raised
                fallback). Only set on a returned result the
                caller keeps; the caller ignores it on an empty transcript.
            error_sink: Optional _ErrorTag the caller reads afterwards to tell a
                clean-but-empty slot result from a transport/API outage (#138). A
                clean V2 empty leaves it untouched (genuine silence); V2 auth sets
                it; whenever the internal V4 stage runs it owns the sink, so a V4
                outage on the slot surfaces here instead of masquerading as clean.

        Returns:
            Transcribed text
        """
        logger.info(f"Starting Soniox transcription: {audio_file_path} "
                    f"(Duration: {duration_seconds:.1f}s)", extra=FILE_ONLY)

        if self._v2_available and duration_seconds < SHORT_AUDIO_THRESHOLD:
            try:
                # An empty V2 result without an exception (usually silence) is
                # returned as-is -- no V4 hop on the slot path. The empty-live
                # cascade in thoughtborne.py keeps its own empty -> V4
                # fall-through one level up (#31).
                result = self._transcribe_v2_sync(audio_file_path, duration_seconds)
                if engine_sink is not None:
                    engine_sink.code = ENGINE_TOKENS["soniox_v2"]
                return result
            except Exception as e:
                if self._is_auth_error(e):
                    # V4 uses the same SONIOX_API_KEY, so a fallback would just
                    # produce a second 401 and a duplicate [AUTH] line (#32).
                    self._report_auth_failure("SONIOX_API_KEY")
                    logger.debug(f"Error during Soniox transcription: {e}", exc_info=True)
                    if error_sink is not None:
                        error_sink.errored, error_sink.reason = True, "auth"
                    return ""
                try:
                    reason = e.code().name  # grpc.RpcError carries the status
                except Exception:
                    reason = type(e).__name__
                logger.warning(
                    f"[FALLBACK] Soniox V2 sync failed ({reason}) -- "
                    f"retrying with {self._v4.get_name()} (slower)"
                )
                # DEBUG keeps the trace file-only so the [FALLBACK] line stands
                # alone on the console; V4's own handlers log ERROR if the
                # fallback fails too.
                logger.debug(f"V2 sync failure detail: {e}", exc_info=True)
                if engine_sink is not None:
                    engine_sink.code = ENGINE_TOKENS["soniox_v4"]
                # The V2 failure's own category is deliberately not stamped here:
                # if V4 recovers clean-empty it was genuine silence, and if V4
                # errors its sink wins (#138).
                return self._v4.transcribe(audio_file_path, duration_seconds, error_sink=error_sink)

        if duration_seconds >= SHORT_AUDIO_THRESHOLD:
            logger.info(f"Long recording ({duration_seconds:.1f}s >= "
                        f"{SHORT_AUDIO_THRESHOLD}s) -- using Soniox V4 (async REST)", extra=FILE_ONLY)
        if engine_sink is not None:
            engine_sink.code = ENGINE_TOKENS["soniox_v4"]
        return self._v4.transcribe(audio_file_path, duration_seconds, error_sink=error_sink)

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
            
            logger.info(f"Testing {self.get_name()} with file: {test_file_path} ({duration:.1f}s)")
            
            # Transcribe
            text = self.transcribe(test_file_path, duration)
            
            if text:
                logger.info(f"Soniox test transcription successful: {len(text)} chars", extra=FILE_ONLY)
                return text
            else:
                logger.warning("Soniox test transcription returned empty text")
                return None
                
        except Exception as e:
            logger.error(f"Soniox test transcription failed: {e}", exc_info=True)
            return None


class SonioxV4Transcriber(AbstractTranscriber):
    """Handles transcription using Soniox Async REST API.

    Workflow: Upload file → Create transcription → Poll status → Get result → Cleanup.
    Uses httpx for HTTP requests. No Soniox SDK needed.
    Context feature enabled for better recognition of domain terms and proper nouns.
    """

    def __init__(self):
        """Initialize the transcriber with API key"""
        super().__init__()
        if not SONIOX_API_KEY:
            raise MissingAPIKeyError("SONIOX_API_KEY", "Soniox v4 transcriber")
        self.api_key = SONIOX_API_KEY
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
        logger.info(f"Soniox async transcriber initialized (model: {SONIOX_ASYNC_MODEL})", extra=FILE_ONLY)
        if SONIOX_CONTEXT:
            logger.info(f"Context enabled: {len(SONIOX_CONTEXT.get('terms', []))} terms", extra=FILE_ONLY)

    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return "Soniox async"

    def transcribe(self, audio_file_path: str, duration_seconds: float, *,
                   error_sink: Optional['_ErrorTag'] = None) -> str:
        """Transcribe an audio file using Soniox Async REST API.

        Args:
            audio_file_path: Path to the audio file
            duration_seconds: Duration of the audio in seconds
            error_sink: Optional _ErrorTag the caller reads afterwards to learn
                that this call failed with a transport/API error instead of
                completing clean-but-empty (#141), with a coarse reason category
                (#138). Set on every error path (job status error, poll timeout,
                HTTP error, unexpected exception); never set on a completed run,
                however empty the text.

        Returns:
            Transcribed text
        """
        import httpx

        logger.info(f"Starting Soniox v4 transcription: {audio_file_path} "
                    f"(Duration: {duration_seconds:.1f}s)", extra=FILE_ONLY)

        file_id = None
        tx_id = None
        start_time = time.time()

        try:
            # Step 1: Upload file
            with open(audio_file_path, "rb") as f:
                resp = httpx.post(
                    f"{SONIOX_ASYNC_API_BASE}/v1/files",
                    headers=self.headers,
                    files={"file": (os.path.basename(audio_file_path), f)},
                    timeout=60
                )
            resp.raise_for_status()
            file_id = resp.json()["id"]
            upload_time = time.time() - start_time
            logger.info(f"File uploaded in {upload_time:.2f}s, ID: {file_id}", extra=FILE_ONLY)

            # Step 2: Create transcription
            tx_config = {
                "model": SONIOX_ASYNC_MODEL,
                "file_id": file_id,
                "language_hints": SONIOX_LANGUAGE_HINTS,
            }
            if SONIOX_CONTEXT:
                tx_config["context"] = SONIOX_CONTEXT

            resp = httpx.post(
                f"{SONIOX_ASYNC_API_BASE}/v1/transcriptions",
                headers=self.headers,
                json=tx_config,
                timeout=30
            )
            resp.raise_for_status()
            tx_id = resp.json()["id"]
            logger.info(f"Transcription created, ID: {tx_id}", extra=FILE_ONLY)

            # Step 3: Poll until completed
            for attempt in range(SONIOX_ASYNC_MAX_POLL_ATTEMPTS):
                resp = httpx.get(
                    f"{SONIOX_ASYNC_API_BASE}/v1/transcriptions/{tx_id}",
                    headers=self.headers,
                    timeout=15
                )
                resp.raise_for_status()
                status = resp.json()["status"]

                if attempt % 10 == 0 and attempt > 0:
                    logger.debug(f"Polling attempt {attempt}: status={status}")

                if status == "completed":
                    logger.info(f"Transcription completed after {attempt} polls", extra=FILE_ONLY)
                    break
                elif status in ("error", "failed"):
                    # "error" is the documented terminal status; "failed" is
                    # undocumented but kept as a defensive catch -- matching an
                    # unknown terminal-looking status here beats polling it into
                    # the full timeout (#141). The field is error_message, not
                    # "error"; the `or` also covers a null error_message.
                    error_msg = resp.json().get("error_message") or "Unknown error"
                    logger.error(f"{self.get_name()} transcription failed: {error_msg}")
                    if error_sink is not None:
                        error_sink.errored, error_sink.reason = True, "service-error"
                    return ""

                time.sleep(SONIOX_ASYNC_POLL_INTERVAL)
            else:
                logger.error(f"{self.get_name()} polling timeout after {SONIOX_ASYNC_MAX_POLL_ATTEMPTS} attempts")
                if error_sink is not None:
                    error_sink.errored, error_sink.reason = True, "service-error"
                return ""

            # Step 4: Get transcript
            resp = httpx.get(
                f"{SONIOX_ASYNC_API_BASE}/v1/transcriptions/{tx_id}/transcript",
                headers=self.headers,
                timeout=15
            )
            resp.raise_for_status()
            text = resp.json().get("text", "").strip()

            elapsed = time.time() - start_time
            logger.info(f"Soniox v4 transcription successful in {elapsed:.2f}s", extra=FILE_ONLY)
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")

            # Clean fillers
            text = self._remove_spoken_fillers(text)

            return text

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                self._report_auth_failure("SONIOX_API_KEY")
                # DEBUG keeps the trace file-only so the [AUTH] line stands
                # alone on the console (#32)
                logger.debug(f"Error during Soniox v4 transcription: {e}", exc_info=True)
            else:
                logger.error(f"Error during {self.get_name()} transcription: {_one_line_error(e)}", exc_info=True)
            if error_sink is not None:
                error_sink.errored, error_sink.reason = True, _http_status_reason(e.response.status_code)
            return ""
        except Exception as e:
            logger.error(f"Error during {self.get_name()} transcription: {_one_line_error(e)}", exc_info=True)
            if error_sink is not None:
                error_sink.errored, error_sink.reason = True, _httpx_exc_reason(e)
            return ""

        finally:
            # Cleanup: Always delete transcription and file from server
            import httpx as httpx_cleanup
            try:
                if tx_id:
                    httpx_cleanup.delete(
                        f"{SONIOX_ASYNC_API_BASE}/v1/transcriptions/{tx_id}",
                        headers=self.headers, timeout=10
                    )
                    logger.debug(f"Transcription {tx_id} deleted from server")
            except Exception as e:
                logger.warning(f"Could not delete transcription: {e}")
            try:
                if file_id:
                    httpx_cleanup.delete(
                        f"{SONIOX_ASYNC_API_BASE}/v1/files/{file_id}",
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

            logger.info(f"Testing {self.get_name()} with file: {test_file_path} ({duration:.1f}s)")

            text = self.transcribe(test_file_path, duration)

            if text:
                logger.info(f"Soniox v4 test transcription successful: {len(text)} chars", extra=FILE_ONLY)
                return text
            else:
                logger.warning(f"{self.get_name()} test transcription returned empty text")
                return None

        except Exception as e:
            logger.error(f"{self.get_name()} test transcription failed: {e}", exc_info=True)
            return None


class SonioxLiveTranscriber(AbstractTranscriber):
    """Handles live transcription using Soniox WebSocket RT API.

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
            raise MissingAPIKeyError("SONIOX_API_KEY", "Soniox Live transcriber")
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

        logger.info(f"Soniox Live transcriber initialized (model: {SONIOX_RT_MODEL})", extra=FILE_ONLY)
        if SONIOX_CONTEXT:
            logger.info(f"Context enabled: {len(SONIOX_CONTEXT.get('terms', []))} terms", extra=FILE_ONLY)

    def get_name(self) -> str:
        """Get the name of this transcriber"""
        return API_DISPLAY["soniox-live"]["label"]

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

                logger.info(f"Opening WebSocket connection to {SONIOX_WS_URL}...", extra=FILE_ONLY)
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
                config.update(soniox_live_endpointing_params())

                self._ws.send(json.dumps(config))
                logger.info("WebSocket config sent", extra=FILE_ONLY)

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

                logger.info("Soniox Live session started", extra=FILE_ONLY)
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
                    f"chunks over {drop_duration:.1f}s", extra=FILE_ONLY
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
        logger.info("Soniox Live sender thread started", extra=FILE_ONLY)

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
                f"(queue size at exit: {remaining})", extra=FILE_ONLY
            )

    def cancel_session(self):
        """Cancel the live session immediately.

        Called from on_cancel_recording().
        """
        with self._session_lock:
            logger.info("Cancelling Soniox Live session", extra=FILE_ONLY)
            self._close_session_internal()

    def _receiver_loop(self):
        """Receiver thread: reads WebSocket messages and collects final tokens.

        Runs until 'finished: true' is received or an error occurs.
        Sets _result_ready event when done.
        """
        import json

        logger.info("Soniox Live receiver thread started", extra=FILE_ONLY)

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
                            logger.info("Received <fin> token, finalization complete", extra=FILE_ONLY)

                # Check if finished
                if msg.get("finished"):
                    logger.info("Received 'finished' signal from server", extra=FILE_ONLY)
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
                       f"({len(self._final_tokens)} final tokens collected)", extra=FILE_ONLY)

    def transcribe(self, audio_file_path: str, duration_seconds: float, *,
                   error_sink: Optional['_ErrorTag'] = None) -> str:
        """Finalize the live session and return the transcript.

        For the Live transcriber, this does NOT use audio_file_path.
        Instead it sends finalize to the WebSocket and waits for the result.
        The audio_file_path is only used for logging (the file is still saved
        by audio_handler for archival purposes).

        Args:
            audio_file_path: Path to archived audio file (for logging only)
            duration_seconds: Duration of the recording
            error_sink: Optional _ErrorTag set on a session error (auth on a 401
                close, else service-error) for ABC uniformity and #138. In the
                current worker flow an empty live transcript always runs the
                internal V2->V4 file lane, whose aggregate signal supersedes this
                sink -- so it is honest bookkeeping, not what feeds the verdict.

        Returns:
            Transcribed text
        """
        import json

        logger.info(f"Soniox Live: finalizing session "
                    f"(duration: {duration_seconds:.1f}s, audio archived at: {audio_file_path})", extra=FILE_ONLY)

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
                if error_sink is not None:
                    error_sink.errored, error_sink.reason = True, "service-error"
                return ""

            # Step 4: Check for errors
            if self._session_error:
                if self._session_auth_error:
                    # Auth failure already surfaced as the [AUTH] console
                    # line; repeating it at ERROR would bury that line (#32)
                    logger.debug(f"Soniox Live session had error: {self._session_error}")
                else:
                    logger.error(f"Soniox Live session had error: {self._session_error}")
                if error_sink is not None:
                    error_sink.errored = True
                    error_sink.reason = "auth" if self._session_auth_error else "service-error"
                return ""

            # Step 5: Assemble text from final tokens
            text = "".join(self._final_tokens).strip()
            # Clean fillers
            text = self._remove_spoken_fillers(text)

            elapsed = time.time() - start_time
            logger.info(f"Soniox Live transcription finalized in {elapsed:.2f}s", extra=FILE_ONLY)
            logger.debug(f"Transcribed text ({len(text)} chars): {text[:100]}...")

            return text

        except Exception as e:
            logger.error(f"Error during Soniox Live finalization: {e}", exc_info=True)
            if error_sink is not None:
                error_sink.errored, error_sink.reason = True, "service-error"
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
                f"total-blocked-time={self._send_latency_blocked_total:.2f}s",
                extra=FILE_ONLY
            )

        # Block 2: log queue-drop diagnostics
        if self._drop_count_total > 0:
            logger.info(
                f"Session queue-drop stats: "
                f"total-chunks-dropped={self._drop_count_total} "
                f"(live transcript had gaps, MP3 archive unaffected)", extra=FILE_ONLY
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

            logger.info(f"Testing {self.get_name()} with file: {test_file_path} ({duration:.1f}s)")

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
                logger.info(f"Soniox Live test transcription successful: {len(text)} chars", extra=FILE_ONLY)
                return text
            else:
                logger.warning("Soniox Live test transcription returned empty text")
                return None

        except Exception as e:
            logger.error(f"Soniox Live test transcription failed: {e}", exc_info=True)
            self.cancel_session()
            return None


def engine_code(transcriber: AbstractTranscriber) -> str:
    """Stable filename token for the engine a transcriber represents (#62).

    Total by design: called on a carousel-slot transcriber, where only
    'soniox-live', 'groq' and 'groq-large' reach here (the hybrid 'soniox' slot
    is tagged per-call via engine_sink instead, since its engine is only known
    at runtime). The remaining branches are defensive completeness.
    """
    if isinstance(transcriber, SonioxLiveTranscriber):
        return ENGINE_TOKENS["soniox_live"]
    if isinstance(transcriber, GroqTranscriber):
        return (ENGINE_TOKENS["groq_large"] if transcriber.model == GROQ_LARGE_MODEL
                else ENGINE_TOKENS["groq_turbo"])
    if isinstance(transcriber, SonioxV4Transcriber):
        return ENGINE_TOKENS["soniox_v4"]
    if isinstance(transcriber, SonioxTranscriber):
        return ENGINE_TOKENS["soniox_v2"]
    return ENGINE_TOKENS["unknown"]


def create_transcriber(api_name: str) -> AbstractTranscriber:
    """
    Factory function to create transcriber instances

    Args:
        api_name: Name of the API to use ('soniox-live', 'soniox', 'groq-large', or 'groq')

    Returns:
        Transcriber instance

    Raises:
        ValueError: If api_name is not supported
    """
    if api_name == "groq":
        return GroqTranscriber()
    elif api_name == "groq-large":
        return GroqTranscriber(model=GROQ_LARGE_MODEL, display_name=API_DISPLAY["groq-large"]["label"])
    elif api_name == "soniox":
        return SonioxTranscriber()
    elif api_name == "soniox-live":
        return SonioxLiveTranscriber()
    else:
        raise ValueError(f"Unknown API: {api_name}. Supported: soniox-live, soniox, groq-large, groq")