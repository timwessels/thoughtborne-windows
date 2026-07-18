#!/usr/bin/env python3
"""Lifecycle tests for the persistent retry marker (#106/#114/#134/#133/#141).

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
retry_recording_thread) against a minimal fake `self` to pin the #133 verdict
ROUTING: an empty result earns the honest "no speech" verdict ONLY on a clean
Soniox Live fallback chain; a non-live engine (soniox/groq) returning empty --
indistinguishable from a swallowed transport error -- stays the conservative
FAILED + retryable, so the Ctrl+Alt+L engine-switch rescue survives an outage.

  - #141 closes a hole in that live chain on LONG recordings (>= 58 s): the async
    V4 stage is then the only file stage (the V2 sync stage is duration-gated
    off), and it used to swallow its own transport/API errors and report
    clean-but-empty -- so a real outage masqueraded as "no speech" and disarmed
    the retry. Tier 1 drives the REAL fallback chain (_run_empty_transcript_
    fallback / _try_fallback) against a faked V4 stage to prove the outage now
    routes to FAILED + retryable while a genuine clean-empty still earns
    no-speech; Tier 2 exercises the REAL SonioxV4Transcriber.transcribe against a
    scripted fake httpx to prove each of its four error returns sets the per-call
    error sink (and a completed-but-empty run does not).
"""
import sys
import types
import ctypes
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
_fake_groq.AuthenticationError = type("AuthenticationError", (Exception,), {})
sys.modules.setdefault("groq", _fake_groq)

import thoughtborne as tb  # noqa: E402
import transcriber as tr  # noqa: E402
from output_handler import TranscriptionTask  # noqa: E402,F401
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
    """A non-live, file-based engine that came back empty (as after a swallowed
    transport error). Not a SonioxTranscriber subclass, so engine_code -> unknown."""
    is_live = False

    def get_name(self):
        return "Groq (fake)"

    def transcribe(self, *a, **k):
        return ""


# ---- #141: faked V4 stages for the REAL fallback chain -------------------
# These stand in for SonioxV4Transcriber inside _try_fallback(kind="v4"). They
# mirror the real transcribe() signature (the keyword-only error_sink), so the
# Tier-1 cases below run the genuine _run_empty_transcript_fallback / _try_fallback
# against them instead of stubbing the whole chain.
class _FakeV4Outage:
    """V4 whose transcribe simulates a swallowed transport/API outage: returns ""
    AND reports through the error sink, exactly as the real class does after #141."""
    def transcribe(self, path, duration, *, error_sink=None):
        if error_sink is not None:
            error_sink.errored = True
        return ""


class _FakeV4CleanEmpty:
    """V4 that completed clean but found nothing -- never touches the sink."""
    def transcribe(self, path, duration, *, error_sink=None):
        return ""


class _FakeV4Success:
    """V4 that returned a real transcript -- never touches the sink."""
    def transcribe(self, path, duration, *, error_sink=None):
        return "recovered text"


class _FakeV2SdkLess:
    """Stands in for the V2 fallback stage with the Soniox SDK absent, so
    _try_fallback(kind="v2") takes its config-skip (returns ("", False)) and the
    cascade falls through to V4 -- without constructing a real SonioxTranscriber."""
    _v2_available = False


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
    def __init__(self, fallback_result=None):
        self.transcriber = None
        self.processing_lock = threading.Lock()
        self.processing_counter = 0
        self.audio_recorder = _FakeAudio()
        self.output_manager = _FakeOutput()
        self._fallback_result = fallback_result   # (transcript, engine, any_error)
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
    """_FakeApp, but running the REAL fallback chain against a faked V4 stage
    (#141). Binds the genuine _run_empty_transcript_fallback / _try_fallback and
    pre-seeds the fallback transcribers so the lazy init never constructs a real
    one (which would demand API keys). The V4 fake carries the outage/clean/
    success behavior under test."""
    _run_empty_transcript_fallback = tb.ThoughtborneApp._run_empty_transcript_fallback
    _try_fallback = tb.ThoughtborneApp._try_fallback

    def __init__(self, fake_v4, fake_v2=None):
        super().__init__()
        self._fallback_init_lock = threading.Lock()
        self._fallback_v2 = fake_v2   # long-recording cases never touch v2
        self._fallback_v4 = fake_v4   # pre-seeded: the lazy init must not run


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


