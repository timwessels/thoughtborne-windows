"""
File IO and pure hotkey-combo helpers for the settings/onboarding app (#144).

The ONLY module that WRITES `.env` and `personal_settings.json` -- reading `.env`
goes through the one shared parser in `config` (D-017), which this module's
`read_env` calls -- plus the pure hotkey-combo helpers the GUI's capture widget
leans on. No tkinter, no network, nothing Windows-bound: it reuses the ctypes-free
`hotkey_parse` layer and `config`'s pure constants, so `test_settings_io.py` runs
on plain Python (the `console_ui.py` + `test_console_ui.py` house style).

Write policy (DECISIONS.md D-002): surgical merge, never a full
rewrite.
  - `.env` is edited line-wise -- only the two managed keys, every other line /
    comment / blank / order preserved; a value is spelled so the shared reader reads
    it back unchanged (`config.format_env_value`), an empty one is omitted because a
    blank value is no instruction, and the distinct `REMOVE_ENV_KEY` sentinel deletes
    a key's line -- D-026's WYSIWYG deletion, which only `resolve_env_save_updates`
    derives, from a field the user cleared.
  - `personal_settings.json` keeps every unmanaged block and every `_comment`
    untouched; hotkeys are written as a diff against `config.DEFAULT_HOTKEYS` (the
    default scheme writes no hotkey entries), unless `hotkeys_effective` is None,
    which leaves the hotkeys block exactly as found -- the ui.language-only write a
    settings-app language toggle makes (D-014). `defaults.api` follows a three-valued
    `default_api` contract (#193/#198, D-008/D-002): `None` leaves the file's value
    exactly as found (an untouched engine field -- so an unrelated save never deletes
    a hand-written pin, invalid junk included); the distinct `REMOVE_API_PIN` sentinel
    force-drops the pin (the two-mode control's remember-mode chosen over a pin); and
    a real engine id writes it verbatim, INCLUDING when it equals
    `config.BUILTIN_DEFAULT_API` -- "always start with X" is exactly the frozen copy
    the old diff-against-the-default rule avoided, so the diff rule for `defaults.api`
    is gone for this surface (the hotkeys diff rule stays). On an absent file only the managed
    blocks are written -- NEVER the example's placeholder `vocabulary` (its dummy
    terms would otherwise become live Soniox vocabulary, a real data bug). The
    GUI-only `ui` block (the settings app's own display language, #144) is a third
    managed block, but written only on demand: `write_personal_settings` touches it
    solely when passed a `ui_language`, so a user who never changed the language
    leaves no `ui` block behind (the dictation tool ignores it entirely).
    `push_to_talk.enabled` (#233, D-002 addendum) is a fourth managed key written the
    same way -- only on demand, and outside `MANAGED_BLOCKS` for the same reason:
    `ptt_enabled=None` leaves the whole `push_to_talk` block exactly as found (and
    creates none), a bool writes ONLY `enabled` and keeps every sibling the user
    hand-tuned beside it.
All writes are atomic (temp file in the same dir + `os.replace`). A present-but-
unreadable target (the bytes cannot be read -- locked / permission-denied) aborts
the save (the read error propagates) rather than clobbering it, and a UTF-8 BOM is
tolerated on read and healed (dropped) on write. A `personal_settings.json` whose
CONTENT cannot be carried (corrupt JSON, a non-object top level, bytes that are
not UTF-8) is never silently overwritten either: the deliberate actions -- Save and
Reset -- go through `save_personal_settings` (D-026), which renames the found file
to a timestamped `personal_settings.backup-...json` first and aborts if that
rename fails ("no backup, no overwrite"), while the raw merge writer refuses such
a file outright.
"""

import copy
import json
import os
import tempfile
import time
from pathlib import Path

import config
from hotkey_parse import (
    parse_hotkey_lexical, canonical_combo, classify_key, dead_combo_reason,
    HotkeyParseError, KEY_INVALID, VK_TO_TOKEN,
)

# ---- Tk event.state modifier bits (decode_key_event) -----------------------
# The capture widget (Checkpoint 2) decodes real Tk <KeyPress> events with these.
# Shift/Control are stable across platforms; the Alt bit on Windows Tk is NOT the
# X11 Mod1 (0x0008) -- Windows Tk reports Alt high (0x20000 is the commonly-cited
# value). They are module-level (not function-local) so the off-Windows test can
# drive decode_key_event with the exact same constants the widget will, avoiding
# drift. (Not independently confirmed here.)
TK_STATE_SHIFT   = 0x0001
TK_STATE_CONTROL = 0x0004
TK_STATE_ALT     = 0x20000


# =============================================================================
# .env
# =============================================================================
ENV_KEYS = ("GROQ_API_KEY", "SONIOX_API_KEY")   # the only keys this app manages

# The "delete this key's line" signal for write_env (#328, D-026's WYSIWYG deletion),
# built like the `defaults.api` one (REMOVE_API_PIN, D-002 addendum): a sentinel object
# matched by IDENTITY, never by equality. The empty string is exactly the value that
# must not mean delete on its own -- it is what an unreadable .env and a never-filled
# wizard field both look like -- so only resolve_env_save_updates below, which can tell
# a cleared field from an empty one, ever turns a value into this.
REMOVE_ENV_KEY = object()


def read_env(path) -> dict:
    """Return {KEY: value} for the managed keys (ENV_KEYS) the `.env` at `path`
    assigns a non-blank value to. THE parser is config.read_env_file -- the tool and
    this app read the same bytes through the same code (D-017), so the settings
    window can never show a key the tool does not use, or hide one it does (the
    parity D-002 needs, here true by construction rather than by two implementations
    that happen to agree). Quoting, a leading `export `, comment tails, `${VAR}`
    staying literal and the blank-is-no-key rule are that reader's, documented for
    users in .env.example's header.

    Never raises: a missing file, a missing key, a blank value, an unreadable or
    non-UTF-8 file all yield that key absent / {} -- this is a pre-fill helper, and
    the GUI simply shows empty fields (the write path, write_env, separately aborts
    on such a file rather than clobber it). The reader's warnings are dropped here;
    only config's import-time lane replays them into the log.

    `encoding` is deliberately not passed on: one default in one place, so the two
    call sites cannot drift apart again."""
    values, _warnings = config.read_env_file(path)
    return {k: v for k, v in values.items() if k in ENV_KEYS}


