#!/usr/bin/env python3
"""Width and charset verification for the Cockpit console renderer (#109).

Runs on plain Python -- no Windows, no audio, no hotkeys -- so `console_ui`'s
every emitted panel/strip is checked programmatically (the #109 acceptance
point). `config` is import-safe off Windows, so fixtures use the real labels,
hotkeys and carousel order.

    python3 test_console_ui.py          # verify, exit non-zero on any violation
    python3 test_console_ui.py --show   # also print every screen for eyeballing

What each rendered block is checked for:
  1. framed: every SGR-stripped line is exactly 70 cells; corners are correct.
  2. plain twin: ansi=False render is line-for-line the same length as the
     SGR-stripped ansi=True render (frames stay aligned), carries no ESC, and
     is ASCII + the single allowed umlaut U-umlaut (the self-test hotkey).
  3. ansi=True: every non-ASCII glyph is in the CP437 safe set.
  4. red (SGR 31) appears only in error renderings.
  5. KEYS grid anchored at columns 24/46 and reading W A D / Y H X / R L 6 /
     G T 4, beneath the headers of #276 (the shared Ctrl+Alt lead, and the MODEL
     header in the SWITCHED panel's one form); OK/WAITING seq block at column 41.
     Every key fixture is built from config.DEFAULT_HOTKEYS with the app's own
     helpers (D-019), so no copy of the canonical action order lives here.
  6. logo fold-in (#109): the active a5 masthead mark renders in ANSI and drops
     in the plain twin; every routine strip carries the monochrome bullet header
     (with its plain 'o' twin) plus one headroom line.
  7. the pair contract (D-019): the renderer's key tables cover the shipped
     actions, the cell budgets stay derived, and every key-bearing surface --
     grid, strips, footers and panels -- holds its geometry and shows the right
     key under rebound hotkey schemes, prose naming the full combo.
  8. the app half of that contract, read as source: `thoughtborne.py` derives no
     key of its own and hands its pairs out in the canonical order unreordered,
     and a stress sweep over every legal combo length proves no framed line can
     leave 70 cells.
"""
import ast
import inspect
import itertools
import re
import sys
from pathlib import Path

import console_ui as u
import hotkey_parse as hp   # the canonical modifier order + key set the ladder derives from
import settings_io          # PRESET_FKEYS as an override fixture; stdlib-only
from config import (API_DISPLAY, API_KEY_ENV, AVAILABLE_APIS, DEFAULT_API,
                    DEFAULT_HOTKEYS, LOG_FILE, engine_has_key)
from hotkey_parse import canonical_combo, common_prefix, first_combo, format_combo

SHOW = "--show" in sys.argv

_SGR = re.compile(r"\x1b\[[0-9;]*m")
def strip(s):
    return _SGR.sub("", s)

# CP437 safe set (terminal-constraints.md) + the U-umlaut, no longer a shipped
# default since #211 but still bindable as a user override (D-012)
# + U+2022 bullet (conhost best-fits it to 0x07; see charset-korrektur-bullet.md).
SAFE = set("─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬█▓▒░▀▄▌▐■Ü•")

RED_OK = {  # renderings allowed to carry red (error states)
    "transcription_failed", "insert_failed", "selftest_failed",
    "device_loss", "mic_failed", "hotkeys_failed", "switch_failed",
}

failures = []
shown = []


def _record(msg):
    failures.append(msg)


def check_block(name, lines, *, ansi, red_key=None):
    """Generic per-block assertions (widths, corners, charset, red exclusivity).
    `red_key` names the rendering for the red rule when `name` also carries the
    scheme it was rendered under (check_footer_schemes)."""
    red_key = name if red_key is None else red_key
    joined = "".join(lines)
    for i, ln in enumerate(lines):
        v = strip(ln)
        if len(v) > u.MAXCOL:
            _record(f"{name}[{i}] len {len(v)} > {u.MAXCOL}: {v!r}")
        if v != "" and len(v) != u.W:
            _record(f"{name}[{i}] framed len {len(v)} != {u.W}: {v!r}")
            continue
        if v:
            corners_l = "╔╠╚║┌└│" if ansi else "+|"
            corners_r = "╗╣╝║┐┘│" if ansi else "+|"
            if v[0] not in corners_l or v[-1] not in corners_r:
                _record(f"{name}[{i}] bad frame edges: {v!r}")
        # charset
        if ansi:
            for ch in v:
                if ord(ch) >= 128 and ch not in SAFE:
                    _record(f"{name}[{i}] non-CP437 glyph {ch!r}: {v!r}")
        else:
            if "\x1b" in ln:
                _record(f"{name}[{i}] plain line carries ESC: {ln!r}")
            for ch in ln:
                # Ü is the one allowed non-ASCII glyph in the plain twin: override-only
                # since #211/D-012 (no shipped default uses it), still render-covered.
                if ord(ch) >= 128 and ch != "Ü":
                    _record(f"{name}[{i}] plain non-ASCII {ch!r}: {ln!r}")
    # red exclusivity (only checkable on the styled ansi render)
    if ansi:
        codes = re.findall(r"\x1b\[([0-9;]+)m", joined)
        has_red = any("31" in c.split(";") for c in codes)
        if has_red and red_key not in RED_OK:
            _record(f"{name} uses red (SGR 31) but is not an error rendering")
        if not has_red and red_key in RED_OK:
            _record(f"{name} is an error rendering but carries no red tag")


def twin(name, fn, **kw):
    """The ansi=False render must be line-for-line length-equal to the SGR-
    stripped ansi=True render. Skipped for the wordmark masthead (the 3-row
    wordmark deliberately collapses to one plain line, changing the count)."""
    a = fn(ansi=True, **kw)
    p = fn(ansi=False, **kw)
    if len(a) != len(p):
        _record(f"{name}: plain line count {len(p)} != ansi {len(a)}")
        return
    for i, (la, lp) in enumerate(zip(a, p)):
        if len(strip(la)) != len(lp):
            _record(f"{name}[{i}] twin length {len(lp)} != {len(strip(la))}: "
                    f"A={strip(la)!r} P={lp!r}")


# ---- fixtures from the real config -------------------------------------------
def lineup_for(current):
    """All engines keyed -- the normal, fully-configured masthead: the `current`
    row is bold, the rest plain, none greyed. #200 replaced the 4th field
    (is_default) with has_key; a fully-keyed lineup is the regression baseline."""
    return [(API_DISPLAY[a]["label"], API_DISPLAY[a]["descriptor"],
             a == current, True) for a in AVAILABLE_APIS]


def lineup_keyed(current, present):
    """A lineup with only `present` (a set of env-var names) configured, so a row
    whose key is absent carries has_key=False and renders dim (#200). Pass
    current=None for the fully-keyless shop-window (no bold row, every row grey)."""
    return [(API_DISPLAY[a]["label"], API_DISPLAY[a]["descriptor"],
             a == current, API_KEY_ENV[a] in present) for a in AVAILABLE_APIS]


def pairs_for(scheme, names=None):
    """The app's _pairs(), rebuilt from the same functions -- the order comes
    from the scheme dict's own iteration, so no copy of it lives here (D-019).
    Built on the pristine DEFAULT_HOTKEYS rather than the effective HOTKEYS: the
    anchor assertions below assume the shipped scheme, and a checkout with its
    own personal_settings.json override must not break the ladder."""
    return [(n, combo_for(scheme, n))
            for n in scheme if names is None or n in names]


def combo_for(scheme, name):
    """One display combo out of a scheme -- pairs_for for a single action, so a
    fixture's switch_key/start_key come from the very scheme it renders."""
    return format_combo(first_combo(scheme[name]))


PAIRS = pairs_for(DEFAULT_HOTKEYS)
_prefix = common_prefix([first_combo(v) for v in DEFAULT_HOTKEYS.values()])
KEY_PREFIX = format_combo(_prefix) if _prefix else None
# Which actions a box shows -- sets, deliberately: pairs_for supplies the order.
REC_ACTIONS = {"stop_recording_clipboard", "stop_recording_send",
               "stop_recording_no_insert", "stop_recording_keyboard",
               "cancel_recording"}
WAIT_ACTIONS = {"stop_recording_clipboard", "stop_recording_keyboard"}
REC_STOPS = pairs_for(DEFAULT_HOTKEYS, REC_ACTIONS)
WAIT_STOPS = pairs_for(DEFAULT_HOTKEYS, WAIT_ACTIONS)

# the one MODEL header form (#276): masthead, switched and switch_failed alike.
# Through combo_for, like every other fixture combo, so a list-shaped binding on
# any of these actions keeps yielding the one combo a surface shows.
SWITCH = combo_for(DEFAULT_HOTKEYS, "switch_api")
PASTE = combo_for(DEFAULT_HOTKEYS, "stop_recording_clipboard")
OPEN = combo_for(DEFAULT_HOTKEYS, "open_history")
START = combo_for(DEFAULT_HOTKEYS, "start_recording")
RETRY = combo_for(DEFAULT_HOTKEYS, "retry_last_failed")
# The #115 footer order, which is deliberately NOT the canonical one (D-019):
# the app owns it in thoughtborne._footer_actions, and this driver cannot import
# the app -- hence the one copy in the ladder. check_app_derives_no_key below
# reads that order out of the app's source and holds this copy against it.
FOOTER_ACTIONS = ["start_recording", "open_history", "switch_api", "exit_program"]
FOOTER_ACTIONS_RETRY = ["start_recording", "retry_last_failed", "switch_api",
                        "exit_program"]


def footer_for(scheme, retry=False):
    """The app's _footer_keys(), rebuilt: [(action_name, display_combo)] in the
    #115 footer order -- record . history/retry . model . quit."""
    names = FOOTER_ACTIONS_RETRY if retry else FOOTER_ACTIONS
    return [(n, combo_for(scheme, n)) for n in names]


FOOTER = footer_for(DEFAULT_HOTKEYS)
FFOOTER = footer_for(DEFAULT_HOTKEYS, retry=True)

PATHS = [  # four checkout depths, shallow to deep (console width stress)
    r"C:\thoughtborne",
    r"D:\Drive\_Code\thoughtborne",
    r"C:\Users\Sample User\Documents\thoughtborne",
    r"C:\Users\SecondUser\Downloads\thoughtborne-windows-main",
]
# #200 keyless shop-window guidance line under the lineup (the app composes it
# from the live open-settings combo; here the shipped Ctrl+Alt+G).
GUIDANCE = "To enable dictation, enter an API key in Settings (Ctrl+Alt+G)"


def run(name, fn, kwargs):
    for ansi in (True, False):
        lines = fn(ansi=ansi, **kwargs)
        check_block(name, lines, ansi=ansi)
        if SHOW and ansi:
            shown.append((name, lines))


