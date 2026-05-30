# CLAUDE.md

Project-specific notes for Claude Code. Loaded automatically when working in this directory. Keep short and factual; update as the project evolves.

## About

Thoughtborne is a hotkey-driven voice-to-text tool for Windows, written in Python. Recording is started with `Ctrl+Alt+W`; one of `A` / `D` / `H` / `Y` (held with `Ctrl+Alt`) ends it and inserts the transcript at the cursor position in whatever Windows app is active. Six transcription APIs are switchable at runtime; the default is `soniox-live`.

- **Repo:** `github.com/timwessels/thoughtborne-windows`, default branch `main`.
- **Mac port:** `github.com/timwessels/thoughtborne-macos` — three APIs instead of six, otherwise analogous. Useful as a reference for commit style, README tone, and porting decisions.

## GitHub workflow

Claude owns commits, pushes, and issues. The default pattern is **decide → say what's next → do it** — not "ask permission first". The user can always intervene; the announce-then-act sequence is what gives that opportunity.

- **Commits.** After a coherent change, announce ("I'll commit X and push") and proceed. Hold off when the working tree mixes unrelated changes, work is mid-stream, or the change is experimental and not yet vetted.
- **Push.** Default is: commit → push. Don't accumulate unpushed commits without a stated reason.
- **Commit messages.** Match `git log` style — short, imperative English. Reference issues with `(#N)`. Detailed body only when the change isn't self-evident.
- **Issues.** Planned work and the backlog live as GitHub Issues (`github.com/timwessels/thoughtborne-windows/issues`). Open one when something is worth tracking (bug surface, deferred design decision, follow-up that doesn't fit the current scope). Each issue stands on its own — problem, spec, acceptance — so the issue is the source of truth for what to build. Close when resolved, referencing the resolving commit. Labels: `bug` / `enhancement` / `backlog` (someday/maybe outside the active focus).
- **Branches.** Direct on `main` for routine work. Feature branches only for risky/experimental things.
- **CHANGELOG.md.** Non-trivial changes get an entry under `## [Unreleased]` (Keep-a-Changelog categories: `### Added` / `### Changed` / `### Fixed` / `### Removed`). On a release tag, that block becomes the versioned entry.

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
