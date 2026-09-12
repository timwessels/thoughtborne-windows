"""
Pure, ctypes-free hotkey lexical layer (#55).

Shared by hotkey_manager (runtime RegisterHotKey resolution) and config
(config-time override validation), so config-time acceptance equals runtime
registrability. No Windows imports -> importable off-Windows, which keeps
config import-safe for the test drivers (test_console_ui.py etc.). The one
genuinely layout/Windows-bound step (VkKeyScanW for special characters such as
the German 'u-umlaut') stays in hotkey_manager; everything a user realistically
rebinds to -- letters, digits, and F-keys, with ctrl/alt/shift/win modifiers --
is resolvable here.

Because every layer passes through here, this is also where a combo gets its one
spelling (`canonical_combo`) and its one display form (`format_combo`,
`first_combo`) -- the grammar the console, the settings app and the tests share
(#272).
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

# Multi-char tokens the runtime resolver (_resolve_vk_code) still accepts as a
# special key via its explicit fallback -- kept in lockstep so config-time
# validation doesn't reject a combo the runtime would accept.
_SPECIAL_ALIASES = {'ue'}


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
KEY_SPECIAL = "special"  # single char / known alias -> resolvable only at runtime (VkKeyScanW)
KEY_INVALID = "invalid"  # cannot be a key at all -> reject


def classify_key(key_token: str) -> str:
    """Classify a parsed key token for config-time validation.

    STATIC keys (letters/digits/F-keys) are certainly registrable off-Windows.
    SPECIAL keys (a single character like the umlaut, or a known alias) can only
    be resolved at runtime via VkKeyScanW, so they are accepted at config-time
    and, if truly unregistrable, fail loudly at RegisterHotKey (never a startup
    abort). INVALID means it cannot be a key at all.
    """
    if key_token in VK_MAP:
        return KEY_STATIC
    if len(key_token) == 1 or key_token in _SPECIAL_ALIASES:
        return KEY_SPECIAL
    return KEY_INVALID


# The one modifier order every stored and displayed combo is written in (#272).
_CANONICAL_MODIFIERS = (
    ('ctrl', MOD_CONTROL),
    ('alt', MOD_ALT),
    ('shift', MOD_SHIFT),
    ('win', MOD_WIN),
)

# Canonical spelling of the multi-char key aliases. 'ue' stays acceptable input
# (_SPECIAL_ALIASES above), but is stored and shown as the character it binds:
# _resolve_vk_code maps both to one VK code, so two spellings of one key would
# otherwise pass the duplicate check and collide at RegisterHotKey instead.
_CANONICAL_KEYS = {'ue': 'ü'}


def canonical_combo(hotkey_str: str) -> str:
    """'Control + ALT+P' -> 'ctrl+alt+p': the one spelling of a combo (#272).

    The modifiers are written back from the parsed flags in the fixed order
    ctrl, alt, shift, win -- so aliases ('control', 'windows'), case, modifier
    order and inner spaces all collapse -- then the key, with 'ue' spelled as
    the 'ü' it binds. Raises HotkeyParseError exactly like parse_hotkey_lexical,
    so callers keep their existing error paths.

    With every effective combo canonical, common_prefix sees one spelling per
    prefix, format_combo needs no name table, and a comparison against the
    defaults compares bindings rather than notations.
    """
    modifiers, key = parse_hotkey_lexical(hotkey_str)
    parts = [name for name, flag in _CANONICAL_MODIFIERS if modifiers & flag]
    parts.append(_CANONICAL_KEYS.get(key, key))
    return '+'.join(parts)


def format_combo(combo: str) -> str:
    """Display spelling of a canonical combo: 'ctrl+alt+f10' -> 'Ctrl+Alt+F10'.

    capitalize() per part is enough once the input is canonical (ctrl/alt/shift/
    win, a letter, a digit, f1-f24, or 'ü'), and it formats a bare prefix
    ('ctrl+alt' -> 'Ctrl+Alt') the same way. The one display formatter: console,
    settings app and tests share it, so no surface invents a second spelling.
    """
    return '+'.join(part.capitalize() for part in combo.split('+'))


def first_combo(value) -> str:
    """The one combo a display surface shows for an action.

    A binding is a combo string or, for the list-shaped actions
    (cancel_recording, exit_program), a list of them -- of which every surface
    has always shown the first. An empty list yields '': apply_hotkey_overrides
    rejects one, and a display helper must not be the thing that raises.
    """
    if isinstance(value, list):
        return value[0] if value else ''
    return value


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