def test_insession_nonlive_empty_is_error_not_no_speech():
    """The regression: a non-live engine's empty result must be FAILED + retryable,
    never the honest 'no speech' verdict (its empty can hide a transport error)."""
    _reset()
    app = _FakeApp()
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=7,
        timestamp="20260718_090000_007", transcriber=_FakeFile())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "non-live empty must be a FAILED verdict"
    assert task.no_speech is False, "non-live empty must NOT be no_speech (the regression)"
    assert app.record_failed_called is True, "the retry slot must be armed"


def test_insession_live_clean_empty_is_no_speech():
    """The intended #133 behavior: a Soniox Live chain that ran clean and empty
    earns the honest 'no speech' verdict and arms no retry."""
    _reset()
    app = _FakeApp(fallback_result=("", "", False))   # chain ran, no error
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=8,
        timestamp="20260718_090000_008", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.no_speech is True, "a clean live chain earns the no-speech verdict"
    assert task.is_error is False, "no-speech is not a failure"
    assert app.record_failed_called is False, "no retry slot for a genuinely silent recording"


def test_insession_live_transport_error_is_error():
    """A Soniox Live chain that hit a transport error stays FAILED + retryable."""
    _reset()
    app = _FakeApp(fallback_result=("", "", True))    # chain hit a transport error
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=9,
        timestamp="20260718_090000_009", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "a live transport error stays retryable"
    assert task.no_speech is False
    assert app.record_failed_called is True


def test_retry_nonlive_empty_keeps_marker_retryable():
    """The retry-path regression: a non-live engine chosen via Ctrl+Alt+L that
    returns empty must keep the recording retryable (marker persists), never drop
    the marker as a no-speech verdict would -- that is the engine-switch escape."""
    _reset()
    rec = _armed_rec()
    task, errors = _drive_retry(_FakeApp(), rec, 11, _FakeFile())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.is_error is True, "non-live empty retry must stay a FAILED verdict"
    assert task.no_speech is False, "non-live empty retry must NOT be no_speech (the regression)"
    assert len(_markers()) == 1, f"the marker must persist -- still retryable: {_markers()}"


def test_retry_live_clean_empty_is_no_speech_drops_marker():
    """A clean-empty Soniox Live retry chain is a final 'no speech' verdict:
    the marker is dropped (a second Ctrl+Alt+R finds nothing to retry)."""
    _reset()
    rec = _armed_rec()
    app = _FakeApp(fallback_result=("", "", False))
    task, errors = _drive_retry(app, rec, 12, _FakeLive())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.no_speech is True, "a clean live retry chain earns the no-speech verdict"
    assert task.is_error is False
    assert app.resolve_failed_called is True, "the slot is resolved -- retry cannot help"
    assert _markers() == [], f"the marker must be cleared: {_markers()}"


def test_retry_live_transport_error_keeps_marker():
    """A Soniox Live retry chain that hit a transport error stays retryable AND
    resets the announcement (delete + fresh write -> exactly one un-announced marker)."""
    _reset()
    rec = _armed_rec()
    app = _FakeApp(fallback_result=("", "", True))
    task, errors = _drive_retry(app, rec, 13, _FakeLive())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.is_error is True, "a live transport error retry stays retryable"
    assert task.no_speech is False
    assert app.resolve_failed_called is False
    assert _markers() == ["voice_20260718_085900_001_d1000.needsretry"], \
        f"exactly one un-announced marker must remain: {_markers()}"


