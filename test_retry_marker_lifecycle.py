#!/usr/bin/env python3
"""Lifecycle tests for the persistent retry marker (#106/#114/#134/#133/#141/#138/#212/#342).

Runs on plain Python -- no Windows, no PyAudio -- so the pure file logic of
`audio_handler`'s marker functions is exercised programmatically (a durable
regression guard, sibling of `test_audio_stall.py` and `test_console_ui.py`).

    python3 test_retry_marker_lifecycle.py    # verify, exit non-zero on failure

This pins decision **D-001** (DECISIONS.md): a saved-but-untranscribed recording
is announced once, stays retryable across restarts, and an all-empty retry is a
final "no speech" verdict rather than an endless nag. The marker-level shapes of
those verdicts (the parts that live in `audio_handler`, not the Windows-coupled
worker) are what this file proves:
  - write -> the reader arms it, un-announced;
  - mark-announced -> the probe reads announced, and the reader still arms it
    silently (the announce bit is display-only, never dis-arms retry);
  - the announce rename is idempotent;
  - migration: every pre-existing (current or legacy #106 bare-name) marker
    reads un-announced, announces once, then reads announced;
  - _recovered / _recovered_2 kill-recovery stems round-trip disjointly;
  - delete removes either flavor; supersede prunes older of either flavor and
    keeps the newest (strict '<');
  - the #133 clean-empty verdict (delete the marker) vs the transport-error
    reset (delete + fresh write -> exactly one marker, un-announced again).

A second layer drives the real worker methods (process_recording_thread /
retry_recording_thread) against a minimal fake `self` to pin the verdict ROUTING.
Since #138 the verdict is single-engine: a clean-but-empty run on ANY selected
engine earns the honest "no speech" verdict (the engine ran end to end with no
transport/API error and found zero chars); an errored run stays the conservative
FAILED + retryable, carrying a coarse reason (auth / no-connection / rate-limited
/ service-error) on the task for #159's panel. This generalizes the earlier
live-only #133 verdict: every engine now reports through the per-call error sink,
so a swallowed outage no longer masquerades as silence on the soniox/groq slots.
Soniox Live keeps its internal async file lane, whose aggregate signal feeds the
verdict exactly as before.

  - #141 closes a hole in that file lane: the async stage used to swallow its own
    transport/API errors and report clean-but-empty -- so a real outage
    masqueraded as "no speech" and disarmed the retry. Since #212 that async
    stage is the only file stage for every duration, so short and long recordings
    take the identical path. Tier 1 drives the REAL fallback chain
    (_run_empty_transcript_fallback / _try_fallback) against a faked async stage
    to prove the outage now routes to FAILED + retryable while a genuine
    clean-empty still earns no-speech; Tier 2 exercises the REAL
    SonioxAsyncTranscriber.transcribe against a scripted fake httpx to prove each
    of its four error returns sets the per-call error sink (and a
    completed-but-empty run does not), plus the one status-specific lane in there:
    an HTTP 400 -- Soniox rejecting the request itself -- also logs a static cause
    hint, and no other status does (#334). The same fake also watches the
    server-side cleanup (#342): on every path, what the call created on
    Soniox's servers is deleted -- transcription first, then file -- and nothing
    else is. Since #345 that happens after the text is returned, in a thread
    that takes over the call's client; every Tier-2 case captures that thread
    instead of starting it and runs it by hand. The same fake drives
    the real ElevenLabsTranscriber (#343) through the same questions: what each
    path reports to the sink, which stored transcript it asks ElevenLabs to
    delete, and how an error is categorized from ElevenLabs' error body rather
    than the status alone -- with httpx's real transport made to refuse and the
    module's key copy faked for the run, since the workshop `.env` may hold a
    real key. Its delete runs in a thread after the text is returned, so the
    case captures that thread instead of starting it and runs it by hand, with
    the wait recorded rather than slept: what is held is the call, its order and
    its wait, never the effect at ElevenLabs -- a premature DELETE is answered
    200 there and changes nothing, which no fake can see. One more case holds
    the factory to building that class for the `elevenlabs` id.

Plus two import-side guards, outside the marker story: importing
`output_handler` must leave pyautogui's corner fail-safe off (#241) and its
trailing pause at zero (#345).
"""
import sys
import types
import ctypes
import inspect
import logging
import shutil
import tempfile
import threading
from pathlib import Path

# ---- stub the Windows-only modules audio_handler imports at top (mirrors
# test_audio_stall.py: subprocess/soundfile first so the non-Windows detection
# is cached before a msvcrt stub lands in sys.modules).
import subprocess  # noqa: E402,F401
import soundfile   # noqa: E402,F401

_fake_msvcrt = types.ModuleType("msvcrt")
_fake_msvcrt.locking = lambda *a, **k: None
_fake_msvcrt.LK_NBLCK = 0
_fake_msvcrt.LK_UNLCK = 0
sys.modules.setdefault("msvcrt", _fake_msvcrt)

_fake_pyaudio = types.ModuleType("pyaudio")
_fake_pyaudio.paInt16 = 16
_fake_pyaudio.get_sample_size = lambda fmt: 2
_fake_pyaudio.PyAudio = object
sys.modules.setdefault("pyaudio", _fake_pyaudio)

import audio_handler as ah  # noqa: E402

# Point the archive at a throwaway dir and silence the (deliberately loud)
# best-effort warnings the marker paths emit.
ah.ARCHIVE_FOLDER = Path(tempfile.mkdtemp(prefix="tb_marker_test_"))
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

# The log goes to a throwaway dir of its own, and BEFORE `import thoughtborne` below:
# that import attaches a RotatingFileHandler to config.LOG_FILE, which creates the file
# where none exists. In a fresh clone that left a 0-byte thoughtborne.log with a fresh
# mtime and no `Program ended` -- exactly what the "While the tool is running" gate reads
# as a running tool, so a mere test run faked the heartbeat (#267). thoughtborne.py binds
# the value at import, so the module attribute has to move before it, not after.
import config  # noqa: E402
_LOG_DIR = Path(tempfile.mkdtemp(prefix="tb_marker_log_"))   # removed in main()
config.LOG_FILE = _LOG_DIR / "thoughtborne.log"


# ---- second layer: the transcript verdict ROUTING (#133), tested by driving
# the real worker methods (process_recording_thread / retry_recording_thread)
# against a minimal fake `self`. That needs thoughtborne itself importable
# off-Windows, so a few more Windows-only / GUI modules get faked here: the
# hotkey_manager and output_handler modules configure argtypes/restype on
# ctypes.windll / ctypes.WinDLL handles at import, and output_handler pulls in
# keyboard/pyperclip/pyautogui; transcriber imports groq. None of that is
# exercised -- only enough to let the pure routing logic run.
class _AnyCallable:
    def __call__(self, *a, **k):
        return 0
    def __getattr__(self, n):
        return _AnyCallable()
    def __setattr__(self, n, v):  # swallow the module-level argtypes/restype config
        pass


class _FakeDLLNamespace:
    def __getattr__(self, n):
        return _AnyCallable()


ctypes.windll = _FakeDLLNamespace()
ctypes.WinDLL = lambda *a, **k: _AnyCallable()

for _mod in ("keyboard", "pyperclip", "pyautogui"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))
_fake_groq = types.ModuleType("groq")
_fake_groq.Groq = type("Groq", (), {"__init__": lambda self, *a, **k: None})
# The exception hierarchy mirrors the real groq SDK's MRO closely enough for
# _groq_error_reason's isinstance ladder (#138), so that mapper's direct unit
# tests below run off-Windows without the SDK installed. APIStatusError carries a
# settable status_code (the real one derives it from the httpx response).
class _FakeGroqError(Exception):
    pass
class _FakeGroqAPIError(_FakeGroqError):
    pass
class _FakeGroqAPIStatusError(_FakeGroqAPIError):
    def __init__(self, message="", *, status_code=None):
        self.status_code = status_code
class _FakeGroqAPIConnectionError(_FakeGroqAPIError):
    pass
class _FakeGroqAPITimeoutError(_FakeGroqAPIConnectionError):
    pass
class _FakeGroqAuthenticationError(_FakeGroqAPIStatusError):
    pass
class _FakeGroqRateLimitError(_FakeGroqAPIStatusError):
    pass
_fake_groq.AuthenticationError = _FakeGroqAuthenticationError
_fake_groq.APIStatusError = _FakeGroqAPIStatusError
_fake_groq.APIConnectionError = _FakeGroqAPIConnectionError
_fake_groq.APITimeoutError = _FakeGroqAPITimeoutError
_fake_groq.RateLimitError = _FakeGroqRateLimitError
_groq_module = sys.modules.setdefault("groq", _fake_groq)
if _groq_module is not _fake_groq:
    # Another driver's stub got there first (one pytest process, no SDK on the
    # box; setdefault means first importer wins). Matching class names would not
    # be enough: _groq_error_reason imports from that module, and its isinstance
    # ladder only recognizes those very class objects -- so the cases below
    # raise them instead of the ones defined here.
    _FakeGroqAuthenticationError = _groq_module.AuthenticationError
    _FakeGroqAPIStatusError = _groq_module.APIStatusError
    _FakeGroqAPIConnectionError = _groq_module.APIConnectionError
    _FakeGroqAPITimeoutError = _groq_module.APITimeoutError
    _FakeGroqRateLimitError = _groq_module.RateLimitError

import thoughtborne as tb  # noqa: E402
import transcriber as tr  # noqa: E402
from output_handler import TranscriptionTask, OutputManager  # noqa: E402,F401
from transcriber import SonioxLiveTranscriber  # noqa: E402

# Undo thoughtborne's import-time global side effects: it swaps sys.stdout/stderr
# for logger wrappers and attaches a RotatingFileHandler pointed at the repo's
# real thoughtborne.log. Restore the streams and detach every handler so these
# tests neither pollute that log nor keep the process's stdout redirected.
sys.stdout = tb.original_stdout
sys.stderr = tb.original_stderr
for _name in ("Thoughtborne", "Thoughtborne.stdio", "Thoughtborne.console"):
    _lg = logging.getLogger(_name)
    for _h in list(_lg.handlers):
        _lg.removeHandler(_h)
# Re-assert CRITICAL: importing thoughtborne reset the 'Thoughtborne' logger to
# DEBUG (its module-level setup), clobbering the silence set in the first block.
# The retry cases raise it to ERROR transiently so their catch-all probe sees an
# error record.
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

# Both ARCHIVE_FOLDER globals must point at the throwaway dir: audio_handler's
# marker functions read ah.ARCHIVE_FOLDER, and thoughtborne's own copy (imported
# from config) is what _record_failed_slot reconstructs paths against.
tb.ARCHIVE_FOLDER = ah.ARCHIVE_FOLDER


