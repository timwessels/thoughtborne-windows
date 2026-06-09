"""
Configuration Module for Thoughtborne

This module contains all configuration constants and settings.
It handles loading environment variables and provides default values.
"""

import json
import logging
import os
from pathlib import Path

# Try to load dotenv, but don't fail if not available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # If python-dotenv is not installed, we can still use environment variables
    pass

_config_logger = logging.getLogger('Thoughtborne.Config')

# ===== PATHS =====
SCRIPT_DIR = Path(__file__).parent.absolute()
LOG_FILE = SCRIPT_DIR / "thoughtborne.log"
ARCHIVE_FOLDER = SCRIPT_DIR / "voice_archive"
TEXT_ARCHIVE_FOLDER = SCRIPT_DIR / "text_archive"

# ===== AUDIO SETTINGS =====
CHUNK = 1024
FORMAT = 16  # pyaudio.paInt16
CHANNELS = 1
RATE = 16000
WAV_OUTPUT_FILENAME = "output.wav"

# ===== AUDIO PREPROCESSING =====
# Removes keyboard click sounds at end of recording and adds silence for better transcription
AUDIO_TRIM_END_MS = 300       # Milliseconds to trim from end (removes hotkey click sound)
AUDIO_SILENCE_PADDING_MS = 1000  # Milliseconds of silence to add at end (helps API detect end of speech)

# ===== GROQ API SETTINGS =====
GROQ_MODEL = "whisper-large-v3-turbo"
LANGUAGE = "de"  # "de" for German, "en" for English, None for auto-detect
MAX_PARALLEL_TRANSCRIPTIONS = 3

# ===== API SELECTION =====
DEFAULT_API = "soniox-live"  # Standard API at startup (soniox-live = fastest, soniox v2 = precise)
AVAILABLE_APIS = ["soniox-live", "soniox", "groq", "soniox-v4"]  # Carousel order (Ctrl+Alt+L)

# ===== API KEYS =====
# Load from environment variable or .env file
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
SONIOX_API_KEY = os.getenv('SONIOX_API_KEY')

# ===== SONIOX API SETTINGS =====
SONIOX_MODEL = "de_v2"  # Language model: "de_v2" for German, "en_v2" for English
# Soniox sync API has a hard limit of 60s. The threshold must be lower than 60s
# because audio preprocessing adds silence padding (AUDIO_SILENCE_PADDING_MS) after
# the duration check, which can push the actual file length over the limit.
# 58s provides a safe margin (see: Bug 2026-01-30, 59.8s recording + 1000ms padding = 60.5s → rejected).
SHORT_AUDIO_THRESHOLD = 58

# ===== SONIOX V4 ASYNC REST API SETTINGS =====
# New REST-based API (replaces gRPC). File upload → transcription → polling → result.
SONIOX_V4_API_BASE = "https://api.soniox.com"
SONIOX_V4_MODEL = "stt-async-v4"
SONIOX_V4_POLL_INTERVAL = 0.5   # Seconds between polling attempts
SONIOX_V4_MAX_POLL_ATTEMPTS = 600  # Max 5 minutes waiting

# ===== SONIOX LIVE (WEBSOCKET RT) SETTINGS =====
# Live-streaming API: audio is sent in real-time during recording.
# After stop, finalize returns the transcript in milliseconds.
SONIOX_WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_RT_MODEL = "stt-rt-v4"
SONIOX_LIVE_FINALIZE_DELAY = 0.2    # Seconds of silence to send before finalize
SONIOX_LIVE_FINALIZE_TIMEOUT = 10.0  # Max seconds to wait for finalize response

