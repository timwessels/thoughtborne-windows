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
  2. compact: nominal fixtures <= 46 cells, stress fixtures <= 76.
  3. plain twin: ansi=False render is line-for-line the same length as the
     SGR-stripped ansi=True render (frames stay aligned), carries no ESC, and
     is ASCII + the single allowed umlaut U-umlaut (the self-test hotkey).
  4. ansi=True: every non-ASCII glyph is in the CP437 safe set.
  5. red (SGR 31) appears only in error renderings.
  6. KEYS grid anchored at columns 24/46; OK/WAITING seq block at column 41.
  7. logo fold-in (#109): the active a5 masthead mark renders in ANSI and drops
     in the plain twin; every routine strip carries the monochrome bullet header
     (with its plain 'o' twin) plus one headroom line, compact forms carry none.
"""
import re
import sys

import console_ui as u
from config import API_DISPLAY, AVAILABLE_APIS, DEFAULT_API, HOTKEYS, LOG_FILE

SHOW = "--show" in sys.argv

_SGR = re.compile(r"\x1b\[[0-9;]*m")
def strip(s):
    return _SGR.sub("", s)

# CP437 safe set (terminal-constraints.md) + the U-umlaut self-test hotkey
# + U+2022 bullet (conhost best-fits it to 0x07; see charset-korrektur-bullet.md).
SAFE = set("─│┌┐└┘├┤┬┴┼═║╔╗╚╝╠╣╦╩╬█▓▒░▀▄▌▐■Ü•")

RED_OK = {  # renderings allowed to carry red (error states)
    "transcription_failed", "insert_failed", "selftest_failed",
    "device_loss", "hotkeys_failed", "switch_failed",
}

failures = []
shown = []


def _record(msg):
    failures.append(msg)


def check_block(name, lines, *, ansi, compact, stress):
    """Generic per-block assertions (widths, corners, charset, red exclusivity)."""
    joined = "".join(lines)
    for i, ln in enumerate(lines):
        v = strip(ln)
        if len(v) > u.MAXCOL:
            _record(f"{name}[{i}] len {len(v)} > {u.MAXCOL}: {v!r}")
        if compact:
            limit = u.MAXCOL if stress else u.COMPACT_MAX
            if len(v) > limit:
                _record(f"{name}[{i}] compact len {len(v)} > {limit}: {v!r}")
        else:
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
                if ord(ch) >= 128 and ch != "Ü":
                    _record(f"{name}[{i}] plain non-ASCII {ch!r}: {ln!r}")
    # red exclusivity (only checkable on the styled ansi render)
    if ansi:
        codes = re.findall(r"\x1b\[([0-9;]+)m", joined)
        has_red = any("31" in c.split(";") for c in codes)
        if has_red and name not in RED_OK:
            _record(f"{name} uses red (SGR 31) but is not an error rendering")
        if not has_red and name in RED_OK:
            _record(f"{name} is an error rendering but carries no red tag")


def twin(name, fn, **kw):
    """The ansi=False render must be line-for-line length-equal to the SGR-
    stripped ansi=True render. Skipped for the wordmark masthead (the 3-row
    wordmark deliberately collapses to one plain line, changing the count)."""
    a = fn(ansi=True, compact=False, **kw)
    p = fn(ansi=False, compact=False, **kw)
    if len(a) != len(p):
        _record(f"{name}: plain line count {len(p)} != ansi {len(a)}")
        return
    for i, (la, lp) in enumerate(zip(a, p)):
        if len(strip(la)) != len(lp):
            _record(f"{name}[{i}] twin length {len(lp)} != {len(strip(la))}: "
                    f"A={strip(la)!r} P={lp!r}")


# ---- fixtures from the real config -------------------------------------------
def lineup_for(current):
    return [(API_DISPLAY[a]["label"], API_DISPLAY[a]["descriptor"],
             a == current, a == DEFAULT_API) for a in AVAILABLE_APIS]


def _fmt(combo):
    return "+".join(p.capitalize() for p in combo.split("+"))


def keys_and_prefix():
    """The 11 key letters (KEY_ACTIONS order) plus the shared modifier prefix,
    exactly as the app derives them from config.HOTKEYS."""
    order = ["start_recording", "stop_recording_keyboard", "stop_recording_clipboard",
             "stop_recording_send", "stop_recording_no_insert", "cancel_recording",
             "retry_last_failed", "switch_api", "open_history",
             "test_transcription", "exit_program"]
    combos = []
    for k in order:
        v = HOTKEYS[k]
        combos.append(v[0] if isinstance(v, list) else v)
    prefixes = {c.rpartition("+")[0] for c in combos}
    if len(prefixes) == 1 and "" not in prefixes:
        prefix = _fmt(prefixes.pop())
        letters = [c.rpartition("+")[2].capitalize() for c in combos]
        return letters, prefix
    return [_fmt(c) for c in combos], None


KEYS, KEY_PREFIX = keys_and_prefix()
SWITCH = _fmt(HOTKEYS["switch_api"])       # full combo (switched/switch_failed panels)
OPEN = _fmt(HOTKEYS["open_history"])
# bare letters the masthead now receives (#115): Ctrl+Alt is established once on
# the READY line, so MODEL and the compact history line carry only the letter.
SWITCH_LETTER = HOTKEYS["switch_api"].rpartition("+")[2].capitalize()   # "L"
OPEN_LETTER = HOTKEYS["open_history"].rpartition("+")[2].capitalize()   # "6"
START = _fmt(HOTKEYS["start_recording"])
RETRY = _fmt(HOTKEYS["retry_last_failed"])
FOOTER = [("W", "record"), ("6", "history"), ("L", "model"), ("4", "quit")]   # #115 order
FFOOTER = [("W", "record"), ("R", "retry"), ("L", "model"), ("4", "quit")]

PATHS = [  # the four real checkout depths (Finalisierung 1.10)
    r"C:\thoughtborne",
    r"D:\Daten\_Code\thoughtborne",
    r"C:\Users\Tim Wessels\Documents\thoughtborne",
    r"C:\Users\Maximilian\Downloads\thoughtborne-windows-main",
]
NOTE_SHORT = "SONIOX_API_KEY missing -> started on Groq Whisper Turbo v3 (default: Soniox Live)"
NOTE_LONG = "default API 'soniox-live' unavailable -> started on Groq Whisper Turbo v3 (default: Soniox Live)"


def run(name, fn, kwargs, *, stress=False):
    for ansi in (True, False):
        for compact in (False, True):
            lines = fn(ansi=ansi, compact=compact, **kwargs)
            check_block(name, lines, ansi=ansi, compact=compact, stress=stress)
            if SHOW and ansi and not compact:
                shown.append((name, lines))


# ---- #109 logo fold-in: active a5 mark, bullet strip header, +1 headroom -----
def check_logo_state():
    """Exercise the branding once it is switched on: the a5 mark in the masthead,
    the `• THOUGHTBORNE` / `o THOUGHTBORNE` header and its headroom on every
    routine strip, and the compact forms left bare."""
    if u.ACTIVE_LOGO_MARK is not u.LOGO_MARK_A5:
        _record("ACTIVE_LOGO_MARK is not the a5 mark")
    if u.ACTIVE_STRIP_HEADER != "THOUGHTBORNE":
        _record("ACTIVE_STRIP_HEADER is not 'THOUGHTBORNE'")

    strips = [
        ("rec", u.render_rec_strip, dict(type_key="A", paste_key="D", send_key="H",
                                         keep_key="Y", cancel_key="X", key_prefix=KEY_PREFIX)),
        ("ok", u.render_ok_strip, dict(seq=12, chars=184, sent=False,
                                       model_label="Soniox Live", footer_keys=FOOTER,
                                       key_prefix=KEY_PREFIX)),
        ("waiting", u.render_waiting_strip, dict(seq=12, chars=184, type_key="A", paste_key="D",
                                                 key_prefix=KEY_PREFIX)),
        ("cancelled", u.render_cancelled_strip, {}),
        ("saved", u.render_saved_strip, dict(duration=12.3, retry_key=RETRY)),
    ]
    for name, fn, kw in strips:
        a = fn(ansi=True, compact=False, **kw)
        p = fn(ansi=False, compact=False, **kw)
        if not strip(a[0]).startswith("┌── • THOUGHTBORNE "):
            _record(f"{name}: ANSI strip header missing the bullet: {strip(a[0])!r}")
        if not p[0].startswith("+-- o THOUGHTBORNE "):
            _record(f"{name}: plain strip header missing the 'o' twin: {p[0]!r}")
        if "\x1b" in a[0]:
            _record(f"{name}: strip header border is not monochrome (carries SGR)")
        if strip(a[1])[1:-1].strip() or p[1][1:-1].strip():
            _record(f"{name}: headroom line not blank: {strip(a[1])!r} / {p[1]!r}")
        c = fn(ansi=True, compact=True, **kw)
        if any("THOUGHTBORNE" in strip(ln) for ln in c):
            _record(f"{name}: compact form unexpectedly carries the header")

    # a5 disc: rendered beside the wordmark in ANSI, gone from the plain twin
    # (which collapses to WM_PLAIN).
    mkw = dict(lineup=lineup_for(DEFAULT_API), keys=KEYS, key_prefix=KEY_PREFIX,
               history_path=PATHS[1] + r"\history", open_key=OPEN_LETTER, switch_key=SWITCH_LETTER,
               start_key=START, logo_lines=u.ACTIVE_LOGO_MARK, with_wordmark=True)
    ma = u.render_masthead(ansi=True, compact=False, **mkw)
    mp = u.render_masthead(ansi=False, compact=False, **mkw)
    mid = u.LOGO_MARK_A5[1]                       # "▄▀▀████" -- the unambiguous mark row
    if not any(mid in strip(ln) for ln in ma):
        _record("masthead: a5 mark not present in the ANSI render")
    if any(mid in ln for ln in mp):
        _record("masthead: a5 mark leaked into the plain twin")
    if not any(u.WM_PLAIN in ln for ln in mp):
        _record("masthead: plain twin lost the WM_PLAIN wordmark")


# ---- #115 brand accent: masthead wordmark + mark only ------------------------
ACC = f"\x1b[{u.ACCENT}m"    # the accent SGR as it appears inline


def _masthead(ansi, compact, *, logo=True, wordmark=True):
    return u.render_masthead(
        lineup_for(DEFAULT_API), KEYS, KEY_PREFIX, PATHS[1] + r"\history",
        OPEN_LETTER, SWITCH_LETTER, START,
        logo_lines=(u.ACTIVE_LOGO_MARK if logo else None), with_wordmark=wordmark,
        ansi=ansi, compact=compact)


def check_accent_state():
    """The brand accent (#115) rides only on the masthead wordmark + logo mark;
    never the tagline, never a strip/panel, and it never trips red-exclusivity."""
    if "31" in u.ACCENT.split(";"):
        _record("ACCENT constant contains the red code (31) -- red must stay error-exclusive")

    ma = _masthead(True, False)
    wm_rows = [i for i, ln in enumerate(ma)
               if any(w in strip(ln) for w in u.WM)]        # the 3 mark+wordmark rows
    for i, ln in enumerate(ma):
        if i in wm_rows and ACC not in ln:
            _record(f"masthead accent: wordmark row {i} not accented")
        if i not in wm_rows and ACC in ln:
            _record(f"masthead accent: leaked onto non-wordmark line {i}: {strip(ln)!r}")
    if any(u.TAGLINE in strip(ln) and ACC in ln for ln in ma):
        _record("masthead accent: the tagline must not be accented")
    if any("\x1b" in ln for ln in _masthead(False, False)):
        _record("masthead accent: plain masthead carries an escape sequence")

    # compact masthead: WM_COMPACT accented in ANSI, never in plain
    if not any(ACC in ln and u.WM_COMPACT in strip(ln) for ln in _masthead(True, True)):
        _record("compact masthead: WM_COMPACT is not accented in ANSI")
    if any("\x1b" in ln for ln in _masthead(False, True)):
        _record("compact masthead: plain form carries an escape sequence")

    # accent is exclusive to the masthead wordmark/mark -- no strip/panel takes it
    model, lu = "Soniox Live", lineup_for(DEFAULT_API)
    others = [
        u.render_rec_strip("A", "D", "H", "Y", "X", KEY_PREFIX, ansi=True, compact=False),
        u.render_ok_strip(12, 184, False, model, FOOTER, KEY_PREFIX, ansi=True, compact=False),
        u.render_waiting_strip(12, 184, "A", "D", KEY_PREFIX, ansi=True, compact=False),
        u.render_transcription_failed(12, RETRY, model, FFOOTER, KEY_PREFIX,
                                      ansi=True, compact=False),
        u.render_transcription_failed(12, RETRY, model, FFOOTER, KEY_PREFIX,   # #159 reason block
                                      reason="no-connection", provider="Soniox",
                                      ansi=True, compact=False),
        u.render_device_loss(12.0, RETRY, model, FFOOTER, KEY_PREFIX, ansi=True, compact=False),
        u.render_switched_panel(model, lu, SWITCH, ansi=True, compact=False),
    ]
    for lines in others:
        if ACC in "".join(lines):
            _record(f"accent leaked into a non-masthead render: {strip(lines[1])!r}")


def check_masthead_layout():
    """#115 masthead: three framed spacers (before MODEL/KEYS/History), tagline
    centered under the wordmark, capitalised `History:` edge without the open
    hint, plain `KEYS` header."""
    ma = [strip(ln) for ln in _masthead(True, False)]
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

    # KEYS header is plain (no 'all are Ctrl+Alt' hint)
    kline = next((s for s in ma if s.startswith("╠══ KEYS")), "")
    if "all are" in kline:
        _record(f"masthead layout: KEYS header still carries a prefix hint: {kline!r}")
    # History edge: capitalised, no open/lowercase-history hint
    edge = ma[-1]
    if not edge.startswith("╚═ History: "):
        _record(f"masthead layout: History edge wrong: {edge!r}")
    if "open:" in edge or "history:" in edge:
        _record(f"masthead layout: History edge still has an open/lowercase hint: {edge!r}")


def check_ctrl_alt_counts():
    """The core #115 rule: exactly one Ctrl+Alt per framed box (0 where the box
    carries no hotkey action). The strongest single pin of 'once per box'."""
    model, lu = "Soniox Live", lineup_for(DEFAULT_API)
    cases = [
        ("masthead", _masthead(True, False), 1),
        ("ready", _masthead(True, False, logo=False, wordmark=False), 1),
        ("rec", u.render_rec_strip("A", "D", "H", "Y", "X", KEY_PREFIX,
                                   ansi=True, compact=False), 1),
        ("ok", u.render_ok_strip(12, 184, False, model, FOOTER, KEY_PREFIX,
                                 ansi=True, compact=False), 1),
        ("ok/typing", u.render_ok_strip(12, 184, False, model, FOOTER, KEY_PREFIX,
                                        mode="typing", cap=4000, ansi=True, compact=False), 1),
        ("typed_capped", u.render_typed_capped(4000, 30818, "A", model, FOOTER, KEY_PREFIX,
                                               ansi=True, compact=False), 1),
        ("waiting", u.render_waiting_strip(12, 184, "A", "D", KEY_PREFIX,
                                           ansi=True, compact=False), 1),
        ("cancelled", u.render_cancelled_strip(ansi=True, compact=False), 0),
        ("saved", u.render_saved_strip(12.3, RETRY, ansi=True, compact=False), 1),
        ("transcription_failed", u.render_transcription_failed(
            12, RETRY, model, FFOOTER, KEY_PREFIX, ansi=True, compact=False), 1),
        ("transcription_failed/reason", u.render_transcription_failed(   # #159 reason block: still 1
            12, RETRY, model, FFOOTER, KEY_PREFIX, reason="no-connection",
            provider="Groq", ansi=True, compact=False), 1),
        ("transcription_failed/auth", u.render_transcription_failed(     # #159 auth -> Settings: still 1
            12, RETRY, model, FFOOTER, KEY_PREFIX, reason="auth",
            provider="Soniox", ansi=True, compact=False), 1),
        ("insert_failed", u.render_insert_failed(
            12, "A", "D", model, FOOTER, KEY_PREFIX, ansi=True, compact=False), 1),
        ("device_loss", u.render_device_loss(
            12.0, RETRY, model, FFOOTER, KEY_PREFIX, ansi=True, compact=False), 1),
        ("selftest_failed", u.render_selftest_failed(
            "self-test failed -- no transcription received",
            ("check your API key in .env,", f"then see {LOG_FILE.name} for details"),
            ansi=True, compact=False), 0),
        ("hotkeys_failed", u.render_hotkeys_failed(ansi=True, compact=False), 0),
        ("switch_failed", u.render_switch_failed(
            model, lu, SWITCH, missing=["SONIOX_API_KEY"], ansi=True, compact=False), 1),
        ("switched", u.render_switched_panel(model, lu, SWITCH, ansi=True, compact=False), 1),
        ("recovered", u.render_recovered_panel(
            "2026-07-11 03:14", 42, False, True, PATHS[3] + r"\history\audio", RETRY,
            ansi=True, compact=False), 1),
        ("noapi", u.render_noapi_panel(
            [("SONIOX_API_KEY", ["soniox-live"])], [], PATHS[1], ansi=True, compact=False), 0),
        ("no_speech", u.render_no_speech(OPEN, ansi=True, compact=False), 1),   # #159: open-history hint
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
            12, RETRY, model, FFOOTER, KEY_PREFIX, ansi=True, compact=False, **kw)

    # Every categorized reason renders a CYAN two-line block, never ACCENT.
    for reason in ("no-connection", "service-error", "rate-limited", "auth"):
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
                    u.render_no_speech(long_open, ansi=ansi, compact=False),
                    ansi=ansi, compact=False, stress=False)


def check_strip_structure():
    """#115 strip key lines: one `Ctrl+Alt + ` lead, first key at column 14, no
    lead-in labels; the OK strip's model sits on its own line."""
    lead = f"  {KEY_PREFIX} +  "
    if len(lead) != 14:
        _record(f"strip lead is {len(lead)} cols, expected 14 (shipped Ctrl+Alt prefix)")
    rec = u.render_rec_strip("A", "D", "H", "Y", "X", KEY_PREFIX, ansi=True, compact=False)
    ok = u.render_ok_strip(12, 184, False, "Soniox Live", FOOTER, KEY_PREFIX,
                           ansi=True, compact=False)
    waiting = u.render_waiting_strip(12, 184, "A", "D", KEY_PREFIX, ansi=True, compact=False)
    for name, lines in (("rec", rec), ("ok", ok), ("waiting", waiting)):
        joined = strip("".join(lines))
        for label in ("stop:", "or:", "insert:", "retry:"):
            if label in joined:
                _record(f"{name}: stale lead-in label {label!r}")
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

    # compact: same label-free rule, one Ctrl+Alt lead where a key line exists
    compacts = [
        ("rec/compact", u.render_rec_strip("A", "D", "H", "Y", "X", KEY_PREFIX,
                                           ansi=True, compact=True)),
        ("waiting/compact", u.render_waiting_strip(12, 184, "A", "D", KEY_PREFIX,
                                                   ansi=True, compact=True)),
        ("insert_failed/compact", u.render_insert_failed(12, "A", "D", "Soniox Live", FOOTER,
                                                         KEY_PREFIX, ansi=True, compact=True)),
    ]
    for name, lines in compacts:
        joined = strip("".join(lines))
        for label in ("stop:", "or:", "insert:", "insert it:", "retry:"):
            if label in joined:
                _record(f"{name}: stale lead-in label {label!r}")
        if joined.count("Ctrl+Alt") != 1:
            _record(f"{name}: expected one Ctrl+Alt lead, got {joined.count('Ctrl+Alt')}")


# ---- #7 typed-insert cap: the OK/typing annotation + the CAPPED strip ---------
def check_typed_cap_surfaces():
    """#7: the OK/typing strip shows the ceiling beside the char count and drops
    the seq block; the CAPPED strip is a calm YELLOW success-with-notice (never
    red), naming the clipboard key and history, framed at full width."""
    model = "Soniox Live"

    ok = u.render_ok_strip(12, 3990, False, model, FOOTER, KEY_PREFIX,
                           mode="typing", cap=4000, ansi=True, compact=False)
    joined = strip("".join(ok))
    if "typed at the cursor" not in joined:
        _record("ok/typing: headline is not 'typed at the cursor'")
    if "(max 4,000)" not in joined:
        _record("ok/typing: missing the '(max 4,000)' annotation")
    row1 = strip(ok[2])   # top border, +1 headroom line, then the OK row
    if "seq " in row1:
        _record(f"ok/typing: the typed strip still carries a seq block: {row1!r}")

    cap = u.render_typed_capped(4000, 999999, "A", model, FOOTER, KEY_PREFIX,
                                ansi=True, compact=False)
    joinedc = "".join(cap)
    codes = re.findall(r"\x1b\[([0-9;]+)m", joinedc)
    if not any("33" in c.split(";") for c in codes):
        _record("typed_capped: CAPPED tag is not yellow (SGR 33)")
    if any("31" in c.split(";") for c in codes):
        _record("typed_capped: carries red (SGR 31) -- a cap is a success, not an error")
    txt = strip(joinedc)
    for want, label in (("CAPPED", "the CAPPED tag"), ("history", "history"),
                        ("A re-inserts", "the paste key")):
        if want not in txt:
            _record(f"typed_capped: does not carry {label}: {txt!r}")
    for ln in cap:
        v = strip(ln)
        if v and len(v) != u.W:
            _record(f"typed_capped framed line != {u.W}: {v!r}")


# ---- #55 override edge: no shared modifier prefix (key_prefix=None) ----------
def check_prefix_none_widths():
    """An override can leave the effective hotkeys without a shared modifier lead
    (a bare F-key rebind, or mixed prefixes), so the app derives key_prefix=None --
    a framed path the shipped config never reaches. Guard the masthead KEYS grid
    and a routine strip on it at full width. The compact/narrow-window layout of
    long full combos is a separate concern, outside #55's display-only scope."""
    lineup = lineup_for(DEFAULT_API)
    mixed_keys = ["F9", "Ctrl+Alt+A", "Ctrl+Alt+D", "Ctrl+Alt+H", "Ctrl+Alt+Y",
                  "Ctrl+Alt+X", "Ctrl+Shift+F12", "Ctrl+Alt+L", "Ctrl+Alt+6",
                  "Ctrl+Alt+Ü", "Ctrl+Alt+4"]
    bare_footer = [("F9", "record"), ("F6", "history"), ("F10", "model"), ("F4", "quit")]
    fixtures = [
        ("masthead_prefix_none", u.render_masthead,
         dict(lineup=lineup, keys=mixed_keys, key_prefix=None,
              history_path=PATHS[1] + r"\history", open_key="6", switch_key="L",
              start_key="F9", with_wordmark=False)),
        ("ok_prefix_none", u.render_ok_strip,
         dict(seq=12, chars=184, sent=False, model_label="Soniox Live",
              footer_keys=bare_footer, key_prefix=None)),
    ]
    for name, fn, kw in fixtures:
        for ansi in (True, False):
            check_block(name, fn(ansi=ansi, compact=False, **kw),
                        ansi=ansi, compact=False, stress=False)
        twin(name, fn, **kw)


# ---- the parameter matrix ----------------------------------------------------
def main():
    for api in AVAILABLE_APIS:
        model = API_DISPLAY[api]["label"]
        lineup = lineup_for(api)

        for note in (None, NOTE_SHORT, NOTE_LONG):
            for path in PATHS:
                run("masthead", u.render_masthead, dict(
                    lineup=lineup, keys=KEYS, key_prefix=KEY_PREFIX, history_path=path + r"\history",
                    open_key=OPEN_LETTER, switch_key=SWITCH_LETTER, start_key=START,
                    note=note, with_wordmark=True))
        # masthead with the active a5 mark beside the wordmark (as the app wires it)
        run("masthead_logo", u.render_masthead, dict(
            lineup=lineup, keys=KEYS, key_prefix=KEY_PREFIX, history_path=PATHS[1] + r"\history",
            open_key=OPEN_LETTER, switch_key=SWITCH_LETTER, start_key=START,
            logo_lines=u.ACTIVE_LOGO_MARK, with_wordmark=True))
        run("ready", u.render_masthead, dict(
            lineup=lineup, keys=KEYS, key_prefix=KEY_PREFIX, history_path=PATHS[1] + r"\history",
            open_key=OPEN_LETTER, switch_key=SWITCH_LETTER, start_key=START, with_wordmark=False))

        run("rec", u.render_rec_strip,
            dict(type_key="A", paste_key="D", send_key="H", keep_key="Y", cancel_key="X",
                 key_prefix=KEY_PREFIX))
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
            dict(duration=12.0, retry_key=RETRY, model_label=model, footer_keys=FFOOTER,
                 key_prefix=KEY_PREFIX))
        run("selftest_failed", u.render_selftest_failed, dict(   # mirrors the app copy (thoughtborne.py)
            reason="self-test failed -- no transcription received",
            action_lines=("check your API key in .env,", f"then see {LOG_FILE.name} for details")))

        for seq in (None, 12, 99999):
            for chars in (7, 184, 99999):
                for sent in (False, True):
                    run("ok", u.render_ok_strip, dict(
                        seq=seq, chars=chars, sent=sent, model_label=model, footer_keys=FOOTER,
                        key_prefix=KEY_PREFIX),
                        stress=(seq == 99999 or chars == 99999))
                run("waiting", u.render_waiting_strip, dict(
                    seq=seq, chars=chars, type_key="A", paste_key="D", key_prefix=KEY_PREFIX),
                    stress=(seq == 99999 or chars == 99999))

        # #7 typed insert: the cap annotation beside the char count (mode='typing',
        # chars <= cap), and the yellow CAPPED strip for a truncated one.
        for chars in (7, 184, 4000):
            for sent in (False, True):
                run("ok/typing", u.render_ok_strip, dict(
                    seq=12, chars=chars, sent=sent, model_label=model, footer_keys=FOOTER,
                    key_prefix=KEY_PREFIX, mode="typing", cap=4000))
        for original_chars in (4001, 30818, 999999):
            run("typed_capped", u.render_typed_capped, dict(
                cap=4000, original_chars=original_chars, paste_key="A", model_label=model,
                footer_keys=FOOTER, key_prefix=KEY_PREFIX),
                stress=(original_chars == 999999))

        for seq in (None, 12, 99999):
            # #159: one FAILED render per reason (incl. the None catch-all that omits
            # the block) x both provider tokens, through the full ansi x compact matrix.
            for reason in (None, "no-connection", "service-error", "rate-limited", "auth"):
                for provider in ("Soniox", "Groq"):
                    run("transcription_failed", u.render_transcription_failed, dict(
                        seq=seq, retry_key=RETRY, model_label=model, footer_keys=FFOOTER,
                        key_prefix=KEY_PREFIX, reason=reason, provider=provider),
                        stress=(seq == 99999))
            # inconclusive (Soniox-Live V2->V4 lane empty + errored): the flag wins
            # over the category, so the "came back empty" message shows.
            run("transcription_failed", u.render_transcription_failed, dict(
                seq=seq, retry_key=RETRY, model_label=model, footer_keys=FFOOTER,
                key_prefix=KEY_PREFIX, reason="service-error", provider="Soniox",
                inconclusive=True),
                stress=(seq == 99999))
            run("insert_failed", u.render_insert_failed, dict(
                seq=seq, type_key="A", paste_key="D", model_label=model, footer_keys=FOOTER,
                key_prefix=KEY_PREFIX),
                stress=(seq == 99999))

        for clean in (True, False):
            for hk in (True, False):
                run("recovered", u.render_recovered_panel, dict(
                    when="2026-07-11 03:14", duration=42, clean_exit=clean,
                    hotkeys_ok=hk, audio_path=PATHS[3] + r"\history\audio", retry_key=RETRY))

    # No speech found (#133): a benign yellow verdict, no api-dependent fixture.
    # #159 adds the mic hint + the open-history pointer (the panel's sole Ctrl+Alt).
    run("no_speech", u.render_no_speech, dict(open_key=OPEN))

    # No-API: MISSING (keys only) and PROBLEMS (with a non-key failure)
    run("noapi", u.render_noapi_panel, dict(
        missing=[("SONIOX_API_KEY", ["soniox-live", "soniox"]),
                 ("GROQ_API_KEY", ["groq-large", "groq"])],
        other_failures=[], env_dir=PATHS[1]))
    run("noapi", u.render_noapi_panel, dict(
        missing=[("SONIOX_API_KEY", ["soniox-live", "soniox"])],
        other_failures=[("groq", "ConnectionError: [Errno 11001] getaddrinfo failed for api.groq.com")],
        env_dir=PATHS[3]), stress=True)

    # ---- structural twin checks (skip the wordmark masthead) -----------------
    lineup = lineup_for(DEFAULT_API)
    twin("ready", u.render_masthead, lineup=lineup, keys=KEYS, key_prefix=KEY_PREFIX,
         history_path=PATHS[1] + r"\history", open_key=OPEN_LETTER, switch_key=SWITCH_LETTER,
         start_key=START, with_wordmark=False)
    twin("ok", u.render_ok_strip, seq=12, chars=184, sent=False,
         model_label="Groq Whisper Large v3", footer_keys=FOOTER, key_prefix=KEY_PREFIX)
    twin("ok/typing", u.render_ok_strip, seq=12, chars=184, sent=True,
         model_label="Soniox Live", footer_keys=FOOTER, key_prefix=KEY_PREFIX,
         mode="typing", cap=4000)
    twin("typed_capped", u.render_typed_capped, cap=4000, original_chars=30818,
         paste_key="A", model_label="Soniox Live", footer_keys=FOOTER, key_prefix=KEY_PREFIX)
    twin("transcription_failed", u.render_transcription_failed, seq=12, retry_key=RETRY,
         model_label="Soniox Live", footer_keys=FFOOTER, key_prefix=KEY_PREFIX)
    twin("transcription_failed/reason", u.render_transcription_failed, seq=12, retry_key=RETRY,
         model_label="Soniox Live", footer_keys=FFOOTER, key_prefix=KEY_PREFIX,
         reason="no-connection", provider="Soniox")
    twin("recovered", u.render_recovered_panel, when="2026-07-11 03:14", duration=42,
         clean_exit=False, hotkeys_ok=False, audio_path=PATHS[3] + r"\history\audio", retry_key=RETRY)
    twin("no_speech", u.render_no_speech, open_key=OPEN)
    twin("noapi", u.render_noapi_panel, missing=[("SONIOX_API_KEY", ["soniox-live", "soniox"])],
         other_failures=[], env_dir=PATHS[1])

    # ---- grid + seq column anchors (default config) --------------------------
    grid = u._keys_grid_lines(KEYS, KEY_PREFIX, True)
    for i in range(min(3, len(grid))):
        content = strip(grid[i])[1:-1]   # drop the ║ borders -> cells at cols 2/24/46
        if content[24] == " " or content[46] == " ":
            _record(f"KEYS grid row {i} anchor 24/46 broken: {content!r}")
    ok = u.render_ok_strip(12, 184, False, "Soniox Live", FOOTER, KEY_PREFIX,
                           ansi=True, compact=False)
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

    # ---- #55 override edge: key_prefix=None framed render --------------------
    check_prefix_none_widths()

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


if __name__ == "__main__":
    sys.exit(main())
