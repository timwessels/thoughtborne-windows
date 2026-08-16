# Decisions

A short log of deliberate, non-obvious product decisions — the kind that are easy
to "fix" straight into a regression later. Each entry says what was decided, why,
and what *not* to do, so a future change meets the reasoning at the point of edit
instead of re-deriving it.

**How to use.** Read this before discussing or specifying any behavior change —
issue text included. An issue that touches a recorded decision cites it
("respects D-001" or "proposes superseding D-001"). Superseding an entry needs
the maintainer's okay; when it happens, mark the old entry "Superseded by D-NNN"
rather than deleting it. Keep this file small — only genuinely contestable calls
belong here, not every design detail.

---

## D-001 — Untranscribed-recording recovery: remind once, keep it retryable

Decided 2026-07-18 (#134, #133).

A recording that was saved but never successfully transcribed — a clean-exit
salvage, an in-session failure, a hard-kill recovery, or a device-loss salvage —
is offered for retry via `Ctrl+Alt+R`. That offer behaves deliberately as
follows:

- **Announce once.** The RECOVERED panel appears on the *first* start after the
  failure, then never again for that recording. Later starts still arm the
  `Ctrl+Alt+R` slot from the persistent marker, silently. Declining to recover is
  a valid choice; the tool must not nag.
- **Persistent, single slot.** Exactly the *newest* untranscribed recording is
  retryable, and it stays retryable across any number of restarts until it is
  retried successfully or a newer failure supersedes it (persistence is #114,
  single-slot is #24/#114). A new failure — including a failed retry that hits a
  transport/API error — resets the announcement to one more panel.
- **Singular wording.** The panel always says "a recording was …", never a count,
  even when several markers exist internally (#126).
- **Audio is never deleted.** Every recording stays in `history/audio/` whatever
  the marker state, so it can always be transcribed by hand.
- **Empty is a final verdict — where it can be told apart.** On the Soniox Live
  path, whose fallback chain reports whether any stage errored, an empty
  transcript from an all-clean run means the recording holds no speech and a
  retry cannot help: the marker is deleted, the audio kept, and the console says
  so honestly ("no speech found …") instead of a generic FAILED + retry hint
  (#133). A transport/API failure is the opposite — the marker is kept and stays
  retryable; and a file-based engine (the Soniox upload or a Groq slot, via
  `Ctrl+Alt+L`), which swallows transport errors and returns empty either way,
  stays on that cautious retryable side too, since its silence and its outage are
  indistinguishable. The same honest verdict applies on the in-session attempt on
  that path, so a genuinely silent dictation on Soniox Live writes no marker in
  the first place.
- **Deliberately simple.** One "announced" bit per marker (carried in the marker
  name as a `_seen` token), no count, no multi-recording bookkeeping, no states
  beyond that. Keeping this lean is the feature, per VISION.md principle #1
  (stability).
- **2026-07-22 addendum (#138/#159).** The "empty is a final verdict — where it
  can be told apart" clause now reaches every engine, not just Soniox Live. Since
  #138 each engine reports a per-call error signal through the `_ErrorTag` sink
  (an `errored` flag plus a coarse `reason`: auth / no-connection / rate-limited /
  service-error), so a clean-but-empty run is told apart from a transport/API
  outage on the Soniox upload slot and the Groq slots too. The verdict follows: a
  selected engine that runs clean and returns zero chars earns the honest
  NO SPEECH verdict (no marker in-session / marker deleted on retry); an engine
  that errored stays the cautious FAILED + retryable path, now naming the reason
  (#159). This is a **single-engine** verdict — the selected engine speaks for
  itself; the earlier cross-provider confirmation chain was dropped 2026-07-22,
  because a wrong single-engine verdict costs only the auto-retry offer (audio is
  never deleted). A Groq clean-empty earns the full verdict like any other engine
  (Whisper hallucinates on silence rather than returning empty, so an empty Groq
  result is a sound silence signal when it occurs). The Soniox Live lane is
  unchanged: its internal duration-gated V2→V4 file lane still runs on the
  archived file and its aggregate signal feeds the verdict as before. Maintainer
  approved 2026-07-21 (widening) and 2026-07-22 (chain-less). Not a supersede —
  this extends D-001's own clause.
- **2026-08-15 addendum (#179).** A fifth reason category, `no-credit` (an HTTP
  402 — an unfunded account — told apart from the generic `service-error`), joins
  the four above so the FAILED panel names the real cause and points at the
  top-up. Like `auth`, a 402 is a conclusive verdict (a blind retry can't help
  until the balance is topped up), so it is excluded from the `error_inconclusive`
  presentation flag alongside `auth`. Respects D-001 — a 402 is still an *errored*
  call, so the recording stays on the cautious, retryable FAILED side and is never
  turned into a NO SPEECH verdict. Not a supersede; extends the addendum's own
  reason enumeration.

Do not reintroduce: a per-start nag, a pending count in the panel, a
consume-on-read marker (breaks cross-restart retry), or any automatic deletion of
recovered audio.

---

## D-002 — Settings app: how it writes config, and when the tool sees changes

Decided 2026-07-21 (#144).

The graphical settings/onboarding app is the primary editor for `.env` and
`personal_settings.json`, and `settings_io.py` is the only code that writes them. The
write contract:

- **Surgical merge, never a full rewrite.** `personal_settings.json` is parsed and
  only the app-managed blocks (`hotkeys`, `defaults`, the GUI-only `ui`) are replaced;
  every other block and every `_`-prefixed key — `_comment`s included, even inside
  managed blocks — is preserved. `.env` is edited line-wise: the managed keys are
  updated in place, all other lines/comments/order kept; an empty field is omitted, so
  a blank never clobbers a stored key.
- **Abort rather than clobber.** A present-but-unreadable target (locked,
  permission-denied) or one that is not UTF-8-decodable (an ANSI-encoded file whose
  vocabulary is intact, just wrongly encoded) aborts the save with an error —
  recoverable user data is never overwritten. Only a file whose bytes read fine but
  whose JSON is corrupt takes the overwrite path, and only after the app has warned.
- **Diff against the shipped defaults.** Hotkeys are written as a #55 partial
  override — only actions that differ from `DEFAULT_HOTKEYS`; `defaults.api` only when
  it differs from the built-in default (D-008 narrows *when* that diff runs, without
  changing it: only for an engine actively selected in the app — an untouched engine
  field leaves `defaults.api` exactly as found; the `defaults.api` half of this diff
  was then dropped 2026-08-16 (#198) for the settings app — see the addendum below —
  while the hotkeys diff is unchanged). A user on the default scheme leaves no frozen
  copy behind, so a future change to the shipped defaults still reaches them.
- **Never seed the example verbatim.** A first write with no existing file produces a
  minimal file with only the managed blocks (carrying the example's `_comment` leads);
  it must NOT copy the example's placeholder `vocabulary` — those dummy terms would
  become live Soniox vocabulary.
- **A GUI-only `ui` block.** The app persists its own display language as
  `ui.language`; the dictation tool ignores the block entirely.
- **No live reload in v1.** Changes are picked up on the tool's *next* start. The
  settings app and the running tool do not coordinate; writing while the tool runs is
  safe (next-start pickup), no file lock.
- **Guidance, not takeover, for external files.** The Windows Terminal tray toggles
  (#143) are explained and pointed to, never written by the app (that file is
  Terminal's own, JSONC, and global to every Terminal window).

**2026-08-16 addendum (#198).** The `defaults.api` diff-against-the-shipped-default
rule is **dropped for the settings app**. Its two-mode engine control (see the D-008
addendum) writes a fixed-mode pin **verbatim, including when it equals the built-in
default**, because "always start with X" is precisely the frozen copy the diff rule
avoided — and here that frozen copy is the user's intent, not an accident. The
don't-freeze-the-default benefit is preserved structurally, not by value comparison:
a user content with the default now lives in **remember-mode**, which writes no pin at
all. To express this, `settings_io.write_personal_settings` gains a three-valued
`default_api` contract — `None` leaves the pin exactly as found (an untouched save), a
distinct `REMOVE_API_PIN` sentinel force-drops it (remember-mode chosen over a pin),
and an engine id writes it verbatim. The **hotkeys** diff rule is untouched, and
freezing the default hotkeys stays forbidden; only the `defaults.api` value comparison
goes away, and only for this surface. Not a supersede — D-002's other guarantees
stand. Maintainer approved 2026-08-15 (#198).

**2026-08-16 addendum (#202).** The "no coordination" clause above is **narrowed, not
lifted.** #202 adds a single one-shot restart handshake: when settings are saved while
the tool is running, the settings app writes a signal file that the tool polls about
once a second and answers by running its regular clean shutdown (the D-001 salvage path
included), after which the app relaunches it. The app learns the tool is up by probing
its D-004 mutex **read-only** and waits for that mutex to release before relaunching —
it never kills the process. Crucially the **pickup stays start-based**: the tool still
reads changed config only at startup, exactly as before; #202 merely *performs* that
start conveniently, so this is not a live reload and the tool never re-reads config in
place. `settings_io.py` remains the only writer of `.env` and `personal_settings.json`;
the restart-signal file is machine-written handshake state (like `runtime_state.json`),
not config. So "the settings app and the running tool do not coordinate" now reads as
"…do not coordinate on config — only through this one-shot restart handshake." Not a
supersede — D-002's write contract and all its other guarantees stand.
Maintainer-settled via issue #202.

Do not reintroduce: a full-file rewrite that drops user comments or unmanaged blocks;
a save that silently overwrites an unreadable or undecodable settings file; freezing
the default hotkeys into the file; seeding the placeholder vocabulary; or a silent
write to Windows Terminal's `settings.json`.

Does not touch D-001.

---

## D-003 — Typed inserts are capped at 4,000 characters, not repaired

Decided 2026-07-22 (#7, spike #161).

The typing insert path (`keyboard.write()` / Win32 `SendInput`) silently loses most of a
long transcript: past an app-dependent break point the target app's input queue
overflows and drops the surplus keystrokes in order, while `SendInput` reports full
success (its return value only covers injection, not the later drop in the target). It is
a general Windows behavior — independently reproduced by Microsoft's own tooling, the
AutoHotkey community, and MS Q&A, repeatedly for Notepad — not a bug we can cheaply fix:
the break point moves with the receiving app's drain rate, so a robust repair would mean
per-app tuning or typing slowly enough that a long insert takes minutes. The decision:

- **Cap, don't repair.** Typed inserts are capped at **4,000 characters** — below the
  lowest break seen on the maintainer's machine (a 5,897-char insert landed whole; the
  break was 6,292 in Notepad, the most overflow-prone common target), ~32% margin. It also
  lands on the documented absolute registry minimum for the message queue, a convergent
  extra floor even in the pathological case. About seven minutes of continuous dictation
  into a paste-hostile field; in months of heavy daily use no typed insert above this was
  ever needed. The constant is trivially adjustable and lives in `typed_cap.py`.
- **No chunking/pacing.** With the cap below the real break point nothing overflows under
  it, so pacing would be dead weight; any chunk delay would have to guess the app's unknown
  drain rate — exactly the fragile repair rejected here. (Spike #161, full model in
  `_research/2026-07_typed-insert-drops/`.)
- **Nothing is lost.** The full transcript always stays in `history/` and is re-insertable
  in one piece via the clipboard hotkey; on truncation a short bracketed ASCII notice is
  appended to the typed text (no newline — a newline would arrive as Enter and could submit
  a single-line form field), and a calm yellow CAPPED strip explains it on the console (a
  success, never a red error).
- **All three routes covered.** The stop-hotkey typing path, the self-test, and the
  clipboard path's paste-failure fallback to `keyboard.write()` all go through the one cap
  helper `cap_typed_text()`.
- **No auto-switch to clipboard above the cap.** Typing is *chosen* for paste-hostile
  targets; silently switching to clipboard would fail in exactly the cases this path exists
  for. A visible cap keeps the user in control.

Do not reintroduce: an uncapped `keyboard.write()` on any typed route; chunking/pacing to
"fix" the overflow; a silent auto-switch to clipboard above the threshold; a newline in the
appended notice; or treating a truncation as a red FAILED (it is a successful, capped
insert).

Does not touch D-001 or D-002.


## D-004 — A second instance refuses rather than running deaf

Decided 2026-07-26 (#166, #165).

Global hotkeys are exclusive in Windows, so only one Thoughtborne can hold them. A
second start — most often an elevated one launched to dictate into an admin window
while a normal one is already up — used to run anyway, deaf: it registered zero
hotkeys, printed a red wall of failures, then showed READY as if fine, and its
`Ctrl+Alt+4` was answered by the first instance (so the user killed the working one).
With push-to-talk on it could also open the microphone, two processes fighting for the
mic and the caret. The decision:

- **Refuse, don't run deaf.** A single-instance guard — a named Windows mutex checked
  at the very top of `main()`, before any migration, recording loop, or hotkey
  registration — detects an existing instance and makes the second start show a calm,
  non-red notice for a few seconds and exit on its own (exit code 0, so the launcher
  window closes itself). No zombie, no push-to-talk double-recording, no `Ctrl+Alt+4`
  trap.
- **The name is fixed, not path-derived**, and the mutex carries a permissive security
  descriptor; an elevation mismatch (`ERROR_ACCESS_DENIED`) counts as "already running"
  exactly like `ERROR_ALREADY_EXISTS`. A second copy on disk, elevated or not, is still
  a second instance. The name is session-scoped (no `Global\` prefix), matching the
  session scope of global hotkeys, so two Windows users each keep their own instance.
- **The OS owns the lock's lifetime.** The kernel releases the mutex when the process
  dies, including a hard kill, so a crash never leaves a stale lock — the next start is
  an ordinary single instance.
- **A documented opt-out** (`THOUGHTBORNE_ALLOW_SECOND_INSTANCE`) lets a developer run a
  second copy for non-hotkey work; the default is guard-on.
- **The trade-off, accepted honestly.** A first instance that is wedged (not answering
  `Ctrl+Alt+4`) blocks every new start until it is ended — so the notice tells the user
  they can end that window in Task Manager. Locking out a live, hotkey-holding instance
  is the right default; the escape hatch is naming the wedge case in the notice.
- **Honest registration verdict, independent of the guard.** `_register_hotkeys()`
  reports success only when every hotkey registered; a shortfall no longer logs "All
  hotkeys registered successfully" nor shows READY, and a genuine partial loss (a
  foreign app owning one combo) gets a yellow panel worded for a partial, not a total,
  loss. The pre-existing second-instance defences (the sidecar lock, the migration-race
  guard, the recovery probe) stay — after a crash the mutex is free and they remain
  load-bearing. Redundant is not dead.

Do not reintroduce: a path-derived mutex name; treating only `ERROR_ALREADY_EXISTS` as
"already running" (the elevation case returns `ERROR_ACCESS_DENIED`); closing the mutex
handle before process exit; an unconditional "All hotkeys registered successfully" line
or a READY masthead on a registration shortfall; a second instance that runs on without
hotkeys.

Respects D-001 — the guard runs before startup recovery, so the surviving instance's
remind-once/retryable marker behaviour is unchanged; only the duplicate arming by a
second process disappears. Does not touch D-002 or D-003.

---

## D-005 — Settings-app launcher: venv-first (probed), system Python is the rescue lane

Decided 2026-07-29 (#171).

`Thoughtborne-Settings.bat` selects a Python interpreter in three ordered stages,
and the order is deliberate:

- **Project venv first, health-probed.** When `.venv\Scripts\pythonw.exe` exists,
  the launcher confirms the venv actually works — `.venv\Scripts\python.exe -c
  "import tkinter"`, run console-inheriting (~0.1 s, no extra window, output
  silenced) — before detaching the windowed app via the venv `pythonw.exe`. This
  is the same interpreter the tool itself runs on (`Thoughtborne.bat` -> `uv run`),
  so the settings app and the tool never diverge on Python version. The probe is
  what makes venv-first strictly better than the old system-Python-first order: a
  present-but-broken venv (base interpreter removed by `uv cache clean` / `uv
  python uninstall`) fails the probe and falls through, instead of a detached
  `pythonw` dying invisibly with no way to report the error.
- **System Python is the rescue lane.** With no healthy venv, a real system
  `pythonw`/`python` on PATH (WindowsApps store stubs filtered out) runs the app.
  This lane works *only because the app is pure stdlib* — no venv, no uv, no
  third-party packages required (the one `dotenv` import in `config.py` is
  try/except-guarded, and `key_check.py` uses `urllib` on purpose). Keeping the
  settings-app import chain stdlib-only is therefore a load-bearing constraint of
  this decision, not an incidental property.
- **uv bootstrap last.** With no system Python either, `uv run pythonw
  thoughtborne_settings.py` (uv on PATH, then `%USERPROFILE%\.local\bin\uv.exe`)
  creates the venv on the spot. This is the git-clone cold-start case; plain
  `uv run` with its sync is correct here — syncing is the point of this stage.

Probe depth is the plain `import tkinter`, not a `Tk()` construction. The import
proves the interpreter launches and tkinter loads — covering the realistic
breakages (deleted base interpreter, half-built venv). The historic
`init.tcl`-not-found class only surfaces at `Tk()` construction; a probe deep
enough to catch it would create and destroy a real window on every launch, a
flicker risk borne by every user forever to catch a case that is rare on the
shipped uv-managed CPython. The minimal probe is the accepted floor; the residual
gap is documented, not silent.

Deliberate non-decision: **no committed `.python-version` pin.** `uv.lock` already
pins identical package versions across Python 3.10–3.13, so a pin buys no
reproducibility; it would instead force an interpreter download on machines that
already have a perfectly suitable Python. The one known-bad interpreter version is
excluded surgically in `requires-python` (companion issue #172, commit 94e5cf5),
not by pinning a single good one.

`Thoughtborne.bat` is intentionally *not* changed to match: it stays on full
`uv run thoughtborne.py`, because sync-on-start is the tool's update mechanism
after a `git pull` (the settings app has no dependencies to sync). The first-run
hook in `thoughtborne.py` already launches the app on the venv interpreter (via
`sys.executable`'s sibling `pythonw`), so all four launch routes — double-click
`.bat`, both Start-menu shortcuts, the setup.ps1 handoff, and the in-tool
first-run hook — reach the venv interpreter when a healthy venv exists.

Do not reintroduce: system-Python-first ordering for the settings launcher; an
unprobed venv launch (a broken venv would die invisibly under detached `pythonw`);
dropping the WindowsApps-stub filter from the rescue lane; a third-party import in
the settings-app chain that would break the stdlib-only rescue lane; or a committed
`.python-version` pin.

Respects D-002 — this changes only which interpreter runs the settings app, not
how or when the app writes `.env` / `personal_settings.json`. Does not touch D-001,
D-003, or D-004.

---

## D-006 — Release assets: two fixed-name files, the ZIP is `git archive` of the tag

Decided 2026-07-29 (#145).

A tagged GitHub release is the installer's source of truth (#76): `setup.ps1`
fetches from `releases/latest/download/<asset>`, a stable alias GitHub resolves to
the newest non-prerelease. That coupling fixes an asset contract:

- **Exactly two assets, fixed names.** Every release carries `thoughtborne.zip`
  (the code) and `setup.ps1` (the standalone installer). The names are wired into
  `setup.ps1` (the `latest/download/thoughtborne.zip` and versioned
  `releases/download/$version/thoughtborne.zip` URLs) and the sandbox harness —
  renaming one silently 404s the installer at runtime.
- **The ZIP is `git archive` of the tag — whole tree, flat, fixed name.** It ships
  the entire tracked tree at the tagged commit, built with `git archive` (not a
  filesystem zip), with no wrapper directory and no version stamp in the name.
  `git archive` is the only builder that applies `.gitattributes`; a `zip -r` over
  a working tree would ship LF `.bat` files and cmd.exe would mis-parse the
  launcher labels. Whole-tree (no `export-ignore` trimming) so a later "shrink the
  ZIP" cannot silently drop a load-bearing file — the size cost (~650 KB) is
  negligible and the dev-only files sit inertly in the install dir.
- **The `setup.ps1` asset comes from the same tag.** The standalone `setup.ps1` is
  taken from the tagged commit (`git show vX.Y.Z:setup.ps1`), so the one-liner
  lane's script and the copy inside the ZIP are byte-identical and can never drift
  (#157).
- **Published as Latest, never a pre-release.** `releases/latest/download/`
  resolves only to the newest non-prerelease; a pre-release publish would leave the
  installer's fetch URL on the previous release and 404.

The ritual that produces all this is `RELEASING.md`; `build-release-zip.sh` builds
and dry-run verifies the assets without tagging or publishing.

Do not reintroduce: renaming either asset; a version-stamped ZIP name; trimming the
ZIP via `.gitattributes export-ignore`; a filesystem zip that loses the `.bat`
CRLF; or publishing the release as a pre-release (breaks `latest/download`).

Respects D-002 — `setup.ps1` still collects no secrets and writes no config (the
settings app remains the only config writer). Does not touch D-001, D-003, D-004,
or D-005.

---

## D-007 — In-place update never overwrites the running `setup.bat`

Decided 2026-07-29 (#157).

The installer's paste-free **in-place update lane** runs the local `setup.bat`
from the install dir, which launches `setup.ps1` via `%~dp0setup.ps1`. cmd.exe
streams a batch file by byte offset *at runtime* — it re-reads the next line from
disk after each command — so replacing the `setup.bat` it is still executing
misparses the file's tail the moment those bytes ever differ from the on-disk
copy (a wrong/garbled exit code, a skipped failure-pause, a syntax error). The
decision:

- **Skip `setup.bat` from the copy on the in-place lane, and only there.**
  `setup.ps1` excludes `setup.bat` from the tree copy exactly when the copy target
  is its own folder, detected as `$PSScriptRoot == $installDir` (compared as
  normalized directories, case-insensitive). That equality *is* the danger
  condition: the running wrapper lives in `$PSScriptRoot`, and the copy overwrites
  `$installDir`. A fresh ZIP install (`$PSScriptRoot` = unpack folder ≠ install
  dir) and the `irm | iex` one-liner (`$PSScriptRoot` empty) both still ship
  `setup.bat` — correct, there is no running wrapper to protect.
- **`setup.ps1` keeps updating.** It is preparsed by the `-File` lane (the whole
  AST is in memory before the first line runs), so overwriting it mid-run is
  harmless. Only the thin wrapper is held back — the code and `setup.ps1` refresh
  as before.
- **The accepted trade.** A change to the near-frozen `setup.bat` reaches an
  existing install on a fresh (re-)install (One-liner/ZIP), not via an in-place
  update. That is the normal fresh-install path, not an extra user step. The
  wrapper contract (`powershell -File setup.ps1 %*` + `THOUGHTBORNE_FROM_BAT`) is
  stable and test-guarded, so an old wrapper drives a newer `setup.ps1` fine.
- **Mechanical, not a promise.** The safety is guaranteed in code, so the wrapper
  may still evolve; a future wrapper change simply does not propagate in-place
  instead of silently re-arming the mis-parse.

Do not reintroduce: copying `setup.bat` over itself on the in-place lane; freezing
`setup.ps1` too; or a path-derived install-dir assumption (the lane is detected by
directory equality, not by deriving `$installDir` from `$PSScriptRoot`).

Respects D-006 — the release ZIP still carries `setup.bat` (whole-tree `git
archive`); the exclusion is a runtime copy-target choice, not an asset change.
Does not touch D-001 through D-005.

---

## D-008 — Startup engine: an explicit `defaults.api` outranks the remembered one

Decided 2026-08-15 (#193).

Thoughtborne records the engine you last selected with `Ctrl+Alt+L` and opens on
it the next time it starts — but only where nothing is configured:

- **Config wins over the memory.** A valid `defaults.api` is deliberate, durable,
  and set through a visible control (the settings app's engine field, the
  documented `personal_settings.json` block); the memory is implicit, invisible,
  and recorded from a key the docs frame as the route-around-an-outage escape
  hatch. Config-wins still solves both cases #193 names — those users have no
  `defaults.api` at all — and it makes the feature inert for every install that
  configured one.
- **"Configured" means present and valid, not different.** `config.DEFAULT_API_IS_EXPLICIT`
  is set when the override is accepted, so a hand-written `"api": "soniox-live"`
  is honored as the explicit pin it is. An *invalid* value warns (as before) and
  leaves the flag false, so the memory applies — both outcomes are "not what you
  typed", and this is the friendlier one.
- **"Configured" also means *in `personal_settings.json`*.** A `DEFAULT_API`
  edited directly in `config.py` does not set the flag, so it loses to a
  remembered engine and takes effect only when nothing is remembered. That
  follows from the rule above rather than contradicting it: the only signal a
  hand-edited constant could give is "differs from the built-in value", and
  difference is exactly what this decision refuses to read as intent — a pin *on*
  the built-in default must count. `defaults.api` is the documented, detectable
  control, so the docs (README, `README.de`, `llms-install.md`) point there for a
  startup engine that outranks the memory and mention the constant as the weaker
  alternative.
- **Only a real switch is recorded, and it is recorded always.** A successful
  `Ctrl+Alt+L` writes the file; the startup carousel's fall-through never does —
  it is an outage, not a choice, and persisting it would make the memory
  self-reinforcing (one Soniox outage and you are on Groq forever). The write
  happens even while a pin outranks it, so the file stays truthful and stays warm
  if the pin is ever removed. The asymmetry that follows is deliberate: pressing
  `Ctrl+Alt+L` *because* an engine is down still records the pick — a keypress is
  intent, and the tool cannot tell "I want Groq now" from "I want Groq from now
  on" — while the automatic fall-through, which the user never asked for, does not.
- **A separate, tool-written state file.** `runtime_state.json` (gitignored,
  beside `.env` and the log) keeps machine-written state apart from
  user-authored config; reads and writes are best-effort, and a missing, corrupt,
  or stale value falls back silently to the normal default chain. Both surfaces —
  the running tool and the settings app — write it without coordinating: the write
  is atomic, it carries one value, and the last writer wins, which is the honest
  outcome when two windows disagree about the engine. Consistent with D-002's
  no-coordination stance on the two apps sharing a folder.
- **The settings app shows the effective engine, and only a *selection* changes
  the file.** Its engine field would otherwise read `defaults.api` alone and so
  name an engine the tool will not start on whenever a memory exists. It therefore
  displays the pin, else the remembered engine, else the built-in default — and
  distinguishes displaying from selecting.
  **Displaying:** a save in which no engine was selected passes `default_api=None`
  to `settings_io.write_personal_settings`, which now means *leave `defaults.api`
  exactly as found* — not "write the loaded value back". That widened contract is
  a data-safety rule, not a nicety: D-002's diff rule drops any value equal to
  the built-in default, so re-writing an untouched field would silently delete a
  hand-written `"api": "soniox-live"` pin on a save about hotkeys, and with a
  memory present that flips the next start. Leaving the key alone likewise
  preserves an *invalid* hand-typed value; the tool warns about it at every start,
  which is the honest way to surface a typo, whereas deleting what the user wrote
  on an unrelated save is not.
  **Selecting:** an engine actually selected in this session — by the user, or by
  #178's key-driven preselect in the first-run wizard, which selects visibly on
  their behalf — is written as before *and* recorded as the last selected engine.
  That second write is what makes picking the built-in default take effect at all,
  since the diff rule drops a pin equal to it; the two surfaces always agree
  afterwards.
- **`(default)` in the console lineup names the *configured* default, not the next
  start.** *(Retired by the 2026-08-16 #200 addendum below — the marker is removed;
  a greyed key-aware row is the lineup's only per-engine signal now.)* The marker
  stayed keyed on `DEFAULT_API` — the engine the tool falls back to — while the `>`
  row names the engine currently active, which after any switch is also the one the
  next start will use. Two markers, two different facts; a third one for "remembered"
  would crowd the lineup to repeat what the `>` row already shows.
- **2026-08-16 addendum (#198).** The settings app's engine field is now an explicit
  **two-mode control** rather than a single dropdown whose active mode was invisible:
  *(•) start with the engine I last switched to* (remember-mode — the memory, shown
  read-only) versus *( ) always start with:* one fixed engine (a `defaults.api` pin).
  This sharpens *Displaying vs. Selecting* above:
  - **A fixed pick writes the pin and no longer writes the memory.** The written pin
    is what makes the choice take effect — including on the built-in default, which the
    settings app now writes **verbatim** (the D-002 addendum drops the
    diff-against-the-default gate for this surface). Stamping the memory on a pin would
    overwrite the user's last real `Ctrl+Alt+L` switch and corrupt the memory's
    self-definition, so it is not done; the memory stays warm and truthful for if the
    pin is later removed.
  - **Remember-mode chosen over a pin removes the pin** (via the `REMOVE_API_PIN`
    signal) and never touches the memory — the untouched memory keeps deciding.
  - **The app's only memory write is the #178 wizard preselect** in remember-mode: a
    fresh user defaults to remember-mode, and a key-driven preselect that actually
    moves the remembered engine (the Groq-only case) records it as the *memory* rather
    than a pin, keeping the newcomer in the #193 zero-config world while preserving
    #178's no-visible-skip first start. This changes #178's earlier pin+memory write
    to a memory-only write (CHANGELOG).
  The on-save signal derivation is extracted to a pure, off-Windows-tested
  `settings_io.resolve_engine_save_signal(...)`. Precedence (config > memory > default)
  and the leave-as-found (`default_api=None`) data-safety contract are **extended, not
  weakened**; the "writing an untouched engine field back to the file" rule still holds
  (untouched → `None`). Not a supersede. Maintainer approved 2026-08-15 (#198).
- **2026-08-16 addendum (#200).** The console lineup is now **key-aware**, and two
  display artifacts this decision described are removed. A lineup row whose key env
  var is absent renders **dim**, so the greyed rows show at a glance which engines are
  usable. With that signal in place, the dim **`(default)` marker** is dropped from
  both lineup render sites, and the startup **fallback NOTE** is dropped from the
  masthead: a start that falls through because the resolved engine is keyless now
  starts silently on the first available engine in lineup order, its story told by the
  greyed row alone (a `FILE_ONLY` log line stays for debugging). A fully keyless start
  no longer exits to the wizard — it stays open as a **shop window** (the masthead with
  every row dim plus a yellow "enter a key in Settings" line) while still auto-launching
  the wizard. This changes only what the console *shows*: startup precedence
  (config > memory > default), the recording behaviour, and the memory-write rules are
  all untouched, so #200 **respects** D-008 rather than superseding it. The
  "`(default)` names the configured default" bullet above is retired with the marker;
  the "do not reintroduce a 'default'-worded fallback note" rule stands (the note is now
  removed, not reworded). Maintainer-settled via the #200 spec. Not a supersede.

Do not reintroduce: writing `personal_settings.json` from the running tool;
persisting the carousel's fall-through engine; a "default"-worded fallback note on
a start that never tried the default; a value-comparison test for "is a default
configured"; writing an untouched engine field back to the file (it creates or normalizes a pin
the user never set); the `(default)` lineup marker or the startup fallback NOTE
(removed by #200); a fully keyless start that exits instead of staying open as a
shop window;
or a settings-app engine field that shows `defaults.api` alone.

Respects D-002 — `settings_io.py` remains the only writer of `.env` and
`personal_settings.json`; the settings app's new coupling to the state file is
read-plus-record-on-pick through `engine_memory`, and it never clears the file.
Does not touch D-001 or D-003 through D-007; the settings app's import chain stays
stdlib-only, as D-005 requires.

---

## D-009 — Settings app: one window, focus don't refuse; ignore extends to pending inserts

Decided 2026-08-15 (#195, #196).

The graphical settings app gets its own single-instance guard, and the running
tool's `Ctrl+Alt+G` ignore behaviour is widened and made visible. The decisions:

- **One window, enforced by a *distinct* named mutex.** The app checks a named
  Windows mutex, `Thoughtborne-Settings-SingleInstance`, at the very top of `main()`
  before `tk.Tk()` (new stdlib module `settings_instance.py`). The name is
  deliberately different from the tool's `Thoughtborne-SingleInstance` (D-004): a
  shared name would make a running tool block every settings launch and vice versa.
  The mechanics are D-004's — permissive security descriptor, session-scoped (no
  `Global\`), `ERROR_ACCESS_DENIED` counted as "already running" alongside
  `ERROR_ALREADY_EXISTS` so an elevated and a normal instance recognise each other,
  and the handle held for the whole process so the kernel frees it on any exit.
- **Focus, don't refuse — a GUI's remedy differs from the tool's.** Where a second
  tool instance shows the calm ALREADY-RUNNING notice and exits (D-004), a second
  settings instance instead *focuses the existing window* (`EnumWindows` + exact
  title match + `ShowWindow(SW_RESTORE)` + `BringWindowToTop` + `SetForegroundWindow`,
  `AttachThreadInput` as the documented fallback) and exits 0 silently. Bringing the
  window to the front IS the feedback; a notice would be noise. The title match is
  exact against the four known localized titles (settings/first-run × DE/EN, computed
  from `settings_strings` so it can't drift) — a bare "Thoughtborne" prefix is refused
  because it would also match the tool's console window. To keep that match findable,
  the window is titled early in `SettingsApp.__init__` — before the slow `_build_ui`
  growth (#178/#180), though after `tk.Tk()`, `_size_window`, `read_env` and the
  `__init__` preamble, so the untitled `"tk"` window lives for those first milliseconds
  to tens of milliseconds. That span is harmless anyway: a not-yet-mapped window is
  filtered out of the focus enumeration by `IsWindowVisible`, and the early title is
  what actually guards the realistic race — a fast repeat-press during the construction
  growth. Focus is
  best-effort: an unfindable window (a near-simultaneous double start whose first
  window is not yet titled) or a cross-integrity UIPI block just means the second
  instance exits without raising — the "at most one window" guarantee still holds.
- **The in-app guard covers both spawn paths.** `Ctrl+Alt+G` / `--first-run` (via
  `_launch_settings_app`) and `Thoughtborne-Settings.bat` (double-click / Start menu /
  setup.ps1 hand-off) both reach the same guard. No spawner-side dedupe in the tool:
  every launch either becomes the one window or focuses it, which is exactly the
  "repeat press raises the window" behaviour wanted; deduping in the tool would
  suppress the raise. A tool-side fast-focus that skips the spawn on a repeat press
  (so a cold pythonw start isn't paid just to focus-and-exit) was deliberately *not*
  built: it is an optimisation against the #195 latency, which is to be measured
  first — the in-app mutex is the guarantee regardless.
- **No silent swallow, and a wider ignore window.** An ignored `Ctrl+Alt+G` press now
  prints a calm console line (INFO), not just a DEBUG entry the INFO-pinned console
  never shows. And the ignore condition widens from `is_recording` to *recording or a
  pending insertion* (`processing_counter > 0`, or the output manager reports a
  queued-and-complete or in-flight insert via `has_pending_output()`): a press in the
  seconds between the stop key and the paste could otherwise open a window and steal
  the insertion's focus target. The response is drop-with-feedback, not defer — a
  deferred auto-open must never front-run a pending insert, and feedback-only meets
  the need without that extra state (the user can simply press again once the
  dictation lands). The output manager tracks the pop→paste tail with a single
  `_inserting` flag under its queue lock, since the tool's `processing_counter` drops
  the moment a task is handed off, before the paste.
- **Startup timing is measured, not guessed.** The app records a one-line breakdown to
  `thoughtborne.log` (spawn→entry via a tool-passed wall-clock stamp, imports,
  `tk.Tk()`, construction, first map), written once at first `<Map>`, quiet and
  file-only, all in try/except, so the spawn-to-visible latency (#195) is diagnosable
  on the live machine instead of inferred. No `RotatingFileHandler` — a two-process
  rotation would race; a plain append at a rare user event is enough.

**2026-08-16 addendum (#203).** The focus-existing remedy above is **strengthened and
made observable**, and the startup instrumentation gains a second line. The
`BringWindowToTop` + `SetForegroundWindow` (+ `AttachThreadInput`) sequence described
above proved ineffective in practice (the #199/#203 forensics saw a found window stay
behind even so — a background process holds no foreground rights, and
`AttachThreadInput` does not reliably lift the UIPI/rights limit). So a **transient
topmost pulse** now runs first — `SetWindowPos` to `HWND_TOPMOST` then straight back to
`HWND_NOTOPMOST`, with `NOACTIVATE` — a cross-process Z-order raise a background process
*is* allowed without foreground rights, and the reliable "visibly on top" result; the
`SetForegroundWindow` path still follows for real keyboard focus where the rights
happen to be there. `focus_existing_settings_window` now returns a **category**
(`not-found` / `raised` / `focused` / `refused`, told apart by a `GetForegroundWindow`
re-probe) that `main()` logs as a `[SETTINGS] focus-existing:` line — so the log can no
longer confuse a real raise with a silent no-op. And the "written once at first
`<Map>`" instrumentation gains a companion `[SETTINGS] visible:` line at first
`<Expose>` (the OS paint), since `<Map>` is decoupled from the window actually becoming
visible (#180/#203). Not a supersede — the one-window guarantee, the focus-don't-refuse
principle, and the distinct mutex all stand; only the remedy mechanism and its
observability change. The transient pulse is **not** a permanent forced-topmost (it
drops the flag again right away -- best-effort, retried once -- and never activates),
so it does not reintroduce a window that stays above everything.

Do not reintroduce: sharing the tool's mutex name for the settings app; a settings
second-instance that refuses-with-notice instead of focusing; a prefix title match
that can hit the tool's console window; a deferred settings auto-open that could
front-run a pending insertion; a DEBUG-only sign for an ignored press; or a
*permanent* forced-topmost for the focus remedy (the transient pulse is deliberate).

Respects D-002 — the fix prevents a second config *editor* from existing; the
`settings_io` write contract is untouched. Respects D-004 as the mutex mechanism
precedent; the remedy (focus vs refuse) and the name deliberately differ. Respects
D-005 — the mutex/focus is stdlib `ctypes`, no third-party single-instance package,
so the settings-app import chain stays stdlib-only. Does not touch D-001, D-003,
D-006, D-007, or D-008.

---

## D-010 — Settings app leaves the native ttk theme for `clam` + an explicit style module

Decided 2026-08-16 (#155).

The settings/onboarding app used to pin the native `vista` ttk theme; the #155
visual design pass leaves it for `clam` plus one explicit style module,
`settings_theme.py`. No vendored third-party theme.

- **Why not vista.** vista draws the notebook pane and tab strip, buttons, entry
  and combobox fields, the scrollbar and the radio indicators through the Windows
  UxTheme API, which takes no colour from ttk styling. `-foreground`, `-font` and
  frame/label backgrounds still apply, but the OS-drawn chrome does not — so "white
  surfaces" under vista is a *half*-restyled window: white frames and labels around
  grey Aero-era chrome, which reads as broken rather than plain. The decisive case
  is the notebook pane, which cannot be made white, so a white body inside a grey
  pane is exactly the grey-band failure the #180 canvas comment warns about. `clam`
  is Tk-drawn top to bottom, so the design is exactly what `settings_theme.py`
  specifies — and it renders identically off-Windows under Xvfb, which for the
  first time makes the app's look verifiable without a Windows machine (the
  autonomous-verification culture the batch runs depend on).
- **Why not a vendored `.tcl` theme** (azure / forest / sun-valley). The letter of
  D-005 is not broken by a Tcl file (no Python import, no pip), but the credible
  candidates are *image* themes: their widget parts are fixed-size PNG sprites, and
  `tk scaling` scales fonts, not photos — at 125/150 % you get correctly-sized text
  inside undersized, blurry widget art, a direct collision with this app's DPI
  requirement. Plus ~1000 lines of third-party Tcl in the one window every
  first-run user passes through, found at runtime relative to the script dir, whose
  Tcl error would kill the very window the D-005 rescue lane exists to save. Our
  needs are met by ~60 `style.configure` lines.
- **Light, not dark.** `messagebox` dialogs, the window title bar and the combobox
  popdown are OS-drawn and stay light; a dark theme would guarantee a visible seam
  in exactly those surfaces we do not control. The five shipped status colours are
  tuned for light backgrounds, and the product already *has* a dark surface (the
  console), so a light settings window beside it is a distinction, not an
  inconsistency. If a dark theme is ever wanted, it is a palette swap in one module
  plus a new contrast pass — but reasons one and two do not go away.
- **One place defines the surfaces.** `settings_theme.py` is now the single source
  of the page and card surfaces. `TFrame` IS the page surface: `_scrollable_tab`
  reads `Style().lookup("TFrame", "background")` for its #180 scroll canvas, so the
  page colour and the canvas background are the same value by construction — change
  one and the other follows.

The module is stdlib-only and imports tkinter lazily (inside `apply_theme` /
`_pick_family`, like `settings_instance`'s ctypes), so its constants import even on
a Python without the tk bindings and the off-Windows `test_settings_theme.py` can
WCAG-check the palette without a display. The palette is the project's own website
palette (`docs/style.css`), so the settings window, the site and the console read
as one product.

Do not reintroduce: pinning `vista` (or any native theme) for the settings app;
styling only frames/labels while leaving OS-drawn chrome (the half-restyled
window); a vendored image-based `.tcl` theme (DPI-blurry, and a runtime-found file
whose error kills the rescue-lane window); a dark palette (it seams the OS-drawn
messagebox / title bar / popdown); or a second place that defines the page/canvas
surface apart from `settings_theme.py`.

Respects D-002 — a visual pass changes no write surface and no save semantics.
Respects D-005 — `settings_theme.py` is stdlib-only, so the system-Python rescue
lane still runs the app, and no third-party theme is vendored. Respects D-008 and
D-009 — the two-mode engine control's logic and the `app.title.*` focus-match
titles are untouched; only their container and styles change. Does not touch D-001,
D-003, D-004, D-006, or D-007.
