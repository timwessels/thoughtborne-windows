#!/usr/bin/env python3
"""Fault-injection tests for the audio stall / deadlock guards (#128).

Runs on plain Python -- no Windows, no PyAudio, no real microphone -- so the
Layer 1/1b/2/3 logic of `audio_handler` is exercised programmatically (a durable
regression guard for the freeze in issue #128, sibling of `test_console_ui.py`).

    python3 test_audio_stall.py          # verify, exit non-zero on any failure

What is proven, against a fake stream that can wedge on demand:
  1. Normal poll -> exactly one fresh chunk appended (Layer 1).
  2. Stall watchdog -> the #49 device-loss endgame is armed (Layer 1b).
  3. A wedged read holding the lock: a concurrent _close_stream() returns within
     the timeout and poisons WITHOUT touching the stream (Layer 2, the #128
     deadlock reproduction).
  4. A wedged get_read_available(): Layer 2 still contains it (the backstop for
     when Layer 1's non-blocking assumption breaks).
  5. Double poison is idempotent and instant.
  6. A native close that wedges at a FREE lock still returns within the timeout
     and poisons -- because the close runs on a worker, not the caller thread
     (the G1 proof: no critical thread ever runs a native audio close).
  7. A normal close runs cleanly on the worker (stopped/closed, no poison).
  8. A wedged open times out on the worker and poisons (Layer 3).
  9. After poison, _ensure_pyaudio_ready() drops the wedged handle WITHOUT
     terminate() and rebuilds a fresh pipeline (recovery).
 10. A reinit that succeeds after a concurrent stop poisoned the pipeline does
     NOT resurrect recording -- it honors the poison, closes the freshly opened
     stream so the mic turns off, and arms no endgame (the P1 poison-race guard).

The timers are shrunk so the whole suite runs in a couple of seconds. Every
blocker thread is a daemon gated on an Event that the teardown releases, so no
orphaned thread outlives the run.
"""
import sys
import time
import types
import logging
import tempfile
import threading
from pathlib import Path

# Import subprocess (hence soundfile's dependency chain) BEFORE stubbing msvcrt:
# the stdlib detects Windows by whether `import msvcrt` succeeds, so a msvcrt
# stub in sys.modules would make subprocess try to import the Windows-only
# _winapi and crash. Pre-importing caches the correct non-Windows detection.
import subprocess  # noqa: E402,F401
import soundfile  # noqa: E402,F401

# ---- stub the Windows-only / unavailable modules audio_handler imports at top
# (numpy and soundfile are real in this environment and drive the tested paths).
_fake_msvcrt = types.ModuleType("msvcrt")
_fake_msvcrt.locking = lambda *a, **k: None
_fake_msvcrt.LK_NBLCK = 0
_fake_msvcrt.LK_UNLCK = 0
sys.modules.setdefault("msvcrt", _fake_msvcrt)


class _FakePyAudio:
    """Minimal PyAudio stand-in for the open/recovery paths."""
    def terminate(self):
        pass

    def get_default_input_device_info(self):
        return {"name": "FakeMic", "index": 0}

    def open(self, **kw):
        return FakeStream(avail_seq=[])


_fake_pyaudio = types.ModuleType("pyaudio")
_fake_pyaudio.paInt16 = 16
_fake_pyaudio.get_sample_size = lambda fmt: 2
_fake_pyaudio.PyAudio = _FakePyAudio
sys.modules.setdefault("pyaudio", _fake_pyaudio)

import audio_handler  # noqa: E402

CHUNK = audio_handler.CHUNK

# Shrink every guard timer so the suite runs fast. record_chunk/_close_stream/
# _open_audio_pipeline_bounded read these as module globals, so overriding them
# on the module is what the code actually sees.
audio_handler.AUDIO_STALL_TIMEOUT_SECONDS = 0.2
audio_handler.AUDIO_CLOSE_LOCK_TIMEOUT = 0.2
audio_handler.AUDIO_OPEN_TIMEOUT_SECONDS = 0.2
audio_handler.AUDIO_READ_POLL_SECONDS = 0.002

# Keep the archive mkdir out of the repo, and silence the (deliberately loud)
# error logging the wedge paths emit.
audio_handler.ARCHIVE_FOLDER = Path(tempfile.mkdtemp(prefix="tb_stall_test_"))
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