# ---- helpers ---------------------------------------------------------------
def _reset():
    """Empty the archive folder so each case starts from a clean slate."""
    for p in ah.ARCHIVE_FOLDER.iterdir():
        p.unlink()


def _mp3(stem):
    """Create a dummy archive mp3 for `stem` so the reader's mp3-exists check
    passes, and return its path string."""
    p = ah.ARCHIVE_FOLDER / f"{stem}.mp3"
    p.touch()
    return str(p)


def _markers():
    return sorted(m.name for m in ah.ARCHIVE_FOLDER.glob("voice_*.needsretry"))


def _write_legacy(stem, ms):
    """Write a marker the way the pre-#134 code did (no _seen), directly by name,
    to model a pre-existing / legacy #106 marker on disk."""
    (ah.ARCHIVE_FOLDER / f"{stem}_d{ms}.needsretry").touch()


# ---- cases -----------------------------------------------------------------
def test_write_arms_unannounced():
    _reset()
    mp3 = _mp3("voice_20260716_001612_154")
    assert ah.write_retry_marker(mp3, 0.256) is True
    armed = ah.recover_salvaged_recordings()
    assert len(armed) == 1, f"expected 1 armed recording, got {armed}"
    assert armed[0][0] == mp3, f"reader must reconstruct the mp3 path: {armed[0]}"
    assert abs(armed[0][1] - 0.256) < 1e-6, f"duration must round-trip: {armed[0]}"
    assert armed[0][2] == "20260716_001612_154"
    assert ah.is_retry_marker_announced(mp3) is False, "a fresh marker is un-announced"


def test_mark_announced_still_arms_silently():
    _reset()
    mp3 = _mp3("voice_20260716_001612_154")
    ah.write_retry_marker(mp3, 0.256)
    ah.mark_retry_marker_announced(mp3)
    assert ah.is_retry_marker_announced(mp3) is True, "probe must read announced after mark"
    assert _markers() == ["voice_20260716_001612_154_d256_seen.needsretry"], \
        f"the marker must carry the _seen token: {_markers()}"
    # The announce bit is display-only: the reader still arms the recording.
    armed = ah.recover_salvaged_recordings()
    assert len(armed) == 1 and armed[0][0] == mp3, \
        f"an announced marker must still arm retry silently: {armed}"


def test_mark_announced_is_idempotent():
    _reset()
    mp3 = _mp3("voice_20260716_001612_154")
    ah.write_retry_marker(mp3, 0.256)
    ah.mark_retry_marker_announced(mp3)
    before = _markers()
    ah.mark_retry_marker_announced(mp3)  # second call must no-op
    assert _markers() == before == ["voice_20260716_001612_154_d256_seen.needsretry"], \
        f"a second mark must not create a second marker: {_markers()}"
    assert ah.is_retry_marker_announced(mp3) is True


def test_migration_current_format():
    """Mirrors the update on Tim's checkout: a pre-existing un-announced marker
    reads un-announced, announces exactly once, then reads announced."""
    _reset()
    mp3 = _mp3("voice_20260718_130804_002")
    _write_legacy("voice_20260718_130804_002", 32064)  # written by the old code
    assert ah.is_retry_marker_announced(mp3) is False, "a pre-#134 marker is un-announced"
    armed = ah.recover_salvaged_recordings()
    assert len(armed) == 1 and armed[0][0] == mp3
    ah.mark_retry_marker_announced(mp3)   # the one-and-only panel
    assert ah.is_retry_marker_announced(mp3) is True, "now silent on every later start"


def test_legacy_106_bare_name_reconstructs_mp3():
    """A legacy #106 bare-name marker (rec=None, seen=None) round-trips and
    reconstructs voice_<ts>.mp3."""
    _reset()
    mp3 = _mp3("voice_20260710_235959_001")
    _write_legacy("voice_20260710_235959_001", 5120)
    armed = ah.recover_salvaged_recordings()
    assert len(armed) == 1, f"the legacy bare-name marker must arm: {armed}"
    assert armed[0][0] == mp3, f"must reconstruct voice_<ts>.mp3: {armed[0]}"
    assert ah.is_retry_marker_announced(mp3) is False
    ah.mark_retry_marker_announced(mp3)
    assert ah.is_retry_marker_announced(mp3) is True


def test_recovered_stems_roundtrip_disjoint():
    """_recovered and _recovered_2 kill-recovery stems round-trip through
    write / mark / is-announced / delete without bleeding into each other or the
    bare stem."""
    _reset()
    bare = _mp3("voice_20260716_001612_154")
    rec1 = _mp3("voice_20260716_001612_154_recovered")
    rec2 = _mp3("voice_20260716_001612_154_recovered_2")
    for mp3 in (bare, rec1, rec2):
        ah.write_retry_marker(mp3, 1.0)
    # Marking one recovered stem must not touch the bare or the other recovered.
    ah.mark_retry_marker_announced(rec1)
    assert ah.is_retry_marker_announced(rec1) is True
    assert ah.is_retry_marker_announced(rec2) is False, "sibling _recovered_2 untouched"
    assert ah.is_retry_marker_announced(bare) is False, "bare stem untouched"
    # Deleting one recovered stem must leave the others in place.
    ah.delete_retry_marker(rec1)
    assert ah.is_retry_marker_announced(rec1) is False
    remaining = _markers()
    assert any("_recovered_2_" in n for n in remaining), f"rec2 marker gone: {remaining}"
    assert any(n.startswith("voice_20260716_001612_154_d") for n in remaining), \
        f"bare marker gone: {remaining}"


def test_delete_removes_either_flavor():
    _reset()
    mp3 = _mp3("voice_20260716_001612_154")
    # un-announced
    ah.write_retry_marker(mp3, 0.256)
    ah.delete_retry_marker(mp3)
    assert _markers() == [], f"delete must remove an un-announced marker: {_markers()}"
    # announced (_seen)
    ah.write_retry_marker(mp3, 0.256)
    ah.mark_retry_marker_announced(mp3)
    ah.delete_retry_marker(mp3)
    assert _markers() == [], f"delete must remove an announced marker: {_markers()}"


def test_supersede_prunes_older_either_flavor_keeps_newest():
    _reset()
    old_mp3 = _mp3("voice_20260716_001612_154")
    new_mp3 = _mp3("voice_20260718_130804_002")
    ah.write_retry_marker(old_mp3, 0.256)
    ah.mark_retry_marker_announced(old_mp3)   # older, announced
    ah.write_retry_marker(new_mp3, 1.5)       # newer, un-announced
    ah.delete_superseded_retry_markers("20260718_130804_002")
    remaining = _markers()
    assert len(remaining) == 1, f"strict '<' must keep exactly the newest: {remaining}"
    assert remaining[0].startswith("voice_20260718_130804_002_d"), remaining
    assert ah.is_retry_marker_announced(new_mp3) is False, "the kept newest stays un-announced"


def test_multiple_markers_both_arm_then_supersede():
    """Two in-session failures with no restart between: both markers on disk,
    both arm; the next start's supersede drops the older one."""
    _reset()
    a = _mp3("voice_20260716_001612_154")
    b = _mp3("voice_20260716_001700_002")
    ah.write_retry_marker(a, 0.256)
    ah.write_retry_marker(b, 0.512)
    armed = ah.recover_salvaged_recordings()
    assert len(armed) == 2, f"both un-announced markers must arm: {armed}"
    ah.delete_superseded_retry_markers("20260716_001700_002")
    assert _markers() == ["voice_20260716_001700_002_d512.needsretry"], _markers()


def test_verdict_clean_empty_deletes_marker():
    """#133 clean-empty verdict, marker-level: an all-engines-empty retry deletes
    the marker (a second Ctrl+Alt+R then finds nothing to retry)."""
    _reset()
    mp3 = _mp3("voice_20260716_001612_154")
    ah.write_retry_marker(mp3, 0.256)
    ah.mark_retry_marker_announced(mp3)          # it had been announced once
    ah.delete_retry_marker(mp3)                  # the verdict
    assert _markers() == [], f"clean-empty verdict must clear the marker: {_markers()}"
    assert ah.recover_salvaged_recordings() == [], "nothing left to arm"


def test_verdict_transport_error_resets_announcement():
    """#134 F-1, marker-level: a failed retry that hit a transport/API error is
    kept retryable AND re-announced. delete + fresh write must leave exactly ONE
    marker, un-announced -- the leftover _seen would otherwise suppress the panel
    (why the delete is load-bearing, not cosmetic)."""
    _reset()
    mp3 = _mp3("voice_20260716_001612_154")
    ah.write_retry_marker(mp3, 0.256)
    ah.mark_retry_marker_announced(mp3)          # announced before the failed retry
    assert ah.is_retry_marker_announced(mp3) is True
    # The reset transport path:
    ah.delete_retry_marker(mp3)
    ah.write_retry_marker(mp3, 0.256)
    assert _markers() == ["voice_20260716_001612_154_d256.needsretry"], \
        f"the reset must leave exactly one un-announced marker: {_markers()}"
    assert ah.is_retry_marker_announced(mp3) is False, "the reset must un-announce"
    assert len(ah.recover_salvaged_recordings()) == 1, "and arm exactly one recording"


def test_orphan_and_unparseable_removed_on_read():
    _reset()
    # Orphan: a marker whose archive mp3 is gone.
    _write_legacy("voice_20260716_001612_154", 256)   # no matching .mp3 created
    # Unparseable: a name the regex rejects.
    (ah.ARCHIVE_FOLDER / "voice_garbage.needsretry").touch()
    armed = ah.recover_salvaged_recordings()
    assert armed == [], f"neither orphan nor unparseable should arm: {armed}"
    assert _markers() == [], f"both must be pruned on read: {_markers()}"


# ---- #133 verdict ROUTING -------------------------------------------------
# These drive the real worker methods to prove which verdict an empty result
# earns. The crux the Critic caught: "no speech" is only trustworthy on the
# Soniox Live file-fallback chain, which reports whether any stage hit a real
# transport/API error. A non-live engine (soniox/groq) returns "" on a swallowed
# transport error just as it does on true silence, so its empty must stay the
# conservative FAILED + retryable, never no_speech.
class _FakeLive(SonioxLiveTranscriber):
    """Cheap SonioxLiveTranscriber: isinstance() and is_live hold, no real init."""
    def __init__(self):
        pass

    def get_name(self):
        return "Soniox Live (fake)"

    def transcribe(self, *a, **k):
        return ""