def _clean_env_updates(updates: dict) -> dict:
    """The part of `updates` write_env would actually act on: unmanaged keys dropped,
    every value folded back into ONE line and stripped of surrounding whitespace (a
    value must be a single .env line, and a pasted key is trimmed before it could
    reach an Authorization header, S4), an empty result dropped, and the
    REMOVE_ENV_KEY sentinel carried through untouched.

    The fold goes through `str.splitlines()` -- the reader's own split, rather than a
    second list of characters beside it -- so it removes exactly what would tear the
    written line apart on the way back in: CR and LF, but a vertical tab, a form feed
    or a NEL just as much (#328). Missing one of those was a quiet loss, not a stray
    character: the speller quotes it (it is whitespace), and the line then comes back
    cut off at it, with a startup warning about the writer's own file.

    The two rules are one decision seen from both sides: a blank VALUE is not an
    instruction and never clobbers a stored key, while a deletion is an instruction
    and has to be spelled as the sentinel (#328). That keeps the writer's own defence
    line independent of whoever computed the update set.

    An empty return therefore means "this save does not touch .env at all" -- and a
    pending deletion is a touch. Both write_env's no-op and unreadable_save_target's
    decision not to probe the file rest on that answer, which is why the rule lives
    here rather than inline in the writer: a pre-flight that answered it differently
    would block saves the writer never touches .env for."""
    cleaned = {}
    for k, v in updates.items():
        if k not in ENV_KEYS:
            continue
        if v is REMOVE_ENV_KEY:
            cleaned[k] = v
            continue
        value = "".join(str(v).splitlines()).strip()
        if value:
            cleaned[k] = value
    return cleaned


def resolve_env_save_updates(live_fields: dict, loaded_env: dict) -> dict:
    """The `.env` update set of one save under D-026's WYSIWYG rule, read as a
    DIFFERENCE (#328): for each managed key, the field's live value against the value
    the window was loaded and shown with (`read_env` at open). Both dicts map
    {ENV_VAR: value}; the live side is cleaned by the one shared rule above first, so
    "cleared" and "cleared with spaces" are the same gesture. A managed key MISSING
    from `live_fields` reads as a cleared field, so a caller must pass every key it
    does not mean to delete -- the app's `_live_env` always builds both.

      - live == loaded           -> the key is absent from the result: this save
        leaves its line exactly as found (an untouched field is no instruction, the
        house contract every other control follows -- and it covers the two empties
        that are not gestures: a wizard field never filled, and the pair that reads
        empty only because the .env could not be read at all)
      - live non-empty, differs  -> that value, to be written
      - live empty, loaded was not -> REMOVE_ENV_KEY: the user cleared a field that
        showed a stored key, so the save deletes that key's line (D-026)

    The premise of the last rule is that the field really displays what is stored --
    which is why the comparison is against the loaded snapshot rather than against
    emptiness: an unreadable `.env` degrades to {} in `read_env`, and a literal
    "empty means delete" would read a briefly locked file as an order to wipe both
    keys. Pure, so the whole table is off-Windows testable."""
    cleaned = _clean_env_updates(live_fields)
    updates = {}
    for key in ENV_KEYS:
        live = cleaned.get(key, "")
        loaded = loaded_env.get(key, "")
        if live == loaded:
            continue
        updates[key] = live if live else REMOVE_ENV_KEY
    return updates


def write_env(path, updates: dict, *, example_path=None) -> None:
    """Set each managed KEY in `updates` in the `.env` at `path`, preserving every
    other line / comment / blank / key order and the file's existing line endings.
    For a KEY that already has one or more uncommented `KEY=...` lines (an
    `export KEY=...` line counts, and keeps its `export`), the value on
    EVERY such line is replaced (the reader is last-wins, so a stale later
    duplicate must not survive); else `KEY=value` is appended. File absent: seed
    from `example_path` (keeping its helpful header), else start empty, then apply
    updates. A value is stripped of surrounding whitespace and any embedded newline;
    an empty result is dropped -- a blank value is not an instruction here, and a
    pasted key is trimmed before it could reach an Authorization header. An empty
    effective update set is a no-op (the file is not touched).

    The REMOVE_ENV_KEY sentinel as a value DELETES that key instead (#328, D-026's
    WYSIWYG deletion): every uncommented line assigning it goes away -- all
    duplicates, since one survivor would revive the value -- while a commented-out
    `# KEY=...` line is a user's note and stays, exactly as it does for an update.
    Deleting a key an absent file cannot hold writes nothing and creates no file, not
    even the example seed. Only the sentinel deletes, and it is matched by identity:
    the app's cleared-field gesture is resolved into it by resolve_env_save_updates,
    which is the one place that can tell a cleared field from an empty one.

    The value written is spelled by config.format_env_value, the reader's own inverse,
    so a value carrying a `#`, spaces or a leading quote survives read -> save -> read
    (#328) instead of being cut on the way back in; a value that grammar cannot carry
    raises ValueError from there, before anything is written.

    An existing-but-UNREADABLE target aborts the save (the read error propagates) so
    a locked/unreadable `.env` is never rewritten with just the new key, losing the
    rest; only a MISSING target is the normal seed-or-empty case. A UTF-8 BOM on the
    target is tolerated on read and dropped on write (the file heals). Atomic (temp
    file + os.replace); never logs or echoes a value."""
    path = Path(path)
    # Managed keys only, cleaned by the one shared rule -- the #291 pre-flight asks the
    # same question with it, and two copies of "does this save touch .env at all" could
    # drift apart. An empty effective set means this call must not even READ the file.
    updates = _clean_env_updates(updates)
    if not updates:
        return
    removes = {k for k, v in updates.items() if v is REMOVE_ENV_KEY}
    # Spell every value before a single line is composed, so a value this grammar
    # cannot carry aborts while nothing is prepared and nothing is on disk.
    writes = {k: config.format_env_value(v)
              for k, v in updates.items() if k not in removes}

    # Read the target byte-faithfully: newline="" keeps \r\n intact (S5) and
    # utf-8-sig drops a BOM if present (S6). A MISSING target falls back to the
    # example seed (best-effort -- the example is optional docs, not user data); any
    # OTHER read error on a PRESENT target propagates so the save aborts and the
    # existing file is never clobbered (B1).
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            lines = f.read().splitlines(keepends=True)
    except FileNotFoundError:
        if not writes:
            # Deletions only, and no file to delete from: there is nothing to do, and
            # least of all to CREATE one (the example seed included) for the sake of
            # removing a line that was never there.
            return
        lines = []
        if example_path is not None:
            try:
                with open(example_path, encoding="utf-8-sig", newline="") as f:
                    lines = f.read().splitlines(keepends=True)
            except (OSError, UnicodeDecodeError):
                # The example is optional shipped docs, not user data: one saved
                # as ANSI (#294) must not abort the user's save with a raw
                # exception -- it just means no seeded header.
                lines = []

    append_ending = "\r\n" if any(l.endswith("\r\n") for l in lines) else "\n"

    def _key_line_prefix(line, key):
        """The text before `key` on an uncommented `KEY=` / `export KEY=` line, or
        None when the line does not assign `key`. `export` is recognized because
        config.read_env_file honours it: without this, a rotation would append a
        second `KEY=` line and leave the stale `export` one behind (the #144
        deferral, closed with #269). The prefix -- the line's original indentation
        plus a normalized `export ` -- is carried into the rewritten line, so a
        shell-sourceable .env stays shell-sourceable (D-002: change the value, not
        the line's form)."""
        s = line.lstrip()
        if s.startswith("#") or "=" not in s:
            return None
        indent = line[:len(line) - len(s)]
        head = s.partition("=")[0].strip()
        if head[:6] == "export" and head[6:7] in (" ", "\t"):
            return indent + "export " if head[6:].lstrip() == key else None
        return indent if head == key else None

    def _line_ending(line):
        # Recognized line terminators are \n and \r\n only. A lone \r (classic-Mac) or
        # an exotic Unicode line separator that str.splitlines() also splits on would
        # return "" here and could glue a rewritten managed-key line onto the next --
        # a near-extinct trigger, deliberately not handled (#144 deferred).
        if line.endswith("\r\n"):
            return "\r\n"
        if line.endswith("\n"):
            return "\n"
        return ""

    # Replace the value on EVERY uncommented occurrence of a managed key (S3), drop
    # every occurrence of a deleted one; append only a key that never appeared.
    seen = set()
    new_lines = []
    for line in lines:
        matched, prefix = None, ""
        for k in updates:
            p = _key_line_prefix(line, k)
            if p is not None:
                matched, prefix = k, p
                break
        if matched in removes:
            continue
        if matched is not None:
            new_lines.append(f"{prefix}{matched}={writes[matched]}{_line_ending(line)}")
            seen.add(matched)
        else:
            new_lines.append(line)

    remaining = {k: v for k, v in writes.items() if k not in seen}
    if remaining and new_lines and not new_lines[-1].endswith(("\n", "\r\n")):
        new_lines[-1] = new_lines[-1] + append_ending
    for key, value in remaining.items():
        new_lines.append(f"{key}={value}{append_ending}")

    _atomic_write(path, "".join(new_lines))


