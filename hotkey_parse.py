"""
Pure, ctypes-free hotkey lexical layer (#55).

Shared by hotkey_manager (runtime RegisterHotKey resolution) and config
(config-time override validation), so config-time acceptance equals runtime
registrability. No Windows imports -> importable off-Windows, which keeps
config import-safe for the test drivers (test_console_ui.py etc.). Every
bindable key -- letters, digits, F-keys, the navigation cluster, arrows, the
numpad, Pause and Scroll Lock (#325), with ctrl/alt/shift/win modifiers --
resolves against the static VK_MAP here; there is no layout-resolved key
lane (D-023 removed the old 'u-umlaut'/VkKeyScanW one). Both lanes also share
one rejection checkpoint here, `combo_rejection_reason`: the dead combos
Windows could never deliver (#325) plus, since D-030, the few combos whose
keystroke collision is invisible from the outside -- the paste the tool's own
clipboard route sends, and the German AltGr combos.

Because every layer passes through here, this is also where a combo gets its one
spelling (`canonical_combo`), its one display form (`format_combo`) and its one
poll-name list (`combo_keys`, #152) -- the grammar the console, the settings app,
the release-wait guards and the tests share (#272).
"""

# ===== Win32 RegisterHotKey modifier flags (plain ints -- no ctypes/DLL) =====
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# Modifier string -> RegisterHotKey modifier flag
MODIFIER_MAP = {
    'ctrl': MOD_CONTROL,
    'control': MOD_CONTROL,
    'alt': MOD_ALT,
    'shift': MOD_SHIFT,
    'win': MOD_WIN,
    'windows': MOD_WIN,
}

# Key string -> VK code, for keys resolvable WITHOUT Windows.
VK_MAP = {}
# Letters a-z -> 0x41..0x5A
for _i in range(26):
    VK_MAP[chr(ord('a') + _i)] = 0x41 + _i
# Digits 0-9 -> 0x30..0x39
for _i in range(10):
    VK_MAP[str(_i)] = 0x30 + _i
# F-keys f1-f24 -> VK_F1..VK_F24 (0x70..0x87, contiguous) (#55). The
# prerequisite for #144's F-key preset scheme; also makes modifier-less combos
# (bare 'f9') expressible, which RegisterHotKey supports.
for _i in range(24):
    VK_MAP[f"f{_i + 1}"] = 0x70 + _i
# Navigation cluster, arrows, numpad, Pause and Scroll Lock (#325) -- all
# static VKs, the same on every layout, so D-023 holds: still one kind of key.
VK_MAP.update({
    'insert': 0x2D, 'delete': 0x2E, 'home': 0x24, 'end': 0x23,
    'pageup': 0x21, 'pagedown': 0x22,
    'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    'pause': 0x13, 'scrolllock': 0x91,
})
# Numpad digits (VK_NUMPAD0..9) -- their own VKs, distinct from the digit row.
for _i in range(10):
    VK_MAP[f"num{_i}"] = 0x60 + _i
VK_MAP.update({
    'numdecimal': 0x6E, 'numdivide': 0x6F, 'nummultiply': 0x6A,
    'numsubtract': 0x6D, 'numadd': 0x6B,
})

# VK -> token: the exact inverse of VK_MAP (#325). The settings capture reads
# event.keycode (which on Windows Tk IS the virtual-key code) and must land on
# the very token the registrar resolves -- built by inversion so the two cannot
# drift. VK_MAP is injective (test-guarded), so the inversion loses nothing.
VK_TO_TOKEN = {vk: token for token, vk in VK_MAP.items()}


class HotkeyParseError(ValueError):
    """A hotkey string that cannot be split into (modifiers, key).

    Subclass of ValueError so hotkey_manager's registration `except ValueError`
    keeps catching structural parse failures unchanged.
    """


def parse_hotkey_lexical(hotkey_str: str) -> tuple:
    """Split 'ctrl+alt+w' into (modifier_flags_incl_NOREPEAT, key_token_lower).

    Structural validation only -- does NOT resolve the key to a VK code (that
    may need Windows). MOD_NOREPEAT is always set (prevents repeat when a key is
    held). Raises HotkeyParseError when there is no non-modifier key or more than
    one. This is the exact body of the old hotkey_manager._parse_hotkey, minus
    the final _resolve_vk_code call.
    """
    parts = hotkey_str.lower().split('+')
    modifiers = MOD_NOREPEAT  # Always set
    key_part = None

    for part in parts:
        part = part.strip()
        if part in MODIFIER_MAP:
            modifiers |= MODIFIER_MAP[part]
        else:
            if key_part is not None:
                raise HotkeyParseError(
                    f"Multiple non-modifier keys in '{hotkey_str}': "
                    f"'{key_part}' and '{part}'")
            key_part = part

    if key_part is None:
        raise HotkeyParseError(f"No non-modifier key found in '{hotkey_str}'")

    return (modifiers, key_part)