class _FakeFile:
    """A non-live, file-based engine that ran CLEAN and came back empty (never
    touches the error sink) -- genuine silence. Not a SonioxAsyncTranscriber
    subclass, so engine_code -> unknown. Since #138 this earns the no-speech verdict."""
    is_live = False

    def get_name(self):
        return "Groq (fake, clean-empty)"

    def transcribe(self, *a, **k):
        return ""


class _FakeFileOutage:
    """A non-live, file-based engine that hit a transport/API error: returns ""
    AND reports it through the sink (errored + a coarse reason), exactly as every
    engine now does (#138). Its empty must stay FAILED + retryable, with the
    reason landing on the task."""
    is_live = False

    def get_name(self):
        return "Groq (fake, outage)"

    def transcribe(self, *a, error_sink=None, **k):
        if error_sink is not None:
            error_sink.errored, error_sink.reason = True, "no-connection"
        return ""


# ---- #141/#212: faked async stages for the REAL fallback chain -----------
# These stand in for SonioxAsyncTranscriber inside _try_fallback. They subclass
# the real class (with a no-op __init__ -- no API key, no SDK, no archive mkdir)
# so isinstance / engine_code / _error_provider treat them as the soniox slot,
# and mirror the real transcribe() signature (the keyword-only error_sink), so
# the Tier-1 cases run the genuine _run_empty_transcript_fallback / _try_fallback
# against them. display_name is set so the worker's get_name() ticker works when
# such a fake is the SELECTED slot.
class _FakeAsyncOutage(tr.SonioxAsyncTranscriber):
    """Async engine simulating a swallowed transport/API outage: returns ""
    AND sets the sink's errored flag WITHOUT a reason -- so the chain's aggregation
    exercises its default (errored-but-no-reason -> "service-error", #138)."""
    def __init__(self):
        self.display_name = "Soniox"
    def transcribe(self, path, duration, *, error_sink=None):
        if error_sink is not None:
            error_sink.errored = True
        return ""


class _FakeAsyncOutageReason(tr.SonioxAsyncTranscriber):
    """Async outage that also stamps a specific reason on the sink, as the real
    class does after #138 -- proves a categorized reason propagates through the
    chain to the failed task."""
    def __init__(self):
        self.display_name = "Soniox"
    def transcribe(self, path, duration, *, error_sink=None):
        if error_sink is not None:
            error_sink.errored, error_sink.reason = True, "no-connection"
        return ""


class _FakeAsyncCleanEmpty(tr.SonioxAsyncTranscriber):
    """Async engine that completed clean but found nothing -- never touches the sink."""
    def __init__(self):
        self.display_name = "Soniox"
    def transcribe(self, path, duration, *, error_sink=None):
        return ""


class _FakeAsyncSuccess(tr.SonioxAsyncTranscriber):
    """Async engine that returned a real transcript -- never touches the sink."""
    def __init__(self):
        self.display_name = "Soniox"
    def transcribe(self, path, duration, *, error_sink=None):
        return "recovered text"


class _ErrorCapture(logging.Handler):
    """Collects ERROR records so a case can prove the routing ran (a catch-all
    logs 'Error processing' / 'Error retrying') rather than the except handler."""
    def __init__(self):
        super().__init__(logging.ERROR)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


class _FakeAudio:
    def __init__(self):
        self.cleanup_called = False

    def save_recording(self, frames, ts):
        return (str(ah.ARCHIVE_FOLDER / "w.wav"), str(ah.ARCHIVE_FOLDER / "m.mp3"))

    def tag_archive_with_engine(self, ts, engine):
        pass

    def cleanup_temp_files(self, wav, mp3):
        # Reached only on the normal in-session path -- the except handler skips
        # it, so this flag discriminates routing from the catch-all.
        self.cleanup_called = True


class _FakeOutput:
    def __init__(self):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)

    def update_last_transcript(self, transcript):
        pass


class _FakeApp:
    """Minimal collaborator surface the two worker methods touch, so they can be
    called as unbound methods without constructing the whole ThoughtborneApp."""
    # Not a fake: retry_recording_thread derives its task's trigger_keys from the
    # effective HOTKEYS through this method (#152), so the genuine one has to be
    # here. What it returns is irrelevant to the verdicts below -- that derivation
    # is test_stop_rebind_wiring.py's subject.
    _wait_keys = tb.ThoughtborneApp._wait_keys

    def __init__(self, fallback_result=None):
        self.transcriber = None
        self.processing_lock = threading.Lock()
        self.processing_counter = 0
        self.audio_recorder = _FakeAudio()
        self.output_manager = _FakeOutput()
        self._fallback_result = fallback_result   # (transcript, engine, any_error, reason)
        self.record_failed_called = False
        self.resolve_failed_called = False

    def _ticker(self, *a, **k):
        pass

    def get_unique_timestamp(self):
        return "20260718_090000_001"

    def _run_empty_transcript_fallback(self, **k):
        return self._fallback_result

    def _record_failed_slot(self, ts, dur):
        self.record_failed_called = True

    def _resolve_failed_slot(self, rec):
        self.resolve_failed_called = True


class _RealChainApp(_FakeApp):
    """_FakeApp, but running the REAL fallback chain against a faked async stage
    (#141/#212). Binds the genuine _run_empty_transcript_fallback / _try_fallback
    and pre-seeds the fallback transcriber so the lazy init never constructs a
    real one (which would demand API keys). The async fake carries the outage/
    clean/success behavior under test."""
    _run_empty_transcript_fallback = tb.ThoughtborneApp._run_empty_transcript_fallback
    _try_fallback = tb.ThoughtborneApp._try_fallback

    def __init__(self, fake_async):
        super().__init__()
        self._fallback_init_lock = threading.Lock()
        self._fallback_async = fake_async   # pre-seeded: the lazy init must not run


def _armed_rec(ts="20260718_085900_001", dur=1.0):
    """Create the archived mp3 + retry marker, return the _FailedRecording the
    retry worker expects."""
    mp3 = ah.ARCHIVE_FOLDER / f"voice_{ts}.mp3"
    mp3.touch()
    ah.write_retry_marker(str(mp3), dur)
    return tb._FailedRecording(archived_mp3_path=str(mp3), duration=dur, origin_timestamp=ts)


def _drive_retry(app, rec, seq, transcriber):
    """Run retry_recording_thread with a transient ERROR-level capture (the file
    keeps 'Thoughtborne' at CRITICAL otherwise). Returns (task, error_messages)."""
    lg = logging.getLogger("Thoughtborne")
    old = lg.level
    lg.setLevel(logging.ERROR)
    cap = _ErrorCapture()
    lg.addHandler(cap)
    try:
        tb.ThoughtborneApp.retry_recording_thread(app, rec, seq, transcriber)
    finally:
        lg.removeHandler(cap)
        lg.setLevel(old)
    return app.output_manager.tasks[-1], cap.records


def test_insession_nonlive_errored_empty_is_failed_with_reason():
    """A non-live engine that ERRORED (reported through the sink) and returned
    empty must be FAILED + retryable, and the reason must land on the task (#138).
    This is the recast of the pre-#138 test: back then a bare non-live empty was
    unconditionally FAILED; now the errored signal is what makes it FAILED."""
    _reset()
    app = _FakeApp()
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=7,
        timestamp="20260718_090000_007", transcriber=_FakeFileOutage())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "an errored non-live empty must be a FAILED verdict"
    assert task.no_speech is False, "an errored non-live empty must NOT be no_speech"
    assert task.error_reason == "no-connection", \
        f"the sink's reason must land on the failed task (#138): {task.error_reason}"
    assert task.error_inconclusive is False, \
        "a non-live engine's own error keeps its category, not the 'came back empty' flag (#159)"
    assert app.record_failed_called is True, "the retry slot must be armed"


def test_insession_nonlive_clean_empty_is_no_speech():
    """The #138 behavior change: a non-live engine that ran CLEAN and returned
    empty (never touched the sink) now earns the honest 'no speech' verdict, just
    like Soniox Live -- single-engine, each engine speaks for itself."""
    _reset()
    app = _FakeApp()
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=71,
        timestamp="20260718_090000_071", transcriber=_FakeFile())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.no_speech is True, "a clean-empty non-live run now earns no-speech (#138)"
    assert task.is_error is False, "no-speech is not a failure"
    assert task.error_reason is None, "a clean run carries no reason"
    assert app.record_failed_called is False, "no retry slot for a genuinely silent recording"


def test_insession_live_clean_empty_is_no_speech():
    """The intended #133 behavior: a Soniox Live chain that ran clean and empty
    earns the honest 'no speech' verdict and arms no retry."""
    _reset()
    app = _FakeApp(fallback_result=("", "", False, None))   # chain ran, no error
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=8,
        timestamp="20260718_090000_008", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.no_speech is True, "a clean live chain earns the no-speech verdict"
    assert task.is_error is False, "no-speech is not a failure"
    assert app.record_failed_called is False, "no retry slot for a genuinely silent recording"


def test_insession_live_transport_error_is_error():
    """A Soniox Live chain that hit a transport error stays FAILED + retryable,
    and the chain's reason lands on the task (#138)."""
    _reset()
    app = _FakeApp(fallback_result=("", "", True, "no-connection"))  # chain hit a transport error
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=9,
        timestamp="20260718_090000_009", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "a live transport error stays retryable"
    assert task.no_speech is False
    assert task.error_reason == "no-connection", f"the chain's reason must land on the task: {task.error_reason}"
    assert task.error_provider == "Soniox", "the failing family is Soniox (live + its file lane, #159)"
    assert task.error_inconclusive is True, \
        "a live FAILED came via the empty async lane -> the 'came back empty' flag (#159)"
    assert app.record_failed_called is True


def test_retry_nonlive_errored_empty_keeps_marker_with_reason():
    """The retry path mirrors #138: a non-live engine (chosen via Ctrl+Alt+L) that
    ERRORED and returned empty keeps the recording retryable (marker persists) and
    carries the reason on the task -- switch-and-retry stays the outage escape."""
    _reset()
    rec = _armed_rec()
    app = _FakeApp()
    task, errors = _drive_retry(app, rec, 11, _FakeFileOutage())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.is_error is True, "an errored non-live empty retry must stay FAILED"
    assert task.no_speech is False, "an errored non-live empty retry must NOT be no_speech"
    assert task.error_reason == "no-connection", \
        f"the sink's reason must land on the failed retry task (#138): {task.error_reason}"
    assert task.error_inconclusive is False, \
        "a non-live retry error keeps its category, not the 'came back empty' flag (#159)"
    assert len(_markers()) == 1, f"the marker must persist -- still retryable: {_markers()}"


