"""
Configuration Module for Thoughtborne

This module contains all configuration constants and settings.
It handles loading environment variables and provides default values.
It also owns the legacy archive-layout migration (#50), kept next to the
path constants it serves.
"""

import copy
import json
import logging
import os
from pathlib import Path

# Pure, ctypes-free hotkey lexical layer (#55): shared with hotkey_manager so a
# config-time override is validated by the exact logic that registers it at
# runtime. hotkey_parse imports nothing Windows-bound, so config stays importable
# off-Windows (the test drivers depend on that).
from hotkey_parse import (
    parse_hotkey_lexical, classify_key, HotkeyParseError, KEY_INVALID,
)

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

# ===== AUDIO STALL / DEADLOCK GUARDS (#128) =====
# A stalled microphone (e.g. a Bluetooth headset that stops delivering audio, or
# a driver that wedges at OS level) must never freeze the app. The capture read
# no longer blocks unbounded inside the stream lock: record_chunk() polls
# get_read_available() every AUDIO_READ_POLL_SECONDS and only reads once a full
# CHUNK is ready (so the read returns at once); no full chunk for
# AUDIO_STALL_TIMEOUT_SECONDS is treated as device loss and routed into the #49
# endgame. No critical thread (the hotkey listener, the exit path) ever waits
# unbounded on that lock or runs a native audio close itself: _close_stream()
# acquires the lock with AUDIO_CLOSE_LOCK_TIMEOUT and runs stop_stream()/close()
# on a throwaway worker bounded by the SAME timeout as a join -- on either
# timeout it abandons (poisons) the wedged stream so hotkeys stay alive, and the
# next recording rebuilds the pipeline. Opening the device is likewise bounded by
# AUDIO_OPEN_TIMEOUT_SECONDS on a worker thread. All are guide values (stability
# is principle #1), not protocol constants.
AUDIO_READ_POLL_SECONDS     = 0.005   # ~5 ms between availability polls; << one 64 ms chunk, not a busy-spin
AUDIO_STALL_TIMEOUT_SECONDS = 5.0     # no full chunk this long => device loss; clears the ~0.5-1.0 s BT warm-up
AUDIO_CLOSE_LOCK_TIMEOUT    = 2.0     # cap for BOTH the close lock acquire and the close-worker join (worst case ~2+2 s, rare)
AUDIO_OPEN_TIMEOUT_SECONDS  = 8.0     # PyAudio init + stream open on a worker; generous for BT warm-up

