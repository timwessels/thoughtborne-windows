#!/usr/bin/env python3
"""Unit tests for the #55 hotkey/default-engine override surface.

Runs on plain Python -- no Windows, no ctypes -- so the two pure layers behind
`personal_settings.json` hotkey overrides are exercised programmatically (a
durable regression guard, sibling of `test_console_ui.py`).

    python3 test_hotkey_overrides.py          # verify, exit non-zero on failure
    python3 test_hotkey_overrides.py --show    # also print a few effective sets

Layer A -- `hotkey_parse` (the ctypes-free lexical layer): the static VK map
(letters, digits, F1-F24, and since #325 the navigation cluster, arrows, the
numpad, Pause and Scroll Lock -- injective, so `VK_TO_TOKEN`, the capture
decode's inversion, loses nothing), the structural parser
`parse_hotkey_lexical`, `classify_key`, `dead_combo_reason` (the one dead
combo class: ctrl with pause/scrolllock arrives as VK_CANCEL and could never
fire), and the one spelling a combo is stored and shown in --
`canonical_combo`, `format_combo` with its `KEY_DISPLAY` short names (#275,
#325), including the guard that both shipped schemes (`DEFAULT_HOTKEYS` and
`settings_io.PRESET_FKEYS`) are written canonically themselves -- and the
drift guard that keeps the README twins' `## Hotkeys` tables on
`DEFAULT_HOTKEYS`, order and combos (D-019). `first_combo`, the picker the
list shape needed, is pinned as retired (D-024).

Layer B -- `config.apply_hotkey_overrides` (the pure production loader config
calls verbatim): partial override by action name, warn-and-keep-default on every
kind of bad entry, F-key names, the value shape (one action binds one combo,
D-024 -- a one-element list from an older version collapses silently, any other
list is rejected), duplicate detection on the *effective* set with
case/modifier-order normalization, and the guarantees that the defaults dict is
never mutated and that their order, the canonical action order every surface
follows (D-019), survives the one loader it passes through.

The did-the-key-actually-fire check is hands-on (RegisterHotKey needs Windows)
and is tracked in a separate `test` issue.
"""
import copy
import logging
import re
import sys
from pathlib import Path

# Silence config's import-time settings warnings -- importing config parses the
# repo's real personal_settings.json, which may legitimately warn; irrelevant to
# these pure-function tests. (Same approach test_retry_marker_lifecycle uses.)
logging.getLogger('Thoughtborne.Config').setLevel(logging.CRITICAL)

import hotkey_parse as hp
import settings_io
from config import apply_hotkey_overrides, DEFAULT_HOTKEYS

SHOW = "--show" in sys.argv

# Own fixture mirroring the real HOTKEYS shape (one combo string per action,
# D-024, plus a digit key) -- NOT config.HOTKEYS, which the repo's own
# personal_settings.json might already have overridden.
DEFAULTS = {
    'start_recording': 'ctrl+alt+w',
    'stop_recording_keyboard': 'ctrl+alt+a',
    'stop_recording_clipboard': 'ctrl+alt+d',
    'switch_api': 'ctrl+alt+l',
    'test_transcription': 'ctrl+alt+t',
    'open_history': 'ctrl+alt+6',
    'cancel_recording': 'ctrl+alt+x',
    'exit_program': 'ctrl+alt+4',
}
_DEFAULTS_SNAPSHOT = copy.deepcopy(DEFAULTS)


def run(raw):
    """Call the production loader and bake in the always-true invariants:
    DEFAULTS is never mutated (guards the deepcopy), the effective set has exactly
    the default keys *in their order* -- that dict order is the canonical action
    order every surface follows by iteration (D-019), and the loader is the one
    place it passes through -- and warnings is a list of strings (tests 17 + 18)."""
    eff, warns = apply_hotkey_overrides(DEFAULTS, raw)
    assert DEFAULTS == _DEFAULTS_SNAPSHOT, "apply_hotkey_overrides mutated DEFAULTS"
    assert list(eff) == list(DEFAULTS), (
        f"effective keys differ from the defaults in content or order -- the "
        f"canonical D-019 order must survive the loader:\n"
        f"  defaults  {list(DEFAULTS)}\n  effective {list(eff)}")
    assert isinstance(warns, list) and all(isinstance(w, str) for w in warns), \
        "warnings must be a list of strings"
    return eff, warns


