# Changelog

All notable changes to Thoughtborne are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Diagnostic logging for audio drop detection (Soniox Live):
  - WebSocket send-latency measurement in `SonioxLiveTranscriber.send_audio_chunk`; warns on sends slower than 100 ms (direct TCP backpressure indicator).
  - Per-session send-latency summary (max latency, blocked-event count, total blocked time) logged on session close.
  - Exact-silence detection in `AudioRecorder.record_chunk`; warns on runs of all-zero samples >= 200 ms (catches microphone stalls that are not caused by send blocking).
  - Audio-gap check in `AudioRecorder.stop_recording`: compares wallclock duration (anchored to first received chunk to exclude PyAudio init and BT warm-up) against actual recorded audio duration; warns on gaps > 0.3 s, which now directly reflect mid-recording audio loss.
- Extended audio-drop diagnostics (`AudioRecorder.record_chunk` and `stop_recording`):
  - Per-chunk PyAudio `stream.read()` latency measurement; DEBUG entry when a read exceeds 50 ms (nominal ~23 ms at CHUNK=1024 / RATE=44100). Points at the audio source (BT profile switch, driver hiccup, internal overflow) — distinct from send-latency which points at the network.
  - Recording-loop iteration-gap measurement (wallclock time between consecutive `record_chunk()` returns); DEBUG entry when the gap exceeds 50 ms (nominal ~33 ms = sleep + send + overhead). Captures any block OUTSIDE the read — typically slow WebSocket sends or GIL contention from the receiver thread.
  - Per-session summary lines (read-latency stats and loop-iteration stats) logged at INFO level on `stop_recording`, complementing the existing send-latency summary. Together these three summaries triangulate where any audio loss originated (audio source / network send / loop-side blocking).

### Fixed

- `SonioxLiveTranscriber._receiver_loop` now resets `_session_active` to `False` in its `finally` block. Without this, every audio chunk sent after a server-initiated WebSocket close produced a fresh ERROR log entry until the user pressed Stop (observed: 2000+ spam entries within seconds of a single disconnect).
