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
    comment / blank / order preserved; an empty value is omitted so a blank field
    never clobbers a stored key.
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
unreadable target aborts the save (the read error propagates) rather than
clobbering it, and a UTF-8 BOM is tolerated on read and healed (dropped) on write.
"""

import copy
import json
import os
import tempfile
from pathlib import Path

import config
from hotkey_parse import (
    parse_hotkey_lexical, canonical_combo, combo_rejection, HotkeyParseError,
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
    """The part of `updates` write_env would actually write: unmanaged keys dropped,
    every value stripped of surrounding whitespace and of any embedded newline (a
    value must be a single .env line, and a pasted key is trimmed before it could
    reach an Authorization header, S4), and an empty result dropped -- a blank field
    must never clobber a stored key.

    An empty return therefore means "this save does not touch .env at all". Both
    write_env's no-op and unreadable_save_target's decision not to probe the file rest
    on that answer, which is why the rule lives here rather than inline in the writer:
    a pre-flight that answered it differently would block saves the writer never
    touches .env for."""
    cleaned = {}
    for k, v in updates.items():
        if k not in ENV_KEYS:
            continue
        value = str(v).replace("\r", "").replace("\n", "").strip()
        if value:
            cleaned[k] = value
    return cleaned


def write_env(path, updates: dict, *, example_path=None) -> None:
    """Set each managed KEY in `updates` in the `.env` at `path`, preserving every
    other line / comment / blank / key order and the file's existing line endings.
    For a KEY that already has one or more uncommented `KEY=...` lines (an
    `export KEY=...` line counts, and keeps its `export`), the value on
    EVERY such line is replaced (the reader is last-wins, so a stale later
    duplicate must not survive); else `KEY=value` is appended. File absent: seed
    from `example_path` (keeping its helpful header), else start empty, then apply
    updates. A value is stripped of surrounding whitespace and any embedded newline;
    an empty result is dropped -- a blank field must never clobber a stored key, and
    a pasted key is trimmed before it could reach an Authorization header. An empty
    effective update set is a no-op (the file is not touched).

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

    # Read the target byte-faithfully: newline="" keeps \r\n intact (S5) and
    # utf-8-sig drops a BOM if present (S6). A MISSING target falls back to the
    # example seed (best-effort -- the example is optional docs, not user data); any
    # OTHER read error on a PRESENT target propagates so the save aborts and the
    # existing file is never clobbered (B1).
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            lines = f.read().splitlines(keepends=True)
    except FileNotFoundError:
        lines = []
        if example_path is not None:
            try:
                with open(example_path, encoding="utf-8-sig", newline="") as f:
                    lines = f.read().splitlines(keepends=True)
            except OSError:
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

    # Replace the value on EVERY uncommented occurrence of a managed key (S3);
    # append only a key that never appeared.
    seen = set()
    new_lines = []
    for line in lines:
        matched, prefix = None, ""
        for k in updates:
            p = _key_line_prefix(line, k)
            if p is not None:
                matched, prefix = k, p
                break
        if matched is not None:
            new_lines.append(f"{prefix}{matched}={updates[matched]}{_line_ending(line)}")
            seen.add(matched)
        else:
            new_lines.append(line)

    remaining = {k: v for k, v in updates.items() if k not in seen}
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
    Soniox). The single key-presence predicate: the settings app's _had_stored_key
    and resolve_first_run below both flow from it, so the window-mode decision can
    never drift from what the app treats as "a key is stored". A blank/whitespace
    value is no key (mirrors write_env dropping a blank so it never clobbers a stored
    key); an unreadable/ANSI .env, which read_env already degrades to {}, reads as no
    key here too."""
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
# startup-engine preselection (#178)
# =============================================================================
def preselect_startup_api(groq_present: bool, soniox_present: bool) -> str:
    """Which startup engine matches the keys the user just entered -- a
    preselection only; the settings app still lets an explicit engine pick win. A
    Groq key with no Soniox key -> 'groq-large' (Groq Whisper Large v3), the same
    engine the running tool falls back to when Soniox is absent, so the
    preselection just makes explicit what the tool would do anyway (and "accurate"
    beats "fast" for the dictation quality bar). Every other case -- Soniox present,
    both keys, or neither -- keeps config.BUILTIN_DEFAULT_API (Soniox Live), the
    shipped default. Pure: reads and writes no file, so it stays off-Windows testable
    and respects D-002."""
    if groq_present and not soniox_present:
        return "groq-large"
    return config.BUILTIN_DEFAULT_API


# =============================================================================
# key-aware engine control (#201)
# =============================================================================
def engine_keyed(api, live_fields: dict, stored_env: dict) -> bool:
    """True iff engine `api` has a usable key for the settings app's key-aware engine
    control (#201): a non-blank live field for its backing .env var (config.API_KEY_ENV),
    or a key already stored there. `live_fields` and `stored_env` map {ENV_VAR: value}.
    A blank/whitespace live field falls back to the stored value -- a blank never
    clobbers a stored key (mirrors write_env), so an empty field on top of a stored key
    still counts as keyed. Delegates the var lookup + stored check to
    `config.engine_has_key` (the #200 console primitive), so the settings control and
    the console lineup can never disagree on 'keyed'. An engine outside
    `config.API_KEY_ENV` -> False (defensive). Pure -> off-Windows testable."""
    var = config.API_KEY_ENV.get(api)
    if not var:
        return False
    live = live_fields.get(var, "")
    if live and live.strip():
        return True
    return config.engine_has_key(api, stored_env)


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
    ({}, None) (a first run is normal, not a warning). Corrupt JSON / a non-object
    top level (the bytes read fine, the content is just invalid) -> ({}, message) so
    the GUI can warn 'your settings file is unreadable, saving will overwrite it'.
    A present-but-UNREADABLE file (the bytes cannot be read at all -- locked /
    permission-denied) is NOT masqueraded as absent: the OSError propagates so a
    caller that would otherwise overwrite it aborts instead, protecting the user's
    vocabulary (B1). An encoding-undecodable file (ANSI/cp1252 -- its German
    vocabulary is intact, just in the wrong encoding) is recoverable data too, so its
    UnicodeDecodeError propagates the same way -> abort; this is distinct from a
    corrupt-but-utf-8 JSON body, which is unrecoverable and takes the warn-then-
    overwrite branch below. A UTF-8 BOM is tolerated (utf-8-sig). The distinction is
    simply whether the bytes could be read + decoded at all."""
    path = Path(path)
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return {}, None
    # A genuine OSError (unreadable bytes / locked file) and a UnicodeDecodeError (an
    # ANSI/cp1252 file whose data is intact but not utf-8 decodable) are deliberately
    # NOT caught here: both propagate so write_personal_settings aborts rather than
    # skeletoning over recoverable user data and destroying its vocabulary (B1). Only
    # a corrupt-but-utf-8 JSON body (read fine, invalid JSON) takes the warn-then-
    # overwrite branch below.
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return {}, f"personal_settings.json is unreadable ({e}); saving will overwrite it"
    if not isinstance(data, dict):
        return {}, "personal_settings.json is not a JSON object; saving will overwrite it"
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


def write_personal_settings(path, *, hotkeys_effective, default_api,
                            example_path=None, ui_language=None,
                            ptt_enabled=None) -> None:
    """Merge-write. Load the existing dict (or build a minimal skeleton from the
    managed blocks' example `_comment` leads -- never the placeholder vocabulary).
    Replace ONLY the managed blocks:
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
    `ptt_enabled` is set, which touches only its `enabled` key. Serialized
    json.dump(indent=2, ensure_ascii=False) + trailing newline. Atomic (temp file +
    os.replace).

    A MISSING target is the normal first-run case (read -> {}), so only the managed
    skeleton is written. A present-but-UNREADABLE target makes the read raise, which
    propagates out of here so the save aborts -- the file is never skeletoned over
    and its vocabulary is never destroyed (B1). A corrupt-JSON target (bytes read
    fine, invalid JSON) stays the deliberate warn-then-overwrite case:
    read_personal_settings already handed the GUI the warning, and a save replaces
    it with a clean managed skeleton -- which is the explicit Save's branch alone, and
    why the SILENT language toggle goes through write_ui_language below (#239)."""
    path = Path(path)
    existing, _warning = read_personal_settings(path)
    data = existing if existing else _managed_skeleton(example_path)

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
    # start. Leaving it also preserves an INVALID value (the tool warns at every
    # start, the honest way to surface a typo). REMOVE_API_PIN force-drops the key
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
    # invalid `enabled` survives (same stance as defaults.api). Anything else -- in
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

    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    _atomic_write(path, content)


def write_ui_language(path, language, example_path=None) -> bool:
    """The D-014 language-toggle persist: the ui.language-only surgical write above,
    gated so the SILENT lane can never take the warn-then-overwrite branch (#239).
    That branch belongs to the explicit Save alone (D-002): over a corrupt-but-
    decodable target (the bytes read fine, the JSON is invalid) write_personal_settings
    starts from a bare managed skeleton, which would destroy hand-written blocks --
    vocabulary, soniox_endpointing -- for a click the user does not read as saving. So
    a warning means: return False, file byte-untouched.

    The corruption is probed FRESH on every call rather than carried from load time:
    a file that breaks while the window is open is protected too, and one that is
    FIXED while it is open starts persisting again (which is what makes the warn
    strip's "until the file is fixed" sentence literally true). The cost is one extra
    read of a tiny file per toggle, and a millisecond-wide TOCTOU window between probe
    and write -- irrelevant against today's unconditional clobber.

    A MISSING file is not a warning and stays the normal first-run lane (managed
    skeleton + ui.language), so a wizard toggle still persists. A present-but-
    unreadable/undecodable one raises out of the probe, exactly as the write itself
    would: swallowing that belongs to the caller's best-effort lane (D-014), not here.

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
    `(path, error)` for the first file this save would abort on because its bytes
    cannot be read or UTF-8-decoded, else `None`. Nothing is written either way.

    Both writers already abort on such a target rather than clobber it (B1, D-002) --
    write_env by letting every read error but FileNotFoundError propagate,
    write_personal_settings through read_personal_settings, which this function calls
    itself so the two can never drift. What that abort cannot do is tell the CALLER
    apart from a write failure (`_atomic_write` raises OSError too) or name the file,
    and it fires only once `.env` has already been rewritten. Asking first buys both
    and keeps the save all-or-nothing, which is what lets the dialog say that nothing
    was changed.

    `.env` is probed ONLY when this save would actually write it: write_env is a no-op
    for an empty effective update set, so two blank key fields must not let an
    unreadable `.env` block a save that never touches it -- a pure hotkey save over a
    cp1252 `.env` works today and keeps working. `_clean_env_updates`, write_env's own
    rule rather than a second copy, answers that. `.env` comes first because it is
    written first, so the file named is the one that would have failed. A
    corrupt-but-decodable personal_settings.json is NOT a failure here: its bytes read
    fine, and it stays on D-002's warn-then-overwrite branch.

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


def resolve_engine_save_signal(*, mode_now, mode_loaded, engine_now, engine_loaded,
                               remember_display_now, remember_display_loaded):
    """Derive the two on-save engine signals for the #198 two-mode control.

    Returns `(default_api_signal, memory_api)`:
      - `default_api_signal` is handed to `write_personal_settings`: `None` (leave
        `defaults.api` as found), `REMOVE_API_PIN` (drop the pin), or an engine id
        (write it verbatim, the built-in default included).
      - `memory_api` is the engine to record via `engine_memory.write_last_engine`,
        or `None` to leave the memory untouched.

    Pure so the whole table is off-Windows testable -- the GUI itself is hands-on
    only, and this is the riskiest logic in the field. `mode` is "fixed" or
    "remember"; `engine_*` is the fixed-dropdown engine id; `remember_display_*` is
    the engine shown next to the remember radio, moved only by the #178 wizard
    preselect.

    Fixed-mode writes the pin whenever the mode or the chosen engine actually changed
    (an untouched fixed pin stays byte-identical via `None`), and never writes the
    memory -- the written pin is what makes the choice take effect. Leaving a pin for
    remember-mode drops it and leaves the memory alone (the untouched memory keeps
    deciding, truthfully). Staying in remember-mode writes the memory only when the
    wizard preselect actually moved the remembered display (D-008: the app's sole
    memory write); an otherwise untouched remember save touches neither file. The two
    return values are mutually exclusive by construction -- a `REMOVE` only fires when
    a pin was left (`mode_loaded == "fixed"`), a memory write only when staying in
    remember-mode -- so a save never does both.
    """
    if mode_now == "fixed":
        if mode_loaded != "fixed" or engine_now != engine_loaded:
            return engine_now, None
        return None, None
    # remember mode
    if mode_loaded == "fixed":
        return REMOVE_API_PIN, None
    if remember_display_now != remember_display_loaded:
        return None, remember_display_now
    return None, None


def resolve_fixed_entry_engine(*, mode_now, mode_loaded, shown_api,
                               live_fields, stored_env):
    """Where the fixed-mode engine selection lands when the user clicks a mode radio
    (#207): an engine id to move the selection to, or None to leave it where it is.

    A move happens only when entering fixed mode from a LOADED remember state while
    the shown engine has no usable key. That selection is then a seed nobody
    key-checked (the remembered engine or the built-in default), and the save right
    after the flip pins it unconditionally, so without the move "always start with"
    can store an engine that cannot start. The landing spot is the first keyed engine
    in config.AVAILABLE_APIS order -- the order the #200 startup fall-through walks
    and the #178 preselect picks from -- so the written pin names the engine the tool
    would have started anyway.

    Everything else returns None. `mode_loaded` is the file's state at load, never the
    click path: a pin loaded from the file is the user's own deliberate state, so
    re-entering fixed mode over it shows it unmoved, keyless or not (D-002 -- its
    flip-away-and-back has to leave defaults.api byte-identical). A keyed shown engine
    stays put; an all-keyless environment has nowhere to land (the save's own "no key"
    dialog owns that case); flipping to remember never moves anything. Key-awareness
    delegates to engine_keyed, so the move can never disagree with the radios' greying
    (#201).

    By construction a non-None return implies (mode_now, mode_loaded) ==
    ("fixed", "remember") -- the one cell where resolve_engine_save_signal writes the
    pin unconditionally -- so the move only ever changes WHICH engine an inevitable
    write records, never whether an untouched save writes (D-002). Pure ->
    off-Windows testable."""
    if mode_now != "fixed" or mode_loaded == "fixed":
        return None
    if engine_keyed(shown_api, live_fields, stored_env):
        return None
    for api in config.AVAILABLE_APIS:
        if engine_keyed(api, live_fields, stored_env):
            return api
    return None


def resolve_ptt_save_signal(*, enabled_now, enabled_loaded):
    """The on-save push-to-talk signal for write_personal_settings' `ptt_enabled`
    (#233). `None` when the toggle still sits where it was loaded: the file's
    `push_to_talk` block is then left exactly as found, so a save about something
    else stays byte-identical there and a hand-typed invalid `enabled` survives
    (D-002). A real change returns the new bool, which writes only `enabled`.

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
    """Pure: {action: value} for actions whose effective binding differs from the
    default, in #55's partial-override shape (value keeps the effective shape --
    str or list[str]). The inverse of what apply_hotkey_overrides consumes, so a
    round-trip (write diff -> apply_hotkey_overrides on the same defaults)
    reproduces `effective`."""
    diff = {}
    for action, default_value in default.items():
        eff_value = effective.get(action, default_value)
        if _norm_value(eff_value) != _norm_value(default_value):
            diff[action] = copy.deepcopy(eff_value)
    return diff


# =============================================================================
# hotkey combo helpers (pure; wrap hotkey_parse)
# =============================================================================
def normalize_combo(raw: str) -> str:
    """'Control + Alt + P' -> 'ctrl+alt+p' -- the canonical form
    apply_hotkey_overrides emits, from the one canonicalizer, so the diff
    compares bindings and not notations.

    Sits in the diff path (_norm_value -> hotkeys_diff_vs_default) and must not
    raise there: an unparseable string falls back to the plain
    lowercase-and-strip, which compares it against itself as before."""
    try:
        return canonical_combo(raw)
    except HotkeyParseError:
        return "+".join(part.strip() for part in raw.lower().split("+"))


def _norm_value(value):
    """Normalize a combo value (str or list[str]) for equality comparison."""
    if isinstance(value, list):
        return [normalize_combo(v) for v in value]
    return normalize_combo(value)


def validate_combo(raw: str) -> tuple:
    """(ok, message). Parses via parse_hotkey_lexical, then asks hotkey_parse's
    combo_rejection whether the result may be bound. ok=False with a human
    message on an unparseable combo (no key / multiple keys), an unrecognized
    key, or a modifier in front of a mouse button (#308). A special key (a single
    character like the umlaut) is accepted -- it resolves at runtime via
    VkKeyScanW, so config-time acceptance matches runtime registrability.

    The rejection rule itself lives in hotkey_parse because the JSON lane
    (config.apply_hotkey_overrides) must refuse exactly the same combos as the
    capture field here; the message is the detail the field shows."""
    if not isinstance(raw, str) or not raw.strip():
        return False, "empty combo"
    try:
        mods, key = parse_hotkey_lexical(raw)
    except HotkeyParseError as e:
        return False, str(e)
    reason = combo_rejection(mods, key)
    return (False, reason) if reason is not None else (True, "")


# Tk keysyms that are themselves modifiers -- a keypress reporting one means only
# a modifier is down, so there is no key to bind yet.
_MODIFIER_KEYSYMS = frozenset({
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Super_L", "Super_R", "Hyper_L", "Hyper_R",
    "ISO_Level3_Shift", "Caps_Lock", "Num_Lock", "Scroll_Lock", "Win_L", "Win_R",
})

# Tk keysym for the one German special key accepted as a hotkey key: 'ü'
# (udiaeresis). No shipped default uses it since the self-test moved to
# ctrl+alt+t (#211, D-012), but it stays offered so the capture widget can bind a
# user override onto ctrl+alt+ü. The other umlauts / ß are deliberately NOT
# offered here -- config.py's DEFAULT_HOTKEYS note and the personal_settings
# example both warn that a non-ASCII hotkey key other than 'ü' can get typed into
# some apps (N8), so the capture widget must not decode them into a bindable
# combo. classify_key treats 'ü' as KEY_SPECIAL (resolved at runtime via
# VkKeyScanW).
_SPECIAL_KEYSYMS = {
    "udiaeresis": "ü",
}


def _keysym_to_token(keysym: str):
    """Map a Tk keysym to a bindable key token, or None. ASCII letters -> lowercase
    letter; digits -> the digit; 'F1'..'F24' -> 'f1'..'f24'; the German special
    keys via _SPECIAL_KEYSYMS. Everything else (punctuation, AltGr-typed symbol
    keysyms like 'at'/'EuroSign', unknown names) -> None."""
    if not keysym:
        return None
    if len(keysym) == 1:
        # .isascii() guards the single-char letter branch: if a Tk build ever reports
        # an umlaut as a raw 1-char keysym (instead of the named 'adiaeresis'), it
        # must NOT become a bindable non-ASCII combo -- only 'ü' via the named
        # 'udiaeresis' is allowed (N8, consistent with _SPECIAL_KEYSYMS).
        if keysym.isalpha() and keysym.isascii():
            return keysym.lower()
        if keysym.isdigit():
            return keysym
        return None
    if keysym[0] in ("F", "f") and keysym[1:].isdigit():
        n = int(keysym[1:])
        return f"f{n}" if 1 <= n <= 24 else None
    return _SPECIAL_KEYSYMS.get(keysym)


def decode_key_event(state_bits: int, keysym: str, char: str):
    """PURE decode of a Tk <KeyPress> event into a combo string (e.g.
    'ctrl+alt+p', or a bare 'f9'), or None when only modifiers are down or the key
    is not bindable. Takes the raw event fields as plain int/str so it is unit-
    testable off-Windows with synthetic inputs -- the capture widget (Checkpoint
    2) just feeds it real `event.state` / `event.keysym` / `event.char`.

    Modifiers come from the TK_STATE_* bits. AltGr on QWERTZ
    (reported as Control+Alt from the right-Alt key) types symbols like @ \\ { }
    [ ] | euro ~ -- whose keysyms are non-bindable names ('at', 'EuroSign', ...)
    that _keysym_to_token maps to None, so those presses decode to None. This is
    the Tk-level equivalent of the project's AltGr filter; the umlaut 'ü'
    (keysym 'udiaeresis') is mapped explicitly and is never filtered.

    `char` (the produced glyph) is part of the Tk event contract and accepted for
    interface completeness; the decode itself is keysym-driven."""
    if keysym in _MODIFIER_KEYSYMS:
        return None
    token = _keysym_to_token(keysym)
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
# means no relearning. cancel_recording / exit_program keep single-element LISTS to
# match their list-shaped defaults (apply_hotkey_overrides preserves shape). Bare f8
# is intentionally left unassigned -- reserved for a future push-to-talk hold key.
PRESET_FKEYS = {   # keys in the canonical DEFAULT_HOTKEYS order (D-019)
    "start_recording": "f9",
    "stop_recording_clipboard": "f10",
    "stop_recording_send": "ctrl+f10",
    "stop_recording_no_insert": "ctrl+alt+f9",
    "stop_recording_keyboard": "ctrl+alt+f10",
    "cancel_recording": ["ctrl+f9"],
    "retry_last_failed": "shift+f8",
    "switch_api": "ctrl+f8",
    "open_history": "ctrl+alt+6",
    "open_settings": "ctrl+alt+g",   # housekeeping stays on Ctrl+Alt (#164)
    "test_transcription": "ctrl+alt+t",
    "exit_program": ["ctrl+alt+4"],
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