# ---- #109 logo fold-in: active a5 mark, bullet strip header, +1 headroom -----
def check_logo_state():
    """Exercise the branding once it is switched on: the a5 mark in the masthead,
    the `• THOUGHTBORNE` / `o THOUGHTBORNE` header and its headroom on every
    routine strip."""
    if u.ACTIVE_LOGO_MARK is not u.LOGO_MARK_A5:
        _record("ACTIVE_LOGO_MARK is not the a5 mark")
    if u.ACTIVE_STRIP_HEADER != "THOUGHTBORNE":
        _record("ACTIVE_STRIP_HEADER is not 'THOUGHTBORNE'")

    strips = [
        ("rec", u.render_rec_strip, dict(stops=REC_STOPS)),
        ("ok", u.render_ok_strip, dict(seq=12, chars=184, sent=False,
                                       model_label="Soniox Live", footer=FOOTER)),
        ("waiting", u.render_waiting_strip, dict(seq=12, chars=184, stops=WAIT_STOPS)),
        ("cancelled", u.render_cancelled_strip, {}),
        ("saved", u.render_saved_strip, dict(duration=12.3, retry_key=RETRY)),
    ]
    for name, fn, kw in strips:
        a = fn(ansi=True, **kw)
        p = fn(ansi=False, **kw)
        if not strip(a[0]).startswith("┌── • THOUGHTBORNE "):
            _record(f"{name}: ANSI strip header missing the bullet: {strip(a[0])!r}")
        if not p[0].startswith("+-- o THOUGHTBORNE "):
            _record(f"{name}: plain strip header missing the 'o' twin: {p[0]!r}")
        if "\x1b" in a[0]:
            _record(f"{name}: strip header border is not monochrome (carries SGR)")
        if strip(a[1])[1:-1].strip() or p[1][1:-1].strip():
            _record(f"{name}: headroom line not blank: {strip(a[1])!r} / {p[1]!r}")

    # a5 disc: rendered beside the wordmark in ANSI, gone from the plain twin
    # (which collapses to WM_PLAIN).
    mkw = dict(lineup=lineup_for(DEFAULT_API), keys=PAIRS,
               history_path=PATHS[1] + r"\history", switch_key=SWITCH,
               start_key=START, logo_lines=u.ACTIVE_LOGO_MARK, with_wordmark=True)
    ma = u.render_masthead(ansi=True, **mkw)
    mp = u.render_masthead(ansi=False, **mkw)
    mid = u.LOGO_MARK_A5[1]                       # "▄▀▀████" -- the unambiguous mark row
    if not any(mid in strip(ln) for ln in ma):
        _record("masthead: a5 mark not present in the ANSI render")
    if any(mid in ln for ln in mp):
        _record("masthead: a5 mark leaked into the plain twin")
    if not any(u.WM_PLAIN in ln for ln in mp):
        _record("masthead: plain twin lost the WM_PLAIN wordmark")


# ---- #115 brand accent: masthead wordmark + mark only ------------------------
ACC = f"\x1b[{u.ACCENT}m"    # the accent SGR as it appears inline


def _masthead(ansi, *, logo=True, wordmark=True, scheme=DEFAULT_HOTKEYS):
    """A masthead off one hotkey scheme -- grid, switch and start combo all
    derived from it, so a fixture can never render two schemes at once."""
    return u.render_masthead(
        lineup_for(DEFAULT_API), pairs_for(scheme), PATHS[1] + r"\history",
        combo_for(scheme, "switch_api"), combo_for(scheme, "start_recording"),
        logo_lines=(u.ACTIVE_LOGO_MARK if logo else None), with_wordmark=wordmark,
        ansi=ansi)


def check_accent_state():
    """The brand accent (#115) rides only on the masthead wordmark + logo mark;
    never the tagline, never a strip/panel, and it never trips red-exclusivity."""
    if "31" in u.ACCENT.split(";"):
        _record("ACCENT constant contains the red code (31) -- red must stay error-exclusive")

    ma = _masthead(True)
    wm_rows = [i for i, ln in enumerate(ma)
               if any(w in strip(ln) for w in u.WM)]        # the 3 mark+wordmark rows
    for i, ln in enumerate(ma):
        if i in wm_rows and ACC not in ln:
            _record(f"masthead accent: wordmark row {i} not accented")
        if i not in wm_rows and ACC in ln:
            _record(f"masthead accent: leaked onto non-wordmark line {i}: {strip(ln)!r}")
    if any(u.TAGLINE in strip(ln) and ACC in ln for ln in ma):
        _record("masthead accent: the tagline must not be accented")
    if any("\x1b" in ln for ln in _masthead(False)):
        _record("masthead accent: plain masthead carries an escape sequence")

    # accent is exclusive to the masthead wordmark/mark -- no strip/panel takes it
    model, lu = "Soniox Live", lineup_for(DEFAULT_API)
    others = [
        u.render_rec_strip(REC_STOPS, ansi=True),
        u.render_ok_strip(12, 184, False, model, FOOTER, ansi=True),
        u.render_waiting_strip(12, 184, WAIT_STOPS, ansi=True),
        u.render_transcription_failed(12, RETRY, model, FFOOTER,
                                      ansi=True),
        u.render_transcription_failed(12, RETRY, model, FFOOTER,   # #159 reason block
                                      reason="no-connection", provider="Soniox",
                                      ansi=True),
        u.render_transcription_failed(12, RETRY, model, FFOOTER,   # #179 credits block
                                      reason="no-credit", provider="Soniox",
                                      ansi=True),
        u.render_mic_failed(model, FFOOTER, ansi=True),   # #179
        u.render_device_loss(12.0, RETRY, model, FFOOTER, ansi=True),
        u.render_switched_panel(model, lu, SWITCH, ansi=True),
    ]
    for lines in others:
        if ACC in "".join(lines):
            _record(f"accent leaked into a non-masthead render: {strip(lines[1])!r}")


