"""Console renderer for the Thoughtborne "Cockpit" console design (#109).

Pure presentation layer: every function takes plain data plus the runtime
switch `ansi` and returns a list of ready-to-print lines. The module imports
nothing from the project -- all dynamic values (labels, hotkey combos, paths,
seq/chars) arrive as parameters -- so it renders and is width-verified without
a running Windows tool (see test_console_ui.py).

Every key-bearing surface receives `(action_name, display_combo)` pairs (in the
canonical config.DEFAULT_HOTKEYS order, D-019; the footers in this module's own
#115 order, FOOTER_ACTIONS); this module holds the labels and the cell geometry
and decides what to show -- the bare key under a shared modifier lead, the full
combo without one, `[...]+<key>` past the key-column budget. Prose is the one
place a key is never bare: a sentence always names the full combo, since a bare
`R` in one reads as "type R".

Two frame classes carry the visual hierarchy (variant B grammar):
  - main panel   (double frame ╔═╗) for orientation moments: startup, errors,
    switch, recovery -- with labelled zone separators (╠══ MODEL ══╣);
  - strip        (single frame ┌─┐) for routine events: recording, success.

The central invariant is in _compose(): width math runs on the VISIBLE
characters only, styling (SGR) is applied afterwards, so escape sequences never
skew a frame edge. Unicode -> plain-ASCII translation and SGR both hang on the
same `ansi` flag; ansi=False lines carry no escape bytes by construction, so the
1:1 PLAIN translate is length-preserving and the frames stay column-aligned.

Only the CP437 safe set is used for frames (│─┌┐└┘ ║═╔╗╚╝╠╣ █▀▄), each gated
behind `ansi`; the plain twin degrades every glyph to ASCII (`+=|-`, `#`). Color
is limited to the 16 ANSI colors + bold; red is reserved for error states.
"""

# ---- geometry ----
W = 70            # outer width of every panel/strip (variant-b invariant)
INNER = W - 2     # 68 content cells between the vertical borders
MAXCOL = 76       # hard limit for any console line (80-col cmd minus margin)

# ---- SGR palette (16 colors + bold; red stays error-exclusive) ----
BOLD = "1"
RED = "31"
GREEN = "32"
CYAN = "36"
YELLOW = "33"
DIM = "90"   # bright black -- SGR 2 (faint) is unreliable on conhost

# Brand accent for the masthead wordmark + logo mark ONLY -- a deliberate,
# documented exception to the 16-color doctrine (terminal-constraints.md).
# Purely decorative: meaning never rides on it, red/semantic tags untouched,
# and SGR never affects width math (_compose). On ancient conhost this rounds to
# a nearby palette color -- still blue-ish, never a broken layout. Flip to CYAN
# for a strict-16-color fallback (one line).
ACCENT = "38;2;89;194;255"   # -> CYAN for a 16-color fallback

# ---- plain-ASCII twin: 1 char -> 1 char, hence length-preserving ----
# Wordmark/mark glyphs (▀▄) are deliberately absent: they are handled by group
# replacement / gating at composition time, never by this table.
PLAIN = str.maketrans({
    "═": "=", "║": "|",
    "╔": "+", "╗": "+", "╚": "+", "╝": "+",
    "╠": "+", "╣": "+", "╦": "+", "╩": "+", "╬": "+",
    "─": "-", "│": "|",
    "┌": "+", "┐": "+", "└": "+", "┘": "+",
    "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+",
    "█": "#",
    "•": "o",   # strip-header bullet -> ASCII twin (1 char, length-preserving)
})

# ---- wordmark (figlet pagga; letter tokens T H O U G H T B O R N E) ----
_R1 = ["▀█▀", "█ █", "█▀█", "█ █", "█▀▀", "█ █", "▀█▀", "█▀▄", "█▀█", "█▀▄", "█▀█", "█▀▀"]
_R2 = [" █ ", "█▀█", "█ █", "█ █", "█ █", "█▀█", " █ ", "█▀▄", "█ █", "█▀▄", "█ █", "█▀▀"]
_R3 = [" ▀ ", "▀ ▀", "▀▀▀", "▀▀▀", "▀▀▀", "▀ ▀", " ▀ ", "▀▀ ", "▀▀▀", "▀ ▀", "▀ ▀", "▀▀▀"]
WM = [" ".join(r) for r in (_R1, _R2, _R3)]   # 47 cols each
WM_PLAIN = "== THOUGHTBORNE =="               # 18 cols (length-matched on purpose)
TAGLINE = "voice-to-text for Windows"
LAMP = "██"

# ---- logo gallery candidates + single activation point (#109) --------------
# Tim's pick (locked): the round a5 disc beside the masthead wordmark, and a
# bullet before the strip-header name. ACTIVE_LOGO_MARK / ACTIVE_STRIP_HEADER
# below are the single activation point. The mark lives only in the ANSI/framed
# masthead; in plain mode the wordmark degrades to WM_PLAIN and the mark drops
# with it (no # cluster). The strip header NAME is pure ASCII and survives
# plain; its bullet degrades 1:1 to "o" via the PLAIN table.
LOGO_MARK_A5 = [           # round disc, cites the v1 mark (gallery a5, width 7)
    " ▄███▄",
    "▄▀▀████",
    " ▀█▄ ▀",
]
LOGO_MARK_B1 = [           # symmetric waveform (gallery b1, width 9)
    "  ▄ █ ▄",
    "█ █ █ █ █",
    "  ▀ █ ▀",
]
STRIP_HEADER_GLYPH = "•"   # bullet before the name; conhost-safe, plain twin -> o

ACTIVE_LOGO_MARK = LOGO_MARK_A5        # None | LOGO_MARK_A5 | LOGO_MARK_B1
ACTIVE_STRIP_HEADER = "THOUGHTBORNE"   # None | "THOUGHTBORNE" (optionally a glyph)