# ---- #141: the async-engine outage hole on long recordings ----------------
# Tier 1 drives the REAL fallback chain against a faked V4 stage, so the wiring
# from the sink through _try_fallback / _run_empty_transcript_fallback to the
# verdict is exercised end to end.
def test_long_v4_outage_keeps_marker():
    """The #141 regression: a long (>= 58 s) recording whose Live transcript came
    back empty, run through the REAL fallback chain while V4 is down, must be
    FAILED + retryable -- never the honest 'no speech' verdict. Before the fix the
    V4 stage swallowed the outage and reported clean-empty, so any_error stayed
    False and the recording earned a false no-speech verdict (marker deleted)."""
    _reset()
    app = _RealChainApp(_FakeV4Outage())
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=60.0, sequence_number=41,
        timestamp="20260718_090000_041", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "a long-recording V4 outage must be a FAILED verdict (#141)"
    assert task.no_speech is False, "the V4 outage must NOT read as no_speech (the regression)"
    assert app.record_failed_called is True, "the retry slot must be armed"


def test_long_v4_clean_empty_is_no_speech():
    """The honest #133 verdict must SURVIVE the #141 fix on the long path: a V4
    stage that completed clean and found nothing (never touched the sink) still
    earns 'no speech' and arms no retry. The fix must not blanket-force FAILED."""
    _reset()
    app = _RealChainApp(_FakeV4CleanEmpty())
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=60.0, sequence_number=42,
        timestamp="20260718_090000_042", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.no_speech is True, "a clean-empty long V4 chain earns the no-speech verdict"
    assert task.is_error is False, "no-speech is not a failure"
    assert app.record_failed_called is False, "no retry slot for a genuinely silent recording"


def test_short_v2_skipped_v4_outage_keeps_marker():
    """The sink also hardens the SHORT path: with the V2 SDK absent (a config skip,
    errored=False) and V4 down, the cascade must still land on FAILED -- the V4
    outage is the only error signal, and the sink is what carries it (#141)."""
    _reset()
    app = _RealChainApp(_FakeV4Outage(), fake_v2=_FakeV2SdkLess())
    tb.ThoughtborneApp.process_recording_thread(
        app, frames=[b"x"], duration=1.0, sequence_number=43,
        timestamp="20260718_090000_043", transcriber=_FakeLive())
    task = app.output_manager.tasks[-1]
    assert app.audio_recorder.cleanup_called, "the normal path ran, not the catch-all"
    assert task.is_error is True, "a short-path V4 outage (V2 SDK-less) must be FAILED (#141)"
    assert task.no_speech is False
    assert app.record_failed_called is True


def test_try_fallback_v4_tuple_semantics():
    """The narrowest pin on the new wiring: _try_fallback(kind='v4') returns
    (transcript, errored) with errored driven purely by the V4 stage's sink --
    outage -> ('', True), clean empty -> ('', False), success -> (text, False)."""
    _reset()
    mp3 = _mp3("voice_20260718_090000_044")
    assert _RealChainApp(_FakeV4Outage())._try_fallback(
        kind="v4", mp3_path=mp3, duration=60.0,
        sequence_number=44, thread_name="T") == ("", True)
    assert _RealChainApp(_FakeV4CleanEmpty())._try_fallback(
        kind="v4", mp3_path=mp3, duration=60.0,
        sequence_number=44, thread_name="T") == ("", False)
    assert _RealChainApp(_FakeV4Success())._try_fallback(
        kind="v4", mp3_path=mp3, duration=60.0,
        sequence_number=44, thread_name="T") == ("recovered text", False)


def test_retry_long_v4_outage_marker_persists():
    """The manual retry path inherits the #141 fix for free. A long-recording
    Ctrl+Alt+R whose Live chain hits a V4 outage keeps exactly one un-announced
    marker and is NOT a no-speech verdict -- the mirror of
    test_retry_live_transport_error_keeps_marker, but through the REAL chain."""
    _reset()
    rec = _armed_rec(ts="20260718_085900_041", dur=60.0)
    app = _RealChainApp(_FakeV4Outage())
    task, errors = _drive_retry(app, rec, 45, _FakeLive())
    assert not any("Error retrying" in m for m in errors), \
        f"the routing must run, not the catch-all: {errors}"
    assert task.is_error is True, "a long-recording V4 outage retry stays retryable (#141)"
    assert task.no_speech is False
    assert app.resolve_failed_called is False
    assert _markers() == ["voice_20260718_085900_041_d60000.needsretry"], \
        f"exactly one un-announced marker must remain: {_markers()}"


