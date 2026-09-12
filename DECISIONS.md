# Decisions

A short log of deliberate, non-obvious product decisions — the kind that are easy
to "fix" straight into a regression later. Each entry says what was decided, why,
and what *not* to do, so a future change meets the reasoning at the point of edit
instead of re-deriving it.

**How to use.** Read this before discussing or specifying any behavior change —
issue text included. An issue that touches a recorded decision cites it
("respects D-001" or "proposes superseding D-001"). Superseding an entry needs
the maintainer's okay; when it happens, mark the old entry "Superseded by D-NNN"
rather than deleting it. Only genuinely contestable calls belong here, not every
design detail.

**Index.** The status column carries the amendment state — what a later addendum
extended, narrowed, reversed or retired. The entries themselves stay the detail.

| ID | Decision | Status |
| --- | --- | --- |
| D-001 | Untranscribed-recording recovery: remind once, keep it retryable | Active; extended 2026-07-22 (#138/#159) and 2026-08-15 (#179) |
| D-002 | Settings app: how it writes config, and when the tool sees changes | Active; the 2026-08-16 addenda drop the `defaults.api` diff for this surface (#198) and narrow the no-coordination clause (#202); extended 2026-08-25 (#233) |
| D-003 | Typed inserts are capped at 4,000 characters, not repaired | Active |
| D-004 | A second instance refuses rather than running deaf | Active; narrowed 2026-09-07 (#269) — the developer opt-out is read from the install directory's `.env` only, per D-017 |
| D-005 | Settings-app launcher: venv-first (probed), system Python is the rescue lane | Retired 2026-08-21 by D-014 (#223) with the standalone lane; the stdlib-only constraint on the settings-app import chain still holds |
| D-006 | Release assets: two fixed-name files, the ZIP is `git archive` of the tag | Active |
| D-007 | In-place update never overwrites the running `setup.bat` | Active |
| D-008 | Startup engine: an explicit `defaults.api` outranks the remembered one | Active; extended 2026-08-16 (#198); the built-in-default `(default)` marker and the fallback note retired by #200, a distinct active-pin tag added 2026-08-21 (#219); precedence untouched |
| D-009 | Settings app: one window, focus don't refuse; ignore extends to pending inserts | Active; focus remedy strengthened 2026-08-16 (#203); narrowed 2026-08-21 (#217) — the recording-active ignore is gone |
| D-010 | Settings app leaves the native ttk theme for `clam` + an explicit style module | Active, partly reversed — the light-over-dark call reversed by the 2026-08-22 addendum (#228); the clam / single-source / WCAG mechanics stand |
| D-011 | Uninstaller keeps user data by default; the silent lane can never delete it | Active |
| D-012 | The self-test default is `Ctrl+Alt+T`; the umlaut lane stays for overrides | Active; narrowed 2026-09-12 by D-023 (#317) — the override lane is removed, the static-default rule stands |
| D-013 | In-place updates are replace-only; orphaned files from an older release survive | Active |
| D-014 | Settings is part of the app: no unsaved-changes guard, one exit, one lane | Active; narrowed 2026-09-06 (#239) — the silent language write is corruption-gated; extended 2026-09-07 (#271) — every save restarts, the keyless close branch is gone. Retires D-005 |
| D-015 | The settings app defaults to English; German is an explicit opt-in | Active |
| D-016 | The Windows app icon is the pixel mark on a hard-cornered dark-grey tile | Active |
| D-017 | API keys come from the install directory's `.env` only | Active |
| D-018 | The console is built for 72 columns and up; there is no second form | Active |
| D-019 | One canonical hotkey order, and one display grammar for keys | Active; narrowed 2026-09-08 (#290) — the footer's own order moved to `console_ui` |
| D-020 | Reset to defaults: the app's own settings, never the user's data | Active |
| D-021 | A checkout names its commit; an installed copy shows the release number alone | Active; extended 2026-09-10 (#306) — a `--dev` test build's suffix travels inside the files, so an installed copy can carry one |
| D-022 | Mouse buttons are not hotkeys here; the supported route is an external remapper | Active |
| D-023 | One kind of hotkey key: the layout-resolved `ü` lane is removed | Active |

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
  archived file and its aggregate signal feeds the verdict as before. (Since
  #212 that internal V2 lane is gone — the `soniox` slot is async-only —
  which leaves the verdict rule above untouched; noted 2026-09-06.) Maintainer
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

**2026-08-25 addendum (#233).** `push_to_talk.enabled` becomes a **fourth app-managed
key**, written **only on demand** — exactly like `ui.language`, so the enumeration in
the surgical-merge bullet above gains one entry and nothing else changes.
`write_personal_settings` takes a three-valued `ptt_enabled`: `None` leaves the whole
`push_to_talk` block exactly as found (and creates none), so a save that never touched
the toggle stays byte-identical there and a hand-typed invalid `enabled` survives to be
warned about at the next start; `True`/`False` writes **only** `enabled`, preserving the
block's `_comment` and every sibling (`trigger`, `insert`, the three thresholds), so
switching the feature off and on again can never cost a hand-tuned value. Unlike
`defaults.api` there is **no removal sentinel** — there is no "delete the block" state
to express. The block is deliberately **not** added to `MANAGED_BLOCKS`, for the reason
`ui` is not: that tuple seeds a block into every absent-file write, which would drop an
unwanted `push_to_talk` block into every fresh install. The settings toggle *displays*
the file through `read_ptt_enabled`, which applies `config.py`'s JSON-boolean-only rule,
so it can never show ON for a file the tool reads as OFF. Not a supersede — D-002's
write contract and all its other guarantees stand.
Maintainer-directed via issue #233 (spec of 2026-08-25).

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
  second copy for non-hotkey work; it is read from the install directory's `.env`
  (D-017 — a shell or system variable of that name no longer counts), and the default
  is guard-on.
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

**Retired 2026-08-21 by D-014 (#223).** The standalone settings launcher is gone —
the settings window now opens only from the running tool (`Ctrl+Alt+G` / the
tool-spawned `--first-run` wizard), so there is no separate interpreter-selection
launcher to design. The rescue for a broken environment is the installer's idempotent
re-run, not a settings side door. The reasoning below is kept for the record (the
venv-first ordering, the stdlib-only rescue constraint, the no-`.python-version`
non-decision); the stdlib-only constraint on the settings-app import chain **still
holds**, because the tool spawns the app on its own venv interpreter and D-002/D-009
depend on it.

Decided 2026-07-29 (#171). *(Retired — see above.)*

The standalone settings launcher selected a Python interpreter in three ordered
stages, and the order was deliberate:

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
  third-party packages required (since #269 `config.py` has no third-party import
  at all, and `key_check.py` uses `urllib` on purpose). Keeping the
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
- **2026-08-21 addendum (#219).** A dim `(default)` tag returns to the console lineup —
  but keyed on an **active explicit pin**, not the built-in default the #200-removed
  marker showed. When a valid `defaults.api` sets a fixed-mode pin
  (`config.DEFAULT_API_IS_EXPLICIT` True), the pinned engine's **startup-masthead**
  lineup row carries a dim ` (default)` tag: the breadcrumb back to the settings app for
  a user who pinned an engine, forgot, and wonders why every start lands on it no matter
  what they switch to — the pin outranks the `Ctrl+Alt+L` memory, so a switch never
  changes the next start. Remember-mode and the built-in default show no tag — exactly
  the states where "default" would name nothing, the emptiness that retired the old
  marker. The tag is **display-only**: it reads the flag, never writes, and touches
  neither precedence (config > memory > default) nor the memory-write rules. It is dim
  regardless of the row's own weight (a keyed-current pinned row stays bold with a dim
  tag), and the #200 keyless dim **wins** — a greyed keyless row shows no tag, since an
  unusable engine does not advertise itself as the default. It appears in the startup
  masthead **only**; the SWITCHED / switch-failed panels omit it (the tag follows the
  pin, and the masthead is the startup-orientation surface). The pin reaches the pure
  renderer as a `pinned_default` label argument (default `None`), so config coupling
  stays out of `console_ui`. Not a supersede — the #200 removal of the *built-in-default*
  marker stands; only its "do not reintroduce" bullet below is narrowed to the semantics
  that were actually wrong. Maintainer-requested via the #219 spec.

Do not reintroduce: writing `personal_settings.json` from the running tool;
persisting the carousel's fall-through engine; a "default"-worded fallback note on
a start that never tried the default; a value-comparison test for "is a default
configured"; writing an untouched engine field back to the file (it creates or normalizes a pin
the user never set); the **built-in-default** `(default)` lineup marker — the one keyed
on `DEFAULT_API` and shown always, removed by #200 (the **active-pin** `(default)` tag of
#219, keyed on `DEFAULT_API_IS_EXPLICIT` and shown only in fixed mode, is deliberately
distinct and permitted) — or the startup fallback NOTE (removed by #200); a fully keyless
start that exits instead of staying open as a shop window;
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
- **The in-app guard covers the tool's spawn path.** `Ctrl+Alt+G` / `--first-run` (via
  `_launch_settings_app`) reaches the guard — since #223/D-014 the standalone launcher
  is gone, so this is the one way the window opens. No spawner-side dedupe in the tool:
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

**2026-08-21 amendment (#217).** The ignore's *recording* half is **removed** —
`Ctrl+Alt+G` now opens the settings window during an active recording too; only the
pending-insert ignore (and its calm console line) remains. Per the maintainer's
explicit direction of 2026-08-20, the focus argument does not hold for an active
recording: a settings window only steals the insertion target if it still holds
focus at *stop* time, a hazard that already exists today (a window can be open from
before the recording started — recording start is not blocked by an open window),
and the one genuinely lossy case, a mid-recording *Save & restart*, is caught
losslessly by the #202 salvage path (the in-flight audio is saved on the clean
shutdown and `Ctrl+Alt+R` re-transcribes it after the relaunch, D-001). This
narrows the ignore from *recording or a pending insertion* back to *pending
insertion only*; it does **not** touch the rest of D-009 — the one-window guarantee,
the distinct mutex, focus-don't-refuse, the pending-insert extension, and the
no-deferred-auto-open rule all stand. Not a supersede.

Do not reintroduce: sharing the tool's mutex name for the settings app; a settings
second-instance that refuses-with-notice instead of focusing; a prefix title match
that can hit the tool's console window; a deferred settings auto-open that could
front-run a pending insertion; a DEBUG-only sign for an ignored press; or a
*permanent* forced-topmost for the focus remedy (the transient pulse is deliberate).
Nor the recording-active ignore for `Ctrl+Alt+G`: since #217, opening during an
active recording is deliberately allowed, and only the pending-insert ignore remains.

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
- **Light, not dark.** *(Reversed by the 2026-08-22 #228 addendum below — the app
  now ships the dark terminal palette; the OS-drawn seams named here are answered by
  a DWM dark title bar and a dark-styled combobox popdown, leaving only the light
  messagebox as an accepted rarity.)* `messagebox` dialogs, the window title bar and the combobox
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
as one product. *(#228 addendum below: the palette is now the console's dark twin
of `docs/style.css`, not the light website palette itself — still one product.)*

**2026-08-22 addendum (#228).** The **light-over-dark call is reversed.** The
settings/onboarding app now wears the tool console's own **dark terminal face** — a
blue-black page, the monospace type chain, the console's light-blue brand accent
(`console_ui.ACCENT`, `#59C2FF`) and the bright terminal status colours, a dark twin
of the light `docs/style.css` — plus a pixel masthead (a generated six-tooth gear +
the console wordmark drawn as Canvas pixel art) that reads as one product with the
console. The **mechanics of D-010 are untouched and stay in force**: `clam` stays
pinned (the leave-vista argument — OS-drawn chrome ignores ttk colour — is exactly
what lets a fully custom dark look exist at all), `settings_theme.py` stays the single
source of both the page and card surfaces (the #180 canvas still reads `TFrame`'s
background, so the two follow each other), and every text/background pair is still
WCAG-checked in `test_settings_theme.py` (AA on both surfaces, the `CONTROL_LINE` 3:1
floor), re-tuned for the dark palette. The OS-drawn seams the *Light, not dark* bullet
warned of are **answered, not avoided**: the **window title bar** is darkened via the
`DwmSetWindowAttribute` dark-mode attribute (guarded — a silent no-op off-Windows and
on Windows builds without it), and the **combobox popdown** (a plain tk `Listbox`) is
dark-styled through the tk option database; only the **messagebox**, OS-drawn and
rarely surfaced, stays light — an **accepted residual seam**, the trade the bullet
said would need paying and that the maintainer now accepts. Maintainer-approved in the
design session of 2026-08-22 (branch `settings-terminal-style`, commit 62c5154, #228).
This reverses one sub-call of D-010; it is an in-entry addendum, **not** a
new-D-number supersede — the clam / single-source / WCAG mechanics all stand.

Do not reintroduce: pinning `vista` (or any native theme) for the settings app;
styling only frames/labels while leaving OS-drawn chrome (the half-restyled
window); a vendored image-based `.tcl` theme (DPI-blurry, and a runtime-found file
whose error kills the rescue-lane window); a dark palette (it seams the OS-drawn
messagebox / title bar / popdown) — **superseded by the 2026-08-22 #228 addendum
above: the dark terminal palette is the shipped design, its title-bar seam answered
by the DWM dark attribute, its popdown dark-styled, and its lone messagebox seam
accepted**; or a second place that defines the page/canvas surface apart from
`settings_theme.py`.

Respects D-002 — a visual pass changes no write surface and no save semantics.
Respects D-005 — `settings_theme.py` is stdlib-only, so the system-Python rescue
lane still runs the app, and no third-party theme is vendored. Respects D-008 and
D-009 — the two-mode engine control's logic and the `app.title.*` focus-match
titles are untouched; only their container and styles change. Does not touch D-001,
D-003, D-004, D-006, or D-007.

---

## D-011 — Uninstaller keeps user data by default; the silent lane can never delete it

Decided 2026-08-18 (#209).

`setup.ps1` now registers a per-user Add/Remove-Programs (Apps list) entry on
every install and in-place update, and a new GUI uninstaller, `uninstall.ps1`
(PowerShell + WinForms, no Python/venv, no admin), removes the install and that
entry. The user-data safety stance is the contestable call:

- **Keep user data by default.** The uninstaller removes the app but keeps the
  user-data set — recordings and transcripts (`history/`, the legacy
  `voice_archive`/`text_archive`), the `.env` key, `personal_settings.json`,
  `runtime_state.json`, and the logs — driven by a sentinel-fenced keep-list
  (`KEEPLIST-BEGIN`/`KEEPLIST-END`). The keep-list is the user-data subset of
  `setup.ps1`'s install DENYLIST **minus `.venv`**: the virtualenv is rebuildable
  tooling of this install, not user data, so it is removed with the app
  (`.env.example` is a shipped template, likewise removed). The install dir is left
  standing only to hold kept data; if nothing is kept it is removed too, and the
  closing notice names the folder where the kept data lives. Because a keep-data
  uninstall removes the fingerprint files (`pyproject.toml`, `thoughtborne.py`) but
  leaves that residue, `setup.ps1`'s reinstall guard accepts a dir whose **every**
  entry matches the data denylist as a re-installable Thoughtborne folder (not a
  foreign one) — so reinstalling into the same dir works, and the copy restores the
  app while the denylist preserves the residue.
- **Deletion is opt-in, off by default, and needs a deliberate act.** The confirm
  dialog carries an **unchecked** checkbox ("also delete my recordings …"); the OK
  ("Remove") button is the `AcceptButton` and takes initial focus, so **no
  click-through path** (Enter / OK / repeated Enter) ever checks it. Only a
  deliberate toggle of the checkbox opts into deletion.
- **The silent lane can never delete user data — structurally, not by default.**
  `QuietUninstallString` is exactly `uninstall.ps1 -Silent` with **no** delete
  flag; there is no command-line parameter that can request deletion at all. The
  `-Silent` path never builds the dialog or the checkbox, so the delete variable
  keeps its initialized `$false`, and the delete branch is gated on **both**
  `(-not $Silent)` **and** the checkbox state — two independent walls. So a
  winget/MDM/automation uninstall always keeps data.
- **Registry writes are install metadata, not config.** The Apps-list entry is
  written with registry cmdlets (`New-Item` / `New-ItemProperty`) only — no
  `Set-Content`/`Out-File`/`Read-Host`, no secret read or written — so `setup.ps1`
  still collects no secrets and the settings app remains the only config writer
  (respects D-002). `DisplayVersion` is read from the installed `pyproject.toml`
  (omitted, never faked, if unparseable); `DisplayIcon` points at the real shipped
  app icon (`assets\logo\favicon.ico` then; `assets\logo\thoughtborne.ico` since
  D-016).
- **The running tool is never killed.** A log-heartbeat guard (the AGENTS.md
  reliable signal, mirroring `setup.ps1`) refuses to uninstall a running tool and
  asks the user to close it with `Ctrl+Alt+4` — it reads the log, never the process
  list, and never terminates the process.
- **uv and its managed Python are left untouched.** They are shared per-user
  tooling outside the install dir; only the in-dir `.venv` is removed.
- **Self-copy to `%TEMP%` with the captured install dir threaded in.** The
  uninstaller copies itself to `%TEMP%` and relaunches (`-FromTemp -InstallDir
  <captured>`) so it holds no handle on the tree it deletes; the install dir is
  captured before the copy and passed explicitly, never re-derived from the temp
  location. The running guard runs **before** the copy (fail fast) and the registry
  key is removed **last, and only when no app files remain** — the remnant check
  reads the dir and drops the Apps-list entry solely once nothing the uninstall
  targeted survives (kept user data does not count), so a partial file-removal
  failure (a locked file) keeps both the leftover and the entry — a half-removed
  install stays visible under Installed apps instead of orphaning into an entry-less
  folder, and the user can clear the leftover by hand. (A clean re-run is not
  promised: if the fingerprint files were removed before the lock hit, the phase-2
  fingerprint guard turns a fresh "Uninstall" away.) It gates only the key removal;
  it never touches files or user data.
- **Fingerprint-guarded target, defused directory cleanup.** Before any deletion the
  uninstaller checks that `$InstallDir` looks like a Thoughtborne install
  (`thoughtborne.py`, or a `pyproject.toml` naming thoughtborne — the same
  fingerprint `setup.ps1` uses), so the ad-hoc "copied the script elsewhere and ran
  it" lane can never empty a stray folder; the regular Settings > Uninstall lane
  passes the real absolute path and always clears it. The confirm dialog names the
  folder it will touch. The final "remove the now-empty install dir" step is
  **non-recursive**, so a misread of a populated dir as empty (an ACL/enum error)
  cannot take kept data with it.

Do not reintroduce: an uninstaller that deletes user data by default; a
command-line/silent parameter that can request deletion; a click-through path
(Enter/OK) that ends with data deleted; a checkbox that starts checked or takes
initial focus; keeping `.venv` (or dropping `.env.example`) on the keep-list;
killing the running tool; removing uv or its managed Python; deriving the install
dir from the `%TEMP%` copy instead of threading the captured one; a config/secret
write in `setup.ps1`'s registry step; an uninstaller that removes from a folder
without the Thoughtborne fingerprint; a recursive final directory removal that could
take falsely-"empty" kept data with it; removing the Apps-list entry while app files
still remain; or a `setup.ps1` reinstall guard that refuses a keep-data residue dir.

Respects D-002 — the registry entry is install metadata written with cmdlets, not
a config write; the settings app stays the only config writer. Respects D-006 —
`uninstall.ps1` ships inside the whole-tree `git archive` ZIP, so it rides the
existing release asset with no asset change. Respects D-007 — the registry write is
a separate step with no copy, so the in-place-update self-overwrite guard for
`setup.bat` is untouched. Does not touch D-001, D-003, D-004, D-005, D-008, D-009,
or D-010.

---

## D-012 — The self-test default is `Ctrl+Alt+T`; the umlaut lane stays for overrides

Decided 2026-08-19 (#211).

The shipped self-test hotkey moves from `Ctrl+Alt+Ü` to `Ctrl+Alt+T`. `Ü` had been
declared intentional (it was an AGENTS.md guardrail); this entry records that the
call was reconsidered and reversed on purpose, so the change is not read later as
an accident and quietly undone.

- **Why the umlaut was the wrong default.** `ü` has no static VK code. Registration
  resolves it at startup via `VkKeyScanW` against the *active* keyboard layout, and
  when that fails `hotkey_manager` falls back to the hard-coded `VK_OEM_4` (0xDB) --
  which on a US layout is `[`. An international user therefore got the self-test on
  an undocumented key, or not at all, on the one hotkey whose entire job is to prove
  the install works. The default also contradicted `config.py`'s own adjacent
  guidance ("avoid ... non-ASCII letters in hotkeys").
- **Why `T`.** Static VK 0x54, present on every layout, mnemonic (T = test), and free
  in the shipped scheme (W H A D Y R X L G 4 6). Every shipped default is now a
  statically mapped key -- letter, digit, or F-key -- so the whole scheme registers
  without consulting the active layout.
- **Only the default moved; the special-key machinery stays.** The `VkKeyScanW` path
  in `hotkey_manager`, the `KEY_SPECIAL` lane and `'ue'` alias in `hotkey_parse`, the
  `udiaeresis` keysym in `settings_io`'s capture decoder, and the `Ü` rendering in the
  settings app and console are all live: a user may still bind `ctrl+alt+ü` (or any
  single character) through the #55 override surface, by hand or in the settings app.
  That code has no shipped caller now, which makes it look deletable -- it is not.

  *Superseded 2026-09-12 by D-023 (#317): the override lane is removed too, with
  the maintainer's okay. Every statement in this entry about binding `ü` is
  history from here on; the static-default rule stands.*
- **Two hard-coded default sources, not one.** `config.DEFAULT_HOTKEYS` is the shipped
  scheme, and `settings_io.PRESET_FKEYS` is the alternative F-key preset whose four
  housekeeping actions (open_history / open_settings / test_transcription /
  exit_program) are deliberately kept identical to it so switching preset means no
  relearning. `PRESET_FKEYS` does not derive from `DEFAULT_HOTKEYS`, so a default
  change has to be applied in both places; a test now enforces that parity.
- **No migration.** A user who never overrode `test_transcription` follows the new
  default silently. A user who pinned `ctrl+alt+ü` in `personal_settings.json` keeps
  it, unchanged and still registrable. A user who happened to bind `ctrl+alt+t` to a
  different action keeps their binding: `apply_hotkey_overrides` drops the colliding
  self-test default with one warning in `thoughtborne.log` -- never a failed start.
- **Accepted trade-offs.** A global hotkey swallows `Ctrl+Alt+T` system-wide while the
  tool runs, costing MS Office its (TM) shortcut and Photoshop its Free-Transform-copy;
  the self-test is a rare, deliberate action and the combo is remappable. AltGr is
  Ctrl+Alt on Windows, so on layouts where AltGr+T yields a glyph (US-International:
  the thorn letter) typing it fires the self-test -- the same aliasing class W/A/D/Y/R/L
  already accept on that layout, not a new category, and on the German QWERTZ target
  layout AltGr+T produces nothing at all.

Do not reintroduce: a shipped default on a key without a static VK code (an umlaut,
`ß`, or any layout-resolved character) in `DEFAULT_HOTKEYS` or `PRESET_FKEYS`;
removing the umlaut/special-key resolution as "dead code" because no default uses it;
letting `PRESET_FKEYS`' housekeeping keys drift from `DEFAULT_HOTKEYS`; or a migration
that rewrites a user's existing `test_transcription` override.

*The "removing the umlaut/special-key resolution" clause is superseded 2026-09-12 by
D-023 (#317) -- that removal happened, deliberately and with the maintainer's okay.
The rest of this list stands.*

Respects D-002 -- the default change flows through the settings app's existing
diff-vs-default write; no new write surface, and a user's explicit pin is never
touched. Does not touch D-001, D-003, D-004, D-005, D-006, D-007, D-008, D-009,
D-010, or D-011.

## D-013 — In-place updates are replace-only; orphaned files from an older release survive

Decided 2026-08-15 (#76, #104).

An in-place update -- re-running the one-liner, or `setup.bat` in the install folder
-- overwrites every file the new release carries and adds the ones it gained. A file
an *earlier* release left in the install dir that the new release no longer ships is
**not** deleted. The residue is accepted deliberately; this is the "(a) replace-only"
option of the one-liner spec's stale-file question, and recording it here closes that
spec's open decision no. 3.

- **Why not clean up the orphans.** Deleting what the new release no longer ships
  means knowing what it ships -- a file manifest. The release does not carry one: the
  ZIP is a flat `git archive` of the tag (D-006) that is simply unpacked over the
  install dir, and nothing in it says "these files and no others". Adding a manifest
  means a new artifact to build, version, and keep honest, on the one lane whose whole
  value is that it has no moving parts. The cost of the alternative is real work; the
  cost of the residue is a few dead files on disk.
- **Why the residue is tolerable.** Orphans are dead weight, not a hazard: nothing
  imports them, and `uv sync` rebuilds `.venv` from `pyproject.toml` + `uv.lock`
  regardless of what else sits in the folder. The one case that could bite is a
  *renamed* module -- the old name stays importable, so a stale import resolves
  against the old file instead of failing loudly. That is the trigger to revisit, not
  a reason to pre-build the machinery.
- **User data is a separate guarantee and unaffected.** The install denylist
  (`DENYLIST-BEGIN`/`END` in `setup.ps1`) keeps `.env`, `personal_settings.json`,
  `runtime_state.json`, `history/`, the legacy archives, the logs, and `.venv` from
  being overwritten at all. Replace-only is about *code* files; nothing here weakens
  the data protection, and the denylist is already written so a future clean-up pass
  can reuse the same list.
- **If orphans ever bite,** the recorded target is (b) manifest-diff-clean: ship a
  manifest with the release and delete on-disk files absent from it, gated by the data
  denylist. Option (c), wiping a code subtree, stays out of reach until code and data
  live in separate folders -- today the tree is flat, with `history/`, `.env` and the
  log beside the `.py` files.

Do not reintroduce: an ad-hoc deletion pass that guesses which files "look stale"
(anything without a manifest is guessing, and it is guessing next to the user's data);
a manifest bolted on without also making it part of the release build; or a "clean
install" that silently deletes the install dir on update -- the uninstaller is the
place where deletion is a deliberate, user-confirmed act (D-011).

Respects D-006 (no change to the asset contract -- the same two assets, unpacked the
same way), D-002 (setup collects no secrets and writes no config file; user data is
denylisted, not merged), D-007 (the `setup.bat` self-overwrite guard is untouched),
and D-011 (uninstalling, where files really are removed, keeps user data by default).
Does not touch D-001, D-003, D-004, D-005, D-008, D-009, D-010, or D-012.

## D-014 — Settings is part of the app: no unsaved-changes guard, one exit, one lane

Decided 2026-08-20 (#221 -> #222 -> #223, the "one-unit" doctrine, landed in that
order; all three steps are now implemented, each recorded below).

The settings/onboarding window is part of the app, not an app of its own. From that
follows radical simplicity: no unsaved-changes bookkeeping, no discard dialog, no
"are you sure" layer. Save saves; Cancel/[X] closes, period. The maintainer accepts
the trade-off explicitly -- the worst case is retyping an API key.

- **No unsaved-changes concept (step 1, #221).** The `dirty` flag, its mark-on-edit
  hooks (`_mark_dirty` and its call sites), the close-time discard prompt (`_on_close`),
  and its DE/EN strings (`dlg.discard.*`) are gone -- built as if the concept had never
  existed, not as "the old code minus the check". Close paths close: the window [X] and
  the Cancel button wire straight to `root.destroy`, the teardown every Save path already
  uses. Save paths are untouched. Field-edit callbacks that also drive real work stay --
  the test-generation stamping, the #178 engine preselect, the #201 key-aware render, the
  hotkey state -- only their poke at the removed flag is gone; not one trace or callback
  existed solely to feed the flag. The separate #193 save-time engine-SELECTION check
  (which field an engine was actually chosen in, feeding `resolve_engine_save_signal`,
  D-008) is NOT this concept and stays.
- **Language toggle self-persists.** With no dirty concept a toggle would be silently
  lost on close, so it is written the instant it happens, via a ui.language-only surgical
  merge: `write_personal_settings(hotkeys_effective=None, default_api=None,
  ui_language=<new>)`, where every managed block's `None` now means "leave exactly as
  found" (the invariant `defaults.api`/`ui.language` already had, generalized to hotkeys
  rather than adding a parallel writer). It fires only on a real toggle, so a session that
  never toggles leaves `personal_settings.json` byte-identical (the #144 contract). The
  write is best-effort: a failed toggle-persist costs only the remembered display language,
  never the settings, so it stays silent rather than raising a dialog. One rule for both
  wizard and settings mode -- the language radios live in the shared header, so there is no
  mode fork. Because the toggle self-persists, the Save path no longer writes ui.language
  at all (`ui_language=None`), which retires the `_lang_toggled` / `_had_ui_block` save
  bookkeeping too; a keyed save still preserves an existing `ui` block as-found, so the
  language is never dropped.
- **One exit (step 2, #222) and one lane (step 3, #223)** build on this: quitting the tool
  ends the settings window too, and the standalone settings lane is retired. Those steps
  cite D-014. D-005's venv-first launcher design retires with the standalone lane in step 3;
  D-009's single-window mutex + focus-existing remedy **stays** (one window is still one
  window).

Absorbs #215 -- removing the dialog machinery removes the false-"dirty" symptom that
issue reported, wholesale.

**2026-09-06 addendum (#239).** The ui.language-only write above now goes through the
gated `settings_io.write_ui_language` instead of calling `write_personal_settings`
directly. Over a healthy file nothing changes -- the same surgical merge, hotkeys and
`defaults.api` left exactly as found. What is new is that the silent lane probes the
target for corruption FRESH on every toggle: over a corrupt-but-decodable
`personal_settings.json` (the bytes read fine, the JSON is invalid) the write would
start from a bare managed skeleton and destroy hand-written blocks like `vocabulary`,
so the toggle now returns without writing and leaves the file byte-identical.
Warn-then-overwrite stays what it always was -- the explicit Save's branch alone
(D-002), because only Save is a write the user asked for. The cost is the one this
entry already accepts: the persist is best-effort, so a gated toggle costs nothing but
the remembered display language (the window says so until the file is fixed). Not a
supersede -- the mechanism is refined, the decision and its promises stand.
Settled via issue #239.

**2026-09-07 addendum (#271).** Every save restarts -- the last exception is gone. The
save paths still carried a pre-#202 branch: with no API key present a save merely closed
the window (`settings_io.resolve_save_action` -> `save_close` in the wizard, `save` in the
everyday dialog), on the premise that relaunching a keyless tool would only re-open the
shop window plus another wizard, "a window loop". The premise was wrong twice. Pickup is
start-based (D-002), so the keyless branch silently deferred every saved hotkey to the
user's next manual start with nothing on screen saying so -- a maintainer install lost a
changed switch-model hotkey exactly that way. And what it dodged is not a loop: a keyless
relaunch re-opens Setup exactly once, the #200/#144 keyless-start rule doing its job. Now
Save writes the files and performs the #202 restart in every case -- wizard or everyday,
keyed or keyless; the rail reads "Save & restart" in both modes, and the no-key dialog
says so ("Save and restart now?") instead of promising a close. `resolve_save_action` and
the `btn.save` / `btn.save_close` strings are removed rather than left as a resolver
returning a constant. That a keyless save comes back with Setup open again is intended and
was confirmed as such: while no key is entered the tool cannot be used at all, so being
shown Setup on every keyless start -- the restart's included -- is the behaviour, not a
symptom. Considered and rejected: suppressing the wizard on that one relaunch, via a
`--restarted-by-settings` launch argument forwarded through `Thoughtborne.bat` and read by
the tool. It would spare a single re-open at the price of a three-file cross-process
contract, to hide a rule the tool wants -- recorded here so it is not re-proposed as a
"fix". Not a supersede: the one-unit doctrine is unchanged, one of its save paths simply
stopped making an exception. Respects D-002 (pickup stays start-based -- the restart
performs the start), D-001 (the #202 salvage path is reused unchanged), D-004 and D-009
(the mutex wait and the single-window guard untouched) and D-008 (engine memory and the
save signals untouched). Settled via issue #271.

Do not reintroduce: an unsaved-changes flag; a discard / "are you sure" prompt on close;
a close handler that does anything but destroy the window; a pass-through `_on_close`
wrapper that only forwards to `destroy`; a language toggle that is persisted only at
Save time; a keyless save that only closes instead of restarting; or a launch argument
that suppresses the first-run wizard for the settings app's own relaunch.

Respects D-002 -- `settings_io` stays the only writer of `.env` / `personal_settings.json`,
and the ui.language-only write is the existing surgical merge with hotkeys/defaults left
exactly as found, so no unmanaged block or `_comment` is dropped (a deliberate consequence:
an invalid hand-typed `ui.language` is now left as found on Save, like the `defaults.api`
invalid-value rule, instead of being normalized). Respects D-008 -- the engine memory and
displaying-vs-selecting logic is untouched. Respects D-009 -- the single-window guard and
focus-existing remedy stand. Does not touch D-001, D-003, D-004, D-006, D-007, D-010,
D-011, or D-012 (D-005 is retired by step 3, not this step).

---

## D-015 — The settings app defaults to English; German is an explicit opt-in

Decided 2026-08-22 (maintainer call in session; no issue).

The settings/onboarding window used to auto-select German when the Windows
display language was German (`detect_ui_language()`, #144). It now always starts
in English; the header's DE/EN toggle stays, and a chosen language self-persists
to `ui.language` (D-014) exactly as before.

- **Why English.** The app itself — console, hotkey grid, panels — speaks
  English. A settings window that opens in German in front of an English tool is
  the inconsistency, not the fix: settings prose *explains* the English UI, so it
  should match it by default.
- **Why German stays offered.** Settings prose is harder than the console's
  short commands; a German-speaking user may genuinely be helped by the German
  variant. Offering it as a visible choice keeps that value without making the
  choice for them.
- **What was removed.** `detect_ui_language()` and its system-language probe are
  gone (guarded by `test_settings_io.py`); `settings_strings` is now pure string
  tables. A stored `ui.language` keeps winning over the default, so existing
  users who toggled keep their language.

Do not reintroduce: system-display-language detection or any other implicit
language guess; the default is a constant, the user's stored choice the only
override.

Respects D-014 (the toggle + self-persist mechanics are untouched) and D-002
(no new writer; the stored choice still comes from the same surgical merge).

---

## D-016 — The Windows app icon is the pixel mark on a hard-cornered dark-grey tile

Decided 2026-09-07 (maintainer call in session; no issue).

Thoughtborne has two marks. The round navy *flow mark* (#48) is the umbrella
brand — GitHub avatar, the lockup, anything that speaks for the project as a
whole. The *pixel mark* — `console_ui.LOGO_MARK_A5`, the 7×6 half-block glyph in
the console masthead — is the app: the website favicon since v1.1.0, and now
every Windows surface. `assets/logo/thoughtborne.ico` is the one app-icon file:
`setup.ps1` points the Start-menu shortcut and the Installed-apps `DisplayIcon`
at it, and the settings window loads it through `iconbitmap` — in both Tk's
class-wide and its window-specific form, the second added for the taskbar
button (#296) — so its title bar, taskbar button and Alt+Tab entry stop showing
Tk's feather.

- **Why the pixel mark.** The program *is* the console; its masthead is what the
  user looks at every day. An icon that shows something else is a second identity
  to learn. Since v1.1.0 the site favicon already made that call for the browser
  tab; the desktop had simply been left behind (the shortcut still carried the
  v1 flow mark, all but invisible on a dark desktop).
- **Why a tile, and why hard corners.** An icon has to survive black, white, the
  accent blue of a pinned tile and a photo wallpaper. No single mark colour does:
  the console accent vanishes on white, navy vanished on black. So the tile brings
  its own ground — but it is not a container with a logo in it. It is part of the
  pixel picture: the mark plus exactly one ring of ground pixels, a 9×8 grid, no
  rounding, no anti-aliasing. The terminal look is adopted on purpose and worn
  with confidence, the way Claude Code wears it, not a legacy look apologised for
  with soft corners. Windows never rounds icons itself; the corners are ours.
- **Why neutral grey, not black or the console ground.** `#242424`, equal RGB —
  a modern terminal's dark grey rather than 1990s pure black, and no blue cast:
  the console/settings ground `#0C1117` and the brand navy both tint blue next to
  Windows' own dark surfaces. On those surfaces (taskbar, Start, Installed apps)
  the tile melts into the ground and only the mark stands, exactly as in the
  masthead; on light and coloured grounds it reads as a tile.
- **Sizes.** Ten frames, 16–256 px, each an integer scale of the grid so pixel
  edges stay hard. At 16 and 24 px the full grid would fill under 80 % of the
  canvas, so there the ring narrows to one device pixel and the mark takes the
  next scale (a strict 9×8 at 16 px is a 9-pixel-wide icon next to full-size
  neighbours); every other frame is the true grid. Frames up to 64 px are stored
  as plain bitmaps, the larger ones as PNG — not taste but necessity: Tk's own
  ICO reader takes a frame's size from its bitmap header, so an all-PNG file
  reads as garbage and the settings window ends up with a blurred scale-down
  (verified on Tk 8.6.12). `assets/logo/make_app_icon.py` is the source of
  truth and `test_app_icon.py` holds the .ico to it — rebuild the .ico when the
  mark or the accent changes; never hand-edit it or scale it with a smoothing
  resampler.

Do not reintroduce: the flow mark on any app surface, a second icon file for a
second surface (one file, every surface), rounded corners, a tinted or
pure-black ground, or a hand-touched .ico. The website favicon is not part of
this decision — it stays the bare mark, navy or accent by colour scheme.

Respects D-006 (the icon rides the whole-tree `git archive` ZIP;
`build-release-zip.sh` lists it as a must-have file) and D-013 (an in-place
update re-copies the file and re-registers `DisplayIcon`; the retired
`favicon.ico` stays orphaned in older installs, which is that rule's stance).
One deliberate exception to the installer's leave-a-matching-shortcut-alone
rule: a Start-menu shortcut whose icon still points at the retired
`favicon.ico` gets exactly that property moved, in place — otherwise every
pre-D-016 install would keep the old icon forever, while any other icon a user
chose stays theirs.

---

## D-017 — API keys come from the install directory's `.env` only

Decided 2026-09-07 (#269).

Thoughtborne obtains `GROQ_API_KEY` / `SONIOX_API_KEY` exclusively from the `.env`
in its install directory. The process environment is neither a fallback nor an
override, and nothing from `.env` is put into the process environment. Both halves
of the product read the file through the same stdlib reader in `config`
(`read_env_file`); `python-dotenv` is no longer a dependency, and `${VAR}`
interpolation is not supported — a value is literal.

Contestable, because most CLI tools honour environment variables. Rejected here:

- **The settings app is the repair surface** and must always be able to show the
  key the tool actually uses (D-002). A source the app can neither see nor write
  breaks that: with a leftover Windows variable set, the tool started keyed while
  the settings app opened its first-run wizard with empty fields — each half right
  about its own source.
- **Side-by-side installs need independent keys** — two checkouts, a test install
  beside the live one.
- **An inherited value silently defeating a key rotation** is exactly the "nothing
  on screen says so" failure the quality bar forbids, and it was not hypothetical:
  the settings app's own restart lane handed the relaunched tool an exported empty
  value, which the old loader kept over the file's real key. Every install set up
  through the wizard hit it on the second key added, or on any key change.
- **Code that detects a source we do not want is dead weight**, so the settings app
  grows no detection or display of inherited variables, and the relaunch path grows
  no environment scrubbing — with nothing exported, that chain carries no key state
  at all.

One reader rather than two also ends the parser split: quoting, a leading
`export `, comment tails and duplicates now mean the same thing to the tool and to
the settings window, by construction rather than by two implementations that
happen to agree. The rules are written out in `.env.example`'s header.

The D-004 developer opt-out (`THOUGHTBORNE_ALLOW_SECOND_INSTANCE`) keeps its `.env`
route through the same reader. Installer and handshake variables
(`THOUGHTBORNE_INSTALL_DIR`, `THOUGHTBORNE_VERSION`, `THOUGHTBORNE_SPAWN_TS`) are
process plumbing, not configuration, and stay environment variables.

The accepted cost: a user who kept a key only as a Windows environment variable
starts keyless after this change, and the start screen tells them where to put it.

Respects D-002 (the write contract is unchanged), D-004 (the opt-out is kept, its
route clarified), D-005 (the stdlib-only constraint only gets easier — `config.py`
now has no third-party import at all) and D-014.

---

## D-018 — The console is built for 72 columns and up; there is no second form

Decided 2026-09-07 (#273).

`console_ui` renders one presentation form: the framed panels and strips, 70
cells wide. The 72 in the title is that figure rounded up — a 72-column window
carries the frames with a column to spare on either side; a window too narrow
for 70 cells wraps them, and that is the whole behaviour — there is no narrow
variant, and nothing in the renderer or the app measures the terminal. (The `ansi` switch is a different axis and stays: it is
the length-equal plain-ASCII twin of the *same* framed layout, not a second one.)

The frameless *compact* form came with the Cockpit redesign (#109) as a design
element — every block measured the terminal width and, under 72 columns,
replaced the framed panels with a frameless variant — and was never recorded as
a decision. It cost 28 branches through the renderer, four helpers of its own, a
second reason table, its own wordmark and a complete second test lane, so every
layout rule had to be thought through twice.

- **No real use case.** Windows Terminal and the classic console both open at
  120 columns; nobody lands under 72 by accident. The console reports, it is not
  operated (README), and for getting it out of the way the README already points
  at Terminal's own hide settings. The dim ticker and log lines were never
  width-limited anyway.
- **It was the worse-tested half.** The width verification ahead of the
  hotkey-presentation package (#272) found six overflows in the compact key
  grid alone, and more elsewhere in the form that already broke under the
  shipped `Ctrl+Alt` scheme — not one of them caught by a test, because the
  width check had no fixture for that form at all: it measured the framed
  masthead and one strip, and never a compact line.
- **Its arithmetic could not carry what comes next.** The two-column combo grid
  #272 introduces does not fit the compact widths at all; it would have needed a
  layout rule of its own, written twice for every screen.
- **VISION principle 6, *lean by default*:** "Convenience for an edge case is
  weighed against what it costs in code to carry forever; when in doubt, the tool
  stays small and maintainable. Removing something nobody needs is as much
  maintenance as adding something people do." A convenience nobody reached for,
  set against a second version of every screen — it loses.

The accepted cost: someone who deliberately runs a very narrow console window
sees wrapped frames instead of a layout made for them. Cosmetic, and the console
is not what one looks at while dictating.

Do not reintroduce: a compact or otherwise frameless console form; a
terminal-width measurement that switches between presentation forms, per block
or anywhere else; a second copy of any renderer text (reason lines, wordmark,
key grid) that exists only for a narrower layout. A window too narrow for the
frames wraps them — that is the answer.

---

## D-019 — One canonical hotkey order, and one display grammar for keys

Decided 2026-09-07, landed with #274 — the structural step of the
hotkey-presentation package (#272). The two steps after it, #276 and #277,
finished implementing the grammar recorded here (both landed 2026-09-08); within
the package this entry deliberately led the code by two commits.

**The order.** The dict order of `config.DEFAULT_HOTKEYS` is the one canonical
action order: `start_recording, stop_recording_clipboard, stop_recording_send,
stop_recording_no_insert, stop_recording_keyboard, cancel_recording,
retry_last_failed, switch_api, open_history, open_settings, test_transcription,
exit_program` — shipped letters `W A D Y H X R L 6 G T 4`. Every multi-column
surface reads left to right, then top to bottom. Nothing else carries an order of
its own: the app iterates `HOTKEYS` (which inherits it by deepcopy) and hands
renderers `(action_name, display_combo)` pairs, renderers look their labels up by
name, and the settings app's Hotkeys tab, the registration log's `Registered:`
lines and the README twins' tables all follow by iteration — the tables held
there by a drift guard in `test_hotkey_overrides.py`. The one deliberate
exception is the four-key footer line, which keeps its #115 reading order (record
· history/retry · model · quit) in `console_ui.FOOTER_ACTIONS` /
`FOOTER_ACTIONS_RETRY`: it reads as a sentence, not a grid, and has read that way
since #115. It selects four actions by name; it is not a second copy of the
twelve-action order.

Before this, four different orders lived in the code (console grid, settings
tab, README tables, registration log), one of them positionally coupled to a
list in `console_ui` with nothing checking the coupling.

**Why `H` stands fifth, not second.** `Ctrl+Alt+H` (simulated typing) is the
fallback insert path — the README calls it "the fallback for apps that block a
paste" and tags the `Ctrl+Alt+A` row above it "clipboard paste — faster" — yet
the old grid showed it right after `W`, reading as the thing you do. The first
row is now the dictation loop (`W A D`), the second the special cases (`Y H X` —
keep, fallback-type, abort), then daily housekeeping, then the rare things with
*quit* last. `A D Y H` rather than `A D H Y` is deliberate: `H` must stop reading
as the default, and the README's own "`Ctrl+Alt+Y` … insert later with `A` or
`H`" matches it.

**The display grammar** (rules 3–5 of #272, executed by the renderer):

- A modifier prefix shared by ALL keys of one key list is shown once, as a lead
  next to the keys it anchors; those keys are bare. Keys that share no prefix are
  shown as full combos — every one of them.
- Prose, and any single named combo (the READY line, `MODEL  switch:`, guidance,
  the WHAT-NOW sentences), always names the full combo: in a console program a
  bare letter in a sentence reads as "type R".
- Key cells are laid out by one geometry — uniform cell width (widest shown key +
  2 + widest label), two-cell gaps — and the column count follows it: three
  columns exactly while every bare key fits the three-column budget (one cell with
  the shipped letters, which is where the anchors 2/24/46 come from); two columns
  otherwise, bare under a lead, full combos without one.
- A combo wider than the derived key-column budget (`KEY_BUDGET`, 13 today,
  computed at import from the grid — the tightest key surface) renders as
  `[...]+<key>`: every modifier replaced by the ASCII `[...]`, the final key kept,
  only the key token bold. Key columns only — prose is never shortened this way,
  and the settings app always shows full combos; it is the place to look one up.
- No surface derives a bare key before it is known whether a lead anchors it. The
  app hands full display combos; the renderer derives a box's lead from exactly
  the combos that box was handed (the computing place is the implementer's call
  per #272; putting it in the renderer makes "a key shown but missing from its own
  lead derivation" structurally impossible). That inversion is where the old wrong
  keys came from: letters derived first, the lead decided later — under the
  settings app's own F-keys preset the REC strip read `F10 type   F10 paste
  F10 paste+Enter`, one letter standing for three different actions.

**2026-09-08 addendum (#290).** The footer's own order moves from the app to the
module that renders it: `console_ui.FOOTER_ACTIONS` / `FOOTER_ACTIONS_RETRY` now
name the four actions, and `thoughtborne._footer_keys` reads them from there. The
module already owned the footer's *words* (`KEY_WORDS`); owning the *order* beside
them removes the second copy the ladder had to keep (it cannot import the app off
Windows) together with the source reader that held that copy honest — about fifty
test lines, replaced by a short guard that the app writes no order of its own. So
where this entry named `thoughtborne._footer_actions` as the order's home — and
the renderer's own docstring said the app owned it — both now name `console_ui`;
the app supplies the combos. The ladder pins those two tuples against their
literals the way it pins `KEY_BUDGET` — a value pin, not a second producer:
nothing builds a footer from it, it only turns a reorder red instead of letting it
quietly re-read every surface. Nothing about the order itself, the display
grammar or any rendering changes — the change was measured byte-identical across
every surface. In the same step the two panels that name the retry combo in prose,
`render_transcription_failed` and `render_device_loss`, read it out of the footer
they are handed, exactly as the switch combo already was: no fallback key enters
the renderer, and the precondition — that these two take the retry footer — is
pinned by the ladder rather than trusted. Maintainer okay 2026-09-08 in #290
(comment 5586850375: "purely under-the-hood, not outward-visible — your call, keep
the code simple"). Not a supersede — the do-not-reintroduce clause against a second
copy of the action order stands, and now bites on the app side.

Accepted edge: two *different* combos that share their final key and are both
over budget shorten to the same `[...]+<key>` token. Reachable only with 14-cell
combos in one box; the settings app disambiguates.

Do not reintroduce: a key letter derived anywhere before the box's lead is known;
a fallback key inside the renderer (the old `"L"`); a second copy of the action
order, positional or literal; a hardcoded cell budget or column count. Respects
D-012 — the reorder changes no value, and the F-keys preset's housekeeping keys
still equal the defaults, compared per action.

---

## D-020 — Reset to defaults: the app's own settings, never the user's data

Decided 2026-09-08 (#282).

The settings app gets what a settings window is expected to have: one control on
the Machine Room tab that puts everything back to how the tool shipped. What it
may touch is the contestable part, and the line is **settings, not data**:

- **Reset means the four app-managed keys, and exactly those.** The `hotkeys`
  block, `defaults.api`, `push_to_talk.enabled` and `ui.language` — precisely
  what `settings_io.write_personal_settings` manages — go back to the shipped
  state: no hotkey overrides, no startup pin, push-to-talk off, English.
  Nothing else in the file is app-written, so there is nothing else to reset.
- **Keys and hand-written content stay — the uninstaller's line (D-011).** The
  `.env` keys, the `vocabulary` and `soniox_endpointing` blocks, the hand-tuned
  push-to-talk trigger/insert/timings, every `_comment` and every block the app
  does not know are the user's data, not settings. The keys are safe
  **structurally**, not by promise: the reset never calls `settings_io.write_env`,
  the only `.env` writer. A parked `_`-prefixed hotkey key
  (`"_disabled_exit_program"`) survives too — `apply_hotkey_overrides` skips
  every `_` key, so such an entry is a comment, not a binding.
- **The values are FORCED, not diffed — that is the whole point.** The save
  signals (`resolve_engine_save_signal` / `resolve_ptt_save_signal`) express "did
  the control move?", and D-002's leave-as-found rule then keeps an invalid
  hand-typed value alive on purpose (`"api": "grok"`, `"enabled": "yes"` — the
  tool warns about them at every start). Those values are *displayed* as the
  default they produce, so a form-driven reset would move nothing, signal `None`,
  and leave exactly the junk the user wants gone. The reset therefore writes
  `hotkeys_effective=DEFAULT_HOTKEYS`, `default_api=REMOVE_API_PIN`,
  `ptt_enabled=False`, `ui_language="en"` unconditionally. It needs no new write
  function and no new sentinel; D-002's surgical merge stands unchanged.
- **The engine memory stays (D-008).** `runtime_state.json` records the engine
  last chosen with `Ctrl+Alt+L`: machine-written, a record of what the user did,
  not a setting — and on the never-delete list in `AGENTS.md`. Dropping the pin is
  exactly D-008's `REMOVE_API_PIN` case, whose own rule is that the memory is left
  alone and keeps deciding. The consequence is named rather than hidden: with a
  pin *and* a divergent memory, the next start lands on the remembered engine,
  not on `soniox-live`. The setting is at its shipped default; the record is not a
  setting. The tab says so, and the engine control shows the remembered engine in
  plain text.
- **One lane, one exit (D-014).** The reset writes once and then goes through the
  existing `_restart_and_relaunch` handshake like every save since #271 — pickup
  is start-based (D-002), so a reset that did not restart would silently defer
  itself. It is not a third save path: no `.env` write, no resolvers, no second
  rail label. Because the button sits on a tab rather than the rail, the restart
  freeze disables it explicitly; otherwise a second click during the deliberately
  responsive wait would start a second handshake.
- **A confirmation is not an unsaved-changes guard.** D-014 forbids an "are you
  sure" on *closing*; this one sits in front of a *destructive* action and follows
  D-011's shape — the destructive answer is an explicit opt-in, never a
  click-through: the dialog opens with **No** preselected, so Enter preserves.
- **Over a corrupt file the dialog tells the truth instead of promising.** A
  corrupt-but-decodable `personal_settings.json` takes D-002's warn-then-overwrite
  branch like any explicit save, so the hand-written blocks really are lost there
  — and a broken file is *correlated* with a hand-edited vocabulary, since that is
  the block people edit. The reset stays available (flattening a broken file is a
  legitimate thing to want), but the confirmation switches to a second body that
  says what will be lost and suggests repairing the file first. The rule is
  unchanged; only the wording stops making a promise it cannot keep in that one
  case. The #239 gate remains what it was: protection for the *silent* language
  write, not for an action confirmed twice.
- **Settings mode only.** The control is built in the everyday dialog, not the
  first-run wizard: a first run has nothing to reset, and the wizard is where an
  unsaved API key sits in a field the reset would not write. The Machine Room
  **tab** is still built in both modes — the #281 rule that the tab list must not
  fork is untouched.
- **The reset is not a wipe, and says so.** A truly empty slate means deleting
  `.env` and `personal_settings.json` by hand; the tab says that in one sentence,
  next to #281's open-the-folder button.

Accepted edge: `write_personal_settings` re-serializes the whole file, so a
hand-formatted `personal_settings.json` comes back normalized even though no
unmanaged value changed. The honest promise is "no unmanaged value is changed,
added or removed"; byte-for-byte holds for `.env` (never opened) and for every
reset after the first (it is idempotent).

Do not reintroduce: a reset that deletes API keys, the `vocabulary` or
`soniox_endpointing` blocks, any `_comment`, or a settings file wholesale; a
reset driven by the save signals instead of forced values (it leaves invalid
hand-typed values behind); a reset that clears `runtime_state.json`; a reset that
writes without restarting; a confirmation whose destructive answer is the
preselected one, or one that promises the hand-written blocks survive a corrupt
file; or a second write path to `.env` beside `settings_io.write_env`.

Respects D-002 (the surgical merge and its three-valued contracts, unchanged),
D-008 (precedence and the memory-write rules untouched), D-011 (the
keep-by-default line and the opt-in shape), D-014 (one lane, one exit; the
confirmation guards an action, not a close) and D-015 (English is the shipped
language the reset writes).

---

## D-021 — A checkout names its commit; an installed copy shows the release number alone

Decided 2026-09-09 (#297).

The console masthead says which version is running. What it says depends on how
the copy was delivered, and both halves of that are contestable.

- **A checkout adds the short commit id, and no date.** The version string moves
  only on a release commit (RELEASING.md), which makes it exact for an installed
  copy — an immutable release snapshot — and stale in any checkout, where the
  code keeps moving under the last released number. So a checkout shows
  `v1.1.0+aa8f43a`. A date was weighed and dropped: its resolution is one day, so
  a copy that moves several times in a day — the normal case on a batch-run day —
  would show the same string for every one of those states, while the id
  identifies exactly one and is the thing a bug report needs. It is also the
  conventional form (SemVer build metadata, `git describe`). The honest
  counter-argument: the question this display really answers is "am I running the
  current state, or did the launcher skip an update?", and a hex id is the least
  legible possible answer to it. That question stays answerable because the id
  changes visibly whenever the checkout moves.
- **The gate is the `.git` beside the script, not a heuristic.** It separates the
  two delivery paths exactly: an install comes out of `git archive` (D-006) and
  `build-release-zip.sh` asserts no `.git` reaches the ZIP, so an installed user
  sees a clean release number and nothing else, and a clone sees the state it
  actually runs. Only the `.git` next to the script is read, never one in a parent
  directory (D-017's neighbour lesson from #238).
- **No `git` at startup.** Everything is read from the files with the standard
  library — `HEAD`, the ref, `packed-refs`, the reflog — and any problem yields no
  suffix, never an exception. The named cost of that: the id says which commit the
  checkout points at, not whether files were edited since. A dirty marker would
  need a real `git status`, and a subprocess on a cold Windows box is not worth it.
- **Dropped whole rather than shortened, and split by reader.** A cut-off version
  number is a false statement, so a token over the 21 columns after the tagline is
  left out entirely — `config.read_version`'s "nothing rather than a guess" in the
  geometry. `thoughtborne.log` carries what does not fit there, including when the
  working copy last moved, because that is where a bug report looks.

Do not reintroduce: a date in the masthead (one-day resolution cannot separate two
states of the same batch-run day), a `git` subprocess at startup, a truncated
version token, a suffix an installed copy assembles at startup -- widening the
`.git` search to a parent directory is how that would come back, and a suffix that
travels inside the files is not that (see the addendum) -- or a second masthead
layout for the case where no version can be read.

**2026-09-10 addendum (#306).** A third state joins the two above, and it is why
the "Do not reintroduce" line now names the suffix by its *source* rather than by
its shape. A maintainer can build an installable ZIP from an unpublished state
(`build-release-zip.sh --dev`), which stamps `<version>+dev.<short sha>` into the
`pyproject.toml` and the `uv.lock` it ships. A copy installed from such a ZIP is an
installed copy by every gate this entry names — there is no `.git` beside it — and
it still shows a suffix, because the suffix is part of the version string it was
built with. That is the whole distinction: the checkout suffix is assembled at
startup from the `.git` next to the script; the dev suffix travels with the files,
so every reader that shows a version shows it without knowing anything new. The
three read:

| Shown | Means |
| --- | --- |
| `v1.1.0` | installed from a published release |
| `v1.1.0+dev.50854a6` | installed like a release, from an unpublished state |
| `v1.1.0+50854a6` | running out of a checkout |

Nothing about the released path moves. A release is built without the flag, so its
`pyproject.toml` carries the bare number and an installed user still sees `v1.1.0`
and nothing else; the stamp exists only inside a `dist/dev/` artifact that is never
published (D-006's asset contract is untouched) and never in the tree, because
`git archive` reads the ref, not the working copy. Not a supersede — the entry's
rule stands, and this names the one delivery path it did not yet distinguish.

Respects D-006 (the ZIP is a `git archive`, which is what makes the gate exact),
D-018 (one console form: the version-less masthead keeps the same layout instead
of falling back to a centered tagline) and D-019 (the token is no key: it carries
no combo and changes no order).

## D-022 — Mouse buttons are not hotkeys here; the supported route is an external remapper

Decided 2026-09-12 (#308, reverting its implementation hours after it landed,
before any release carried it).

The middle and thumb buttons briefly worked as hotkeys (`9a34a28` + `04ff12e`: a
polled listening lane beside RegisterHotKey, capture in the settings window, a
caveat dialog about non-exclusivity). Reverted whole, for three reasons that
outweigh the narrow case it served:

- **The external route is better, not just equal.** Windows cannot make a mouse
  button exclusive for us — RegisterHotKey accepts the VK and never fires
  (measured 2026-09-08), and the low-level mouse hook that could swallow the
  press is the lane the Modern-Standby precedent (#66) rules out for a tool
  whose first principle is stability. A dedicated remapper (X-Mouse Button
  Control, AutoHotkey) *does* swallow the press and can emit F13–F24 — keys the
  grammar has accepted since #55, with static VK codes (D-012 holds) and no
  physical key to collide with. That yields an exclusive hotkey with no
  double-triggering: strictly more than the built-in lane could ever deliver,
  which is why the lane's own caveat dialog already recommended it.
- **The capture guarantee did not survive real driver software.** The design
  rested on "what captures, works": a button the mouse's driver swallows never
  reaches the capture field. A gesture-configured button under Logi Options+
  breaks that — its synthesized click reaches the settings window (captures
  fine) but never moves the global key state the polled lane reads (never
  fires). "Assignable but dead" through the UI is the worst version of the
  feature, and closing that hole would have meant more machinery on top.
- **The tool stays small on purpose.** The lane cost ~1,500 lines (~5% of the
  codebase) plus a second listening mechanism, a second registration ledger,
  and two issues of its own (#314, #315) — a standing tax on every future
  hotkey change, paid for a case that almost always has a better answer. The
  maintainer's stated bar — a codebase an agent can still reason about whole —
  is itself a product feature and wins here.

The user-facing answer is a pointer, not a feature: the settings app says in
plain text that a mouse button cannot be bound here and where the route is
described, and the README carries the recipe (remapper → F13–F24 → bind that
key as a normal hotkey). Tracked separately.

Do not reintroduce: mouse virtual keys in the hotkey grammar, a polled or
hooked mouse-listening lane, or mouse capture in the settings window — however
cleanly a request reads (#287's mouse half was such a request, and the answer
to the next one is this entry and the README recipe). If the facts change —
Windows grows a reservation mechanism that fires for mouse VKs, or the F13–F24
route stops working — that is a supersede discussion, not a re-implementation.
The full implementation survives in git history at `04ff12e` and `9a34a28`,
with the Win32 measurements in the issue.

## D-023 — One kind of hotkey key: the layout-resolved `ü` lane is removed

Decided 2026-09-12 (#317). Completes D-012, which moved the last shipped default
off the umlaut and deliberately kept the machinery for user overrides; this entry
removes that machinery too, with the maintainer's explicit okay of 2026-09-12 that
D-012 and the AGENTS.md guardrail required.

- **What went.** The second key lane, which existed for one offered key, `ü`: the
  `VkKeyScanW` runtime resolution with its `VK_OEM_4` fallback in `hotkey_manager`,
  the `KEY_SPECIAL` classification and the `'ue'` alias in `hotkey_parse`, the
  `udiaeresis` keysym in the settings app's capture decoder, the `Ü` glyph
  allowance in the console charset checks, and the "`ü` is still accepted" clauses
  across the docs. The lane was in truth wider than its one offered key: *any*
  single character (`#`, `ö`, punctuation) passed config-time validation and was
  resolved through `VkKeyScanW` at startup. All of that is one rejection class now.
- **Why.** One kind of key in the system instead of two, and the one failure class
  only this lane had -- startup registration depending on the active keyboard
  layout -- disappears with it. No shipped default has used the lane since #211
  (D-012); the maintainer's own settings never bound it; the other umlauts and `ß`
  were deliberately never offered (the N8 concern: non-ASCII hotkey keys can get
  typed into some apps).
- **Behavior now.** A `ü`/`ue` -- or any other non-static -- key in a
  `personal_settings.json` override is rejected the way unknown keys always were:
  one warning in `thoughtborne.log`, the action keeps its default, the tool always
  starts. The capture widget decodes the ü keypress to None, exactly like ä/ö
  before it; `validate_combo` reports the normal unrecognized-key message. No
  migration -- nothing rewrites a user's file.
- **What stands.** D-012's core -- the self-test on `Ctrl+Alt+T`, every shipped
  default on a statically mapped key -- is untouched and remains test-guarded.
  Superseded is only its "the special-key machinery stays" clause, and the
  AGENTS.md guardrail built on it, visibly, by this entry.

Do not reintroduce: a runtime- or layout-resolved key lane in any form -- no
`VkKeyScanW` path, no special-key classification, no capture keysym exceptions.
If a real need for a non-static hotkey key ever materializes, that is a supersede
discussion citing this entry, not a quiet re-add; the implementation survives in
git history (pre-#317).
