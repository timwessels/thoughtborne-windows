# llms-install.md — guided setup for AI coding agents

You are an AI coding agent setting up Thoughtborne for a human user. This file is your script: work through it top to bottom, adapt the commands to the machine you find, and **ask before running commands**. It is plain, human-readable Markdown by design — the user is welcome to read along.

Thoughtborne is a hotkey-driven voice-to-text tool for Windows. The user presses `Ctrl+Alt+W`, speaks, presses `Ctrl+Alt+A` — and the transcript appears at the cursor in whatever app is active.

## Objective

A working installation on the user's Windows machine.

**DONE WHEN — all four:**

1. Dependencies are installed (uv-managed `.venv`, or the pip fallback).
2. `.env` exists and contains at least one working API key.
3. The tool starts: the startup banner appears, and `thoughtborne.log` contains `All hotkeys registered successfully`.
4. The self-test passes: the user presses `Ctrl+Alt+Ü`, and the bundled `test_audio.mp3` is transcribed and inserted at the cursor.

## Step 0 — Environment checks (before anything else)

**Which side are you on?** If you are running in a Linux shell on a Windows machine (e.g. `Microsoft` or `WSL` appears in `/proc/version`), you are inside WSL2 — and this is the single most common setup mistake:

> **The WSL trap:** Thoughtborne must live in the *Windows-native* Python environment. Audio devices, global hotkeys, and text insertion exist only there. A Linux-side install fails by design: `uv.lock` resolves for Windows only, and PyAudio has no Linux wheels. Treat that failure as a signpost, not as a bug to fix. The same goes for the files: the repo must sit on the Windows filesystem (a `/mnt/<drive>/...` path as seen from WSL), not inside WSL's own Linux filesystem, which the Windows-side Python and uv cannot reliably work on.

From WSL2, run every install/start step through Windows interop (`powershell.exe` / `cmd.exe`), or hand those steps to the user. Path mapping: `/mnt/c/...` on the Linux side is `C:\...` on the Windows side (`wslpath -w` converts). A known-good pattern for starting the tool in its own window from WSL:

```
powershell.exe -Command "Start-Process cmd.exe -ArgumentList '/c','uv run thoughtborne.py' -WorkingDirectory 'C:\path\to\thoughtborne-windows'"
```

Known-bad: a single-line `cmd.exe /c start "title" ...` from WSL — interop quote handling breaks it (cmd hangs, the tool never starts).

**Tooling.** Check what is already there:

- Is `uv` installed? (`where uv` on Windows, `where.exe uv` from WSL.) If not, Step 2 covers installing it.
- Is `winget` available? (Only needed for the easiest uv install.)
- Only if the pip fallback becomes necessary: enumerate Pythons via the `py` launcher (`py --list`). Do not trust a bare `python` on a fresh machine — it may be the Microsoft Store stub, which opens the Store instead of running anything. Thoughtborne needs Python 3.10–3.13, **not 3.14** (PyAudio ships no 3.14 wheels).

**Hardware & permissions.** A microphone must exist, and Windows microphone access must be on (Settings > Privacy & security > Microphone). Not fully checkable from the CLI — ask the user.

**Internet.** The first start downloads Python and the dependencies once (uv).

## Step 1 — Get the code

If you are reading this file inside the cloned repo, this step is done. Otherwise:

```
git clone https://github.com/timwessels/thoughtborne-windows.git
```

or download and unpack the ZIP from GitHub.

**From WSL.** Clone onto a Windows drive — `/mnt/c/...` or any other `/mnt/<drive>/...` path (see the WSL trap in Step 0). If the repo already sits inside WSL's Linux filesystem (typically a clone into the Linux home), move the folder to a `/mnt/...` path before continuing.

## Step 2 — Install dependencies

Outcome: `uv run thoughtborne.py` can resolve and launch. There is no separate "install dependencies" command on the uv path — `uv run` (and `Thoughtborne.bat`) creates and syncs the local `.venv` on every start.

