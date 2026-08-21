"""
ttk theme + design tokens for the settings/onboarding app (#155, D-010).

TERMINAL-STYLE EXPERIMENT (branch settings-terminal-style): the palette flips
from the light website look to the tool's own console face -- a dark blue-black
page, monospace type, the console's light-blue brand accent (`console_ui.ACCENT`,
#59C2FF) and the classic bright terminal status colours. The *mechanics* of
D-010 are untouched: `clam` stays pinned (it is what makes a fully custom look
possible at all -- vista's OS-drawn chrome ignores ttk colours), this module
stays the single source of both surfaces, and every text/background pair is
still WCAG-checked in `test_settings_theme.py`. What this branch deliberately
revisits is D-010's light-over-dark call; the OS-drawn seams it warned about
(title bar, messagebox) are answered by the DWM dark-title-bar attribute in
`thoughtborne_settings` and an accepted light messagebox rarity.

Pure stdlib (tkinter/ttk/font only), so it imports off-Windows -- the tests load it
directly, and the settings app the tool spawns runs on the tool's own venv
interpreter. tkinter is imported *lazily*, inside `apply_theme` / `_pick_family` (like
`settings_instance`'s ctypes), so the palette + size constants import even on a
Python that lacks the tk bindings, and the only part that touches tkinter is
applying the theme -- which needs a real Tk root anyway. One entry point,
`apply_theme(root) -> Theme`, which pins the `clam` ttk theme, derives the named
fonts from an availability-checked family chain, and configures every ttk style
the app uses on two surfaces -- a near-black page and a slightly lifted panel
card. It returns a `Theme` carrying the fonts, the DPI factor, the `sp()`
spacing helper and the measured content-column width.

`TFrame` IS the page surface: `_scrollable_tab` reads `Style().lookup("TFrame",
"background")` for its #180 scroll canvas, so the two must stay in sync -- change
the page colour here and the canvas follows. Colour roles mirror the console
doctrine (#109): red stays reserved for error states, the accent is decorative
plus focus/selection, meaning never rides on colour alone.
"""
# tkinter is imported lazily inside apply_theme / _pick_family (see the docstring):
# the palette + size constants below must import on a Python without the tk bindings.

# ---- palette: the console's terminal face, dark twin of docs/style.css --------
INK          = "#DCE7F0"   # body text: soft white, blue-tinted like the console
INK_SOFT     = "#A9BAC8"   # secondary prose
MUTED        = "#8DA0B0"   # tertiary notes, unselected tab text (>=4.5 on card)
PAGE         = "#0C1117"   # the terminal surface (blue-black, not pure black)
CARD         = "#141B23"   # interactive groups: one step lifted from the page
CARD_HOVER   = "#1B242F"
TAB_BG       = "#10161D"   # unselected notebook tab
LINE         = "#26303B"   # decorative hairlines, card borders
CONTROL_LINE = "#647B8D"   # entry/combobox/button borders (WCAG 1.4.11, >=3:1)
FIELD        = "#090D12"   # input wells sit *below* the page, like a prompt line
SEL          = "#1F4A66"   # text selection
ACCENT       = "#59C2FF"   # the console masthead accent (console_ui.ACCENT)
FOCUS        = ACCENT      # focus ring == accent (one accent, not two)
WARN_BG      = "#241D0C"
WARN_LINE    = "#57451D"
PRIMARY_BG        = ACCENT # the one glowing action: accent-filled, dark text
PRIMARY_BG_HOVER  = "#7CD0FF"
PRIMARY_BG_ACTIVE = "#38A8EC"
PRIMARY_FG        = "#06121C"

# App-facing status colours (the app imports these names). Bright terminal
# variants; red stays error-exclusive per the console doctrine.
LINK_COLOR = ACCENT
TEXT_COLOR = INK
GREEN      = "#3FD68F"
RED        = "#FF7B72"
GREY       = "#93A4B2"
AMBER      = "#E3B341"

