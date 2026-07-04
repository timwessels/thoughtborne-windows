<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/thoughtborne-lockup-dark.svg">
    <img src="assets/logo/thoughtborne-lockup.svg" alt="Thoughtborne" width="420">
  </picture>
</p>
# Thoughtborne

**[Deutsche Version](README.de.md)**

Hotkey-driven voice-to-text for Windows. Press a hotkey, talk, press another — and the transcript lands at the cursor in whatever app is active, as if you had typed it. Optimized for German, built first and foremost for one job: talking to AI.

The quality bar is simple: transcripts must be **good enough to send to an LLM unread** — no proofreading pass. Why the tool exists, what guides its decisions, and what it deliberately is not (a polished GUI product, a cross-platform app, a subscription service) is in [VISION.md](VISION.md). You bring your own API keys and pay per use — or take the free path (see [the model lineup](#the-model-lineup)).

<!-- screenshot slot (#37): glanceable console screenshot drops in here -->

## The model lineup

Four transcription APIs, switchable at runtime with `Ctrl+Alt+L`. The lineup follows what tests best for German in hands-on use, re-evaluated every few months ([VISION.md](VISION.md)). Two jobs are deliberately kept side by side: verbatim transcripts that are ready the instant you stop, and polished ones that read like writing ([the two modes](VISION.md#two-modes-two-jobs-verbatim-vs-polished)).

| API | In short | What it does | Speed | Key & cost |
|-----|----------|--------------|-------|------------|
| **Soniox Live** | verbatim · instant (default) | Transcribes while you record — the transcript is ready the moment you stop, fillers and all; ideal for talking to AI. | ~0.5 s after stop | Soniox (prepaid) |
| **Soniox** | polished · takes longer | Sends the audio after you stop and returns text that reads like writing — clean punctuation, no fillers; for emails and texts meant for humans. | ~4–6 s (short) / ~10–40 s (long) | Soniox (prepaid) |
| **Groq Large** | accurate · free | The more accurate of the two free options — the recommended way to try Thoughtborne without paying. | ~1 s | Groq (free tier) |
| **Groq** | fast · free | The fastest option, for quick notes — accuracy below the other three. | ~0.7 s | Groq (free tier) |

**The free path:** both Groq entries run on Groq's free tier (as of June 2026: per model, 20 requests/min, 2,000 requests/day, 7,200 audio-seconds/hour, 28,800 audio-seconds/day) — you can try Thoughtborne without paying anyone. Soniox has no free tier (as of June 2026): you top up a small prepaid balance before the API works; pricing is usage-based ([soniox.com/pricing](https://soniox.com/pricing)). For scale: half a year of heavy personal use — on the order of 150 hours of transcription — cost roughly $10–15 ([VISION.md](VISION.md)).

Engines, for the curious: `stt-rt-v4` (Soniox Live) · `de_v2` + `stt-async-v4` (Soniox — short recordings run the sync v2 engine, long ones and the automatic fallback run v4 async; you don't need to care which ran) · `whisper-large-v3` (Groq Large) · `whisper-large-v3-turbo` (Groq).

## Requirements

- **Windows.** The tool is Windows-only by design (global hotkeys, audio capture, and text insertion are Win32); a macOS sister port exists (see [Project & links](#project--links)).
- **A microphone**, with Windows microphone access allowed (Settings > Privacy & security > Microphone).
- **At least one API key** — Groq (free) or Soniox (prepaid); see [API keys](#api-keys).
- **Internet.** Transcription runs through the APIs; the first start also downloads Python and the dependencies once.
- **No Python needed** on the standard path — uv downloads a suitable one automatically. (pip fallback: Python 3.10–3.13, not 3.14.)

## Installation

<!-- quick-start slot (#51): a guided setup path drops in here as the first option, when it exists -->

Three ways in — pick one. The commands work in PowerShell and cmd alike.

### Standard setup (uv)

Thoughtborne uses [uv](https://docs.astral.sh/uv/) as its Python project manager: uv downloads a suitable Python and all dependencies into a local `.venv` automatically — no pre-installed Python required.

1. **Install uv** (one-time):

   ```
   winget install --id=astral-sh.uv -e
   ```

   Then open a fresh terminal — a window that was already open does not see winget's PATH update (`Thoughtborne.bat` finds uv on its own either way).

   No winget? Use the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

2. **Get the code:**

   ```
   git clone https://github.com/timwessels/thoughtborne-windows.git
   cd thoughtborne-windows
   ```

   Or download and unpack the ZIP from GitHub — it unpacks as `thoughtborne-windows-main`, so adjust the `cd`.

3. **Set up API keys:** copy `.env.example` to `.env` and enter at least one key — where to get the keys is in [API keys](#api-keys).

   ```
   copy .env.example .env
   notepad .env
   ```

4. **Start:**

   ```
   uv run thoughtborne.py
   ```

   Or double-click `Thoughtborne.bat` — it starts the tool via uv and offers to install uv if it is missing. The first start downloads Python and the dependencies once; after that, uv keeps everything up to date automatically — even after a `git pull` with new dependencies, no manual steps are needed.

### Setup with an AI coding agent

Working with an AI coding agent (Claude Code, Cursor, Codex …)? Hand it the setup — [`llms-install.md`](llms-install.md) walks the agent through installation, API keys, and the self-test. In the cloned repo, just tell it:

```text
Read llms-install.md and guide me through the setup. Ask before running commands.
```

`llms-install.md` is ordinary, human-readable Markdown — feel free to read it yourself.

### Classic pip + venv (fallback)

Without uv, the classic way still works. Important: **Python 3.10–3.13, not 3.14** — PyAudio ships no pre-built wheels for 3.14 yet, so installation fails there with a build error.

```
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-optional.txt
python thoughtborne.py
```

The optional file installs the Soniox SDK. Without it, the `soniox` slot runs entirely on the v4 engine — it works, just slower for short recordings. (On the uv path the SDK is included automatically.)

## API keys

The keys are your own — you sign up directly with the providers. Audio goes to the transcription API you chose, and nowhere beyond that; recordings and transcripts are archived locally, nowhere else. Every integrated API must offer at least an opt-out from training on user data ([VISION.md](VISION.md)). At least one key is required — without any key, the tool refuses to start and names exactly which keys are missing.

**Groq** (free): sign up at [console.groq.com](https://console.groq.com) → API Keys page ([console.groq.com/keys](https://console.groq.com/keys)) → create a key and copy it immediately (it is shown only once) → put it into the `GROQ_API_KEY=` line of `.env`.

**Soniox** (prepaid): sign up at [soniox.com](https://soniox.com) → in the console ([console.soniox.com](https://console.soniox.com)), top up a small prepaid balance (required before the API works) → create and copy a key → put it into the `SONIOX_API_KEY=` line of `.env`.

Only a Groq key? Nothing to configure: startup automatically skips the Soniox entries, says so, and starts on the first available API. To start on Groq silently instead, set `DEFAULT_API = "groq-large"` in `config.py`.

## First run

Start the tool — double-click `Thoughtborne.bat` or run `uv run thoughtborne.py`. A console window opens with a startup banner showing the active API and the hotkey list; details go to `thoughtborne.log`.

Then dictate:

1. Focus any text field (plain Notepad works well).
2. Press `Ctrl+Alt+W` and say a sentence — the console confirms that the recording is running.
3. Press `Ctrl+Alt+A` — the transcript appears at the cursor.

**Self-test:** `Ctrl+Alt+Ü` transcribes the bundled `test_audio.mp3` through the active API and inserts the result at the cursor (focus a text field first) — the quickest way to check that everything works.

Your data stays with you: every dictation is kept in one `history/` folder in the project directory — recordings as MP3 in `history/audio/`, transcripts in `history/transcripts/`, paired by timestamp. The startup banner shows the path and `Ctrl+Alt+6` opens the folder; updating from an older version migrates the previous `voice_archive/` and `text_archive/` folders into it automatically on first start. If a transcription fails, `Ctrl+Alt+R` retries it from the archived recording.

`Ctrl+Alt+4` exits the tool.

## Hotkeys

| Hotkey | Action |
|--------|--------|
| `Ctrl+Alt+W` | Start recording (works while a previous recording is still transcribing) |
| `Ctrl+Alt+A` | Stop + insert at the cursor (simulated typing) |
| `Ctrl+Alt+D` | Stop + insert at the cursor (clipboard paste — faster) |
| `Ctrl+Alt+H` | Stop + insert + press Enter (one-press send, for chats) |
| `Ctrl+Alt+Y` | Stop + transcribe only — insert later with `A` or `D` |
| `Ctrl+Alt+X` | Cancel the recording (nothing is inserted) |
| `Ctrl+Alt+R` | Retry the last failed transcription (from the archived recording) |
| `Ctrl+Alt+L` | Switch transcription API (cycles Soniox Live → Soniox → Groq Large → Groq) |
| `Ctrl+Alt+6` | Open the recordings & transcripts folder (`history/`) in Explorer |
| `Ctrl+Alt+Ü` | Self-test: transcribe the bundled `test_audio.mp3` |
| `Ctrl+Alt+4` | Exit |

Transcripts are always inserted in recording order, even when several recordings are processing in parallel.

**`Ü` on a non-German keyboard:** `Ü` is its own key on the German QWERTZ layout (right of `P`). On other layouts, if the self-test does not trigger, remap the combination in the `HOTKEYS` dict in `config.py` — the entries there show the format.

## Customization

**Recognition vocabulary** (recommended): copy `personal_settings.example.json` to `personal_settings.json` and fill the `vocabulary` block with your names, project terms, and frequent foreign words — they are passed to the speech model as context and noticeably improve recognition. Used by Soniox Live and the v4 path of the Soniox upload slot; the Groq APIs ignore it. Without the file, the tool simply runs unpersonalized.

```
copy personal_settings.example.json personal_settings.json
```

**Push-to-talk (optional):** a second way to dictate, for quick short bursts. Tap **Left-Ctrl**, release, then **press and hold** Left-Ctrl — recording runs as long as you hold it, and releasing inserts the transcript at the cursor (just like the hotkeys). It is **off by default**; enable it in the `push_to_talk` block of `personal_settings.json`. Because Ctrl is the trigger, a mandatory AltGr filter makes sure German QWERTZ characters (`@ \ { } [ ] | € ~`) never set it off — AltGr is `Ctrl+Alt`, and the gesture only counts a *bare* Ctrl (no Alt, no other key down), so `Ctrl+C` → `Ctrl+V` and every other Ctrl combo are left alone too. Configurable in the same block: the trigger key (`lctrl`, `rctrl`, or `lalt`), the insert path (`clipboard` like `D` is the default; `type` like `A` is the fallback for apps that block paste; also `send` like `H` or `no_insert`), and the three timing thresholds. **JetBrains IDEs:** double-Ctrl is the IDE's "Run Anything" shortcut, so it collides when an IntelliJ-family IDE has focus — *enable* the "Disable double modifier key shortcuts" option in the IDE's Advanced Settings to switch that collision off. Note: as with all the hotkeys, push-to-talk cannot reach a window running elevated (as administrator) — Windows blocks input there for non-elevated processes. This is the same limitation the existing hotkeys have, not a new restriction.

**Settings in `config.py`:** the configuration is deliberately plain constants with comments. The ones most users touch:

- `DEFAULT_API` — the API at startup (`"soniox-live"`, `"soniox"`, `"groq-large"`, `"groq"`).
- `LANGUAGE` — default `"de"`. English works (`"en"`), but the artifact filters and tuning target German — honest expectations ([VISION.md](VISION.md)).
- `HOTKEYS` — all key combinations. If one collides with another program, change it here; avoid special characters like `#` and non-ASCII letters (the established `ü` is the known-good exception).

More settings (parallel transcriptions, audio trimming, …) are documented as comments in `config.py` itself.

**Or tell your coding agent.** The project's configurability strategy is readable code rather than a sprawling settings surface ([VISION.md](VISION.md)): describe the change you want to your AI coding agent — [`AGENTS.md`](AGENTS.md) gives it the ground rules for working in this repo.

## Troubleshooting

**PyAudio installation fails (pip path).** PyAudio ships official Windows wheels for Python 3.10–3.13 — `pip install` needs no compiler there. A build error usually means Python 3.14: switch to 3.13 or use the uv path (uv picks a suitable Python automatically).

**`python` opens the Microsoft Store.** That is the Store alias stub on a machine without Python — use the `py` launcher (as in the pip commands above) or the uv path.

**`winget` not found.** Install uv via the [official installer instructions](https://docs.astral.sh/uv/getting-started/installation/), or use the pip path.

**The tool starts, but no audio / empty transcripts.** Check Windows microphone permission (Settings > Privacy & security > Microphone) and the default input device; `thoughtborne.log` records which input device was used.

**A hotkey does not register** (a `FAILED:` line in the startup log). Another program already owns that combination — global hotkeys are exclusive in Windows. Change the combo in the `HOTKEYS` dict in `config.py`.

**Insertion does nothing in one specific window.** The target app runs elevated (as administrator), and Windows blocks simulated input from non-elevated processes. Run Thoughtborne elevated too, or dictate into non-elevated apps.

**First start is very slow, or fails offline.** uv downloads Python and the dependencies once; it needs internet that one time.

**API errors.** Check the keys in `.env` and your internet connection; on the free tier, mind the [rate limits](#the-model-lineup).

## Project & links

- [VISION.md](VISION.md) — why the tool exists, the quality bar, what guides decisions.
- [CHANGELOG.md](CHANGELOG.md) — what changed, release by release.
- [LICENSE](LICENSE) — MIT.
- For AI coding agents: [AGENTS.md](AGENTS.md) (working in this repo) · [llms-install.md](llms-install.md) (guided setup).
- **macOS:** a sister port exists — [thoughtborne-macos](https://github.com/timwessels/thoughtborne-macos): three transcription APIs instead of four, otherwise analogous; available as-is.

Issues and contributions are welcome. Thoughtborne has been the maintainer's daily tool for years and is actively maintained.