# ---- key copy, keyed by action name (#274) ---------------------------------
# console_ui imports nothing from the project: that these keys match
# config.DEFAULT_HOTKEYS is pinned by test_console_ui, not by an import.
KEY_LABELS = {   # the KEYS grid; exactly the twelve action names
    "start_recording": "start recording",
    "stop_recording_clipboard": "stop, paste",
    "stop_recording_send": "stop, paste+Enter",
    "stop_recording_no_insert": "stop, keep only",
    "stop_recording_keyboard": "stop, type text",
    "cancel_recording": "cancel recording",
    "retry_last_failed": "retry last failed",
    "switch_api": "switch model",
    "open_history": "open history",
    "open_settings": "settings (gear)",
    "test_transcription": "self-test",
    "exit_program": "quit",
}
KEY_WORDS = {    # strips and footers, short form
    "start_recording": "record",
    "stop_recording_clipboard": "paste",
    "stop_recording_send": "paste+Enter",
    "stop_recording_no_insert": "keep only",
    "stop_recording_keyboard": "type",
    "cancel_recording": "cancel",
    "retry_last_failed": "retry",
    "switch_api": "model",
    "open_history": "history",
    "exit_program": "quit",
}
# The four actions the footer lists, in the #115 reading order (record .
# history/retry . model . quit) -- deliberately NOT the canonical D-019 order:
# the footer line reads as a sentence, not a grid, and has read that way since
# #115. It selects four actions by name; it is not a second copy of the twelve-
# action order. The app supplies the combos for these names (D-019, #290).
FOOTER_ACTIONS = ("start_recording", "open_history", "switch_api", "exit_program")
FOOTER_ACTIONS_RETRY = ("start_recording", "retry_last_failed", "switch_api",
                        "exit_program")

ELLIPSIS = "[...]"   # ASCII on purpose: survives CP437 and the plain twin 1:1

SEQCOL = 41  # column where the OK/WAITING strip's seq/chars block starts


# =====================================================================
# Line primitives -- width on visible chars, styling last (the invariant)
# =====================================================================
def _sgr(text, codes, ansi):
    if not ansi or not codes:
        return text
    return f"\x1b[{';'.join(codes)}m{text}\x1b[0m"


def _segs(x):
    """Accept a bare string (one unstyled segment) or a list of (text, codes)."""
    return [(x, ())] if isinstance(x, str) else x


def _compose(left, right, segs, ansi, width=INNER):
    """Frame one content line. segs: list of (text, codes). The padding runs on
    the visible length; a plain (ansi=False) line -- which carries no escape
    bytes -- is translated 1:1 as the last step so the frame stays aligned."""
    visible = sum(len(t) for t, _ in segs)
    body = "".join(_sgr(t, c, ansi) for t, c in segs) + " " * (width - visible)
    line = left + body + right
    return line if ansi else line.translate(PLAIN)


# double-frame main panel
def dtop(ansi):
    return _fin("╔" + "═" * INNER + "╗", ansi)


def dbot(ansi):
    return _fin("╚" + "═" * INNER + "╝", ansi)


def dsep(ansi):
    return _fin("╠" + "═" * INNER + "╣", ansi)


def dline(segs, ansi):
    return _compose("║", "║", _segs(segs), ansi)


def dzone(segs, ansi):
    """Labelled zone separator: ╠══ LABEL ═...═╣."""
    segs = _segs(segs)
    visible = sum(len(t) for t, _ in segs)
    styled = "".join(_sgr(t, c, ansi) for t, c in segs)
    fill = W - 1 - (4 + visible + 1)   # "╠══ " + label + " " ... "╣"
    line = "╠══ " + styled + " " + "═" * max(0, fill) + "╣"
    return line if ansi else line.translate(PLAIN)


def dedge(segs, ansi):
    """Bottom edge with embedded (dim) text: ╚═ text ═...═╝."""
    segs = _segs(segs)
    visible = sum(len(t) for t, _ in segs)
    styled = "".join(_sgr(t, c, ansi) for t, c in segs)
    fill = W - 1 - (3 + visible + 1)   # "╚═ " + text + " " ... "╝"
    line = "╚═ " + styled + " " + "═" * max(2, fill) + "╝"
    return line if ansi else line.translate(PLAIN)


# single-frame strip
def stop_(ansi):
    return _fin("┌" + "─" * INNER + "┐", ansi)


def sbot(ansi):
    return _fin("└" + "─" * INNER + "┘", ansi)


def sline(segs, ansi):
    return _compose("│", "│", _segs(segs), ansi)


def _fin(line, ansi):
    """Translate a frame-only line to its plain twin (borders never carry SGR,
    so a bare translate is safe). ansi=True passes the CP437 glyphs through."""
    return line if ansi else line.translate(PLAIN)


# =====================================================================
# Truncation + wrapping helpers
# =====================================================================
def truncate_path_middle(path, budget):
    """Middle-ellipsis a path to `budget` cells, keeping the drive root and,
    when close enough, snapping the tail to a component boundary (I2)."""
    if len(path) <= budget:
        return path
    head = path[:3]                            # "C:\"  (drive root)
    tail = path[-(budget - len(head) - 3):]    # 3 = len("...")
    cut = tail.find("\\")                      # snap to a component boundary
    if 0 <= cut <= 15:
        tail = tail[cut:]
    return head + "..." + tail


def truncate_end(text, budget):
    return text if len(text) <= budget else text[:budget - 3] + "..."


