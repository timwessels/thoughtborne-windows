#!/usr/bin/env python3
"""Lifecycle tests for the persistent retry marker (#106/#114/#134/#133).

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