_GATES = []  # every blocker Event, released in teardown so no thread is orphaned


class FakeStream:
    """A stream whose availability/read/stop can be scripted or made to wedge.

    A wedge is a `gate.wait()` on an Event that is never set until teardown, so
    the blocked call mimics a driver-wedged native PortAudio call.
    """
    def __init__(self, avail_seq=None, read_blocks=False, avail_blocks=False,
                 stop_blocks=False, read_error=None):
        self._avail = list(avail_seq if avail_seq is not None else [])
        self.read_blocks = read_blocks
        self.avail_blocks = avail_blocks
        self.stop_blocks = stop_blocks
        self.read_error = read_error
        self.stopped = False
        self.closed = False
        self.avail_calls = 0
        self.read_calls = 0
        self.gate = threading.Event()
        _GATES.append(self.gate)

    def get_read_available(self):
        self.avail_calls += 1
        if self.avail_blocks:
            self.gate.wait()
        return self._avail.pop(0) if self._avail else 0

    def read(self, n, exception_on_overflow=False):
        self.read_calls += 1
        if self.read_blocks:
            self.gate.wait()
        if self.read_error is not None:
            raise OSError(self.read_error)
        return b"\x00\x00" * n

    def stop_stream(self):
        if self.stop_blocks:
            self.gate.wait()
        self.stopped = True

    def close(self):
        self.closed = True


def make_recorder(stream, recording=True):
    r = audio_handler.AudioRecorder()
    r.stream = stream
    r.stream_is_open = stream is not None
    r.recording = recording
    r.frames = []
    r._recording_start_time = None
    return r


# ---- cases -----------------------------------------------------------------

def test_normal_poll_one_chunk():
    """Layer 1: two empty polls then a full chunk -> exactly one fresh append."""
    s = FakeStream(avail_seq=[0, 0, CHUNK])
    r = make_recorder(s)
    ok = r.record_chunk()
    assert ok is True, "record_chunk should return True on a good chunk"
    assert len(r.frames) == 1, f"expected 1 frame, got {len(r.frames)}"
    assert s.read_calls == 1, f"expected exactly 1 read, got {s.read_calls}"
    assert s.avail_calls == 3, f"expected 3 availability polls, got {s.avail_calls}"


def test_stall_watchdog_arms_endgame():
    """Layer 1b: no chunk ever available -> watchdog fires and pins the endgame."""
    s = FakeStream(avail_seq=[])  # get_read_available always returns 0
    r = make_recorder(s)
    writer_sentinel = object()
    r._sidecar_writer = writer_sentinel
    frames_before = r.frames
    t0 = time.perf_counter()
    ok = r.record_chunk()
    elapsed = time.perf_counter() - t0
    assert ok is False, "a stall must stop the chunk (return False)"
    assert r.recording is False, "stall abort must clear recording"
    assert r.recording_aborted is True, "stall abort must raise recording_aborted"
    assert r.aborted_frames is frames_before, "stall abort must pin the frames list"
    assert r.aborted_writer is writer_sentinel, "stall abort must pin the sidecar writer"
    assert r._sidecar_writer is None, "the writer pin must be transferred, not copied"
    assert elapsed >= audio_handler.AUDIO_STALL_TIMEOUT_SECONDS, \
        f"watchdog fired too early ({elapsed:.3f}s)"
    assert elapsed < 1.0, f"watchdog took too long ({elapsed:.3f}s)"
    # The stall abort releases the (still-open) stream so the mic turns off; a
    # healthy stream closes cleanly on the worker without poisoning.
    assert s.stopped is True and s.closed is True, "stall abort must close the stream"
    assert r._audio_poisoned is False, "a clean stall close must not poison"


def test_stall_abort_skipped_when_not_recording():
    """Layer 1b guard (G2): a session a stop/cancel already ended is not hijacked."""
    s = FakeStream(avail_seq=[])
    r = make_recorder(s, recording=False)
    ok = r.record_chunk()
    assert ok is False
    # record_chunk's top guard returns before the loop when not recording, but
    # the watchdog guard itself must also refuse to arm -- assert the endgame is
    # untouched either way.
    assert r.recording_aborted is False, "must not arm the endgame when not recording"
    assert r.aborted_frames is None