def _wrap(text, width, indent):
    """Greedy word wrap; continuation lines get `indent` spaces. Returns the line
    bodies (indent already applied to lines 1..n)."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        cand = w if not cur else cur + " " + w
        if len(cand) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if not lines:
        return [""]
    return [lines[0]] + [" " * indent + ln for ln in lines[1:]]


# =====================================================================
# Shared zone builders
# =====================================================================
def _lineup_lines(lineup, ansi, pinned_label=None):
    """MODEL lineup rows. lineup: list of (label, descriptor, is_current,
    has_key). The current row is bold; a row whose key env var is absent renders
    dim (#200), so the greyed rows show at a glance which engines are usable.
    `pinned_label` (a label, or None) tags the engine an active defaults.api pin
    forces at startup with a dim ` (default)`."""
    width = max((len(lbl) for lbl, *_ in lineup), default=0)
    rows = []
    for label, descriptor, is_current, has_key in lineup:
        marker = ">" if is_current else " "
        main = "  " + marker + " " + label.ljust(width) + " - " + descriptor
        codes = (BOLD,) if is_current else () if has_key else (DIM,)
        segs = [(main, codes)]
        # #219: dim (default) tag on the engine an active fixed pin forces. Dim
        # regardless of the row's own weight; the `and has_key` lets the #200
        # keyless dim win, so a greyed (unusable) row never claims to be default.
        # Distinct from the #200-removed built-in-default marker (this keys on an
        # explicit pin, not the fallback DEFAULT_API).
        if pinned_label is not None and has_key and label == pinned_label:
            segs.append((" (default)", (DIM,)))
        rows.append(dline(segs, ansi))
    return rows


def _display_prefix(combos):
    """The shared modifier lead of one box, derived from the display combos it
    is handed (#272 rule 5: the renderer decides what to show). Mirrors
    hotkey_parse.common_prefix on formatted combos -- a deliberate twin, since
    this module imports nothing from the project; the canonical spelling of #275
    makes the two agree (format_combo maps token-wise). None on any mix."""
    prefixes = {c.rpartition("+")[0] for c in combos}
    if len(prefixes) == 1 and "" not in prefixes:
        return prefixes.pop()
    return None


def _bare(combo, key_prefix):
    """What a box shows for `combo` under its lead: the part behind the shared
    prefix -- or the full combo when there is no lead. Every caller derives the
    prefix from exactly the combos it passes here, so a combo that does not carry
    it cannot occur; the second return is the guarantee behind that, not a case
    -- a wrong prefix can never yield a wrong key, only a redundant one."""
    if key_prefix and combo.startswith(key_prefix + "+"):
        return combo[len(key_prefix) + 1:]
    return combo


def _shorten(key, budget):
    """The `[...]` rule (#272 rule 4): over budget, every modifier collapses into
    the ASCII ELLIPSIS and the final key token stays."""
    return key if len(key) <= budget else ELLIPSIS + "+" + key.rpartition("+")[2]


def _cell_budget(labels, columns, indent=2):
    """The widest key column that still fits `columns` aligned cells of these
    labels into the frame (uniform cell = key + 2 + widest label, 2-cell gaps)."""
    lmax = max(len(a) for a in labels)
    return (INNER - indent - (columns - 1) * 2 - columns * (2 + lmax)) // columns


# The one key-column budget every surface shortens against, derived at import
# from the grid -- the tightest key surface -- so a combo looks the same
# everywhere. 13 today; test-pinned as a derived value, never hardcoded.
KEY_BUDGET = _cell_budget(KEY_LABELS.values(), 2)


def _cell_rows(cells, key_prefix, emit, ansi, columns=2, indent=2):
    """Aligned key cells. cells: [(display_combo, label)] in reading order.
    Under a lead: bare keys; without one: full combos, shortened past
    min(KEY_BUDGET, this box's own geometry budget). One uniform cell width
    (widest shown key + 2 + widest label), 2-cell gaps -- the geometry that puts
    the shipped letter grid's anchors at 2/24/46 and yields the two-column combo
    budget, both derived, both test-pinned. On a shortened key only the key token
    is bold (`[...]` is a renderer artifact, not part of the key)."""
    labels = [a for _, a in cells]
    if key_prefix is not None:
        keys = [_bare(c, key_prefix) for c, _ in cells]
    else:
        budget = min(KEY_BUDGET, _cell_budget(labels, columns, indent))
        keys = [_shorten(c, budget) for c, _ in cells]
    wk = max(len(k) for k in keys)
    lmax = max(len(a) for a in labels)
    rows = []
    shown = list(zip(keys, labels))
    for i in range(0, len(shown), columns):
        row = shown[i:i + columns]
        segs = [(" " * indent, ())]
        for j, (k, a) in enumerate(row):
            if k.startswith(ELLIPSIS + "+"):
                segs.append((ELLIPSIS + "+", ()))
                segs.append((k[len(ELLIPSIS) + 1:], (BOLD,)))
            else:
                segs.append((k, (BOLD,)))
            tail = " " * (wk - len(k)) + "  " + a
            if j < len(row) - 1:                      # no pad behind the last cell
                tail += " " * (lmax - len(a)) + "  "  # label pad + 2-cell gap
            segs.append((tail, ()))
        rows.append(emit(segs, ansi))
    return rows


def _flow_line(lead, cells, emit, ansi):
    """The #115 key line: `lead`, then `key word` cells three spaces apart,
    unaligned; only the key tokens bold. cells: [(shown_key, word)]."""
    segs = [(lead, ())]
    for i, (k, w) in enumerate(cells):
        if i:
            segs.append(("   ", ()))               # 3-space cell separator
        segs.append((k, (BOLD,)))
        segs.append((" " + w, ()))
    return emit(segs, ansi)


def _flow_width(lead, cells):
    """Visible width of a _flow_line body (frame borders excluded) -- the
    measurement behind the flow-line-or-cells switch on the strips."""
    return len(lead) + sum(len(k) + 1 + len(w) for k, w in cells) + 3 * (len(cells) - 1)


def _keys_grid_lines(pairs, key_prefix, ansi):
    """KEYS grid rows. pairs: [(action_name, display_combo)] in canonical
    (DEFAULT_HOTKEYS) order; labels looked up by name. Three columns while a lead
    is set and every bare key fits the three-column budget (one cell with the
    shipped letters -- anchors 2/24/46 fall out of the geometry); otherwise two
    columns -- bare keys under the lead, full combos with `[...]` without one."""
    cells = [(c, KEY_LABELS[n]) for n, c in pairs]
    columns = 2
    if key_prefix is not None and \
            max(len(_bare(c, key_prefix)) for c, _ in cells) \
            <= _cell_budget(KEY_LABELS.values(), 3):
        columns = 3
    return _cell_rows(cells, key_prefix, dline, ansi, columns=columns)


def _key_lines(pairs, emit, ansi, split=None):
    """One key list (#272 rules 3/4): the #115 flow line under a shared lead while
    it fits the frame, aligned cells with full combos otherwise. pairs:
    [(action_name, display_combo)] in the order the list reads, words from
    KEY_WORDS. `split` breaks the flow form after that many cells and puts the
    rest on a continuation line indented to the lead's width (the REC strip's 3/2
    reading rhythm) -- the helper knows only "break after n cells", nothing about
    that strip; None keeps the list on one line. EVERY flow line is measured, so a
    split whose second line would not fit falls back together with the first. The
    lead is derived from exactly these combos, so a key can never be shown bare
    without the lead that anchors it (D-019); the cell form is also the honest
    fallback for a lead that would not fit."""
    cells = [(c, KEY_WORDS[n]) for n, c in pairs]
    prefix = _display_prefix([c for _, c in pairs])
    if prefix is not None:
        lead = f"  {prefix} +  "                          # 14 cols for "Ctrl+Alt"
        flow = [(_bare(c, prefix), w) for c, w in cells]
        rows = ([(lead, flow)] if split is None
                else [(lead, flow[:split]), (" " * len(lead), flow[split:])])
        if max(_flow_width(ld, fl) for ld, fl in rows) <= INNER:
            return [_flow_line(ld, fl, emit, ansi) for ld, fl in rows]
    return _cell_rows(cells, None, emit, ansi)


def _footer_lines(model_label, footer, emit, ansi):
    """The action footer (#115): the model on its own line, then the box's key
    list. footer: [(action_name, display_combo)] in the #115 footer order of
    FOOTER_ACTIONS above -- this module owns the order and the words, the app
    supplies the combos. `emit` is dline or sline (double vs single frame)."""
    return [emit([("  model: ", ()), (model_label, (BOLD,))], ansi),
            *_key_lines(footer, emit, ansi)]


def _footer_key(footer, name):
    """The display combo the footer carries for `name`, or None -- the one way a
    panel names a key it also lists (D-019: no fallback key in the renderer)."""
    return next((c for n, c in footer if n == name), None)


def _tag_headline(lamp_and_tag, tag_codes, rest, ansi):
    """Panel headline with a leading space, a styled ``██ TAG`` (or ``TAG``) and
    an unstyled remainder."""
    return dline([(" ", ()), (lamp_and_tag, tag_codes), (rest, ())], ansi)


# =====================================================================
# Masthead / READY (Screen 1 / 2)
# =====================================================================
def render_masthead(lineup, keys, history_path,
                    switch_key, start_key,
                    guidance=None, with_wordmark=True, logo_lines=None,
                    pinned_default=None, *, ansi):
    """`keys`: the twelve (action_name, display_combo) pairs in canonical order;
    the shared lead of exactly those combos heads the KEYS zone and its grid
    (D-019, #276). `switch_key` and `start_key` are full display combos -- the
    form the switched panels have always taken."""
    lines = [dtop(ansi)]
    if with_wordmark:
        lines.extend(_masthead_wordmark(logo_lines, ansi))
        lines.append(dsep(ansi))
    lines.append(dline([("  ", ()), ("READY", (BOLD, GREEN)),
                        (f" -- press {start_key} and start talking", ())], ansi))
    # #115: one framed spacer before each zone header + the history edge. Gated on
    # with_wordmark so the terse re-display stays tight.
    if with_wordmark:
        lines.append(dline("", ansi))                    # spacer before MODEL
    lines.append(dzone([("MODEL", (BOLD,)), (f"  switch: {switch_key}", ())], ansi))
    lines.extend(_lineup_lines(lineup, ansi, pinned_default))
    if guidance:
        lines.extend(_guidance_lines(guidance, ansi))
    if with_wordmark:
        lines.append(dline("", ansi))                    # spacer before KEYS
    key_prefix = _display_prefix([c for _, c in keys])
    # #276: with a shared lead the header anchors the bare keys right below it
    # (the open plus is deliberate); a mixed scheme keeps a plain header over the
    # full combos.
    head = [("KEYS", (BOLD,))]
    if key_prefix is not None:
        head.append((f"  {key_prefix} +", ()))
    lines.append(dzone(head, ansi))
    lines.extend(_keys_grid_lines(keys, key_prefix, ansi))
    if with_wordmark:
        lines.append(dline("", ansi))                    # spacer before History edge
    budget = 63 - len("History: ")                       # #115: no open hint here
    path = truncate_path_middle(history_path, budget)
    lines.append(dedge([(f"History: {path}", (DIM,))], ansi))
    return lines


def _masthead_wordmark(logo_lines, ansi):
    if not ansi:
        # Plain twin: the 3-row wordmark collapses to one centered ASCII line
        # (wordmark + tagline); any mark drops with it (no # cluster).
        wm = WM_PLAIN + "  " + TAGLINE
        pad = (INNER - len(wm)) // 2
        return [dline(" " * pad + wm, ansi)]
    # ANSI: mark + wordmark carry the brand ACCENT (indent/gap stay unstyled, so
    # the accent spans match the mockup); width math is unaffected (SGR only).
    rows = []
    if logo_lines:
        markw = max(len(r) for r in logo_lines)
        gap = 4
        indent = max(0, (INNER - (markw + gap + len(WM[0]))) // 2)
        wm_offset = indent + markw + gap                 # 16 for the a5 mark
        mark = [r.ljust(markw) for r in logo_lines]
        # pad the mark block to 3 rows so it aligns beside the wordmark
        while len(mark) < 3:
            mark.append(" " * markw)
        for i in range(3):
            rows.append(dline([(" " * indent, ()), (mark[i], (ACCENT,)),
                               (" " * gap, ()), (WM[i], (ACCENT,))], ansi))
    else:
        wm_offset = 11
        for i in range(3):
            rows.append(dline([(" " * 11, ()), (WM[i], (ACCENT,))], ansi))
    # tagline centered under the 47-col wordmark block (derived from wm_offset,
    # not hardcoded), never accented
    rows.append(dline(" " * (wm_offset + (len(WM[0]) - len(TAGLINE)) // 2) + TAGLINE, ansi))
    return rows


def _guidance_lines(text, ansi):
    """#200 keyless shop-window hint under the lineup: a calm YELLOW instruction
    (never red), 2-space indent, wrapped to the frame. Composed by the app (so
    it stays #55-override-aware); console_ui only styles and wraps it."""
    body = _wrap(text, INNER - 2, 2)
    return ([dline([("  ", ()), (body[0], (YELLOW,))], ansi)]
            + [dline([(cont, (YELLOW,))], ansi) for cont in body[1:]])


# =====================================================================
# Strips (routine)
# =====================================================================
def _strip_top(ansi):
    """REC/OK/... strip top border, optionally carrying the ACTIVE_STRIP_HEADER
    name and its bullet glyph. Both survive the plain twin (the name is ASCII,
    the bullet degrades 1:1 to "o" via PLAIN), so the frame stays aligned."""
    name = ACTIVE_STRIP_HEADER
    if not name:
        return stop_(ansi)
    glyph = STRIP_HEADER_GLYPH if ACTIVE_LOGO_MARK is not None else ""
    label = (glyph + " " if glyph else "") + name
    head = "┌── " + label + " "
    line = head + "─" * (W - 1 - len(head)) + "┐"
    return line if ansi else line.translate(PLAIN)


def _strip_open(ansi):
    """Opening lines of a full-frame strip: the top border, plus one headroom
    line when the border carries the ACTIVE_STRIP_HEADER name -- so the name in
    the border does not crowd the first content row."""
    top = _strip_top(ansi)
    return [top, sline("", ansi)] if ACTIVE_STRIP_HEADER else [top]


def _strip_row1_seq(left_segs, seq, chars):
    """Append the right-anchored seq/chars block (col 41) to a strip row."""
    if chars is None:
        return left_segs
    left_visible = sum(len(t) for t, _ in left_segs)
    right = (f"seq {seq}    " if seq is not None else "") + f"{chars} chars"
    pad = max(1, SEQCOL - left_visible)
    return left_segs + [(" " * pad + right, ())]


def render_rec_strip(stops, *, ansi):
    """stops: the five (action_name, display_combo) stop pairs in canonical order
    (paste, paste+Enter, keep only, type, cancel). The #115 key list in a 3/2
    split -- the strip's own reading rhythm, hence an argument to _key_lines and
    not a second copy of the flow-or-cells rule. The cell fallback is unreachable
    with canonical combos and today's words (widest case: 67 of 68 cells) -- it
    guards copy growth, and the honest form when it does trigger is the no-lead
    one."""
    return [
        *_strip_open(ansi),
        sline([("  ", ()), ("REC", (BOLD, YELLOW)), ("  recording...", ())], ansi),
        *_key_lines(stops, sline, ansi, split=3),
        sbot(ansi),
    ]


def render_ok_strip(seq, chars, sent, model_label, footer, *, mode=None,
                    cap=None, ansi):
    if mode == 'typing':
        # Typed insert (keyboard.write) -- length-capped (#7). Show the ceiling
        # beside the char count; a *truncated* one goes to render_typed_capped, so
        # here chars <= cap. No seq block (the cap annotation takes that room).
        what = "typed at the cursor + sent" if sent else "typed at the cursor"
        annot = f"{chars:,} chars (max {cap:,})"
        left = [("  ", ()), ("OK", (BOLD, GREEN)), (f"  {what}", ())]
        left_vis = sum(len(t) for t, _ in left)
        pad = max(1, INNER - left_vis - len(annot))
        return [
            *_strip_open(ansi),
            sline(left + [(" " * pad + annot, ())], ansi),
            *_footer_lines(model_label, footer, sline, ansi),
            sbot(ansi),
        ]
    what = "inserted at the cursor + sent" if sent else "inserted at the cursor"
    row1 = _strip_row1_seq([("  ", ()), ("OK", (BOLD, GREEN)), (f"  {what}", ())],
                           seq, chars)
    return [
        *_strip_open(ansi),
        sline(row1, ansi),
        *_footer_lines(model_label, footer, sline, ansi),
        sbot(ansi),
    ]


def render_typed_capped(cap, original_chars, paste_key, model_label, footer,
                        *, ansi):
    """A typed insert that hit the #7 length cap. Benign success-with-notice, never
    red: the text WAS inserted (capped), the full transcript is kept in history and
    re-insertable via the clipboard hotkey -- `paste_key` named in full, as prose
    always is (D-019). Yellow CAPPED tag -- it is a successful insert with a
    heads-up, not a failure."""
    head = truncate_end(
        f"typed insert limited to {cap:,} chars (of {original_chars:,})", INNER - 10)
    hint = truncate_end(
        f"full text kept in history -- {paste_key} pastes all of it", INNER - 4)
    return [
        *_strip_open(ansi),
        sline([("  ", ()), ("CAPPED", (BOLD, YELLOW)), (f"  {head}", ())], ansi),
        sline([("  " + hint, ())], ansi),
        *_footer_lines(model_label, footer, sline, ansi),
        sbot(ansi),
    ]


def render_waiting_strip(seq, chars, stops, *, ansi):
    """stops: the two (action_name, display_combo) insert pairs in canonical
    order -- paste before type, the default route before the fallback."""
    row1 = _strip_row1_seq([("  ", ()), ("WAITING", (BOLD, GREEN)),
                            ("  kept -- not inserted yet", ())], seq, chars)
    return [
        *_strip_open(ansi),
        sline(row1, ansi),
        *_key_lines(stops, sline, ansi),
        sbot(ansi),
    ]


def render_cancelled_strip(*, ansi):
    return [
        *_strip_open(ansi),
        sline([("  ", ()), ("CANCELLED", (BOLD,)),
               ("  recording discarded -- nothing saved", ())], ansi),
        sbot(ansi),
    ]


def render_saved_strip(duration, retry_key, *, ansi):
    dur = f"{duration:.0f}s"
    return [
        *_strip_open(ansi),
        sline([("  ", ()), ("SAVED", (BOLD, YELLOW)),
               (f"  the recording was still running -- audio saved ({dur})", ())], ansi),
        # Budget = INNER minus the indent, so the widest legal combo (22 cells)
        # still reads in full: the guard is against copy growth (#277), not a
        # frame break any configuration can reach.
        sline("  " + truncate_end(
            f"next start: press {retry_key} to transcribe & insert it", INNER - 2), ansi),
        sbot(ansi),
    ]


# =====================================================================
# Failure / recovery panels
# =====================================================================
def _failed_top(tag, rest, ansi, tag_codes=(BOLD, RED)):
    return _tag_headline(LAMP + " " + tag, tag_codes, "  " + rest, ansi)


# #159: FAILED reason -> (what happened, the one realistic next step). {P} = the
# short provider token ("Soniox"/"Groq"), never the long display name (width).
# CYAN, never ACCENT (masthead-exclusive). The "inconclusive" row is the Soniox
# Live → async file lane ending empty with >=1 errored stage -- shown regardless of
# the reason category. reason=None (uncategorized failure) omits the block.
_REASON_LINES = {
    "no-connection": ("Couldn't reach the {P} server", "Is the Wi-Fi down again?"),
    "service-error": ('The {P} server just says "error"',
                      "Retry, wait, ...investigate -- or just switch the model"),
    "rate-limited": ("{P} is catching its breath (rate limit)",
                     "Too many requests -- give it a minute, then retry"),
    "auth": ("{P} turned down the API key",
             "Typo in Settings? Key no longer active at {P}?"),
    "no-credit": ("The {P} account is out of credit",
                  "Add a little credit in the {P} console"),
    "inconclusive": ("The recording came back empty",
                     "Might be silence, might be a hiccup -- worth a retry"),
}


def _reason_pair(reason, provider, inconclusive):
    """The (line1, line2) explanation for a FAILED reason, provider-filled, or None
    for an uncategorized failure (reason=None) -- then the panel omits the block.
    error_inconclusive wins over the category (the Soniox Live empty-lane case)."""
    pair = _REASON_LINES.get("inconclusive" if inconclusive else reason)
    if pair is None:
        return None
    p = provider or "Soniox"
    return pair[0].replace("{P}", p), pair[1].replace("{P}", p)


def render_transcription_failed(seq, model_label, footer,
                                *, reason=None, provider=None,
                                inconclusive=False, ansi):
    """`footer`: the RETRY footer -- both keys this panel names in prose, retry
    and switch, are read out of it by name; there is no fallback key in the
    renderer (D-019). The app hands `_footer_keys(retry=True)` here, and the
    ladder holds it to that (#290)."""
    seq_part = f" (seq {seq})" if (seq is not None and seq >= 0) else ""
    pair = _reason_pair(reason, provider, inconclusive)
    is_auth = reason == "auth" and not inconclusive
    is_credits = reason == "no-credit" and not inconclusive   # #179
    # Prose names the full combo (D-019): a bare letter in a sentence reads as
    # "type R". The lead below anchors the footer's own key list only, so Ctrl+Alt
    # may well appear here and once more down there. Both keys come out of the
    # footer by name; the switch one absent, the parenthetical is simply omitted.
    retry_key = _footer_key(footer, "retry_last_failed")
    switch_key = _footer_key(footer, "switch_api")
    lines = [
        dtop(ansi),
        _failed_top("FAILED", f"transcription failed{seq_part} -- nothing inserted", ansi),
    ]
    if pair:
        l1 = truncate_end(pair[0], INNER - 4)
        l2 = truncate_end(pair[1], INNER - 4)
        lines.append(dline([("  " + l1, (BOLD, CYAN))], ansi))          # what: left
        pad = INNER - 2 - len(l2)                                       # next step: right
        lines.append(dline([(" " * max(1, pad) + l2, (CYAN,))], ansi))
        lines.append(dline("", ansi))
    lines.append(dzone([("WHAT NOW", (BOLD,))], ansi))
    if is_auth:
        lines.append(dline([("  fix the key, then restart -- the recording will wait for you", (BOLD,))], ansi))
        lines.append(dline([('  set the key in Settings (Start menu "Thoughtborne Settings")', (DIM,))], ansi))
    elif is_credits:
        p = provider or "Soniox"
        lines.append(dline([("  " + truncate_end(
            f"top up, then press {retry_key} -- it will wait for you", INNER - 4), (BOLD,))], ansi))
        lines.append(dline([(f"  add a little credit in the {p} console", (DIM,))], ansi))
    else:
        lines.append(dline([("  " + truncate_end(
            f"press {retry_key} to retry this recording", INNER - 4), (BOLD,))], ansi))
        if switch_key:
            lines.append(dline("  " + truncate_end(
                f"or switch the model ({switch_key}), then retry", INNER - 4), ansi))
    lines += [dsep(ansi), *_footer_lines(model_label, footer, dline, ansi), dbot(ansi)]
    return lines


def render_insert_failed(seq, stops, model_label, footer, *, ansi):
    """stops: the two (action_name, display_combo) insert pairs in canonical order
    -- paste before type, the default route before the fallback, as on the WAITING
    strip. The two keys are alternatives offered side by side, i.e. a key list and
    not prose: two full combos in one sentence do not fit the frame. The
    introducing words take their own line so they survive both forms."""
    seq_part = f" (seq {seq})" if (seq is not None and seq >= 0) else ""
    return [
        dtop(ansi),
        _failed_top("FAILED", f"could not insert{seq_part} -- the transcript is kept", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  insert the last transcript with:", ansi),
        *_key_lines(stops, dline, ansi),
        dsep(ansi),
        *_footer_lines(model_label, footer, dline, ansi),
        dbot(ansi),
    ]


def render_selftest_failed(reason, action_lines, *, ansi):
    if isinstance(action_lines, str):
        action_lines = (action_lines,)
    lines = [
        dtop(ansi),
        _failed_top("FAILED", reason, ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
    ]
    lines += [dline("  " + a, ansi) for a in action_lines]
    lines.append(dbot(ansi))
    return lines


def render_device_loss(duration, model_label, footer, *, ansi):
    """`footer`: the RETRY footer -- the WHAT-NOW sentence names the retry combo
    it also lists, read out of the footer by name (D-019, #290)."""
    dur = f"{duration:.0f}s"
    retry_key = _footer_key(footer, "retry_last_failed")
    return [
        dtop(ansi),
        _failed_top("FAILED", "microphone lost -- recording ended early", ansi),
        dline(f"    audio saved ({dur}, not transcribed)", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  " + truncate_end(
            f"reconnect the microphone, then press {retry_key} to transcribe it",
            INNER - 4), ansi),
        dsep(ansi),
        *_footer_lines(model_label, footer, dline, ansi),
        dbot(ansi),
    ]


def render_mic_failed(model_label, footer, *, ansi):
    """The audio stream could not be opened on Ctrl+Alt+W (on_start_recording's
    `if not self.audio_recorder.start_recording():` branch in thoughtborne.py):
    no input device, or Windows denied microphone access. Replaces the two red log
    lines with a panel (#179). Red like render_device_loss (an audio FAILED where
    the hotkeys still work, so the footer is honest), but non-retry -- nothing was
    captured; the fix is external, then record again -- the footer's `record`
    entry names the key."""
    return [
        dtop(ansi),
        _failed_top("FAILED", "the microphone could not be opened", ansi),
        dline("    nothing was recorded", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  Is a microphone connected and set as the input device?", ansi),
        dline("  Windows: Settings > Privacy > Microphone must allow apps", ansi),
        dsep(ansi),
        *_footer_lines(model_label, footer, dline, ansi),
        dbot(ansi),
    ]


def render_hotkeys_failed(*, ansi):
    return [
        dtop(ansi),
        _failed_top("FAILED", "hotkeys could not be registered", ansi),
        dline("    the tool cannot react to key presses", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  close any other running Thoughtborne instance, then restart", ansi),
        dbot(ansi),
    ]


def render_hotkeys_partial(registered, expected, *, ansi):
    """Some -- not all -- hotkeys registered (#166 honest verdict). A foreign app
    likely owns one combo. NOT red: the tool runs and most keys work, so this is a
    yellow advisory, not the total-loss FAILED panel (which stays red for 0/N)."""
    head = f"{registered} of {expected} hotkeys registered"
    return [
        dtop(ansi),
        _tag_headline(LAMP + " SOME KEYS INACTIVE", (BOLD, YELLOW), "  " + head, ansi),
        dline("    another app likely owns one combo -- see the log for which", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  close the other app, or rebind it in Settings, then restart", ansi),
        dbot(ansi),
    ]


def render_already_running(*, ansi):
    """A second start found an instance already holding the hotkeys (#166). Calm,
    never red -- nothing is broken; the tool already runs elsewhere and this start
    closes itself. The wedged-instance hint (a first instance not answering the
    exit hotkey, #128) is a required DIM line so the user knows the escape hatch.
    Final wording is a later #160 voice concern; structure + non-red is the point."""
    return [
        dtop(ansi),
        dline([("  ", ()), ("ALREADY RUNNING", (BOLD, CYAN))], ansi),
        dline("", ansi),
        dline("  Thoughtborne is up in another window -- that one has the keys.", ansi),
        dline("  Nothing broken here; this window just isn't needed.", ansi),
        dline("", ansi),
        dline([("  (that window not responding? end it there, or via Task Manager)", (DIM,))], ansi),
        dline([("  closing ...", (DIM,))], ansi),
        dbot(ansi),
    ]


def render_switch_failed(current_label, lineup, switch_key, missing=None,
                         *, ansi):
    """`missing`: the env-var names of the skipped entries (see switch_api). When
    present, the panel names them so the console user keeps the actionable info
    the file-only skip lines carry (#44/#109)."""
    miss_line = [("  missing: ", ()), (", ".join(missing), (BOLD,))] if missing else None
    lines = [
        dtop(ansi),
        _failed_top("FAILED", "no other API available", ansi),
        dline(f"    staying on {current_label}", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  add the missing key(s) to .env (see README), then restart", ansi),
    ]
    if miss_line:
        lines.append(dline(miss_line, ansi))
    lines.append(dzone([("MODEL", (BOLD,)), (f"  switch: {switch_key}", ())], ansi))
    lines.extend(_lineup_lines(lineup, ansi))
    lines.append(dbot(ansi))
    return lines


def render_switched_panel(new_label, lineup, switch_key, *, ansi):
    return [
        dtop(ansi),
        dline([("  ", ()), ("SWITCHED", (BOLD, CYAN)),
               ("  now transcribing with: ", ()), (new_label, (BOLD,))], ansi),
        dzone([("MODEL", (BOLD,)), (f"  switch: {switch_key}", ())], ansi),
        *_lineup_lines(lineup, ansi),
        dbot(ansi),
    ]


def render_recovered_panel(when, duration, clean_exit, hotkeys_ok,
                           audio_path, retry_key, *, ansi):
    dur = f"{duration:.0f}s"
    cause = "saved but not transcribed" if clean_exit else "rescued after a hard kill"
    head = f"a recording was {cause}"
    detail = f"from {when} ({dur})"

    lines = [
        dtop(ansi),
        _tag_headline(LAMP + " RECOVERED", (BOLD, YELLOW), "  " + head, ansi),
        dline("    " + detail, ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
    ]
    if hotkeys_ok:
        lines.append(dline("  " + truncate_end(
            f"press {retry_key} to transcribe & insert it", INNER - 4), ansi))
    else:
        lines.append(dline("  the audio is safe in the audio folder", ansi))
        lines.append(dline("  " + truncate_end(
            f"once hotkeys work, press {retry_key} to transcribe & insert it", INNER - 4), ansi))
    edge = truncate_path_middle(audio_path, 56)
    lines.append(dedge([(f"audio: {edge}", (DIM,))], ansi))
    return lines


def render_no_speech(open_key, *, ansi):
    """A recording that transcribed to empty on every engine held no speech (#133).
    A deliberately calm yellow panel -- benign, not an error: no red, no WHAT-NOW
    zone, no retry hotkey (a retry cannot help, and the audio is kept in history).
    Two hints (#159): the mic may have delivered silence while you spoke (a real
    field case), and the archived file is one key away for a listen -- `open_key`
    is the open-history hotkey (respects #55 overrides), the panel's sole Ctrl+Alt.
    Mirrors the RECOVERED/SAVED yellow-lamp voice without offering an action."""
    head = "no speech found in this recording"
    detail = "the audio is kept in history -- a retry cannot help"
    return [
        dtop(ansi),
        _tag_headline(LAMP + " NO SPEECH", (BOLD, YELLOW), "  " + head, ansi),
        dline("    " + detail, ansi),
        dline("    You were talking? Then the mic sent silence -- check input", ansi),
        dline("    " + truncate_end(  # guard a long #55 open_history override (#159)
            f"to be sure, give the file a listen: {open_key} opens history", INNER - 6), ansi),
        dbot(ansi),
    ]


def render_keyless_notice(settings_key, *, ansi):
    """A dictation / self-test / switch / retry hotkey was pressed while no API
    key is configured (#200 shop-window). Calm YELLOW, never red -- nothing is
    broken; the tool just needs a key first. `settings_key` is the live
    open-settings combo (respects #55 overrides), echoing the masthead's yellow
    guidance line. Reuses the SETUP NEEDED tag of the no-API panel: same
    situation (a key is needed), same calm colour."""
    step = f"enter an API key in Settings ({settings_key}), then restart"
    return [
        dtop(ansi),
        _tag_headline(LAMP + " SETUP NEEDED", (BOLD, YELLOW),
                      "  no API key yet -- dictation is off", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  " + truncate_end(step, INNER - 4), ansi),   # truncate guards a long #55 combo
        dbot(ansi),
    ]


def render_noapi_panel(missing, other_failures, env_dir, *, ansi):
    """No constructible API at startup. Tim's call (#109): yellow SETUP NEEDED,
    numbered steps, never red -- a missing first-run key is a setup step, not an
    error. `missing`: [(env_var, [api_slots])]; `other_failures`: [(slot, reason)]."""
    zone = "PROBLEMS" if other_failures else "MISSING"
    lines = [
        dtop(ansi),
        _tag_headline(LAMP + " SETUP NEEDED", (BOLD, YELLOW),
                      "  Thoughtborne cannot transcribe yet", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  Thoughtborne turns speech into text using an online service.", ansi),
        dline("  It needs one service key -- a one-time setup:", ansi),
        dline("  1. Get a key -- .env.example lists where to sign up:", ansi),
        dline("       GROQ_API_KEY    - free, no payment details needed", ansi),
        dline("       SONIOX_API_KEY  - prepaid, best German accuracy", ansi),
        dline("  2. Enter it in Thoughtborne Settings (Start menu), or", ansi),
        dline("     copy .env.example to .env and paste the key there", ansi),
        dline("  3. Start Thoughtborne again (double-click Thoughtborne.bat)", ansi),
        dzone([(zone, (BOLD,))], ansi),
    ]
    lines += _noapi_zone_lines(missing, other_failures, ansi)
    edge = truncate_path_middle(env_dir, 63 - len("folder: "))
    lines.append(dedge([(f"folder: {edge}", (DIM,))], ansi))
    return lines


def _noapi_zone_lines(missing, other_failures, ansi):
    lines = []
    varw = max((len(v) for v, _ in missing), default=0)   # align the "(needed" column
    for env_var, slots in missing:
        joined = ", ".join(slots)
        name = env_var.ljust(varw)
        if other_failures:
            text = f"  {name} missing  (needed for: {joined})"
        else:
            text = f"  {name}  (needed for: {joined})"
        lines.append(dline([(text, (BOLD,))], ansi))
    for slot, reason in other_failures:
        lines.append(dline("  " + truncate_end(f"{slot} failed: {reason}", INNER - 4), ansi))
    return lines
