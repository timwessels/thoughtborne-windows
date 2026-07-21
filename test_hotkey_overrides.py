#!/usr/bin/env python3
"""Unit tests for the #55 hotkey/default-engine override surface.

Runs on plain Python -- no Windows, no ctypes -- so the two pure layers behind
`personal_settings.json` hotkey overrides are exercised programmatically (a
durable regression guard, sibling of `test_console_ui.py`).

    python3 test_hotkey_overrides.py          # verify, exit non-zero on failure
    python3 test_hotkey_overrides.py --show    # also print a few effective sets

Layer A -- `hotkey_parse` (the ctypes-free lexical layer): the static VK map
(letters, digits, F1-F24), the structural parser `parse_hotkey_lexical`, and
`classify_key`.

Layer B -- `config.apply_hotkey_overrides` (the pure production loader config
calls verbatim): partial override by action name, warn-and-keep-default on every
kind of bad entry, F-key names, list vs. string shapes (a string-default action
stays a string, only already-list actions multi-bind -- the maintainer's #55
decision), duplicate detection on the *effective* set with case/modifier-order
normalization, and the guarantee that the defaults dict is never mutated.

The did-the-key-actually-fire check is hands-on (RegisterHotKey needs Windows)
and is tracked in a separate `test` issue.
"""
import copy
import logging
import sys

# Silence config's import-time settings warnings -- importing config parses the
# repo's real personal_settings.json, which may legitimately warn; irrelevant to
# these pure-function tests. (Same approach test_retry_marker_lifecycle uses.)
logging.getLogger('Thoughtborne.Config').setLevel(logging.CRITICAL)

import hotkey_parse as hp
from config import apply_hotkey_overrides

SHOW = "--show" in sys.argv

# Own fixture mirroring the real HOTKEYS shape (str-default and list-default
# actions, plus the umlaut and a digit key) -- NOT config.HOTKEYS, which the
# repo's own personal_settings.json might already have overridden.
DEFAULTS = {
    'start_recording': 'ctrl+alt+w',
    'stop_recording_keyboard': 'ctrl+alt+a',
    'stop_recording_clipboard': 'ctrl+alt+d',
    'switch_api': 'ctrl+alt+l',
    'test_transcription': 'ctrl+alt+ü',
    'open_history': 'ctrl+alt+6',
    'cancel_recording': ['ctrl+alt+x'],
    'exit_program': ['ctrl+alt+4'],
}
_DEFAULTS_SNAPSHOT = copy.deepcopy(DEFAULTS)


def run(raw):
    """Call the production loader and bake in the always-true invariants:
    DEFAULTS is never mutated (guards the deepcopy), the effective set has exactly
    the default keys, and warnings is a list of strings (tests 17 + 18)."""
    eff, warns = apply_hotkey_overrides(DEFAULTS, raw)
    assert DEFAULTS == _DEFAULTS_SNAPSHOT, "apply_hotkey_overrides mutated DEFAULTS"
    assert set(eff) == set(DEFAULTS), "effective keys differ from defaults"
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
    assert hp.classify_key('ü') == hp.KEY_SPECIAL     # single char -> runtime resolve
    assert hp.classify_key('ue') == hp.KEY_SPECIAL    # known alias
    assert hp.classify_key('foo') == hp.KEY_INVALID   # multi-char non-key
    assert hp.classify_key('') == hp.KEY_INVALID      # empty token (e.g. 'ctrl+alt+')


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
    # list override on a list-default action -> genuine multi-binding
    eff, warns = run({'exit_program': ['ctrl+alt+4', 'ctrl+alt+q']})
    only_changed(eff, {'exit_program': ['ctrl+alt+4', 'ctrl+alt+q']})
    assert warns == [], warns
    # string override on a string-default action -> stays a string
    eff, warns = run({'switch_api': 'ctrl+alt+p'})
    only_changed(eff, {'switch_api': 'ctrl+alt+p'})
    assert warns == [], warns
    # one-element list on a string-default action collapses to a string
    eff, warns = run({'switch_api': ['ctrl+alt+p']})
    only_changed(eff, {'switch_api': 'ctrl+alt+p'})
    assert warns == [], warns
    # multi-element list on a string-default action is rejected (keeps default)
    eff, warns = run({'switch_api': ['ctrl+alt+p', 'ctrl+alt+j']})
    only_changed(eff, {})
    assert any('multiple combos' in w for w in warns), warns


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
    for bad in (5, True, {}, [], ['ctrl+alt+l', 7]):
        eff, warns = run({'switch_api': bad})
        only_changed(eff, {})
        assert warns, f"expected a warning for switch_api={bad!r}"


def test_fkey_names_bare_and_list():
    # F-key name, a bare (modifier-less) F-key, list collapse, and lowercasing
    eff, warns = run({'start_recording': 'F13', 'test_transcription': ['f24']})
    only_changed(eff, {'start_recording': 'f13', 'test_transcription': 'f24'})
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


def test_duplicate_within_same_action_list():
    # One combo listed twice inside a single action's list is a *self*-duplicate,
    # not a cross-action collision -- honest wording, and the action reverts to its
    # default (SOLLTE 4).
    eff, warns = run({'exit_program': ['ctrl+alt+4', 'ctrl+alt+4']})
    only_changed(eff, {})
    assert any('more than once' in w for w in warns), warns
    assert not any('collides with another action' in w for w in warns), warns


def test_duplicate_case_and_order_normalized():
    # ALT+CTRL+4 canonicalizes to exit_program's ctrl+alt+4 -> reverts
    eff, warns = run({'start_recording': 'ALT+CTRL+4'})
    only_changed(eff, {})
    assert any('collides' in w for w in warns), warns


def test_free_then_reuse_no_false_collision():
    # start_recording vacates ctrl+alt+w, so exit_program may take it
    eff, warns = run({'start_recording': 'f9', 'exit_program': 'ctrl+alt+w'})
    only_changed(eff, {'start_recording': 'f9', 'exit_program': ['ctrl+alt+w']})
    assert warns == [], warns


CASES = [
    test_vk_map_fkeys_and_statics,
    test_parse_modifiers_and_key,
    test_parse_bare_fkey,
    test_parse_raises_structural,
    test_classify_key,
    test_common_prefix,
    test_partial_override,
    test_value_shapes,
    test_inner_spaces_canonicalized,
    test_unknown_action,
    test_comment_key_ignored,
    test_bad_combos_keep_default,
    test_wrong_value_types_keep_default,
    test_fkey_names_bare_and_list,
    test_duplicate_override_vs_untouched_default,
    test_duplicate_two_overrides_same_combo,
    test_duplicate_within_same_action_list,
    test_duplicate_case_and_order_normalized,
    test_free_then_reuse_no_false_collision,
]


def main():
    if SHOW:
        for raw in ({'start_recording': 'f9'},
                    {'exit_program': ['ctrl+alt+4', 'ctrl+alt+q']},
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


if __name__ == "__main__":
    sys.exit(main())
