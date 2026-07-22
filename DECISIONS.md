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
  it differs from the built-in default. A user on the default scheme leaves no frozen
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