# Monospace chain: Cascadia (the Windows Terminal face) -> Consolas (always on
# Windows) -> DejaVu Sans Mono (the Linux/Xvfb render) -> Courier New (floor).
FAMILY_CHAIN = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New")
SIZE_BODY, SIZE_SMALL, SIZE_H2, SIZE_H1, SIZE_TITLE = 10, 9, 11, 13, 15


class Theme:
    """Handed back to the app: fonts, the DPI factor, spacing + column helpers."""

    def __init__(self, root):
        self.root = root
        try:
            self.factor = max(root.winfo_fpixels("1i") / 96.0, 1.0)
        except Exception:
            self.factor = 1.0

    def sp(self, n):
        """A spacing token -> device pixels. pack pady/padx are raw pixels and
        would otherwise stay fixed while the DPI-scaled fonts grow around them."""
        return max(int(round(n * self.factor)), 1)

    def column_px(self):
        """The capped content-column width, measured from the body font, so it
        scales with DPI and font size instead of being a hard-coded pixel value."""
        try:
            return self.body_font.measure("n" * 100)
        except Exception:
            return self.sp(660)


def _pick_family(root):
    """First family in FAMILY_CHAIN that Tk actually has, else TkFixedFont's."""
    import tkinter.font as tkfont
    try:
        available = {f.lower() for f in tkfont.families(root)}
    except Exception:
        available = set()
    for fam in FAMILY_CHAIN:
        if fam.lower() in available:
            return fam
    try:
        return tkfont.nametofont("TkFixedFont").cget("family")
    except Exception:
        return "TkFixedFont"


