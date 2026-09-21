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
`parse_hotkey_lexical`, `classify_key`, `combo_rejection_reason` (the two
rejected classes: dead -- ctrl with pause/scrolllock arrives as VK_CANCEL and
could never fire -- and D-030's invisible keystroke collisions, the tool's own
paste and the German AltGr combos, held against the freedom that stands beside
them), and the one spelling a combo is stored and shown in --
`canonical_combo`, `format_combo` with its `KEY_DISPLAY` short names (#275,
#325), including the guard that both shipped schemes (`DEFAULT_HOTKEYS` and
`settings_io.PRESET_FKEYS`) are written canonically themselves -- and the
drift guard that keeps the README twins' `## Hotkeys` tables on
`DEFAULT_HOTKEYS`, order and combos (D-019). `first_combo`, the picker the
list shape needed, is pinned as retired (D-024). Since #152 also `combo_keys`,
the poll-name list a release-wait guard is handed: the canonical modifier order,
the bare-binding case, and that every name it emits is one `is_key_pressed` can
resolve.

Layer B -- `config.apply_hotkey_overrides` (the pure production loader config
calls verbatim): partial override by action name, warn-and-keep-default on every
kind of bad entry, F-key names, the value shape (one action binds one combo,
D-024 -- a one-element list from an older version collapses silently, any other
list is rejected), duplicate detection on the *effective* set with
case/modifier-order normalization, its one exemption -- the D-029 toggle pair
(start_recording plus exactly one stop action), accepted in both half forms and
held to exactly that shape by the negatives the pre-existing collision cases
cannot cover -- and the guarantees that the defaults dict is
never mutated and that their order, the canonical action order every surface
follows (D-019), survives the one loader it passes through. Plus the two pure
derivations that pair feeds, `config.toggle_stop_action` (the partner by name)
and `config.hotkey_registration_plan` (the partner skipped by name, which is
what keeps `expected_count` honest). Plus
`config.mistrigger_key_map` (#152), the second pure consumer of an effective
scheme: the shipped map, both skip rules (a token two candidates share, the
start action's own key), a partial rebind, and the shipped F-key preset, where
both rules together leave the net deliberately empty.

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
from config import (apply_hotkey_overrides, mistrigger_key_map, toggle_stop_action,
                    hotkey_registration_plan, TOGGLE_STOP_ACTIONS, DEFAULT_HOTKEYS)

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


def _reason(combo):
    """The checkpoint's verdict on a written combo, through the real parser."""
    mods, key = hp.parse_hotkey_lexical(combo)
    return hp.combo_rejection_reason(mods, key)


def test_combo_rejection_reason_dead_class():
    # #325's technical rejection: held Ctrl shifts the scancode of Pause and
    # Scroll Lock, so both arrive as VK_CANCEL -- the combo registers, never fires
    # (the #308/D-022 "assignable but dead" class).
    for combo in ('ctrl+pause', 'ctrl+alt+scrolllock', 'ctrl+shift+pause',
                  'ctrl+alt+shift+win+scrolllock'):
        assert _reason(combo), f"{combo!r} should be dead"
    # ...and nothing else is: ctrl-free siblings, bare keys, ctrl with any other key.
    for combo in ('alt+pause', 'shift+scrolllock', 'pause', 'scrolllock',
                  'ctrl+alt+home', 'ctrl+p', 'ctrl+alt+numadd'):
        assert _reason(combo) is None, f"{combo!r} should be live"
    # The checkpoint answers the same whether or not MOD_NOREPEAT rides along:
    # parse_hotkey_lexical always sets it, test_console_ui asks with plain
    # MODIFIER_MAP flags.
    assert hp.combo_rejection_reason(hp.MOD_CONTROL, 'pause')
    assert hp.combo_rejection_reason(hp.MOD_CONTROL | hp.MOD_NOREPEAT, 'pause')


def test_paste_and_altgr_combos_rejected():
    # D-030's class: the combo registers AND fires, on a keypress nobody can see
    # coming -- so every message names the mechanism it collides with.
    reason = _reason('ctrl+v')
    assert reason and 'paste the tool itself sends' in reason, reason
    # All nine German AltGr combos, driven off the frozen table itself, so a key
    # added there arrives here with its own case.
    assert set(hp._ALTGR_DE) == set('qem237890'), hp._ALTGR_DE
    assert set(hp._ALTGR_DE) <= set(hp.VK_MAP), \
        "an AltGr key outside VK_MAP would make its rule unreachable"
    for key in hp._ALTGR_DE:
        reason = _reason(f'ctrl+alt+{key}')
        assert reason and 'AltGr' in reason, (key, reason)
    assert '\N{EURO SIGN}' in _reason('ctrl+alt+e')   # names the character it steals
    assert '@' in _reason('ctrl+alt+q')
    # Spelling cannot dodge the checkpoint: it sees the parsed pair, so aliases,
    # case and modifier order collapse before it.
    for spelling in ('alt+ctrl+e', 'CONTROL+ALT+E', 'Ctrl + Alt + E'):
        assert _reason(spelling), spelling
    # What stays bindable, on purpose (D-027's no-paternalism line, in force):
    # a letter or digit bare or with shift alone is a VISIBLE sacrifice -- the
    # typed insert route sends no such keystroke (KEYEVENTF_UNICODE), so the
    # tool has no stake in it. And since RegisterHotKey matches modifiers
    # exactly, 'v' with any other modifier set and ctrl+alt+shift+<AltGr key>
    # collide with nothing the user or the tool types.
    for combo in ('a', 'z', '0', '9', 'v', 'e', 'shift+a', 'shift+5', 'shift+e',
                  'ctrl+shift+v', 'ctrl+alt+v', 'alt+v', 'win+v', 'ctrl+a',
                  'ctrl+shift+e', 'ctrl+alt+shift+e', 'alt+e', 'win+e',
                  'ctrl+alt+5', 'ctrl+alt+1', 'ctrl+alt+4', 'ctrl+alt+6',
                  'num7', 'num0', 'shift+num5', 'ctrl+alt+num7',
                  'f9', 'shift+f8', 'ctrl+alt+numadd'):
        assert _reason(combo) is None, f"{combo!r} must stay bindable: {_reason(combo)}"


def test_shipped_schemes_carry_no_rejected_combo():
    # The delivery guard for D-030: neither shipped scheme may hold a combo the
    # checkpoint rejects -- that would warn on a fresh install and leave the
    # action on a default it just refused.
    for source, table in (("DEFAULT_HOTKEYS", DEFAULT_HOTKEYS),
                          ("settings_io.PRESET_FKEYS", settings_io.PRESET_FKEYS)):
        for action, combo in table.items():
            assert _reason(combo) is None, \
                f"{source}[{action}] = {combo!r} is rejected: {_reason(combo)}"


def test_first_combo_retired():
    # D-024 (#318): one action binds one combo, so there is no first one to pick --
    # first_combo went with the list shape. Pin the removal: a helper that takes a
    # binding and returns one of several is the door back to multi-combo actions.
    assert not hasattr(hp, 'first_combo'), \
        "hotkey_parse.first_combo is back -- D-024 retired it with the list shape"


def test_combo_keys():
    # #152: the poll names a release-wait guard hands is_key_pressed -- modifiers
    # first in the canonical order, the key last.
    assert hp.combo_keys('ctrl+alt+h') == ['ctrl', 'alt', 'h']
    assert hp.combo_keys('f10') == ['f10']            # bare binding: just the key
    assert hp.combo_keys('ctrl+shift+f10') == ['ctrl', 'shift', 'f10']
    assert hp.combo_keys('ALT+Ctrl+W') == ['ctrl', 'alt', 'w']   # canonical order
    assert hp.combo_keys('win+shift+alt+control+f24') == \
        ['ctrl', 'alt', 'shift', 'win', 'f24']
    assert hp.combo_keys('ctrl+num7') == ['ctrl', 'num7']
    # A nav token also reads its numpad twin with Num Lock off ('home' <-> Numpad-7):
    # a property of the shared VKs, documented in combo_keys, not special-cased.
    assert hp.combo_keys('home') == ['home']
    # The contract that makes the list usable: every emitted name resolves in
    # hotkey_manager.VK_KEY_MAP -- the modifiers as the canonical four, the key as
    # its VK_MAP token. A name outside it would poll as never-pressed, i.e. a
    # guard that silently does not guard.
    for combo in ('ctrl+alt+h', 'f10', 'ctrl+shift+f10', 'shift+win+home',
                  'win+shift+alt+control+f24', 'num7'):
        *mods, key = hp.combo_keys(combo)
        assert set(mods) <= {'ctrl', 'alt', 'shift', 'win'}, combo
        assert key in hp.VK_MAP, combo
    # Same spelling source as canonical_combo, and the same error path: never a
    # modifier-only list.
    for combo in ('ctrl+alt+h', 'f10', 'CTRL + Alt + 6'):
        assert '+'.join(hp.combo_keys(combo)) == hp.canonical_combo(combo)
    for bad in ('ctrl+alt', 'ctrl+alt+a+b'):
        try:
            hp.combo_keys(bad)
            assert False, f"expected HotkeyParseError for {bad!r}"
        except hp.HotkeyParseError:
            pass
    # '' is not a parse error -- it yields the empty key token, which the loader
    # rejects via classify_key. So what keeps an unpollable name out of a
    # release-wait list is that validation, not this function.
    assert hp.combo_keys('') == [''] and hp.classify_key('') == hp.KEY_INVALID


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
                           ('exit_program', ['ctrl+alt+5'])):
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


def test_paste_and_altgr_overrides_rejected():
    # D-030 through the production loader: the same warn-and-keep-default path,
    # each warning naming the entry and the mechanism, as it reaches
    # thoughtborne.log and the console replay.
    eff, warns = run({'cancel_recording': 'ctrl+v', 'switch_api': 'ctrl+alt+e'})
    only_changed(eff, {})
    assert len(warns) == 2, warns
    assert any(w.startswith("hotkeys.cancel_recording: 'ctrl+v' -- ")
               and 'paste the tool itself sends' in w for w in warns), warns
    assert any(w.startswith("hotkeys.switch_api: 'ctrl+alt+e' -- ")
               and 'AltGr' in w for w in warns), warns
    # The rejection runs per entry, before the collision loop, so the rejected
    # action keeps its default and a second override onto that default collides
    # exactly as it always did -- no new kind of outcome.
    eff, warns = run({'start_recording': 'ctrl+v', 'switch_api': 'ctrl+alt+w'})
    only_changed(eff, {})
    assert any('collides' in w for w in warns), warns


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


def test_toggle_pair_accepted_without_warning():
    # D-029, the one exempt duplicate: start_recording plus ONE stop action on
    # the same combo. Both values stand, and no warning is owed -- the recording
    # state decides which of the two fires, so neither loses silently.
    eff, warns = run({'start_recording': 'pause', 'stop_recording_keyboard': 'pause'})
    only_changed(eff, {'start_recording': 'pause', 'stop_recording_keyboard': 'pause'})
    assert warns == [], warns
    # The exemption reads the parsed form, like the collision grouping itself:
    # a different spelling of the same combo is the same pair.
    eff, warns = run({'start_recording': 'ALT+CTRL+f9',
                      'stop_recording_clipboard': 'ctrl + alt + F9'})
    only_changed(eff, {'start_recording': 'ctrl+alt+f9',
                       'stop_recording_clipboard': 'ctrl+alt+f9'})
    assert warns == [], warns


def test_toggle_stop_half_alone_onto_default_start():
    # The likeliest hand-edit form: only the stop half listed, on the shipped
    # start combo. Its own path through the revert loop -- an override colliding
    # with an UNTOUCHED default, which reverts in
    # test_duplicate_override_vs_untouched_default and must stand here.
    eff, warns = run({'stop_recording_clipboard': 'ctrl+alt+w'})
    only_changed(eff, {'stop_recording_clipboard': 'ctrl+alt+w'})
    assert warns == [], warns


def test_toggle_start_half_alone_onto_a_stop_default():
    # The mirror half: start_recording moved onto a stop action's untouched
    # default. The loop knows no direction, so this forms the pair too --
    # pinned as a decision rather than left to drift.
    eff, warns = run({'start_recording': 'ctrl+alt+a'})     # stop_recording_keyboard's
    only_changed(eff, {'start_recording': 'ctrl+alt+a'})
    assert warns == [], warns


def test_toggle_exemption_is_exactly_the_pair():
    # The negatives are what make the exemption a decision instead of a
    # loosening: everything that is not start plus ONE stop action collides
    # exactly as before, with the unchanged warning and the defaults in force.
    # (The pre-existing collision cases all pair non-stop actions, so they would
    # stay green under a rule that was too broad.)
    for raw, expected_warnings in (
            # start plus two stops: no state can decide between the two stops
            ({'start_recording': 'pause', 'stop_recording_keyboard': 'pause',
              'stop_recording_clipboard': 'pause'}, 3),
            # cancel is deliberately no partner -- a toggle that discards would
            # be a different feature (D-029)
            ({'start_recording': 'pause', 'cancel_recording': 'pause'}, 2),
            # ...nor is any non-stop action
            ({'start_recording': 'pause', 'switch_api': 'pause'}, 2),
            # ...nor two stops among themselves, with no start to resolve them
            ({'stop_recording_keyboard': 'pause',
              'stop_recording_clipboard': 'pause'}, 2)):
        eff, warns = run(raw)
        only_changed(eff, {})
        assert sum('collides' in w for w in warns) == expected_warnings, (raw, warns)


def test_toggle_stop_action_derivation():
    # The pure partner lookup every consumer reads -- dispatch, registration
    # plan, settings-app feedback.
    assert toggle_stop_action(DEFAULTS) is None
    eff, _ = run({'start_recording': 'pause', 'stop_recording_clipboard': 'pause'})
    assert toggle_stop_action(eff) == 'stop_recording_clipboard'
    eff, _ = run({'stop_recording_keyboard': 'ctrl+alt+w'})
    assert toggle_stop_action(eff) == 'stop_recording_keyboard'
    # Parsed comparison, not string equality: a hand-written spelling neither
    # hides nor fakes a pair.
    assert toggle_stop_action(dict(DEFAULTS, start_recording='Alt + Control + W',
                                   stop_recording_clipboard='ctrl+alt+w')) \
        == 'stop_recording_clipboard'
    # No shipped scheme forms a pair: a toggle is something the user opts into,
    # and a preset that grew one would turn a stop key into a start key for
    # everyone who clicks it.
    for scheme in (DEFAULT_HOTKEYS, settings_io.PRESET_FKEYS):
        assert toggle_stop_action(scheme) is None, scheme
    # Defensive, for shapes the loader cannot produce.
    assert toggle_stop_action({}) is None
    assert toggle_stop_action(dict(DEFAULTS, start_recording='ctrl+alt')) is None
    assert toggle_stop_action(dict(DEFAULTS, stop_recording_keyboard='ctrl+alt+w',
                                   stop_recording_clipboard='ctrl+alt+w')) is None
    assert toggle_stop_action(dict(DEFAULTS, cancel_recording='ctrl+alt+w')) is None


def test_registration_plan_dedupes_the_partner():
    # Without the skip the second RegisterHotKey on the shared combo fails with
    # 1409, and every start would report a hotkey shortfall.
    assert hotkey_registration_plan(DEFAULTS) == list(DEFAULTS.items())
    eff, _ = run({'start_recording': 'pause', 'stop_recording_clipboard': 'pause'})
    plan = hotkey_registration_plan(eff)
    assert [a for a, _ in plan] == [a for a in eff if a != 'stop_recording_clipboard']
    assert ('start_recording', 'pause') in plan
    assert len(plan) == len(eff) - 1
    # No partner derived -> nothing skipped, whatever the scheme looks like.
    ambiguous = dict(DEFAULTS, stop_recording_keyboard='ctrl+alt+w',
                     stop_recording_clipboard='ctrl+alt+w')
    assert hotkey_registration_plan(ambiguous) == list(ambiguous.items())


def test_toggle_stop_set_matches_the_shipped_stops():
    # Drift guard: the literal frozenset is held to the real action names
    # without being given an order of its own (D-019).
    assert TOGGLE_STOP_ACTIONS == {a for a in DEFAULT_HOTKEYS
                                   if a.startswith('stop_recording_')}


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


# The five actions the mis-trigger net can correct to, as thoughtborne.py hands
# them in -- a container of action names, order irrelevant (the map's order comes
# from the scheme, D-019).
MISTRIGGER_CANDIDATES = ('stop_recording_clipboard', 'stop_recording_send',
                         'stop_recording_no_insert', 'stop_recording_keyboard',
                         'cancel_recording')


def test_mistrigger_map_shipped_scheme():
    # #152: the net's key list derived from the shipped scheme -- the five
    # candidates in the canonical action order, each on its own key token.
    assert mistrigger_key_map(DEFAULT_HOTKEYS, MISTRIGGER_CANDIDATES) == [
        ('a', 'stop_recording_clipboard'),
        ('d', 'stop_recording_send'),
        ('y', 'stop_recording_no_insert'),
        ('h', 'stop_recording_keyboard'),
        ('x', 'cancel_recording'),
    ]
    # Non-candidates never enter it, whatever they are bound to.
    assert all(a in MISTRIGGER_CANDIDATES
               for _k, a in mistrigger_key_map(DEFAULT_HOTKEYS, MISTRIGGER_CANDIDATES))


def test_mistrigger_map_partial_rebind():
    # One action moved: its new token replaces the old one IN PLACE (canonical
    # order), everything else byte-identical to the shipped map.
    hk = dict(DEFAULT_HOTKEYS, stop_recording_send='ctrl+shift+f2')
    assert mistrigger_key_map(hk, MISTRIGGER_CANDIDATES) == [
        ('a', 'stop_recording_clipboard'),
        ('f2', 'stop_recording_send'),
        ('y', 'stop_recording_no_insert'),
        ('h', 'stop_recording_keyboard'),
        ('x', 'cancel_recording'),
    ]


def test_mistrigger_map_ambiguous_token_drops_all_bearers():
    # Two candidates on the same key token via DIFFERENT combos: polling that key
    # cannot tell which action was meant, so BOTH drop out -- a token-keyed dict
    # would have kept one of them and fired the wrong action.
    hk = dict(DEFAULT_HOTKEYS, stop_recording_clipboard='ctrl+alt+p',
              cancel_recording='alt+p')
    got = mistrigger_key_map(hk, MISTRIGGER_CANDIDATES)
    assert all(k != 'p' for k, _a in got), got
    assert [a for _k, a in got] == ['stop_recording_send',
                                    'stop_recording_no_insert',
                                    'stop_recording_keyboard']
    # Ambiguity is measured within the candidate set only: a non-candidate on the
    # same token cannot be mis-fired through this net (its own hotkey calls its
    # own callback), so it must not disarm a healthy entry.
    hk = dict(DEFAULT_HOTKEYS, open_history='ctrl+alt+shift+a')
    assert ('a', 'stop_recording_clipboard') in mistrigger_key_map(hk, MISTRIGGER_CANDIDATES)


def test_mistrigger_map_exempts_the_start_key():
    # The start action's own key is physically down on every second press of the
    # start hotkey, so an entry on it would stop the running recording every time.
    hk = dict(DEFAULT_HOTKEYS, cancel_recording='ctrl+w')
    got = mistrigger_key_map(hk, MISTRIGGER_CANDIDATES)
    assert all(k != 'w' for k, _a in got), got
    assert len(got) == 4
    # A scheme without a usable start action simply grants no exemption.
    hk = {k: v for k, v in DEFAULT_HOTKEYS.items() if k != 'start_recording'}
    assert len(mistrigger_key_map(hk, MISTRIGGER_CANDIDATES)) == 5


def test_mistrigger_map_fkey_preset_is_empty():
    # The shipped one-click F-key preset: start on bare f9, f10 borne by three
    # candidates and f9 by two more -- ambiguity plus the start exemption empty
    # the map. Pinned because it is a shipped configuration, not a hand-edited
    # corner: the net is deliberately inert there rather than firing the wrong
    # action, and what that preset gains from #152 is the release-wait lists.
    assert mistrigger_key_map(settings_io.PRESET_FKEYS, MISTRIGGER_CANDIDATES) == []


def test_mistrigger_map_survives_broken_entries():
    # Never raises on a hand-edited default: an unparseable combo costs its own
    # entry, not the whole net (which would take the healthy entries with it).
    hk = dict(DEFAULT_HOTKEYS, stop_recording_keyboard='ctrl+alt')
    got = mistrigger_key_map(hk, MISTRIGGER_CANDIDATES)
    assert [a for _k, a in got] == ['stop_recording_clipboard', 'stop_recording_send',
                                    'stop_recording_no_insert', 'cancel_recording']
    assert mistrigger_key_map({}, MISTRIGGER_CANDIDATES) == []


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
    test_combo_rejection_reason_dead_class,
    test_paste_and_altgr_combos_rejected,
    test_first_combo_retired,
    test_shipped_defaults_are_static,
    test_shipped_schemes_carry_no_rejected_combo,
    test_shipped_combos_are_canonical,
    test_readme_hotkey_tables_match_defaults,
    test_combo_keys,
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
    test_paste_and_altgr_overrides_rejected,
    test_duplicate_override_vs_untouched_default,
    test_duplicate_two_overrides_same_combo,
    test_duplicate_case_and_order_normalized,
    test_free_then_reuse_no_false_collision,
    test_toggle_pair_accepted_without_warning,
    test_toggle_stop_half_alone_onto_default_start,
    test_toggle_start_half_alone_onto_a_stop_default,
    test_toggle_exemption_is_exactly_the_pair,
    test_toggle_stop_action_derivation,
    test_registration_plan_dedupes_the_partner,
    test_toggle_stop_set_matches_the_shipped_stops,
    test_mistrigger_map_shipped_scheme,
    test_mistrigger_map_partial_rebind,
    test_mistrigger_map_ambiguous_token_drops_all_bearers,
    test_mistrigger_map_exempts_the_start_key,
    test_mistrigger_map_fkey_preset_is_empty,
    test_mistrigger_map_survives_broken_entries,
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