# =============================================================================
# first-run mode decision (#163)
# =============================================================================
def env_has_key(env: dict) -> bool:
    """True iff a read_env() result holds a non-empty managed API key (Groq or
    Soniox). The single key-presence predicate behind resolve_first_run below, so the
    window-mode decision can never drift from what the app treats as "a key is
    stored". A blank/whitespace value is no key (the reader leaves a blank value out
    of the dict entirely, so "no key" has one shape); an unreadable/ANSI .env, which
    read_env already degrades to {}, reads as no key here too."""
    return bool(env.get("GROQ_API_KEY", "").strip()
                or env.get("SONIOX_API_KEY", "").strip())


def resolve_first_run(flag: bool, env: dict) -> bool:
    """The settings app's window-mode decision (#163). Open the first-run wizard when
    the explicit --first-run flag is set (thoughtborne.py's keyless-start hook) OR when
    no readable API key is stored yet (the installer hand-off and every other keyless
    open, which pass no flag); a stored key with no flag -> the plain settings dialog.
    Pure, so the whole decision is off-Windows testable. It changes only WHICH mode the
    window opens in, never how config is written -- respects D-002."""
    return bool(flag) or not env_has_key(env)


# =============================================================================
# personal_settings.json (surgical merge)
# =============================================================================
# The blocks whose `_comment` lead the managed-skeleton seeds on an absent-file
# write. The GUI-only `ui` block (#144) is app-managed too, but deliberately NOT
# in this set: it is written only when a `ui_language` is passed, so seeding an
# empty `ui` block on every first write (which adding it here would do) would
# violate the "no language changed -> no ui block" minimal-diff rule. See
# write_personal_settings. `push_to_talk` (#233) is the second block kept out for
# exactly that reason: its toggle writes only on demand, so listing it here would
# drop a push_to_talk block into every fresh install's first write.
MANAGED_BLOCKS = ("hotkeys", "defaults")

# The distinct "drop the pin" signal for write_personal_settings' `default_api`
# (#198, D-002 addendum). Matched by identity, never equality: an empty string ""
# is exactly the accidental value that must NOT be allowed to silently nuke a pin,
# so a sentinel object is both safer and self-documenting. Three-valued contract:
# None = leave defaults.api as found / REMOVE_API_PIN = drop it / a real id = write.
REMOVE_API_PIN = object()


