# AGENTS.md

Guidance for AI coding agents working in this repository. Plain Markdown — humans are welcome to read it too.

## What this is

Thoughtborne is a hotkey-driven voice-to-text tool for Windows, written in Python. Recording is started with `Ctrl+Alt+W`; one of `A` / `D` / `H` / `Y` (held with `Ctrl+Alt`) ends it and inserts the transcript at the cursor position in whatever Windows app is active. Four transcription APIs are switchable at runtime; the default is `soniox-live`. Windows-only by design (Win32 hotkeys, audio capture, text insertion).

- **Setting the tool up for a user?** Follow [`llms-install.md`](llms-install.md) — the guided setup, including API-key onboarding and the WSL2 pitfalls.
- **Vision & direction:** [`VISION.md`](VISION.md) — why the tool exists, the quality bar (*good enough to send to LLMs unread*), who it's for, decision principles, and non-goals. Calibrate judgment calls — scope, trade-offs, priorities — against it. Stability is principle #1; the quality floor and the no-training-on-user-data rule are hard gates.
- **Mac port:** [`thoughtborne-macos`](https://github.com/timwessels/thoughtborne-macos) — three APIs instead of four, otherwise analogous. Useful as a reference for porting decisions.

## Run & verify

- Run: `uv run thoughtborne.py`, or double-click `Thoughtborne.bat`. Requires Windows, a microphone, and at least one API key in `.env` (template: `.env.example`).
- There is no automated test suite. The verification ladder: `python -m py_compile <changed files>` for syntax; start the tool and check the startup banner plus `All hotkeys registered successfully` in `thoughtborne.log`; the in-app self-test `Ctrl+Alt+Ü` transcribes `test_audio.mp3` end to end (needs a valid API key).
- Working from inside WSL2? The tool itself must run Windows-side; [`llms-install.md`](llms-install.md) covers the interop.

## While the tool is running

If `thoughtborne.py` is currently running, do not modify code, rename files, or otherwise disturb the working directory. Reliable check: a Windows python process whose command line contains `thoughtborne.py` (e.g. via `powershell.exe Get-CimInstance Win32_Process`). The log's mtime alone misleads — a clean shutdown writes `Program ended` as its final lines and still looks recently touched. Ask the user to stop it with `Ctrl+Alt+4` first. The user may be dictating into the same agent session that is being asked to edit — the hotkey exit is the clean handoff.

## Conventions

- **Language:** English for code, inline comments, commit messages, and all public documentation.
- **Bilingual README:** `README.md` (English) and `README.de.md` (German) are content-equivalent twins — a change to one is mirrored in the other.
- **Commit messages:** short, imperative English; reference issues with `(#N)`.
- **CHANGELOG.md:** non-trivial changes get an entry under `## [Unreleased]` (Keep-a-Changelog categories: `### Added` / `### Changed` / `### Fixed` / `### Removed`).

## Guardrails

- **Never commit `.env`**, and never reproduce API keys in committed files, logs, or chat output.
- **Hallucination-filter patterns** in `transcriber.py` (`_clean_transcript_hallucinations`, `_clean_groq_hallucinations`) are **data**, not prose. Never translate, paraphrase, or "improve" them.
- **`Ctrl+Alt+Ü` hotkey** uses the German QWERTZ umlaut key and triggers the test transcription. Intentional, do not change.
- **Folders starting with `_`** (e.g. `_research/`, `_backups/`) are gitignored, local-only workspaces of whoever owns the checkout. Never auto-delete, never "clean up", never remove duplicates. Deletion only on explicit instruction.
- **Be conservative with working code.** Do not refactor or rewrite without a stated reason — "clean code is a feature" (`VISION.md`) justifies keeping code legible *while changing it for a reason*, not standalone rewrites. Direction and ambition live in `VISION.md`; this rule caps code churn, it is not a feature freeze.

## Where things live

- **Source:** `thoughtborne.py`, `audio_handler.py`, `transcriber.py`, `output_handler.py`, `hotkey_manager.py`, `config.py`.
- **Windows launcher:** `Thoughtborne.bat`.
- **Public docs:** `README.md`, `README.de.md`, `CHANGELOG.md`, `VISION.md`, `LICENSE`, `AGENTS.md`, `llms-install.md`, `.env.example`, `personal_settings.example.json`.
- **User data** (created at runtime, gitignored): `voice_archive/`, `text_archive/`, `thoughtborne.log` — the user's data, never delete.