# ---- #141 Tier 2: the four error channels of the REAL SonioxV4Transcriber --
# Tier 1 proves the wiring; these prove each real error return actually sets the
# sink (and a completed-but-empty run does not). The real transcribe() does
# `import httpx` per call, so a sys.modules["httpx"] swap reaches it; post/get
# are scripted, delete is a no-op the finally-cleanup calls.
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
    real handler's 401-vs-generic split works."""
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.response = types.SimpleNamespace(status_code=status_code)


def _make_fake_httpx(post_script, get_script):
    """A scriptable stand-in for the per-call `import httpx`. post/get pop the next
    scripted item -- a _FakeResp is returned, an Exception instance is raised on
    the call (a network failure). delete is a no-op."""
    mod = types.ModuleType("httpx")
    mod.HTTPStatusError = _FakeHTTPStatusError
    posts = iter(post_script)
    gets = iter(get_script)

    def _serve(it):
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    mod.post = lambda *a, **k: _serve(posts)
    mod.get = lambda *a, **k: _serve(gets)
    mod.delete = lambda *a, **k: None
    return mod


def _run_v4_case(post_script, get_script):
    """Run the REAL SonioxV4Transcriber.transcribe against a scripted fake httpx.
    Returns (result_text, error_tag, error_messages). Constructs the transcriber
    via __new__ (no API-key check, no archive-dir side effect) and restores every
    patched global plus the sys.modules['httpx'] entry afterwards."""
    v4 = tr.SonioxV4Transcriber.__new__(tr.SonioxV4Transcriber)
    v4.api_key = "test-key"
    v4.headers = {"Authorization": "Bearer test-key"}
    dummy = ah.ARCHIVE_FOLDER / "v4_input.mp3"
    dummy.touch()

    fake = _make_fake_httpx(post_script, get_script)
    saved_httpx = sys.modules.get("httpx")
    saved_max = tr.SONIOX_ASYNC_MAX_POLL_ATTEMPTS
    saved_interval = tr.SONIOX_ASYNC_POLL_INTERVAL
    sys.modules["httpx"] = fake
    tr.SONIOX_ASYNC_MAX_POLL_ATTEMPTS = 3
    tr.SONIOX_ASYNC_POLL_INTERVAL = 0

    lg = logging.getLogger("Thoughtborne")
    old = lg.level
    lg.setLevel(logging.ERROR)
    cap = _ErrorCapture()
    lg.addHandler(cap)
    tag = tr._ErrorTag()
    try:
        result = v4.transcribe(str(dummy), 60.0, error_sink=tag)
    finally:
        lg.removeHandler(cap)
        lg.setLevel(old)
        if saved_httpx is not None:
            sys.modules["httpx"] = saved_httpx
        else:
            sys.modules.pop("httpx", None)
        tr.SONIOX_ASYNC_MAX_POLL_ATTEMPTS = saved_max
        tr.SONIOX_ASYNC_POLL_INTERVAL = saved_interval
    return result, tag, cap.records


def test_v4_job_error_sets_sink_and_reads_error_message():
    """Job-level status 'error' -> "" and sink set; the log carries the actual
    error_message field (pins the field fix: the old code read 'error' and
    always logged 'Unknown error')."""
    text, tag, errors = _run_v4_case(
        post_script=[_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})],
        get_script=[_FakeResp({"status": "error", "error_message": "insufficient credits"})],
    )
    assert text == "", "a job error returns empty"
    assert tag.errored is True, "a job error must set the sink (#141)"
    assert any("insufficient credits" in m for m in errors), \
        f"the real error_message must be logged, not 'Unknown error': {errors}"