def only_changed(eff, changed):
    """Assert `changed` holds for the named actions and every other action is
    byte-identical to its default."""
    for k, v in changed.items():
        assert eff[k] == v, f"{k}: expected {v!r}, got {eff[k]!r}"
    for k in DEFAULTS:
        if k not in changed:
            assert eff[k] == DEFAULTS[k], f"{k} changed unexpectedly to {eff[k]!r}"


# ======================================================================
# Layer A -- hotkey_parse
# ======================================================================

def test_vk_map_fkeys_and_statics():
    assert hp.VK_MAP['f1'] == 0x70
    assert hp.VK_MAP['f9'] == 0x78
    assert hp.VK_MAP['f24'] == 0x87
    assert 'f0' not in hp.VK_MAP and 'f25' not in hp.VK_MAP
    assert hp.VK_MAP['w'] == 0x57     # letter still resolves
    assert hp.VK_MAP['4'] == 0x34     # digit still resolves


def test_vk_map_extended_keys():
    # #325: navigation cluster, arrows, numpad, Pause, Scroll Lock -- spot checks
    assert hp.VK_MAP['insert'] == 0x2D
    assert hp.VK_MAP['pageup'] == 0x21
    assert hp.VK_MAP['left'] == 0x25
    assert hp.VK_MAP['num0'] == 0x60
    assert hp.VK_MAP['num9'] == 0x69
    assert hp.VK_MAP['numadd'] == 0x6B
    assert hp.VK_MAP['pause'] == 0x13
    assert hp.VK_MAP['scrolllock'] == 0x91
    # the numpad digits are their own VKs, not the digit row's
    assert hp.VK_MAP['num7'] != hp.VK_MAP['7']
    # no alias spellings -- one token per key, like everywhere else
    for absent in ('numpad0', 'pgup', 'pgdn', 'ins', 'del', 'arrowleft', 'break'):
        assert absent not in hp.VK_MAP, absent
    # VK_MAP stays injective -- VK_TO_TOKEN (the capture decode's inversion,
    # #325) loses nothing exactly as long as this holds.
    assert len(set(hp.VK_MAP.values())) == len(hp.VK_MAP), \
        "VK_MAP is no longer injective -- VK_TO_TOKEN silently drops a token"
    assert len(hp.VK_TO_TOKEN) == len(hp.VK_MAP)
    assert hp.VK_TO_TOKEN[0x24] == 'home' and hp.VK_TO_TOKEN[0x78] == 'f9'


def test_parse_modifiers_and_key():
    mods, key = hp.parse_hotkey_lexical('ctrl+alt+w')
    assert key == 'w'
    assert mods == hp.MOD_NOREPEAT | hp.MOD_CONTROL | hp.MOD_ALT
    # modifier order + case independence
    assert hp.parse_hotkey_lexical('ALT+Ctrl+W') == (mods, key)


def test_parse_bare_fkey():
    assert hp.parse_hotkey_lexical('f9') == (hp.MOD_NOREPEAT, 'f9')


def test_parse_raises_structural():
    for bad in ('ctrl+alt', 'ctrl+alt+a+b'):
        try:
            hp.parse_hotkey_lexical(bad)
            assert False, f"expected HotkeyParseError for {bad!r}"
        except hp.HotkeyParseError:
            pass
    # Subclass of ValueError so hotkey_manager's registration `except ValueError`
    # keeps catching structural parse failures.
    assert issubclass(hp.HotkeyParseError, ValueError)


def test_classify_key():
    assert hp.classify_key('w') == hp.KEY_STATIC
    assert hp.classify_key('f9') == hp.KEY_STATIC
    assert hp.classify_key('4') == hp.KEY_STATIC
    # D-023 (#317): with the layout-resolved lane gone there is no KEY_SPECIAL --
    # 'ü', its old 'ue' alias and every other single character are plain invalid
    # now, rejected with a warning like any unknown key.
    assert hp.classify_key('ü') == hp.KEY_INVALID
    assert hp.classify_key('ue') == hp.KEY_INVALID
    assert hp.classify_key('#') == hp.KEY_INVALID
    assert hp.classify_key('foo') == hp.KEY_INVALID   # multi-char non-key
    assert hp.classify_key('') == hp.KEY_INVALID      # empty token (e.g. 'ctrl+alt+')


