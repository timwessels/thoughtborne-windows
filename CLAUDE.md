# CLAUDE.md

Project-specific notes for Claude Code. Loaded automatically when working in this directory. Keep short and factual; update as the project evolves.

## About

Thoughtborne is a hotkey-driven voice-to-text tool for Windows, written in Python. Recording is started with `Ctrl+Alt+W`; one of `A` / `D` / `H` / `Y` (held with `Ctrl+Alt`) ends it and inserts the transcript at the cursor position in whatever Windows app is active. Six transcription APIs are switchable at runtime; the default is `soniox-live`.

- **Repo:** `github.com/timwessels/thoughtborne-windows`, default branch `main`.
- **Mac port:** `github.com/timwessels/thoughtborne-macos` — three APIs instead of six, otherwise analogous. Useful as a reference for commit style, README tone, and porting decisions.

## Workflow

- **Commits:** Only on explicit request. Match the style in `git log` — short, descriptive, English, imperative mood.
- **Push:** Only on explicit request.
- **CHANGELOG.md:** For any non-trivial change, add an entry under `## [Unreleased]` using Keep-a-Changelog categories (`### Added` / `### Changed` / `### Fixed` / `### Removed`). When a release tag is cut, that block becomes the versioned entry.
- **Branches:** Direct commits on `main` are fine for routine work. Feature branches only for experimental or risky changes.

## Issue tracking

Planned work and the backlog live as GitHub Issues:
`github.com/timwessels/thoughtborne-windows/issues`. Each issue is written to
stand on its own — problem, spec, acceptance — so the issue is the source of
truth for what to build.

- **Labels:** `bug` / `enhancement`; add `backlog` for someday/maybe items
  outside the active focus.
- **Commits:** when a change addresses an issue, reference it in the message
  (e.g. `(#1)`); close the issue once the change fully resolves it.

## Language

English for code, inline comments, commit messages, and all public documentation (README, CHANGELOG, LICENSE, CLAUDE.md).

## When the tool is running

If `thoughtborne.py` is currently running (check whether `thoughtborne.log` was written to very recently), do not modify code, rename files, or otherwise disturb the working directory. Ask the user to stop it with `Ctrl+Alt+4` first. The user may be dictating into the same Claude session that is being asked to edit — the hotkey exit is the clean handoff.

## Do not touch

- **Hallucination-filter patterns** in `transcriber.py` (`_clean_transcript_hallucinations`, `_clean_groq_hallucinations`) are **data**, not prose. Never translate, paraphrase, or "improve" them.
- **`Ctrl+Alt+Ü` hotkey** uses the German QWERTZ umlaut key and triggers the test transcription. Intentional, do not change.
- **Gitignored `_*` folders** (`_backups/`, `_archive/`, `_docs/`, `_research/`, `_speedtest/`, `_tools/`, `_temp-claudecode/`) are local-only workspaces. Never auto-delete, never "clean up", never remove duplicates. Deletion only on explicit instruction. See `_backups/BACKUP_README.md`.
- **This repo is in maintenance mode**, not rewrite mode. Do not refactor working code without a stated reason.

## Hard to undo — always ask first

Flipping the repo to public, force-pushing `main`, destructive branch deletion, rewriting published history (`git filter-branch`, `git reset --hard` on pushed commits), renaming the repo.

## Where things live

- **Source:** `thoughtborne.py`, `audio_handler.py`, `transcriber.py`, `output_handler.py`, `hotkey_manager.py`, `config.py`.
- **Windows launcher:** `Thoughtborne.bat`.
- **Modal deployment:** `modal_parakeet/deploy.py` (production app `parakeet-german`), `modal_parakeet/test_endpoint.py`.
- **Public docs:** `README.md`, `CHANGELOG.md`, `LICENSE`, `.env.example`, `personal_settings.example.json`.
- **Local workspaces** (gitignored): see list above under "Do not touch".