def check_masthead_layout():
    """#115 masthead: three framed spacers (before MODEL/KEYS/History), tagline
    centered under the wordmark, capitalised `History:` edge without the open
    hint -- plus the #276 header grammar (the KEYS lead, the one MODEL form)."""
    ma = [strip(ln) for ln in _masthead(True)]
    blank = "║" + " " * u.INNER + "║"
    blanks = [i for i, s in enumerate(ma) if s == blank]
    if len(blanks) != 3:
        _record(f"masthead layout: expected 3 framed spacers, found {len(blanks)}")

    def spacer_before(prefix, label):
        idx = next((i for i, s in enumerate(ma) if s.startswith(prefix)), None)
        if idx is None:
            _record(f"masthead layout: {label} line not found")
        elif ma[idx - 1] != blank:
            _record(f"masthead layout: no blank spacer before {label}: {ma[idx - 1]!r}")
    spacer_before("╠══ MODEL", "MODEL")
    spacer_before("╠══ KEYS", "KEYS")
    spacer_before("╚═ History:", "History edge")

    # tagline indent derived from the wordmark offset, not hardcoded
    markw = max(len(r) for r in u.LOGO_MARK_A5)
    gap = 4
    indent = max(0, (u.INNER - (markw + gap + len(u.WM[0]))) // 2)
    wm_offset = indent + markw + gap
    expected = wm_offset + (len(u.WM[0]) - len(u.TAGLINE)) // 2
    tag = next((s for s in ma if u.TAGLINE in s), "")
    inner = tag[1:-1]
    got = len(inner) - len(inner.lstrip(" "))
    if got != expected:
        _record(f"masthead layout: tagline indent {got} != derived {expected}")

    # #276: the KEYS header carries the shared lead the grid's bare keys hang
    # from -- and only then; a mixed scheme (one bare F-key) leaves it plain.
    kline = next((s for s in ma if s.startswith("╠══ KEYS")), "")
    if not kline.startswith("╠══ KEYS  Ctrl+Alt + ═"):
        _record(f"masthead layout: KEYS header lacks the Ctrl+Alt lead: {kline!r}")
    mixed = [strip(ln) for ln in _masthead(
        True, scheme=dict(DEFAULT_HOTKEYS, start_recording="f9"))]
    kmixed = next((s for s in mixed if s.startswith("╠══ KEYS")), "")
    if not kmixed.startswith("╠══ KEYS ═"):
        _record(f"masthead layout: mixed-scheme KEYS header is not plain: {kmixed!r}")

    # #276: one MODEL header form -- the masthead's is the SWITCHED panel's line
    mzone = next((s for s in ma if s.startswith("╠══ MODEL")), "")
    switched = [strip(ln) for ln in u.render_switched_panel(
        API_DISPLAY[DEFAULT_API]["label"], lineup_for(DEFAULT_API), SWITCH, ansi=True)]
    szone = next((s for s in switched if s.startswith("╠══ MODEL")), "")
    if mzone != szone:
        _record(f"masthead layout: MODEL header {mzone!r} != SWITCHED panel's {szone!r}")
    # History edge: capitalised, no open/lowercase-history hint
    edge = ma[-1]
    if not edge.startswith("╚═ History: "):
        _record(f"masthead layout: History edge wrong: {edge!r}")
    if "open:" in edge or "history:" in edge:
        _record(f"masthead layout: History edge still has an open/lowercase hint: {edge!r}")


def check_ctrl_alt_counts():
    """The #115 rule as #276 and #277 re-scoped it: one lead per key list, next to
    the keys it anchors, plus the full combo wherever prose names a key (D-019) --
    so a panel that both explains and offers keys names the modifier more than
    once, and each fixture is pinned at exactly what it should read. The masthead
    is the orientation surface and spells it out: READY line, MODEL header, KEYS
    lead, plus the keyless guidance line."""
    model, lu = "Soniox Live", lineup_for(DEFAULT_API)
    cases = [
        ("masthead", _masthead(True), 3),
        ("ready", _masthead(True, logo=False, wordmark=False), 3),
        ("masthead/keyless", _masthead_with(lineup_keyed(None, set()),
                                            guidance=GUIDANCE), 4),
        ("rec", u.render_rec_strip(REC_STOPS, ansi=True), 1),
        ("ok", u.render_ok_strip(12, 184, False, model, FOOTER,
                                 ansi=True), 1),
        ("ok/typing", u.render_ok_strip(12, 184, False, model, FOOTER,
                                        mode="typing", cap=4000, ansi=True), 1),
        ("typed_capped", u.render_typed_capped(4000, 30818, PASTE, model, FOOTER,
                                               ansi=True), 2),
        ("waiting", u.render_waiting_strip(12, 184, WAIT_STOPS, ansi=True), 1),
        ("cancelled", u.render_cancelled_strip(ansi=True), 0),
        ("saved", u.render_saved_strip(12.3, RETRY, ansi=True), 1),
        # retry prose + switch prose + the footer's lead
        ("transcription_failed", u.render_transcription_failed(
            12, RETRY, model, FFOOTER, ansi=True), 3),
        ("transcription_failed/reason", u.render_transcription_failed(   # #159 reason block adds none
            12, RETRY, model, FFOOTER, reason="no-connection",
            provider="Groq", ansi=True), 3),
        ("transcription_failed/auth", u.render_transcription_failed(     # #159 auth names no key at all
            12, RETRY, model, FFOOTER, reason="auth",
            provider="Soniox", ansi=True), 1),
        ("transcription_failed/credits", u.render_transcription_failed(  # #179 top-up prose + lead
            12, RETRY, model, FFOOTER, reason="no-credit",
            provider="Soniox", ansi=True), 2),
        ("insert_failed", u.render_insert_failed(
            12, WAIT_STOPS, model, FOOTER, ansi=True), 2),
        ("device_loss", u.render_device_loss(   # WHAT-NOW prose + the footer's lead
            12.0, RETRY, model, FFOOTER, ansi=True), 2),
        ("mic_failed", u.render_mic_failed(   # #179: footer carries the sole Ctrl+Alt
            model, FFOOTER, ansi=True), 1),
        ("selftest_failed", u.render_selftest_failed(
            "self-test failed -- no transcription received",
            ("check your API key in Settings,", f"then see {LOG_FILE.name} for details"),
            ansi=True), 0),
        ("hotkeys_failed", u.render_hotkeys_failed(ansi=True), 0),
        ("switch_failed", u.render_switch_failed(
            model, lu, SWITCH, missing=["SONIOX_API_KEY"], ansi=True), 1),
        ("switched", u.render_switched_panel(model, lu, SWITCH, ansi=True), 1),
        ("recovered", u.render_recovered_panel(
            "2026-07-11 03:14", 42, False, True, PATHS[3] + r"\history\audio", RETRY,
            ansi=True), 1),
        ("noapi", u.render_noapi_panel(
            [("SONIOX_API_KEY", ["soniox-live"])], [], PATHS[1], ansi=True), 0),
        ("no_speech", u.render_no_speech(OPEN, ansi=True), 1),   # #159: open-history hint
        ("already_running", u.render_already_running(ansi=True), 0),   # #166: no hotkey embed
        ("hotkeys_partial", u.render_hotkeys_partial(10, 11, ansi=True), 0),   # #166
        ("keyless", u.render_keyless_notice("Ctrl+Alt+G", ansi=True), 1),   # #200
    ]
    for name, lines, expected in cases:
        n = strip("".join(lines)).count("Ctrl+Alt")
        if n != expected:
            _record(f"Ctrl+Alt count: {name} has {n}, expected {expected}")


def check_failed_reason_block():
    """#159: the FAILED reason block says why -- CYAN (never the masthead-exclusive
    ACCENT), L1 left-anchored / L2 right-anchored, auth points at Settings (not
    [AUTH]/.env), an uncategorized failure keeps the generic hint, and the
    inconclusive flag shows 'came back empty' over the raw category."""
    model = "Soniox Live"

    def render(**kw):
        return u.render_transcription_failed(
            12, RETRY, model, FFOOTER, ansi=True, **kw)

    # Every categorized reason renders a CYAN two-line block, never ACCENT.
    for reason in ("no-connection", "service-error", "rate-limited", "auth", "no-credit"):
        joined = "".join(render(reason=reason, provider="Soniox"))
        if ACC in joined:
            _record(f"failed/{reason}: reason block used ACCENT (masthead-exclusive)")
        codes = re.findall(r"\x1b\[([0-9;]+)m", joined)
        if not any("36" in c.split(";") for c in codes):
            _record(f"failed/{reason}: reason block is not CYAN (SGR 36)")

    # Provider token is interpolated (short form, never the long display name).
    groq = strip("".join(render(reason="no-connection", provider="Groq")))
    if "Groq" not in groq:
        _record(f"failed: provider token not interpolated: {groq!r}")

    # auth points at Settings, not [AUTH] / .env, and shows the key message -- a
    # rejected key is a conclusive verdict, never the inconclusive 'came back
    # empty' (#159: auth is never marked inconclusive, so it keeps its own guidance).
    auth = strip("".join(render(reason="auth", provider="Soniox")))
    if "Settings" not in auth:
        _record("failed/auth: does not mention Settings")
    if "[AUTH]" in auth or ".env" in auth:
        _record(f"failed/auth: still references [AUTH]/.env: {auth!r}")
    if "turned down the API key" not in auth:
        _record("failed/auth: did not show the auth key message")
    if "came back empty" in auth:
        _record("failed/auth: showed the inconclusive message instead of the auth reason")

    # #179: no-credit (402) shows its own credits block + top-up WHAT-NOW, provider
    # interpolated, and -- like auth -- is a conclusive verdict, never the
    # inconclusive 'came back empty' line when inconclusive=False.
    credit = strip("".join(render(reason="no-credit", provider="Soniox")))
    if "out of credit" not in credit:
        _record("failed/no-credit: did not state the account is out of credit")
    if f"top up, then press {RETRY}" not in credit:
        _record("failed/no-credit: WHAT-NOW does not offer the top-up step")
    if "Soniox console" not in credit:
        _record("failed/no-credit: does not name the Soniox console for the top-up")
    if "came back empty" in credit:
        _record("failed/no-credit: showed the inconclusive message instead of the credits reason")
    if ".env" in credit or "config.py" in credit:
        _record(f"failed/no-credit: references a stale signpost: {credit!r}")
    # provider interpolated into the credits lines (a Groq 402 must not say "Soniox
    # console"). The footer's model: line still shows the selected engine, so check
    # the console-token forms specifically rather than any "Soniox" substring.
    groq_credit = strip("".join(render(reason="no-credit", provider="Groq")))
    if "Groq console" not in groq_credit or "Soniox console" in groq_credit:
        _record(f"failed/no-credit: provider token not interpolated in the top-up line: {groq_credit!r}")

    # None reason -> no reason block, generic retry hint retained.
    none_lines = strip("".join(render()))
    if "retry this recording" not in none_lines:
        _record("failed/None: lost the generic retry hint")
    if "came back empty" in none_lines or "Wi-Fi" in none_lines:
        _record("failed/None: rendered a reason block for an uncategorized failure")

    # inconclusive -> 'came back empty' over the raw category, paired with a
    # transient reason (no-connection). auth is excluded upstream (#159), so it is
    # not the example here -- the worker never marks a rejected key inconclusive.
    inc = strip("".join(render(reason="no-connection", provider="Soniox", inconclusive=True)))
    if "came back empty" not in inc:
        _record("failed/inconclusive: did not show the 'came back empty' message")
    if "reach the Soniox server" in inc or "Wi-Fi" in inc:
        _record("failed/inconclusive: showed the raw category instead of inconclusive")

    # L1 left-anchored at col 2, L2 right-anchored with a 2-cell margin.
    lines = render(reason="no-connection", provider="Soniox")
    l1, l2 = strip(lines[2]), strip(lines[3])
    if l1[1:3] != "  " or l1[3] == " ":
        _record(f"failed L1 not left-anchored at col 2: {l1!r}")
    inner = l2[1:-1]
    if not (inner.endswith("  ") and inner[-3] != " "):
        _record(f"failed L2 not right-anchored (2-cell margin): {l2!r}")


def check_no_speech_open_key_width():
    """#159/#55: the NO SPEECH open-history hint embeds the full open_history combo.
    An unusually long #55 override must not push the framed line past the 70-cell
    panel width -- the embed is truncate_end-guarded like every other variable embed
    in the module. check_block flags any framed line whose length != W."""
    long_open = "Ctrl+Shift+Alt+F12"   # a pathologically long #55 override
    for ansi in (True, False):
        check_block("no_speech_long_open",
                    u.render_no_speech(long_open, ansi=ansi),
                    ansi=ansi)


def check_strip_structure():
    """#115 strip key lines: one `Ctrl+Alt + ` lead, first key at column 14, no
    lead-in labels; the OK strip's model sits on its own line."""
    lead = f"  {KEY_PREFIX} +  "
    if len(lead) != 14:
        _record(f"strip lead is {len(lead)} cols, expected 14 (shipped Ctrl+Alt prefix)")
    rec = u.render_rec_strip(REC_STOPS, ansi=True)
    ok = u.render_ok_strip(12, 184, False, "Soniox Live", FOOTER,
                           ansi=True)
    waiting = u.render_waiting_strip(12, 184, WAIT_STOPS, ansi=True)
    for name, lines in (("rec", rec), ("ok", ok), ("waiting", waiting)):
        joined = strip("".join(lines))
        for label in ("stop:", "or:", "insert:", "retry:"):
            if label in joined:
                _record(f"{name}: stale lead-in label {label!r}")
        # D-019 retired the two long strip words; a regression would read fine
        # but break the widest-case flow-line measurement (67 of 68 cells).
        for stale in ("keep for later", "type text"):
            if stale in joined:
                _record(f"{name}: stale strip word {stale!r}")
        keyline = next((strip(ln) for ln in lines if f"{KEY_PREFIX} +" in strip(ln)), None)
        if keyline is None:
            _record(f"{name}: no Ctrl+Alt key line")
            continue
        inner = keyline[1:-1]
        if not inner.startswith(lead):
            _record(f"{name}: key line lead wrong: {inner[:16]!r}")
        elif inner[len(lead)] == " ":
            _record(f"{name}: first key not at column {len(lead)}: {inner[:18]!r}")
    okj = [strip(ln) for ln in ok]
    if not any(s[1:].strip().startswith("model: Soniox Live") for s in okj):
        _record("ok: missing a dedicated 'model:' line")
    if any(("model:" in s) and (f"{KEY_PREFIX} +" in s) for s in okj):
        _record("ok: model and keys are not split onto separate lines")


# ---- #7 typed-insert cap: the OK/typing annotation + the CAPPED strip ---------
def check_typed_cap_surfaces():
    """#7: the OK/typing strip shows the ceiling beside the char count and drops
    the seq block; the CAPPED strip is a calm YELLOW success-with-notice (never
    red), naming the clipboard key and history, framed at full width."""
    model = "Soniox Live"

    ok = u.render_ok_strip(12, 3990, False, model, FOOTER,
                           mode="typing", cap=4000, ansi=True)
    joined = strip("".join(ok))
    if "typed at the cursor" not in joined:
        _record("ok/typing: headline is not 'typed at the cursor'")
    if "(max 4,000)" not in joined:
        _record("ok/typing: missing the '(max 4,000)' annotation")
    row1 = strip(ok[2])   # top border, +1 headroom line, then the OK row
    if "seq " in row1:
        _record(f"ok/typing: the typed strip still carries a seq block: {row1!r}")

    cap = u.render_typed_capped(4000, 999999, PASTE, model, FOOTER, ansi=True)
    joinedc = "".join(cap)
    codes = re.findall(r"\x1b\[([0-9;]+)m", joinedc)
    if not any("33" in c.split(";") for c in codes):
        _record("typed_capped: CAPPED tag is not yellow (SGR 33)")
    if any("31" in c.split(";") for c in codes):
        _record("typed_capped: carries red (SGR 31) -- a cap is a success, not an error")
    txt = strip(joinedc)
    for want, label in (("CAPPED", "the CAPPED tag"), ("history", "history"),
                        (f"{PASTE} pastes", "the paste key, named in full")):
        if want not in txt:
            _record(f"typed_capped: does not carry {label}: {txt!r}")
    for ln in cap:
        v = strip(ln)
        if v and len(v) != u.W:
            _record(f"typed_capped framed line != {u.W}: {v!r}")


# ---- D-019 key tables, derived budgets, prefix parity ------------------------
def check_key_tables_and_budgets():
    """The renderer's key copy is keyed by action name, so it must cover exactly
    the actions config ships (`console_ui` cannot import them); the cell budgets
    are derived at import, not hardcoded; and `_display_prefix` -- the deliberate
    twin of `hotkey_parse.common_prefix` this module may not import -- agrees
    with the original on formatted combos."""
    if set(u.KEY_LABELS) != set(DEFAULT_HOTKEYS):
        _record(f"KEY_LABELS keys != DEFAULT_HOTKEYS: "
                f"{set(u.KEY_LABELS) ^ set(DEFAULT_HOTKEYS)}")
    # A subset on purpose: open_settings / test_transcription appear on no strip
    # or footer. A missing word a surface DOES need is a KeyError in its fixture.
    if not set(u.KEY_WORDS) <= set(DEFAULT_HOTKEYS):
        _record(f"KEY_WORDS carries unknown action names: "
                f"{set(u.KEY_WORDS) - set(DEFAULT_HOTKEYS)}")

    # Derived values, pinned: a copy change that moves them must be a decision.
    if u.KEY_BUDGET != 13:
        _record(f"KEY_BUDGET is {u.KEY_BUDGET}, expected the derived 13")
    three = u._cell_budget(u.KEY_LABELS.values(), 3)
    if three != 1:
        _record(f"three-column key budget is {three}, expected the derived 1")
    # ...and derived in the source, not merely equal to the derivation today. A
    # literal `KEY_BUDGET = 13` passes every value comparison -- including one
    # against _cell_budget itself -- until a grid label grows and the global
    # budget silently drifts from the geometry it is supposed to be. D-019 names
    # "a hardcoded cell budget" on its do-not-reintroduce list, so read the module.
    assign = next((n.value for n in ast.parse(
        Path(u.__file__).read_text(encoding="utf-8")).body
        if isinstance(n, ast.Assign) and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "KEY_BUDGET"),
        None)
    if not (isinstance(assign, ast.Call) and isinstance(assign.func, ast.Name)
            and assign.func.id == "_cell_budget"):
        _record("console_ui.py: KEY_BUDGET is not a _cell_budget(...) call -- the one "
                "key-column budget must be derived from the grid at import, never "
                "written down (D-019)")

    for combos in (["ctrl+alt+w", "ctrl+alt+a"],          # one shared prefix
                   ["ctrl+alt+w", "ctrl+shift+a"],        # mixed prefixes
                   ["f9", "ctrl+alt+a"],                  # a bare key mixed in
                   ["ctrl+alt+shift+win+f24"],            # a single long combo
                   ["f9", "f10"]):                        # bare keys only
        want = common_prefix(combos)
        want = format_combo(want) if want is not None else None
        got = u._display_prefix([format_combo(c) for c in combos])
        if got != want:
            _record(f"_display_prefix{combos} -> {got!r}, common_prefix says {want!r}")


# ---- D-019 cell geometry: the grid under override schemes --------------------
def _bold_tokens(lines):
    """The key tokens of a render: the pure-bold spans (tags carry a colour too)."""
    return re.findall(r"\x1b\[1m([^\x1b]*)\x1b", "".join(lines))


def _cell_columns(line):
    """Visible start columns of the key cells in one rendered row: the bold key
    token, or the unstyled `[...]+` artifact standing in front of it. Read off
    the render, so the alignment check does not restate the cell formula."""
    visible, cols, i = "", [], 0
    while i < len(line):
        m = _SGR.match(line, i)
        if m:
            if m.group(0) == f"\x1b[{u.BOLD}m":
                cols.append(len(visible))
            i = m.end()
        else:
            visible += line[i]
            i += 1
    pre = u.ELLIPSIS + "+"
    return [c - len(pre) if visible[:c].endswith(pre) else c for c in cols]


def check_grid_schemes():
    """The KEYS grid under override schemes it must survive: the settings app's
    own F-keys preset, a single bare-F-key rebind, a three-modifier scheme (the
    `[...]` rule), a pathological 22-cell one, and all-F-keys under one lead --
    two columns each, aligned, framed at full width, nothing truncated."""
    lineup = lineup_for(DEFAULT_API)

    def grid_for(name, scheme, expect_rows=6, columns=2):
        """Render a whole masthead on `scheme` (framed, both ansi states, twin),
        then return its grid rows -- asserting the row count and that every row
        puts its cells at the same columns: the uniform cell width, read off the
        render rather than restated from the formula. READY line and MODEL header
        come from the same scheme, so (d) is also the width check for the widest
        headers a legal scheme can produce."""
        pairs = pairs_for(scheme)
        kw = dict(lineup=lineup, keys=pairs, history_path=PATHS[1] + r"\history",
                  switch_key=combo_for(scheme, "switch_api"),
                  start_key=combo_for(scheme, "start_recording"),
                  with_wordmark=False)
        for ansi in (True, False):
            check_block(name, u.render_masthead(ansi=ansi, **kw), ansi=ansi)
        twin(name, u.render_masthead, **kw)
        rows = u._keys_grid_lines(pairs, u._display_prefix([c for _, c in pairs]), True)
        if len(rows) != expect_rows:
            _record(f"{name}: {len(rows)} grid rows, expected {expect_rows}")
        starts = {tuple(_cell_columns(r)) for r in rows}
        if starts != {tuple(_cell_columns(rows[0]))} or len(_cell_columns(rows[0])) != columns:
            _record(f"{name}: cells not aligned in {columns} columns: {sorted(starts)}")
        return rows

    # (a) the settings app's F-keys preset: mixed prefixes, every combo within
    #     the key-column budget (widest: Ctrl+Alt+F10 = 12), so nothing shortens.
    rows = grid_for("grid/fkeys-preset", settings_io.PRESET_FKEYS)
    if u.ELLIPSIS in strip("".join(rows)):
        _record("grid/fkeys-preset: shortened a combo that fits the budget")

    # (b) one bare-F-key rebind -- the realistic #55 case that costs the lead.
    rows = grid_for("grid/f9-override", dict(DEFAULT_HOTKEYS, start_recording="f9"))
    joined = strip("".join(rows))
    if u.ELLIPSIS in joined or "Ctrl+Alt+W" in joined:
        _record(f"grid/f9-override: unexpected shortening or a stale key: {joined!r}")

    # (c) three modifiers on eleven actions, one bare key: no lead, and every
    #     16-cell combo shortens -- with only the key token bold ([...] is a
    #     renderer artifact, not part of the key).
    three = {n: ("f9" if n == "start_recording"
                 else "ctrl+alt+shift+" + first_combo(v).rpartition("+")[2])
             for n, v in DEFAULT_HOTKEYS.items()}
    rows = grid_for("grid/three-modifiers", three)
    ansi_joined = "".join(rows)
    if f"{u.ELLIPSIS}+\x1b[1m" not in ansi_joined:
        _record("grid/three-modifiers: expected shortened keys with only the key token bold")
    if f"\x1b[1m{u.ELLIPSIS}" in ansi_joined:
        _record("grid/three-modifiers: the [...] artifact is styled as part of the key")

    # (d) pathological: a four-modifier lead with multi-cell bare keys (22-cell
    #     combos). Illegal as a config -- every action on one combo -- but a
    #     legitimate renderer stress: two columns of bare keys under the lead.
    rows = grid_for("grid/four-modifier-lead",
                    {n: ("ctrl+alt+shift+win+u" if n == "exit_program"
                         else "ctrl+alt+shift+win+f24") for n in DEFAULT_HOTKEYS})
    if u.ELLIPSIS in strip("".join(rows)):
        _record("grid/four-modifier-lead: shortened a bare key under a lead")

    # (e) all of Ctrl+Alt+F1..F12: one lead, but 3-cell bare keys -> two columns,
    #     not three (the column count follows the geometry, not the lead).
    rows = grid_for("grid/fkeys-under-lead",
                    {n: f"ctrl+alt+f{i + 1}" for i, n in enumerate(DEFAULT_HOTKEYS)})
    joined = strip("".join(rows))
    if "Ctrl+Alt" in joined or "F12" not in joined:
        _record(f"grid/fkeys-under-lead: keys are not bare under the lead: {joined!r}")


# ---- D-019 REC / WAITING on the pair contract --------------------------------
def check_strip_pairs():
    """The two strips the pair contract reaches in this step: the shipped flow
    form word for word, the aligned cell form under a rebound scheme (where the
    old code showed the same wrong key three times), and the width fallback."""
    rec = [strip(ln) for ln in u.render_rec_strip(REC_STOPS, ansi=True)]
    want = ["  Ctrl+Alt +  A paste   D paste+Enter   Y keep only",
            "              H type   X cancel"]
    got = [ln[1:-1].rstrip() for ln in rec[3:5]]   # the two key lines
    if got != want:
        _record(f"rec strip: {got!r} != the canonical {want!r}")
    waiting = [strip(ln) for ln in u.render_waiting_strip(12, 184, WAIT_STOPS, ansi=True)]
    if waiting[3][1:-1].rstrip() != "  Ctrl+Alt +  A paste   H type":
        _record(f"waiting strip: {waiting[3][1:-1].rstrip()!r} != the canonical form")

    # Rebound to the settings app's F-keys preset: aligned cells with full,
    # DIFFERENT combos. Before D-019 this rendered `F10 type  F10 paste
    # F10 paste+Enter` -- three keys, one letter, two of them wrong.
    stops = pairs_for(settings_io.PRESET_FKEYS, REC_ACTIONS)
    lines = u.render_rec_strip(stops, ansi=True)
    for ansi in (True, False):
        check_block("rec/fkeys-preset", u.render_rec_strip(stops, ansi=ansi), ansi=ansi)
    twin("rec/fkeys-preset", u.render_rec_strip, stops=stops)
    tokens = _bold_tokens(lines)
    if len(tokens) != len(stops):
        _record(f"rec/fkeys-preset: {len(tokens)} key tokens for {len(stops)} keys")
    if len(set(tokens)) != len(tokens):
        _record(f"rec/fkeys-preset: two keys render the same token: {tokens}")
    # top border, headroom, REC head, 3 cell rows for 5 keys, bottom border
    if len(lines) != 7:
        _record(f"rec/fkeys-preset: {len(lines)} lines, expected 7 (3 cell rows)")
    # [...] may only ever stand in a key column, i.e. immediately before a token.
    joined = "".join(lines)
    if joined.count(u.ELLIPSIS) != joined.count(f"{u.ELLIPSIS}+\x1b[1m"):
        _record("rec/fkeys-preset: an ELLIPSIS outside a key column")

    # The flow line does not fit: a four-modifier lead plus wide keys. Unreachable
    # with canonical combos and today's words (widest real case: 67 of 68 cells),
    # so the fixture is synthetic -- the renderer measures, it never parses.
    wide = [(n, "Ctrl+Alt+Shift+Win+F24" + str(i))
            for i, (n, _) in enumerate(REC_STOPS)]
    lines = u.render_rec_strip(wide, ansi=True)
    for ansi in (True, False):
        check_block("rec/lead-too-wide", u.render_rec_strip(wide, ansi=ansi), ansi=ansi)
    twin("rec/lead-too-wide", u.render_rec_strip, stops=wide)
    if any("Ctrl+Alt+Shift+Win +" in strip(ln) for ln in lines):
        _record("rec/lead-too-wide: rendered a flow line that does not fit the frame")
    if not all(t.startswith("F24") for t in _bold_tokens(lines)):
        _record(f"rec/lead-too-wide: expected [...]-shortened keys, got {_bold_tokens(lines)}")


# ---- D-019 the footers and panels under rebound schemes (#277) --------------
FOOTER_SCHEMES = {
    "shipped": DEFAULT_HOTKEYS,
    "fkeys-preset": settings_io.PRESET_FKEYS,
    "f9-override": dict(DEFAULT_HOTKEYS, start_recording="f9"),
    # Four modifiers on F-keys: the retry footer's flow line still fits (67 of 68
    # cells) while the history one (69) does not -- the derived fallback, pinned
    # in check_footer_lead_fallback below.
    "four-modifier": {n: ("ctrl+alt+shift+win+u" if n == "exit_program"
                          else "ctrl+alt+shift+win+f24") for n in DEFAULT_HOTKEYS},
}
# The prose each panel must spell out in full, per surface -- {r}/{s}/{p} are the
# scheme's retry / switch / paste combos. Every one of these fits the guard budget
# for any legal combo, so a truncation can never hide a miss.
PROSE = {
    "typed_capped": ["full text kept in history -- {p} pastes"],
    "device_loss": ["reconnect the microphone, then press {r}"],
    "saved": ["next start: press {r}"],
    "recovered": ["once hotkeys work, press {r}"],
    "failed/None": ["press {r} to retry this recording", "or switch the model ({s})"],
    "failed/no-credit": ["top up, then press {r}"],
}


def _key_tokens(lines):
    """The keys as a box shows them: each bold token, carrying the unstyled
    `[...]+` artifact in front of it where the renderer shortened one. Read off
    the render, so this does not restate the shortening rule."""
    out, pre = [], u.ELLIPSIS + "+"
    for ln in lines:
        visible, i = "", 0
        while i < len(ln):
            m = _SGR.match(ln, i)
            if m:
                if m.group(0) == f"\x1b[{u.BOLD}m":
                    tok = ln[m.end():ln.index("\x1b", m.end())]
                    out.append(pre + tok if visible.endswith(pre) else tok)
                i = m.end()
            else:
                visible += ln[i]
                i += 1
    return out


def check_footer_schemes():
    """Every key-bearing footer surface under the schemes it must survive: the
    shipped one, the settings app's own F-keys preset (where the old code showed
    `F8` for both `Shift+F8` and `Ctrl+F8` -- the bug #277 closed), a single bare
    F-key rebind, and a four-modifier scheme. Per surface and scheme: framed at
    full width in both ansi states plus the plain twin, no two different combos
    rendering as the same key token, `[...]` only in key columns, and every prose
    line naming the full combo instead of a bare key."""
    model = "Soniox Live"
    for sname, scheme in FOOTER_SCHEMES.items():
        f, ff = footer_for(scheme), footer_for(scheme, retry=True)
        stops = pairs_for(scheme, WAIT_ACTIONS)
        rec_stops = pairs_for(scheme, REC_ACTIONS)
        r = combo_for(scheme, "retry_last_failed")
        sw = combo_for(scheme, "switch_api")
        pa = combo_for(scheme, "stop_recording_clipboard")
        surfaces = [
            ("ok", "ok", u.render_ok_strip,
             dict(seq=12, chars=184, sent=False, model_label=model, footer=f), [f]),
            ("ok/typing", "ok", u.render_ok_strip,
             dict(seq=12, chars=184, sent=True, model_label=model, footer=f,
                  mode="typing", cap=4000), [f]),
            ("typed_capped", "typed_capped", u.render_typed_capped,
             dict(cap=4000, original_chars=30818, paste_key=pa, model_label=model,
                  footer=f), [f]),
            ("mic_failed", "mic_failed", u.render_mic_failed,
             dict(model_label=model, footer=ff), [ff]),
            ("insert_failed", "insert_failed", u.render_insert_failed,
             dict(seq=12, stops=stops, model_label=model, footer=f), [stops, f]),
            ("device_loss", "device_loss", u.render_device_loss,
             dict(duration=12.0, retry_key=r, model_label=model, footer=ff), [ff]),
            ("saved", "saved", u.render_saved_strip,
             dict(duration=12.3, retry_key=r), []),
            ("recovered", "recovered", u.render_recovered_panel,
             dict(when="2026-07-11 03:14", duration=42, clean_exit=False,
                  hotkeys_ok=False, audio_path=PATHS[3] + r"\history\audio",
                  retry_key=r), []),
            ("waiting", "waiting", u.render_waiting_strip,
             dict(seq=12, chars=184, stops=stops), [stops]),
            # REC keeps its own 3/2 flow split rather than the shared key-line
            # helper, so only its width/twin come from here -- its key tokens are
            # pinned in check_strip_pairs.
            ("rec", "rec", u.render_rec_strip, dict(stops=rec_stops), []),
        ]
        for reason in (None, *u._REASON_LINES):
            surfaces.append((f"failed/{reason}", "transcription_failed",
                             u.render_transcription_failed,
                             dict(seq=12, retry_key=r, model_label=model, footer=ff,
                                  reason=reason, provider="Soniox"), [ff]))
        for label, red, fn, kw, key_lists in surfaces:
            name = f"{label} [{sname}]"
            lines = fn(ansi=True, **kw)
            for ansi in (True, False):
                check_block(name, fn(ansi=ansi, **kw), ansi=ansi, red_key=red)
            twin(name, fn, **kw)

            # The key lists the box shows are the ones _key_lines renders, and no
            # two different combos may arrive as the same token (the #277 bug).
            # Exception, accepted in D-019: two over-budget combos ending on the
            # same key both shorten to `[...]+<key>`.
            inner = {strip(ln)[1:-1] for ln in lines}
            for pairs in key_lists:
                shown = u._key_lines(pairs, u.dline, True)
                for ln in shown:
                    if strip(ln)[1:-1] not in inner:
                        _record(f"{name}: key line {strip(ln)[1:-1]!r} is not in the render")
                tokens = _key_tokens(shown)
                if len(tokens) != len(pairs):
                    _record(f"{name}: {len(tokens)} key tokens for {len(pairs)} keys")
                    continue
                seen = {}
                for (_, combo), tok in zip(pairs, tokens):
                    if seen.setdefault(tok, combo) != combo and not tok.startswith(u.ELLIPSIS):
                        _record(f"{name}: {tok!r} stands for both {seen[tok]!r} and {combo!r}")

            joined = "".join(lines)
            if joined.count(u.ELLIPSIS) != joined.count(f"{u.ELLIPSIS}+\x1b[1m"):
                _record(f"{name}: an ELLIPSIS outside a key column")

            # Prose spells the combination out, and never offers a bare key.
            text = strip(joined)
            for want in PROSE.get(label, []):
                want = want.format(r=r, s=sw, p=pa)
                if want not in text:
                    _record(f"{name}: prose does not read {want!r}: {text!r}")
            for combo in (r, sw, pa):
                bare = combo.rpartition("+")[2]
                if bare == combo:
                    continue          # a bare-key binding: nothing to give away
                for form in (f"press {bare} ", f"press {bare},", f"model ({bare})"):
                    if form in text:
                        _record(f"{name}: prose offers the bare key {form!r}")


def check_footer_lead_fallback():
    """The footer's flow-line-or-cells switch, derived rather than special-cased:
    on a four-modifier F-key scheme the retry footer's line still fits the frame
    (67 of 68 cells) and keeps its #115 lead, while the history one (69) falls
    back to two aligned cell rows with the full combos."""
    scheme = FOOTER_SCHEMES["four-modifier"]
    lead = "  Ctrl+Alt+Shift+Win +  "
    hist = [strip(ln)[1:-1] for ln in
            u._footer_lines("Soniox Live", footer_for(scheme), u.sline, True)]
    retry = [strip(ln)[1:-1] for ln in
             u._footer_lines("Soniox Live", footer_for(scheme, retry=True), u.sline, True)]
    if len(hist) != 3 or any(lead in ln for ln in hist):
        _record(f"footer/four-modifier: the history footer kept a lead that does "
                f"not fit: {hist!r}")
    if len(retry) != 2 or not retry[1].startswith(lead):
        _record(f"footer/four-modifier: the retry footer lost its fitting lead: {retry!r}")


# ---- D-019 the app half, read as source (#277) ------------------------------
_APP = Path(__file__).resolve().with_name("thoughtborne.py")
_DERIVERS = {"partition", "rpartition", "split", "rsplit"}
_HANDOVERS = {"_show", "_pairs", "_footer_keys"}


def _is_handover(node):
    """`self._show(...)` / `self._pairs(...)` / `self._footer_keys(...)` -- the
    three ways the app is allowed to produce a key for a renderer."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
            and node.func.attr in _HANDOVERS)


def _footer_order_from_app(tree):
    """The #115 footer order as `thoughtborne._footer_actions` returns it:
    (plain, retry) action names. This is what holds the ladder's literal copy
    (FOOTER_ACTIONS above) honest -- so an order this cannot read is recorded as
    a violation rather than skipped: an unread order is an unchecked copy."""
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "_footer_actions"), None)
    if fn is None:
        _record("thoughtborne.py: _footer_actions is gone -- the ladder's copy of the "
                "#115 footer order has nothing left to be held against")
        return None, None
    assigned = {}
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            assigned.setdefault(node.targets[0].id, []).append(node.value)

    def resolve(node):
        """One list element as (name without retry, name with retry), or None:
        a literal, a local assigned exactly once, or the `retry` conditional the
        history/retry slot has always been."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, node.value
        if isinstance(node, ast.Name):
            values = assigned.get(node.id, [])
            return resolve(values[0]) if len(values) == 1 else None
        if (isinstance(node, ast.IfExp) and isinstance(node.test, ast.Name)
                and node.test.id == "retry"):
            plain, retry = resolve(node.orelse), resolve(node.body)
            return (plain[0], retry[1]) if plain and retry else None
        return None

    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    elements = returns[0].value.elts if (
        len(returns) == 1 and isinstance(returns[0].value, ast.List)) else None
    resolved = [resolve(e) for e in elements] if elements is not None else [None]
    if any(r is None for r in resolved):
        _record(f"thoughtborne.py:{fn.lineno}: _footer_actions no longer reads as one "
                f"list of action names -- teach this check its new shape, or the "
                f"ladder's copy of the footer order goes unchecked")
        return None, None
    return [r[0] for r in resolved], [r[1] for r in resolved]


def _check_pairs_iterates_hotkeys(tree):
    """`thoughtborne._pairs` -- the handover every key-bearing console surface is
    built from -- must return a comprehension straight over HOTKEYS, which
    inherits the canonical order by deepcopy. Anything in between (a sort, a
    second order) reorders the grid, both strips, the footers and every panel at
    once, and no rendering check can catch it: the renderers show whatever order
    they are handed, and the fixtures build their own from DEFAULT_HOTKEYS. So the
    order's app half is read as source, the way G4 reads the footer's."""
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "_pairs"), None)
    if fn is None:
        _record("thoughtborne.py: _pairs is gone -- the canonical hotkey order has no "
                "checked path onto the console surfaces left (D-019)")
        return
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    node = returns[0].value if len(returns) == 1 else None
    if not (isinstance(node, ast.ListComp) and len(node.generators) == 1
            and isinstance(node.generators[0].iter, ast.Name)
            and node.generators[0].iter.id == "HOTKEYS"):
        _record(f"thoughtborne.py:{fn.lineno}: _pairs no longer reads as one "
                f"comprehension over HOTKEYS -- every console surface would follow "
                f"whatever order it hands out instead of the canonical one (D-019)")