def test_canonical_combo():
    # One spelling per binding (#272/#275): aliases, case, modifier order and
    # inner spaces all collapse into ctrl, alt, shift, win + key.
    assert hp.canonical_combo('ctrl+alt+w') == 'ctrl+alt+w'      # already canonical
    assert hp.canonical_combo('Control + ALT + P') == 'ctrl+alt+p'
    assert hp.canonical_combo('alt+ctrl+w') == 'ctrl+alt+w'
    assert hp.canonical_combo('windows+shift+p') == 'shift+win+p'
    assert hp.canonical_combo('win+shift+alt+control+f24') == 'ctrl+alt+shift+win+f24'
    assert hp.canonical_combo(' F9 ') == 'f9'                    # bare key, no prefix
    assert hp.canonical_combo('CTRL+ALT+6') == 'ctrl+alt+6'
    # unparseable input raises exactly like parse_hotkey_lexical, so callers keep
    # their existing error paths
    for bad in ('ctrl+alt', 'ctrl+alt+a+b'):
        try:
            hp.canonical_combo(bad)
            assert False, f"expected HotkeyParseError for {bad!r}"
        except hp.HotkeyParseError:
            pass


def test_format_combo():
    assert hp.format_combo('ctrl+alt+w') == 'Ctrl+Alt+W'
    assert hp.format_combo('ctrl+alt+f10') == 'Ctrl+Alt+F10'
    assert hp.format_combo('ctrl+alt+6') == 'Ctrl+Alt+6'
    assert hp.format_combo('f9') == 'F9'
    assert hp.format_combo('ctrl+alt') == 'Ctrl+Alt'   # a bare prefix formats too
    # #325: KEY_DISPLAY feeds format_combo the short names capitalize() cannot
    # spell; tokens outside the table keep the capitalize() grammar.
    assert hp.format_combo('ctrl+alt+pageup') == 'Ctrl+Alt+PgUp'
    assert hp.format_combo('scrolllock') == 'ScrLk'
    assert hp.format_combo('num7') == 'Num7'
    assert hp.format_combo('ctrl+numadd') == 'Ctrl+NumAdd'
    assert hp.format_combo('alt+shift+pause') == 'Alt+Shift+Pause'
    # KEY_DISPLAY's two hard rules: never a '+' in a name (every display
    # consumer splits combos on '+'), never over 6 cells (the console cells'
    # width arithmetic) -- and no ghost entries outside VK_MAP.
    for token, name in hp.KEY_DISPLAY.items():
        assert token in hp.VK_MAP, f"KEY_DISPLAY names a non-token {token!r}"
        assert '+' not in name, f"KEY_DISPLAY[{token!r}] = {name!r} carries a '+'"
        assert len(name) <= 6, f"KEY_DISPLAY[{token!r}] = {name!r} is over 6 cells"
    # The widest combos any surface can be handed, now that the aliases collapse:
    # the all-modifier F-key chord keeps its 22 cells, and the #325 numpad names
    # top out at 25 -- the layout budget of the later display steps rests on this
    # (test_console_ui derives its MAX_COMBO from the same display names).
    assert len(hp.format_combo(hp.canonical_combo('windows+shift+alt+control+f24'))) == 22
    assert len(hp.format_combo(hp.canonical_combo('win+shift+alt+control+numdecimal'))) == 25


def test_dead_combo_reason():
    # #325's one technical rejection: held Ctrl shifts the scancode of Pause and
    # Scroll Lock, so both arrive as VK_CANCEL -- the combo registers, never fires
    # (the #308/D-022 "assignable but dead" class).
    for combo in ('ctrl+pause', 'ctrl+alt+scrolllock', 'ctrl+shift+pause',
                  'ctrl+alt+shift+win+scrolllock'):
        mods, key = hp.parse_hotkey_lexical(combo)
        assert hp.dead_combo_reason(mods, key), f"{combo!r} should be dead"
    # ...and nothing else is: ctrl-free siblings, bare keys, ctrl with any other key.
    for combo in ('alt+pause', 'shift+scrolllock', 'pause', 'scrolllock',
                  'ctrl+alt+home', 'ctrl+p', 'ctrl+alt+numadd'):
        mods, key = hp.parse_hotkey_lexical(combo)
        assert hp.dead_combo_reason(mods, key) is None, f"{combo!r} should be live"


