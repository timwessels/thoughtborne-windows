"""
Pure, ctypes-free hotkey lexical layer (#55).

Shared by hotkey_manager (runtime RegisterHotKey resolution) and config
(config-time override validation), so config-time acceptance equals runtime
registrability. No Windows imports -> importable off-Windows, which keeps
config import-safe for the test drivers (test_console_ui.py etc.). The one
genuinely layout/Windows-bound step (VkKeyScanW for special characters such as
the German 'u-umlaut') stays in hotkey_manager; everything a user realistically
rebinds to -- letters, digits, and F-keys, with ctrl/alt/shift/win modifiers --
is resolvable here. The three mouse buttons (#308) are here too, in a map of
their own: they are a token the grammar knows and RegisterHotKey cannot serve,
so the lexical layer is also where a combo is told which lane it belongs to.

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

# Mouse buttons bindable as a hotkey key (#308) -> VK code, for the polled lane.
# Deliberately NOT in VK_MAP: that map is what _resolve_vk_code hands to
# RegisterHotKey, and RegisterHotKey *accepts* a mouse VK, returns TRUE and then
# never fires for it (measured 2026-09-08) -- a key that looks registered and is
# dead. These ride mouse_detector instead, polled on their own thread and
# clock -- never the audio-paced recording loop -- which is also the stronger
# reason no shipped default may sit on one: not merely layout-resolved like
# the umlaut (D-012), simply not registrable at all.
# Left and right button stay out on purpose: left is how the settings app's
# capture field is armed, and binding either would make the machine unusable.
MOUSE_VK_MAP = {
    'mbutton': 0x04,    # VK_MBUTTON  -- wheel click
    'xbutton1': 0x05,   # VK_XBUTTON1 -- thumb, Windows' "Back"
    'xbutton2': 0x06,   # VK_XBUTTON2 -- thumb, "Forward"
}


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
KEY_MOUSE = "mouse"      # a mouse button -> bindable, but never via RegisterHotKey (#308)
KEY_INVALID = "invalid"  # cannot be a key at all -> reject


def classify_key(key_token: str) -> str:
    """Classify a parsed key token for config-time validation.

    STATIC keys (letters/digits/F-keys) are certainly registrable off-Windows.
    SPECIAL keys (a single character like the umlaut, or a known alias) can only
    be resolved at runtime via VkKeyScanW, so they are accepted at config-time
    and, if truly unregistrable, fail loudly at RegisterHotKey (never a startup
    abort). MOUSE keys are bindable but take a different lane entirely (#308):
    a fourth outcome rather than a flavour of STATIC precisely so the D-012 guard
    -- which asks `== KEY_STATIC` of every shipped default -- keeps failing for
    one, since a shipped mouse default would be dead for every user.
    INVALID means it cannot be a key at all.
    """
    if key_token in VK_MAP:
        return KEY_STATIC
    if key_token in MOUSE_VK_MAP:
        return KEY_MOUSE
    if len(key_token) == 1 or key_token in _SPECIAL_ALIASES:
        return KEY_SPECIAL
    return KEY_INVALID


def combo_rejection(modifiers: int, key_token: str) -> "str | None":
    """The one reason a structurally parsed combo is still unbindable, or None.

    parse_hotkey_lexical answers "is this shaped like a combo"; this answers "may
    it be bound". Both acceptance authorities go through here --
    config.apply_hotkey_overrides for the JSON lane and settings_io.validate_combo
    for the settings app -- so a rule cannot hold on one path and leak on the
    other. The returned text is the reason alone, without a subject: each caller
    puts it into its own sentence (a log warning there, the capture field's
    detail slot here).
    """
    kind = classify_key(key_token)
    if kind == KEY_INVALID:
        return f"unrecognized key '{key_token}'"
    # A mouse button is bindable only bare (#308). The normal case is "this thumb
    # button dictates"; a modifier in front of it would ask what a held Shift
    # means for a button other programs keep reacting to anyway. MOD_NOREPEAT is
    # set on every parse, so it is masked out rather than tested for.
    if kind == KEY_MOUSE and (modifiers & ~MOD_NOREPEAT):
        return f"'{key_token}' is a mouse button and takes no modifier"
    return None


def mouse_vk(hotkey_str: str):
    """The VK code of a BARE mouse combo, or None for everything else (#308).

    The one place that decides which lane a combo takes: a hit here means "poll
    it", None means "hand it to RegisterHotKey". Fail-open by construction --
    anything unparseable, and a mouse token still carrying a modifier (which both
    acceptance layers refuse, so it cannot arrive from a validated config), falls
    through to the keyboard lane and fails there the way it always has, with a
    logged `FAILED: ... Parse error` rather than an exception out of registration.
    """
    try:
        modifiers, key = parse_hotkey_lexical(hotkey_str)
    except HotkeyParseError:
        return None
    if key in MOUSE_VK_MAP and not (modifiers & ~MOD_NOREPEAT):
        return MOUSE_VK_MAP[key]
    return None


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


# Display spelling of the key tokens capitalize() gets wrong -- it would print
# 'Xbutton1' (#308). Modifiers and every other key still go through capitalize(),
# so this stays a three-row exception inside the one formatter rather than a
# second one (D-019). No modifier name collides with these three tokens, so the
# lookup is safe on every part of a combo.
_DISPLAY_KEYS = {'mbutton': 'MButton', 'xbutton1': 'XButton1', 'xbutton2': 'XButton2'}


def format_combo(combo: str) -> str:
    """Display spelling of a canonical combo: 'ctrl+alt+f10' -> 'Ctrl+Alt+F10'.

    capitalize() per part is enough once the input is canonical (ctrl/alt/shift/
    win, a letter, a digit, f1-f24, or 'ü'), and it formats a bare prefix
    ('ctrl+alt' -> 'Ctrl+Alt') the same way; the mouse tokens are the one
    exception, spelled out in _DISPLAY_KEYS above. The one display formatter:
    console, settings app and tests share it, so no surface invents a second
    spelling.
    """
    return '+'.join(_DISPLAY_KEYS.get(part, part.capitalize())
                    for part in combo.split('+'))


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
