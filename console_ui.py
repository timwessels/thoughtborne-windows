"""Console renderer for the Thoughtborne "Cockpit" console design (#109).

Pure presentation layer: every function takes plain data plus the two runtime
switches (`ansi`, `compact`) and returns a list of ready-to-print lines. The
module imports nothing from the project -- all dynamic values (labels, hotkey
letters, paths, seq/chars) arrive as parameters -- so it renders and is width-
verified without a running Windows tool (see test_console_ui.py).

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
COMPACT_MAX = 46  # design guideline for the frameless compact form
COMPACT_THRESHOLD = 72  # terminal columns below this -> compact form (N5)

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
WM_COMPACT = "▐█ THOUGHTBORNE █▌"             # 18 cols
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

# ---- KEYS grid copy (design order matches the 11 letters the app supplies) --
KEY_ACTIONS = [
    "start recording", "stop, type text", "stop, paste",
    "stop, paste+Enter", "stop, keep only", "cancel recording",
    "retry last failed", "switch model", "open history",
    "self-test", "quit",
]

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


def cline(segs, ansi):
    """Frameless compact line: styled, translated to ASCII in plain mode, no pad."""
    segs = _segs(segs)
    text = "".join(_sgr(t, c, ansi) for t, c in segs)
    return text if ansi else text.translate(PLAIN)


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


def _wrap(text, width, indent, first=None):
    """Greedy word wrap; continuation lines get `indent` spaces. `first` caps the
    first line separately (for a tag prefix). Returns the line bodies (indent
    already applied to lines 1..n)."""
    first = width if first is None else first
    words = text.split()
    lines, cur = [], ""
    for w in words:
        limit = first if not lines else width
        cand = w if not cur else cur + " " + w
        if len(cand) <= limit or not cur:
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
def _lineup_lines(lineup, ansi):
    """MODEL lineup rows. lineup: list of (label, descriptor, is_current,
    is_default). The current row is bold; (default) is dim."""
    width = max((len(lbl) for lbl, *_ in lineup), default=0)
    rows = []
    for label, descriptor, is_current, is_default in lineup:
        marker = ">" if is_current else " "
        prefix = "  " + marker + " "
        main = prefix + label.ljust(width) + " - " + descriptor
        codes = (BOLD,) if is_current else ()
        if is_default:
            main = prefix + label.ljust(width) + " - " + descriptor.ljust(24)
            rows.append(dline([(main, codes), ("(default)", (DIM,))], ansi))
        else:
            rows.append(dline([(main, codes)], ansi))
    return rows


def _keys_grid_lines(keys, key_prefix, ansi):
    """KEYS grid rows. keys: 11 key-letter strings in KEY_ACTIONS order. Anchors
    at columns 2/24/46 for single-letter keys under a shared prefix; degrades to
    a full-combo list when there is no common prefix."""
    if key_prefix is None:
        # No shared modifier prefix -- list full combos, one per line.
        return [dline([("  ", ()), (k, (BOLD,)), ("  " + a, ())], ansi)
                for k, a in zip(keys, KEY_ACTIONS)]
    cells = [(k, a) for k, a in zip(keys, KEY_ACTIONS)]
    rows = []
    for i in range(0, len(cells), 3):
        row = cells[i:i + 3]
        segs = [("  ", ())]
        for j, (k, a) in enumerate(row):
            text = k + "  " + a
            pad = (22 - len(text)) if j < len(row) - 1 else 0
            segs.append((k, (BOLD,)))
            segs.append(("  " + a + " " * max(0, pad), ()))
        rows.append(dline(segs, ansi))
    return rows


def _footer_lines(model_label, keyhints, key_prefix, emit, ansi):
    """Two-line action footer (#115): model on its own line, then one key line
    with a single `Ctrl+Alt + ` lead (no per-key modifier repetition). `emit` is
    dline or sline (double vs single frame). key_prefix None -> bare 2-space lead
    (documented edge; the shipped config never mixes prefixes)."""
    lead = f"  {key_prefix} +  " if key_prefix else "  "
    segs = [(lead, ())]
    for i, (k, w) in enumerate(keyhints):
        if i:
            segs.append(("   ", ()))               # 3-space cell separator
        segs.append((k, (BOLD,)))
        segs.append((" " + w, ()))
    return [emit([("  model: ", ()), (model_label, (BOLD,))], ansi),
            emit(segs, ansi)]


def _tag_headline(lamp_and_tag, tag_codes, rest, ansi):
    """Panel headline with a leading space, a styled ``██ TAG`` (or ``TAG``) and
    an unstyled remainder."""
    return dline([(" ", ()), (lamp_and_tag, tag_codes), (rest, ())], ansi)


# =====================================================================
# Masthead / READY (Screen 1 / 2)
# =====================================================================
def render_masthead(lineup, keys, key_prefix, history_path,
                    open_key, switch_key, start_key,
                    note=None, with_wordmark=True, logo_lines=None,
                    *, ansi, compact):
    if compact:
        return _masthead_compact(lineup, keys, open_key, switch_key,
                                 start_key, note, with_wordmark, ansi)
    lines = [dtop(ansi)]
    if with_wordmark:
        lines.extend(_masthead_wordmark(logo_lines, ansi))
        lines.append(dsep(ansi))
    lines.append(dline([("  ", ()), ("READY", (BOLD, GREEN)),
                        (f" -- press {start_key} and start talking", ())], ansi))
    if note:
        lines.extend(_note_lines(note, ansi))
    # #115: one framed spacer before each zone header + the history edge. Gated on
    # with_wordmark (mirrors _masthead_compact) so the terse re-display stays tight.
    if with_wordmark:
        lines.append(dline("", ansi))                    # spacer before MODEL
    lines.append(dzone([("MODEL", (BOLD,)), (f"  switch: {switch_key}", ())], ansi))
    lines.extend(_lineup_lines(lineup, ansi))
    if with_wordmark:
        lines.append(dline("", ansi))                    # spacer before KEYS
    lines.append(dzone([("KEYS", (BOLD,))], ansi))       # #115: plain, Ctrl+Alt hint dropped
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


def _note_lines(note, ansi):
    """#40 startup fallback note, wrapped under a NOTE tag (8-space hanging
    indent). `note` is the composed reason string."""
    body = _wrap(note, INNER - 8, 8)
    lines = [dline([("  ", ()), ("NOTE", (BOLD, YELLOW)), ("  " + body[0], ())], ansi)]
    for cont in body[1:]:                     # _wrap already applied the 8-space indent
        lines.append(dline([(cont, ())], ansi))
    return lines


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
    the border does not crowd the first content row. Compact strips have no
    header and no headroom."""
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


