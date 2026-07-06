"""
Configuration Module for Thoughtborne

This module contains all configuration constants and settings.
It handles loading environment variables and provides default values.
It also owns the legacy archive-layout migration (#50), kept next to the
path constants it serves.
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
# Unified history layout (#50): one folder to open, audio and transcripts as
# timestamp-paired siblings. Pre-#50 layouts (voice_archive/ + text_archive/)
# are migrated by migrate_legacy_archives() below.
HISTORY_FOLDER = SCRIPT_DIR / "history"
ARCHIVE_FOLDER = HISTORY_FOLDER / "audio"
TEXT_ARCHIVE_FOLDER = HISTORY_FOLDER / "transcripts"


def migrate_legacy_archives(base_dir=None) -> list:
    """Move the pre-#50 archive folders into the unified history/ layout (#50).

    voice_archive/ -> history/audio/ and text_archive/ -> history/transcripts/,
    each pair handled independently, via an atomic same-volume directory rename
    (no copying -- years of recordings move as one metadata operation, and the
    #49 .partial sidecars travel along, so the startup recovery finds them at
    the new location).

    MUST run before anything creates or scans the new folders: before
    AudioRecorder / transcriber construction (their mkdir calls) and before
    recover_partial_files(). main() calls it first thing.

    Never deletes user data, and never raises -- any failure degrades to a
    WARNING and leaves the old folder untouched for the next start. The only
    thing it ever removes is a truly EMPTY new-side folder (rmdir), which is
    what heals the "migration failed once, mkdir then created empty new
    folders" state.

    base_dir parameterizes the layout root for tests; production uses
    SCRIPT_DIR. Returns the list of user-facing event strings (also logged
    here) so a test driver can assert on outcomes.
    """
    base = base_dir if base_dir is not None else SCRIPT_DIR
    pairs = [
        (base / "voice_archive", base / "history" / "audio"),
        (base / "text_archive", base / "history" / "transcripts"),
    ]
    events = []
    for old, new in pairs:
        try:
            if not old.is_dir():
                continue  # nothing legacy here: fresh start or already migrated
            if new.is_dir():
                try:
                    # Only succeeds on a truly empty directory -- inherently
                    # safe. Heals the failed-first-attempt state where a later
                    # mkdir created the new folder empty.
                    new.rmdir()
                except OSError:
                    msg = (f"Old and new archive folders BOTH exist -- touching "
                           f"neither. Old: {old}  New: {new}  To resolve: move "
                           f"the files from the old folder into the new one "
                           f"while Thoughtborne is not running, then delete the "
                           f"emptied old folder.")
                    _config_logger.warning(msg)
                    events.append(msg)
                    continue
            new.parent.mkdir(parents=True, exist_ok=True)
            n_files = sum(1 for f in old.rglob("*") if f.is_file())
            old.rename(new)  # os.rename underneath: atomic on the same volume
            msg = (f"Archive migrated to the new layout: {old.name} -> "
                   f"{new.relative_to(base)} ({n_files} files)")
            _config_logger.info(msg)
            events.append(msg)
        except OSError as e:
            if not old.exists() and new.is_dir():
                # Benign race: a second instance migrated this pair first.
                _config_logger.info(f"{old.name} was already migrated by another instance")
                continue
            msg = (f"Could not migrate {old} -> {new} ({e}). Your files are "
                   f"untouched in {old}; the next start retries the migration "
                   f"(or explains how to resolve it).")
            _config_logger.warning(msg)
            events.append(msg)
        except Exception as e:
            # A rescue/maintenance path must never block the start (#50).
            msg = (f"Unexpected error migrating {old} -> {new} ({e}). Your "
                   f"files are untouched in {old}; the next start retries the "
                   f"migration (or explains how to resolve it).")
            _config_logger.error(msg, exc_info=True)
            events.append(msg)
    return events

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

# ===== CRASH-SAFETY SIDECAR (#49) =====
# During recording, an observer thread appends the captured audio to a raw-PCM
# .partial file in ARCHIVE_FOLDER every SIDECAR_FLUSH_SECONDS (flush + fsync per
# batch), so a hard kill / crash / BSOD loses at most this many seconds of
# dictation. Guide value, not gospel: smaller shrinks the loss window, larger
# reduces disk traffic. The write runs off the capture hot path either way.
SIDECAR_FLUSH_SECONDS = 5

# ===== GROQ API SETTINGS =====
GROQ_MODEL = "whisper-large-v3-turbo"  # 'groq' carousel entry (fastest)
GROQ_LARGE_MODEL = "whisper-large-v3"  # 'groq-large' carousel entry (higher accuracy, #36)
LANGUAGE = "de"  # "de" for German, "en" for English, None for auto-detect
MAX_PARALLEL_TRANSCRIPTIONS = 3

# ===== API SELECTION =====
DEFAULT_API = "soniox-live"  # Standard API at startup (soniox-live = fastest, soniox v2 = precise)
AVAILABLE_APIS = ["soniox-live", "soniox", "groq-large", "groq"]  # Carousel order (Ctrl+Alt+L)

# ===== API DISPLAY (single source of truth, #30/#37) =====
# One label set for everything user-facing: the status block, the API lineup,
# and the transcribers' get_name(). Labels and descriptors match the README
# model-lineup wording (#47); if this wording ever changes, change README.md
# and README.de.md in the same commit (bilingual rule in AGENTS.md).
# AVAILABLE_APIS above stays the only ordering source.
API_DISPLAY = {
    "soniox-live": {"label": "Soniox Live", "descriptor": "verbatim, instant"},
    "soniox":      {"label": "Soniox",      "descriptor": "polished, takes longer"},
    "groq-large":  {"label": "Groq Whisper Large v3", "descriptor": "accurate, free"},
    "groq":        {"label": "Groq Whisper Turbo v3", "descriptor": "fast, free"},
}

# ===== ARCHIVE ENGINE TOKENS (single source, #84) =====
# Filename tokens naming the engine that produced an archived transcript/recording (#62).
# Keyed by a stable internal engine id (underscored, so it never collides with the hyphenated
# carousel/API keys in AVAILABLE_APIS). The token strings are the only thing that ever
# changes. Deliberately more technical than the in-tool #86 labels: the audience derives the
# engine from a filename, no legend. The Son… / GWhisper… stems stay recognizably consistent
# with the #86 labels (Soniox …, Groq Whisper …). The -v2/-v4 suffix records the model
# generation the tool REQUESTED (the pin), which stays truthful even if Soniox silently
# serves a newer generation under the old name (#82).
ENGINE_TOKENS = {
    "soniox_live": "SonLive-v4",     # Soniox Live (websocket RT)
    "soniox_v2":   "Son-v2",         # soniox slot, V2 sync (<58 s)
    "soniox_v4":   "Son-v4",         # soniox slot, V4 async REST (long / fallback)
    "groq_large":  "GWhisperLar-v3", # Groq whisper-large-v3
    "groq_turbo":  "GWhisperTur-v3", # Groq whisper-large-v3-turbo
    "unknown":     "unknown",        # defensive completeness (engine_code fall-through)
}

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
# SpeechContext boost for the personal vocabulary phrases on the V2 sync path (#73).
# Soniox's legacy customization docs define boost as a decode-stage bias on recognizing
# the given phrases; valid range -50..50 (values outside are clipped server-side, no
# error), with 15 the documented "start here" value and "increase if needed" the next
# step, and the boost applying to each phrase as a whole. An excessively high boost may
# hurt accuracy (unquantified by Soniox) -- the false-positive risk of a boosted term
# landing where it wasn't spoken, exactly the "meaning-bearing wrong word" the quality
# bar guards against. 15.0 is now a measured optimum, not a copied guess (#85): a sweep
# of the known V2 failure recording over boosts 0/15/20/25/30/40/50 rescued no hard term
# at any value (WezTerm, tmux-Session, Kannengießer, van Wynsberghe stay garbled -- the
# de_v2 base model simply lacks the hypotheses, #81), everything the context can rescue
# (Cygnus, QISPOS) already lands at 15, boosts 20..30 gained nothing over 15 (same
# terms rescued, no false positives, only immaterial text differences), and boosts
# >= 40 began degrading other recordings -- doubling a spoken filler at 40, inserting
# unspoken vocabulary at 50. So 15 is the safe ceiling. The v4 engines have no equivalent
# knob -- they let the server weight the context dict -- so this tunes the legacy gRPC
# (de_v2) path only. Sweep evidence lives in _research/2026-07_soniox-v2-boost-sweep/.
SONIOX_V2_CONTEXT_BOOST = 15.0

# ===== SONIOX V4 ASYNC REST API SETTINGS =====
# V4 async REST engine: used by the 'soniox' slot for long recordings and as
# automatic fallback when V2 sync fails (#31). File upload → transcription →
# polling → result.
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
# ===== PUSH-TO-TALK (#66) =====
# Opt-in, DEFAULT OFF. The gesture is: tap the trigger modifier, release, then
# press-and-HOLD it; recording runs while held, releasing inserts. Built on
# GetAsyncKeyState polling (no low-level keyboard hook -- the hook was removed
# in early 2026 because Modern Standby silently invalidated it). These are the
# defaults; the optional "push_to_talk" block of personal_settings.json (same
# file as the vocabulary above) overrides any of them. Bad values warn and keep
# the default. thoughtborne.py imports the (already-overridden) values.
PTT_ENABLED = False          # master switch; default off (the gesture reads every trigger press)
PTT_TRIGGER = "lctrl"        # lctrl (default) | rctrl | lalt
PTT_INSERT = "clipboard"     # clipboard (default) | type (fallback) | send | no_insert
PTT_TAP_WINDOW_S = 0.30      # max gap from first release to second press
PTT_MIN_HOLD_S = 0.20        # second press must be held this long before recording starts
PTT_RELEASE_TAIL_S = 0.15    # keep recording this long after release (anti-clip)

# Allowed trigger names -> raw VK code. lctrl is the spike's recommended primary
# (bottom-left, blind-reachable); rctrl is the AltGr-safe fallback; lalt is
# offered for owner adaptability despite its Alt-menu-flash quirk.
_PTT_TRIGGER_VK = {"lctrl": 0xA2, "rctrl": 0xA3, "lalt": 0xA4}
PTT_TRIGGER_VK = _PTT_TRIGGER_VK[PTT_TRIGGER]

SONIOX_CONTEXT = None
_personal_settings_path = SCRIPT_DIR / "personal_settings.json"
if _personal_settings_path.exists():
    try:
        with open(_personal_settings_path, 'r', encoding='utf-8') as _f:
            _settings = json.load(_f)
        SONIOX_CONTEXT = _settings.get("vocabulary")

        # Push-to-talk override (#66): read from the same _settings dict so the
        # file is parsed once. Absent block or any invalid field -> the default
        # beside the constant above stays in force.
        _ptt_cfg = _settings.get("push_to_talk", {})
        if isinstance(_ptt_cfg, dict):
            # Honor "enabled" only as a real JSON boolean: bool("false") is True
            # in Python, so a quoted-string value would silently switch on this
            # off-by-default feature that reads every trigger press.
            _en = _ptt_cfg.get("enabled", PTT_ENABLED)
            if isinstance(_en, bool):
                PTT_ENABLED = _en
            else:
                _config_logger.warning(
                    f"push_to_talk.enabled '{_en}' invalid (need a JSON boolean true/false); "
                    f"using {PTT_ENABLED}")

            _trig = str(_ptt_cfg.get("trigger", PTT_TRIGGER)).lower()
            if _trig in _PTT_TRIGGER_VK:
                PTT_TRIGGER, PTT_TRIGGER_VK = _trig, _PTT_TRIGGER_VK[_trig]
            else:
                _config_logger.warning(
                    f"push_to_talk.trigger '{_trig}' unknown; using '{PTT_TRIGGER}'")

            _ins = str(_ptt_cfg.get("insert", PTT_INSERT)).lower()
            if _ins in ("type", "clipboard", "send", "no_insert"):
                PTT_INSERT = _ins
            else:
                _config_logger.warning(
                    f"push_to_talk.insert '{_ins}' unknown; using '{PTT_INSERT}'")

            # Thresholds: accept positive numbers only; reject 0, negatives,
            # bools, and non-numbers (a stray double-tap firing a 0 s recording
            # is exactly what the min-hold guards against).
            for _name, _key in (("PTT_TAP_WINDOW_S", "tap_window_s"),
                                 ("PTT_MIN_HOLD_S", "min_hold_s"),
                                 ("PTT_RELEASE_TAIL_S", "release_tail_s")):
                _v = _ptt_cfg.get(_key)
                if isinstance(_v, (int, float)) and not isinstance(_v, bool) and _v > 0:
                    globals()[_name] = float(_v)
                elif _v is not None:
                    _config_logger.warning(
                        f"push_to_talk.{_key} '{_v}' invalid (need a positive number); "
                        f"using {globals()[_name]}")
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
    'switch_api': 'ctrl+alt+l',                # L = Cycle transcription APIs
    'open_history': 'ctrl+alt+6',              # 6 = Open the history folder in Explorer (#50)
    'exit_program': ['ctrl+alt+4']             # 4 = Exit program
}

# ===== QUEUE SETTINGS =====
TRANSCRIPT_HISTORY_SIZE = 10
OUTPUT_QUEUE_TIMEOUT = 0.5  # seconds