def check_app_derives_no_key():
    """The app half of the pair contract, read as source: `thoughtborne.py`
    derives no key of its own (D-019), hands its pairs out in the canonical order
    unreordered, and the footer order the fixtures copy is the one it returns. The
    ladder cannot import the app off Windows, so this parses it -- an AST, never a
    substring, so the same names in a comment or docstring cannot trip it."""
    tree = ast.parse(_APP.read_text(encoding="utf-8"), filename=str(_APP))
    gone = ("_key_letter", "_format_hotkey")

    for node in ast.walk(tree):
        # G1: the two letter helpers stay gone, definition and call alike.
        if isinstance(node, ast.FunctionDef) and node.name in gone:
            _record(f"thoughtborne.py:{node.lineno}: {node.name} is back -- the app "
                    f"hands full combos, the renderer decides (D-019)")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in gone):
            _record(f"thoughtborne.py:{node.lineno}: calls {node.func.attr} -- the app "
                    f"hands full combos, the renderer decides (D-019)")
        # G2: nothing takes a combo apart at its '+'.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _DERIVERS and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "+"):
            _record(f"thoughtborne.py:{node.lineno}: splits a combo on '+' -- a key "
                    f"derived before the box's lead is known is the bug class #272 closed")

    # G3: every key-bearing renderer argument comes from one of the three
    # handovers, directly or through a local assigned exactly once. A name this
    # cannot resolve is skipped rather than guessed at -- G1/G2 stay the hard net.
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        assigned = {}
        for node in ast.walk(fn):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                assigned.setdefault(node.targets[0].id, []).append(node.value)
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "console_ui"
                    and node.func.attr.startswith("render_")):
                continue
            target = getattr(u, node.func.attr, None)
            if target is None:
                _record(f"thoughtborne.py:{node.lineno}: calls console_ui."
                        f"{node.func.attr}, which the renderer does not define")
                continue
            params = list(inspect.signature(target).parameters)
            bound = {params[i]: a for i, a in enumerate(node.args) if i < len(params)}
            bound.update({kw.arg: kw.value for kw in node.keywords if kw.arg})
            for pname, arg in bound.items():
                if not (pname.endswith("_key") or pname in ("keys", "stops", "footer")):
                    continue
                if _is_handover(arg):
                    continue
                if isinstance(arg, ast.Name):
                    values = assigned.get(arg.id, [])
                    if len(values) != 1 or _is_handover(values[0]):
                        continue
                _record(f"thoughtborne.py:{node.lineno}: {node.func.attr}'s `{pname}` "
                        f"is not one of self._show/_pairs/_footer_keys -- the app hands "
                        f"full combos and the renderer decides (D-019)")

    # G4: the #115 footer order the fixtures build on is the app's own, read out
    # of _footer_actions -- the ladder's one literal copy of an order (D-019).
    plain, retry = _footer_order_from_app(tree)
    if plain is not None and (plain, retry) != (FOOTER_ACTIONS, FOOTER_ACTIONS_RETRY):
        _record(f"thoughtborne.py: _footer_actions lists {plain!r} / {retry!r}, the "
                f"ladder copies {FOOTER_ACTIONS!r} / {FOOTER_ACTIONS_RETRY!r} -- every "
                f"footer fixture would pin an order the app no longer has")

    # G5: the canonical order reaches the surfaces by iteration -- _pairs adds no
    # order of its own between HOTKEYS and the renderers (D-019).
    _check_pairs_iterates_hotkeys(tree)