# classify_key outcomes
KEY_STATIC = "static"    # in VK_MAP -> definitely registrable, no Windows needed
KEY_INVALID = "invalid"  # cannot be a key at all -> reject


def classify_key(key_token: str) -> str:
    """Classify a parsed key token for config-time validation.

    STATIC keys -- letters, digits, F-keys, the navigation cluster, arrows,
    the numpad, Pause and Scroll Lock, which since D-023 are the whole
    bindable set -- are certainly registrable off-Windows. Everything else is
    INVALID: rejected at config time with a warning, the action keeping its
    default.
    """
    return KEY_STATIC if key_token in VK_MAP else KEY_INVALID


# Held Ctrl shifts the scancode of Pause and of Scroll Lock, so both keys reach
# Windows as VK_CANCEL (0x03): a ctrl+ combo on either registers fine and can
# never fire -- the #308/D-022 "assignable but dead" class. The technical
# rejection of #325, shared by both validation lanes (config's JSON overrides
# and the settings capture) so they cannot drift.
_CTRL_SHIFTED_KEYS = frozenset({'pause', 'scrolllock'})

# What AltGr types on the German T1 layout (Austria identical), for the keys
# VK_MAP can bind -- frozen data (D-030), never a runtime layout lookup (D-023,
# and the active layout can change between a config read and a keypress). AltGr
# IS Ctrl+Alt on Windows, so each of these combos is what physically typing that
# character sends. The Swiss remainders (broken bar on AltGr+1, not sign on
# AltGr+6) are deliberately absent -- text-far, and ctrl+alt+6 is the shipped
# open_history default. The other German AltGr characters (backslash, pipe,
# tilde) sit on OEM keys outside VK_MAP and need no rule. The four non-ASCII
# characters are written as named escapes, so this file stays plain ASCII and
# still says which character it means.
_ALTGR_DE = {
    'q': '@',
    'e': '\N{EURO SIGN}',
    'm': '\N{MICRO SIGN}',
    '2': '\N{SUPERSCRIPT TWO}',
    '3': '\N{SUPERSCRIPT THREE}',
    '7': '{', '8': '[', '9': ']', '0': '}',
}


def combo_rejection_reason(modifiers: int, key_token: str) -> "str | None":
    """A human-readable reason when (modifiers, key) must be rejected at config
    time, or None for an acceptable combo. Takes the parsed pair both callers
    already hold; the one checkpoint both validation lanes ask (config's JSON
    overrides and the settings capture), so they cannot drift. Two documented
    classes (D-030), each naming its mechanism in the message:

    - dead: registers but can never fire (ctrl with pause/scrolllock arrives as
      VK_CANCEL) -- #325's class, unchanged.
    - an invisible keystroke collision: the combo registers AND fires, on a
      keypress nobody can see coming. Exact ctrl+v is the paste the clipboard
      insert route really sends (`keyboard.send('ctrl+v')`); ctrl+alt on the
      _ALTGR_DE keys is what typing those characters sends, because AltGr is
      Ctrl+Alt. A *visible* sacrifice -- a bare letter, a shadowed OS shortcut
      -- stays the user's call (D-027). The typed insert route needs no rule:
      `keyboard.write` injects KEYEVENTF_UNICODE events, which no RegisterHotKey
      binding matches.

    RegisterHotKey matches modifiers exactly, so both new rules are exact
    matches: `v` with any other modifier set and `ctrl+alt+shift+e` keep
    binding. `modifiers` may or may not carry MOD_NOREPEAT; it is masked out
    here, so a caller holding plain MODIFIER_MAP flags gets the same answer.
    """
    mods = modifiers & ~MOD_NOREPEAT
    if mods & MOD_CONTROL and key_token in _CTRL_SHIFTED_KEYS:
        return (f"'{key_token}' cannot combine with ctrl -- Windows delivers "
                f"VK_CANCEL instead, so the hotkey would never fire")
    if mods == MOD_CONTROL and key_token == 'v':
        return ("ctrl+v is the paste the tool itself sends to insert a "
                "transcript -- the hotkey would swallow the tool's own output "
                "(and every other paste)")
    if mods == (MOD_CONTROL | MOD_ALT) and key_token in _ALTGR_DE:
        return (f"ctrl+alt+{key_token} is AltGr+{key_token} -- typing "
                f"'{_ALTGR_DE[key_token]}' on a German keyboard sends exactly "
                f"this combo, so the hotkey would fire and swallow the "
                f"character")
    return None


# The one modifier order every stored and displayed combo is written in (#272).
_CANONICAL_MODIFIERS = (
    ('ctrl', MOD_CONTROL),
    ('alt', MOD_ALT),
    ('shift', MOD_SHIFT),
    ('win', MOD_WIN),
)


