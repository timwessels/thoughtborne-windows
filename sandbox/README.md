# Windows-Sandbox install verification (#76)

A throwaway-VM harness that runs the real Thoughtborne install path end to end on
a clean Windows and checks it reaches a working state (hotkeys registered). It is
the standard **pre-release sanity check** for the installer, and the place the
`test_setup.py` static guard's honest gaps (real execution-policy bypass, real uv
bootstrap and `uv sync`, real ZIP fetch/extract, shortcut creation, the actual
end-to-end launch) get exercised for real.

**This is a working skeleton with TODO markers**, not a finished harness. The
static structure is committed; the parts that can only be validated on a real
Windows 11 **Pro/Enterprise** box (screenshot capture, exact launch/poll timing,
the `Ctrl+Alt+Ü` self-test automation) are marked `TODO` in
`verify-in-sandbox.ps1` and need a hands-on pass before this is trusted green.

## Requirements

- **Windows 11 Pro or Enterprise.** Windows Sandbox is not available on Home.
- Enable the feature once (admin, one reboot): turn on *Windows Sandbox* in
  *Turn Windows features on or off*, or from an elevated PowerShell enable the
  `Containers-DisposableClientVM` optional feature, then reboot.
- A **published release carrying the two assets** (`setup.ps1` + `thoughtborne.zip`)
  for the full one-liner path. Until that exists (#145 / WP6), `setup.ps1`'s code
  fetch 404s -- see *Modes* below.

## Files

- `thoughtborne-install-test.wsb` -- the sandbox config. Maps this `sandbox/`
  folder in and runs `verify-in-sandbox.ps1` at logon. **Edit the `<HostFolder>`
  path first** (marked `__EDIT_ME__`): Windows Sandbox needs an absolute host
  path and does not expand env vars, so it cannot ship portable.
- `verify-in-sandbox.ps1` -- the in-sandbox driver: install -> drop a throwaway
  key -> launch -> poll `thoughtborne.log` for `All hotkeys registered
  successfully` -> copy logs out -> write a `RESULT.txt` verdict.

## The throwaway API key (required)

Drop a file named **`temp.env`** in this folder before running, holding one
working key line, e.g. `SONIOX_API_KEY=...` or `GROQ_API_KEY=...`. Without it the
harness reports `SKIP`: on a keyless start the tool opens the #144 onboarding
wizard and exits **before** registering hotkeys, so the "hotkeys registered"
assertion could never fire. **Never commit `temp.env`** -- it is a real key.
The repo `.gitignore` excludes it (and the per-run `out-<timestamp>/` folders, and
the throwaway `setup.ps1` / `setup.bat` copies below); keep it out of any commit
regardless.

## Run it

1. Edit `<HostFolder>` in `thoughtborne-install-test.wsb` to this folder's
   absolute path on your machine.
2. Put a `temp.env` here (see above).
3. For a `local` run (the default), copy the installer into this folder first:
   `setup.ps1` from the repo root is **required** -- the mapped folder is all the
   sandbox sees, and the driver runs the `setup.ps1` it finds here (copy `setup.bat`
   too if you want to exercise the double-click wrapper). Both are gitignored here
   as throwaway copies; the canonical ones live in the repo root. An `oneliner` run
   skips this step -- it fetches the published `setup.ps1` from the release URL.
4. Double-click `thoughtborne-install-test.wsb`. The sandbox boots, runs the
   driver, and writes results into a new `out-<timestamp>\` folder here
   (`RESULT.txt` plus the captured `thoughtborne.log`).

Expected during a successful run: after `uv sync`, `setup.ps1` creates the two
Start-menu shortcuts and **auto-launches the #144 settings wizard** (same handoff
as a keyless start). That is expected -- the tool still registers its hotkeys, so
the "hotkeys registered" assertion holds -- but any screenshot capture must time
around the wizard window being on screen.

## Modes

`verify-in-sandbox.ps1 -Mode`:

- `local` (default) -- runs the `setup.ps1` copied in via the mapped folder. Good
  for testing a work-in-progress script offline. **Caveat:** `setup.ps1` still
  fetches the code ZIP from the release URL, so even `local` mode needs the
  published `thoughtborne.zip` to finish the copy step; before then it exercises
  the preamble, guards, and uv bootstrap only.
- `oneliner` -- fetches and runs the *published* `setup.ps1` from the release
  `latest/download` URL: the real end-user path. Needs a published release.

## What it does not cover (yet)

The `TODO` markers: a dependency-free screenshot capture, the real launch/poll
timing under a first-run `uv sync` (which may download a ~22 MB Python), and
firing the `Ctrl+Alt+Ü` self-test from automation. Settle these on the Win11 Pro
box; they are why this ships as a skeleton rather than a green check.