# ---- D-019 the stress check: no framed line ever leaves 70 cells (#277) ------
_MODS = [n for n, _ in hp._CANONICAL_MODIFIERS]
_STRESS_PREFIXES = [_MODS[:i] for i in range(len(_MODS) + 1)]
_NARROW_KEYS = [chr(ord("a") + i) for i in range(12)]        # 1 cell
_WIDE_KEYS = [f"f{i}" for i in range(10, 22)]                # the widest static keys
# The longest combo a legal config can hold, derived from hotkey_parse rather
# than pinned: every canonical modifier plus the widest key it maps.
MAX_COMBO = format_combo(canonical_combo(
    "+".join(_MODS + [max(sorted(hp.VK_MAP), key=len)])))

_STRESS_KEY_ACTION = {"switch_key": "switch_api", "start_key": "start_recording",
                      "paste_key": "stop_recording_clipboard",
                      "retry_key": "retry_last_failed", "open_key": "open_history",
                      "settings_key": "open_settings"}
_STRESS_PAIRS = {"keys": None, "stops": REC_ACTIONS, "footer": FOOTER_ACTIONS}
_STRESS_SWEEP = {"reason": (None, *u._REASON_LINES), "inconclusive": (False, True),
                 "hotkeys_ok": (False, True), "clean_exit": (False, True),
                 "sent": (False, True), "mode": (None, "typing")}
