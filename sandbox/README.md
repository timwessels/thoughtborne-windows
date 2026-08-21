# Windows-Sandbox install verification (#76)

A throwaway-VM harness that runs the real Thoughtborne install path end to end on
a clean Windows and checks it reaches a working state -- hotkeys registered, then
the end-to-end self-test transcribes. It is the standard **pre-release sanity
check** for the installer, and the place the `test_setup.py` static guard's honest
gaps (real execution-policy bypass, real uv bootstrap and `uv sync`, real ZIP
fetch/extract, shortcut creation, the actual end-to-end launch) get exercised for
real.

The harness is **finished** and driven from the host by `run-sandbox.ps1`: you run
one script, it starts a disposable sandbox, the sandbox installs and launches
Thoughtborne on its own, and a verdict file comes back. The committed
`thoughtborne-install-test.wsb` is a **portable template** -- do not hand-edit it;
the launcher fills the real host path into a `%TEMP%` copy at run time (see
*Files*). The harness structure is drift-guarded by `test_setup.py` (#181); the two
things only a real Windows box can settle are listed under *What it does not cover*.

## Requirements

- **Windows 11 Pro or Enterprise.** Windows Sandbox is not available on Home.
- Enable the feature once (admin, one reboot): turn on *Windows Sandbox* in
  *Turn Windows features on or off*, or from an elevated PowerShell enable the
  `Containers-DisposableClientVM` optional feature, then reboot.
- A **published release carrying the two assets** (`setup.ps1` + `thoughtborne.zip`)
  for the full one-liner path. Until that exists (#145 / WP6), `setup.ps1`'s code
  fetch 404s -- see *Modes* below.

## Files

- `run-sandbox.ps1` -- **the host-side launcher (this is what you run).** It
  preflights (Windows Sandbox present, `temp.env` present, and for a `local` run a
  `setup.ps1` copy present), copies the `.wsb` template to `%TEMP%` with the real
  host path and `-Mode`/`-Version` spliced in, starts the disposable sandbox by CLI
  (`WindowsSandbox.exe`), and polls the mapped folder for this run's
  `out-<timestamp>\RESULT.txt`. It **never** runs the installer on the host -- the
  real install path executes only inside the throwaway VM.
- `thoughtborne-install-test.wsb` -- the sandbox config, a **portable template**.
  Maps this `sandbox/` folder in and runs `verify-in-sandbox.ps1` at logon. Its
  `<HostFolder>` holds the placeholder token `SANDBOX_HOSTFOLDER_ABS_PATH`; **do
  not hand-edit it** -- `run-sandbox.ps1` substitutes the real absolute path into a
  `%TEMP%` copy at run time (Windows Sandbox needs an absolute host path and does
  not expand env vars, so the tracked file cannot ship a machine-specific path).
- `verify-in-sandbox.ps1` -- the in-sandbox driver: drop the throwaway key into the
  install dir -> install (setup.ps1's own #223/D-014 hand-off starts the tool, so the
  driver never launches it itself) -> poll `thoughtborne.log` for
  `All hotkeys registered successfully` -> fire the `Ctrl+Alt+T` self-test and poll
  for a transcription -> copy logs + screenshots out -> write a `RESULT.txt` verdict.

## The throwaway API key (required)

Drop a file named **`temp.env`** in this folder before running, holding one
working key line, e.g. `SONIOX_API_KEY=...` or `GROQ_API_KEY=...`. Without it the
harness reports `SKIP`: on a keyless start the tool opens the #144 onboarding
wizard and exits **before** registering hotkeys, so the "hotkeys registered"
assertion could never fire (and the self-test has no engine to transcribe with).
**Never commit `temp.env`** -- it is a real key. The repo `.gitignore` excludes it
(and the per-run `out-<timestamp>/` folders, the throwaway `setup.ps1` / `setup.bat`
copies below, and any `*.local.wsb`); keep it out of any commit regardless.

## Run it

1. Put a `temp.env` here (see above).
2. For a `local` run, copy the installer into this folder first: `setup.ps1` from
   the repo root is **required** -- the mapped folder is all the sandbox sees, and
   the driver runs the `setup.ps1` it finds here (copy `setup.bat` too if you want
   to exercise the double-click wrapper). Both are gitignored here as throwaway
   copies; the canonical ones live in the repo root. An `oneliner` run skips this
   step -- it fetches the published `setup.ps1` from the release URL.
3. Run `run-sandbox.ps1` -- from the host, or from WSL via `powershell.exe ... -File`:

   ```
   powershell.exe -NoProfile -ExecutionPolicy Bypass \
     -File "$(wslpath -w sandbox/run-sandbox.ps1)" -Mode oneliner -Version v1.1.0-rc
   ```

   It generates the run config, launches the throwaway sandbox, and polls for a new
   `out-<timestamp>\` folder here (`RESULT.txt` plus the captured `thoughtborne.log`
   and screenshots). `-Mode local` (the default) tests the copied-in `setup.ps1`;
   `-Version` is optional (empty => the release `latest` alias). The verdict is a
   file, so an agent driving this from WSL never blocks on the sandbox-desktop GUI.

Expected during a successful run: after `uv sync`, `setup.ps1` creates **one**
Start-menu shortcut and **starts the tool itself** (its #223/D-014 hand-off). Because
the throwaway key is placed before the install, that instance is a keyed one -- it
opens as the Cockpit console (not the wizard) and registers its hotkeys, and that is
the instance the harness then verifies. Any screenshot capture just catches that
console on screen.

## Modes

`run-sandbox.ps1 -Mode` (threaded through to `verify-in-sandbox.ps1`):

- `local` (default) -- runs the `setup.ps1` copied in via the mapped folder. Good
  for testing a work-in-progress script offline. **Caveat:** `setup.ps1` still
  fetches the code ZIP from the release URL, so even `local` mode needs the
  published `thoughtborne.zip` to finish the copy step; before then it exercises
  the preamble, guards, and uv bootstrap only.
- `oneliner` -- fetches and runs the *published* `setup.ps1` from the release
  `latest/download` URL (or the `-Version` tag's URL): the real end-user path.
  Needs a published release.

## Verdicts

`RESULT.txt`'s first line is the verdict; the launcher prints it and maps it to
its own exit code:

- **`PASS`** (exit 0) -- install + hotkeys registered + self-test transcribed.
- **`PARTIAL`** (exit 2) -- install and hotkeys OK, but the self-test could not be
  confirmed transcribing (injection unconfirmed, or fired but no transcription).
  The `RESULT.txt` detail line names the cause. Non-zero on purpose, so a bare
  exit-code check never waves a run through whose self-test did not confirm.
- **`FAIL`** (exit 1) -- install, boot, or hotkey registration is broken
  (release-blocking).
- **`SKIP`** (exit 3) -- no `temp.env`, so the run cannot even reach hotkey
  registration.

The launcher also exits **4** when no verdict lands within its timeout
(`-ResultTimeoutSec`, default 900 s -- a first run does a real `uv sync` with a
~22 MB Python download inside the install call). Inspect the open sandbox window
and the newest `out-*` folder in that case.

## What it does not cover

Two things only a real machine or full VM can settle -- the sandbox cannot:

- **Defender / AMSI and Edge SmartScreen fidelity.** Windows Sandbox does not
  reproduce the host's Defender real-time scanning or Edge's "not commonly
  downloaded" gating, and the one-liner's `WebClient` fetch bypasses Edge /
  SmartScreen entirely. Whether the install path stays clean under real Defender /
  AMSI and SmartScreen is a real-box / VM check.
- **First confirmation of the self-test path.** The `Ctrl+Alt+T` self-test injects
  the registered chord as synthetic input and expects `RegisterHotKey` to fire;
  this path is reasoned from the code, not yet run for real. Until a first real
  pass confirms it, a run where injection does not trip the hotkey stays `PARTIAL`
  (install is fine), not `FAIL`, and the cause is named in `RESULT.txt`.