def canonical_combo(hotkey_str: str) -> str:
    """'Control + ALT+P' -> 'ctrl+alt+p': the one spelling of a combo (#272).

    The modifiers are written back from the parsed flags in the fixed order
    ctrl, alt, shift, win -- so aliases ('control', 'windows'), case, modifier
    order and inner spaces all collapse -- then the key. Raises
    HotkeyParseError exactly like parse_hotkey_lexical, so callers keep their
    existing error paths.

    With every effective combo canonical, common_prefix sees one spelling per
    prefix, format_combo maps token-wise off exactly one spelling, and a
    comparison against the defaults compares bindings rather than notations.
    """
    modifiers, key = parse_hotkey_lexical(hotkey_str)
    parts = [name for name, flag in _CANONICAL_MODIFIERS if modifiers & flag]
    parts.append(key)
    return '+'.join(parts)


def combo_keys(combo: str) -> list:
    """The poll names of a combo, modifiers first, key last: 'ctrl+alt+h' ->
    ['ctrl', 'alt', 'h']; a bare 'f10' -> ['f10'] (#152).

    The third derived form of the one combo grammar, beside canonical_combo and
    format_combo: the names a release-wait guard hands to
    hotkey_manager.is_key_pressed, so what the tool waits for is what the action
    is actually bound to. Every emitted name resolves there -- the modifiers as
    the canonical four, the key as its VK_MAP token. Two properties of that VK
    table ride along: 'win' is VK_LWIN, so a held RIGHT Win key goes unseen (the
    same grain _ensure_no_modifiers_pressed polls), and a navigation token shares
    its VK with the numpad ('home' also reads Numpad-7 with Num Lock off).

    Raises HotkeyParseError exactly like parse_hotkey_lexical, so a caller keeps
    its existing error path; the list therefore always ends in a key and is never
    modifiers only.
    """
    modifiers, key = parse_hotkey_lexical(combo)
    keys = [name for name, flag in _CANONICAL_MODIFIERS if modifiers & flag]
    keys.append(key)
    return keys


# Token -> display name, for the tokens capitalize() spells wrong (#325).
# Every other combo part keeps the capitalize() grammar, so this table is part
# of the one display grammar (D-019), not a second one. Two hard rules, test-
# guarded: a name never contains '+' (every display consumer splits combos on
# '+' -- console_ui._display_prefix/_bare and common_prefix below), and never
# exceeds 6 cells (the console key cells' width arithmetic, pinned by the
# ladder's stress checks).
KEY_DISPLAY = {
    'insert': 'Ins', 'delete': 'Del', 'pageup': 'PgUp', 'pagedown': 'PgDn',
    'numdecimal': 'NumDec', 'numdivide': 'NumDiv', 'nummultiply': 'NumMul',
    'numsubtract': 'NumSub', 'numadd': 'NumAdd',
    'scrolllock': 'ScrLk',
}


def format_combo(combo: str) -> str:
    """Display spelling of a canonical combo: 'ctrl+alt+f10' -> 'Ctrl+Alt+F10',
    'ctrl+alt+pageup' -> 'Ctrl+Alt+PgUp'.

    capitalize() per part, except where KEY_DISPLAY names the short form
    (#325); a bare prefix ('ctrl+alt' -> 'Ctrl+Alt') formats the same way. The
    one display formatter: console, settings app and tests share it, so no
    surface invents a second spelling.
    """
    return '+'.join(KEY_DISPLAY.get(part, part.capitalize())
                    for part in combo.split('+'))


def common_prefix(combos) -> "str | None":
    """The modifier prefix shared by all combos (e.g. 'ctrl+alt'), or None (#55).

    Returns the raw lowercased prefix -- everything before each combo's final
    '+' -- when every combo shares the same non-empty prefix, else None. A bare
    key like 'f9' has an empty prefix, so any set that mixes one in yields None.
    Pure -- no Windows, no formatting; an empty `combos` yields None. Expects
    canonical combos ('ctrl+alt+p', no inner spaces), which
    config.apply_hotkey_overrides guarantees for overrides.

    The rule it states is the once-per-box display lead (#115): with per-user
    overrides the lead must follow the *effective* keys a box actually shows, not
    one global assumption across every action. No production caller since #277,
    though -- the lead is derived inside the renderer now, from exactly the combos
    of the box being drawn (D-019), and console_ui may import nothing from the
    project, so it carries a deliberate twin (`_display_prefix`). This is that
    twin's reference version: test_console_ui holds the two against each other.
    """
    prefixes = {c.rpartition('+')[0] for c in combos}
    if len(prefixes) == 1 and '' not in prefixes:
        return prefixes.pop()
    return None