def render_rec_strip(type_key, paste_key, send_key, keep_key, cancel_key,
                     key_prefix, *, ansi, compact):
    if compact:
        clead = f"{key_prefix} +  " if key_prefix else ""   # 12 cols, no frame indent
        return [
            cline([("REC", (BOLD, YELLOW)), ("  recording...", ())], ansi),
            cline([(clead, ()), (type_key, (BOLD,)), (" type   ", ()),
                   (paste_key, (BOLD,)), (" paste   ", ()),
                   (send_key, (BOLD,)), (" paste+Enter", ())], ansi),
            cline([(" " * len(clead), ()), (keep_key, (BOLD,)), (" keep for later   ", ()),
                   (cancel_key, (BOLD,)), (" cancel", ())], ansi),
        ]
    lead = f"  {key_prefix} +  " if key_prefix else "  "   # 14 cols for "Ctrl+Alt"
    return [
        *_strip_open(ansi),
        sline([("  ", ()), ("REC", (BOLD, YELLOW)), ("  recording...", ())], ansi),
        sline([(lead, ()),
               (type_key, (BOLD,)), (" type   ", ()),
               (paste_key, (BOLD,)), (" paste   ", ()),
               (send_key, (BOLD,)), (" paste+Enter", ())], ansi),
        sline([(" " * len(lead), ()),
               (keep_key, (BOLD,)), (" keep for later   ", ()),
               (cancel_key, (BOLD,)), (" cancel", ())], ansi),
        sbot(ansi),
    ]