def test_first_combo_retired():
    # D-024 (#318): one action binds one combo, so there is no first one to pick --
    # first_combo went with the list shape. Pin the removal: a helper that takes a
    # binding and returns one of several is the door back to multi-combo actions.
    assert not hasattr(hp, 'first_combo'), \
        "hotkey_parse.first_combo is back -- D-024 retired it with the list shape"


def test_common_prefix():
    # Pure core of the per-box display lead (#55/#115): the shared prefix, or None
    # on any mix -- so a box whose keys still share Ctrl+Alt keeps its lead even
    # when a *different* box's key was rebound away.
    assert hp.common_prefix(['ctrl+alt+w', 'ctrl+alt+a', 'ctrl+alt+4']) == 'ctrl+alt'
    assert hp.common_prefix(['ctrl+alt+w']) == 'ctrl+alt'    # single combo
    assert hp.common_prefix(['ctrl+alt+w', 'f9']) is None    # a bare key mixed in
    assert hp.common_prefix(['ctrl+alt+w', 'ctrl+shift+w']) is None  # different modifiers
    assert hp.common_prefix(['f9']) is None                  # single bare key -> no lead
    assert hp.common_prefix([]) is None                      # empty set -> no lead


# ======================================================================
# Layer B -- config.apply_hotkey_overrides
# ======================================================================

def test_partial_override():
    eff, warns = run({'start_recording': 'f9'})
    only_changed(eff, {'start_recording': 'f9'})
    assert warns == [], warns


def test_value_shapes():
    # D-024 (#318): one action binds one combo.
    eff, warns = run({'switch_api': 'ctrl+alt+p'})
    only_changed(eff, {'switch_api': 'ctrl+alt+p'})
    assert warns == [], warns
    # A ONE-element list -- the shape the F-key preset wrote into user files
    # before #318 -- is accepted and collapses SILENTLY. Zero warnings is the
    # load-bearing half: an update must never reset a key a user still presses.
    for action, legacy in (('switch_api', ['ctrl+alt+p']),
                           ('cancel_recording', ['ctrl+f9']),
                           ('exit_program', ['ctrl+alt+9'])):
        eff, warns = run({action: legacy})
        only_changed(eff, {action: legacy[0]})
        assert warns == [], (action, warns)
    # Any other list is rejected like every other bad entry: exactly one warning,
    # the default stays.
    for bad in (['ctrl+alt+p', 'ctrl+alt+j'], []):
        eff, warns = run({'switch_api': bad})
        only_changed(eff, {})
        assert len(warns) == 1, (bad, warns)
        assert 'one combo' in warns[0], warns


def test_inner_spaces_canonicalized():
    # A combo written with spaces around the '+' is stored canonically
    # ('Ctrl + Alt + P' -> 'ctrl+alt+p'), so the display-prefix code -- which
    # splits on '+' -- sees clean parts, not ' alt ' (SOLLTE 1). It registers
    # either way; this is about the console lead.
    eff, warns = run({'switch_api': 'Ctrl + Alt + P'})
    only_changed(eff, {'switch_api': 'ctrl+alt+p'})
    assert warns == [], warns


def test_unknown_action():
    eff, warns = run({'nope': 'f9'})
    only_changed(eff, {})
    assert any('nope' in w for w in warns), warns


def test_comment_key_ignored():
    # A "_comment" (or any _-prefixed key) is a JSON comment, never an action:
    # applied, it must neither warn nor block the real override beside it -- this
    # is what lets personal_settings.example.json's hotkeys block be copied as-is.
    eff, warns = run({'_comment': 'docs', 'start_recording': 'f9'})
    only_changed(eff, {'start_recording': 'f9'})
    assert warns == [], warns


def test_bad_combos_keep_default():
    for bad in ('foo', 'ctrl+alt+', 'ctrl+alt+a+b', ''):
        eff, warns = run({'switch_api': bad})
        only_changed(eff, {})
        assert warns, f"expected a warning for switch_api={bad!r}"


def test_wrong_value_types_keep_default():
    for bad in (5, True, {}, [], [7], ['ctrl+alt+l', 7]):
        eff, warns = run({'switch_api': bad})
        only_changed(eff, {})
        assert warns, f"expected a warning for switch_api={bad!r}"