_STRESS_NEUTRAL = {
    "seq": 99999, "chars": 99999, "model_label": "Groq Whisper Large v3",
    "cap": 4000, "original_chars": 999999, "duration": 42.0,
    "when": "2026-07-11 03:14", "audio_path": PATHS[3] + r"\history\audio",
    "history_path": PATHS[3] + r"\history", "lineup": lineup_for(DEFAULT_API),
    "with_wordmark": False, "logo_lines": None, "pinned_default": None,
    "provider": "Soniox", "registered": 10, "expected": 11,
    "current_label": "Soniox Live", "new_label": "Soniox Live",
    "other_failures": [], "env_dir": PATHS[3],
    "action_lines": ("check your API key in Settings,", "then see the log"),
}
_STRESS_OVERRIDE = {   # where one parameter name means two different things
    ("render_selftest_failed", "reason"): "self-test failed -- no transcription received",
    ("render_switch_failed", "missing"): ["SONIOX_API_KEY"],
    ("render_noapi_panel", "missing"): [("SONIOX_API_KEY", ["soniox-live", "soniox"])],
}


def _stress_scheme(mods, keys, mixed):
    """One rung of the combo ladder as a whole hotkey scheme: every action on the
    same modifiers with its own key (shared -- one lead for a whole box), or with
    the prefixes rotated and bare keys mixed in (no lead anywhere)."""
    scheme = {}
    for i, n in enumerate(DEFAULT_HOTKEYS):
        k = keys[i % len(keys)]
        if mixed and i % 3 == 0:
            parts = [k]                       # bare -- kills any shared lead
        elif mixed and i % 3 == 1 and mods:
            parts = mods[:1] + [k]
        else:
            parts = mods + [k]
        scheme[n] = "+".join(parts)
    return scheme


def _stress_guidance(scheme):
    """The keyless masthead's guidance line, shaped like the app's (#200): prose
    with one full combo in it, built from the scheme under test rather than typed
    out, so the longest combos reach the one framed line the app composes itself."""
    return ("To enable dictation, enter an API key in Settings "
            f"({combo_for(scheme, 'open_settings')})")


def _stress_kwargs(fn, scheme):
    """Fill one renderer's signature from the tables above, by parameter NAME: a
    `*_key` takes one full combo, `keys`/`stops`/`footer` take pair lists, and the
    sweep parameters yield one kwargs dict per branch. A parameter in none of the
    tables is a hard failure -- so a new key-bearing input cannot slip past."""
    name = fn.__name__
    base, sweep = {}, {}
    for pname in inspect.signature(fn).parameters:
        if pname == "ansi":
            continue
        if (name, pname) in _STRESS_OVERRIDE:
            base[pname] = _STRESS_OVERRIDE[(name, pname)]
        elif pname.endswith("_key") and pname in _STRESS_KEY_ACTION:
            base[pname] = combo_for(scheme, _STRESS_KEY_ACTION[pname])
        elif pname == "footer":
            base[pname] = footer_for(scheme)
        elif pname in _STRESS_PAIRS:
            base[pname] = pairs_for(scheme, _STRESS_PAIRS[pname])
        elif pname == "guidance":            # keyless masthead: with and without
            sweep[pname] = (None, _stress_guidance(scheme))
        elif pname in _STRESS_SWEEP:
            sweep[pname] = _STRESS_SWEEP[pname]
        elif pname in _STRESS_NEUTRAL:
            base[pname] = _STRESS_NEUTRAL[pname]
        else:
            _record(f"stress: {name} has an unclassified parameter {pname!r} -- put it "
                    f"in the fixture tables so a new key-bearing input cannot slip through")
            return []
    names = list(sweep)
    return [dict(base, **dict(zip(names, values)))
            for values in itertools.product(*(sweep[k] for k in names))]


def check_stress_widths():
    """Every renderer, fed schemes of growing combo length up to the canonical
    maximum (MAX_COMBO, derived above), once with one shared prefix and once with
    the prefixes mixed: no framed line may ever be anything but 70 cells, in
    either ansi state. Truncation is expected and fine -- this measures width, not
    copy. The renderer list comes from `dir(console_ui)`, so a new view is in it
    the moment it exists."""
    renderers = [getattr(u, n) for n in sorted(dir(u)) if n.startswith("render_")]
    for mods in _STRESS_PREFIXES:
        for keys in (_NARROW_KEYS, _WIDE_KEYS):
            for mixed in (False, True):
                scheme = _stress_scheme(mods, keys, mixed)
                tag = (f"{'+'.join(mods) or 'bare'}+{len(keys[0])}c/"
                       f"{'mixed' if mixed else 'shared'}")
                for fn in renderers:
                    for kw in _stress_kwargs(fn, scheme):
                        _stress_widths(f"{fn.__name__} [{tag}]", fn, kw)
    # The ladder must actually reach the longest combo a config can hold.
    top = _stress_scheme(_MODS, _WIDE_KEYS, False)
    if len(combo_for(top, "start_recording")) != len(MAX_COMBO):
        _record(f"stress: the ladder tops out below {MAX_COMBO!r} "
                f"({combo_for(top, 'start_recording')!r})")

    # The sweep above accepts truncation, so it cannot tell a guard that saves the
    # frame from one that eats a legal combo. The SAVED line is the tightest of
    # them -- its sentence plus MAX_COMBO fills the frame exactly -- so pin that it
    # still names the combination in full at the widest rebinding (#277/#280).
    saved = [strip(ln) for ln in u.render_saved_strip(42.0, MAX_COMBO, ansi=False)]
    line = next((ln for ln in saved if "next start" in ln), "")
    if MAX_COMBO not in line or "..." in line:
        _record(f"saved strip truncates the widest legal combo {MAX_COMBO!r} -- its "
                f"width guard is against copy growth, not against a rebinding: {line!r}")
    elif len(line) != u.W:
        _record(f"saved strip at {MAX_COMBO}: framed len {len(line)} != {u.W}: {line!r}")