def render_ok_strip(seq, chars, sent, model_label, footer_keys, key_prefix,
                    *, ansi, compact):
    what = "inserted at the cursor + sent" if sent else "inserted at the cursor"
    if compact:
        seq_part = f"seq {seq}, " if (seq is not None) else ""
        tail = "inserted + sent" if sent else "inserted at the cursor"
        return [cline([("OK", (BOLD, GREEN)), (f"  {tail} ({seq_part}{chars} chars)", ())], ansi)]
    row1 = _strip_row1_seq([("  ", ()), ("OK", (BOLD, GREEN)), (f"  {what}", ())],
                           seq, chars)
    return [
        *_strip_open(ansi),
        sline(row1, ansi),
        *_footer_lines(model_label, footer_keys, key_prefix, sline, ansi),
        sbot(ansi),
    ]


def render_waiting_strip(seq, chars, type_key, paste_key, key_prefix, *, ansi, compact):
    if compact:
        clead = f"{key_prefix} +  " if key_prefix else ""
        return [
            cline([("WAITING", (BOLD, GREEN)), (f"  kept -- not inserted ({chars} chars)", ())], ansi),
            cline([(clead, ()), (type_key, (BOLD,)), (" type text   ", ()),
                   (paste_key, (BOLD,)), (" paste", ())], ansi),
        ]
    row1 = _strip_row1_seq([("  ", ()), ("WAITING", (BOLD, GREEN)),
                            ("  kept -- not inserted yet", ())], seq, chars)
    lead = f"  {key_prefix} +  " if key_prefix else "  "
    return [
        *_strip_open(ansi),
        sline(row1, ansi),
        sline([(lead, ()),
               (type_key, (BOLD,)), (" type text   ", ()),
               (paste_key, (BOLD,)), (" paste", ())], ansi),
        sbot(ansi),
    ]


def render_cancelled_strip(*, ansi, compact):
    if compact:
        return [cline([("CANCELLED", (BOLD,)), ("  recording discarded", ())], ansi)]
    return [
        *_strip_open(ansi),
        sline([("  ", ()), ("CANCELLED", (BOLD,)),
               ("  recording discarded -- nothing saved", ())], ansi),
        sbot(ansi),
    ]


def render_saved_strip(duration, retry_key, *, ansi, compact):
    dur = f"{duration:.0f}s"
    if compact:
        return [
            cline([("SAVED", (BOLD, YELLOW)), ("  recording was still running", ())], ansi),
            cline([(f"   audio saved ({dur}), not transcribed", ())], ansi),
            cline([(f"   next start: {retry_key} transcribes it", ())], ansi),
        ]
    return [
        *_strip_open(ansi),
        sline([("  ", ()), ("SAVED", (BOLD, YELLOW)),
               (f"  the recording was still running -- audio saved ({dur})", ())], ansi),
        sline([(f"  next start: press {retry_key} to transcribe & insert it", ())], ansi),
        sbot(ansi),
    ]


# =====================================================================
# Failure / recovery panels
# =====================================================================
def _failed_top(tag, rest, ansi, tag_codes=(BOLD, RED)):
    return _tag_headline(LAMP + " " + tag, tag_codes, "  " + rest, ansi)