- **Primary (uv).** If uv is missing: `winget install --id=astral-sh.uv -e` — or simply have the user double-click `Thoughtborne.bat`, which offers the uv install itself. (A shell that was already open does not see winget's PATH update; open a fresh one, or let the .bat find the uv shim on its own.)
- **Fallback (pip + venv)** — only when uv is not an option:

  ```
  py -3.13 -m venv .venv
  .venv\Scripts\activate
  pip install -r requirements.txt
  pip install -r requirements-optional.txt
  ```

  The optional file installs the Soniox SDK: it enables the fast v2 path of the `soniox` upload slot. Without it that slot still works, served entirely by the v5 engine.
- Do not "pre-verify" with a Linux-side dry run — see the WSL trap in Step 0.

## Step 3 — API keys (the user does the signups)

Frame this honestly: the keys are the user's own, and audio goes only to the transcription API they choose, nowhere else (see [`VISION.md`](VISION.md)). At least one key is required — the tool refuses to start keyless, with an error block naming the missing keys. Offer both providers, explain the trade-off, and let the user pick (both is fine):

| Provider | Cost | Role in Thoughtborne | Console |
|---|---|---|---|
| **Groq** | Free tier, no payment needed (as of June 2026) | The free way in: `groq-large` (accurate) and `groq` (fastest) | console.groq.com |
| **Soniox** | Prepaid — top up a balance before the API works; no free tier (as of June 2026). Usage-based pricing (see soniox.com/pricing); typical personal dictation use stays low-cost | The default APIs: `soniox-live` (verbatim, instant) and `soniox` (polished upload) | console.soniox.com |

Guide the user through each provider they chose — signup, find the key, hand it over:

- **Groq:** sign up at https://console.groq.com → API Keys page (https://console.groq.com/keys) → Create API Key → copy it immediately (it is shown only once) → it goes into the `GROQ_API_KEY=` line of `.env`.
- **Soniox:** sign up at https://soniox.com → in the console, top up a small prepaid balance (required before the API works) → create and copy an API key → it goes into the `SONIOX_API_KEY=` line of `.env`.

Agent steps:

1. Copy `.env.example` to `.env` (`copy` in cmd/PowerShell, `cp` from WSL — the repo folder is shared between both sides).
2. Let the user paste the key to you, or let them edit `.env` themselves — their choice.
3. Write the key into the matching `.env` line. **Never echo, log, or commit it.**

With only a Groq key, startup automatically skips the Soniox entries and starts on the first available API, announcing each skip. To start on Groq silently instead, set `DEFAULT_API = "groq-large"` in `config.py` — ask the user, don't assume.

## Step 4 — Optional personalization (ask, don't assume)

Three things are commonly personalized. Offer them; apply only what the user wants.

- **Recognition vocabulary** (recommended): copy `personal_settings.example.json` to `personal_settings.json` and fill the `vocabulary` block with the user's names, project terms, and frequent foreign words. Used by every Soniox engine — Soniox Live and both paths of the Soniox upload slot; the Groq APIs ignore it. Without the file, the tool simply runs unpersonalized.
- **Hotkeys:** rebind any combination in the `hotkeys` block of `personal_settings.json` (copy `personal_settings.example.json` first) — a partial override keyed by action name, with `config.py` keeping the defaults. If a default collides with something the user already runs, override it there. F-keys `f1`–`f24` are allowed (including modifier-less ones like `f9`); avoid special characters like `#` and non-ASCII letters (they can cause issues with the keyboard module); the default `ü` self-test key is established and known to work. A bad or colliding entry warns in the log and keeps the default — never a failed start. The default engine is overridable the same way, in a `defaults` block (`"api"`).
- **Dictation language:** `LANGUAGE` in `config.py`, default `"de"`. Thoughtborne is German-first; English works, but the artifact filters and tuning target German — be honest about that if the user asks.
- **Push-to-talk** (opt-in, off by default): an alternative dictation gesture — tap Left-Ctrl, release, then press-and-hold Left-Ctrl; recording runs while held, releasing inserts. Enabled in the `push_to_talk` block of `personal_settings.json` (`enabled: true`); the trigger key, insert path (`clipboard` by default, `type` as the paste-blocked fallback), and timing thresholds are configurable there. Ask whether the user wants it — it reads every trigger press while enabled, so it is a deliberate choice. A mandatory AltGr filter keeps German QWERTZ characters (`@ \ { } [ ] | €`) from false-triggering. If the user works in a JetBrains IDE, mention that double-Ctrl is the IDE's "Run Anything" shortcut and they should enable "Disable double modifier key shortcuts" in the IDE's Advanced Settings to avoid the clash. Same elevated-window limit as the hotkeys (see Known failure modes).

## Step 5 — First start & self-test

Outcome: startup banner, `All hotkeys registered successfully` in `thoughtborne.log`, then a passing self-test.

1. Start the tool **in its own window** — it is interactive and long-running; never block your own shell with it. Easiest: the user double-clicks `Thoughtborne.bat`. From WSL, use the start pattern from Step 0.
2. Self-test: have the user focus a text field (plain Notepad works well), then press `Ctrl+Alt+Ü`. The tool transcribes the bundled `test_audio.mp3` through the active API and inserts the text at the cursor.
3. Real dictation: `Ctrl+Alt+W`, speak a sentence, `Ctrl+Alt+A`. The transcript should appear at the cursor. (`Ctrl+Alt+4` exits the tool.)
4. Walk the DONE-WHEN list and report status honestly — including anything that only half-works.

## Known failure modes

- **PyAudio build error during pip install** → Python 3.14 (no wheels yet) → use the uv path, or a Python 3.10–3.13.
- **`python` opens the Microsoft Store** → Store alias stub on a machine without Python → use the `py` launcher or the uv path.
- **`winget` not found** → install uv via the official installer instructions (https://docs.astral.sh/uv/getting-started/installation/) or use the pip path.
- **Tool starts but no audio / empty transcripts** → microphone permission off, or wrong default input device → Windows Settings > Privacy & security > Microphone; check the input device logged in `thoughtborne.log`.
- **A hotkey does not register** (a `FAILED:` line at startup in the log) → another program already owns that combination — global hotkeys are exclusive in Windows → override the combo in the `hotkeys` block of `personal_settings.json` (`config.py` keeps the defaults).
- **Insertion does nothing in one specific window** → the target app runs elevated (as administrator) and Windows blocks simulated input from non-elevated processes → run Thoughtborne elevated too, or dictate into non-elevated apps.
- **Push-to-talk does not respond while an elevated window has focus** → by design: the underlying key-state read returns nothing under Windows' UI-privilege isolation, exactly like the existing hotkeys → not a regression; run elevated too, or use a non-elevated window.
- **First start very slow, or fails offline** → uv downloads Python and the dependencies once; it needs internet that one time.
- **`uv run`/`uv sync` fails on Linux with a platform error** → you are on the WSL side; that failure is by design → back to Step 0.

## If things go wrong — escalation rules

After two or three failed attempts at the same step, stop and report to the user: what you tried, the exact error text, your best hypothesis, and the options you see. Do not escalate into creative workarounds.

**Never:**

- commit, echo, or log `.env` contents or API keys;
- delete or recreate `history/`, `voice_archive/`, `text_archive/`, or any other user data;
- modify project code beyond the documented configuration surfaces (`config.py` settings, `personal_settings.json`);
- uninstall or globally alter existing Python installations;
- disable Windows security features to make a step pass;
- use destructive git operations (the clone may already carry the user's local changes).

Questions beyond installation — contributing, conventions, guardrails — are covered in [`AGENTS.md`](AGENTS.md).