def test_fkey_names_bare_and_legacy_list():
    # F-key name, a bare (modifier-less) F-key, the D-024 collapse of a legacy
    # one-element list, and lowercasing
    eff, warns = run({'start_recording': 'F13', 'test_transcription': ['f24']})
    only_changed(eff, {'start_recording': 'f13', 'test_transcription': 'f24'})
    assert warns == [], warns


def test_extended_keys_through_loader():
    # #325: the new keys load like any other -- bare included. The permissive
    # rule as a fixture: any supported key may be bound without modifiers, in
    # both lanes; a misconfigured bare arrow is the user's to notice and undo.
    eff, warns = run({'start_recording': 'ctrl+alt+pageup', 'switch_api': 'left',
                      'open_history': 'num5', 'test_transcription': 'pause'})
    only_changed(eff, {'start_recording': 'ctrl+alt+pageup', 'switch_api': 'left',
                       'open_history': 'num5', 'test_transcription': 'pause'})
    assert warns == [], warns


def test_ctrl_pause_scrolllock_rejected():
    # The one dead combo class (#325) through the production loader: the usual
    # warn-and-keep-default path, same as every other bad entry.
    for combo in ('ctrl+pause', 'ctrl+alt+scrolllock'):
        eff, warns = run({'switch_api': combo})
        only_changed(eff, {})
        assert len(warns) == 1 and 'never fire' in warns[0], (combo, warns)
    # ...while the ctrl-free siblings load warning-free.
    eff, warns = run({'switch_api': 'alt+pause', 'open_history': 'shift+scrolllock'})
    only_changed(eff, {'switch_api': 'alt+pause', 'open_history': 'shift+scrolllock'})
    assert warns == [], warns


def test_duplicate_override_vs_untouched_default():
    # ctrl+alt+4 is exit_program's default -> start_recording reverts, exit stays
    eff, warns = run({'start_recording': 'ctrl+alt+4'})
    only_changed(eff, {})
    assert any('collides' in w for w in warns), warns


def test_duplicate_two_overrides_same_combo():
    eff, warns = run({'start_recording': 'f9', 'switch_api': 'f9'})
    only_changed(eff, {})   # both revert to their defaults
    assert sum('collides' in w for w in warns) >= 2, warns


def test_duplicate_case_and_order_normalized():
    # ALT+CTRL+4 canonicalizes to exit_program's ctrl+alt+4 -> reverts
    eff, warns = run({'start_recording': 'ALT+CTRL+4'})
    only_changed(eff, {})
    assert any('collides' in w for w in warns), warns


def test_free_then_reuse_no_false_collision():
    # start_recording vacates ctrl+alt+w, so exit_program may take it
    eff, warns = run({'start_recording': 'f9', 'exit_program': 'ctrl+alt+w'})
    only_changed(eff, {'start_recording': 'f9', 'exit_program': 'ctrl+alt+w'})
    assert warns == [], warns


def test_shipped_defaults_are_static():
    # #211 / D-012: no shipped default may sit on a key without a static VK code --
    # those are resolved against the ACTIVE layout at startup and fail off QWERTZ.
    # Guards against the self-test (or any default) regressing onto the umlaut.
    for action, combo in DEFAULT_HOTKEYS.items():
        _mods, key = hp.parse_hotkey_lexical(combo)
        assert hp.classify_key(key) == hp.KEY_STATIC, \
            f"D-012: default {action}={combo!r} uses layout-resolved key {key!r}"


def test_shipped_combos_are_canonical():
    # #275: both shipped schemes must already be in the one spelling -- a hand-
    # written 'control+alt+p' or 'alt+ctrl+w' among them would put a second
    # notation into the console lead, the display formatter and the settings
    # app's diff (which compares an effective set against DEFAULT_HOTKEYS).
    for source, table in (("DEFAULT_HOTKEYS", DEFAULT_HOTKEYS),
                          ("settings_io.PRESET_FKEYS", settings_io.PRESET_FKEYS)):
        for action, combo in table.items():
            assert hp.canonical_combo(combo) == combo, \
                f"{source}[{action}] = {combo!r} is not canonical " \
                f"({hp.canonical_combo(combo)!r})"