def _stress_widths(name, fn, kw):
    a, pl = fn(ansi=True, **kw), fn(ansi=False, **kw)
    for i, ln in enumerate(a):
        v = strip(ln)
        if v and len(v) != u.W:
            _record(f"stress {name}[{i}]: framed len {len(v)} != {u.W}: {v!r}")
    if len(a) != len(pl):
        _record(f"stress {name}: plain line count {len(pl)} != ansi {len(a)}")
        return
    for i, (la, lp) in enumerate(zip(a, pl)):
        if len(strip(la)) != len(lp):
            _record(f"stress {name}[{i}]: twin length {len(lp)} != {len(strip(la))}")


# ---- #55 override edge: no shared modifier prefix ----------------------------
def check_prefix_none_widths():
    """An override can leave the effective hotkeys without a shared modifier lead
    (a bare F-key rebind, or mixed prefixes), so the renderer derives none -- a
    framed path the shipped config never reaches. Guard the masthead KEYS grid
    and a routine strip on it at full width."""
    lineup = lineup_for(DEFAULT_API)
    # Ctrl+Alt+Ü is deliberate: the umlaut is override-only since #211/D-012, and this
    # synthetic fixture is the console's only remaining render coverage of the glyph.
    # Ctrl+Shift+F12 (14 cells) is over the key-column budget and renders `[...]+F12`.
    scheme = dict(DEFAULT_HOTKEYS, start_recording="f9",
                  retry_last_failed="ctrl+shift+f12",
                  test_transcription="ctrl+alt+ü")
    fixtures = [
        ("masthead_prefix_none", u.render_masthead,
         dict(lineup=lineup, keys=pairs_for(scheme),
              history_path=PATHS[1] + r"\history",
              switch_key=combo_for(scheme, "switch_api"),
              start_key=combo_for(scheme, "start_recording"),
              with_wordmark=False)),
        ("ok_prefix_none", u.render_ok_strip,
         dict(seq=12, chars=184, sent=False, model_label="Soniox Live",
              footer=footer_for(scheme))),
    ]
    for name, fn, kw in fixtures:
        for ansi in (True, False):
            check_block(name, fn(ansi=ansi, **kw),
                        ansi=ansi)
        twin(name, fn, **kw)


# ---- #200 key-aware lineup + keyless shop-window -----------------------------
def _line_has_dim(line):
    """True if the SGR codes on this rendered line include DIM (SGR 90)."""
    codes = re.findall(r"\x1b\[([0-9;]+)m", line)
    return any("90" in c.split(";") for c in codes)


def check_keyless_lineup():
    """#200 key-aware lineup: no `(default)` tag in any no-pin rendering (#219
    reintroduced it, but only under an active pin, which none of these fixtures
    carry); a row whose key env var is absent renders DIM while keyed and current
    rows do not; the fully-keyless masthead greys every row; and its yellow
    guidance line is YELLOW, never red."""
    model = "Soniox Live"

    # `(default)` must not appear in any NO-PIN rendering (masthead/switched/
    # switch-failed), any fixture, any form -- none of these pass a pin (#219).
    fixtures = [
        ("all", lineup_for(DEFAULT_API)),
        ("keyless", lineup_keyed(None, set())),
        ("groq-only", lineup_keyed("groq-large", {"GROQ_API_KEY"})),
        ("soniox-only", lineup_keyed("soniox-live", {"SONIOX_API_KEY"})),
    ]
    for fname, lu in fixtures:
        renders = [
            ("masthead", _masthead_with(lu)),
            ("switched", u.render_switched_panel(model, lu, SWITCH, ansi=True)),
            ("switch_failed", u.render_switch_failed(
                model, lu, SWITCH, missing=["SONIOX_API_KEY"], ansi=True)),
        ]
        for rname, lines in renders:
            if "(default)" in strip("".join(lines)):
                _record(f"{fname}/{rname}: renders a (default) tag with no pin active")

    # Grey rule, checked positionally on the lineup renderer (labels overlap
    # as substrings -- "Soniox" is inside "Soniox Live" -- so zip by AVAILABLE_APIS
    # order rather than matching text). Groq-only: the two soniox rows dim, the two
    # groq rows not; groq-large is current and must never be dim.
    present = {"GROQ_API_KEY"}
    lu = lineup_keyed("groq-large", present)
    rows = u._lineup_lines(lu, True)
    if len(rows) != len(AVAILABLE_APIS):
        _record(f"_lineup_lines: emitted {len(rows)} rows, "
                f"expected {len(AVAILABLE_APIS)}")
    else:
        for a, row in zip(AVAILABLE_APIS, rows):
            has_key = API_KEY_ENV[a] in present
            dim = _line_has_dim(row)
            if has_key and dim:
                _record(f"_lineup_lines: keyed row {a} is dim")
            if not has_key and not dim:
                _record(f"_lineup_lines: keyless row {a} is not dim")
            if a == "groq-large" and dim:
                _record(f"_lineup_lines: current row {a} must never be dim")

    # Fully-keyless masthead: every lineup row greyed + a YELLOW guidance line.
    lu = lineup_keyed(None, set())
    for a, row in zip(AVAILABLE_APIS, u._lineup_lines(lu, True)):
        if not _line_has_dim(row):
            _record(f"keyless masthead: row {a} not greyed on a fully-keyless start")
    ma = _masthead_with(lu, guidance=GUIDANCE)
    gline = next((ln for ln in ma if "enter an API key in Settings" in strip(ln)), None)
    if gline is None:
        _record("keyless masthead: yellow guidance line missing")
    else:
        codes = re.findall(r"\x1b\[([0-9;]+)m", gline)
        if not any("33" in c.split(";") for c in codes):
            _record("keyless masthead: guidance line is not YELLOW (SGR 33)")
        if any("31" in c.split(";") for c in codes):
            _record("keyless masthead: guidance line carries red (SGR 31)")
    # A keyed masthead (no guidance passed) must not sprout the line.
    keyed = _masthead_with(lineup_for(DEFAULT_API))
    if any("enter an API key in Settings" in strip(ln) for ln in keyed):
        _record("keyed masthead: guidance line shown without a keyless start")


def _masthead_with(lineup, *, guidance=None, pinned_default=None):
    """A masthead render for the #200/#219 checks (ANSI), wordmark on, given lineup."""
    return u.render_masthead(
        lineup, PAIRS, PATHS[1] + r"\history", SWITCH,
        START, guidance=guidance, with_wordmark=True, logo_lines=u.ACTIVE_LOGO_MARK,
        pinned_default=pinned_default, ansi=True)


def check_pinned_default_tag():
    """#219: a valid fixed-mode pin tags its engine's masthead row with a dim
    ` (default)` -- only that row, dim even when the row is bold (current), gone
    when the row is keyless (#200 wins), and only in the masthead (switched /
    switch-failed never receive a pin, covered by check_keyless_lineup's ban)."""
    TAG = " (default)"
    DIM_TAG = f"\x1b[{u.DIM}m{TAG}\x1b[0m"        # exact bytes of (" (default)", (DIM,))

    # The label match relies on label uniqueness -- assert it (cheap insurance;
    # a future duplicate label would tag two rows).
    labels = [API_DISPLAY[a]["label"] for a in AVAILABLE_APIS]
    if len(set(labels)) != len(labels):
        _record("API_DISPLAY labels are not unique; pinned_default match is ambiguous")

    # (A) pin on a NON-current keyed engine: tag present, dim, exactly one row.
    lu = lineup_for("soniox-live")               # all keyed; current = soniox-live
    pin = API_DISPLAY["groq"]["label"]           # pin a different engine
    ma = _masthead_with(lu, pinned_default=pin)
    check_block("masthead/pinned", ma, ansi=True)
    tag_rows = [ln for ln in ma if TAG in strip(ln)]
    if len(tag_rows) != 1:
        _record(f"pinned masthead: {len(tag_rows)} rows carry the tag, expected 1")
    else:
        row = tag_rows[0]
        if pin not in strip(row):
            _record("pinned masthead: tag is not on the pinned engine's row")
        if DIM_TAG not in row:
            _record("pinned masthead: (default) tag is not dim (SGR 90)")

    # (B) pin on the CURRENT (bold) engine: the bold row still carries a dim tag.
    lu = lineup_for("groq")
    ma = _masthead_with(lu, pinned_default=API_DISPLAY["groq"]["label"])
    brow = next((ln for ln in ma
                 if API_DISPLAY["groq"]["label"] in strip(ln) and TAG in strip(ln)), None)
    if brow is None or DIM_TAG not in brow or f"\x1b[{u.BOLD}m" not in brow:
        _record("pinned+current masthead: expected a bold row with a dim (default) tag")

    # (C) keyless pin: the #200 keyless dim wins -> NO tag anywhere.
    lu = lineup_keyed("groq-large", {"GROQ_API_KEY"})   # the two soniox rows keyless
    ma = _masthead_with(lu, pinned_default=API_DISPLAY["soniox-live"]["label"])
    if TAG in strip("".join(ma)):
        _record("keyless pinned masthead: a greyed row still shows (default)")

    # (D) plain twin: ASCII tag present on the pinned row, no ESC.
    lu = lineup_for("soniox-live")
    mplain = u.render_masthead(
        lu, PAIRS, PATHS[1] + r"\history", SWITCH,
        START, with_wordmark=True, logo_lines=u.ACTIVE_LOGO_MARK,
        pinned_default=API_DISPLAY["groq"]["label"], ansi=False)
    prow = next((ln for ln in mplain if API_DISPLAY["groq"]["label"] in ln), None)
    if prow is None or TAG not in prow or "\x1b" in prow:
        _record("pinned masthead plain twin: ASCII (default) tag missing or ESC present")


def check_engine_has_key():
    """#200 pure predicate: presence-only, blank == absent, right var per engine,
    and the map covering exactly AVAILABLE_APIS (a new engine without a key
    mapping would otherwise render its row dim forever)."""
    cases = [
        (engine_has_key("groq", {"GROQ_API_KEY": "x"}), True, "present key"),
        (engine_has_key("groq", {"GROQ_API_KEY": "  "}), False, "blank key == absent"),
        (engine_has_key("groq", {}), False, "missing key"),
        (engine_has_key("soniox-live", {"GROQ_API_KEY": "x"}), False, "wrong var (groq for soniox-live)"),
        (engine_has_key("soniox", {"SONIOX_API_KEY": "x"}), True, "soniox off SONIOX_API_KEY"),
        (engine_has_key("nope", {}), False, "unknown engine"),
    ]
    for got, want, label in cases:
        if got is not want:
            _record(f"engine_has_key ({label}): got {got!r}, expected {want!r}")
    if set(API_KEY_ENV) != set(AVAILABLE_APIS):
        _record(f"API_KEY_ENV covers {set(API_KEY_ENV)} != AVAILABLE_APIS {set(AVAILABLE_APIS)}")