def test_begin_stall_abort_guards_when_not_recording():
    """Layer 1b guard (G2), head-on: _begin_stall_abort() itself must no-op when
    a stop/cancel already set recording=False -- no pin, no recording_aborted,
    the stream left untouched, return False. The record_chunk-level case above
    never reaches this guard (record_chunk's top guard returns first), so this
    exercises the guard directly."""
    s = FakeStream()
    r = make_recorder(s, recording=False)
    writer_sentinel = object()
    r._sidecar_writer = writer_sentinel
    ret = r._begin_stall_abort()
    assert ret is False, "the guard must return False when not recording"
    assert r.recording_aborted is False, "must not arm the endgame when not recording"
    assert r.aborted_frames is None, "must not pin frames when not recording"
    assert r.aborted_writer is None, "must not pin the writer when not recording"
    assert r._sidecar_writer is writer_sentinel, "the writer pin must be left untouched"
    assert s.stopped is False and s.closed is False, "the stream must not be closed"


def test_wedged_read_close_times_out_and_poisons():
    """Layer 2: a read wedged holding the lock -> _close_stream times out,
    poisons, and NEVER touches the stream (the #128 deadlock reproduction)."""
    s = FakeStream(avail_seq=[CHUNK] * 8, read_blocks=True)
    r = make_recorder(s)
    ta = threading.Thread(target=r.record_chunk, daemon=True)
    ta.start()
    time.sleep(0.05)  # let the read enter and hold the lock
    t0 = time.perf_counter()
    r._close_stream()
    elapsed = time.perf_counter() - t0
    assert elapsed < audio_handler.AUDIO_CLOSE_LOCK_TIMEOUT + 0.3, \
        f"_close_stream did not return promptly ({elapsed:.3f}s)"
    assert r._audio_poisoned is True, "a wedged read must poison on close timeout"
    assert r.stream_is_open is False
    assert s.stopped is False and s.closed is False, \
        "poison must leak the wedged stream, never call stop/close on it"


def test_wedged_avail_close_times_out_and_poisons():
    """Layer 2 backstop: even a wedged get_read_available() (Layer 1's
    non-blocking assumption breaking) is contained by the close timeout."""
    s = FakeStream(avail_blocks=True)
    r = make_recorder(s)
    ta = threading.Thread(target=r.record_chunk, daemon=True)
    ta.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    r._close_stream()
    elapsed = time.perf_counter() - t0
    assert elapsed < audio_handler.AUDIO_CLOSE_LOCK_TIMEOUT + 0.3, \
        f"_close_stream did not return promptly ({elapsed:.3f}s)"
    assert r._audio_poisoned is True
    assert s.stopped is False and s.closed is False


def test_double_poison_is_instant():
    """Layer 2: a second close on an already-poisoned pipeline returns at once."""
    r = make_recorder(FakeStream(avail_seq=[CHUNK]))
    r._audio_poisoned = True
    t0 = time.perf_counter()
    r._close_stream()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.05, f"double poison should be instant ({elapsed:.3f}s)"


def test_native_close_wedge_at_free_lock_poisons():
    """G1 proof: with the lock FREE, a native stop_stream() that wedges still
    returns within the timeout and poisons -- because the close runs on a
    throwaway worker, not the caller thread."""
    s = FakeStream(stop_blocks=True)
    r = make_recorder(s, recording=False)  # a stop already set recording=False
    t0 = time.perf_counter()
    r._close_stream()
    elapsed = time.perf_counter() - t0
    # acquire is instant (lock free); the join bounds the wedged worker.
    assert elapsed < 2 * audio_handler.AUDIO_CLOSE_LOCK_TIMEOUT + 0.3, \
        f"a wedged native close did not stay bounded ({elapsed:.3f}s)"
    assert r._audio_poisoned is True, "a wedged native close must poison"
    assert r.stream is None, "the stream must have been detached before the close"
    assert s.stopped is False, "stop_stream wedged before completing"
    assert s.closed is False