def test_retry_nonlive_clean_empty_is_no_speech_drops_marker():
    """The #138 behavior change on the retry path: a non-live engine that ran CLEAN
    and returned empty now earns the no-speech verdict -- the marker is dropped and
    the slot resolved, exactly as a clean Soniox Live retry chain does."""
    _reset()
    rec = _armed_rec()
    app = _FakeApp()
    task, errors = _drive_retry(app, rec, 111, _FakeFile())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.no_speech is True, "a clean-empty non-live retry now earns no-speech (#138)"
    assert task.is_error is False
    assert app.resolve_failed_called is True, "the slot is resolved -- retry cannot help"
    assert _markers() == [], f"the marker must be cleared: {_markers()}"


def test_retry_live_clean_empty_is_no_speech_drops_marker():
    """A clean-empty Soniox Live retry chain is a final 'no speech' verdict:
    the marker is dropped (a second Ctrl+Alt+R finds nothing to retry)."""
    _reset()
    rec = _armed_rec()
    app = _FakeApp(fallback_result=("", "", False, None))
    task, errors = _drive_retry(app, rec, 12, _FakeLive())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.no_speech is True, "a clean live retry chain earns the no-speech verdict"
    assert task.is_error is False
    assert app.resolve_failed_called is True, "the slot is resolved -- retry cannot help"
    assert _markers() == [], f"the marker must be cleared: {_markers()}"


def test_retry_live_transport_error_keeps_marker():
    """A Soniox Live retry chain that hit a transport error stays retryable AND
    resets the announcement (delete + fresh write -> exactly one un-announced
    marker); the chain's reason lands on the task (#138)."""
    _reset()
    rec = _armed_rec()
    app = _FakeApp(fallback_result=("", "", True, "service-error"))
    task, errors = _drive_retry(app, rec, 13, _FakeLive())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.is_error is True, "a live transport error retry stays retryable"
    assert task.no_speech is False
    assert task.error_reason == "service-error", f"the chain's reason must land on the task: {task.error_reason}"
    assert task.error_provider == "Soniox", "the failing family is Soniox (live + its file lane, #159)"
    assert task.error_inconclusive is True, \
        "a live retry FAILED came via the empty async lane -> the 'came back empty' flag (#159)"
    assert app.resolve_failed_called is False
    assert _markers() == ["voice_20260718_085900_001_d1000.needsretry"], \
        f"exactly one un-announced marker must remain: {_markers()}"


def test_insession_live_auth_error_not_inconclusive():
    """#159: a Soniox Live chain that failed on AUTH (a rejected key) stays FAILED +
    retryable, but must NOT be marked inconclusive -- auth is a conclusive verdict,
    so the panel gives the auth guidance (fix the key in Settings), never the
    'came back empty' line. Mirror of the transport-error case with
    reason=auth: without the fix, the live lane would flag inconclusive and the auth
    category would be swallowed."""
    _reset()
    app = _FakeApp(fallback_result=("", "", True, "auth"))   # chain rejected on auth
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=91,
        timestamp="20260718_090000_091", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "an auth failure stays retryable"
    assert task.error_reason == "auth", f"the chain's reason must land on the task: {task.error_reason}"
    assert task.error_provider == "Soniox", "the failing family is Soniox (live + its file lane, #159)"
    assert task.error_inconclusive is False, \
        "auth is conclusive -- never the 'came back empty' inconclusive flag (#159)"
    assert app.record_failed_called is True


def test_retry_live_auth_error_not_inconclusive():
    """#159 on the retry path: a Soniox Live retry chain that failed on AUTH stays
    retryable but must NOT be inconclusive -- the panel gives the auth guidance, not
    the 'came back empty' line. Mirror of test_retry_live_transport_error_keeps_marker
    with reason=auth."""
    _reset()
    rec = _armed_rec()
    app = _FakeApp(fallback_result=("", "", True, "auth"))
    task, errors = _drive_retry(app, rec, 93, _FakeLive())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.is_error is True, "a live auth-error retry stays retryable"
    assert task.error_reason == "auth", f"the chain's reason must land on the task: {task.error_reason}"
    assert task.error_provider == "Soniox", "the failing family is Soniox (live + its file lane, #159)"
    assert task.error_inconclusive is False, \
        "auth is conclusive -- never the 'came back empty' inconclusive flag (#159)"
    assert app.resolve_failed_called is False


def test_insession_live_credits_error_not_inconclusive():
    """#179: a Soniox Live chain whose file-fallback hit a 402 (no-credit) stays
    FAILED + retryable, but must NOT be marked inconclusive -- an empty balance is a
    conclusive verdict, so the panel gives the credits guidance (top up your
    balance), never the 'came back empty' line. This is the trap the
    #179 fix guards: without excluding no-credit alongside auth at the in-session
    inconclusive site, the live lane would flag inconclusive on the default engine and
    the 402 message would never appear. Mirror of the auth guard with reason=no-credit."""
    _reset()
    app = _FakeApp(fallback_result=("", "", True, "no-credit"))   # chain hit a 402
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=94,
        timestamp="20260718_090000_094", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "a no-credit failure stays retryable"
    assert task.error_reason == "no-credit", f"the chain's reason must land on the task: {task.error_reason}"
    assert task.error_provider == "Soniox", "the failing family is Soniox (live + its file lane, #159)"
    assert task.error_inconclusive is False, \
        "no-credit is conclusive -- never the 'came back empty' inconclusive flag (#179)"
    assert app.record_failed_called is True


def test_retry_live_credits_error_not_inconclusive():
    """#179 on the retry path: a Soniox Live retry chain that failed on a 402
    (no-credit) stays retryable but must NOT be inconclusive -- the panel gives the
    credits guidance, not the 'came back empty' line. Mirror of the auth retry guard
    with reason=no-credit; guards the retry-lane inconclusive site."""
    _reset()
    rec = _armed_rec()
    app = _FakeApp(fallback_result=("", "", True, "no-credit"))
    task, errors = _drive_retry(app, rec, 95, _FakeLive())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.is_error is True, "a live no-credit-error retry stays retryable"
    assert task.error_reason == "no-credit", f"the chain's reason must land on the task: {task.error_reason}"
    assert task.error_provider == "Soniox", "the failing family is Soniox (live + its file lane, #159)"
    assert task.error_inconclusive is False, \
        "no-credit is conclusive -- never the 'came back empty' inconclusive flag (#179)"
    assert app.resolve_failed_called is False


# ---- #141/#212: the async-engine outage hole ------------------------------
# Tier 1 drives the REAL fallback chain against a faked async stage, so the
# wiring from the sink through _try_fallback / _run_empty_transcript_fallback to
# the verdict is exercised end to end. Since #212 the async stage is the only
# file stage for every duration.
def test_long_async_outage_keeps_marker():
    """The #141 regression: a long recording whose Live transcript came back
    empty, run through the REAL fallback chain while the async engine is down,
    must be FAILED + retryable -- never the honest 'no speech' verdict. Before the
    fix the async stage swallowed the outage and reported clean-empty, so
    any_error stayed False and the recording earned a false no-speech verdict
    (marker deleted)."""
    _reset()
    app = _RealChainApp(_FakeAsyncOutage())
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=60.0, sequence_number=41,
        timestamp="20260718_090000_041", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "a long-recording async outage must be a FAILED verdict (#141)"
    assert task.no_speech is False, "the async outage must NOT read as no_speech (the regression)"
    assert task.error_reason == "service-error", \
        f"an errored stage with no reason must default to service-error (#138): {task.error_reason}"
    assert app.record_failed_called is True, "the retry slot must be armed"


def test_long_async_outage_reason_propagates_to_task():
    """A categorized async outage (the real class stamps a reason since #138) must
    propagate that reason through the fallback chain to the failed task."""
    _reset()
    app = _RealChainApp(_FakeAsyncOutageReason())
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=60.0, sequence_number=411,
        timestamp="20260718_090000_411", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert task.is_error is True, "a categorized async outage is still FAILED"
    assert task.error_reason == "no-connection", \
        f"the async stage's specific reason must reach the task (#138): {task.error_reason}"


def test_long_async_clean_empty_is_no_speech():
    """The honest #133 verdict must SURVIVE the #141 fix: an async stage that
    completed clean and found nothing (never touched the sink) still earns 'no
    speech' and arms no retry. The fix must not blanket-force FAILED."""
    _reset()
    app = _RealChainApp(_FakeAsyncCleanEmpty())
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=60.0, sequence_number=42,
        timestamp="20260718_090000_042", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.no_speech is True, "a clean-empty async chain earns the no-speech verdict"
    assert task.is_error is False, "no-speech is not a failure"
    assert app.record_failed_called is False, "no retry slot for a genuinely silent recording"


def test_short_async_outage_keeps_marker():
    """Duration no longer changes routing (#212): a SHORT recording whose Live
    transcript came back empty takes the identical single async stage. With the
    async engine down it must still land on FAILED -- the sink carries the outage
    signal (#141)."""
    _reset()
    app = _RealChainApp(_FakeAsyncOutage())
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=43,
        timestamp="20260718_090000_043", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "a short-recording async outage must be FAILED (#141/#212)"
    assert task.no_speech is False
    assert task.error_reason == "service-error", \
        f"the async stage's outage reason (defaulted) must reach the task (#138): {task.error_reason}"
    assert app.record_failed_called is True


# ---- #138/#212: the selected soniox slot routes like every async engine -----
# The soniox slot is now a SonioxAsyncTranscriber (its own class serves it, #212),
# so the worker threads error_sink straight into it and tags it via engine_code --
# no per-call engine sink, no hybrid wrapper. A selected-slot outage must still
# reach FAILED (not a false NO SPEECH) and a clean-empty must earn no-speech;
# these drive the REAL async engine TYPE (a no-init subclass) end to end through
# the worker, plus a token-tag pin on the worker's else-branch.
def test_soniox_slot_tags_async_token():
    """engine_code tags a selected soniox slot straight from its type now (no
    per-call sink): the async engine maps to the Son-v6 token."""
    slot = tr.SonioxAsyncTranscriber.__new__(tr.SonioxAsyncTranscriber)
    assert tr.engine_code(slot) == tr.ENGINE_TOKENS["soniox_async"] == "Son-v6"


def test_async_get_name_mirrors_display_name():
    """get_name() reflects self.display_name -- it is not hard-wired (#212). One
    async class serves two callers: the selected soniox slot is built with its
    API_DISPLAY label, which the console shows; the empty-live fallback
    keeps the version-neutral default "Soniox async" in its own file logs. A
    regression that pinned get_name() to a constant -- or flipped the fallback
    default -- would silently break one of the two, and the suite would stay green."""
    named = tr.SonioxAsyncTranscriber.__new__(tr.SonioxAsyncTranscriber)
    named.display_name = "Soniox"
    assert named.get_name() == "Soniox"

    fallback = tr.SonioxAsyncTranscriber.__new__(tr.SonioxAsyncTranscriber)
    fallback.display_name = "Soniox async"
    assert fallback.get_name() == "Soniox async"

    # The empty-live caller omits display_name, so the constructor default must
    # stay the specific "Soniox async" (never the console label) for its logs.
    default = inspect.signature(
        tr.SonioxAsyncTranscriber.__init__).parameters["display_name"].default
    assert default == "Soniox async", default