# Block 2: Producer-Consumer queue between recording loop and WebSocket send.
# Audio chunks are handed off to a dedicated sender thread via this queue so
# TCP backpressure on _ws.send() never blocks the recording loop (and never
# stalls PyAudio capture or the BT pipeline). When the queue is full (sender
# can't drain fast enough), new chunks are dropped — the live transcript gets
# a gap, but the MP3 archive is unaffected because frames are kept in the
# recording loop independently of this queue.
#
# Default 50 ≈ 3.2 s buffer (CHUNK=1024 / RATE=16000 → ~64 ms per chunk).
# Larger values absorb longer TCP stalls but increase the chance of a Soniox
# "prolonged buffering" disconnect when the burst eventually flushes.
SONIOX_LIVE_QUEUE_MAX_CHUNKS = 50
# Max seconds _close_session_internal() waits for the sender thread to exit.
SONIOX_LIVE_SENDER_JOIN_TIMEOUT = 3.0
# Max seconds transcribe() waits for the sender to drain queued finalize items
# (silence + finalize command + EOS) before falling through to receiver wait.
SONIOX_LIVE_FINALIZE_DRAIN_TIMEOUT = 5.0

# ===== SONIOX SHARED V4 SETTINGS =====
# Language and context settings used by both v4 Async and Live APIs
SONIOX_LANGUAGE_HINTS = ["de"]

# SONIOX_CONTEXT is loaded from the "vocabulary" block of an optional
# personal_settings.json in the project root. See personal_settings.example.json
# for the format. Personalization is user-specific (names, project terms etc.)
# and therefore kept out of the repository. When the file is missing, invalid,
# or has no "vocabulary" block, SONIOX_CONTEXT stays None and no personalization
# is sent to the Soniox API.
SONIOX_CONTEXT = None
_personal_settings_path = SCRIPT_DIR / "personal_settings.json"
if _personal_settings_path.exists():
    try:
        with open(_personal_settings_path, 'r', encoding='utf-8') as _f:
            _settings = json.load(_f)
        SONIOX_CONTEXT = _settings.get("vocabulary")
    except (json.JSONDecodeError, OSError) as _e:
        _config_logger.warning(f"Could not load {_personal_settings_path.name}: {_e}")

# ===== LOGGING =====
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 3
LOG_FORMAT = '%(asctime)s - %(levelname)s - [%(threadName)s] %(funcName)s - %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
# Bounded queue between the hotkey-listener-side logging call and the QueueListener
# thread that writes to the cmd console (#11). Sized for several seconds of sustained
# log bursts in normal use; when the cmd window stays in Mark-Mode longer than the
# queue can absorb, the newest console records are dropped (the file log keeps all).
LOG_CONSOLE_QUEUE_MAX = 200

# ===== UI SETTINGS =====
STATUS_UPDATE_INTERVAL = 5  # seconds
CLIPBOARD_RESTORE_DELAY = 0.1  # seconds
KEY_RELEASE_DELAY = 0.05  # seconds

# ===== HOTKEYS =====
# Windows uses Ctrl+Alt instead of Cmd+Control (Mac)
# German QWERTZ keyboard layout consideration:
# - 'y' key is where 'z' is on US keyboards
# - 'ü' is a German umlaut (own key on QWERTZ)
# Note: Avoid special characters like '#' and non-ASCII letters like 'ä' in hotkeys
#       They can cause issues with the keyboard module (character gets typed in some apps)
HOTKEYS = {
    'start_recording': 'ctrl+alt+w',           # W = Start recording
    'stop_recording_keyboard': 'ctrl+alt+a',   # A = Stop & insert (keyboard typing)
    'stop_recording_clipboard': 'ctrl+alt+d',  # D = Stop & insert (clipboard paste)
    'stop_recording_send': 'ctrl+alt+h',       # H = Stop & insert & SEND (press Enter)
    'stop_recording_no_insert': 'ctrl+alt+y',  # Y = Stop & process only (insert later) - NEW!
    'retry_last_failed': 'ctrl+alt+r',         # R = Retry last FAILED transcription
    'cancel_recording': ['ctrl+alt+x'],        # X = Cancel recording
    'test_transcription': 'ctrl+alt+ü',        # Ü = Test transcription
    'switch_api': 'ctrl+alt+l',                # L = Switch API (GROQ <-> Soniox)
    'exit_program': ['ctrl+alt+4']             # 4 = Exit program
}

# ===== QUEUE SETTINGS =====
TRANSCRIPT_HISTORY_SIZE = 10
OUTPUT_QUEUE_TIMEOUT = 0.5  # seconds