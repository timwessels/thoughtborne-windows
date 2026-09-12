# AGENTS.md

Guidance for AI coding agents working in this repository. Plain Markdown — humans are welcome to read it too.

## What this is

Thoughtborne is a hotkey-driven voice-to-text tool for Windows, written in Python. Recording is started with `Ctrl+Alt+W`; one of `A` / `D` / `Y` / `H` (held with `Ctrl+Alt`) ends it and inserts the transcript at the cursor position in whatever Windows app is active. Four transcription APIs are switchable at runtime; the default is `soniox-live`. Windows-only by design (Win32 hotkeys, audio capture, text insertion).

- **Setting the tool up for a user?** Follow [`llms-install.md`](llms-install.md) — the guided setup, including API-key onboarding and the WSL2 pitfalls.
- **Vision & direction:** [`VISION.md`](VISION.md) — why the tool exists, the quality bar (*good enough to send to LLMs unread*), who it's for, decision principles, and non-goals. Calibrate judgment calls — scope, trade-offs, priorities — against it. Stability is principle #1; the quality floor and the no-training-on-user-data rule are hard gates. The target user, in one sentence: a self-reliant, German-dictating techie who wants no subscription, no lock-in, verifiable privacy, and a tool that bends to their workflow (full persona under "Who it's for" in `VISION.md`).
- **macOS:** [`thoughtborne-macos`](https://github.com/timwessels/thoughtborne-macos) — an archived, much earlier variant.

## Run & verify

- Run: `uv run thoughtborne.py`, or double-click `Thoughtborne.bat`. Requires Windows, a microphone, and at least one API key in `.env` (template: `.env.example`). Python 3.10 is the floor (`requires-python` in `pyproject.toml`), so nothing 3.11-only such as `tomllib` — CI's 3.10 leg catches it, but only after the push.
- Working from inside WSL2? The tool itself must run Windows-side; [`llms-install.md`](llms-install.md) covers the interop. `uv sync` does not work off Windows either — `pyproject.toml` resolves for `sys_platform == 'win32'` only.
- **Verification is a ladder.** Bottom rung: `python -m py_compile <changed files>`. Middle rung: `python3 run_tests.py` runs every `test_*.py` in the repo root off-Windows (stdlib plus `soundfile`, `groq` and `pyflakes`), prints one verdict line per driver, exits non-zero if any failed, and surfaces each driver's `(skipped ...)` notes so a green run never reads as if a skipped check had passed. CI runs the same command on every push and pull request ([`.github/workflows/tests.yml`](.github/workflows/tests.yml), Python 3.10 + 3.12). Top rungs, hands-on only: start the tool and check the startup masthead plus `All hotkeys registered successfully` in `thoughtborne.log`, then let the in-app self-test `Ctrl+Alt+T` transcribe `test_audio.mp3` end to end (needs a valid API key).
- Each driver is a plain script that runs and reads on its own; each also carries a `test_all()` so `python -m pytest` can collect it, but `run_tests.py` and CI stay the gate. What a driver checks in detail lives in its module docstring — read it before changing what the driver guards, and keep the detail there rather than here.

| Driver | What it covers | Flags |
| --- | --- | --- |
| `test_app_icon.py` | The shipped `assets/logo/thoughtborne.ico` against `console_ui.LOGO_MARK_A5` and `ACCENT` (D-016): every frame decoded and compared pixel for pixel, plus the two consumers (`setup.ps1`, the settings window) naming that file, and that window setting both `iconbitmap` forms in separate guards (#296). | `--show` |
| `test_archive_migration.py` | `config.migrate_legacy_archives` against tempdir layouts, asserted on the files on disk; an escaping exception is itself a failure. | `--show` |
| `test_audio_stall.py` | The audio stall/deadlock guards in `audio_handler`, against a fault-injecting fake stream. | — |
| `test_config_loading.py` | The hardened `personal_settings.json` and `.env` readers against tempdir fixtures, each re-checked by a real `import config` in a subprocess, plus the one `.env` parser both halves share, the guards that keep the process environment out (D-017), the `pyproject.toml` version reader with its regex twin in `setup.ps1`, and the checkout state read from the `.git` beside the script, fail-open in every shape git writes it. | `--show`; skips its `groq` lane when that is absent, and its real-repository case where there is no `.git` |
| `test_console_ui.py` | Every console panel/strip: widths, CP437 charset, the plain-ASCII twin, red-exclusivity, the key-aware lineup and the keyless panels, the mixed-scheme fixtures for every key-bearing surface, the combo stress check, the static guard that the app derives no key of its own (D-019), the app ↔ fixture seam measured in both directions, and the values a surface must show. | `--show` |
| `test_deps_sync.py` | `pyproject.toml`'s dependencies and `requirements.txt` stay in lockstep, naming the drifting package. | `--show` |
| `test_engine_memory.py` | The last-selected-engine memory (`runtime_state.json`): round-trip, fallbacks that must never cost a start, the startup precedence rule (D-008). | — |
| `test_hotkey_overrides.py` | The `personal_settings.json` hotkey override surface — `hotkey_parse` and `config.apply_hotkey_overrides` — plus the guard that every shipped default is a statically mapped key (D-012) and the one holding the README twins' `## Hotkeys` tables to `DEFAULT_HOTKEYS`, order and combos (D-019). | `--show` |
| `test_ptt_detector.py` | The push-to-talk gesture state machine against synthetic tick sequences, compared as exact per-tick action lists. | — |
| `test_restart_signal.py` | The settings-app → tool restart handshake: the signal file's write/consume round-trip and the no-shutdown-without-a-successful-consume invariant. | — |
| `test_retry_marker_lifecycle.py` | The persistent retry-marker lifecycle against a tempdir archive (D-001), plus the guard that importing `output_handler` leaves pyautogui's corner fail-safe off. | — |
| `test_settings_instance.py` | The settings single-instance guard (D-009): lazy ctypes, the localized window titles, a mutex name distinct from the tool's, fail-open off-Windows. | — |
| `test_settings_io.py` | The settings-app IO core: the surgical `.env` / `personal_settings.json` merges, the hotkey-combo helpers, `key_check`, the DE/EN string table with every key the app hands a text sink, the pure save/engine decision helpers. | `--show` |
| `test_settings_theme.py` | The theme module: palette shape, WCAG contrast on both surfaces, the type ladder, the `clam` pin and the page/scroll-canvas sync. | — |
| `test_settings_visibility.py` | The tkinter-free visibility helpers, the `[SETTINGS]` log lane, and the regressions driven against the real app (auto-hide, wrapping, maximize→restore, language toggle, the reset control, crash lanes, the tab strip fitting the minimum window width, and no placed text element left blank). | `--show` |
| `test_setup.py` | Static drift guard on `setup.ps1`, `setup.bat`, `uninstall.ps1` and the sandbox harness — reads them as text, runs no PowerShell; real behaviour belongs to `sandbox/`. | `--show` |
| `test_text_cleanup.py` | The transcript cleanup functions `_remove_spoken_fillers` and `_clean_groq_hallucinations`, every case as input → output. | `--show` |
| `test_typed_cap.py` | The typed-insert length cap helper `cap_typed_text` (D-003). | — |
| `test_typed_cap_wiring.py` | That the cap is actually wired into both typed routes of the real `OutputManager` — the gap `test_typed_cap.py` cannot close. | — |
| `test_undefined_names.py` | The undefined-name gate (#289): every Python file in the checkout through pyflakes, red only on a short, closed list of messages that mean broken code, with the #269 mutation and its two controls as a per-run proof that the lane still detects. | `--show`; skips without `pyflakes` |

## While the tool is running

If `thoughtborne.py` is running **from this checkout**, do not modify code, rename files, or otherwise disturb the working directory. Ask the user to stop it with `Ctrl+Alt+4` first — they may be dictating into the very agent session that is being asked to edit, and the hotkey exit is the clean handoff. An instance running from a *different* checkout does not block edits here, but it owns the global hotkeys, so don't launch the tool from this checkout while one runs elsewhere.

The reliable check is the log heartbeat: the recording loop writes a `Recording loop alive` DEBUG line to that checkout's `thoughtborne.log` every 60 seconds, so the tool is running exactly when the log's mtime is fresh (under ~3 minutes) **and** its final lines do not say `Program ended`. Neither half alone suffices: a clean shutdown writes `Program ended` and still looks recently touched, and a stale mtime without that line just means the process died uncleanly. A process-list check is *not* reliable: when the tool runs elevated, a non-elevated query (e.g. `Get-CimInstance Win32_Process`, typical from WSL) sees the python process but an empty `CommandLine`, and falsely reports the tool as not running.

## Conventions

- **This file is a map, not a ledger.** It is loaded into every session, so every line costs every session; its job is what a fresh agent needs before opening a single file. Test detail belongs in the driver's docstring, a coupling the code cannot show in the docstring of the file it binds, and when something landed and why in `CHANGELOG.md`, `DECISIONS.md` and the commit. Issue numbers only where they keep a settled question from being reopened.
- **Language:** English for code, inline comments, commit messages, and all public documentation.
- **Bilingual README:** `README.md` (English) and `README.de.md` (German) are content-equivalent twins — a change to one is mirrored in the other.
- **Install-step sync:** the install commands also live in `docs/index.html` and `docs/en/index.html`, and `llms-install.md` describes the clone path in prose — a change to the install steps is mirrored across all of these in the same change.
- **Commit messages:** short, imperative English; reference issues with `(#N)`.
- **CHANGELOG.md:** non-trivial changes get an entry under `## [Unreleased]` (Keep-a-Changelog categories: `### Added` / `### Changed` / `### Fixed` / `### Removed`).
- **Decision log:** `DECISIONS.md` records deliberate, contestable product decisions. Check it **before discussing or specifying any behavior change** — issue texts included — so a settled call is not silently reopened. An issue that touches a recorded decision cites it (`respects D-001` / `proposes superseding D-001`); superseding an entry needs the maintainer's okay (mark the old one `Superseded by D-NNN`, never delete it).

## Guardrails

- **Never commit `.env`**, and never reproduce API keys in committed files, logs, or chat output.
- **Hallucination-filter patterns** in `transcriber.py` (`_clean_groq_hallucinations`) are **data**, not prose. Never translate, paraphrase, or "improve" them.
- **Umlaut hotkey support is live machinery, not dead code.** No shipped default uses `ü` (the self-test is `Ctrl+Alt+T`, D-012), but users still bind it through the `personal_settings.json` overrides — keep the `VkKeyScanW` path in `hotkey_manager.py`, the `KEY_SPECIAL` lane in `hotkey_parse.py`, the `udiaeresis` keysym in `settings_io.py`, and the `ue` → `ü` spelling in `hotkey_parse.canonical_combo`, which `format_combo` renders as `Ü` for every surface (the console charset check allows that one glyph on purpose). Conversely, never move a shipped default onto a layout-resolved key: it has no static VK code, and registration fails off German QWERTZ.
- **Folders starting with `_`** (e.g. `_research/`, `_backups/`) are gitignored, local-only workspaces of whoever owns the checkout. Never auto-delete, never "clean up", never remove duplicates. Deletion only on explicit instruction.
- **Be conservative with working code.** Do not refactor or rewrite without a stated reason — "clean code is a feature" (`VISION.md`) justifies keeping code legible *while changing it for a reason*, not standalone rewrites. Direction and ambition live in `VISION.md`; this rule caps code churn, it is not a feature freeze.

## Where things live

- **Source:**
  - `thoughtborne.py` — the app: hotkey actions, recording loop, single-instance guard, the settings-app spawn.
  - `audio_handler.py` — capture, the stall guards, the retry-marker files.
  - `transcriber.py` — the four engines and the transcript cleanup.
  - `output_handler.py` — text insertion: the typed and clipboard routes, plus the send-after-insert flag.
  - `hotkey_manager.py` — Win32 hotkey registration; `hotkey_parse.py` — the ctypes-free lexical layer it shares with `config`, plus the one canonical combo spelling (`canonical_combo`) and its display form (`format_combo`, `first_combo`).
  - `ptt_detector.py` — the push-to-talk gesture state machine, Win32-decoupled.
  - `config.py` — constants, the `.env` and `personal_settings.json` loading with the hotkey/engine overrides, the legacy-archive migration, and `VERSION` read from `pyproject.toml` (the regex twin of `setup.ps1`'s).
  - `console_ui.py` — the console renderer, pure stdlib.
  - `typed_cap.py` — the 4,000-character typed-insert cap (D-003).
  - `engine_memory.py` — the last selected engine, remembered in `runtime_state.json`, and the startup precedence rule (D-008).
  - `restart_signal.py` — the settings-app → tool restart handshake (the `restart_request` file) and the tool's single-instance mutex name, the one source both sides share.
- **Settings app:**
  - `thoughtborne_settings.py` — the tkinter settings/onboarding window, pure stdlib. It opens only from the running tool (`Ctrl+Alt+G`, or the `--first-run` wizard spawn; D-014) and has no launcher of its own.
  - `settings_io.py` / `key_check.py` — its file IO, validation and live key check; no tkinter.
  - `settings_strings.py` — the DE/EN string table and `t()`; the app defaults to English with no system-language detection (D-015). Its `engine.desc.*` EN descriptors track `config.API_DISPLAY`, so change both together.
  - `settings_instance.py` — the single-instance guard and focus-existing remedy (D-009), with a mutex name distinct from the tool's.
  - `settings_visibility.py` — the tkinter-free scroll/wrap helpers and the `[SETTINGS]` log lane, which takes the log path as a parameter so tests can redirect it.
  - `settings_theme.py` — the `clam`-based ttk theme and design tokens the app applies first (D-010).
- **Windows launcher:** `Thoughtborne.bat` — runs the tool via uv and offers the uv install when it is missing.
- **Installer & uninstaller:** `setup.ps1` (the ASCII-only, BOM-free, iex-safe installer shipped as a release asset — uv-based, idempotent, writes the per-user Installed-apps entry), `setup.bat` (the double-click/ZIP wrapper and in-place updater), `uninstall.ps1` (GUI uninstaller — PowerShell + WinForms, no Python, no admin; keeps user data by default, and its silent lane can never delete it, D-011), and `sandbox/` (the Windows-Sandbox pre-release harness — install, hotkeys, transcribing self-test, a settings-window screenshot; its README is the reference, and `temp.env`, `out-*/`, `*.local.wsb` are gitignored).
- **Test ladder:** the `test_*.py` drivers in the repo root and `run_tests.py` (see *Run & verify*).
- **Brand assets:** `assets/logo/` — the SVG/PNG masters and `thoughtborne.ico`, the one Windows app icon (Start-menu shortcut, Installed apps, the settings window; D-016), rebuilt from `console_ui.LOGO_MARK_A5` by `assets/logo/make_app_icon.py` (Pillow, build-time only). Its README lists every file and the palette.
- **Public docs:** `README.md`, `README.de.md`, `CHANGELOG.md`, `DECISIONS.md`, `VISION.md`, `CONTRIBUTING.md`, `SECURITY.md`, `RELEASING.md` (the maintainer release ritual — tag, build the `git archive` install ZIP + `setup.ps1` assets, publish; D-006), `build-release-zip.sh` (builds both release assets into gitignored `dist/` and dry-run-verifies the ZIP; never tags or publishes), `LICENSE`, `AGENTS.md`, `llms-install.md`, `.env.example`, `personal_settings.example.json`.
- **Website:** `docs/` — the public project site at [thoughtborne.app](https://thoughtborne.app), deployed by `.github/workflows/deploy-site.yml` (an FTPS mirror of `docs/` on every `main` push that touches `docs/**`). Public content, not a local workspace. Site code stays comment-lean: a comment earns its place only by recording an invariant or trap the code cannot show (sync couplings such as the twin install one-liners, the `?v=` cache-buster discipline); design history and rationale belong in commits and issues.
- **User data** (created at runtime, gitignored): `history/` (recordings in `audio/`, transcripts in `transcripts/`), `thoughtborne.log`, and `runtime_state.json` (machine-written, but it carries the user's last engine choice) — never delete. Older checkouts may still carry the legacy `voice_archive/` + `text_archive/` folders (auto-migrated into `history/` at startup) — never delete those either.
