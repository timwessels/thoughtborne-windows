"""
ttk theme + design tokens for the settings/onboarding app (#155, D-010).

Pure stdlib (tkinter/ttk/font only), so the D-005 system-Python rescue lane keeps
working. tkinter is imported *lazily*, inside `apply_theme` / `_pick_family` (like
`settings_instance`'s ctypes), so the palette + size constants import even on a
Python that lacks the tk bindings, and the only part that touches tkinter is
applying the theme -- which needs a real Tk root anyway. One entry point,
`apply_theme(root) -> Theme`, which pins the `clam` ttk theme (leaving the native
`vista` theme, D-010), derives the named fonts from an availability-checked family
chain, and configures every ttk style the app uses on two surfaces -- a white page
and a faint blue-grey card. It returns a `Theme` carrying the fonts, the DPI
factor, the `sp()` spacing helper and the measured content-column width.

Why leave the native theme: `vista` draws the notebook pane, buttons, fields and
scrollbar through the Windows UxTheme API, which ignores ttk background styling --
so "white surfaces" under vista yields a *half*-restyled window (white frames,
grey chrome). `clam` is Tk-drawn top to bottom, so the design is exactly what is
specified here, and it renders identically off-Windows, which is what makes the
look verifiable under Xvfb. `messagebox` dialogs, the window title bar and the
combobox popdown stay OS-drawn (hence light, not dark): a dark theme would seam in
exactly the surfaces this module does not control. See D-010.

`TFrame` IS the page surface: `_scrollable_tab` reads `Style().lookup("TFrame",
"background")` for its #180 scroll canvas, so the two must stay in sync -- change
the page colour here and the canvas follows. The palette is the project's own
website palette (`docs/style.css`), so the settings window, the site and the
console read as one product; every text/background pair is WCAG-checked in
`test_settings_theme.py`.
"""
# tkinter is imported lazily inside apply_theme / _pick_family (see the docstring):
# the palette + size constants below must import on a Python without the tk bindings.

# ---- palette: the project's website tokens (docs/style.css), WCAG-checked -----
INK          = "#0E202E"   # --ink/--navy: body text, primary button fill
INK_SOFT     = "#52606A"   # secondary prose
MUTED        = "#626E7A"   # tertiary notes, unselected tab text (>=4.5 on card)
PAGE         = "#FFFFFF"   # the reading surface
CARD         = "#F7F9FB"   # --paper: interactive groups
CARD_HOVER   = "#EEF3F7"
TAB_BG       = "#EAEFF4"   # unselected notebook tab
LINE         = "#DDE1E5"   # --line: decorative hairlines, card borders
CONTROL_LINE = "#7F8C99"   # entry/combobox/button borders (WCAG 1.4.11, >=3:1)
FIELD        = "#FFFFFF"
SEL          = "#D8E4EE"   # text selection
FOCUS        = "#0B5CAB"   # focus ring == link blue (one accent, not two)
WARN_BG      = "#FFF8E5"
WARN_LINE    = "#E8D9A8"
PRIMARY_BG        = INK
PRIMARY_BG_HOVER  = "#1B3546"
PRIMARY_BG_ACTIVE = "#081620"
PRIMARY_FG        = "#FFFFFF"

# App-facing status colours (moved here from thoughtborne_settings.py; the app
# imports these names). TEXT_COLOR was "black" -> INK, for one consistent ink.
LINK_COLOR = "#0B5CAB"
TEXT_COLOR = INK
GREEN      = "#107C10"
RED        = "#C42B1C"
GREY       = "#6D6D6D"
AMBER      = "#8A6D00"

FAMILY_CHAIN = ("Segoe UI", "DejaVu Sans", "Helvetica")   # first available wins
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
    """First family in FAMILY_CHAIN that Tk actually has, else TkDefaultFont's."""
    import tkinter.font as tkfont
    try:
        available = {f.lower() for f in tkfont.families(root)}
    except Exception:
        available = set()
    for fam in FAMILY_CHAIN:
        if fam.lower() in available:
            return fam
    try:
        return tkfont.nametofont("TkDefaultFont").cget("family")
    except Exception:
        return "TkDefaultFont"


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
                       ("TkMenuFont", SIZE_BODY), ("TkHeadingFont", SIZE_H2)):
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
    st.configure("H1.TLabel", font=th.h1_font)
    st.configure("H2.TLabel", font=th.h2_font)
    st.configure("Title.TLabel", font=th.title_font)
    st.configure("Warn.TLabel", background=WARN_BG, foreground=AMBER,
                 padding=(th.sp(10), th.sp(7)), relief="solid", borderwidth=1,
                 bordercolor=WARN_LINE)          # a bordered amber callout strip
    # A borderless amber guidance line (#201): AMBER text on the page, no box -- a
    # calm "next step" note, distinct from Warn.TLabel's bordered "something is
    # broken" callout. AMBER on PAGE (and CARD) is already WCAG-checked in
    # test_settings_theme.py. Inherits background=PAGE + body font from TLabel.
    st.configure("Hint.TLabel", foreground=AMBER)
    # Card twins: only the background differs; foreground/font inherit by dotted
    # style-name fallback (Card.Muted.TLabel -> Muted.TLabel -> TLabel).
    for suffix in ("TLabel", "Muted.TLabel", "Small.TLabel", "H1.TLabel", "H2.TLabel"):
        st.configure("Card." + suffix, background=CARD)

    # ---- notebook (no TNotebook.Client style: the pane is TNotebook's own) ----
    st.configure("TNotebook", background=PAGE, borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure("TNotebook.Tab", background=TAB_BG, foreground=MUTED,
                 bordercolor=LINE, lightcolor=TAB_BG, darkcolor=TAB_BG,
                 padding=(th.sp(14), th.sp(7)), font=th.body_font)
    st.map("TNotebook.Tab",
           background=[("selected", PAGE), ("active", CARD_HOVER)],
           foreground=[("selected", INK)],
           lightcolor=[("selected", PAGE)], darkcolor=[("selected", PAGE)],
           expand=[("selected", (0, 0, 0, 0))])           # no jump on selection

    # ---- buttons: neutral outline + one navy primary (the rail's main action) --
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

    # ---- entry / combobox ----
    st.configure("TEntry", fieldbackground=FIELD, foreground=INK,
                 bordercolor=CONTROL_LINE, lightcolor=CONTROL_LINE, insertcolor=INK,
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
                 indicatorbackground=FIELD, indicatorforeground=INK,
                 upperbordercolor=CONTROL_LINE, lowerbordercolor=CONTROL_LINE,
                 focuscolor=FOCUS, padding=(0, th.sp(3)))
    st.map("TRadiobutton", background=[("active", PAGE)],
           indicatorbackground=[("selected", FIELD), ("active", CARD_HOVER)],
           upperbordercolor=[("selected", INK)], lowerbordercolor=[("selected", INK)])
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
           background=[("pressed", MUTED), ("active", "#C3CCD4")],
           lightcolor=[("pressed", MUTED), ("active", "#C3CCD4")],
           darkcolor=[("pressed", MUTED), ("active", "#C3CCD4")])
    return th