def apply_theme(root):
    """Pin clam, derive the fonts, configure every style; return the Theme.

    Best-effort where it can realistically fault -- theme selection, the family
    lookup and the named-font reconfigure are each guarded, so a missing family or
    named font degrades rather than raising. The style calls assume a healthy clam
    root (clam is built in, so there is no runtime-loaded theme file to fault, D-005).
    Cheap at startup -- single-digit ms warm, ~10 ms cold -- so the #195 budget is
    untouched."""
    import tkinter as tk
    from tkinter import ttk
    import tkinter.font as tkfont
    th = Theme(root)
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except tk.TclError:
        pass                       # keep whatever theme is active rather than die

    fam = _pick_family(root)
    # Reconfigure the NAMED fonts so every widget that does not ask for a font
    # inherits (ttk and tk alike). Positive sizes are points -> they follow the
    # `tk scaling` call, so DPI stays intact; negative (pixel) sizes would not.
    for name, size in (("TkDefaultFont", SIZE_BODY), ("TkTextFont", SIZE_BODY),
                       ("TkMenuFont", SIZE_BODY), ("TkHeadingFont", SIZE_H2),
                       ("TkFixedFont", SIZE_BODY)):
        try:
            tkfont.nametofont(name).configure(family=fam, size=size)
        except Exception:
            pass
    th.body_font = tkfont.nametofont("TkDefaultFont")

    # These named Font objects live only as long as the returned Theme holds them:
    # tkinter's Font.__del__ deletes the underlying Tk font once its last Python
    # reference drops, so the caller must keep the Theme (the app does, as self.theme).
    def _mk(name, size, weight="normal"):
        return tkfont.Font(root=root, name=name, exists=False,
                           family=fam, size=size, weight=weight)
    th.small_font = _mk("TbSmall", SIZE_SMALL)
    th.h2_font    = _mk("TbH2", SIZE_H2, "bold")
    th.h1_font    = _mk("TbH1", SIZE_H1, "bold")
    th.title_font = _mk("TbTitle", SIZE_TITLE, "bold")
    th.chip_font  = _mk("TbChip", SIZE_BODY, "bold")
    # One shared underlined font for every link (each _link used to build its own).
    th.link_font  = _mk("TbLink", SIZE_BODY)
    th.link_font.configure(underline=True)

    root.configure(background=PAGE)

    # ---- surfaces. TFrame IS the page: the #180 canvas reads its background. ----
    st.configure("TFrame", background=PAGE)
    st.configure("Card.TFrame", background=CARD, relief="solid", borderwidth=1,
                 bordercolor=LINE)
    st.configure("Plain.Card.TFrame", relief="flat", borderwidth=0)  # rows in a card
    st.configure("Header.TFrame", background=PAGE)
    st.configure("Rail.TFrame", background=CARD)
    st.configure("Hair.TFrame", background=LINE)          # 1px rules (height=1 frames)

    # ---- text ----
    st.configure("TLabel", background=PAGE, foreground=INK, font=th.body_font,
                 anchor="w")                              # capped column hugs left
    st.configure("Muted.TLabel", foreground=INK_SOFT)
    st.configure("Small.TLabel", foreground=MUTED, font=th.small_font)
    # Section headings carry the accent -- the settings twin of the console's
    # bright zone labels (`== MODEL ==`); body text stays ink, so the accent
    # remains a heading/focus colour, not a second text colour.
    st.configure("H1.TLabel", font=th.h1_font, foreground=ACCENT)
    st.configure("H2.TLabel", font=th.h2_font)
    st.configure("Title.TLabel", font=th.title_font, foreground=ACCENT)
    st.configure("Warn.TLabel", background=WARN_BG, foreground=AMBER,
                 padding=(th.sp(10), th.sp(7)), relief="solid", borderwidth=1,
                 bordercolor=WARN_LINE)          # a bordered amber callout strip
    # A borderless amber guidance line (#201): AMBER text on the page, no box -- a
    # calm "next step" note, distinct from Warn.TLabel's bordered "something is
    # broken" callout. AMBER on PAGE (and CARD) is WCAG-checked in
    # test_settings_theme.py. Inherits background=PAGE + body font from TLabel.
    st.configure("Hint.TLabel", foreground=AMBER)
    # Card twins: only the background differs; foreground/font inherit by dotted
    # style-name fallback (Card.Muted.TLabel -> Muted.TLabel -> TLabel).
    for suffix in ("TLabel", "Muted.TLabel", "Small.TLabel", "H1.TLabel", "H2.TLabel"):
        st.configure("Card." + suffix, background=CARD)

    # ---- notebook (no TNotebook.Client style: the pane is TNotebook's own) ----
    # Selected tab: page-coloured with the accent name -- the "active window" of a
    # terminal multiplexer; unselected tabs sit dim on the darker strip.
    st.configure("TNotebook", background=PAGE, borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure("TNotebook.Tab", background=TAB_BG, foreground=MUTED,
                 bordercolor=LINE, lightcolor=TAB_BG, darkcolor=TAB_BG,
                 padding=(th.sp(14), th.sp(7)), font=th.body_font)
    st.map("TNotebook.Tab",
           background=[("selected", PAGE), ("active", CARD_HOVER)],
           foreground=[("selected", ACCENT)],
           lightcolor=[("selected", PAGE)], darkcolor=[("selected", PAGE)],
           expand=[("selected", (0, 0, 0, 0))])           # no jump on selection

    # ---- buttons: quiet dark outline + one glowing accent primary --------------
    st.configure("TButton", background=PAGE, foreground=INK, bordercolor=CONTROL_LINE,
                 lightcolor=PAGE, darkcolor=PAGE, focuscolor=FOCUS, relief="solid",
                 borderwidth=1, padding=(th.sp(12), th.sp(5)), font=th.body_font,
                 anchor="center")
    st.map("TButton",
           background=[("pressed", LINE), ("active", CARD_HOVER), ("disabled", CARD)],
           foreground=[("disabled", MUTED)],
           bordercolor=[("focus", FOCUS)],
           lightcolor=[("pressed", LINE), ("active", CARD_HOVER)],
           darkcolor=[("pressed", LINE), ("active", CARD_HOVER)])
    st.configure("Primary.TButton", background=PRIMARY_BG, foreground=PRIMARY_FG,
                 bordercolor=PRIMARY_BG, lightcolor=PRIMARY_BG, darkcolor=PRIMARY_BG,
                 focuscolor=PRIMARY_FG)
    st.map("Primary.TButton",
           background=[("pressed", PRIMARY_BG_ACTIVE), ("active", PRIMARY_BG_HOVER),
                       ("disabled", LINE)],
           lightcolor=[("pressed", PRIMARY_BG_ACTIVE), ("active", PRIMARY_BG_HOVER)],
           darkcolor=[("pressed", PRIMARY_BG_ACTIVE), ("active", PRIMARY_BG_HOVER)],
           bordercolor=[("pressed", PRIMARY_BG_ACTIVE), ("active", PRIMARY_BG_HOVER)])
    st.configure("Card.TButton", background=CARD, lightcolor=CARD, darkcolor=CARD)

    # ---- entry / combobox: prompt-line wells, accent caret + focus border ------
    st.configure("TEntry", fieldbackground=FIELD, foreground=INK,
                 bordercolor=CONTROL_LINE, lightcolor=CONTROL_LINE, insertcolor=ACCENT,
                 padding=(th.sp(7), th.sp(5)), selectbackground=SEL,
                 selectforeground=INK)
    st.map("TEntry", bordercolor=[("focus", FOCUS)], lightcolor=[("focus", FOCUS)])
    st.configure("TCombobox", fieldbackground=FIELD, foreground=INK, background=PAGE,
                 bordercolor=CONTROL_LINE, lightcolor=CONTROL_LINE, arrowcolor=INK_SOFT,
                 arrowsize=th.sp(16), padding=(th.sp(7), th.sp(4)))  # wider arrow, nearer native
    st.map("TCombobox",
           fieldbackground=[("readonly", FIELD), ("disabled", CARD)],
           foreground=[("disabled", MUTED)], arrowcolor=[("disabled", LINE)],
           bordercolor=[("focus", FOCUS)], lightcolor=[("focus", FOCUS)],
           # kill the blue highlight a readonly combobox paints over its own text
           selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", INK)])
    for opt, val in (("background", FIELD), ("foreground", INK),
                     ("selectBackground", SEL), ("selectForeground", INK),
                     ("borderWidth", 0)):
        try:                       # the popdown is a plain tk Listbox -> option db
            root.option_add(f"*TCombobox*Listbox.{opt}", val)
        except Exception:
            pass

    # ---- radio, separator, scrollbar ----
    st.configure("TRadiobutton", background=PAGE, foreground=INK, font=th.body_font,
                 indicatorbackground=FIELD, indicatorforeground=ACCENT,
                 upperbordercolor=CONTROL_LINE, lowerbordercolor=CONTROL_LINE,
                 focuscolor=FOCUS, padding=(0, th.sp(3)))
    st.map("TRadiobutton", background=[("active", PAGE)],
           indicatorbackground=[("selected", FIELD), ("active", CARD_HOVER)],
           upperbordercolor=[("selected", ACCENT)], lowerbordercolor=[("selected", ACCENT)])
    st.configure("Card.TRadiobutton", background=CARD)
    # A disabled engine radio (#201, key-aware control) greys its label to MUTED --
    # a deliberate palette token (D-010 single source), already WCAG-covered; clam's
    # bare disabled foreground would be an off-palette grey. The greyed indicator +
    # non-interactivity carry "disabled" too, so this is not the only signal.
    st.map("Card.TRadiobutton", background=[("active", CARD)],
           foreground=[("disabled", MUTED)])
    st.configure("TSeparator", background=LINE)
    st.configure("Vertical.TScrollbar", background=LINE, troughcolor=PAGE,
                 bordercolor=PAGE, lightcolor=LINE, darkcolor=LINE, arrowcolor=MUTED,
                 arrowsize=th.sp(12), width=th.sp(11))
    st.map("Vertical.TScrollbar",
           background=[("pressed", MUTED), ("active", "#3B4A59")],
           lightcolor=[("pressed", MUTED), ("active", "#3B4A59")],
           darkcolor=[("pressed", MUTED), ("active", "#3B4A59")])
    return th