def read_personal_settings(path) -> tuple:
    """Return (data, warning). A valid file -> (dict, None). A MISSING file ->
    ({}, None) (a first run is normal, not a warning). A file whose CONTENT cannot
    be carried -- corrupt JSON, a non-object top level, or bytes that do not decode
    as UTF-8 (an ANSI/cp1252 file) -> ({}, message): since D-026 all three are one
    whole-file loss class, and a deliberate save backs the file up before rewriting
    it (`save_personal_settings`; the message is what its backup log line carries).
    A present-but-UNREADABLE file (the bytes cannot be read at all -- locked /
    permission-denied) is NOT masqueraded as absent: the OSError propagates so a
    caller aborts instead of deciding anything over a file it could not even read
    (B1) -- the backup rename would fail on such a file too. A UTF-8 BOM is
    tolerated (utf-8-sig)."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return {}, None
    except UnicodeDecodeError as e:
        # An ANSI/cp1252 file holds intact data (German vocabulary) in the wrong
        # encoding -- exactly what the D-026 backup preserves, so this stopped being
        # an abort case (#263). A genuine OSError still propagates: unreadable
        # bytes leave nothing to decide over, and nothing a rename could save.
        return {}, f"personal_settings.json is not UTF-8 ({e}); a save backs it up"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return {}, f"personal_settings.json is not valid JSON ({e}); a save backs it up"
    if not isinstance(data, dict):
        return {}, "personal_settings.json is not a JSON object; a save backs it up"
    return data, None


def read_ptt_enabled(personal: dict) -> bool:
    """True iff a personal_settings dict switches push-to-talk ON, by exactly the
    rule the running tool applies (config.py): the value must be a real JSON boolean.
    A quoted "yes", a 1, a missing key, a missing or non-dict block -> False, which is
    what the tool does too (it warns and keeps the shipped default OFF). So the
    settings toggle can never show ON for a file the tool reads as OFF, and a hand-
    typed typo is surfaced as the off state it actually produces. Pure -> off-Windows
    testable."""
    block = personal.get("push_to_talk")
    if not isinstance(block, dict):
        return False
    value = block.get("enabled")
    return value if isinstance(value, bool) else False


def _managed_skeleton(example_path) -> dict:
    """A minimal personal_settings dict with only the managed blocks, each
    carrying its `_comment` lead from the example (so the written file stays
    self-documenting) but NONE of the example's placeholder `vocabulary` -- those
    dummy terms would otherwise become live Soniox vocabulary (D-002, a real data
    bug)."""
    example = {}
    if example_path is not None:
        # Best-effort: the example is optional docs, not user data. Unlike the
        # target file, an unreadable example must NOT abort the save (B1 is about
        # protecting the user's own file), so swallow its read error here.
        try:
            example, _ = read_personal_settings(example_path)
        except OSError:
            example = {}
    skeleton = {}
    for block in MANAGED_BLOCKS:
        src = example.get(block)
        if isinstance(src, dict) and "_comment" in src:
            skeleton[block] = {"_comment": src["_comment"]}
        else:
            skeleton[block] = {}
    return skeleton


def _merge_personal_settings(found: dict, *, hotkeys_effective, default_api,
                             example_path=None, ui_language=None,
                             ptt_enabled=None) -> dict:
    """The one merge behind both write lanes: `found` (a read_personal_settings
    result; an empty one starts from the managed skeleton) with the managed
    surfaces applied per the contracts write_personal_settings documents.
    Extracted (#263) so `save_personal_settings` can compose its result in memory
    BEFORE the D-026 backup rename; the merge itself is the unchanged D-002
    surgical merge. May mutate `found` -- both callers pass a fresh read.
    Replaced are ONLY the managed surfaces:
      - hotkeys (three-valued like ui.language / defaults.api): `hotkeys_effective`
        is written as the diff vs `config.DEFAULT_HOTKEYS` in #55's partial-override
        shape; an empty diff leaves only the block's `_comment` (or drops the block),
        a leading `_comment` is preserved. `hotkeys_effective=None` leaves the hotkeys
        block exactly as found -- the ui.language-only write (a settings-app language
        toggle, D-014) passes None so a toggle never rewrites or normalizes hotkeys.
      - defaults.api (on demand, like ui.language below; three-valued, #193/#198,
        D-008/D-002): `default_api=None` means "no engine was picked this save" and
        leaves the file's `defaults.api` exactly as found -- untouched, unread,
        uncorrected. `REMOVE_API_PIN` force-drops the key (remember-mode chosen over
        a pin). A real engine id is written verbatim, INCLUDING when it equals
        `config.BUILTIN_DEFAULT_API` -- the two-mode "always start with X" fixed pin
        is exactly the frozen copy the old diff-against-the-default rule avoided, so
        that gate is gone here. Any sibling keys + `_comment` in `defaults` stay in
        every case; the block drops if it becomes empty.
      - ui.language (#144, GUI-only): written ONLY when `ui_language` is `"de"` or
        `"en"`. `ui_language=None` leaves any `ui` block exactly as found (and
        creates none), so a user who never toggled the language keeps a clean file.
        When set, any other keys in `ui` (and its `_comment`) are preserved; a
        brand-new `ui` block gets the example's `_comment` lead if available.
      - push_to_talk.enabled (#233, on demand like ui.language above; three-valued,
        D-002 addendum): written ONLY when `ptt_enabled` is not `None` -- callers pass
        a bool or `None` (resolve_ptt_save_signal returns exactly that), anything else
        is coerced with bool(). `None` -- the untouched toggle -- leaves the whole
        `push_to_talk` block exactly as found and creates none, a hand-typed invalid
        `enabled` included (the tool warns about that at every start, the honest way
        to surface a typo). A write touches only `enabled`, so the block's `_comment`
        and every sibling (`trigger`, `insert`, the three thresholds) survive and
        switching the feature off and on again can never cost a hand-tuned value;
        config's defaults fill the rest, so a freshly created block needs no other
        key. Unlike `defaults.api` there is no
        removal sentinel -- there is no "delete the block" state to express.
    Every unmanaged block (vocabulary / soniox_endpointing) and every `_comment` is
    preserved untouched; `push_to_talk` is preserved untouched too unless
    `ptt_enabled` is set, which touches only its `enabled` key. Leave-as-found
    covers a hand-typed INVALID value too when its signal is None -- the deliberate
    lane (`save_personal_settings`) converts such a None into the shown default
    first (D-026's normalize-on-save), backup-covered; this merge stays literal."""
    data = found if found else _managed_skeleton(example_path)

    # ---- hotkeys: write only the diff vs the shipped defaults -----------------
    # hotkeys_effective=None means "leave the hotkeys block exactly as found"
    # (symmetric with default_api=None / ui_language=None): the ui.language-only write
    # a settings-app language toggle makes (D-014) passes None so a toggle never
    # rewrites or normalizes the user's hotkeys.
    # Preserve every JSON-comment key (any '_'-prefixed key -- apply_hotkey_overrides
    # skips all of them, so a user may park e.g. "_disabled_start_recording"); only
    # the real action entries are replaced by the fresh diff (N7).
    if hotkeys_effective is not None:
        hk_block = data.get("hotkeys")
        preserved_hk = ({k: v for k, v in hk_block.items() if k.startswith("_")}
                        if isinstance(hk_block, dict) else {})
        diff = hotkeys_diff_vs_default(hotkeys_effective, config.DEFAULT_HOTKEYS)
        new_hk = dict(preserved_hk)
        new_hk.update(diff)
        if new_hk:
            data["hotkeys"] = new_hk
        else:
            data.pop("hotkeys", None)

    # ---- defaults.api: on demand; three-valued signal (#193/#198, D-008/D-002) ---
    # None (the "engine field untouched" case) leaves the key exactly as found:
    # rewriting it would run the removed diff rule over a value the user never
    # touched and could delete a hand-written `"api": "soniox-live"` pin on an
    # unrelated save -- and with a remembered engine present that changes the next
    # start. Leaving it also preserves an INVALID value here (the deliberate lane,
    # save_personal_settings, converts that None to REMOVE_API_PIN first --
    # normalize-on-save, D-026). REMOVE_API_PIN force-drops the key
    # (remember-mode chosen over a pin). A real id is written verbatim, the built-in
    # default included -- "always start with X" is the frozen copy that used to be
    # diffed away; the diff-against-the-default gate is intentionally gone here.
    if default_api is not None:
        def_block = data.get("defaults")
        new_def = dict(def_block) if isinstance(def_block, dict) else {}
        if default_api is REMOVE_API_PIN:
            new_def.pop("api", None)
        else:
            new_def["api"] = default_api
        if new_def:
            data["defaults"] = new_def
        else:
            data.pop("defaults", None)

    # ---- ui.language: GUI-only, written only on demand (#144) ------------------
    # None -> leave any existing `ui` block exactly as found (and create none), so
    # a no-language-change session leaves a clean file. Set -> merge into the
    # existing block (keeping its `_comment` and any siblings) or seed a fresh one
    # with the example's `_comment` lead. The dictation tool ignores this block.
    if ui_language is not None:
        ui_block = data.get("ui")
        if isinstance(ui_block, dict):
            new_ui = dict(ui_block)
        else:
            new_ui = {}
            comment = _example_block_comment(example_path, "ui")
            if comment is not None:
                new_ui["_comment"] = comment
        new_ui["language"] = ui_language
        data["ui"] = new_ui

    # ---- push_to_talk.enabled: on demand, three-valued (#233, D-002 addendum) ---
    # None -> leave the whole block exactly as found (and create none), so a save
    # that never touched the toggle keeps the file byte-identical and a hand-typed
    # invalid `enabled` survives HERE (the deliberate lane converts that None to
    # the shown False first -- normalize-on-save, D-026). Anything else -- in
    # practice the bool resolve_ptt_save_signal returns -- writes ONLY
    # `enabled`: the `_comment` and every sibling the user hand-tuned there --
    # trigger, insert, the three thresholds -- are carried over, so disabling and
    # re-enabling never costs a timing. A non-dict block is replaced by a fresh one
    # (the ui rule above): the tool ignores such a block entirely, so nothing of
    # value is lost.
    if ptt_enabled is not None:
        ptt_block = data.get("push_to_talk")
        if isinstance(ptt_block, dict):
            new_ptt = dict(ptt_block)
        else:
            new_ptt = {}
            comment = _example_block_comment(example_path, "push_to_talk")
            if comment is not None:
                new_ptt["_comment"] = comment
        new_ptt["enabled"] = bool(ptt_enabled)
        data["push_to_talk"] = new_ptt

    return data


def write_personal_settings(path, *, hotkeys_effective, default_api,
                            example_path=None, ui_language=None,
                            ptt_enabled=None) -> None:
    """The raw merge-write: read the target, apply `_merge_personal_settings`
    above (its docstring carries the per-surface contracts), serialize with
    json.dumps(indent=2, ensure_ascii=False) + trailing newline, write atomically
    (temp file + os.replace).

    A MISSING target is the normal first-run case (read -> {}), so only the
    managed skeleton is written. A present-but-UNREADABLE target makes the read
    raise, which propagates so the save aborts -- the file is never skeletoned
    over and its vocabulary is never destroyed (B1). A target the read cannot
    CARRY -- corrupt JSON, a non-object top level, undecodable bytes -- raises
    ValueError instead of skeletoning over it: since D-026 the only path that may
    write over such a file is the backup lane (`save_personal_settings` below),
    so this writer refusing outright is what makes "a save that discards content
    without a successful backup rename" structurally impossible rather than a
    discipline. In practice the raise is a TOCTOU backstop and a guard against
    future direct callers: the silent language toggle gates itself off such a
    file before reaching here (#239/D-014), and the deliberate actions go through
    the backup lane."""
    path = Path(path)
    existing, warning = read_personal_settings(path)
    if warning is not None:
        raise ValueError(warning)
    data = _merge_personal_settings(existing, hotkeys_effective=hotkeys_effective,
                                    default_api=default_api,
                                    example_path=example_path,
                                    ui_language=ui_language,
                                    ptt_enabled=ptt_enabled)
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _owned_losses(found: dict) -> list:
    """What a deliberate save CANNOT carry from `found` into its result, per
    entry and signal-independent -- D-026's per-entry loss classes. The
    discriminator is "would the tool's loader warn and fall back?": for the
    hotkeys block the loader itself answers (its warnings ARE the losses --
    unknown actions, bad shapes, unparseable combos, dead combos, collision
    losers -- so the two can never drift), for the other owned surfaces the same
    validity rules the app's load applies. Meaning-preserving normalization is NOT
    a loss: canonical spellings, the D-024 one-element-list collapse, an entry
    equal to its default falling out of the diff, BOM healing, re-serialization
    (the accepted D-020 edge). Neither is a deliberate change the caller signals
    (a rebind, a dropped pin, a reset over valid values): "cannot carry" is
    inability, not instruction. Pure -> off-Windows testable."""
    losses = []
    hk = found.get("hotkeys")
    if hk is not None:
        if isinstance(hk, dict):
            losses.extend(config.apply_hotkey_overrides(
                config.DEFAULT_HOTKEYS, hk)[1])
        else:
            losses.append("hotkeys: not a JSON object")
    dblk = found.get("defaults")
    if dblk is not None:
        if isinstance(dblk, dict):
            if "api" in dblk and dblk["api"] not in config.AVAILABLE_APIS:
                losses.append(f"defaults.api: unknown engine {dblk['api']!r}")
        else:
            losses.append("defaults: not a JSON object")
    ptt = found.get("push_to_talk")
    if ptt is not None:
        if isinstance(ptt, dict):
            if "enabled" in ptt and not isinstance(ptt["enabled"], bool):
                losses.append(f"push_to_talk.enabled: {ptt['enabled']!r} "
                              "is not a JSON boolean")
        else:
            losses.append("push_to_talk: not a JSON object")
    ui = found.get("ui")
    if ui is not None:
        if isinstance(ui, dict):
            if "language" in ui and ui["language"] not in ("de", "en"):
                losses.append(f"ui.language: {ui['language']!r} is not 'de' or 'en'")
        else:
            losses.append("ui: not a JSON object")
    return losses


def _normalized_signals(found, default_api, ui_language, ptt_enabled):
    """D-026's normalize-on-save, expressed entirely through the existing
    three-valued signal contracts so the merge needs no change: wherever the
    found value is one the owned surface cannot carry AND the caller passed the
    leave-as-found None, the signal becomes the shown default -- the value the
    readers effectively produce for the broken entry (no pin / OFF / English),
    which is what the window displayed for it. An actively moved control
    (signal != None) replaces the value anyway; the loss is recorded either way
    (`_owned_losses` is signal-independent), so the backup keeps the trace.
    hotkeys need no conversion: a deliberate save passes the full effective dict
    and the diff rewrite normalizes the block. Pure."""
    dblk = found.get("defaults")
    if default_api is None and dblk is not None and (
            not isinstance(dblk, dict)
            or ("api" in dblk and dblk["api"] not in config.AVAILABLE_APIS)):
        default_api = REMOVE_API_PIN
    ui = found.get("ui")
    if ui_language is None and ui is not None and (
            not isinstance(ui, dict)
            or ("language" in ui and ui["language"] not in ("de", "en"))):
        ui_language = "en"
    ptt = found.get("push_to_talk")
    if ptt_enabled is None and ptt is not None and (
            not isinstance(ptt, dict)
            or ("enabled" in ptt and not isinstance(ptt["enabled"], bool))):
        ptt_enabled = False
    return default_api, ui_language, ptt_enabled


def _backup_aside(path):
    """Rename `path` to its D-026 backup name
    (`<stem>.backup-YYYY-MM-DD_HHMMSS<suffix>`, `-2`/`-3`/... on collision) and
    return the backup Path. None when the file vanished between probe and rename
    (nothing left to lose -- what was deleted externally no rename can save). Any
    other rename failure propagates: no backup, no overwrite (D-026).

    The exists-loop instead of a no-clobber primitive (3.10 has none portable):
    on Windows os.rename raises FileExistsError over an existing target (a safe
    abort), on POSIX it would clobber -- but this module is the single writer and
    the app is single-instance (D-009), so the window between the check and the
    rename is practically empty. os.replace would be wrong here: it clobbers
    everywhere."""
    path = Path(path)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    candidate = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}.backup-{stamp}-{n}{path.suffix}")
        n += 1
    try:
        os.rename(path, candidate)
    except FileNotFoundError:
        return None
    return candidate


def save_personal_settings(path, *, hotkeys_effective, default_api,
                           example_path=None, ui_language=None,
                           ptt_enabled=None) -> tuple:
    """The D-026 write lane for the two deliberate actions, Save and Reset:
    backup before loss, no backup -> no overwrite. Returns (backup, losses):
    `backup` is the Path the found file was renamed to (None when no backup was
    owed, or when the file vanished between probe and rename), `losses` the
    reasons one was owed -- the whole-file warning, or the per-entry loader
    warnings -- for the caller to log (this module never logs, D-026 gives the
    log duty to the app).

    The lane, in order:
      1. Probe the target FRESH (the #239 pattern): read_personal_settings at
         write time, so a file that broke while the window was open is caught.
         An OSError (locked / permission-denied) propagates -> abort before
         anything happened.
      2. A whole-file warning (corrupt JSON, non-object, not UTF-8) is the loss;
         otherwise `_owned_losses` lists what this save cannot carry, and
         `_normalized_signals` converts leave-as-found Nones over invalid owned
         entries into the shown defaults -- safe now because the backup keeps
         the trace.
      3. The result is composed IN MEMORY, then -- only when there are losses --
         the found file is renamed aside (`_backup_aside`); a failed rename
         propagates: no backup, no overwrite, save aborted.
      4. The atomic write. If THAT fails after a successful backup, the backup
         is renamed back (best-effort) and the original error re-raised: an
         aborted save must not leave the target missing from its place -- the
         savefail dialog's "everything is still there" stays true of the file,
         and a failed rollback still leaves the data safe in the backup.

    `hotkeys_effective` must be the FULL effective dict here (never None): both
    deliberate actions have one to pass -- the window state, the shipped
    defaults -- and the hotkey loss class assumes the block is rewritten as its
    diff. A normal save over a healthy, fully-understood file creates no backup
    (D-026); the silent language toggle never takes this lane at all -- its gate
    refuses any file it cannot carry (write_ui_language, D-014/#239)."""
    path = Path(path)
    found, warning = read_personal_settings(path)
    if warning is not None:
        losses = [warning]
    else:
        losses = _owned_losses(found)
        default_api, ui_language, ptt_enabled = _normalized_signals(
            found, default_api, ui_language, ptt_enabled)
    data = _merge_personal_settings(found, hotkeys_effective=hotkeys_effective,
                                    default_api=default_api,
                                    example_path=example_path,
                                    ui_language=ui_language,
                                    ptt_enabled=ptt_enabled)
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    backup = _backup_aside(path) if losses else None
    try:
        _atomic_write(path, content)
    except BaseException:
        if backup is not None:
            try:
                os.rename(backup, path)
            except OSError:
                pass
        raise
    return backup, losses


def write_ui_language(path, language, example_path=None) -> bool:
    """The D-014 language-toggle persist: the ui.language-only surgical write above,
    gated so the SILENT lane never writes over a file it cannot carry (#239) --
    and, since D-026, never takes the backup lane either: rescuing a broken file
    belongs to the deliberate actions (save_personal_settings), not to a click the
    user does not read as saving. So a warning -- corrupt JSON, a non-object top
    level, undecodable bytes -- means: return False, file byte-untouched, no
    backup created.

    The corruption is probed FRESH on every call rather than carried from load time:
    a file that breaks while the window is open is protected too, and one that is
    FIXED while it is open starts persisting again. The cost is one extra read of a
    tiny file per toggle, and a millisecond-wide TOCTOU window between probe and
    write -- irrelevant against today's unconditional clobber.

    A MISSING file is not a warning and stays the normal first-run lane (managed
    skeleton + ui.language), so a wizard toggle still persists. A present-but-
    unreadable one (the bytes cannot be read, OSError) raises out of the probe,
    exactly as the write itself would: swallowing that belongs to the caller's
    best-effort lane (D-014), not here.

    The signature is deliberately narrow -- no hotkeys, no engine pin, no push-to-talk:
    the silent lane structurally cannot write anything but ui.language. Returns True
    iff the language was written."""
    _, warning = read_personal_settings(path)
    if warning is not None:
        return False
    write_personal_settings(path, hotkeys_effective=None, default_api=None,
                            ui_language=language, example_path=example_path)
    return True


# =============================================================================
# read-failure probes: the save pre-flight (#291), the no-key claim (#294)
# =============================================================================
def _probe_env_readable(path) -> None:
    """Raise what write_env's own guard read raises for an unreadable `.env` -- an
    OSError (locked / permission-denied) or a UnicodeDecodeError (an ANSI/cp1252 file)
    -- and return quietly for a MISSING one, which is that writer's normal seed case.
    The open() is deliberately write_env's open(), byte for byte (utf-8-sig,
    newline=""): this exists to PREDICT that read, so a change to one is a change to
    both, and test_settings_io.py pins that they still agree."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            f.read()
    except FileNotFoundError:
        return


def unreadable_save_target(*, env_path, env_updates, ps_path):
    """The pre-flight an explicit save runs BEFORE it writes anything (#291): return
    `(path, error)` for the first file this save would abort on, else `None`.
    Nothing is written either way. For `.env` that is bytes that cannot be read OR
    UTF-8-decoded (that file stays outside the D-026 backup model); for
    `personal_settings.json` only bytes that cannot be READ at all (OSError) --
    an undecodable or corrupt one is no abort case anymore, it rides the backup
    lane (save_personal_settings, D-026).

    Both writers abort on the targets probed here rather than clobber them (B1,
    D-002/D-026) -- write_env by letting every read error but FileNotFoundError
    propagate, the personal-settings lane through read_personal_settings, which
    this function calls itself so the two can never drift. What that abort cannot
    do is tell the CALLER apart from a write failure (`_atomic_write` raises
    OSError too) or name the file, and it fires only once `.env` has already been
    rewritten. Asking first buys both and keeps the save all-or-nothing, which is
    what lets the dialog say that nothing was changed.

    `.env` is probed ONLY when this save would actually touch it: write_env is a no-op
    for an empty effective update set, so key fields that carry no instruction must not
    let an unreadable `.env` block a save that never touches it -- a pure hotkey save
    over a cp1252 `.env` works today and keeps working. `_clean_env_updates`,
    write_env's own rule rather than a second copy, answers that -- and it counts a
    pending DELETION as a touch (#328), which is how D-026's "a present-but-unreadable
    or undecodable .env still aborts the save" keeps holding for removals too. `.env`
    comes first because it is written first, so the file named is the one that would
    have failed.

    This is not a lock: a file that turns unreadable between the probe and the write
    falls back to the writers' own abort and the caller's write-failure message --
    exactly what happens today. The example files the writers may seed from are
    deliberately out of scope; they are shipped docs, not the user's data."""
    if _clean_env_updates(env_updates):
        try:
            _probe_env_readable(env_path)
        except Exception as e:
            return Path(env_path), e
    try:
        read_personal_settings(ps_path)
    except Exception as e:
        return Path(ps_path), e
    return None


def env_read_failure(path):
    """Return the error a present-but-unreadable `.env` at `path` fails on, else
    `None` -- the question the no-key confirmation has to ask before it speaks (#294).

    `read_env` degrades a locked or non-UTF-8 `.env` to `{}`, exactly like a missing
    one. That is right for a pre-fill helper (the GUI simply shows empty fields), but
    it leaves the save's no-key dialog claiming "no key is entered, and none was
    found" over a file that may hold the user's only key -- a statement that is
    not merely incomplete but false, which weighs heavier in a tool whose first
    principle is reliability. A caller that gets an error here says instead that the
    file could not be read. A MISSING `.env` is deliberately not a failure: that is
    the genuine "none was found".

    Predicts the same read as the #291 pre-flight (`_probe_env_readable`, itself
    write_env's guard read byte for byte) rather than opening a second one, so the
    two branches of one save can never disagree about whether that file is readable.
    Never raises: an unreadable file is this function's answer, not its accident."""
    try:
        _probe_env_readable(path)
    except Exception as e:
        return e
    return None


def resolve_engine_save_signal(*, mode_now, mode_loaded, engine_now, engine_loaded):
    """Derive the on-save `defaults.api` signal for the #198 two-mode control -- the
    value the app hands `save_personal_settings` as `default_api`: `None` (leave
    `defaults.api` as found), `REMOVE_API_PIN` (drop the pin), or an engine id (write
    it verbatim, the built-in default included).

    Pure so the whole table is off-Windows testable -- the GUI itself is hands-on
    only, and this is the riskiest logic in the field. `mode` is "fixed" or
    "remember"; `engine_*` is the fixed-picker engine id.

    Fixed-mode writes the pin whenever the mode or the chosen engine actually changed
    (an untouched fixed pin stays byte-identical via `None`). Leaving a pin for
    remember-mode drops it, so the untouched memory keeps deciding, truthfully; an
    untouched remember save writes nothing at all.

    Key-agnostic by signature (D-028): no key state reaches this decision, so a pin on
    an engine without a key is written like any other and resolves at the next start
    through the carousel (#40/#200). There is no memory signal either -- the settings
    app writes no engine memory at all (D-028 retired the #178 wizard preselect, its
    one memory write)."""
    if mode_now == "fixed":
        if mode_loaded != "fixed" or engine_now != engine_loaded:
            return engine_now
        return None
    # remember mode
    if mode_loaded == "fixed":
        return REMOVE_API_PIN
    return None


def resolve_ptt_save_signal(*, enabled_now, enabled_loaded):
    """The on-save push-to-talk signal for the write lanes' `ptt_enabled` (#233);
    the app hands it to `save_personal_settings`. `None` when the toggle still sits
    where it was loaded: the file's `push_to_talk` block is then left exactly as
    found, so a save about something else stays byte-identical there. Over a
    hand-typed INVALID `enabled` that None no longer means "it survives" -- the
    deliberate lane converts it into the shown default, OFF (D-026's
    normalize-on-save), backup-covered; only the raw merge stays literal there.
    A real change returns the new bool, which writes only `enabled`.

    Pure, so the whole table is off-Windows tested (the GUI is hands-on only).
    Deliberately NOT symmetric with resolve_engine_save_signal's REMOVE sentinel:
    there is no "delete the block" state to express here."""
    if bool(enabled_now) == bool(enabled_loaded):
        return None
    return bool(enabled_now)


def _example_block_comment(example_path, block):
    """The `_comment` lead of `block` in the example file, or None. Best-effort
    (the example is optional docs, not user data), mirroring _managed_skeleton: an
    unreadable/absent example never aborts a save, it just means no seeded comment."""
    if example_path is None:
        return None
    try:
        example, _ = read_personal_settings(example_path)
    except OSError:
        return None
    src = example.get(block)
    if isinstance(src, dict) and "_comment" in src:
        return src["_comment"]
    return None


def hotkeys_diff_vs_default(effective: dict, default: dict) -> dict:
    """Pure: {action: combo} for actions whose effective binding differs from the
    default, in #55's partial-override shape. The inverse of what
    apply_hotkey_overrides consumes, so a round-trip (write diff ->
    apply_hotkey_overrides on the same defaults) reproduces `effective`.

    `effective` is a post-loader set: action -> combo STRING, as
    apply_hotkey_overrides emits it and every caller hands it over (D-024). That
    contract is what normalize_combo's never-raise promise rests on -- it absorbs
    an unparseable string, not a value of another type."""
    diff = {}
    for action, default_value in default.items():
        eff_value = effective.get(action, default_value)
        if normalize_combo(eff_value) != normalize_combo(default_value):
            diff[action] = eff_value
    return diff


# =============================================================================
# hotkey combo helpers (pure; wrap hotkey_parse)
# =============================================================================
def normalize_combo(raw: str) -> str:
    """'Control + Alt + P' -> 'ctrl+alt+p' -- the canonical form
    apply_hotkey_overrides emits, from the one canonicalizer, so the diff
    compares bindings and not notations.

    Sits in the diff path (hotkeys_diff_vs_default) and must not raise there: an
    unparseable string falls back to the plain lowercase-and-strip, which
    compares it against itself as before."""
    try:
        return canonical_combo(raw)
    except HotkeyParseError:
        return "+".join(part.strip() for part in raw.lower().split("+"))


def validate_combo(raw: str) -> tuple:
    """(ok, message). Parses via parse_hotkey_lexical + classify_key. ok=False
    with a human message on an unparseable combo (no key / multiple keys), a
    key outside the static set (since D-023 classify_key knows only static and
    invalid, so config-time acceptance matches runtime registrability), or the
    one dead combo class -- ctrl with pause/scrolllock, shared with the JSON
    lane via hotkey_parse.dead_combo_reason (#325)."""
    if not isinstance(raw, str) or not raw.strip():
        return False, "empty combo"
    try:
        _mods, key = parse_hotkey_lexical(raw)
    except HotkeyParseError as e:
        return False, str(e)
    if classify_key(key) == KEY_INVALID:
        return False, f"unrecognized key '{key}'"
    reason = dead_combo_reason(_mods, key)
    if reason:
        return False, reason
    return True, ""


# Virtual keys that are themselves modifiers -- a keypress reporting one means
# only a modifier is down, there is no key to bind yet (the capture widget
# stays armed, silently). Windows Tk fills event.keycode with the WM_KEYDOWN
# wParam, which for modifiers is the generic VK (Shift 0x10, Ctrl 0x11, Alt
# 0x12); the side-specific codes (0xA0-0xA5) and the Win keys (0x5B/0x5C) are
# included defensively. Caps Lock (0x14) and Num Lock (0x90) are deliberately
# NOT here: they are no modifiers, and since #325 they report honestly as
# unbindable instead of being swallowed.
MODIFIER_VKS = frozenset({0x10, 0x11, 0x12, 0x5B, 0x5C,
                          0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5})


def decode_key_event(state_bits: int, keycode: int):
    """PURE decode of a Tk <KeyPress> event into a combo string (e.g.
    'ctrl+alt+p', or a bare 'home'), or None when the key is not bindable --
    a lone modifier included, since no modifier VK is a VK_MAP token.

    On Windows Tk, event.keycode IS the virtual-key code (Tk stores the
    WM_KEYDOWN wParam there), so capture and registration share
    hotkey_parse.VK_MAP by construction (#325) -- a capturable-but-
    unregistrable key cannot exist, and the numpad is capturable at all
    (Windows Tk has no KP_* keysyms). Off Windows the keycode is a hardware
    code, not a VK: the tests feed VK numbers as plain ints, and the real-
    event leg is Windows-only by design. Modifiers come from the TK_STATE_*
    bits, as before.

    Keycode-driven, AltGr (reported as Ctrl+Alt from the right-Alt key)
    decodes to the live ctrl+alt+<key> combo it is -- and fires as -- rather
    than being filtered on its symbol keysym; the old keysym lane and its
    parallel key table went with it (#325)."""
    token = VK_TO_TOKEN.get(keycode)
    if token is None:
        return None
    parts = []
    if state_bits & TK_STATE_CONTROL:
        parts.append("ctrl")
    if state_bits & TK_STATE_ALT:
        parts.append("alt")
    if state_bits & TK_STATE_SHIFT:
        parts.append("shift")
    parts.append(token)
    return "+".join(parts)


# =============================================================================
# presets
# =============================================================================
# The final researched F-key preset (Fork 2, #144). From the maintainer's
# 2026-07-21 study (local, not in the repo), verified zero-warning through
# apply_hotkey_overrides. Schema in one line: three F-keys, three families --
# F8 = engine, F9 = record, F10 = deliver; a BARE key is
# the daily op (F9 records, F10 delivers), CTRL is the important sibling case
# (cancel / send / switch engine), CTRL+ALT the rare/technical one (deliver without
# insert / via typing). Housekeeping (open_history / open_settings /
# test_transcription / exit_program) is kept identical to the shipped Ctrl+Alt scheme so switching preset
# means no relearning. Bare f8 is intentionally left unassigned -- reserved for a
# future push-to-talk hold key.
PRESET_FKEYS = {   # keys in the canonical DEFAULT_HOTKEYS order (D-019)
    "start_recording": "f9",
    "stop_recording_clipboard": "f10",
    "stop_recording_send": "ctrl+f10",
    "stop_recording_no_insert": "ctrl+alt+f9",
    "stop_recording_keyboard": "ctrl+alt+f10",
    "cancel_recording": "ctrl+f9",
    "retry_last_failed": "shift+f8",
    "switch_api": "ctrl+f8",
    "open_history": "ctrl+alt+6",
    "open_settings": "ctrl+alt+g",   # housekeeping stays on Ctrl+Alt (#164)
    "test_transcription": "ctrl+alt+t",
    "exit_program": "ctrl+alt+4",
}


def preset_ctrl_alt() -> dict:
    """The shipped Ctrl+Alt letter scheme (== config.DEFAULT_HOTKEYS), as a fresh
    copy the caller may mutate freely."""
    return copy.deepcopy(config.DEFAULT_HOTKEYS)


def preset_fkeys() -> dict:
    """The final researched F-key scheme (see PRESET_FKEYS), as a fresh copy the
    caller may mutate freely."""
    return copy.deepcopy(PRESET_FKEYS)


# =============================================================================
# atomic write
# =============================================================================
def _atomic_write(path, content: str) -> None:
    """Write `content` to `path` atomically: a temp file in the SAME directory
    (so os.replace is an atomic same-filesystem rename), fsync-free but flushed,
    then os.replace over the target. newline='' so the bytes are written exactly
    as composed (line endings preserved). The temp file is removed if anything
    fails; on success none is left behind."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
