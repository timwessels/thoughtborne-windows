"""
File IO and pure hotkey-combo helpers for the settings/onboarding app (#144).

The ONLY module that touches `.env` and `personal_settings.json`, plus the pure
hotkey-combo helpers the GUI's capture widget leans on. No tkinter, no network,
nothing Windows-bound: it reuses the ctypes-free `hotkey_parse` layer and
`config`'s pure constants, so `test_settings_io.py` runs on plain Python (the
`console_ui.py` + `test_console_ui.py` house style).

Write policy (the coming DECISIONS.md D-002): surgical merge, never a full
rewrite.
  - `.env` is edited line-wise -- only the two managed keys, every other line /
    comment / blank / order preserved; an empty value is omitted so a blank field
    never clobbers a stored key.
  - `personal_settings.json` keeps every unmanaged block and every `_comment`
    untouched; hotkeys are written as a diff against `config.DEFAULT_HOTKEYS` (the
    default scheme writes no hotkey entries) and `defaults.api` only when it
    differs from `config.BUILTIN_DEFAULT_API`. On an absent file only the managed
    blocks are written -- NEVER the example's placeholder `vocabulary` (its dummy
    terms would otherwise become live Soniox vocabulary, a real data bug). The
    GUI-only `ui` block (the settings app's own display language, #144) is a third
    managed block, but written only on demand: `write_personal_settings` touches it
    solely when passed a `ui_language`, so a user who never changed the language
    leaves no `ui` block behind (the dictation tool ignores it entirely).
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
    parse_hotkey_lexical, classify_key, HotkeyParseError, KEY_INVALID,
)

# ---- Tk event.state modifier bits (decode_key_event) -----------------------
# The capture widget (Checkpoint 2) decodes real Tk <KeyPress> events with these.
# Shift/Control are stable across platforms; the Alt bit on Windows Tk is NOT the
# X11 Mod1 (0x0008) -- Windows Tk reports Alt high (0x20000 is the commonly-cited
# value). They are module-level (not function-local) so the off-Windows test can
# drive decode_key_event with the exact same constants the widget will, avoiding
# drift. VERIFY HANDS-ON on Windows (#144 test issue): confirm the Alt bit, and
# that AltGr on QWERTZ reports Control+Alt.
TK_STATE_SHIFT   = 0x0001
TK_STATE_CONTROL = 0x0004
TK_STATE_ALT     = 0x20000


# =============================================================================
# .env
# =============================================================================
ENV_KEYS = ("GROQ_API_KEY", "SONIOX_API_KEY")   # the only keys this app manages


def read_env(path) -> dict:
    """Return {KEY: value} for the managed keys found as uncommented KEY=value
    lines. A missing file or a missing key -> that key absent from the dict.
    Never raises on a malformed line (it is skipped). The value is the raw text
    right of the first '=', stripped of surrounding whitespace; not evaluated or
    unquoted. A leading UTF-8 BOM is tolerated (utf-8-sig). An undecodable file (an
    ANSI/cp1252 .env, e.g. a German umlaut in a comment) -> {}: this pre-fill helper
    can't pre-fill it (the GUI shows empty fields) but must never raise -- the write
    path (write_env) separately aborts on such a file rather than clobber it."""
    result = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return result
    except UnicodeDecodeError:
        # An ANSI/cp1252 .env is intact but not utf-8 decodable; read_env is a
        # never-raises pre-fill helper, so degrade to {} (can't pre-fill) instead of
        # crashing the GUI. UnicodeDecodeError is a ValueError, NOT an OSError, so the
        # clause above would miss it.
        return result
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in ENV_KEYS:
            result[key] = value.strip()
    return result


def write_env(path, updates: dict, *, example_path=None) -> None:
    """Set each managed KEY in `updates` in the `.env` at `path`, preserving every
    other line / comment / blank / key order and the file's existing line endings.
    For a KEY that already has one or more uncommented `KEY=...` lines, the value on
    EVERY such line is replaced (python-dotenv is last-wins, so a stale later
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
    # Managed keys only; strip surrounding whitespace and any embedded newline (a
    # value must be a single .env line). Drop an empty result so a blank field never
    # clobbers a stored key, and so a pasted key is trimmed before it could reach an
    # Authorization header (S4).
    cleaned = {}
    for k, v in updates.items():
        if k not in ENV_KEYS:
            continue
        value = str(v).replace("\r", "").replace("\n", "").strip()
        if value:
            cleaned[k] = value
    updates = cleaned
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

    def _is_key_line(line, key):
        # An `export KEY=value` line is deliberately NOT matched (the parse yields
        # "export KEY" != key): still functional via python-dotenv (last-wins on the
        # bare KEY= line the writer appends), only a cosmetic stale line remains.
        # Deferred (#144).
        s = line.lstrip()
        if s.startswith("#") or "=" not in s:
            return False
        return s.partition("=")[0].strip() == key

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
        matched = next((k for k in updates if _is_key_line(line, k)), None)
        if matched is not None:
            new_lines.append(f"{matched}={updates[matched]}{_line_ending(line)}")
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
# personal_settings.json (surgical merge)
# =============================================================================
# The blocks whose `_comment` lead the managed-skeleton seeds on an absent-file
# write. The GUI-only `ui` block (#144) is app-managed too, but deliberately NOT
# in this set: it is written only when a `ui_language` is passed, so seeding an
# empty `ui` block on every first write (which adding it here would do) would
# violate the "no language changed -> no ui block" minimal-diff rule. See
# write_personal_settings.
MANAGED_BLOCKS = ("hotkeys", "defaults")


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


def write_personal_settings(path, *, hotkeys_effective: dict, default_api: str,
                            example_path=None, ui_language=None) -> None:
    """Merge-write. Load the existing dict (or build a minimal skeleton from the
    managed blocks' example `_comment` leads -- never the placeholder vocabulary).
    Replace ONLY the managed blocks:
      - hotkeys: the diff of `hotkeys_effective` vs `config.DEFAULT_HOTKEYS` in
        #55's partial-override shape; an empty diff leaves only the block's
        `_comment` (or drops the block). A leading `_comment` is preserved.
      - defaults.api: set only if `default_api` differs from
        `config.BUILTIN_DEFAULT_API`; else the key is dropped (any sibling keys +
        `_comment` in `defaults` stay).
      - ui.language (#144, GUI-only): written ONLY when `ui_language` is `"de"` or
        `"en"`. `ui_language=None` leaves any `ui` block exactly as found (and
        creates none), so a user who never toggled the language keeps a clean file.
        When set, any other keys in `ui` (and its `_comment`) are preserved; a
        brand-new `ui` block gets the example's `_comment` lead if available.
    Every unmanaged block (vocabulary / push_to_talk / soniox_endpointing) and
    every `_comment` is preserved untouched. Serialized json.dump(indent=2,
    ensure_ascii=False) + trailing newline. Atomic (temp file + os.replace).

    A MISSING target is the normal first-run case (read -> {}), so only the managed
    skeleton is written. A present-but-UNREADABLE target makes the read raise, which
    propagates out of here so the save aborts -- the file is never skeletoned over
    and its vocabulary is never destroyed (B1). A corrupt-JSON target (bytes read
    fine, invalid JSON) stays the deliberate warn-then-overwrite case:
    read_personal_settings already handed the GUI the warning, and a save replaces
    it with a clean managed skeleton."""
    path = Path(path)
    existing, _warning = read_personal_settings(path)
    data = existing if existing else _managed_skeleton(example_path)

    # ---- hotkeys: write only the diff vs the shipped defaults -----------------
    # Preserve every JSON-comment key (any '_'-prefixed key -- apply_hotkey_overrides
    # skips all of them, so a user may park e.g. "_disabled_start_recording"); only
    # the real action entries are replaced by the fresh diff (N7).
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

    # ---- defaults.api: written only when it differs from the built-in ---------
    def_block = data.get("defaults")
    new_def = dict(def_block) if isinstance(def_block, dict) else {}
    if default_api and default_api != config.BUILTIN_DEFAULT_API:
        new_def["api"] = default_api
    else:
        new_def.pop("api", None)
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

    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    _atomic_write(path, content)


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
    """'Ctrl + Alt + P' -> 'ctrl+alt+p' -- the canonical form
    apply_hotkey_overrides emits (lowercase, each part stripped, rejoined on
    '+')."""
    return "+".join(part.strip() for part in raw.lower().split("+"))


def _norm_value(value):
    """Normalize a combo value (str or list[str]) for equality comparison."""
    if isinstance(value, list):
        return [normalize_combo(v) for v in value]
    return normalize_combo(value)


def validate_combo(raw: str) -> tuple:
    """(ok, message). Parses via parse_hotkey_lexical + classify_key. ok=False
    with a human message on an unparseable combo (no key / multiple keys) or a
    KEY_INVALID key. Special keys (a single character like the umlaut) are
    KEY_SPECIAL and accepted -- they resolve at runtime via VkKeyScanW, so
    config-time acceptance matches runtime registrability."""
    if not isinstance(raw, str) or not raw.strip():
        return False, "empty combo"
    try:
        _mods, key = parse_hotkey_lexical(raw)
    except HotkeyParseError as e:
        return False, str(e)
    if classify_key(key) == KEY_INVALID:
        return False, f"unrecognized key '{key}'"
    return True, ""


# Tk keysyms that are themselves modifiers -- a keypress reporting one means only
# a modifier is down, so there is no key to bind yet.
_MODIFIER_KEYSYMS = frozenset({
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Meta_L", "Meta_R", "Super_L", "Super_R", "Hyper_L", "Hyper_R",
    "ISO_Level3_Shift", "Caps_Lock", "Num_Lock", "Scroll_Lock", "Win_L", "Win_R",
})

# Tk keysym for the one German special key accepted as a hotkey key: 'ü'
# (udiaeresis), the documented self-test key (ctrl+alt+ü). The other umlauts / ß
# are deliberately NOT offered here -- config.py's DEFAULT_HOTKEYS note and the
# personal_settings example both warn that a non-ASCII hotkey key other than 'ü'
# can get typed into some apps (N8), so the capture widget must not decode them
# into a bindable combo. classify_key treats 'ü' as KEY_SPECIAL (resolved at
# runtime via VkKeyScanW).
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

    Modifiers come from the TK_STATE_* bits (verify hands-on). AltGr on QWERTZ
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
# The final researched F-key preset (Fork 2, #144). Source: the maintainer's
# 2026-07-21 study (_temp-claudecode/tageslauf-2026-07-21/fpreset-recherche.md),
# verified zero-warning through apply_hotkey_overrides. Schema in one line: three
# F-keys, three families -- F8 = engine, F9 = record, F10 = deliver; a BARE key is
# the daily op (F9 records, F10 delivers), CTRL is the important sibling case
# (cancel / send / switch engine), CTRL+ALT the rare/technical one (deliver without
# insert / via typing). Housekeeping (open_history / test_transcription /
# exit_program) is kept identical to the shipped Ctrl+Alt scheme so switching preset
# means no relearning. cancel_recording / exit_program keep single-element LISTS to
# match their list-shaped defaults (apply_hotkey_overrides preserves shape). Bare f8
# is intentionally left unassigned -- reserved for a future push-to-talk hold key.
PRESET_FKEYS = {
    "start_recording": "f9",
    "stop_recording_keyboard": "ctrl+alt+f10",
    "stop_recording_clipboard": "f10",
    "stop_recording_send": "ctrl+f10",
    "stop_recording_no_insert": "ctrl+alt+f9",
    "retry_last_failed": "shift+f8",
    "cancel_recording": ["ctrl+f9"],
    "test_transcription": "ctrl+alt+ü",
    "switch_api": "ctrl+f8",
    "open_history": "ctrl+alt+6",
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
