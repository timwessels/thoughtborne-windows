# CLAUDE.md

Project-specific notes for Claude Code. Loaded automatically when working in this directory. Keep short and factual; update as the project evolves.

## About

Thoughtborne is a hotkey-driven voice-to-text tool for Windows, written in Python. Recording is started with `Ctrl+Alt+W`; one of `A` / `D` / `H` / `Y` (held with `Ctrl+Alt`) ends it and inserts the transcript at the cursor position in whatever Windows app is active. Four transcription APIs are switchable at runtime; the default is `soniox-live`.

- **Repo:** `github.com/timwessels/thoughtborne-windows`, default branch `main`.
- **Mac port:** `github.com/timwessels/thoughtborne-macos` — three APIs instead of four, otherwise analogous. Useful as a reference for commit style, README tone, and porting decisions.
- **Vision & direction:** `VISION.md` — why the tool exists, the quality bar (*good enough to send to LLMs unread*), who it's for, decision principles, and non-goals. Calibrate judgment calls — scope, trade-offs, priorities — against it. Stability is principle #1; the quality floor and the no-training-on-user-data rule are hard gates.

## GitHub workflow

Claude owns commits, pushes, and issues. The default pattern is **decide → say what's next → do it** — not "ask permission first". The user can always intervene; the announce-then-act sequence is what gives that opportunity.

- **Commits.** After a coherent change, announce ("I'll commit X and push") and proceed. Hold off when the working tree mixes unrelated changes, work is mid-stream, or the change is experimental and not yet vetted.
- **Push.** Default is: commit → push. Don't accumulate unpushed commits without a stated reason.
- **Commit messages.** Match `git log` style — short, imperative English. Reference issues with `(#N)`. Detailed body only when the change isn't self-evident.
- **Issues.** Planned work and the backlog live as GitHub Issues (`github.com/timwessels/thoughtborne-windows/issues`). Open one when something is worth tracking — a bug surface, a deferred design decision, a follow-up that doesn't fit the current scope, or even a half-formed idea worth parking (as `idea`, see labels). A `ready` issue stands on its own — problem, spec, acceptance — and is the source of truth for what to build; `idea`/`spike` issues are deliberately unrefined and grow their spec when picked up. Close when resolved, referencing the resolving commit.
- **Issue labels — two axes.** Every issue carries one *type* and one *status*. *Type* (what kind of work): `bug` / `enhancement` / `spike` (research or evaluation — the outcome is knowledge, not a merge) / `test` (hands-on manual verification of shipped changes — the outcome is a confirmed-working checkbox, traced to the change; see "Autonomous issue runs"). *Status* (how ripe): `idea` (raw, evaluate before planning) → `backlog` (understood, deliberately deferred) → `ready` (specified, in active focus); issues mature along that line. **The default working set is `ready`:** "continue with the issues" — and any automated run — means the `ready` issues by priority, **except `test` issues** (those are the user's manual verification, not codeable work); touch `backlog` or `idea` only when explicitly asked.
- **Research & spikes.** Worked-out knowledge — `spike` results, evaluations, comparisons, small tests — lives in `_research/` (gitignored, local per checkout): one folder `YYYY-MM_topic/` per research with an `index.md` (front-matter + key finding), not in commits. Look there before researching something fresh, and file results there. `_research/README.md` has the full schema, lineage rules, and a generated index.
- **Branches.** Direct on `main` for routine work. Feature branches only for risky/experimental things.
- **CHANGELOG.md.** Non-trivial changes get an entry under `## [Unreleased]` (Keep-a-Changelog categories: `### Added` / `### Changed` / `### Fixed` / `### Removed`). On a release tag, that block becomes the versioned entry.

## Autonomous issue runs (meta-orchestration)

The standard mode for working several issues with little supervision — typically an overnight run. Three layers, each delegating downward so no single context fills up:

- **Meta-orchestrator** — this instance, and the only one the user talks to. Holds the project picture (direction, intent, rough code layout) but doesn't dig into issues itself. Per issue it opens a tmux session (surfaces as a tab for the user; tmux mechanics live in the `tmux-session` skill), briefs it, reviews what comes back, answers its questions, and sees each through to closed.
- **Issue instance** — one Claude Code session per issue, run under the `thoughtborne-orchestrate` skill (`fundierte-recherche` for spikes). It loads enough code to understand and judge, but delegates the actual work; when unsure it sends an agent to find out rather than guessing. It rarely writes code itself — good delegation is the point.
- **Agents** — the subagents doing the concrete work (write, research, verify).

**Autonomy is the goal at every layer.** When there's a call to make, think it through and make it rather than waiting on the user; when there's something to verify, take it as far as automated checks reach — human testing is the exception, not the gate. Where the test tooling falls short, improving it so a run can self-verify is itself worthwhile, and a run that hits that wall is worth flagging back to the user ("here's what I'd need to test X automatically"). When a hands-on test genuinely can't be avoided, park it in a dedicated **`test`** issue (status `ready`) for the user to run later rather than blocking the run, and raise it through the meta-orchestrator — never straight from an issue instance. One such issue per run, aggregating its hands-on checks as a checkbox list, each traced to the change/commit it covers; it stays open so the user ticks items off as they confirm them — deliberately or just from normal use — and the change-to-check mapping doubles as a debugging aid if a regression later surfaces.

## Language

English for code, inline comments, commit messages, and all public documentation (README, CHANGELOG, LICENSE, CLAUDE.md).

## When the tool is running

If `thoughtborne.py` is currently running, do not modify code, rename files, or otherwise disturb the working directory. Reliable check: a Windows python process whose command line contains `thoughtborne.py` (e.g. via `powershell.exe Get-CimInstance Win32_Process`). The log's mtime alone misleads — a clean shutdown writes `Program ended` as its final lines and still looks recently touched. Ask the user to stop it with `Ctrl+Alt+4` first. The user may be dictating into the same Claude session that is being asked to edit — the hotkey exit is the clean handoff.

## Do not touch

- **Hallucination-filter patterns** in `transcriber.py` (`_clean_transcript_hallucinations`, `_clean_groq_hallucinations`) are **data**, not prose. Never translate, paraphrase, or "improve" them.
- **`Ctrl+Alt+Ü` hotkey** uses the German QWERTZ umlaut key and triggers the test transcription. Intentional, do not change.
- **Gitignored `_*` folders** (`_backups/`, `_archive/`, `_docs/`, `_research/`, `_speedtest/`, `_tools/`, `_temp-claudecode/`) are local-only workspaces. Never auto-delete, never "clean up", never remove duplicates. Deletion only on explicit instruction. See `_backups/BACKUP_README.md`.
- **Be conservative with working code.** Do not refactor or rewrite without a stated reason — "clean code is a feature" (`VISION.md`) justifies keeping code legible *while changing it for a reason*, not standalone rewrites. Direction and ambition live in `VISION.md`; this rule caps code churn, it is not a feature freeze.

## Hard to undo — always ask first

Flipping the repo to public, force-pushing `main`, destructive branch deletion, rewriting published history (`git filter-branch`, `git reset --hard` on pushed commits), renaming the repo.

## Where things live

- **Source:** `thoughtborne.py`, `audio_handler.py`, `transcriber.py`, `output_handler.py`, `hotkey_manager.py`, `config.py`.
- **Windows launcher:** `Thoughtborne.bat`.
- **Public docs:** `README.md`, `CHANGELOG.md`, `VISION.md`, `LICENSE`, `.env.example`, `personal_settings.example.json`.
- **Local workspaces** (gitignored): see list above under "Do not touch".