# Recording-loop liveness guard (#128): if get_read_available() itself wedges at
# driver level, the recording-loop thread stays is_alive() but pins _stream_lock
# forever inside record_chunk() and never captures another chunk. The recording
# loop stamps a monotonic tick every iteration; on the next Ctrl+Alt+W,
# on_start_recording treats a tick older than this as a wedged loop and refuses to
# start a recording that would look live but stay silent. Well above the worst-
# case legitimate iteration (~9 s: 5 s stall watchdog + 2+2 s bounded close), so a
# merely busy loop is never mistaken for a wedged one.
RECORDING_LOOP_STALE_SECONDS = 15.0

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
# with the #86 labels (Soniox …, Groq Whisper …). The version suffix records the model
# generation the tool REQUESTED (the pin) -- `-v2` for the legacy gRPC sync engine, `-v5`
# for the Live and async REST engines since the #121 re-pin -- which stays truthful even if
# Soniox silently serves a newer generation under the old name (#82); a re-pin updates the
# token from that point on, and existing archive files keep the token they were written with.
ENGINE_TOKENS = {
    "soniox_live": "SonLive-v5",     # Soniox Live (websocket RT)
    "soniox_v2":   "Son-v2",         # soniox slot, V2 sync (<58 s)
    "soniox_v4":   "Son-v5",         # soniox slot, async REST (long / fallback)
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
# unspoken vocabulary at 50. So 15 is the safe ceiling. The v5 engines have no equivalent
# knob -- they let the server weight the context dict -- so this tunes the legacy gRPC
# (de_v2) path only. Sweep evidence lives in _research/2026-07_soniox-v2-boost-sweep/.
SONIOX_V2_CONTEXT_BOOST = 15.0

# ===== SONIOX ASYNC REST API SETTINGS =====
# Async REST engine: used by the 'soniox' slot for long recordings and as
# automatic fallback when V2 sync fails (#31). File upload → transcription →
# polling → result.
SONIOX_ASYNC_API_BASE = "https://api.soniox.com"
SONIOX_ASYNC_MODEL = "stt-async-v5"
SONIOX_ASYNC_POLL_INTERVAL = 0.5   # Seconds between polling attempts
SONIOX_ASYNC_MAX_POLL_ATTEMPTS = 600  # Max 5 minutes waiting

# ===== SONIOX LIVE (WEBSOCKET RT) SETTINGS =====
# Live-streaming API: audio is sent in real-time during recording.
# After stop, finalize returns the transcript in milliseconds.
SONIOX_WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_RT_MODEL = "stt-rt-v5"
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

# ===== SONIOX SHARED SETTINGS =====
# Language and context settings used by both the Async and Live APIs
SONIOX_LANGUAGE_HINTS = ["de"]

# ===== SONIOX V5 ENDPOINTING (#121) =====
# Optional fine-tuning of Soniox's v5 real-time endpoint detector, for the Live
# (WebSocket) path only -- the async REST API has no endpointing concept. DEFAULT
# is all-None -> nothing is sent, so the Live WS config JSON stays byte-identical
# to before and Soniox applies its own defaults. Values come from an optional
# "soniox_endpointing" block in personal_settings.json (same file as vocabulary
# and push_to_talk); any out-of-range/wrong-typed field warns and is dropped
# (never sent) so an invalid value can never reach -- and be rejected by --
# Soniox mid-session. enable_endpoint_detection stays True unconditionally
# (already sent, transcriber.py); these only tune the detector it turns on.
# Ranges/defaults per Soniox docs (2026-07, _research/2026-07_soniox-v5-endpointing/):
#   endpoint_sensitivity              number  -1.0..1.0  default 0.0  (v5-only; lower waits longer -> dictation-friendly)
#   endpoint_latency_adjustment_level integer 0..3       default 0    (v5-only; higher ends sentences sooner)
#   max_endpoint_delay_ms             number  500..3000  default 2000 (v4+;   hard cap on wait after speech ends)
# Concrete dictation tuning is deferred to a follow-up issue; this is only the hook.
SONIOX_ENDPOINT_SENSITIVITY = None
SONIOX_ENDPOINT_LATENCY_ADJUSTMENT_LEVEL = None
SONIOX_MAX_ENDPOINT_DELAY_MS = None


def soniox_live_endpointing_params() -> dict:
    """Configured Soniox v5 endpointing params, ready to merge into the Live
    WebSocket config JSON (#121). Empty dict when nothing is configured, so the
    caller's config stays byte-identical to before. Live path only -- the async
    REST API has no endpointing fields, so nothing here is ever sent there.
    Uses `is not None` (not truthiness) so an explicit 0.0/0 is included."""
    params = {}
    if SONIOX_ENDPOINT_SENSITIVITY is not None:
        params["endpoint_sensitivity"] = SONIOX_ENDPOINT_SENSITIVITY
    if SONIOX_ENDPOINT_LATENCY_ADJUSTMENT_LEVEL is not None:
        params["endpoint_latency_adjustment_level"] = SONIOX_ENDPOINT_LATENCY_ADJUSTMENT_LEVEL
    if SONIOX_MAX_ENDPOINT_DELAY_MS is not None:
        params["max_endpoint_delay_ms"] = SONIOX_MAX_ENDPOINT_DELAY_MS
    return params


def apply_hotkey_overrides(defaults: dict, raw: dict) -> tuple:
    """Return (effective_hotkeys, warnings) for the #55 'hotkeys' override block.

    Pure and side-effect-free (no logging, no globals) so it is unit-testable and
    IS the production loader: config calls it verbatim. `defaults` is HOTKEYS
    (action -> str | list[str]); `raw` is the parsed personal_settings 'hotkeys'
    object. Every rejected entry leaves that action's default in force -- never a
    startup abort (stability, VISION principle #1). A combo colliding with another
    action's effective binding is dropped (the default stays). Never raises.

    Shape is preserved exactly: an action whose default is a string stays a string
    (a one-element list override collapses to it; a multi-combo override is
    rejected), and only an action whose default is already a list accepts several
    combos -- so thoughtborne._register_hotkeys keeps reading the shapes it does
    today.
    """
    warnings = []
    effective = copy.deepcopy(defaults)
    overridden = set()

    for action, value in raw.items():
        if action.startswith('_'):
            continue   # JSON-comment convention (e.g. "_comment"); never an action
        if action not in defaults:
            warnings.append(
                f"hotkeys: unknown action '{action}' -- ignored "
                f"(valid: {', '.join(sorted(defaults))})")
            continue

        # Normalize to a list of combo strings (a bare string is one combo).
        if isinstance(value, str):
            combos = [value]
        elif isinstance(value, list):
            combos = value
        else:
            warnings.append(
                f"hotkeys.{action}: value must be a combo string or a list of "
                f"them; keeping default")
            continue
        if not combos:
            warnings.append(f"hotkeys.{action}: empty list; keeping default")
            continue

        norm = []
        ok = True
        for c in combos:
            if not isinstance(c, str) or not c.strip():
                warnings.append(
                    f"hotkeys.{action}: '{c}' is not a non-empty combo string; "
                    f"keeping default")
                ok = False
                break
            try:
                _mods, key = parse_hotkey_lexical(c)
            except HotkeyParseError as e:
                warnings.append(
                    f"hotkeys.{action}: '{c}' is not a valid combo ({e}); "
                    f"keeping default")
                ok = False
                break
            if classify_key(key) == KEY_INVALID:
                warnings.append(
                    f"hotkeys.{action}: '{c}' has an unrecognized key '{key}'; "
                    f"keeping default")
                ok = False
                break
            # Canonicalize: lowercased and inner spaces dropped ('Ctrl + Alt + P'
            # -> 'ctrl+alt+p'), matching the defaults' shape. The registrar strips
            # per part regardless, but the string-level prefix comparison in
            # _keys_grid_data / _prefix_for and _format_hotkey split on '+' and
            # would otherwise see stray-space parts like ' alt '.
            norm.append('+'.join(p.strip() for p in c.lower().split('+')))
        if not ok:
            continue

        # Match the default's shape (maintainer decision, #55): a string-valued
        # action stays a string; genuine multi-binding is only for actions whose
        # default is already a list, so _register_hotkeys' shapes are unchanged.
        if isinstance(defaults[action], list):
            effective[action] = norm
        elif len(norm) == 1:
            effective[action] = norm[0]
        else:
            warnings.append(
                f"hotkeys.{action}: multiple combos are not supported for this "
                f"action; keeping default")
            continue
        overridden.add(action)

    # ---- duplicate detection on the EFFECTIVE set --------------------------
    # Canonical form of a combo: (modifier_bitmask_incl_NOREPEAT, key_token) from
    # the pure parse -- case- and modifier-order-independent, matching what
    # RegisterHotKey collides on for every statically resolvable key. Run on the
    # effective set (not the raw defaults) so "free a default key, then reuse it"
    # is not a false positive.
    def _flatten(d):
        for act, val in d.items():
            for combo in ([val] if isinstance(val, str) else val):
                mods, key = parse_hotkey_lexical(combo)
                yield (mods, key), act

    # Revert every overridden action that collides, until no collision involves an
    # override. Defaults are mutually collision-free in the shipped config, so this
    # converges (in practice one pass); a default is never mutated. A colliding
    # combo is tagged 'cross' (shared with a *different* action) or 'self' (the
    # same combo listed twice within one action's list) so the warning is honest.
    while True:
        groups = {}
        for canon, act in _flatten(effective):
            groups.setdefault(canon, []).append(act)
        to_revert = {}   # action -> 'cross' | 'self'  ('cross' wins if both apply)
        for canon, acts in groups.items():
            if len(acts) <= 1:
                continue
            cross = len(set(acts)) > 1
            for a in acts:
                if a in overridden and to_revert.get(a) != 'cross':
                    to_revert[a] = 'cross' if cross else 'self'
        if not to_revert:
            break
        for a, kind in to_revert.items():
            effective[a] = copy.deepcopy(defaults[a])
            overridden.discard(a)
            if kind == 'cross':
                warnings.append(
                    f"hotkeys.{a}: combo collides with another action; keeping default")
            else:
                warnings.append(
                    f"hotkeys.{a}: the same combo is listed more than once; keeping default")

    return effective, warnings


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
# Captured during the single personal_settings parse below, applied after the
# HOTKEYS defaults are defined (#55). Stays None when the file/block is absent.
_hotkeys_override = None
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

        # Soniox v5 endpointing override (#121): optional fine-tuning of the
        # Live-path endpoint detector, read from the same _settings dict. An
        # absent block or any invalid field leaves the None default in force, so
        # nothing is sent for that field and the WS config JSON is byte-identical
        # to today. Validated client-side so an out-of-range value never reaches
        # Soniox (which would reject it and could drop the live session).
        _ep_cfg = _settings.get("soniox_endpointing", {})
        if isinstance(_ep_cfg, dict):
            # endpoint_sensitivity: number in -1.0..1.0. Reject bool (True/False
            # are ints in Python and would slip past isinstance + the range).
            # 0.0 is a VALID explicit value -> use `is not None`, never truthiness,
            # so an explicitly configured 0.0 is honored and sent (not dropped).
            _sens = _ep_cfg.get("endpoint_sensitivity")
            if isinstance(_sens, (int, float)) and not isinstance(_sens, bool) and -1.0 <= _sens <= 1.0:
                SONIOX_ENDPOINT_SENSITIVITY = float(_sens)
            elif _sens is not None:
                _config_logger.warning(
                    f"soniox_endpointing.endpoint_sensitivity '{_sens}' invalid "
                    f"(need a number -1.0..1.0); not sending it")

            # endpoint_latency_adjustment_level: integer 0..3 (strict int; reject
            # bool and float so a stray 1.0 or True can't be sent as a level).
            _lvl = _ep_cfg.get("endpoint_latency_adjustment_level")
            if isinstance(_lvl, int) and not isinstance(_lvl, bool) and 0 <= _lvl <= 3:
                SONIOX_ENDPOINT_LATENCY_ADJUSTMENT_LEVEL = _lvl
            elif _lvl is not None:
                _config_logger.warning(
                    f"soniox_endpointing.endpoint_latency_adjustment_level '{_lvl}' invalid "
                    f"(need an integer 0..3); not sending it")

            # max_endpoint_delay_ms: number in 500..3000, sent as an integer ms.
            _delay = _ep_cfg.get("max_endpoint_delay_ms")
            if isinstance(_delay, (int, float)) and not isinstance(_delay, bool) and 500 <= _delay <= 3000:
                SONIOX_MAX_ENDPOINT_DELAY_MS = int(_delay)
            elif _delay is not None:
                _config_logger.warning(
                    f"soniox_endpointing.max_endpoint_delay_ms '{_delay}' invalid "
                    f"(need a number 500..3000); not sending it")

        # Hotkey overrides (#55): capture the optional "hotkeys" block here (so the
        # file is parsed once) and apply it after the HOTKEYS defaults are defined
        # below -- the defaults don't exist yet at this point in the module.
        _hk = _settings.get("hotkeys")
        if _hk is not None and not isinstance(_hk, dict):
            _config_logger.warning(
                f"personal_settings 'hotkeys' must be an object of "
                f"action -> combo; ignoring (got {type(_hk).__name__})")
        else:
            _hotkeys_override = _hk

        # Default engine override (#55): defaults.api must be one of AVAILABLE_APIS,
        # else warn and keep DEFAULT_API. Same warn-and-keep pattern as the blocks
        # above; DEFAULT_API and AVAILABLE_APIS are defined far above this block.
        _defaults_cfg = _settings.get("defaults", {})
        if isinstance(_defaults_cfg, dict):
            _api = _defaults_cfg.get("api")
            if _api is not None:
                if isinstance(_api, str) and _api in AVAILABLE_APIS:
                    DEFAULT_API = _api
                else:
                    _config_logger.warning(
                        f"defaults.api '{_api}' unknown (need one of "
                        f"{AVAILABLE_APIS}); using '{DEFAULT_API}'")
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
# Pass as `extra=FILE_ONLY` on a log call to keep that record off the console while
# the file handler still records it unchanged (level + content preserved). The
# Cockpit console (#109) routes routine/technical lines this way instead of
# downgrading their level, so the file log stays complete.
FILE_ONLY = {'file_only': True}

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

# Hotkey overrides (#55): apply the optional personal_settings "hotkeys" block
# (captured during the single settings parse above) now that the HOTKEYS defaults
# exist. The pure validator returns the effective set plus human-readable warnings;
# every rejected entry keeps that action's default and a combo that would collide
# with another action is dropped -- never a startup abort (VISION principle #1).
if _hotkeys_override:
    HOTKEYS, _hk_warnings = apply_hotkey_overrides(HOTKEYS, _hotkeys_override)
    for _w in _hk_warnings:
        _config_logger.warning(_w)

# ===== QUEUE SETTINGS =====
TRANSCRIPT_HISTORY_SIZE = 10
OUTPUT_QUEUE_TIMEOUT = 0.5  # seconds