def test_insession_soniox_slot_async_outage_is_failed():
    """An async outage while the soniox slot is selected must reach FAILED +
    retryable with the reason on the task -- never a false NO SPEECH (#138)."""
    _reset()
    app = _FakeApp()
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=60.0, sequence_number=51,
        timestamp="20260718_090000_051", transcriber=_FakeAsyncOutageReason())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "an async outage on the soniox slot must be FAILED (#138)"
    assert task.no_speech is False, "must NOT read as no_speech (the #141-class regression on the slot)"
    assert task.error_reason == "no-connection", \
        f"the async reason must thread through the slot's sink to the task: {task.error_reason}"
    assert app.record_failed_called is True


def test_insession_soniox_slot_async_clean_empty_is_no_speech():
    """The boundary: the soniox slot running clean and empty (async completed, no
    error) earns the no-speech verdict -- the slot must not spuriously set the sink."""
    _reset()
    app = _FakeApp()
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=60.0, sequence_number=52,
        timestamp="20260718_090000_052", transcriber=_FakeAsyncCleanEmpty())
    task = app.output_manager.tasks[-1]
    assert task.no_speech is True, "a clean-empty soniox slot earns no-speech (#138)"
    assert task.is_error is False
    assert task.error_reason is None
    assert app.record_failed_called is False


def test_try_fallback_async_tuple_semantics():
    """The narrowest pin on the wiring: _try_fallback returns (transcript, errored,
    reason), all three driven purely by the async stage's sink -- outage-without-
    reason -> ('', True, None) (the aggregator defaults it later), categorized
    outage -> ('', True, 'no-connection'), clean empty -> ('', False, None),
    success -> (text, False, None)."""
    _reset()
    mp3 = _mp3("voice_20260718_090000_044")
    assert _RealChainApp(_FakeAsyncOutage())._try_fallback(
        mp3_path=mp3, duration=60.0,
        sequence_number=44, thread_name="T") == ("", True, None)
    assert _RealChainApp(_FakeAsyncOutageReason())._try_fallback(
        mp3_path=mp3, duration=60.0,
        sequence_number=44, thread_name="T") == ("", True, "no-connection")
    assert _RealChainApp(_FakeAsyncCleanEmpty())._try_fallback(
        mp3_path=mp3, duration=60.0,
        sequence_number=44, thread_name="T") == ("", False, None)
    assert _RealChainApp(_FakeAsyncSuccess())._try_fallback(
        mp3_path=mp3, duration=60.0,
        sequence_number=44, thread_name="T") == ("recovered text", False, None)


def test_retry_long_async_outage_marker_persists():
    """The manual retry path inherits the #141 fix for free. A long-recording
    Ctrl+Alt+R whose Live chain hits an async outage keeps exactly one un-announced
    marker and is NOT a no-speech verdict -- the mirror of
    test_retry_live_transport_error_keeps_marker, but through the REAL chain."""
    _reset()
    rec = _armed_rec(ts="20260718_085900_041", dur=60.0)
    app = _RealChainApp(_FakeAsyncOutage())
    task, errors = _drive_retry(app, rec, 45, _FakeLive())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.is_error is True, "a long-recording async outage retry stays retryable (#141)"
    assert task.no_speech is False
    assert task.error_reason == "service-error", \
        f"the retry path must also carry the outage reason (#138): {task.error_reason}"
    assert app.resolve_failed_called is False
    assert _markers() == ["voice_20260718_085900_041_d60000.needsretry"], \
        f"exactly one un-announced marker must remain: {_markers()}"


# ---- #141 Tier 2: the four error channels of the REAL SonioxAsyncTranscriber --
# Tier 1 proves the wiring; these prove each real error return actually sets the
# sink (and a completed-but-empty run does not). The real transcribe() does
# `import httpx` per call, so a sys.modules["httpx"] swap reaches its
# httpx.Client; post/get are scripted, delete records what the clean-up
# asks to remove.
class _FakeResp:
    """A scripted httpx response: json() returns the stored dict; raise_for_status
    raises the stored exception (an HTTP error) or is a no-op."""
    def __init__(self, json_data=None, raise_exc=None):
        self._json = json_data or {}
        self._raise = raise_exc

    def json(self):
        return self._json

    def raise_for_status(self):
        if self._raise is not None:
            raise self._raise


class _FakeHTTPStatusError(Exception):
    """Stand-in for httpx.HTTPStatusError: carries .response.status_code so the
    real handler's 401-vs-generic split works. With a `body`, .response.json()
    returns it -- the ElevenLabs handler reads the error body (#343)."""
    def __init__(self, status_code, body=None):
        super().__init__(f"HTTP {status_code}")
        self.response = types.SimpleNamespace(status_code=status_code)
        if body is not None:
            self.response.json = lambda: body


def _make_fake_httpx(post_script, get_script, deleted=None, delete_fails=(),
                     delete_sent=None, clients=None):
    """A scriptable stand-in for the per-call `import httpx`, as transcribe() uses
    it: one Client per call. post/get pop the next scripted item -- a _FakeResp is
    returned, an Exception instance is raised on the call (a network failure).
    delete appends its URL to `deleted` when one is given, and the headers its
    client was built with plus its timeout to `delete_sent` when that is given,
    then raises if the URL ends with one of `delete_fails`. Every client built is
    appended to `clients` when that is given and counts its close() calls; once
    closed it refuses every request, as httpx's own client does."""
    mod = types.ModuleType("httpx")
    mod.HTTPStatusError = _FakeHTTPStatusError
    mod.Timeout = lambda *a, **k: (a, k)   # the ElevenLabs post builds one (#343)
    posts = iter(post_script)
    gets = iter(get_script)

    def _serve(it):
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    deletes = [] if deleted is None else deleted

    def _delete(url, **k):
        deletes.append(url)
        if url.endswith(tuple(delete_fails)):
            raise ConnectionError("handshake timed out")
        return types.SimpleNamespace(status_code=200)

    class _Client:
        def __init__(self, *a, headers=None, **k):
            self.headers, self.closes = headers, 0
            if clients is not None:
                clients.append(self)

        def _refuse_if_closed(self):
            if self.closes:
                raise RuntimeError("Cannot send a request, as the client has been closed.")

        def post(self, *a, **k):
            self._refuse_if_closed()
            return _serve(posts)

        def get(self, *a, **k):
            self._refuse_if_closed()
            return _serve(gets)

        def delete(self, url, **k):
            self._refuse_if_closed()
            if delete_sent is not None:
                delete_sent.append((self.headers, k.get("timeout")))
            return _delete(url, **k)

        def close(self):
            self.closes += 1

    mod.Client = _Client
    return mod


class _CapturedThread:
    """threading.Thread as both file engines start their clean-up at the provider:
    start() only files the thread, so the case decides when -- and whether -- it
    runs."""
    started, fail_start = [], False

    def __init__(self, target=None, args=(), name=None, daemon=None):
        self.target, self.args, self.daemon = target, args, daemon

    def start(self):
        if _CapturedThread.fail_start:
            raise RuntimeError("can't create new thread at interpreter shutdown")
        _CapturedThread.started.append(self)


def _run_async_case(post_script, get_script, deleted=None, delete_fails=(), trace=None,
                    start_fails=False):
    """Run the REAL SonioxAsyncTranscriber.transcribe against a scripted fake httpx.
    Returns (result_text, error_tag, error_messages). Constructs the transcriber
    via __new__ (no API-key check, no archive-dir side effect) and restores every
    patched global plus the sys.modules['httpx'] entry afterwards.

    The clean-up thread transcribe() starts (#345) is captured, not started, so
    no case leaves a real thread behind: once the call has returned, the case
    notes what was deleted by then and which clients were still open, runs each
    captured thread by hand (still behind the fake httpx), and fills `trace` when
    given -- `at_return` (deletes before the return), `daemon` (each thread's
    flag), `open_at_return` (per client built), `closes` (each client's close()
    count at the end) and `timeouts` (each DELETE's). `start_fails` makes the
    thread refuse to start, as it can at interpreter shutdown."""
    engine = tr.SonioxAsyncTranscriber.__new__(tr.SonioxAsyncTranscriber)
    engine.api_key = "test-key"
    engine.headers = {"Authorization": "Bearer test-key"}
    engine.display_name = "Soniox async"   # get_name() reads this since #212
    dummy = ah.ARCHIVE_FOLDER / "async_input.mp3"
    dummy.touch()
    deleted = [] if deleted is None else deleted
    trace = {} if trace is None else trace
    clients, sent = [], []

    fake = _make_fake_httpx(post_script, get_script, deleted, delete_fails, sent, clients)
    saved_httpx = sys.modules.get("httpx")
    saved_max = tr.SONIOX_ASYNC_MAX_POLL_ATTEMPTS
    saved_interval = tr.SONIOX_ASYNC_POLL_INTERVAL
    saved_threading = tr.threading
    sys.modules["httpx"] = fake
    tr.SONIOX_ASYNC_MAX_POLL_ATTEMPTS = 3
    tr.SONIOX_ASYNC_POLL_INTERVAL = 0
    _CapturedThread.started, _CapturedThread.fail_start = [], start_fails
    tr.threading = types.SimpleNamespace(Thread=_CapturedThread)

    lg = logging.getLogger("Thoughtborne")
    old = lg.level
    lg.setLevel(logging.ERROR)
    cap = _ErrorCapture()
    lg.addHandler(cap)
    tag = tr._ErrorTag()
    try:
        result = engine.transcribe(str(dummy), 60.0, error_sink=tag)
        trace["at_return"] = list(deleted)
        trace["daemon"] = [t.daemon for t in _CapturedThread.started]
        trace["open_at_return"] = [c.closes == 0 for c in clients]
        for thread in _CapturedThread.started:
            thread.target(*thread.args)
        trace["closes"] = [c.closes for c in clients]
        trace["timeouts"] = [timeout for _, timeout in sent]
    finally:
        lg.removeHandler(cap)
        lg.setLevel(old)
        if saved_httpx is not None:
            sys.modules["httpx"] = saved_httpx
        else:
            sys.modules.pop("httpx", None)
        tr.SONIOX_ASYNC_MAX_POLL_ATTEMPTS = saved_max
        tr.SONIOX_ASYNC_POLL_INTERVAL = saved_interval
        tr.threading = saved_threading
    return result, tag, cap.records