def _readme_hotkey_column(path):
    """First-column combos of the ## Hotkeys table (backticked, one per row)."""
    text = path.read_text(encoding="utf-8")
    section = text.split("## Hotkeys", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\|\s*`([^`]+)`\s*\|", section, flags=re.M)


def test_readme_hotkey_tables_match_defaults():
    # D-019: the README twins' ## Hotkeys tables track DEFAULT_HOTKEYS -- order
    # AND values (the spirit of test_deps_sync). The canonical order is the dict's,
    # and the tables are the user-facing copy of it.
    expected = [hp.format_combo(DEFAULT_HOTKEYS[a]) for a in DEFAULT_HOTKEYS]
    here = Path(__file__).resolve().parent
    for name in ("README.md", "README.de.md"):
        got = _readme_hotkey_column(here / name)
        if SHOW and got != expected:
            print(f"----- {name} vs DEFAULT_HOTKEYS -----")
            for e, g in zip(expected, got):
                print(f"    {e:14} | {g}{'' if e == g else '   <-- drift'}")
        assert got == expected, (f"{name} ## Hotkeys drifted from DEFAULT_HOTKEYS:\n"
                                 f"  expected {expected}\n  got      {got}")


def test_override_is_canonicalized():
    # An alias-spelled override arrives canonical, so the console lead, the
    # display and the diff all see one spelling.
    eff, warns = run({'switch_api': 'control+alt+p'})
    only_changed(eff, {'switch_api': 'ctrl+alt+p'})
    assert warns == [], warns
    # A reordered spelling of an action's own default is value-equal to it: no
    # collision, and the settings app's diff drops it on the next save.
    eff, warns = run({'start_recording': 'ALT + Control + W'})
    only_changed(eff, {})
    assert warns == [], warns


def test_umlaut_override_rejected():
    # D-023 (#317): the umlaut and its old 'ue' alias are ignored exactly like
    # any unknown key -- one warning, the default stays, never a failed start.
    for combo in ('ctrl+alt+ü', 'ctrl+alt+ue'):
        eff, warns = run({'test_transcription': combo})
        only_changed(eff, {})
        assert any('unrecognized key' in w for w in warns), (combo, warns)


CASES = [
    test_vk_map_fkeys_and_statics,
    test_vk_map_extended_keys,
    test_parse_modifiers_and_key,
    test_parse_bare_fkey,
    test_parse_raises_structural,
    test_classify_key,
    test_canonical_combo,
    test_format_combo,
    test_dead_combo_reason,
    test_first_combo_retired,
    test_shipped_defaults_are_static,
    test_shipped_combos_are_canonical,
    test_readme_hotkey_tables_match_defaults,
    test_common_prefix,
    test_partial_override,
    test_value_shapes,
    test_inner_spaces_canonicalized,
    test_override_is_canonicalized,
    test_umlaut_override_rejected,
    test_unknown_action,
    test_comment_key_ignored,
    test_bad_combos_keep_default,
    test_wrong_value_types_keep_default,
    test_fkey_names_bare_and_legacy_list,
    test_extended_keys_through_loader,
    test_ctrl_pause_scrolllock_rejected,
    test_duplicate_override_vs_untouched_default,
    test_duplicate_two_overrides_same_combo,
    test_duplicate_case_and_order_normalized,
    test_free_then_reuse_no_false_collision,
]


def main():
    if SHOW:
        for raw in ({'start_recording': 'f9'},
                    {'cancel_recording': ['ctrl+f9']},
                    {'start_recording': 'ctrl+alt+4', 'switch_api': 'bogus'}):
            eff, warns = apply_hotkey_overrides(DEFAULTS, raw)
            print(f"----- override {raw} -----")
            for k in DEFAULTS:
                mark = " *" if eff[k] != DEFAULTS[k] else ""
                print(f"    {k}: {eff[k]!r}{mark}")
            for w in warns:
                print(f"    warn: {w}")
            print()

    failures = []
    for case in CASES:
        try:
            case()
            print(f"PASS  {case.__name__}")
        except AssertionError as e:
            failures.append((case.__name__, str(e)))
            print(f"FAIL  {case.__name__}: {e}")
        except Exception as e:  # a crash is also a failure
            failures.append((case.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERROR {case.__name__}: {type(e).__name__}: {e}")

    if failures:
        print(f"\nFAIL: {len(failures)}/{len(CASES)} case(s) failed")
        return 1
    print(f"\nOK: all {len(CASES)} hotkey-override cases pass")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