# ---- the parameter matrix ----------------------------------------------------
def main():
    for api in AVAILABLE_APIS:
        model = API_DISPLAY[api]["label"]
        lineup = lineup_for(api)

        for path in PATHS:
            run("masthead", u.render_masthead, dict(
                lineup=lineup, keys=PAIRS, history_path=path + r"\history",
                switch_key=SWITCH, start_key=START,
                with_wordmark=True))
        # masthead with the active a5 mark beside the wordmark (as the app wires it)
        run("masthead_logo", u.render_masthead, dict(
            lineup=lineup, keys=PAIRS, history_path=PATHS[1] + r"\history",
            switch_key=SWITCH, start_key=START,
            logo_lines=u.ACTIVE_LOGO_MARK, with_wordmark=True))
        run("ready", u.render_masthead, dict(
            lineup=lineup, keys=PAIRS, history_path=PATHS[1] + r"\history",
            switch_key=SWITCH, start_key=START, with_wordmark=False))

        run("rec", u.render_rec_strip, dict(stops=REC_STOPS))
        run("cancelled", u.render_cancelled_strip, {})
        run("saved", u.render_saved_strip, dict(duration=12.3, retry_key=RETRY))
        run("hotkeys_failed", u.render_hotkeys_failed, {})
        run("switched", u.render_switched_panel,
            dict(new_label=model, lineup=lineup, switch_key=SWITCH))
        run("switch_failed", u.render_switch_failed,
            dict(current_label=model, lineup=lineup, switch_key=SWITCH,
                 missing=["SONIOX_API_KEY", "GROQ_API_KEY"]))
        run("switch_failed", u.render_switch_failed,   # empty branch (non-key skips)
            dict(current_label=model, lineup=lineup, switch_key=SWITCH, missing=[]))
        run("device_loss", u.render_device_loss,
            dict(duration=12.0, retry_key=RETRY, model_label=model, footer=FFOOTER))
        run("mic_failed", u.render_mic_failed,   # #179: audio stream would not open
            dict(model_label=model, footer=FFOOTER))
        run("selftest_failed", u.render_selftest_failed, dict(   # mirrors the app copy (thoughtborne.py)
            reason="self-test failed -- no transcription received",
            action_lines=("check your API key in Settings,", f"then see {LOG_FILE.name} for details")))

        for seq in (None, 12, 99999):
            for chars in (7, 184, 99999):
                for sent in (False, True):
                    run("ok", u.render_ok_strip, dict(
                        seq=seq, chars=chars, sent=sent, model_label=model, footer=FOOTER))
                run("waiting", u.render_waiting_strip, dict(
                    seq=seq, chars=chars, stops=WAIT_STOPS))

        # #7 typed insert: the cap annotation beside the char count (mode='typing',
        # chars <= cap), and the yellow CAPPED strip for a truncated one.
        for chars in (7, 184, 4000):
            for sent in (False, True):
                run("ok/typing", u.render_ok_strip, dict(
                    seq=12, chars=chars, sent=sent, model_label=model,
                    footer=FOOTER, mode="typing", cap=4000))
        for original_chars in (4001, 30818, 999999):
            run("typed_capped", u.render_typed_capped, dict(
                cap=4000, original_chars=original_chars, paste_key=PASTE,
                model_label=model, footer=FOOTER))

        for seq in (None, 12, 99999):
            # #159: one FAILED render per reason (incl. the None catch-all that omits
            # the block) x both provider tokens, in both ansi states.
            for reason in (None, "no-connection", "service-error", "rate-limited", "auth", "no-credit"):
                for provider in ("Soniox", "Groq"):
                    run("transcription_failed", u.render_transcription_failed, dict(
                        seq=seq, retry_key=RETRY, model_label=model, footer=FFOOTER,
                        reason=reason, provider=provider))
            # inconclusive (Soniox-Live async file lane empty + errored): the flag wins
            # over the category, so the "came back empty" message shows.
            run("transcription_failed", u.render_transcription_failed, dict(
                seq=seq, retry_key=RETRY, model_label=model, footer=FFOOTER,
                reason="service-error", provider="Soniox", inconclusive=True))
            run("insert_failed", u.render_insert_failed, dict(
                seq=seq, stops=WAIT_STOPS, model_label=model, footer=FOOTER))

        for clean in (True, False):
            for hk in (True, False):
                run("recovered", u.render_recovered_panel, dict(
                    when="2026-07-11 03:14", duration=42, clean_exit=clean,
                    hotkeys_ok=hk, audio_path=PATHS[3] + r"\history\audio", retry_key=RETRY))

    # #200 keyless shop-window (api-independent): the masthead with every lineup
    # row greyed + a yellow guidance line, and the calm keyless notice a hotkey
    # press raises. run() sweeps both ansi states; check_block enforces width,
    # charset, the plain twin, and (both being yellow) red-exclusivity.
    run("masthead_keyless", u.render_masthead, dict(
        lineup=lineup_keyed(None, set()), keys=PAIRS,
        history_path=PATHS[1] + r"\history",
        switch_key=SWITCH, start_key=START, guidance=GUIDANCE, with_wordmark=True))
    run("keyless", u.render_keyless_notice, dict(settings_key="Ctrl+Alt+G"))
    twin("keyless", u.render_keyless_notice, settings_key="Ctrl+Alt+G")

    # No speech found (#133): a benign yellow verdict, no api-dependent fixture.
    # #159 adds the mic hint + the open-history pointer (the panel's sole Ctrl+Alt).
    run("no_speech", u.render_no_speech, dict(open_key=OPEN))

    # Single-instance guard + honest hotkey verdict (#166), both api-independent:
    # the calm ALREADY RUNNING notice (CYAN, never red) and the partial-
    # registration advisory (YELLOW, never red -- most keys still work). run()
    # sweeps both ansi states and check_block enforces "not red" on both.
    run("already_running", u.render_already_running, {})
    run("hotkeys_partial", u.render_hotkeys_partial, dict(registered=10, expected=11))

    # No-API: MISSING (keys only) and PROBLEMS (with a non-key failure)
    run("noapi", u.render_noapi_panel, dict(
        missing=[("SONIOX_API_KEY", ["soniox-live", "soniox"]),
                 ("GROQ_API_KEY", ["groq-large", "groq"])],
        other_failures=[], env_dir=PATHS[1]))
    run("noapi", u.render_noapi_panel, dict(
        missing=[("SONIOX_API_KEY", ["soniox-live", "soniox"])],
        other_failures=[("groq", "ConnectionError: [Errno 11001] getaddrinfo failed for api.groq.com")],
        env_dir=PATHS[3]))

    # ---- structural twin checks (skip the wordmark masthead) -----------------
    lineup = lineup_for(DEFAULT_API)
    twin("ready", u.render_masthead, lineup=lineup, keys=PAIRS,
         history_path=PATHS[1] + r"\history", switch_key=SWITCH,
         start_key=START, with_wordmark=False)
    twin("rec", u.render_rec_strip, stops=REC_STOPS)
    twin("waiting", u.render_waiting_strip, seq=12, chars=184, stops=WAIT_STOPS)
    twin("ok", u.render_ok_strip, seq=12, chars=184, sent=False,
         model_label="Groq Whisper Large v3", footer=FOOTER)
    twin("ok/typing", u.render_ok_strip, seq=12, chars=184, sent=True,
         model_label="Soniox Live", footer=FOOTER,
         mode="typing", cap=4000)
    twin("typed_capped", u.render_typed_capped, cap=4000, original_chars=30818,
         paste_key=PASTE, model_label="Soniox Live", footer=FOOTER)
    twin("transcription_failed", u.render_transcription_failed, seq=12, retry_key=RETRY,
         model_label="Soniox Live", footer=FFOOTER)
    twin("transcription_failed/reason", u.render_transcription_failed, seq=12, retry_key=RETRY,
         model_label="Soniox Live", footer=FFOOTER,
         reason="no-connection", provider="Soniox")
    twin("transcription_failed/credits", u.render_transcription_failed, seq=12, retry_key=RETRY,
         model_label="Soniox Live", footer=FFOOTER,
         reason="no-credit", provider="Soniox")
    twin("mic_failed", u.render_mic_failed, model_label="Soniox Live",
         footer=FFOOTER)
    twin("recovered", u.render_recovered_panel, when="2026-07-11 03:14", duration=42,
         clean_exit=False, hotkeys_ok=False, audio_path=PATHS[3] + r"\history\audio", retry_key=RETRY)
    twin("no_speech", u.render_no_speech, open_key=OPEN)
    twin("already_running", u.render_already_running)
    twin("hotkeys_partial", u.render_hotkeys_partial, registered=10, expected=11)
    twin("noapi", u.render_noapi_panel, missing=[("SONIOX_API_KEY", ["soniox-live", "soniox"])],
         other_failures=[], env_dir=PATHS[1])

    # ---- grid + seq column anchors (default config) --------------------------
    grid = u._keys_grid_lines(PAIRS, KEY_PREFIX, True)
    if len(grid) != 4:
        _record(f"KEYS grid: expected 4 rows (3+3+3+3), got {len(grid)}")
    for i in range(len(grid)):
        content = strip(grid[i])[1:-1]   # drop the ║ borders -> cells at cols 2/24/46
        if content[24] == " " or content[46] == " ":
            _record(f"KEYS grid row {i} anchor 24/46 broken: {content!r}")
    # D-019 reading order, pinned at the anchors: W A D / Y H X / R L 6 / G T 4.
    for i, want in enumerate(("WAD", "YHX", "RL6", "GT4")):
        content = strip(grid[i])[1:-1]
        got = content[2] + content[24] + content[46]
        if got != want:
            _record(f"KEYS grid row {i} reads {got!r}, expected {want!r}: {content!r}")
    last = strip(grid[-1])[1:-1]         # bottom row: 4 quit at col 46
    if "quit" not in last or last[46] != "4":
        _record(f"KEYS grid: '4 quit' not bottom-right: {last!r}")
    ok = u.render_ok_strip(12, 184, False, "Soniox Live", FOOTER,
                           ansi=True)
    row1 = strip(ok[2])   # top border, +1 headroom line, then the OK row (#109 fold-in)
    if not row1[u.SEQCOL:].lstrip().startswith("seq 12"):
        _record(f"OK strip seq anchor {u.SEQCOL} broken: {row1!r}")

    # ---- #109 logo fold-in state --------------------------------------------
    check_logo_state()

    # ---- #115 cockpit polish: accent, masthead layout, once-per-box ---------
    check_accent_state()
    check_masthead_layout()
    check_ctrl_alt_counts()
    check_failed_reason_block()
    check_no_speech_open_key_width()
    check_strip_structure()
    check_typed_cap_surfaces()

    # ---- D-019 pair contract: key tables, cell geometry, the two strips ------
    check_key_tables_and_budgets()
    check_grid_schemes()
    check_strip_pairs()

    # ---- D-019 the footers and panels, the app half, the width stress (#277) -
    check_footer_schemes()
    check_footer_lead_fallback()
    check_app_derives_no_key()
    check_stress_widths()

    # ---- #55 override edge: a scheme with no shared lead ---------------------
    check_prefix_none_widths()

    # ---- #200 key-aware lineup + keyless shop-window -------------------------
    check_keyless_lineup()
    check_engine_has_key()

    # ---- #219 dim (default) tag on the fixed-pin engine ----------------------
    check_pinned_default_tag()

    # ---- report -------------------------------------------------------------
    if SHOW:
        for name, lines in shown:
            print(f"----- {name} -----")
            for ln in lines:
                print(strip(ln))
            print()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures[:60]:
            print("  " + f)
        return 1
    print("OK: all console_ui screens pass width/charset/twin/anchor checks")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