def test_async_job_error_sets_sink_and_reads_error_message():
    """Job-level status 'error' -> "" and sink set; the log carries the actual
    error_message field (pins the field fix: the old code read 'error' and
    always logged 'Unknown error')."""
    text, tag, errors = _run_async_case(
        post_script=[_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})],
        get_script=[_FakeResp({"status": "error", "error_message": "insufficient credits"})],
    )
    assert text == "", "a job error returns empty"
    assert tag.errored is True, "a job error must set the sink (#141)"
    assert tag.reason == "service-error", f"a job error is service-error (#138): {tag.reason}"
    assert any("insufficient credits" in m for m in errors), \
        f"the real error_message must be logged, not 'Unknown error': {errors}"


def test_async_poll_timeout_sets_sink():
    """Status stays 'processing' past the poll cap -> "" and sink set."""
    text, tag, _ = _run_async_case(
        post_script=[_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})],
        get_script=[_FakeResp({"status": "processing"})] * 3,
    )
    assert text == ""
    assert tag.errored is True, "a poll timeout must set the sink (#141)"
    assert tag.reason == "service-error", f"a poll timeout is service-error (#138): {tag.reason}"


def test_async_http_error_sets_sink():
    """A non-401 HTTP error on upload -> "" and sink set (the generic branch)."""
    text, tag, _ = _run_async_case(
        post_script=[_FakeResp(raise_exc=_FakeHTTPStatusError(500))],
        get_script=[],
    )
    assert text == ""
    assert tag.errored is True, "an HTTP transport error must set the sink (#141)"
    assert tag.reason == "service-error", f"a 500 maps to service-error (#138): {tag.reason}"


def test_async_http_400_adds_cause_hint():
    """#334: a 400 means Soniox rejected the request itself (measured: a hand-edited
    vocabulary block with a wrong shape), so the same branch leaves one extra static
    cause hint in the log naming the plausible causes -- the FAILED panel now says
    "investigate" and this is what there is to find. Every other status keeps today's
    single line. The only condition is the status code already in hand: no parsing of
    the provider's error body. The substring pins are order-neutral, so rewording or
    reordering the causes is free as long as the pinned substrings survive -- the
    reject sentence, "personal settings" and "vocabulary". The other two causes (an
    API change, a problem on Soniox's side) are unpinned: dropping them stays green."""
    text, tag, errors = _run_async_case(
        post_script=[_FakeResp(raise_exc=_FakeHTTPStatusError(400))],
        get_script=[],
    )
    assert text == ""
    assert tag.errored is True, "a 400 must set the sink (#141)"
    assert tag.reason == "service-error", f"a 400 keeps its #138 category: {tag.reason}"
    hints = [m for m in errors if "rejected the request itself" in m]
    assert len(hints) == 1, f"exactly one 400 cause hint expected: {errors}"
    assert "personal settings" in hints[0] and "vocabulary" in hints[0], \
        f"the hint must name the hand-edited settings as a cause: {hints[0]!r}"
    # The counter-lane: any other status stays at the single line it logs today.
    _, _, errors_500 = _run_async_case(
        post_script=[_FakeResp(raise_exc=_FakeHTTPStatusError(500))],
        get_script=[],
    )
    assert len(errors_500) == 1 and "rejected the request itself" not in errors_500[0], \
        f"a non-400 must not grow the hint: {errors_500}"


def test_async_network_exception_sets_sink():
    """A raised network exception on upload -> "" and sink set (the generic
    except Exception branch -- DNS/connect failure, the real outage shape)."""
    text, tag, _ = _run_async_case(
        post_script=[ConnectionError("network down")],
        get_script=[],
    )
    assert text == ""
    assert tag.errored is True, "a network exception must set the sink (#141)"
    assert tag.reason == "no-connection", \
        f"a connect/timeout exception maps to no-connection (#138): {tag.reason}"


def test_async_clean_empty_does_not_set_sink():
    """A completed run with empty text -> "" but the sink stays False -- the
    boundary the honest no-speech verdict depends on (must never read as errored)."""
    text, tag, _ = _run_async_case(
        post_script=[_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})],
        get_script=[_FakeResp({"status": "completed"}), _FakeResp({"text": ""})],
    )
    assert text == "", "a completed-but-empty run returns empty"
    assert tag.errored is False, "a clean-empty run must NOT set the sink (#141)"
    assert tag.reason is None, "a clean-empty run carries no reason (#138)"


def test_async_failed_status_sets_sink():
    """The 'failed' terminal status (named by the docs' error page, not by the API
    reference, which says 'error') is caught immediately
    in the error/failed branch and sets the sink -- not left to poll into the
    timeout. The branch-specific 'transcription failed' log line pins it to that
    branch: it is absent from both the poll-timeout and the generic-exception
    paths, so dropping 'failed' from the status tuple in transcriber.py (which then
    polls until the single scripted response is exhausted and lands in the
    catch-all) turns this case red."""
    text, tag, errors = _run_async_case(
        post_script=[_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})],
        get_script=[_FakeResp({"status": "failed"})],
    )
    assert text == ""
    assert tag.errored is True, "the 'failed' status branch must set the sink (#141)"
    assert tag.reason == "service-error", f"the 'failed' status is service-error (#138): {tag.reason}"
    assert any("transcription failed" in m for m in errors), \
        f"'failed' must hit the error/failed branch, not poll into the timeout: {errors}"


def test_async_cleanup_runs_on_every_path():
    """#342, #345: what a transcribe() call created on Soniox's servers -- the
    transcription, then the uploaded file -- is deleted on every path, and nothing
    else is. It matters because an account keeps at most 1,000 files and an upload
    left behind stays for 30 days. Since #345 the deletes come after the text is
    returned: nothing is deleted when the call returns, and one non-daemon thread
    takes over the call's client, sends them with SONIOX_ASYNC_DELETE_TIMEOUT_SECONDS
    and closes it. The call builds that one client and no other (#342), and closes
    it at once when it created nothing. The two deletes stand apart: a failed
    transcription delete must not cost the file delete (the live log has deletes
    failing on the handshake). A thread that will not start must not cost the
    clean-up either: it then runs before the return, as it did before #345."""
    created = [_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})]
    done = [_FakeResp({"status": "completed"}), _FakeResp({"text": "x"})]
    both = ["/v1/transcriptions/tx1", "/v1/files/file1"]
    cases = (
        # (label, posts, gets, delete_fails, start_fails, deleted at return, deleted)
        ("success", created, done, (), False, [], both),
        ("job error", created, [_FakeResp({"status": "error"})], (), False, [], both),
        ("poll timeout", created, [_FakeResp({"status": "processing"})] * 3, (), False,
         [], both),
        ("poll raises", created, [ConnectionError("blip")], (), False, [], both),
        ("transcript fetch rejected", created,
         [_FakeResp({"status": "completed"}),
          _FakeResp(raise_exc=_FakeHTTPStatusError(500))], (), False, [], both),
        ("create fails", [_FakeResp({"id": "file1"}), ConnectionError("blip")], [], (),
         False, [], ["/v1/files/file1"]),
        ("upload fails", [ConnectionError("blip")], [], (), False, [], []),
        ("transcription delete fails", created, done, (both[0],), False, [], both),
        ("thread will not start", created, done, (), True, both, both),
    )
    for label, posts, gets, fails, start_fails, want_at_return, want in cases:
        deleted, trace = [], {}
        _run_async_case(list(posts), list(gets), deleted, fails, trace, start_fails)
        at_return = [url.replace(tr.SONIOX_ASYNC_API_BASE, "") for url in trace["at_return"]]
        got = [url.replace(tr.SONIOX_ASYNC_API_BASE, "") for url in deleted]
        threaded = 1 if want and not start_fails else 0
        assert at_return == want_at_return, \
            f"{label}: deleted {at_return} before returning the text, expected {want_at_return}"
        assert got == want, f"{label}: cleanup deleted {got}, expected {want}"
        assert trace["daemon"] == [False] * threaded, \
            f"{label}: clean-up thread(s) with daemon={trace['daemon']}, expected " \
            f"{'one non-daemon thread' if threaded else 'none'}"
        assert trace["open_at_return"] == [bool(threaded)], \
            f"{label}: client(s) open at the return: {trace['open_at_return']}, " \
            f"expected the call's one client, {'open for' if threaded else 'closed without'} " \
            f"a clean-up thread"
        assert trace["closes"] == [1], \
            f"{label}: close() count per client {trace['closes']}, expected one client " \
            f"closed once"
        assert trace["timeouts"] == [tr.SONIOX_ASYNC_DELETE_TIMEOUT_SECONDS] * len(got), \
            f"{label}: delete timeouts {trace['timeouts']}, expected " \
            f"SONIOX_ASYNC_DELETE_TIMEOUT_SECONDS on each"


# ---- #343: the REAL ElevenLabsTranscriber against the same scripted fake httpx --
# The workshop .env can hold a real ElevenLabs key and `import config` reads it, so
# besides the module fake every run swaps the key copy in `transcriber` for a fake
# one and makes httpx's real transport refuse: a module-level `import httpx` slipping
# past the sys.modules fake fails here before anything is sent.
_FAKE_EL_KEY = "not-a-real-elevenlabs-key"