def render_transcription_failed(seq, retry_key, env_dir, model_label,
                                footer_keys, key_prefix, *, ansi, compact):
    seq_part = f" (seq {seq})" if (seq is not None and seq >= 0) else ""
    env = truncate_path_middle(env_dir, INNER - len("  (.env is in )"))
    if compact:
        return [
            cline([(LAMP + " FAILED", (BOLD, RED)),
                   (f"  transcription failed{seq_part}", ())], ansi),
            cline("   nothing was inserted", ansi),
            cline([("WHAT NOW", (BOLD,))], ansi),
            cline(f"  press {retry_key} to retry this recording", ansi),
            cline("  [AUTH] above? fix the key in .env,", ansi),
            cline(f"  then restart  (.env: {truncate_path_middle(env_dir, 20)})", ansi),
            cline([("model: ", ()), (model_label, (BOLD,))], ansi),
        ]
    # Framed: the footer key line carries the sole Ctrl+Alt (its `R retry` anchors
    # the bare retry letter used here) -- one Ctrl+Alt per box (#115).
    retry_letter = retry_key.rpartition('+')[2]
    return [
        dtop(ansi),
        _failed_top("FAILED", f"transcription failed{seq_part} -- nothing inserted", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline([(f"  press {retry_letter} to retry this recording", (BOLD,))], ansi),
        dline("  if an [AUTH] line shows above: fix the key in .env, restart", ansi),
        dline([(f"  (.env is in {env})", (DIM,))], ansi),
        dsep(ansi),
        *_footer_lines(model_label, footer_keys, key_prefix, dline, ansi),
        dbot(ansi),
    ]


def render_insert_failed(seq, type_key, paste_key, model_label, footer_keys,
                         key_prefix, *, ansi, compact):
    seq_part = f" (seq {seq})" if (seq is not None and seq >= 0) else ""
    if compact:
        clead = f"{key_prefix} +  " if key_prefix else ""
        return [
            cline([(LAMP + " FAILED", (BOLD, RED)),
                   (f"  could not insert{seq_part}", ())], ansi),
            cline([("   the transcript is kept", ())], ansi),
            cline([("WHAT NOW", (BOLD,))], ansi),
            cline([(clead, ()), (type_key, (BOLD,)), (" type   ", ()),
                   (paste_key, (BOLD,)), (" paste", ())], ansi),
            cline([("model: ", ()), (model_label, (BOLD,))], ansi),
        ]
    return [
        dtop(ansi),
        _failed_top("FAILED", f"could not insert{seq_part} -- the transcript is kept", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline([("  insert the last transcript with ", ()),
               (type_key, (BOLD,)), (" (type) or ", ()),
               (paste_key, (BOLD,)), (" (paste)", ())], ansi),
        dsep(ansi),
        *_footer_lines(model_label, footer_keys, key_prefix, dline, ansi),
        dbot(ansi),
    ]


def render_selftest_failed(reason, action_lines, *, ansi, compact):
    if isinstance(action_lines, str):
        action_lines = (action_lines,)
    if compact:
        head, _, tail = reason.partition(" -- ")
        out = [cline([(LAMP + " FAILED", (BOLD, RED)), ("  " + head, ())], ansi)]
        if tail:
            out.append(cline("   " + tail, ansi))
        out.append(cline([("WHAT NOW", (BOLD,))], ansi))
        out += [cline("  " + a, ansi) for a in action_lines]
        return out
    lines = [
        dtop(ansi),
        _failed_top("FAILED", reason, ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
    ]
    lines += [dline("  " + a, ansi) for a in action_lines]
    lines.append(dbot(ansi))
    return lines


def render_device_loss(duration, retry_key, model_label, footer_keys, key_prefix,
                       *, ansi, compact):
    dur = f"{duration:.0f}s"
    if compact:
        return [
            cline([(LAMP + " FAILED", (BOLD, RED)), ("  microphone lost", ())], ansi),
            cline([(f"   recording ended, audio saved ({dur})", ())], ansi),
            cline([("WHAT NOW", (BOLD,))], ansi),
            cline([(f"  reconnect the mic, then press {retry_key}", ())], ansi),
            cline([("model: ", ()), (model_label, (BOLD,))], ansi),
        ]
    # Framed: the footer key line's `R retry` anchors the bare retry letter here.
    retry_letter = retry_key.rpartition('+')[2]
    return [
        dtop(ansi),
        _failed_top("FAILED", "microphone lost -- recording ended early", ansi),
        dline(f"    audio saved ({dur}, not transcribed)", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline(f"  reconnect your microphone, then press {retry_letter} to transcribe it", ansi),
        dsep(ansi),
        *_footer_lines(model_label, footer_keys, key_prefix, dline, ansi),
        dbot(ansi),
    ]


def render_hotkeys_failed(*, ansi, compact):
    if compact:
        return [
            cline([(LAMP + " FAILED", (BOLD, RED)), ("  hotkeys not registered", ())], ansi),
            cline([("   the tool cannot react to keys", ())], ansi),
            cline([("WHAT NOW", (BOLD,))], ansi),
            cline([("  close any other Thoughtborne, then restart", ())], ansi),
        ]
    return [
        dtop(ansi),
        _failed_top("FAILED", "hotkeys could not be registered", ansi),
        dline("    the tool cannot react to key presses", ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
        dline("  close any other running Thoughtborne instance, then restart", ansi),
        dbot(ansi),
    ]


def render_switch_failed(current_label, lineup, switch_key, missing=None,
                         *, ansi, compact):
    """`missing`: the env-var names of the skipped entries (see switch_api). When
    present, the panel names them so the console user keeps the actionable info
    the file-only skip lines carry (#44/#109)."""
    miss_line = [("  missing: ", ()), (", ".join(missing), (BOLD,))] if missing else None
    if compact:
        out = [
            cline([(LAMP + " FAILED", (BOLD, RED)), ("  no other API available", ())], ansi),
            cline(f"   staying on {current_label}", ansi),
            cline("  add the missing key(s) to .env, then restart", ansi),
        ]
        if miss_line:
            out.append(cline(miss_line, ansi))
        return out
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


def render_switched_panel(new_label, lineup, switch_key, *, ansi, compact):
    if compact:
        return [
            cline([("SWITCHED", (BOLD, CYAN)), ("  now transcribing with:", ())], ansi),
            cline([("   ", ()), (new_label, (BOLD,))], ansi),
            cline([("MODEL", (BOLD,)), (f"  switch: {switch_key}", ())], ansi),
            *_compact_lineup(lineup, ansi),
        ]
    return [
        dtop(ansi),
        dline([("  ", ()), ("SWITCHED", (BOLD, CYAN)),
               ("  now transcribing with: ", ()), (new_label, (BOLD,))], ansi),
        dzone([("MODEL", (BOLD,)), (f"  switch: {switch_key}", ())], ansi),
        *_lineup_lines(lineup, ansi),
        dbot(ansi),
    ]


def render_recovered_panel(when, duration, clean_exit, hotkeys_ok,
                           audio_path, retry_key, *, ansi, compact):
    dur = f"{duration:.0f}s"
    cause = "saved but not transcribed" if clean_exit else "rescued after a hard kill"
    head = f"a recording was {cause}"
    detail = f"from {when} ({dur})"

    if compact:
        full = f"a recording was {cause} -- {when} ({dur})"
        wrapped = _wrap(full, COMPACT_MAX - 3, 3, first=COMPACT_MAX - len(LAMP) - 12)
        out = [cline([(LAMP + " RECOVERED", (BOLD, YELLOW)), ("  " + wrapped[0], ())], ansi)]
        out += [cline(w, ansi) for w in wrapped[1:]]
        if hotkeys_ok:
            out.append(cline(f"   press {retry_key} to transcribe it", ansi))
        else:
            out.append(cline("   audio is safe in the audio folder", ansi))
            out.append(cline(f"   once hotkeys work, press {retry_key}", ansi))
        return out

    lines = [
        dtop(ansi),
        _tag_headline(LAMP + " RECOVERED", (BOLD, YELLOW), "  " + head, ansi),
        dline("    " + detail, ansi),
        dzone([("WHAT NOW", (BOLD,))], ansi),
    ]
    if hotkeys_ok:
        lines.append(dline(f"  press {retry_key} to transcribe & insert it", ansi))
    else:
        lines.append(dline("  the audio is safe in the audio folder", ansi))
        lines.append(dline(f"  once hotkeys work, press {retry_key} to transcribe & insert it", ansi))
    edge = truncate_path_middle(audio_path, 56)
    lines.append(dedge([(f"audio: {edge}", (DIM,))], ansi))
    return lines


def render_noapi_panel(missing, other_failures, env_dir, *, ansi, compact):
    """No constructible API at startup. Tim's call (#109): yellow SETUP NEEDED,
    numbered steps, never red -- a missing first-run key is a setup step, not an
    error. `missing`: [(env_var, [api_slots])]; `other_failures`: [(slot, reason)]."""
    zone = "PROBLEMS" if other_failures else "MISSING"
    if compact:
        out = [
            cline([(LAMP + " SETUP NEEDED", (BOLD, YELLOW)), ("  no API key yet", ())], ansi),
            cline([("WHAT NOW", (BOLD,))], ansi),
            cline("  Thoughtborne needs one service key", ansi),
            cline("  (a one-time setup):", ansi),
            cline("  1. .env.example lists where to sign up:", ansi),
            cline("       GROQ_API_KEY    - free", ansi),
            cline("       SONIOX_API_KEY  - prepaid, best German", ansi),
            cline("  2. copy .env.example to .env", ansi),
            cline("  3. paste your key after the = in Notepad", ansi),
            cline("  4. restart Thoughtborne", ansi),
            cline([(zone, (BOLD,))], ansi),
        ]
        out += _noapi_zone_lines(missing, other_failures, ansi, compact=True)
        return out
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
        dline("  2. Copy .env.example and name the copy .env", ansi),
        dline("  3. Open .env in Notepad, paste your key after the = sign", ansi),
        dline("  4. Start Thoughtborne again (double-click Thoughtborne.bat)", ansi),
        dzone([(zone, (BOLD,))], ansi),
    ]
    lines += _noapi_zone_lines(missing, other_failures, ansi, compact=False)
    edge = truncate_path_middle(env_dir, 63 - len("folder: "))
    lines.append(dedge([(f"folder: {edge}", (DIM,))], ansi))
    return lines


def _noapi_zone_lines(missing, other_failures, ansi, compact):
    emit = cline if compact else dline
    lines = []
    varw = max((len(v) for v, _ in missing), default=0)   # align the "(needed" column
    for env_var, slots in missing:
        joined = ", ".join(slots)
        name = env_var if compact else env_var.ljust(varw)
        if other_failures:
            text = (f"  {env_var} missing  ({joined})" if compact
                    else f"  {name} missing  (needed for: {joined})")
        else:
            text = (f"  {env_var}  ({joined})" if compact
                    else f"  {name}  (needed for: {joined})")
        lines.append(emit([(text, (BOLD,))], ansi))
    for slot, reason in other_failures:
        budget = COMPACT_MAX - 2 if compact else INNER - 4
        lines.append(emit("  " + truncate_end(f"{slot} failed: {reason}", budget), ansi))
    return lines


# =====================================================================
# Compact masthead + compact lineup
# =====================================================================
def _compact_lineup(lineup, ansi):
    rows = []
    for label, _desc, is_current, is_default in lineup:
        marker = ">" if is_current else " "
        if is_default:
            rows.append(cline([(f" {marker} ", ()),
                               (label.ljust(21), (BOLD,) if is_current else ()),
                               ("(default)", (DIM,))], ansi))
        else:
            rows.append(cline([(f" {marker} ", ()),
                               (label, (BOLD,) if is_current else ())], ansi))
    return rows


def _compact_keys(keys, ansi):
    cells = list(zip(keys, KEY_ACTIONS))
    rows = []
    for i in range(0, len(cells), 2):
        pair = cells[i:i + 2]
        k0, a0 = pair[0]
        segs = [(" ", ()), (k0, (BOLD,)), ("  " + a0, ())]
        if len(pair) > 1:
            k1, a1 = pair[1]
            pad = 22 - (1 + len(k0) + 2 + len(a0))
            segs.append((" " * max(1, pad), ()))
            segs.append((k1, (BOLD,)))
            segs.append(("  " + a1, ()))
        rows.append(cline(segs, ansi))
    return rows


def _masthead_compact(lineup, keys, open_key, switch_key, start_key,
                      note, with_wordmark, ansi):
    lines = []
    if with_wordmark:
        # ANSI: WM_COMPACT carries the brand ACCENT; plain degrades to WM_PLAIN
        # (its glyphs aren't in the plain table) and is never accented.
        wm_seg = (WM_COMPACT, (ACCENT,)) if ansi else (WM_PLAIN, ())
        lines.append(cline([wm_seg, ("  " + TAGLINE, ())], ansi))
        lines.append("")
    lines.append(cline([("READY", (BOLD, GREEN)),
                        (f" -- press {start_key} and start talking", ())], ansi))
    if note:
        body = _wrap(note, COMPACT_MAX - 6, 6)
        lines.append(cline([("NOTE", (BOLD, YELLOW)), ("  " + body[0], ())], ansi))
        for cont in body[1:]:
            lines.append(cline("  " + cont if not cont.startswith(" ") else cont, ansi))
    if with_wordmark:
        lines.append("")
    lines.append(cline([("MODEL", (BOLD,)), (f"  switch: {switch_key}", ())], ansi))
    lines.extend(_compact_lineup(lineup, ansi))
    if with_wordmark:
        lines.append("")
    lines.append(cline([("KEYS", (BOLD,))], ansi))       # #115: plain, Ctrl+Alt hint dropped
    lines.extend(_compact_keys(keys, ansi))
    if with_wordmark:
        lines.append("")
        lines.append(cline(f"history: press {open_key}", ansi))
    return lines
