# Changelog

All notable changes to Thoughtborne are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Live audio sample rate dropped from 44.1 kHz to 16 kHz mono (`config.RATE = 16000`, #2). Soniox V4 operates internally at 16 kHz and downsamples everything above it server-side; all six transcription APIs (Soniox V2/V4-async/Live, Modal Parakeet, HuggingFace Whisper, Groq Whisper) work natively at 16 kHz, so this is pure bandwidth saving with no recognition-quality loss. Live upload drops from ~705 kbps to ~256 kbps (-64 %), the root-cause counterpart to the Block-2 sender-thread fix that treated TCP-backpressure symptoms only. New MP3s in `voice_archive/` are 16 kHz mono; existing recordings stay 44.1 kHz. Diagnostic threshold for slow PyAudio `stream.read()` raised from 50 ms to 100 ms to match the new nominal chunk time (~64 ms at CHUNK=1024 / RATE=16000); the loop-iteration-gap threshold stays at 50 ms because it measures wallclock outside the read and is sample-rate-independent.
- `SonioxLiveTranscriber` now uses a producer/consumer architecture for the WebSocket send path. `send_audio_chunk()` is non-blocking: it enqueues the chunk on an internal `queue.Queue` (size `SONIOX_LIVE_QUEUE_MAX_CHUNKS`, default 50 ≈ 1.16 s) and a dedicated sender thread drains the queue to the WebSocket. TCP backpressure now stalls only the sender thread, so the recording loop keeps reading PyAudio without gaps even when sends block. Under heavy backpressure beyond the queue capacity, newest chunks are dropped (WARNING on drop-mode entry, INFO on recovery, per-chunk drops at DEBUG); the MP3 archive is unaffected because frames are stored in `audio_handler` independently of the send queue. Finalize uses a sentinel-based queue drain with a timeout fallback so it can't hang on a dead sender. Three new config constants (`SONIOX_LIVE_QUEUE_MAX_CHUNKS`, `SONIOX_LIVE_SENDER_JOIN_TIMEOUT`, `SONIOX_LIVE_FINALIZE_DRAIN_TIMEOUT`).

### Fixed

- `SonioxLiveTranscriber.transcribe` now calls `_close_session_internal()` on the early-return path ("No active Soniox Live session to finalize") as well, so per-session send-latency and queue-drop stats are logged even when finalize hits a session that already died (e.g. from a 20-s Soniox idle timeout during prolonged TCP backpressure). Previously those exact sessions — the most diagnostically interesting ones — silently skipped the stats line.

### Added

- Empty-transcript fallback for Soniox Live (#1, Block 3). When `SonioxLiveTranscriber.transcribe()` returns an empty string -- typically a Class-B failure where the server closed the WebSocket (`1011 keepalive ping timeout`) before the stop hotkey under sustained TCP backpressure -- `process_recording_thread` now retries the just-saved MP3 (same content as the archived copy in `voice_archive/`) against a file-based Soniox API instead of marking the recording as failed. Choice depends on duration: recordings shorter than `SHORT_AUDIO_THRESHOLD` (58 s) go to `SonioxTranscriber` (V2 sync, ~2-3 s for 30 s audio) first, with a fall-through to `SonioxV4Transcriber` if V2 raises or returns empty -- so a future V2 shutdown by Soniox leaves the fallback chain working. Recordings of 58 s or longer go straight to `SonioxV4Transcriber` (V4 async polling, ~10-60 s; the only option past V2's 60 s hard limit). Both fallback transcribers are lazily instantiated as singletons on first use, guarded by a small init lock so parallel Class-B disconnects don't race on construction. A clearly framed console block plus INFO log entry surface the trigger; an additional INFO line plus console line marks the V2-to-V4 fall-through when it happens. If every available fallback returns empty / fails, the exhausted-stages line is logged at ERROR and the existing is_error path takes over. Trigger is restricted to `SonioxLiveTranscriber` -- empty results from V2 / V4-async / Modal / HuggingFace / Groq already mean the file-based path was tried and failed, so a second pass would not help. The fallback is not interruptible (Ctrl+Alt+X is a no-op once recording has stopped).
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