def _run_elevenlabs_case(post_script, deleted=None, delete_fails=(), trace=None,
                         start_fails=False):
    """Run the REAL ElevenLabsTranscriber.transcribe against the scripted fake
    httpx. Returns (result_text, error_tag, error_messages); every patched global
    and the sys.modules['httpx'] entry are restored afterwards.

    The delete thread transcribe() starts is captured, not started: once the call
    has returned, the case notes what was deleted by then, runs each captured
    thread by hand (still behind the fake httpx), and fills `trace` when given --
    `at_return` (deletes before the return), `daemon` (each thread's flag),
    `sleeps` (each wait, with how many deletes had happened before it) and `sent`
    (each DELETE's client headers and timeout). The wait is
    recorded, never slept, so no case depends on timing. `start_fails` makes the
    thread refuse to start, as it does at interpreter shutdown."""
    engine = tr.ElevenLabsTranscriber.__new__(tr.ElevenLabsTranscriber)
    engine.display_name = "ElevenLabs Scribe v3"
    engine.headers = {"xi-api-key": _FAKE_EL_KEY}
    dummy = ah.ARCHIVE_FOLDER / "elevenlabs_input.mp3"
    dummy.touch()
    deleted = [] if deleted is None else deleted
    trace = {} if trace is None else trace
    sleeps, sent = [], []

    def no_network(self, request):
        raise AssertionError(f"the case reached httpx's real transport: {request.url}")

    real_httpx = sys.modules.get("httpx")
    transport = getattr(real_httpx, "HTTPTransport", None)
    saved_handle = transport.handle_request if transport is not None else None
    saved = (tr.ELEVENLABS_API_KEY, tr.threading, tr.time)
    sys.modules["httpx"] = _make_fake_httpx(post_script, [], deleted, delete_fails, sent)
    tr.ELEVENLABS_API_KEY = _FAKE_EL_KEY
    _CapturedThread.started, _CapturedThread.fail_start = [], start_fails
    tr.threading = types.SimpleNamespace(Thread=_CapturedThread)
    tr.time = types.SimpleNamespace(
        time=saved[2].time, sleep=lambda seconds: sleeps.append((seconds, len(deleted))))
    if transport is not None:
        transport.handle_request = no_network

    lg = logging.getLogger("Thoughtborne")
    old = lg.level
    lg.setLevel(logging.ERROR)
    cap = _ErrorCapture()
    lg.addHandler(cap)
    tag = tr._ErrorTag()
    try:
        result = engine.transcribe(str(dummy), 60.0, error_sink=tag)
        trace["at_return"] = list(deleted)
        trace["daemon"] = [t.daemon for t in _CapturedThread.started]
        for thread in _CapturedThread.started:
            thread.target(*thread.args)
        trace["sleeps"] = sleeps
        trace["sent"] = sent
    finally:
        lg.removeHandler(cap)
        lg.setLevel(old)
        if real_httpx is not None:
            sys.modules["httpx"] = real_httpx
        else:
            sys.modules.pop("httpx", None)
        tr.ELEVENLABS_API_KEY, tr.threading, tr.time = saved
        if transport is not None:
            transport.handle_request = saved_handle
    return result, tag, cap.records


# Measured ElevenLabs error bodies (2026-11-17; request ids dropped), as `detail`.
_EL_WRONG_KEY = {"type": "authentication_error", "code": "invalid_api_key",
                 "message": "API key is invalid.", "status": "invalid_api_key",
                 "param": "api_key"}                                          # HTTP 400
_EL_UNPREFIXED_KEY = {"type": "authentication_error", "code": "unauthorized",
                      "message": "Invalid API key", "status": "invalid_api_key"}  # 401
_EL_NO_PERMISSION = {"type": "authentication_error", "code": "unauthorized",
                     "message": "The API key you used is missing the permission "
                                "user_read to execute this operation.",
                     "status": "missing_permissions"}                         # HTTP 401
_EL_QUOTA = {"type": "invalid_request", "code": "quota_exceeded",
             "message": "This request exceeds your quota of 10000.",
             "status": "quota_exceeded"}                                      # HTTP 401
_EL_BAD_TERM = {"type": "validation_error", "code": "invalid_parameters",
                "message": "All keywords must be at most 50 characters.",
                "status": "invalid_keyword_length", "param": "keyterms"}      # HTTP 400


def test_elevenlabs_sink_and_cleanup_on_every_path():
    """#343: each path of the real transcribe() -- what it returns, what it reports
    to the per-call sink (D-001: a clean-empty run must not arm a retry), and which
    stored transcript it asks ElevenLabs to delete, and when: nothing before the
    text is returned, then one non-daemon thread that waits
    ELEVENLABS_DELETE_DELAY_SECONDS and only then sends the DELETE, with the key
    header and ELEVENLABS_DELETE_TIMEOUT_SECONDS on it. Every answer
    with a transcription_id has something to delete, an empty one too; an error
    answer creates nothing, and a timeout leaves the id unknown. A delete that
    fails, or a thread that will not start, must not cost the text. This holds the
    call, not its effect at ElevenLabs -- that takes the real service."""
    tx = "/v1/speech-to-text/transcripts/"
    wait = [(tr.ELEVENLABS_DELETE_DELAY_SECONDS, 0)]
    cases = (
        # (label, post script, delete_fails, start_fails, text, errored, reason, deleted)
        ("success", [_FakeResp({"text": "Äh, Hallo Welt", "transcription_id": "tx1"})],
         (), False, "Hallo Welt", False, None, [tx + "tx1"]),
        ("clean empty", [_FakeResp({"text": "", "transcription_id": "tx2"})],
         (), False, "", False, None, [tx + "tx2"]),
        ("rejected key",
         [_FakeResp(raise_exc=_FakeHTTPStatusError(400, {"detail": _EL_WRONG_KEY}))],
         (), False, "", True, "auth", []),
        ("read timeout", [type("ReadTimeout", (Exception,), {})("timed out")],
         (), False, "", True, "no-connection", []),
        ("delete fails", [_FakeResp({"text": "Hallo", "transcription_id": "tx3"})],
         (tx + "tx3",), False, "Hallo", False, None, [tx + "tx3"]),
        ("thread will not start", [_FakeResp({"text": "Hallo", "transcription_id": "tx4"})],
         (), True, "Hallo", False, None, []),
    )
    for label, posts, fails, start_fails, want_text, errored, reason, want_deleted in cases:
        deleted, trace = [], {}
        text, tag, _ = _run_elevenlabs_case(posts, deleted, fails, trace, start_fails)
        assert text == want_text, f"{label}: returned {text!r}, expected {want_text!r}"
        assert (tag.errored, tag.reason) == (errored, reason), \
            f"{label}: sink is {(tag.errored, tag.reason)}, expected {(errored, reason)}"
        assert trace["at_return"] == [], \
            f"{label}: deleted {trace['at_return']} before returning the text"
        got = [url.replace(tr.ELEVENLABS_API_BASE, "") for url in deleted]
        assert got == want_deleted, f"{label}: cleanup deleted {got}, expected {want_deleted}"
        assert trace["daemon"] == [False] * len(want_deleted), \
            f"{label}: delete thread(s) with daemon={trace['daemon']}, expected one " \
            f"non-daemon thread per delete"
        assert trace["sleeps"] == wait * len(want_deleted), \
            f"{label}: waits {trace['sleeps']} (seconds, deletes before), expected " \
            f"{wait * len(want_deleted)} -- the wait has to come before the DELETE"
        sent = [({"xi-api-key": _FAKE_EL_KEY}, tr.ELEVENLABS_DELETE_TIMEOUT_SECONDS)]
        assert trace["sent"] == sent * len(want_deleted), \
            f"{label}: DELETE(s) sent as {trace['sent']} (client headers, timeout), " \
            f"expected {sent * len(want_deleted)}"


def test_elevenlabs_http_error_reads_the_body():
    """#343: a key ElevenLabs rejects comes back as 400 or 401, a used-up free quota
    as 401 too -- so the handler reads the body. A rejected key prints the [AUTH]
    line naming ELEVENLABS_API_KEY; the quota answer is no-credit and does not.
    Either way one ERROR line keeps what ElevenLabs said, and no ERROR line
    carries the key -- the case sees ERROR records only, so neither the line's
    file-only routing nor the lower levels are held here."""
    for status, detail, reason, auth_line in (
            (400, _EL_WRONG_KEY, "auth", True),
            (401, _EL_QUOTA, "no-credit", False),
            (400, _EL_BAD_TERM, "service-error", False)):
        label = f"{status} {detail['status']}"
        text, tag, errors = _run_elevenlabs_case(
            [_FakeResp(raise_exc=_FakeHTTPStatusError(status, {"detail": detail}))])
        assert text == "" and tag.errored is True, f"{label}: not reported as failed"
        assert tag.reason == reason, f"{label}: reason {tag.reason!r}, expected {reason!r}"
        auth = [m for m in errors if m.startswith("[AUTH]")]
        assert len(auth) == (1 if auth_line else 0), f"{label}: [AUTH] lines {auth}"
        assert all("ELEVENLABS_API_KEY" in m for m in auth), f"{label}: {auth}"
        said = [m for m in errors if m.startswith(f"ElevenLabs answered HTTP {status}")]
        assert len(said) == 1 and detail["status"] in said[0], \
            f"{label}: expected one line with ElevenLabs' own status: {errors}"
        assert not any(_FAKE_EL_KEY in m for m in errors), f"{label}: the key was logged"


def test_elevenlabs_error_reason_maps_measured_bodies():
    """#343: the body-first mapping, on the measured bodies and the status fallback
    (402 and 429 are documented, not measured; a body that is not an object --
    the 422 list form is documented -- falls back to the status)."""
    reason = tr._elevenlabs_error_reason
    assert reason(400, _EL_WRONG_KEY) == "auth"
    assert reason(401, _EL_UNPREFIXED_KEY) == "auth"
    assert reason(401, _EL_NO_PERMISSION) == "auth"
    assert reason(401, _EL_QUOTA) == "no-credit", "a 401 with the quota code is no credit"
    assert reason(400, _EL_BAD_TERM) == "service-error"
    assert reason(402, {}) == "no-credit"
    assert reason(429, {}) == "rate-limited"
    assert reason(500, {}) == "service-error"
    assert reason(401, {}) == "auth", "a 401 without a readable body stays auth"

    detail = tr._elevenlabs_error_detail
    ns = types.SimpleNamespace
    assert detail(ns(json=lambda: {"detail": _EL_QUOTA})) == _EL_QUOTA
    assert detail(ns(json=lambda: {"detail": [{"msg": "x"}]})) == {}
    assert detail(ns(json=lambda: [1, 2])) == {}
    assert detail(ns()) == {}, "a response without a JSON body reads as no detail"


def test_elevenlabs_tags_scribe_token():
    """#343: engine_code names the Scribe engine in archive filenames; without its
    branch the transcript would be filed as 'unknown'."""
    engine = tr.ElevenLabsTranscriber.__new__(tr.ElevenLabsTranscriber)
    assert tr.engine_code(engine) == tr.ENGINE_TOKENS["elevenlabs_scribe"] == "Scribe-v3"


def test_factory_builds_elevenlabs():
    """#343: create_transcriber("elevenlabs") builds the ElevenLabs class under its
    API_DISPLAY label -- the built-in default's own factory branch, which
    test_startup_carousel.py replaces with a fake. Building one sends nothing; the
    module's key copy is faked for the run all the same."""
    saved = tr.ELEVENLABS_API_KEY
    tr.ELEVENLABS_API_KEY = _FAKE_EL_KEY
    try:
        engine = tr.create_transcriber("elevenlabs")
    finally:
        tr.ELEVENLABS_API_KEY = saved
    assert type(engine) is tr.ElevenLabsTranscriber, type(engine).__name__
    assert engine.get_name() == config.API_DISPLAY["elevenlabs"]["label"], engine.get_name()


# ---- #138/#159 direct unit tests for the two coarse-category mappers --------
# _groq_error_reason / _http_status_reason turn a provider failure into the
# coarse reason string #159 shows the user, so pin every branch of each. The http
# mapper is a plain status-code map; the groq mapper runs an isinstance ladder, so
# its inputs are instances of the fake groq hierarchy built in the preamble (real
# groq construction needs httpx Response/Request objects).