def test_v4_poll_timeout_sets_sink():
    """Status stays 'processing' past the poll cap -> "" and sink set."""
    text, tag, _ = _run_v4_case(
        post_script=[_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})],
        get_script=[_FakeResp({"status": "processing"})] * 3,
    )
    assert text == ""
    assert tag.errored is True, "a poll timeout must set the sink (#141)"


def test_v4_http_error_sets_sink():
    """A non-401 HTTP error on upload -> "" and sink set (the generic branch)."""
    text, tag, _ = _run_v4_case(
        post_script=[_FakeResp(raise_exc=_FakeHTTPStatusError(500))],
        get_script=[],
    )
    assert text == ""
    assert tag.errored is True, "an HTTP transport error must set the sink (#141)"


def test_v4_network_exception_sets_sink():
    """A raised network exception on upload -> "" and sink set (the generic
    except Exception branch -- DNS/connect failure, the real outage shape)."""
    text, tag, _ = _run_v4_case(
        post_script=[ConnectionError("network down")],
        get_script=[],
    )
    assert text == ""
    assert tag.errored is True, "a network exception must set the sink (#141)"


def test_v4_clean_empty_does_not_set_sink():
    """A completed run with empty text -> "" but the sink stays False -- the
    boundary the honest no-speech verdict depends on (must never read as errored)."""
    text, tag, _ = _run_v4_case(
        post_script=[_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})],
        get_script=[_FakeResp({"status": "completed"}), _FakeResp({"text": ""})],
    )
    assert text == "", "a completed-but-empty run returns empty"
    assert tag.errored is False, "a clean-empty run must NOT set the sink (#141)"


def test_v4_failed_status_sets_sink():
    """The defensive 'failed' terminal status (undocumented) is caught immediately
    in the error/failed branch and sets the sink -- not left to poll into the
    timeout. The branch-specific 'transcription failed' log line pins it to that
    branch: it is absent from both the poll-timeout and the generic-exception
    paths, so dropping 'failed' from the status tuple in transcriber.py (which then
    polls until the single scripted response is exhausted and lands in the
    catch-all) turns this case red."""
    text, tag, errors = _run_v4_case(
        post_script=[_FakeResp({"id": "file1"}), _FakeResp({"id": "tx1"})],
        get_script=[_FakeResp({"status": "failed"})],
    )
    assert text == ""
    assert tag.errored is True, "the 'failed' status branch must set the sink (#141)"
    assert any("transcription failed" in m for m in errors), \
        f"'failed' must hit the error/failed branch, not poll into the timeout: {errors}"


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
    # #133 verdict routing (drives the real worker methods)
    test_insession_nonlive_empty_is_error_not_no_speech,
    test_insession_live_clean_empty_is_no_speech,
    test_insession_live_transport_error_is_error,
    test_retry_nonlive_empty_keeps_marker_retryable,
    test_retry_live_clean_empty_is_no_speech_drops_marker,
    test_retry_live_transport_error_keeps_marker,
    # #141 async-engine outage on long recordings -- Tier 1 (real chain,
    # faked V4 stage)
    test_long_v4_outage_keeps_marker,
    test_long_v4_clean_empty_is_no_speech,
    test_short_v2_skipped_v4_outage_keeps_marker,
    test_try_fallback_v4_tuple_semantics,
    test_retry_long_v4_outage_marker_persists,
    # #141 Tier 2 (real SonioxV4Transcriber, scripted fake httpx)
    test_v4_job_error_sets_sink_and_reads_error_message,
    test_v4_poll_timeout_sets_sink,
    test_v4_http_error_sets_sink,
    test_v4_network_exception_sets_sink,
    test_v4_clean_empty_does_not_set_sink,
    test_v4_failed_status_sets_sink,
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
        # import); no case runs past this point, so clearing it here leaves no
        # tb_marker_test_* dir behind in /tmp.
        shutil.rmtree(ah.ARCHIVE_FOLDER, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