def test_reinit_poison_race_does_not_resurrect():
    """P1 (#128): a concurrent stop/cancel poisons the pipeline while the >2s
    reinit holds _stream_lock. The reinit still succeeds and opens a fresh
    stream, but record_chunk must honor the poison instead of resurrecting the
    session it was reviving -- return False, leave recording False, arm NO
    device-loss endgame (the stop owns the session), leave the stop's sidecar
    writer untouched, and close the freshly opened stream so the mic turns off
    rather than leaking it live until the next start."""
    s = FakeStream(avail_seq=[CHUNK],
                   read_error="[Errno -9999] Unanticipated host error")
    r = make_recorder(s)
    r.stream_error_count = r.max_stream_errors - 1  # the next -9999 trips reinit
    writer_sentinel = object()
    r._sidecar_writer = writer_sentinel

    new_stream = FakeStream()  # the stream the "reinit" opens

    def fake_reinit():
        # Mimic the race: a stop/cancel poisoned the pipeline mid-reinit, yet the
        # reinit itself finished and opened a healthy fresh stream.
        r._audio_poisoned = True
        r.stream = new_stream
        r.stream_is_open = True
        return True

    r._reinitialize_stream = fake_reinit
    ok = r.record_chunk()
    assert ok is False, "a poisoned reinit must stop the chunk (return False)"
    assert r.recording is False, "must NOT resurrect recording after a poison"
    assert r.recording_aborted is False, "the stop owns the session -- no endgame"
    assert r.aborted_frames is None, "must not pin frames on the poison path"
    assert r._sidecar_writer is writer_sentinel, "the writer belongs to the stop"
    assert new_stream.stopped is True and new_stream.closed is True, \
        "the freshly opened stream must be closed so the mic turns off"
    assert r.stream is None, "the closed fresh stream must be detached"
    assert r.stream_is_open is False


def test_normal_close_runs_clean_no_poison():
    """The healthy path: the close worker stops and closes the stream, no poison."""
    s = FakeStream()
    r = make_recorder(s, recording=False)
    r._close_stream()
    assert s.stopped is True, "a clean close must stop the stream"
    assert s.closed is True, "a clean close must close the stream"
    assert r._audio_poisoned is False, "a clean close must not poison"
    assert r.stream is None
    assert r.stream_is_open is False


def test_open_timeout_poisons():
    """Layer 3: a wedged PyAudio init/open times out on the worker and poisons."""
    r = audio_handler.AudioRecorder()
    gate = threading.Event()
    _GATES.append(gate)

    def blocking_ensure():
        gate.wait()
        return True

    r._ensure_pyaudio_ready = blocking_ensure
    t0 = time.perf_counter()
    ok = r._open_audio_pipeline_bounded()
    elapsed = time.perf_counter() - t0
    assert ok is False, "a wedged open must fail the start"
    assert elapsed < audio_handler.AUDIO_OPEN_TIMEOUT_SECONDS + 0.3, \
        f"the bounded open did not time out promptly ({elapsed:.3f}s)"
    assert r._audio_poisoned is True, "a wedged open must poison"


def test_ensure_pyaudio_drops_poisoned_without_terminate():
    """Recovery: after poison, _ensure_pyaudio_ready drops the wedged handle
    WITHOUT terminate() and builds a fresh pipeline, clearing poison."""
    r = audio_handler.AudioRecorder()

    class RecordingPyAudio:
        def __init__(self):
            self.terminated = False

        def terminate(self):
            self.terminated = True

    old_p = RecordingPyAudio()
    r.p = old_p
    r._audio_poisoned = True
    ok = r._ensure_pyaudio_ready()
    assert ok is True, "a fresh pipeline must build after poison"
    assert old_p.terminated is False, "the poisoned handle must NOT be terminated"
    assert r._audio_poisoned is False, "poison must be cleared once rebuilt"
    assert isinstance(r.p, _FakePyAudio), "a fresh PyAudio instance must be built"


CASES = [
    test_normal_poll_one_chunk,
    test_stall_watchdog_arms_endgame,
    test_stall_abort_skipped_when_not_recording,
    test_begin_stall_abort_guards_when_not_recording,
    test_wedged_read_close_times_out_and_poisons,
    test_wedged_avail_close_times_out_and_poisons,
    test_double_poison_is_instant,
    test_native_close_wedge_at_free_lock_poisons,
    test_reinit_poison_race_does_not_resurrect,
    test_normal_close_runs_clean_no_poison,
    test_open_timeout_poisons,
    test_ensure_pyaudio_drops_poisoned_without_terminate,
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
    finally:
        for gate in _GATES:
            gate.set()  # release every blocked daemon so nothing is orphaned

    if failures:
        print(f"\nFAIL: {len(failures)}/{len(CASES)} case(s) failed")
        return 1
    print(f"\nOK: all {len(CASES)} audio-stall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