def test_groq_error_reason_maps_every_branch():
    # Instances of the fake groq hierarchy (see preamble): isinstance-compatible
    # with the classes _groq_error_reason imports, so every branch runs without
    # the real SDK. The 401-via-APIStatusError case is synthetic (a real 401
    # arrives as AuthenticationError) but it pins the inner status-code line.
    assert tr._groq_error_reason(_FakeGroqAuthenticationError()) == "auth"
    assert tr._groq_error_reason(_FakeGroqAPIConnectionError()) == "no-connection"
    assert tr._groq_error_reason(_FakeGroqAPITimeoutError()) == "no-connection"
    assert tr._groq_error_reason(_FakeGroqRateLimitError()) == "rate-limited"
    assert tr._groq_error_reason(_FakeGroqAPIStatusError(status_code=429)) == "rate-limited"
    assert tr._groq_error_reason(_FakeGroqAPIStatusError(status_code=401)) == "auth"
    assert tr._groq_error_reason(_FakeGroqAPIStatusError(status_code=402)) == "no-credit", \
        "a 402 APIStatusError is an unfunded account -> no-credit (#179)"
    assert tr._groq_error_reason(_FakeGroqAPIStatusError(status_code=500)) == "service-error", \
        "a 5xx APIStatusError is service-error"
    assert tr._groq_error_reason(RuntimeError("boom")) == "service-error", \
        "an uncategorized exception falls through to service-error"


def test_http_status_reason_maps_every_branch():
    assert tr._http_status_reason(401) == "auth"
    assert tr._http_status_reason(402) == "no-credit", \
        "a 402 is an unfunded account -> no-credit (#179)"
    assert tr._http_status_reason(429) == "rate-limited"
    assert tr._http_status_reason(500) == "service-error"
    assert tr._http_status_reason(403) == "service-error", \
        "a non-401/402/429 4xx is service-error (the default)"


# ---- #159: provider-family token + the sink->task->callback path -------------
def test_error_provider_maps_family():
    """#159: _error_provider collapses every transcriber to the short family token
    the FAILED reason line interpolates -- Groq to 'Groq', every Soniox variant
    (live / async upload slot) to 'Soniox'."""
    groq = tr.GroqTranscriber.__new__(tr.GroqTranscriber)   # __new__: no API-key init
    assert tb._error_provider(groq) == "Groq"
    assert tb._error_provider(_FakeLive()) == "Soniox", "Soniox Live -> Soniox"
    assert tb._error_provider(_FakeAsyncCleanEmpty()) == "Soniox", \
        "the Soniox upload slot -> Soniox"
    scribe = tr.ElevenLabsTranscriber.__new__(tr.ElevenLabsTranscriber)
    assert tb._error_provider(scribe) == "ElevenLabs", "Scribe -> ElevenLabs (#343)"


def test_output_manager_forwards_reason_provider_inconclusive():
    """#159 acceptance (consumer side): the OutputManager forwards a FAILED task's
    reason / provider / inconclusive to the on_task_complete callback, so the panel
    can say why. Drives the REAL output loop with a capturing callback."""
    captured = {}
    fired = threading.Event()

    def cb(**kw):
        if kw.get("event") == "failed":
            captured.update(kw)
            fired.set()

    om = OutputManager(on_task_complete_callback=cb)
    try:
        om.add_task(TranscriptionTask(
            sequence_number=0, timestamp="20260722_000000_000",
            is_error=True, is_complete=True,
            error_reason="rate-limited", error_provider="Groq", error_inconclusive=False))
        assert fired.wait(3.0), "the failed callback never fired"
    finally:
        om.stop()
    assert captured.get("kind") == "transcription", f"kind must forward: {captured}"
    assert captured.get("reason") == "rate-limited", f"reason must forward to the callback: {captured}"
    assert captured.get("provider") == "Groq", f"provider must forward to the callback: {captured}"
    assert captured.get("inconclusive") is False, f"inconclusive must forward to the callback: {captured}"


def test_pyautogui_failsafe_off_after_import():
    """#241: importing output_handler must switch pyautogui's corner fail-safe off.
    Left on, a pointer resting in a screen corner makes the post-insert Enter press
    raise, and the insertion handler reports an insert that already landed as a red
    failure. The assignment lands on the stub module installed at the top of this
    file, which makes this the ladder's only guard against the line vanishing --
    e.g. while replacing pyautogui (#257)."""
    failsafe = getattr(sys.modules["pyautogui"], "FAILSAFE", "<unset>")
    assert failsafe is False, \
        f"output_handler must set pyautogui.FAILSAFE = False at import (#241); got {failsafe!r}"


def test_pyautogui_pause_zero_after_import():
    """#345: importing output_handler must set pyautogui's PAUSE to 0. Left at its
    default, every pyautogui call sleeps 0.1 s after it returns -- after the Enter of
    the send variant, a wait that only holds back the console's report and the next
    insert. Same stub-module reading as the fail-safe guard above."""
    pause = getattr(sys.modules["pyautogui"], "PAUSE", "<unset>")
    assert pause == 0, \
        f"output_handler must set pyautogui.PAUSE = 0 at import (#345); got {pause!r}"


CASES = [
    test_write_arms_unannounced,
    test_mark_announced_still_arms_silently,
    test_mark_announced_is_idempotent,
    test_migration_current_format,
    test_legacy_106_bare_name_reconstructs_mp3,
    test_recovered_stems_roundtrip_disjoint,
    test_delete_removes_either_flavor,
    test_supersede_prunes_older_either_flavor_keeps_newest,
    test_multiple_markers_both_arm_then_supersede,
    test_verdict_clean_empty_deletes_marker,
    test_verdict_transport_error_resets_announcement,
    test_orphan_and_unparseable_removed_on_read,
    # #133/#138 verdict routing (drives the real worker methods): the verdict is
    # now single-engine -- clean-empty -> no_speech on ANY engine, errored -> FAILED
    # + reason.
    test_insession_nonlive_errored_empty_is_failed_with_reason,
    test_insession_nonlive_clean_empty_is_no_speech,
    test_insession_live_clean_empty_is_no_speech,
    test_insession_live_transport_error_is_error,
    test_retry_nonlive_errored_empty_keeps_marker_with_reason,
    test_retry_nonlive_clean_empty_is_no_speech_drops_marker,
    test_retry_live_clean_empty_is_no_speech_drops_marker,
    test_retry_live_transport_error_keeps_marker,
    # #159: auth is conclusive -> the live empty-lane must NOT mark it inconclusive
    test_insession_live_auth_error_not_inconclusive,
    test_retry_live_auth_error_not_inconclusive,
    # #179: no-credit (402) is conclusive too -> same carve-out from inconclusive
    test_insession_live_credits_error_not_inconclusive,
    test_retry_live_credits_error_not_inconclusive,
    # #141/#212 async-engine outage -- Tier 1 (real chain, faked async stage)
    test_long_async_outage_keeps_marker,
    test_long_async_outage_reason_propagates_to_task,
    test_long_async_clean_empty_is_no_speech,
    test_short_async_outage_keeps_marker,
    # #138/#212 selected soniox slot routes like every async engine
    test_soniox_slot_tags_async_token,
    test_async_get_name_mirrors_display_name,
    test_insession_soniox_slot_async_outage_is_failed,
    test_insession_soniox_slot_async_clean_empty_is_no_speech,
    test_try_fallback_async_tuple_semantics,
    test_retry_long_async_outage_marker_persists,
    # #141 Tier 2 (real SonioxAsyncTranscriber, scripted fake httpx)
    test_async_job_error_sets_sink_and_reads_error_message,
    test_async_poll_timeout_sets_sink,
    test_async_http_error_sets_sink,
    # #334 the one status-specific lane in that branch: a 400 also logs a cause hint
    test_async_http_400_adds_cause_hint,
    test_async_network_exception_sets_sink,
    test_async_clean_empty_does_not_set_sink,
    test_async_failed_status_sets_sink,
    test_async_cleanup_runs_on_every_path,
    # #343 the real ElevenLabsTranscriber: sink, cleanup, the body-first error mapping
    test_elevenlabs_sink_and_cleanup_on_every_path,
    test_elevenlabs_http_error_reads_the_body,
    test_elevenlabs_error_reason_maps_measured_bodies,
    test_elevenlabs_tags_scribe_token,
    test_factory_builds_elevenlabs,
    # #138/#159 direct unit tests for the two coarse-category mappers
    test_groq_error_reason_maps_every_branch,
    test_http_status_reason_maps_every_branch,
    # #159 provider-family token + the sink->task->callback propagation
    test_error_provider_maps_family,
    test_output_manager_forwards_reason_provider_inconclusive,
    # #241 the corner fail-safe stays off (a landed insert must not report red)
    test_pyautogui_failsafe_off_after_import,
    # #345 and its trailing pause is zero (no wait after the Enter)
    test_pyautogui_pause_zero_after_import,
]


def main():
    failures = []
    try:
        for case in CASES:
            try:
                case()
                print(f"PASS  {case.__name__}")
            except AssertionError as e:
                failures.append((case.__name__, str(e)))
                print(f"FAIL  {case.__name__}: {e}")
            except Exception as e:  # a crash is also a failure
                failures.append((case.__name__, f"{type(e).__name__}: {e}"))
                print(f"ERROR {case.__name__}: {type(e).__name__}: {e}")

        if failures:
            print(f"\nFAIL: {len(failures)}/{len(CASES)} case(s) failed")
            return 1
        print(f"\nOK: all {len(CASES)} retry-marker-lifecycle cases pass")
        return 0
    finally:
        # Every case shares the module-global throwaway archive dir (mkdtemp at
        # import); no case runs past this point, so clearing it here leaves a
        # standalone run no tb_marker_test_* dir behind in /tmp.
        shutil.rmtree(ah.ARCHIVE_FOLDER, ignore_errors=True)
        # The log dir goes the same way -- and unlike the archive dir it needs no
        # re-creation in test_all(): nothing reaches it after the import block,
        # which detaches every handler the thoughtborne import attached.
        shutil.rmtree(_LOG_DIR, ignore_errors=True)


def test_all():
    """The pytest entry point (#242). main()'s finally removes the shared archive
    dir; re-create it so collection order can never strand a later case -- which
    is why under pytest, unlike a standalone run, one empty tb_marker_test_* dir
    is left behind in /tmp."""
    try:
        assert main() == 0
    finally:
        ah.ARCHIVE_FOLDER.mkdir(exist_ok=True)


if __name__ == "__main__":
    sys.exit(main())
