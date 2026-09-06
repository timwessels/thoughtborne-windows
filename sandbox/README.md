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
*Files*). The harness structure is drift-guarded by `test_setup.py` (#181); the
limits of a green run are listed under *What it does not cover*.

## Requirements

- **Windows 11 Pro or Enterprise.** Windows Sandbox is not available on Home.
- Enable the feature once (admin, one reboot): turn on *Windows Sandbox* in
  *Turn Windows features on or off*, or from an elevated PowerShell enable the
  `Containers-DisposableClientVM` optional feature, then reboot.
- A **published release carrying the two assets** (`setup.ps1` + `thoughtborne.zip`,
  D-006). Both modes need one: even a `local` run has `setup.ps1` fetch the code ZIP
  from the release URL -- see *Modes* below.

## Files

- `run-sandbox.ps1` -- **the host-side launcher (this is what you run).** It
  preflights (Windows Sandbox present, `temp.env` present, and for a `local` run a
  `setup.ps1` copy present), copies the `.wsb` template to `%TEMP%` with the real
  host path and `-Mode`/`-Version` spliced in, starts the disposable sandbox by CLI
  (`WindowsSandbox.exe`), waits for evidence that it really came up
  (`-BootTimeoutSec`, exit 6 if nothing ever runs -- a sandbox that is alive but
  slow is waited out instead, see *Exit codes*), and polls the mapped folder for this
  run's `out-<timestamp>\RESULT.txt`. When it is done it stops the sandbox it
  started (`-KeepSandbox` leaves it running) and writes a `HOST.txt` run record --
  mode, version, timestamps, exit code, and whether the host screen was locked while
  the run worked. It **never** runs the installer on the host -- the real install
  path executes only inside the throwaway VM.
- `thoughtborne-install-test.wsb` -- the sandbox config, a **portable template**.
  Maps this `sandbox/` folder in and runs `verify-in-sandbox.ps1` at logon. Its
  `<HostFolder>` holds the placeholder token `SANDBOX_HOSTFOLDER_ABS_PATH`; **do
  not hand-edit it** -- `run-sandbox.ps1` substitutes the real absolute path into a
  `%TEMP%` copy at run time (Windows Sandbox needs an absolute host path and does
  not expand env vars, so the tracked file cannot ship a machine-specific path).
- `verify-in-sandbox.ps1` -- the in-sandbox driver: drop the throwaway key into the
  install dir -> install (setup.ps1's own #223/D-014 hand-off starts the tool, so the
  driver never launches it itself) -> poll `thoughtborne.log` for
  `All hotkeys registered successfully` -> build a key-injection binding and record
  the image's fingerprint in `ENV.txt` -> fire the `Ctrl+Alt+T` self-test and poll
  for a transcription -> open the settings window with `Ctrl+Alt+G` and photograph it
  (see *Settings-window lane*) -> copy logs + screenshots out -> write a `RESULT.txt`
  verdict. The chords are synthesized from the exact modifier+VK the tool logged, so
  they are layout-independent, and the preferred binding is built in memory (no
  on-disk C# compiler), with the classic compiler route kept as a fallback; the
  detail line names which of the two carried the run as `injection-route=`, or
  `none` when the image supports neither.
- `settings-shot-checklist.md` -- what the settings-window screenshot is graded
  against, and the exact `SETTINGS-SHOT.txt` answer format. See *Settings-window lane*.

## The throwaway API key (required)

Drop a file named **`temp.env`** in this folder before running, holding one
working key line, e.g. `SONIOX_API_KEY=...` or `GROQ_API_KEY=...`. Without it the
launcher's preflight refuses to start the run (exit 1), and the driver reports
`SKIP` if it ever runs keyless anyway: a keyless start stays open as the #200 shop
window and *does* register its hotkeys, but it has no engine -- so the self-test
lane, the part that separates `PASS` from `PARTIAL`, could never be exercised.
**Never commit `temp.env`** -- it is a real key. The repo `.gitignore` excludes it
(and the per-run `out-<timestamp>/` folders, the throwaway `setup.ps1` / `setup.bat`
copies below, and any `*.local.wsb`); keep it out of any commit regardless.

## Run it

1. Put a `temp.env` here (see above).
2. For a `local` run, copy the installer into this folder first: `setup.ps1` from
   the repo root is **required** -- the mapped folder is all the sandbox sees, and
   the driver runs the `setup.ps1` it finds here. (A `setup.bat` copy only makes the
   double-click wrapper available for a hands-on try inside the sandbox -- no lane of
   the harness runs it.) Both are gitignored here as throwaway copies; the canonical
   ones live in the repo root. A `oneliner` run skips this step -- it fetches the
   published `setup.ps1` from the release URL.
3. Run `run-sandbox.ps1` -- from the host, or from WSL via `powershell.exe ... -File`:

   ```
   powershell.exe -NoProfile -ExecutionPolicy Bypass \
     -File "$(wslpath -w sandbox/run-sandbox.ps1)" -Mode oneliner -Version v1.1.0-rc
   ```

   It generates the run config, launches the throwaway sandbox, and polls for a new
   `out-<timestamp>\` folder here: `RESULT.txt`, the captured `thoughtborne.log`, the
   screenshots, `ENV.txt` (what the sandbox image offered -- PowerShell version and
   language mode, whether it carries Notepad and the C# compiler, which injection
   route bound) and `HOST.txt` (the host-side run record). `-Mode local` (the
   default) tests the copied-in `setup.ps1`; `-Version` names the release the code
   ZIP -- and in `oneliner` mode the installer script -- is fetched from, empty
   meaning the `latest` alias, which GitHub resolves to the newest non-prerelease
   (a tag that is not Latest has to be named). The verdict is a file, so an agent
   driving this from WSL never blocks on the sandbox-desktop GUI.

Expected during a successful run: after `uv sync`, `setup.ps1` creates **one**
Start-menu shortcut and **starts the tool itself** (its #223/D-014 hand-off). Because
the throwaway key is placed before the install, that instance is a keyed one -- it
opens as the Cockpit console (not the wizard) and registers its hotkeys, and that is
the instance the harness then verifies. Any screenshot capture just catches that
console on screen.

## Modes

`run-sandbox.ps1 -Mode` (threaded through to `verify-in-sandbox.ps1`):

- `local` (default) -- runs the `setup.ps1` copied in via the mapped folder, so a
  work-in-progress installer can be tested before it is published. **Caveat:** that
  script still fetches the code ZIP from the release URL, so a `local` run needs the
  published `thoughtborne.zip`, and the code it installs is the released one, not
  this checkout.
- `oneliner` -- fetches and runs the *published* `setup.ps1` from the release
  `latest/download` URL (or the `-Version` tag's URL): the real end-user path.
  Needs a published release.

## Verdicts

`RESULT.txt`'s first line is the verdict; the launcher prints it and maps it to
its own exit code:

- **`PASS`** (exit 0) -- install + hotkeys registered + self-test transcribed: the
  synthesized chord really tripped `RegisterHotKey` in the running tool, and the
  bundled `test_audio.mp3` came back transcribed.
- **`PARTIAL`** (exit 2) -- install and hotkeys OK, but the self-test could not be
  confirmed transcribing (injection unconfirmed, or fired but no transcription).
  The `RESULT.txt` detail line names the cause and the run's `ENV.txt` says what
  the image offered; a hands-on keypress on a real box is the backstop. Non-zero on
  purpose, so a bare exit-code check never waves a run through whose self-test did
  not confirm.
- **`FAIL`** (exit 1) -- install, boot, or hotkey registration is broken
  (release-blocking).
- **`SKIP`** (exit 3) -- no `temp.env`, so the tool has no engine and the self-test
  lane cannot run. The launcher's preflight normally refuses before the sandbox even
  starts, so this verdict means the driver itself found no key.

The detail line also carries two **reported-only** items that never change the
verdict: `injection-route=` (which key-injection mechanism carried the run, or
`none`) and `settings=` (the settings-window lane below).

Three host-side exit codes are not verdicts:

- **6** -- the sandbox never came up. `-BootTimeoutSec` (default 300 s) is a
  *fail-fast* window, not a kill deadline: when it expires the launcher looks for a
  running sandbox, and only if there is none does the run end right there -- the
  `.wsb` was rejected, which Windows reports as a GUI dialog an automated run never
  sees. If a sandbox **is** alive, the launcher says so and keeps waiting for the
  driver's `out-*` folder up to the run's whole budget (boot + result), because the
  `.wsb`'s own mapped-folder wait can take ~180 s before the driver runs at all; only
  then does it give up with 6, pointing at the LogonCommand or the mapped folder. A
  slow sandbox is never stopped mid-run.
- **4** -- it booted, but no verdict landed within `-ResultTimeoutSec` (default
  900 s -- a first run does a real `uv sync` with a ~22 MB Python download inside
  the install call). Inspect the newest `out-*` folder.
- **5** -- no usable verdict: `RESULT.txt`'s first line is not one of the four
  above, or the launcher itself errored out (it says `LAUNCHER ERROR:` and records
  `verdict=LAUNCHER-ERROR` in `HOST.txt`, so the run record and the exit code agree).

A preflight refusal -- no Windows Sandbox, no `temp.env`, `-Mode local` without a
`setup.ps1` copy -- also exits 1, but before anything starts: it prints an `ERROR:`
line and produces no sandbox, no `out-*` folder and no `RESULT.txt`.

On every path after the launch the launcher **stops the sandbox it started** and
leaves a `HOST.txt` run record beside the verdict. Pass `-KeepSandbox` to keep the
window open for a hands-on look. The stop goes through `wsb stop --id`, scoped to
the ids that appeared since the launch, so it can never touch a sandbox this run
did not start -- and if the CLI reports no sandbox of this run (or is not present
at all), it says so and leaves the window rather than guessing.

## Settings-window lane

After the self-test the driver presses `Ctrl+Alt+G`, waits for the settings window
to report its first paint in the log, and captures the whole desktop as
`screen-settings-<HHMMSS>.png` -- so a release check catches a stray console window
or clipped text in the real app before a release does. (It retries the press: the
tool legitimately ignores `Ctrl+Alt+G` while the self-test's insert is still in
flight, #196.)

The harness cannot grade a picture. The screenshot is judged host-side against
[`settings-shot-checklist.md`](settings-shot-checklist.md), and the answer belongs
next to it as `SETTINGS-SHOT.txt`; the launcher prints both paths when a settings
screenshot exists. **The item does not gate the install verdict** -- `PASS` still
means install + hotkeys + transcribed self-test.

## What it does not cover

A `PASS` is one clean first install into a throwaway image, driven as far as a
transcribing self-test. What that leaves open:

- **Defender / AMSI and Edge SmartScreen fidelity.** Windows Sandbox does not
  reproduce the host's Defender real-time scanning, and nothing here meets Edge's
  "not commonly downloaded" gating: the installer arrives through `irm` or the
  mapped folder and the code ZIP through a `WebClient` download, so no browser is
  ever in the path. Whether the install path stays clean under real Defender / AMSI
  and SmartScreen is a real-box / VM check.
- **Every install lane but the first one.** Each run installs once into a fresh
  image, over a directory holding nothing but the pre-placed key -- so installing
  *over* an existing Thoughtborne never happens (neither the in-place update, D-007,
  nor a reinstall), the running-instance guard passes silently because nothing is
  running yet, nothing reads back the Apps-list entry the install writes, and
  `uninstall.ps1` never runs at all. The only automated cover those have is
  `test_setup.py`, which reads the scripts as text.
- **Dictation itself.** The self-test transcribes the bundled `test_audio.mp3`, so
  nothing here records from a microphone, and no part of the verdict depends on
  where the inserted transcript landed -- the Notepad target is a nicety for the
  screenshot.
- **The fallback key-injection route.** A stock sandbox image binds the preferred
  in-memory route, so runs report `injection-route=reflection-emit`; the
  `add-type-csc` fallback and the routeless `none` outcome are reasoned from the
  code, not run. Neither route survives Constrained Language Mode, which is why
  `ENV.txt` records the image's language mode beside the route.
