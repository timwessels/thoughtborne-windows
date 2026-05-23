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

### Fixed

- `SonioxLiveTranscriber._receiver_loop` now resets `_session_active` to `False` in its `finally` block. Without this, every audio chunk sent after a server-initiated WebSocket close produced a fresh ERROR log entry until the user pressed Stop (observed: 2000+ spam entries within seconds of a single disconnect).
