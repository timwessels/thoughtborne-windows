"""
Graphical settings + first-run onboarding app for Thoughtborne (#144).

One tkinter window that doubles as the first-run wizard (rail: Back / Next /
"Save & start") and the everyday settings dialog (rail: Save / Cancel) -- the two
modes differ by the `--first-run` CLI flag, or an auto-promote to the wizard when no
API key is stored yet (#163); the tabs (Overview -> Provider -> Hotkeys -> Behavior
-> How you dictate) are identical in both ("one window, one face"). German or
English, switchable in the header.

Pure stdlib: tkinter + ctypes + threading + queue + subprocess + webbrowser. This
module holds NO IO or validation logic of its own -- every file read/write, key
check, hotkey decode/validate/collision check, and preset is a call into the CP1
modules (`settings_io`, `key_check`, `config`, `settings_strings`) plus
`engine_memory` for the #193 last-engine state file and `settings_theme` for the
#155 visual design (all stdlib-only, so the D-005 system-Python rescue lane keeps
working). The app no longer pins the native `vista` ttk theme: `settings_theme`
pins `clam` plus an explicit style module as the first step of `__init__`, so the
window's white surfaces, card sections and type ladder are what this code
specifies rather than what the OS draws (D-010). Tk is not thread-safe, so the "Test key"
round-trip runs on a daemon worker and marshals its result back through a
`queue.Queue` polled by `root.after` -- widgets are only ever touched on the UI
thread.

Every Windows-only call (High-DPI awareness, launching wt.exe / Thoughtborne.bat)
lives inside a function behind try/except, so importing this module can never hard-
crash at load. It is not unit-tested (a display + real Windows are needed -- the
render, the Tk state-bit capture, and the live key check are hands-on, #151); it
must `py_compile` cleanly.

Known capture limits, mirrored from state-144 / the #151 hands-on list (not shown
in the UI): Win-modifier combos cannot be captured (no Tk state bit -- hand-edit
path only), Shift+digit rows depend on Tk keysym behavior, and `TK_STATE_ALT`
(0x20000) plus AltGr-as-Ctrl+Alt need real-Windows confirmation. A combo the
RUNNING tool already holds as a global hotkey cannot be captured here either --
Windows RegisterHotKey consumes that keypress system-wide, so it fires the action
instead of ever reaching the capture widget; capture a free combo, or stop the
tool first.
"""

import argparse
import copy
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser

# Startup-timing instrumentation (#195): stamp the earliest in-process moment -- in
# both clocks -- and read the tool's cross-process spawn stamp BEFORE the heavy imports
# below, so the breakdown written at first paint can tell the pythonw/venv cold start
# (the previously unmeasured part: OS process creation + interpreter/venv start up to
# this first Python line) apart from imports and tkinter construction. Two clocks
# because they answer different questions: perf_counter (_T_MODTOP) drives the local
# phase deltas; a wall clock (_WALL_ENTRY via time.time) is comparable across processes,
# so _WALL_ENTRY - _SPAWN_TS is the spawn->entry cold start and time.time() - _SPAWN_TS
# at first map is the spawn->visible total. All best-effort -- a missing/garbled spawn
# stamp just drops the cross-process deltas.
_T_MODTOP = time.perf_counter()
_WALL_ENTRY = time.time()
try:
    _SPAWN_TS = float(os.environ.get("THOUGHTBORNE_SPAWN_TS", ""))
except (TypeError, ValueError):
    _SPAWN_TS = None

import tkinter as tk
from tkinter import ttk, messagebox

import config
import engine_memory
import key_check
import restart_signal
import settings_instance
import settings_io
import settings_strings as strings
import settings_theme
import settings_visibility
from hotkey_parse import parse_hotkey_lexical
from key_check import KeyStatus

_T_IMPORTS = time.perf_counter()

# ---- colours: one palette, defined in settings_theme (#155, D-010). The status
# colours (glyph + colour + text together; red stays for the rejected key) plus
# the two surfaces the non-ttk widgets (tk.Label links, key chips) sit on. ----
from settings_theme import (LINK_COLOR, TEXT_COLOR, GREEN, RED, GREY, AMBER,
                            PAGE, CARD)

# CreateProcess flag exists only on Windows; 0 is a harmless no-op elsewhere so a
# stray import off-Windows can't fail at module load.
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

_TAB_KEYS = ("welcome.tab", "provider.tab", "hotkeys.tab", "behavior.tab", "done.tab")


def _enable_high_dpi() -> None:
    """Make the app system-DPI aware BEFORE tk.Tk() so text is crisp at 150/200 %
    (plan D1). SetProcessDpiAwareness(1) is SYSTEM DPI aware -- the stable baseline;
    per-monitor-v2 (value 2, crisp when dragged across mixed-DPI monitors) is a noted
    optional upgrade, deliberately not taken here. Prefer the modern shcore call,
    fall back to the legacy user32 one (also system aware); all guarded so a non-
    Windows or old system just runs unscaled."""
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _size_window(root: tk.Tk) -> None:
    """Size the window DPI-robustly and never larger than the screen.

    A fixed physical-pixel geometry does not grow with the DPI-scaled fonts, so at
    150/200 % it both clips content and can overrun a small (1366x768) laptop. Scale
    the base dimensions by the DPI factor (winfo_fpixels('1i')/96, i.e. 1.0 at 100 %),
    then clamp to the screen less a margin for the taskbar/edges, so the window -- and
    its rail buttons -- always stay on-screen and reachable. Content still fitting
    inside the scaled window is a hands-on check (#151); the point here is to not ship
    a guaranteed clip on scaled displays. All wrapped in try/except so a display quirk
    just leaves Tk's own default size."""
    base_w, base_h = 800, 860
    min_w, min_h = 700, 680
    try:
        try:
            factor = max(root.winfo_fpixels("1i") / 96.0, 1.0)
        except Exception:
            factor = 1.0
        max_w = max(root.winfo_screenwidth() - 80, 320)
        max_h = max(root.winfo_screenheight() - 80, 320)
        w = min(int(base_w * factor), max_w)
        h = min(int(base_h * factor), max_h)
        root.geometry(f"{w}x{h}")
        root.minsize(min(int(min_w * factor), max_w), min(int(min_h * factor), max_h))
    except Exception:
        pass


class SettingsApp:
    """The single-window app. Widgets store *semantic* state (a test indicator
    holds a KeyStatus, a hotkey row holds its combo) and render text from it, so a
    mid-test / mid-capture language switch re-renders correctly via render_all()."""

    def __init__(self, root: tk.Tk, first_run: bool):
        self.root = root
        self.first_run = first_run

        # Theme first (#155, D-010): pin clam + our styles/fonts before any widget
        # is built, so every entry point (the app, the render harness, a test) gets
        # the same window. Returns the design tokens the builders use (fonts, the
        # DPI-aware sp() helper); _column_px is the measured cap for the centred
        # content column (§4.4). apply_theme guards the steps that can realistically
        # fault (theme pick, family lookup, font reconfigure). Holding self.theme for
        # the window's whole life is also what keeps its named fonts alive -- tkinter
        # deletes a Font from Tk once its last Python reference drops.
        self.theme = settings_theme.apply_theme(root)
        self._column_px = self.theme.column_px()

        # Re-render registries: simple text-bearing widgets and link widgets.
        self._text_widgets = []     # (widget, string-key)
        self._link_widgets = []     # (widget, text-key, url-key)

        # per-provider widget handles + state
        self._entries = {}
        self._reveal_btns = {}
        self._test_btns = {}
        self._indicators = {}
        self._soniox_balance_note = None   # #179: shown only after a green Soniox test
        self._revealed = {"groq": False, "soniox": False}
        self._test_state = {}       # provider -> None | "testing" | KeyStatus
        # A generation stamp per provider, bumped on every test launch AND on every
        # field edit; a poll result whose stamp no longer matches the current one is
        # stale (its key was edited mid-test) and is discarded, so a green light can
        # never render against a key it wasn't tested against.
        self._test_gen = {"groq": 0, "soniox": 0}
        self._test_queue = {"groq": queue.Queue(), "soniox": queue.Queue()}
        self._combo_labels = {}     # action -> tk.Label
        self._armed = None          # the action currently capturing a keypress

        # Per-tab scroll region (#180): a canvas per notebook page, filled by
        # _scrollable_tab in tab-build order so its index parallels _tab_frames;
        # _active_canvas is the visible tab's canvas (the wheel/key scroll target).
        self._tab_canvases = []
        self._active_canvas = None
        # Bank sub-120 wheel deltas so precision touchpads (which send deltas well
        # under one 120-notch) accumulate into whole scroll steps instead of each
        # truncating to zero (#180).
        self._wheel_accum = 0
        # #203: wraplength for canvas-body labels is set top-down, once per settled
        # canvas width, not per label <Configure> -- each wraplength change forces a
        # full GDI text remeasurement, and binding that to every label's <Configure>
        # cost seconds on Windows (the initial layout steps through several real
        # widths). _wrap_labels is the registry _register_wrap fills; the flag
        # coalesces many width changes in one settle into a single _push_wraps pass.
        self._wrap_labels = []
        self._wrap_push_pending = False

        # Frozen-rail latch (#202): set once _restart_and_relaunch begins its wait, it
        # makes update_rail / _render_rail early-return so a tab change or language
        # toggle during the wait can't re-enable Back or repaint the "Restarting…"
        # label. Never cleared -- every restart path ends in root.destroy().
        self._restarting = False

        # ---- load state from the CP1 IO (the GUI's own live view) --------------
        load_error = None
        warning = None
        try:
            personal, warning = settings_io.read_personal_settings(
                config.SCRIPT_DIR / "personal_settings.json")
        except Exception as e:
            # A locked / non-UTF-8 file: continue with empty state; every save will
            # abort the same way until the user fixes it (nothing is clobbered).
            personal, warning, load_error = {}, None, e

        hk = personal.get("hotkeys")
        hk = hk if isinstance(hk, dict) else {}
        # Start from exactly what the running tool would register (junk tolerated).
        self.hotkeys_state = config.apply_hotkey_overrides(config.DEFAULT_HOTKEYS, hk)[0]

        # The engine field shows the engine the tool would actually START on
        # (#193, D-008): the `defaults.api` pin if the file carries a valid one,
        # else the engine remembered from the last Ctrl+Alt+L switch, else the
        # built-in default. Showing only the pin would name an engine the tool
        # will not open on whenever a memory exists.
        api = None
        dblk = personal.get("defaults")
        if isinstance(dblk, dict):
            api = dblk.get("api")
        self._pinned_api = api if api in config.AVAILABLE_APIS else None
        remembered = engine_memory.read_last_engine(
            engine_memory.state_path(config.SCRIPT_DIR), config.AVAILABLE_APIS)
        # Two-mode engine control (#198): "fixed" means a defaults.api pin is in
        # force (the fixed dropdown), "remember" means start on the engine last
        # switched to with Ctrl+Alt+L (the #193 memory, shown read-only). A valid
        # pin's PRESENCE decides the loaded mode -- a hand-written pin is itself a
        # prior explicit "always start with X" choice (D-008: presence, not
        # difference from the built-in default).
        self._mode_loaded = "fixed" if self._pinned_api is not None else "remember"
        # The fixed-mode dropdown selection. Seeded to the pin, else the
        # remembered/built-in engine, so a later flip to fixed starts on a sensible
        # engine rather than the first carousel slot.
        shown = self._pinned_api or remembered or config.BUILTIN_DEFAULT_API
        self.engine_index = config.AVAILABLE_APIS.index(shown)
        # Save-time engine-selection check (#193): only a field an engine was actually
        # SELECTED in -- by the user, or by #178's preselect below -- is persisted,
        # so merely displaying a remembered engine never promotes it into a pin.
        self._engine_index_loaded = self.engine_index
        # The engine named next to the remember radio: the real memory if one
        # exists, else the built-in default (so the user sees exactly what remember
        # mode will start on before saving). Moved only by the #178 wizard preselect;
        # _loaded is frozen at load so _save can tell whether it actually moved --
        # a move writes the memory (not a pin), the app's sole memory write (D-008).
        self._remember_display_api = remembered or config.BUILTIN_DEFAULT_API
        self._remember_display_loaded_api = self._remember_display_api
        # Whether a real Ctrl+Alt+L memory backs the remember display -- picks the
        # "currently remembered" vs "no switch recorded yet" wording. (A wizard
        # preselect moving the display to a non-default engine also earns the
        # "remembered" wording -- see _render_engine_control -- since it is what the
        # next start will use once saved.)
        self._has_memory = remembered is not None
        # #178 engine preselection: in the first-run wizard, entering a key can
        # preselect the matching startup engine -- until the user picks one
        # explicitly. A pin is itself a prior explicit choice, so start locked when
        # one exists (presence, not difference -- D-008); a fresh wizard without a
        # pin stays unlocked so the key can preselect, and a merely remembered
        # engine does not block it either (a keyless newcomer is who #178 serves).
        self._engine_user_chose = self._pinned_api is not None

        ui = personal.get("ui")
        lang = ui.get("language") if isinstance(ui, dict) else None
        if lang not in ("de", "en"):
            lang = strings.detect_ui_language()
        self.lang = lang

        # Title the window as early as possible (#196, D-009): the focus-existing
        # remedy matches on this exact title, and _build_ui below can take a moment
        # (#178/#180 growth) during which an untitled "tk" window would be unfindable
        # by a repeat launch. render_all() re-sets the same value harmlessly later.
        try:
            self.root.title(strings.t(
                "app.title.firstrun" if self.first_run else "app.title.settings",
                self.lang))
        except tk.TclError:
            pass

        # tk vars (root already exists)
        self.lang_var = tk.StringVar(value=self.lang)
        # The shared engine-mode radio var (#198), seeded to the loaded mode.
        self.mode_var = tk.StringVar(value=self._mode_loaded)
        env = settings_io.read_env(config.SCRIPT_DIR / ".env")
        self.groq_var = tk.StringVar(value=env.get("GROQ_API_KEY", ""))
        self.soniox_var = tk.StringVar(value=env.get("SONIOX_API_KEY", ""))
        # A readable key is already stored iff read_env surfaced one. Used by the
        # pre-save "no key" check and the "Save & start" launch guard: a blank field
        # never clobbers a stored key (settings_io), so an empty field on top of a
        # stored key is NOT keyless. (An unreadable/ANSI .env reads as no keys here;
        # that rarer case is caught downstream -- write_env aborts such a save.)
        self._had_stored_key = settings_io.env_has_key(env)
        # Per-provider stored-key snapshot for the key-aware engine control (#201).
        # The console-side predicate is per-engine (config.engine_has_key), so the
        # engine radios need per-var stored info, not just the _had_stored_key
        # aggregate. "Keyed" per engine = a non-blank live field OR a key stored for
        # the engine's backing .env var; a blank field never clobbers a stored key
        # (settings_io), so both count. This snapshot dict is keyed off API_KEY_ENV;
        # _live_env and settings_io.env_has_key still name the two vars directly, so a
        # third key/engine would extend the snapshot but not the whole pipeline for free.
        self._stored_env = {v: env.get(v, "") for v in set(config.API_KEY_ENV.values())}

        self._build_ui()

        # Field-edit traces added AFTER prefill so the initial prefill isn't seen as a user edit.
        self.groq_var.trace_add("write", lambda *a: self._on_field_edit("groq"))
        self.soniox_var.trace_add("write", lambda *a: self._on_field_edit("soniox"))

        self.render_all()

        if load_error is not None:
            # A read failure at load is NOT a save failure -- use the dedicated
            # load-failure text so the user isn't told "saving failed" before touching
            # anything (the file is untouched; a later save aborts the same way).
            messagebox.showerror(
                strings.t("dlg.loadfail.title", self.lang),
                strings.t("dlg.loadfail.body", self.lang) + "\n\n" + str(load_error))
        elif warning:
            # Text comes from the render registry (warn.corrupt); just make it visible.
            self.warn_strip.pack(side="top", fill="x", padx=12, before=self.notebook)

        root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    # ------------------------------------------------------------------ helpers
    def _reg(self, widget, key):
        """Register a text-bearing widget for language re-render and set it now."""
        self._text_widgets.append((widget, key))
        widget.config(text=strings.t(key, self.lang))
        return widget

    def _register_wrap(self, lbl):
        """Register a wrapping label for the coalesced wrap pass (#203). Every wrapping
        label -- prose, dynamic prose, the hotkey capture/status lines, the corrupt-file
        strip, the wizard subtitle -- goes through one mechanism: its own <Configure>
        stores the label's realized width and schedules a single idle _push_wraps, which
        is the ONLY place wraplength is actually set. Deferring the set is the fix: each
        wraplength change forces a full GDI text remeasurement, and the initial layout
        steps a label through several real widths, so setting wraplength on every
        <Configure> (the pre-fix behaviour) cost seconds of native layout work per open
        on Windows. Keeping wraplength fixed through the storm lets Tk reuse the cached
        text layout, and the push then measures each label once per settled width.

        Trade-off: until the first push runs, wraplength is 0, so a label is one line at
        its natural width. The push runs at idle before Tk redraws, so under X11 the
        window was never painted before wrapping completed (0 of 107 Expose events seen
        early); but paint-before-push is not structurally guaranteed, so a brief
        unwrapped first frame on Windows is possible -- answered by the maintainer's
        hands-on/first-paint measurement, not provable off-Windows."""
        self._wrap_labels.append(lbl)
        lbl.bind("<Configure>", self._on_wrap_configure)

    def _on_wrap_configure(self, event):
        # Store the label's OWN realized width (reliable and per-label -- unlike reading
        # winfo_width later, which can race the body-pin propagation) and coalesce the
        # expensive wraplength set into one idle pass (#203).
        event.widget._pending_wrap_width = event.width
        self._schedule_wrap_push()

    def _schedule_wrap_push(self):
        """Coalesce the many <Configure>s of one settle into a single wrap pass (#203),
        so each label's wraplength (and its GDI remeasurement) is set once per settle,
        not once per intermediate width step."""
        if not self._wrap_push_pending:
            self._wrap_push_pending = True
            try:
                self.root.after_idle(self._push_wraps)
            except Exception:
                self._wrap_push_pending = False

    def _push_wraps(self):
        """The one place wraplength is set (#203): for each registered label, wrap it to
        the width its last <Configure> reported, guarded so an unchanged value skips the
        GDI remeasurement. A label whose geometry has not settled is skipped and picked
        up by its next <Configure>: width <= 1 is a not-yet-sized (hidden) tab, and a
        width above the window bound (below) is a not-yet-pinned label still at its full
        single-line natural width -- writing either would only set a garbage wraplength.
        This is self-correcting: the settled <Configure> always arrives once the body pin
        propagates. Never raises -- a torn-down widget just skips."""
        self._wrap_push_pending = False
        # The upper bound for a settled label width is the window width -- nothing can be
        # wider than the toplevel. A canvas-body label caps below that at _column_px; a
        # root-chrome label (warn strip, wizard subtitle) can span the full width. A
        # stored width above this bound is a not-yet-pinned label still at its full
        # single-line natural width, so skip it (a max() fallback covers an unmapped
        # root whose winfo_width is still 1).
        try:
            cap = max(self._column_px, self.root.winfo_width())
        except tk.TclError:
            cap = self._column_px
        for lbl in self._wrap_labels:
            try:
                w = getattr(lbl, "_pending_wrap_width", None)
                if w is None or w <= 1 or w > cap:
                    continue
                wl = settings_visibility.wrap_length(w)
                if wl != getattr(lbl, "_last_wraplength", None):
                    lbl._last_wraplength = wl
                    lbl.configure(wraplength=wl)
            except tk.TclError:
                pass

    def _prose(self, parent, key, surface="", **kw):
        """A left-justified explainer label that wraps to its own width. `surface`
        picks the style for the surface it sits on -- "" (page), "Muted.",
        "Card.", "Card.Muted.", "Card.Small." ... -- resolved by dotted style-name
        fallback; a caller may still override with an explicit style=."""
        kw.setdefault("style", surface + "TLabel")
        lbl = ttk.Label(parent, justify="left", **kw)
        self._reg(lbl, key)
        self._register_wrap(lbl)
        return lbl

    def _prose_dyn(self, parent, surface="", **kw):
        """A wrapping explainer label that is deliberately NOT registered for
        language re-render -- its text is a format string (e.g. done.loop.body with
        {start}/{stop}), so render_all must not blind-t() it into raw '{start}'. Its
        owner re-renders it by hand from semantic state (the live hotkey combos).
        `surface` picks the style variant exactly as in _prose."""
        kw.setdefault("style", surface + "TLabel")
        lbl = ttk.Label(parent, justify="left", **kw)
        self._register_wrap(lbl)
        return lbl

    def _link(self, parent, text_key, url_key, bg=PAGE):
        """A blue, underlined, hand-cursor label that opens url_key in a browser.
        tk.Label ignores ttk styles, so it takes the shared underlined link font
        and an explicit background for the surface it sits on (#155): on a white
        page a default-grey label would otherwise draw a grey box around the link."""
        lbl = tk.Label(parent, fg=LINK_COLOR, cursor="hand2", background=bg,
                       font=self.theme.link_font)
        self._link_widgets.append((lbl, text_key, url_key))
        lbl.config(text=strings.t(text_key, self.lang))
        lbl.bind("<Button-1>",
                 lambda e, uk=url_key: webbrowser.open(strings.t(uk, self.lang)))
        return lbl

    def _tab_link(self, parent, text_key, tab_key, bg=PAGE):
        """A blue, underlined, hand-cursor label that selects another notebook tab
        (in-app navigation, #197). Unlike _link (which opens a URL) it stays inside
        the window; text re-renders through the standard text registry (via _reg),
        not _link_widgets (that registry is URL-keyed). The target is addressed by
        tab KEY, so a tab reorder can never point the jump at the wrong page. Same
        shared link font + explicit surface background as _link (#155)."""
        lbl = tk.Label(parent, fg=LINK_COLOR, cursor="hand2", background=bg,
                       font=self.theme.link_font)
        self._reg(lbl, text_key)
        lbl.bind("<Button-1>", lambda e, tk_=tab_key: self._goto_tab(tk_))
        return lbl

    def _section(self, parent, heading_key, level="H1", pady=(0, 0)):
        """A section heading (Title/H1/H2 style), registered for language re-render
        and packed left. Replaces the repeated ttk.Label(font=...) + _reg + pack of
        the pre-#155 code; the type ladder now lives entirely in the styles."""
        lbl = ttk.Label(parent, style=level + ".TLabel")
        self._reg(lbl, heading_key)
        lbl.pack(anchor="w", pady=pady)
        return lbl

    def _card(self, parent, heading_key=None):
        """The card pattern (#155): a bordered, faint blue-grey Frame with an
        optional H2 heading. The caller packs children straight into the returned
        frame; every ttk child needs a Card.-surfaced style and every tk child
        (links, chips) an explicit CARD background, since neither inherits it."""
        card = ttk.Frame(parent, style="Card.TFrame",
                         padding=(self.theme.sp(14), self.theme.sp(12)))
        if heading_key:
            h = ttk.Label(card, style="Card.H2.TLabel")
            self._reg(h, heading_key)
            h.pack(anchor="w", pady=(0, self.theme.sp(6)))
        return card

    def _goto_tab(self, tab_key):
        # Select by _TAB_KEYS position, never a magic int; a mistyped/removed key
        # just does nothing rather than crashing the click.
        try:
            self.notebook.select(_TAB_KEYS.index(tab_key))
        except Exception:
            pass

    # -------------------------------------------------------------- UI assembly
    def _build_ui(self):
        # rail first (pinned bottom), then header (top), then notebook (fills).
        self._build_rail()
        self._build_header()
        self.warn_strip = ttk.Label(self.root, style="Warn.TLabel", justify="left")
        # Register it so render_all() re-renders its text on a language toggle; its
        # only text is warn.corrupt, and it stays unpacked (invisible) until a corrupt
        # settings file packs it into view below. #155: wrap dynamically like _prose
        # (the old fixed wraplength=740 was wrong at every scaling != 100 %).
        self._reg(self.warn_strip, "warn.corrupt")
        self._register_wrap(self.warn_strip)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side="top", fill="both", expand=True, padx=8, pady=(2, 0))
        self._tab_frames = [
            # welcome FIRST so its _scrollable_tab appends _tab_canvases[0], keeping
            # that list index-parallel to _tab_frames (#180); Python evaluates the
            # list literal left-to-right, so listing it first guarantees it.
            self._build_welcome_tab(),
            self._build_provider_tab(),
            self._build_hotkeys_tab(),
            self._build_behavior_tab(),
            self._build_done_tab(),
        ]
        for frame, key in zip(self._tab_frames, _TAB_KEYS):
            self.notebook.add(frame, text=strings.t(key, self.lang))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # Wheel + keyboard scrolling for the visible tab (#180). ONE global handler
        # set keyed to self._active_canvas, which _on_tab_changed swaps on tab change --
        # deterministic, and it sidesteps the <Enter>/<Leave> rebind idiom that misfires
        # here (the body's labels are canvas descendants, so pointer-into-content fires
        # <Leave> on the canvas). PageUp/PageDown/Home/End only -- not the arrows, which
        # would hijack the caret in the key Entry fields and the capture labels.
        if self._tab_canvases:
            self._active_canvas = self._tab_canvases[0]
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        for seq in ("<Prior>", "<Next>", "<Home>", "<End>"):
            self.root.bind_all(seq, self._on_scroll_key)

    def _build_header(self):
        header = ttk.Frame(self.root, style="Header.TFrame",
                           padding=(self.theme.sp(20), self.theme.sp(14),
                                    self.theme.sp(20), self.theme.sp(10)))
        header.pack(side="top", fill="x")
        # Only frames take a frame style; a Header.TFrame on the title label would
        # render the frame layout and silently drop the text (#155).
        lang_frame = ttk.Frame(header, style="Header.TFrame")
        lang_frame.pack(side="right", anchor="ne")
        # The two radio labels name themselves and never translate.
        ttk.Radiobutton(lang_frame, text=strings.t("lang.de", "de"), value="de",
                        variable=self.lang_var, command=self._on_lang).pack(side="left")
        ttk.Radiobutton(lang_frame, text=strings.t("lang.en", "en"), value="en",
                        variable=self.lang_var, command=self._on_lang).pack(side="left")
        self.title_lbl = ttk.Label(header, style="Title.TLabel")
        self._reg(self.title_lbl,
                  "welcome.heading" if self.first_run else "app.title.settings")
        self.title_lbl.pack(side="top", anchor="w")
        if self.first_run:
            self._prose(header, "welcome.sub", surface="Muted.").pack(
                side="top", anchor="w", fill="x", pady=(self.theme.sp(4), 0))
        # A 1px hairline under the header so it reads as chrome above the tabs (#155).
        ttk.Frame(self.root, style="Hair.TFrame", height=1).pack(side="top", fill="x")

    def _build_rail(self):
        rail = ttk.Frame(self.root, style="Rail.TFrame",
                         padding=(self.theme.sp(20), self.theme.sp(10),
                                  self.theme.sp(20), self.theme.sp(12)))
        rail.pack(side="bottom", fill="x")
        # A 1px hairline above the rail, mirroring the header's (#155). Packed
        # bottom AFTER the rail so it sits just above it.
        ttk.Frame(self.root, style="Hair.TFrame", height=1).pack(side="bottom", fill="x")
        # The primary action (Save / Next) carries the one filled navy accent; the
        # secondary (Cancel / Back) is a quiet card-surfaced outline button, so the
        # two are no longer visually identical (#155).
        if self.first_run:
            self.next_btn = ttk.Button(rail, style="Primary.TButton",
                                       command=self._on_next)
            self.next_btn.pack(side="right")
            self.back_btn = ttk.Button(rail, style="Card.TButton",
                                       command=self._on_back)
            self.back_btn.pack(side="right", padx=(0, self.theme.sp(8)))
        else:
            self.cancel_btn = ttk.Button(rail, style="Card.TButton",
                                         command=self.root.destroy)
            self.cancel_btn.pack(side="right")
            self.save_btn = ttk.Button(rail, style="Primary.TButton",
                                       command=lambda: self._save(False))
            self.save_btn.pack(side="right", padx=(0, self.theme.sp(8)))
            self.footer_lbl = ttk.Label(rail, style="Card.Small.TLabel")
            self.footer_lbl.pack(side="left")

    def _scrollable_tab(self):
        """A notebook page whose body scrolls vertically when it overflows (#180).

        Returns (outer, body): `outer` is what the caller hands to notebook.add();
        `body` is the padded frame the caller packs into exactly as it used to pack
        into a plain ttk.Frame(self.notebook, padding=12). The header and rail live on
        self.root, OUTSIDE every one of these, so they can never be scrolled away or
        clipped -- only a tab's own content scrolls. Vertical only: the inner body is
        pinned to the canvas width, so fill='x' children and the _prose wrap behave
        exactly as before and nothing ever needs horizontal scrolling."""
        outer = ttk.Frame(self.notebook)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0)
        # Match the themed frame background so the canvas never shows a band behind
        # or below the ttk content. TFrame IS the page surface (#155, D-010): the
        # theme paints it white (PAGE), so this must track the TFrame style or a
        # grey band would show -- settings_theme is the single place they are kept
        # in sync.
        try:
            bg = ttk.Style().lookup("TFrame", "background")
            if bg:
                canvas.configure(background=bg)
        except Exception:
            pass

        vbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas, padding=(self.theme.sp(20), self.theme.sp(16)))
        win = canvas.create_window((0, 0), window=body, anchor="nw")

        canvas.grid(row=0, column=0, sticky="nsew")
        # vbar is grid()/grid_remove()'d on demand by _autohide (starts hidden).
        self._tab_canvases.append(canvas)   # index parallels self._tab_frames

        # #203: all three reconfigure handlers below carry an equal-value guard so each
        # is a no-op once its output is stable and synthesizes no further <Configure>,
        # which stops the reactions (body -> scrollregion -> auto-hide grid -> canvas
        # width -> body width/centre) from re-triggering each other. The guards are
        # necessary but not sufficient: the real cost was measuring text on every
        # wraplength change, which is now decoupled -- wrapping runs through the
        # coalesced _push_wraps (fed by each label's own <Configure>), which sets each
        # label's wraplength once per settle instead of re-measuring at every width
        # step. The auto-hide grid flip is the one width-changing edge, so gating it on
        # a real overflow-state change (_bar_shown) is what keeps the loop from
        # re-triggering; the others gate on their last value.
        def _autohide(lo, hi, c=canvas):
            # Flip the bar's grid ONLY on a real overflow-state change; grid/
            # grid_remove changes the canvas width, so an unconditional call is the
            # storm's main re-trigger. vbar.set stays unconditional (cheap, no
            # geometry side effect).
            want = settings_visibility.scrollbar_should_show(lo, hi)
            if want != getattr(c, "_bar_shown", None):
                c._bar_shown = want
                if want:
                    vbar.grid(row=0, column=1, sticky="ns")
                else:
                    vbar.grid_remove()
            vbar.set(lo, hi)
        canvas.configure(yscrollcommand=_autohide)

        def _on_body_config(event, c=canvas):
            bbox = c.bbox("all")
            # Guard: an empty body returns None; re-set the scrollregion only when the
            # bbox actually moved, so a settled body stops feeding the cascade (#203).
            if bbox and bbox != getattr(c, "_last_scrollregion", None):
                c._last_scrollregion = bbox
                c.configure(scrollregion=bbox)
        body.bind("<Configure>", _on_body_config)

        def _on_canvas_config(event, c=canvas, w=win):
            # Pin the inner body to the canvas width (so fill='x' children get a
            # bounded width and _prose wraps), but cap it at the measured content
            # column and centre it (#155 §4.4): German prose otherwise runs ~110
            # chars wide on a maximised window. At the default window size the cap
            # barely bites. Vertical scrolling is unaffected -- xview stays unwired,
            # so the x-offset never scrolls sideways, and _overflows keys off
            # bbox[3] (bottom y), which the offset does not move. Re-pin only on a
            # real (width, x) change so a settled canvas stops re-wrapping (#203).
            width = min(event.width, self._column_px)
            x = max((event.width - width) // 2, 0)
            if (width, x) != getattr(c, "_last_body_geom", None):
                c._last_body_geom = (width, x)
                c.itemconfigure(w, width=width)
                c.coords(w, x, 0)
                # The body width change re-allocates its fill='x' labels, so each fires
                # its own <Configure> -> the coalesced wrap push runs from there (#203);
                # no push is triggered here, which would read pre-propagation widths.
        canvas.bind("<Configure>", _on_canvas_config)

        return outer, body

    # ---- welcome / overview tab ----
    def _build_welcome_tab(self):
        # The orientation page that opens the app in both modes (#197), rebuilt as a
        # guided onboarding path (#204): after the unchanged intro + live dictation
        # loop, a numbered setup path (1 key, 2 hotkeys, 3 optional startup) leads a
        # fresh user step by step, then the console-is-a-status-display reassurance
        # and the full-README pointer. Assistant-flavoured, never wizard-gated: the
        # numbers give order, every tab stays reachable by its own link. Teasers and
        # pointers only -- the canonical BYOK text stays on the Provider tab and the
        # full loop on the done tab. Built via _scrollable_tab like every other page
        # (mandatory for the index-parallel canvas, #180).
        outer, f = self._scrollable_tab()
        sp = self.theme.sp

        # 1) What it is (one-breath intro) -- the page lead. Unchanged (#204).
        self._section(f, "welcome.intro.heading", level="H1")
        self._prose(f, "welcome.intro.body").pack(fill="x", pady=(sp(2), sp(8)))

        # 2) The dictation loop from the LIVE hotkey state ({start}/{stop}) -- a
        #    shorter teaser than done.loop.body. The label is a format string, so it
        #    is NOT _reg-istered; _render_welcome_page fills it by hand. Unchanged.
        self.welcome_loop_lbl = self._prose_dyn(f)
        self.welcome_loop_lbl.pack(fill="x", pady=(0, sp(2)))
        self._tab_link(f, "welcome.loop.link", "done.tab").pack(anchor="w", pady=(0, sp(8)))

        # 3) The guided setup path -- the new core (#204), directly after the intro.
        #    A keeper separator (navigation is a different kind of content) + the
        #    existing "Set things up" heading, then three numbered step cards. The
        #    numbers live in the heading TEXT ("1 -- ..."), not a step-badge style;
        #    each card ends in a tab-link into the tab that holds the full walkthrough.
        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=(sp(20), sp(16)))
        self._section(f, "welcome.next.heading", level="H2", pady=(0, sp(8)))
        self._build_welcome_step(f, "welcome.step1.heading", "welcome.step1.body",
                                 "welcome.byok.link", "provider.tab")
        self._build_welcome_step(f, "welcome.step2.heading", "welcome.step2.body",
                                 "welcome.link.hotkeys", "hotkeys.tab")
        self._build_welcome_step(f, "welcome.step3.heading", "welcome.step3.body",
                                 "welcome.link.behavior", "behavior.tab")

        # 4) The console is a status display (honest about the failed-start
        #    exception) -- moved below the setup path (#204): reassurance and
        #    orientation, not a setup step.
        self._section(f, "welcome.console.heading", level="H2", pady=(sp(28), 0))
        self._prose(f, "welcome.console.body").pack(fill="x", pady=(sp(2), sp(8)))

        # 5) The full-picture exit, correctly last.
        self._link(f, "welcome.link.readme", "url.readme").pack(
            anchor="w", pady=(sp(12), 0))
        return outer

    def _build_welcome_step(self, parent, heading_key, body_key, link_key, tab_key):
        # One numbered step card of the #204 guided setup path: the card's H2 is the
        # numbered heading ("1 -- ..."), then the body prose, then a tab-link into
        # the tab that carries the full walkthrough. Read-only -- Step 1 only points
        # at the Provider tab; no key is collected here (D-002). Mirrors the stacked
        # card shape of _build_preset_card.
        sp = self.theme.sp
        card = self._card(parent, heading_key)
        card.pack(side="top", fill="x", pady=(0, sp(8)))
        self._prose(card, body_key, surface="Card.").pack(fill="x")
        self._tab_link(card, link_key, tab_key, bg=CARD).pack(
            anchor="w", pady=(sp(6), 0))

    # ---- provider tab ----
    def _build_provider_tab(self):
        outer, f = self._scrollable_tab()
        sp = self.theme.sp
        self._section(f, "provider.keys.heading", level="H1")
        self._prose(f, "provider.keys.body").pack(fill="x", pady=(sp(2), sp(6)))
        self._prose(f, "provider.lanes.body").pack(fill="x", pady=(0, sp(8)))
        # Groq first (the free "try it now" lane), Soniox second (carries default).
        self._build_provider_card(f, "groq", "provider.groq.heading",
                                  "provider.groq.body", "url.groq_keys",
                                  "provider.field.groq")
        self._build_provider_card(f, "soniox", "provider.soniox.heading",
                                  "provider.soniox.body", "url.soniox_console",
                                  "provider.field.soniox")
        self._prose(f, "provider.keep_note", surface="Muted.").pack(fill="x", pady=(sp(6), 0))
        return outer

    def _build_provider_card(self, parent, provider, heading_key, body_key,
                             url_key, field_key):
        sp = self.theme.sp
        card = self._card(parent, heading_key)
        card.pack(fill="x", pady=(0, sp(12)))
        self._prose(card, body_key, surface="Card.").pack(fill="x", pady=(0, sp(6)))
        self._link(card, url_key, url_key, bg=CARD).pack(anchor="w", pady=(0, sp(10)))
        flbl = ttk.Label(card, style="Card.TLabel")
        self._reg(flbl, field_key)
        flbl.pack(anchor="w", pady=(0, sp(3)))
        # A flat, card-surfaced row (no second border) holds the key field + buttons.
        row = ttk.Frame(card, style="Plain.Card.TFrame")
        row.pack(fill="x")
        var = self.groq_var if provider == "groq" else self.soniox_var
        entry = ttk.Entry(row, textvariable=var, show="•")
        entry.pack(side="left", fill="x", expand=True)
        self._entries[provider] = entry
        rbtn = ttk.Button(row, width=10, style="Card.TButton",
                          command=lambda p=provider: self._toggle_reveal(p))
        rbtn.pack(side="left", padx=(sp(6), 0))
        self._reveal_btns[provider] = rbtn
        tbtn = ttk.Button(row, style="Card.TButton",
                          command=lambda p=provider: self._test_key(p))
        self._reg(tbtn, "btn.test_key")
        tbtn.pack(side="left", padx=(sp(6), 0))
        self._test_btns[provider] = tbtn
        ind = ttk.Label(card, style="Card.TLabel")
        ind.pack(anchor="w", pady=(sp(6), 0))
        self._indicators[provider] = ind
        # #179: a Soniox-only reminder under the card that a green test proves the
        # key, not the account balance. Toggled from _render_indicator by the test
        # verdict; not _reg-istered (its text is state-driven, re-rendered via
        # render_all() -> _render_indicator on a language switch).
        if provider == "soniox":
            self._soniox_balance_note = self._prose_dyn(card, surface="Card.Small.")
            self._soniox_balance_note.pack(anchor="w", pady=(sp(3), 0))

    def _toggle_reveal(self, provider):
        revealed = not self._revealed[provider]
        self._revealed[provider] = revealed
        self._entries[provider].config(show="" if revealed else "•")
        self._render_reveal_btn(provider)

    def _render_reveal_btn(self, provider):
        key = "provider.reveal.hide" if self._revealed[provider] else "provider.reveal.show"
        self._reveal_btns[provider].config(text=strings.t(key, self.lang))

    # ---- provider "Test key" (off the UI thread) ----
    def _on_field_edit(self, provider):
        # Editing a field voids any pending/shown verdict: bump the generation so an
        # in-flight test's result is discarded when it lands, reset the indicator to
        # idle, and re-enable the test button (so a mid-test edit can't leave it stuck
        # disabled).
        self._test_gen[provider] += 1
        self._test_state[provider] = None
        self._test_btns[provider].config(state="normal")
        self._render_indicator(provider)
        self._maybe_preselect_engine()   # #178: key-driven startup-engine preselect
        self._render_engine_control()    # #201: live grey/un-grey + guidance as keys change

    def _test_key(self, provider):
        var = self.groq_var if provider == "groq" else self.soniox_var
        key = var.get()
        # Stamp this launch; the worker tags its result with the same stamp so the
        # poll can tell it apart from a run a later edit/launch has superseded.
        self._test_gen[provider] += 1
        gen = self._test_gen[provider]
        self._test_btns[provider].config(state="disabled")
        self._test_state[provider] = "testing"
        self._render_indicator(provider)
        checker = (key_check.check_groq_key if provider == "groq"
                   else key_check.check_soniox_key)
        q = self._test_queue[provider]

        def work():
            try:
                result = checker(key)
            except Exception:
                # key_check never raises by contract; belt-and-braces so a stray
                # failure degrades to UNREACHABLE instead of a dead worker.
                result = key_check.KeyResult(KeyStatus.UNREACHABLE, "error")
            q.put((gen, result))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(100, lambda p=provider: self._poll_test(p))

    def _poll_test(self, provider):
        try:
            gen, result = self._test_queue[provider].get_nowait()
        except queue.Empty:
            self.root.after(100, lambda p=provider: self._poll_test(p))
            return
        if gen != self._test_gen[provider]:
            # A field edit (or a newer test launch) superseded this run: its verdict
            # is against a key that is no longer in the field -- discard it. Whoever
            # superseded it already owns the button/indicator state.
            return
        self._test_state[provider] = result.status
        self._test_btns[provider].config(state="normal")
        self._render_indicator(provider)

    def _render_indicator(self, provider):
        lbl = self._indicators[provider]
        state = self._test_state.get(provider)
        # #179: the Soniox balance reminder rides on a VALID verdict only -- cleared
        # for idle / testing / rejected / unreachable. Runs before the early returns
        # so a field edit (state -> None) also clears a previously shown note.
        if provider == "soniox" and self._soniox_balance_note is not None:
            self._soniox_balance_note.config(
                text=strings.t("test.valid.soniox_balance", self.lang)
                if state == KeyStatus.VALID else "")
        if state is None:
            lbl.config(text="", foreground=GREY)
            return
        if state == "testing":
            lbl.config(text=strings.t("test.testing", self.lang), foreground=GREY)
            return
        # glyph + color + text together (never color alone -- accessibility).
        table = {
            KeyStatus.VALID: ("✓", GREEN, "test.valid"),
            KeyStatus.INVALID: ("✗", RED, "test.invalid"),
            KeyStatus.UNREACHABLE: ("●", GREY, "test.unreachable"),
        }
        glyph, color, key = table[state]
        lbl.config(text=f"{glyph}  {strings.t(key, self.lang)}", foreground=color)

    # ---- hotkeys tab ----
    def _build_hotkeys_tab(self):
        outer, f = self._scrollable_tab()
        sp = self.theme.sp
        self._prose(f, "hotkeys.intro").pack(fill="x")

        # Presets: a heading (the tab used to jump straight from prose into the
        # boxes) then the two cards STACKED (#155 §4.4 -- side by side inside the
        # capped column squeezed the F-key card's text and its button until they
        # clipped). Stacked, both texts read normally and each button sits under
        # its own card.
        self._section(f, "hotkeys.presets.heading", level="H2", pady=(sp(20), sp(8)))
        prow = ttk.Frame(f)
        prow.pack(fill="x")
        self._build_preset_card(prow, "ctrl_alt", "hotkeys.preset.ctrl_alt.title",
                                "hotkeys.preset.ctrl_alt.body")
        self._build_preset_card(prow, "fkeys", "hotkeys.preset.fkeys.title",
                                "hotkeys.preset.fkeys.body",
                                caveat_key="hotkeys.preset.fkeys.caveat")

        self._section(f, "hotkeys.custom.heading", level="H2", pady=(sp(28), 0))
        self._prose(f, "hotkeys.custom.body").pack(fill="x", pady=(sp(2), sp(4)))

        # A dynamic advisory (#178): a combo the running tool already holds can't be
        # captured here. Rendered from hotkeys_state so the {exit_key} hint tracks a
        # rebind of exit_program (its own attribute -- never overwrite capture_lbl /
        # status_lbl below).
        self.capture_limit_lbl = self._prose_dyn(f, surface="Muted.")
        self.capture_limit_lbl.pack(fill="x", pady=(0, sp(8)))

        grid = ttk.Frame(f)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        # A named-column header row (#155): the three columns were unlabelled. It
        # is grid row 0, so the action rows below start at row 1.
        ha = ttk.Label(grid, style="Small.TLabel")
        self._reg(ha, "hotkeys.col.action")
        ha.grid(row=0, column=0, sticky="w", padx=(0, sp(8)), pady=(0, sp(3)))
        hc = ttk.Label(grid, style="Small.TLabel")
        self._reg(hc, "hotkeys.col.combo")
        hc.grid(row=0, column=1, sticky="w", padx=sp(8), pady=(0, sp(3)))
        for r, name in enumerate(config.DEFAULT_HOTKEYS, start=1):
            al = ttk.Label(grid)
            self._reg(al, f"action.{name}")
            al.grid(row=r, column=0, sticky="w", padx=(0, sp(8)), pady=sp(3))
            # A key chip: kept a tk.Label (it carries dynamic colour + focus), given
            # a bordered, card-tinted box and the bold chip font. The box is set once
            # here and never touched again; _render_combo_label only swaps text +
            # foreground + font (armed drops to the regular weight), so the chip look
            # survives every re-render and the armed/LINK_COLOR state still works.
            cl = tk.Label(grid, anchor="w", foreground=TEXT_COLOR, takefocus=True,
                          background=CARD, relief="solid", borderwidth=1,
                          padx=sp(8), pady=sp(2), font=self.theme.chip_font)
            cl.grid(row=r, column=1, sticky="w", padx=sp(8), pady=sp(3))
            cl.bind("<KeyPress>", lambda e, n=name: self._on_capture_key(e, n))
            cl.bind("<Escape>", lambda e, n=name: self._on_capture_escape(e, n))
            cl.bind("<FocusOut>", lambda e, n=name: self._on_capture_focusout(e, n))
            self._combo_labels[name] = cl
            cb = ttk.Button(grid, command=lambda n=name: self._arm(n))
            self._reg(cb, "btn.change_key")
            cb.grid(row=r, column=2, sticky="e", pady=sp(3))

        # capture_lbl carries the amber capture feedback (unbindable / need-modifier /
        # invalid / collision), status_lbl the hotkey warnings -- the longest single
        # lines on the tab. Wrap them to their own width like _prose so a long German
        # message reflows instead of running past the capped content column (#155).
        self.capture_lbl = ttk.Label(f, foreground=AMBER, justify="left")
        self.capture_lbl.pack(fill="x", pady=(sp(8), sp(2)))
        self.status_lbl = ttk.Label(f, justify="left")
        self.status_lbl.pack(fill="x")
        for _lbl in (self.capture_lbl, self.status_lbl):
            self._register_wrap(_lbl)
        return outer

    def _build_preset_card(self, parent, which, title_key, body_key, caveat_key=None):
        # Stacked cards (#155): the title is the card's H2, then the body, an
        # optional caveat (the F-key preset's IDE-debug warning, a caveat that now
        # looks like one), then the apply button under its own card.
        sp = self.theme.sp
        card = self._card(parent, title_key)
        card.pack(side="top", fill="x", pady=(0, sp(8)))
        self._prose(card, body_key, surface="Card.").pack(fill="x")
        if caveat_key:
            self._prose(card, caveat_key, surface="Card.Small.").pack(
                fill="x", pady=(sp(6), 0))
        btn = ttk.Button(card, style="Card.TButton",
                         command=lambda w=which: self._apply_preset(w))
        self._reg(btn, "btn.use_preset")
        btn.pack(anchor="w", pady=(sp(10), 0))

    def _apply_preset(self, which):
        self.hotkeys_state = (settings_io.preset_ctrl_alt() if which == "ctrl_alt"
                              else settings_io.preset_fkeys())
        self._disarm()
        self._render_hotkey_grid()
        self._render_hotkey_status()
        self._render_capture_limit()
        self._render_done_page()
        self._render_welcome_page()

    # combo prettifier (display only; storage stays canonical lowercase)
    def _pretty_combo(self, value):
        combos = value if isinstance(value, list) else [value]
        if not combos:
            return ""
        text = self._pretty_one(combos[0])
        if len(combos) > 1:
            text += " " + strings.t("hotkeys.more_suffix", self.lang).format(n=len(combos) - 1)
        return text

    @staticmethod
    def _pretty_one(combo):
        names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
        out = []
        for part in combo.split("+"):
            if part in names:
                out.append(names[part])
            elif len(part) >= 2 and part[0] == "f" and part[1:].isdigit():
                out.append("F" + part[1:])
            elif part == "ü":
                out.append("Ü")
            elif len(part) == 1:
                out.append(part.upper())
            else:
                out.append(part)
        return "+".join(out)

    def _render_combo_label(self, name):
        # A re-render sets only text + foreground + font; the chip's box (card tint,
        # border, padding -- set once at creation) is never touched here, so the chip
        # look survives every re-render and the armed/disarmed switch (#155). Armed
        # drops to the regular body font: the capture prompt is prose, not a key, so
        # the lighter weight both reads right and keeps the longer German prompt
        # inside the capped hotkey grid instead of clipping the chip's right edge.
        lbl = self._combo_labels[name]
        if self._armed == name:
            lbl.config(text=strings.t("capture.prompt", self.lang),
                       foreground=LINK_COLOR, font=self.theme.body_font)
        else:
            lbl.config(text=self._pretty_combo(self.hotkeys_state[name]),
                       foreground=TEXT_COLOR, font=self.theme.chip_font)

    def _render_hotkey_grid(self):
        for name in self._combo_labels:
            self._render_combo_label(name)

    def _hotkey_warnings(self):
        diff = settings_io.hotkeys_diff_vs_default(self.hotkeys_state, config.DEFAULT_HOTKEYS)
        _eff, warns = config.apply_hotkey_overrides(config.DEFAULT_HOTKEYS, diff)
        return warns

    def _render_hotkey_status(self):
        warns = self._hotkey_warnings()
        if not warns:
            self.status_lbl.config(text=strings.t("hotkeys.status.ok", self.lang),
                                   foreground=GREEN)
        else:
            text = strings.t("hotkeys.status.warn_prefix", self.lang) + "\n" + "\n".join(warns)
            self.status_lbl.config(text=text, foreground=AMBER)

    # capture widget interaction
    def _arm(self, name):
        if self._armed is not None and self._armed != name:
            self._disarm()
        self._armed = name
        self.capture_lbl.config(text="")
        self._render_combo_label(name)
        self._combo_labels[name].focus_set()

    def _disarm(self):
        prev = self._armed
        self._armed = None
        if prev is not None and prev in self._combo_labels:
            self._render_combo_label(prev)

    def _on_capture_escape(self, event, name):
        if self._armed == name:
            self.capture_lbl.config(text="")
            self._disarm()
        return "break"

    def _on_capture_focusout(self, event, name):
        # Focus left an armed row (clicked elsewhere): treat like Esc, no save.
        if self._armed == name:
            self._disarm()

    def _on_capture_key(self, event, name):
        if self._armed != name:
            return
        combo = settings_io.decode_key_event(event.state, event.keysym, event.char)
        if combo is None:
            if event.keysym in settings_io._MODIFIER_KEYSYMS:
                return "break"   # a bare modifier is down: stay armed, silently
            self.capture_lbl.config(text=strings.t("capture.unbindable", self.lang))
            return "break"

        # GUI modifier guard: a non-F-key needs Ctrl and/or Alt, else it would
        # globally steal a bare letter/digit from every app. F-keys pass bare.
        parts = combo.split("+")
        key, mods = parts[-1], parts[:-1]
        is_fkey = len(key) >= 2 and key[0] == "f" and key[1:].isdigit()
        if not is_fkey and "ctrl" not in mods and "alt" not in mods:
            self.capture_lbl.config(text=strings.t("capture.need_modifier", self.lang))
            return "break"

        ok, msg = settings_io.validate_combo(combo)
        if not ok:
            self.capture_lbl.config(
                text=strings.t("capture.invalid", self.lang).format(detail=msg))
            return "break"

        # Collision: build the candidate state and let apply_hotkey_overrides --
        # the runtime acceptance authority -- judge it. A list-valued action gets
        # its whole list replaced by [combo] (an explicit, visible user act).
        candidate = copy.deepcopy(self.hotkeys_state)
        candidate[name] = ([combo] if isinstance(config.DEFAULT_HOTKEYS[name], list)
                           else combo)
        diff = settings_io.hotkeys_diff_vs_default(candidate, config.DEFAULT_HOTKEYS)
        _eff, warns = config.apply_hotkey_overrides(config.DEFAULT_HOTKEYS, diff)
        if warns:
            holder = self._collision_holder(name, combo)
            holder_disp = strings.t(f"action.{holder}", self.lang) if holder else combo
            self.capture_lbl.config(
                text=strings.t("capture.collision", self.lang).format(action=holder_disp))
            self._disarm()
            return "break"

        self.hotkeys_state[name] = candidate[name]
        self.capture_lbl.config(text="")
        self._disarm()
        self._render_hotkey_status()
        self._render_capture_limit()
        self._render_done_page()
        self._render_welcome_page()
        return "break"

    def _collision_holder(self, name, combo):
        """The other action whose binding shares combo's canonical form (display
        aid only). apply_hotkey_overrides stays the acceptance authority."""
        try:
            target = parse_hotkey_lexical(combo)
        except Exception:
            return None
        for action, value in self.hotkeys_state.items():
            if action == name:
                continue
            for c in (value if isinstance(value, list) else [value]):
                try:
                    if parse_hotkey_lexical(c) == target:
                        return action
                except Exception:
                    continue
        return None

    # ---- behavior tab ----
    def _build_behavior_tab(self):
        outer, f = self._scrollable_tab()
        sp = self.theme.sp
        self._section(f, "behavior.engine.heading", level="H1")
        self._prose(f, "behavior.engine.body").pack(fill="x", pady=(sp(2), sp(6)))

        # Two-mode engine control (#198): remember-mode (start on the Ctrl+Alt+L
        # memory, shown read-only) vs fixed-mode (a defaults.api pin). A shared
        # StringVar drives the mode radio pair. The fixed picker is a radio per engine
        # (#201, replacing the #198 combobox -- a combobox cannot disable individual
        # rows): all four engines stay visible so the user learns they exist, and one
        # without a key renders greyed + unselectable. #155 keeps the whole control in
        # a card. The control LOGIC is unchanged -- _render_engine_control / _on_mode /
        # _on_engine keep D-008/#198 semantics; only the widget changed. A container
        # holds the card + the all-keyless guidance line, so toggling that line's
        # visibility never reorders it below the tab's later sections.
        ctrl = ttk.Frame(f)
        ctrl.pack(fill="x")
        card = self._card(ctrl)
        card.pack(fill="x")
        remember_rb = ttk.Radiobutton(card, style="Card.TRadiobutton", value="remember",
                                      variable=self.mode_var, command=self._on_mode)
        self._reg(remember_rb, "behavior.engine.mode.remember")
        remember_rb.pack(anchor="w")
        # The read-only engine remember-mode will start on -- a format string
        # ({engine}), so NOT _reg-istered; _render_engine_control fills it by hand.
        self.remember_lbl = self._prose_dyn(card, surface="Card.Muted.")
        self.remember_lbl.pack(fill="x", padx=(sp(24), 0), pady=(0, sp(6)))

        fixed_rb = ttk.Radiobutton(card, style="Card.TRadiobutton", value="fixed",
                                  variable=self.mode_var, command=self._on_mode)
        self._reg(fixed_rb, "behavior.engine.mode.fixed")
        fixed_rb.pack(anchor="w")
        # One radio per engine. The label (engine name + descriptor) is language- and
        # state-dependent, so it is composed in _render_engine_control, not _reg-istered
        # here. engine_var holds the selected engine id; a programmatic .set() fires no
        # command, so RENDERING the selection -- even a greyed keyless pin -- is never a
        # user pick, which is the basis of the D-002 byte-identity of an untouched save.
        self.engine_var = tk.StringVar(value=config.AVAILABLE_APIS[self.engine_index])
        self._engine_radios = {}
        for a in config.AVAILABLE_APIS:
            rb = ttk.Radiobutton(card, style="Card.TRadiobutton", value=a,
                                 variable=self.engine_var, command=self._on_engine)
            rb.pack(anchor="w", padx=(sp(24), 0), pady=(sp(1), 0))
            self._engine_radios[a] = rb

        # #201 all-keyless guidance: a calm, borderless amber line (Hint.TLabel) under
        # the control, shown only when no key is stored and none entered. Built here
        # unpacked; _render_engine_control toggles its visibility off _has_any_key.
        # Via _prose it wraps through the #203 coalesced pass and re-renders on a
        # language switch.
        self.engine_guidance = self._prose(ctrl, "behavior.engine.keyless", surface="Hint.")

        # Pointer to the Soniox recognition vocabulary (#178) -- names only the
        # section + file, so it never depends on wording #177 may add to the example.
        self._section(f, "behavior.vocab.heading", level="H2", pady=(sp(28), 0))
        self._prose(f, "behavior.vocab.body", surface="Muted.").pack(fill="x", pady=(sp(2), 0))

        # The 12-line tray wall is split into three digestible blocks (#155); the
        # third is the honest-limits caveat, so it is muted.
        self._section(f, "behavior.tray.heading", level="H2", pady=(sp(28), 0))
        self._prose(f, "behavior.tray.body").pack(fill="x", pady=(sp(2), sp(4)))
        self._prose(f, "behavior.tray.body2").pack(fill="x", pady=(0, sp(4)))
        self._prose(f, "behavior.tray.body3", surface="Muted.").pack(fill="x", pady=(0, sp(6)))
        tbtn = ttk.Button(f, command=self._open_terminal)
        self._reg(tbtn, "btn.open_terminal")
        tbtn.pack(anchor="w")

        self._section(f, "behavior.admin.heading", level="H2", pady=(sp(28), 0))
        self._prose(f, "behavior.admin.body").pack(fill="x", pady=(sp(2), sp(4)))
        self._link(f, "behavior.admin.link", "url.admin_recipe").pack(anchor="w")
        return outer

    # ---- done / closing tab ----
    def _build_done_tab(self):
        # The closing / reference page (#178): teaches the dictation loop and the
        # live-configured control keys. Built in BOTH modes (a full tab in settings
        # mode too, so the tab count never forks _TAB_KEYS); the heading adapts and
        # the farewell line is first-run only.
        outer, f = self._scrollable_tab()
        sp = self.theme.sp
        self._section(
            f, "done.heading.firstrun" if self.first_run else "done.heading.settings",
            level="H1")

        # The loop sentence is the single most important line in the app, so it sits
        # in a card as the page's subject (#155). Dynamic (format-string) lines --
        # rendered from the live hotkey combos in _render_done_page, so they are NOT
        # registered for blind re-render.
        loop_card = self._card(f)
        loop_card.pack(fill="x", pady=(sp(6), sp(12)))
        self.done_loop_lbl = self._prose_dyn(loop_card, surface="Card.")
        self.done_loop_lbl.pack(fill="x")
        self.done_controls_lbl = self._prose_dyn(f, surface="Muted.")
        self.done_controls_lbl.pack(fill="x", pady=(0, sp(12)))

        # Static reference (both modes): starting the tool by a shortcut key.
        self._prose(f, "done.startkey.body", surface="Muted.").pack(fill="x", pady=(0, sp(12)))

        if self.first_run:                # the farewell belongs to the wizard only
            self._prose(f, "done.threewindow.body", surface="Muted.").pack(fill="x")
        return outer

    def _render_done_page(self):
        # The loop + control keys, from the LIVE hotkey state (never the shipped
        # defaults) -- called wherever hotkeys_state changes so a preset/rebind shows
        # here at once. The {…} placeholders are DE/EN-parity-guarded in the test.
        start = self._pretty_combo(self.hotkeys_state["start_recording"])
        stop = self._pretty_combo(self.hotkeys_state["stop_recording_clipboard"])
        ex = self._pretty_combo(self.hotkeys_state["exit_program"])
        gear = self._pretty_combo(self.hotkeys_state["open_settings"])
        self.done_loop_lbl.config(
            text=strings.t("done.loop.body", self.lang).format(start=start, stop=stop))
        self.done_controls_lbl.config(
            text=strings.t("done.controls.body", self.lang).format(
                exit_key=ex, settings_key=gear))

    def _render_welcome_page(self):
        # The welcome loop teaser from the LIVE hotkey state -- same {start}/{stop}
        # contract as _render_done_page, wired at the same three sites, so a
        # preset/rebind shows here at once. Pin-guarded in the test.
        start = self._pretty_combo(self.hotkeys_state["start_recording"])
        stop = self._pretty_combo(self.hotkeys_state["stop_recording_clipboard"])
        self.welcome_loop_lbl.config(
            text=strings.t("welcome.loop.body", self.lang).format(start=start, stop=stop))

    def _render_capture_limit(self):
        ex = self._pretty_combo(self.hotkeys_state["exit_program"])
        self.capture_limit_lbl.config(
            text=strings.t("hotkeys.capture_limit", self.lang).format(exit_key=ex))

    def _render_engine_control(self):
        # Each engine radio (#201): (re)compose its label + descriptor for the current
        # language, then set its state. Enabled iff fixed-mode AND the engine is keyed
        # (a non-blank field or a stored key for its backing var); otherwise disabled
        # (greyed, unselectable) -- in remember-mode every engine radio is disabled,
        # since the fixed picker is inactive there. Setting engine_var shows the current
        # selection; a programmatic set fires no command, so this never counts as a user
        # pick (D-002): engine_index and _engine_user_chose stay untouched, so an
        # untouched save (even a greyed keyless pin) still writes byte-identically.
        mode_fixed = self.mode_var.get() == "fixed"
        live = self._live_env()
        for a in config.AVAILABLE_APIS:
            rb = self._engine_radios[a]
            rb.config(text=f"{config.API_DISPLAY[a]['label']} — "
                           f"{strings.t('engine.desc.' + a, self.lang)}")
            keyed = settings_io.engine_keyed(a, live, self._stored_env)
            rb.state(["!disabled"] if (mode_fixed and keyed) else ["disabled"])
        self.engine_var.set(config.AVAILABLE_APIS[self.engine_index])

        # All-keyless guidance: shown iff fully keyless (no field, no stored key).
        # _has_any_key is exactly that negation, so it is the single source here too.
        if self._has_any_key():
            self.engine_guidance.pack_forget()
        else:
            self.engine_guidance.pack(fill="x", pady=(self.theme.sp(6), 0))

        # The remember label names the engine remember-mode will start on -- the
        # "currently remembered" wording when a real memory OR a wizard preselect names
        # a specific engine (the display differs from the built-in default), else the
        # "no switch recorded yet -> built-in default" wording (so that wording only
        # ever renders when the shown engine IS the built-in default, keeping it true).
        disp = config.API_DISPLAY[self._remember_display_api]["label"]
        if self._has_memory or self._remember_display_api != config.BUILTIN_DEFAULT_API:
            key = "behavior.engine.remember.current"
        else:
            key = "behavior.engine.remember.none"
        self.remember_lbl.config(text=strings.t(key, self.lang).format(engine=disp))

    def _on_engine(self):
        # A command callback (no event arg): only reachable by clicking an ENABLED
        # radio -- a disabled (keyless, or remember-mode) radio ignores the click, so
        # this can never fire for a keyless engine. Track selection by index into
        # AVAILABLE_APIS. An explicit pick locks out the #178 key-driven preselection
        # for good.
        self.engine_index = config.AVAILABLE_APIS.index(self.engine_var.get())
        self._engine_user_chose = True

    def _on_mode(self):
        # An explicit mode choice is explicit engagement -> stop the #178 key-driven
        # preselect from moving the remembered engine under the user, then re-render
        # so the dropdown enables/disables and the remember label reflects the mode.
        self._engine_user_chose = True
        self._render_engine_control()

    def _maybe_preselect_engine(self):
        # #178: in the first-run wizard, let the entered key preselect the matching
        # startup engine -- Groq-only -> Groq Whisper Large v3, else the built-in
        # default (Soniox Live). In the two-mode control (#198) a fresh wizard user
        # starts in remember mode, so the preselect moves the *remembered* engine
        # (written to the memory on save, not a pin) -- and the fixed dropdown too, so
        # a later flip to fixed starts on the same engine. Preselection only: gated to
        # first-run and skipped once the user engaged an engine or mode explicitly.
        if not self.first_run or self._engine_user_chose:
            return
        target = settings_io.preselect_startup_api(
            bool(self.groq_var.get().strip()), bool(self.soniox_var.get().strip()))
        self._remember_display_api = target
        self.engine_index = config.AVAILABLE_APIS.index(target)
        self._render_engine_control()   # reflect live, even off-tab

    def _open_terminal(self):
        # No documented wt.exe flag opens the settings pane directly (web re-checked
        # 2026-07); launch the window and let behavior.tray.body tell the user to
        # press Ctrl+,. The app NEVER touches Terminal's settings.json (F4, D-002).
        try:
            subprocess.Popen(["wt.exe"])
        except Exception:
            messagebox.showinfo(strings.t("behavior.tray.heading", self.lang),
                                strings.t("behavior.tray.no_wt", self.lang))

    # ------------------------------------------------------------- language / rail
    def _on_lang(self):
        new = self.lang_var.get()
        if new == self.lang:
            return
        self.lang = new
        self.render_all()
        self._persist_language()   # D-014: a toggle self-persists at once

    def _persist_language(self):
        # A language toggle self-persists immediately (D-014): with no unsaved-changes
        # guard, Cancel/[X] just close, so a toggle not written now would be lost. A
        # narrow ui.language-only surgical write -- hotkeys/defaults/unmanaged blocks
        # stay exactly as found (hotkeys_effective=None, default_api=None), so a session
        # that only ever toggles the language leaves every other block byte-identical.
        # One rule for both modes: the language radios live in the shared header, so this
        # fires in the wizard and the everyday dialog alike. Best-effort -- a failed write
        # costs only the remembered display language, never the settings, so it stays
        # silent rather than raising an error dialog on every failed toggle (mirrors
        # engine_memory.write_last_engine's stance).
        try:
            settings_io.write_personal_settings(
                config.SCRIPT_DIR / "personal_settings.json",
                hotkeys_effective=None, default_api=None, ui_language=self.lang,
                example_path=config.SCRIPT_DIR / "personal_settings.example.json")
        except Exception:
            pass

    def _has_any_key(self):
        """True iff a key is entered OR one is already stored. The single predicate
        behind both the honest last-tab button (#178) and the "Save & start" launch
        guard in _save, so the button label can never promise a start the guard then
        refuses. A blank field never clobbers a stored key (settings_io), so an empty
        field on top of a stored key still counts as keyed."""
        return bool(self.groq_var.get().strip() or self.soniox_var.get().strip()
                    or self._had_stored_key)

    def _live_env(self):
        """The two managed key fields as an {ENV_VAR: value} dict, for the per-engine
        keyed test (config.API_KEY_ENV names those vars). Fed to
        settings_io.engine_keyed alongside the load-time _stored_env snapshot, so a
        key typed this session greys/un-greys the matching engines live (#201)."""
        return {"GROQ_API_KEY": self.groq_var.get(),
                "SONIOX_API_KEY": self.soniox_var.get()}

    def update_rail(self, event=None):
        """Recompute the first-run rail. Settings mode is static (a no-op) but the
        binding stays wired so there is one code path, not scattered mode forks."""
        if self._restarting:
            return    # #202: keep the frozen "Restarting…"/disabled rail during the
                      # restart wait -- a tab change must not re-enable Back or relabel
        if not self.first_run:
            return
        idx = self.notebook.index("current")
        last = len(self._tab_frames) - 1
        self.back_btn.config(state="disabled" if idx == 0 else "normal")
        if idx < last:
            key = "btn.next"
        else:
            # Honest last-tab label (#178, #202): a running tool + a key makes this a
            # RESTART; else the #178 start/close split by key presence. Live mutex
            # probe -- update_rail fires on tab change and language re-render only, so
            # a running tool starting/exiting while the window is open stays reflected.
            key = "btn." + settings_io.resolve_save_action(
                first_run=True, has_key=self._has_any_key(),
                tool_running=restart_signal.tool_is_running())
        self.next_btn.config(text=strings.t(key, self.lang))

    # ---------------------------------------------------------- per-tab scrolling
    def _on_tab_changed(self, event=None):
        # The <<NotebookTabChanged>> handler (#180): keep the rail behaviour, then make
        # the newly visible tab's canvas the wheel/key scroll target and open it at the
        # top (never mid-scroll after a shrink).
        self.update_rail(event)
        idx = self.notebook.index("current")
        if 0 <= idx < len(self._tab_canvases):
            self._active_canvas = self._tab_canvases[idx]
            self._active_canvas.yview_moveto(0.0)
            self._wheel_accum = 0     # no leftover delta carries across tabs

    def _overflows(self, c):
        bbox = c.bbox("all")
        return bool(bbox) and bbox[3] > c.winfo_height()

    def _entry_has_focus(self):
        # True when the keyboard focus sits in a text entry (the two key fields).
        # Home/End must then move the caret, not double as a scroll. The engine picker
        # is a radio list since #201 (not an Entry subclass), so focus on it correctly
        # returns False here. focus_get can raise mid-teardown -- guard it.
        try:
            return isinstance(self.root.focus_get(), (tk.Entry, ttk.Entry))
        except Exception:
            return False

    def _on_mousewheel(self, event):
        # A modal dialog / open dropdown holds a grab; leave its own wheel handling
        # alone and never scroll the (hidden) main content underneath it.
        if self.root.grab_current() is not None:
            return
        c = self._active_canvas
        if c is not None and self._overflows(c):
            # Bank the delta and spend it in whole 120-notches: truncating each
            # event to int(delta / 120) drops the sub-120 deltas that precision
            # touchpads send, stalling slow two-finger scrolls. A classic mouse
            # (delta == +-120) still moves exactly one unit per notch.
            self._wheel_accum += event.delta
            steps = int(self._wheel_accum / 120)
            if steps:
                self._wheel_accum -= steps * 120
                c.yview_scroll(-steps, "units")

    def _on_scroll_key(self, event):
        if self.root.grab_current() is not None:
            return
        c = self._active_canvas
        if c is None or not self._overflows(c):
            return
        k = event.keysym
        # Home/End in a focused Entry belong to the caret; PageUp/PageDown are inert in
        # a single-line Entry, so they always scroll -- which keeps top/bottom reachable
        # by keyboard even while a key field has focus.
        if k in ("Home", "End") and self._entry_has_focus():
            return
        if k == "Prior":
            c.yview_scroll(-1, "pages")
        elif k == "Next":
            c.yview_scroll(1, "pages")
        elif k == "Home":
            c.yview_moveto(0.0)
        elif k == "End":
            c.yview_moveto(1.0)

    def _on_back(self):
        idx = self.notebook.index("current")
        if idx > 0:
            self.notebook.select(idx - 1)

    def _on_next(self):
        idx = self.notebook.index("current")
        last = len(self._tab_frames) - 1
        if idx < last:
            self.notebook.select(idx + 1)
        else:
            self._save(start_after=True)

    def _render_rail(self):
        if self._restarting:
            return    # #202: a language toggle mid-wait must not repaint the frozen rail
        if self.first_run:
            self.back_btn.config(text=strings.t("btn.back", self.lang))
            self.update_rail()
        else:
            # A running tool turns everyday "Save" into "Save & restart" (#202); the
            # footer then tells the truth (changes apply right away, not next start).
            # Live probe, mirrored at click time in _save.
            action = settings_io.resolve_save_action(
                first_run=False, has_key=self._has_any_key(),
                tool_running=restart_signal.tool_is_running())
            self.save_btn.config(text=strings.t("btn." + action, self.lang))
            self.cancel_btn.config(text=strings.t("btn.cancel", self.lang))
            self.footer_lbl.config(text=strings.t(
                "footer.restart" if action == "save_restart" else "footer.next_start",
                self.lang))

    # ---------------------------------------------------------------- render_all
    def render_all(self):
        """Re-apply every string for the current language. Static text via the
        registries; dynamic widgets re-render from their semantic state."""
        for widget, key in self._text_widgets:
            try:
                widget.config(text=strings.t(key, self.lang))
            except tk.TclError:
                pass
        for widget, text_key, _url_key in self._link_widgets:
            widget.config(text=strings.t(text_key, self.lang))
        for i, key in enumerate(_TAB_KEYS):
            self.notebook.tab(i, text=strings.t(key, self.lang))
        for provider in ("groq", "soniox"):
            self._render_reveal_btn(provider)
            self._render_indicator(provider)
        self._render_engine_control()
        self._render_hotkey_grid()
        self._render_hotkey_status()
        self._render_capture_limit()
        self._render_done_page()
        self._render_welcome_page()
        self._render_rail()
        self.root.title(strings.t(
            "app.title.firstrun" if self.first_run else "app.title.settings", self.lang))

    # ------------------------------------------------------------- save / close
    def _save(self, start_after=False):
        # A key is present if one is entered OR one is already stored (_has_any_key --
        # a blank field never clobbers a stored key, so an empty field on top of a
        # stored key is NOT keyless, and the "no key" warning must not fire there).
        has_key = self._has_any_key()
        # Pre-save checks (order matters): no key at all, then hotkey warnings.
        if not has_key:
            if not messagebox.askyesno(strings.t("dlg.nokey.title", self.lang),
                                       strings.t("dlg.nokey.body", self.lang)):
                return
        if self._hotkey_warnings():
            if not messagebox.askyesno(strings.t("dlg.hotkeywarn.title", self.lang),
                                       strings.t("dlg.hotkeywarn.body", self.lang)):
                return

        # Engine field (#193/#198, D-008): derive the two on-save signals from the
        # two-mode control in one pure, off-Windows-tested place. `default_api_signal`
        # is what defaults.api gets -- None (leave as found) / REMOVE_API_PIN (drop
        # the pin) / an id (write verbatim, built-in default included). `memory_api`
        # is the engine to record in runtime_state.json, or None. The two are
        # mutually exclusive: the app's only memory write is the #178 wizard preselect
        # in remember mode; a fixed pick records no memory -- its written pin is what
        # takes effect (that pin now writes even on the built-in default, D-002).
        engine_now = config.AVAILABLE_APIS[self.engine_index]
        engine_loaded = config.AVAILABLE_APIS[self._engine_index_loaded]
        default_api_signal, memory_api = settings_io.resolve_engine_save_signal(
            mode_now=self.mode_var.get(), mode_loaded=self._mode_loaded,
            engine_now=engine_now, engine_loaded=engine_loaded,
            remember_display_now=self._remember_display_api,
            remember_display_loaded=self._remember_display_loaded_api)
        try:
            settings_io.write_env(
                config.SCRIPT_DIR / ".env",
                {"GROQ_API_KEY": self.groq_var.get(),
                 "SONIOX_API_KEY": self.soniox_var.get()},
                example_path=config.SCRIPT_DIR / ".env.example")
            settings_io.write_personal_settings(
                config.SCRIPT_DIR / "personal_settings.json",
                hotkeys_effective=self.hotkeys_state,
                default_api=default_api_signal,
                example_path=config.SCRIPT_DIR / "personal_settings.example.json",
                # language self-persists on toggle (D-014); the save never writes it --
                # ui_language=None leaves any existing ui block exactly as found.
                ui_language=None)
        except Exception as e:
            # Atomic writes + abort-on-unreadable (CP1) mean no file is left half-
            # written or corrupted. .env is written before personal_settings.json, so
            # a failure of the second still leaves the first's (valid) update on disk --
            # hence the message speaks of atomicity, not "nothing was overwritten".
            messagebox.showerror(
                strings.t("dlg.savefail.title", self.lang),
                strings.t("dlg.savefail.body", self.lang) + "\n\n" + str(e))
            return

        if memory_api is not None:
            # After the settings files are safely on disk, and best-effort by
            # contract (never raises, returns False on failure): a lost memory
            # write costs only the remembered value, never the save -- so it stays
            # silent rather than firing an error dialog over the saved settings.
            engine_memory.write_last_engine(
                engine_memory.state_path(config.SCRIPT_DIR), memory_api,
                config.AVAILABLE_APIS)

        # Post-save action (#202). Re-probe the mutex at CLICK time -- the label's
        # probe may be minutes stale, and both drift directions must resolve right:
        # a tool that started since the label was drawn still restarts; one that
        # exited falls back to a plain save/start with no signal written. `start_after`
        # is the wizard's launch-after-save flag (== self.first_run at both call
        # sites), so it doubles as the resolver's first_run.
        action = settings_io.resolve_save_action(
            first_run=start_after, has_key=has_key,
            tool_running=restart_signal.tool_is_running())
        if action == "save_restart":
            self._restart_and_relaunch()   # owns the window from here (wait -> relaunch)
            return
        # "Save & start" launches only with a key present: a keyless launch would make
        # the tool re-detect no key, relaunch this wizard and exit -- a visible bounce.
        # resolve_save_action already gives save_close (not save_start) when keyless,
        # so this branch is reached only when a launch is warranted. A failed launch
        # still saved -- say so, then close.
        if action == "save_start" and not self._launch_tool():
            messagebox.showerror(strings.t("dlg.startfail.title", self.lang),
                                 strings.t("dlg.startfail.body", self.lang))
        self.root.destroy()

    def _launch_tool(self):
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", str(config.SCRIPT_DIR / "Thoughtborne.bat")],
                cwd=str(config.SCRIPT_DIR), creationflags=CREATE_NEW_CONSOLE)
            return True
        except Exception:
            return False

    def _restart_and_relaunch(self):
        """#202: tell the running tool to exit (a signal file it polls ~1 Hz), wait
        for its D-004 single-instance mutex NAME to vanish, then relaunch via the
        normal _launch_tool lane. The save has already succeeded.

        root.after-based so the window stays responsive during the wait; the rail is
        frozen and the window's [X] neutralized so a mid-restart close can't strand a
        half-done state (tool told to exit, nobody relaunching). On timeout an honest
        dialog and NO launch -- the tool is never killed (spec). The signal is written
        here, in _save's tail AFTER the settings files, so the relaunched tool reads
        the new state.

        Two-phase deadline. RESTART_WAIT_SECONDS is the PRE-ACK budget: the tool must
        delete (consume) the signal within it, or it counts as non-responsive and the
        timeout fires promptly and honestly. The instant the signal file is gone (the
        tool's ACK -- taken before the mutex frees, since consume LEADS the shutdown),
        the deadline stretches once to the generous RESTART_SHUTDOWN_GRACE_SECONDS so a
        long-but-healthy salvage+teardown -- the advertised mid-recording restart -- is
        not cut off by a false "did not close" dialog.
        """
        sig_path = restart_signal.signal_path(config.SCRIPT_DIR)
        if not restart_signal.request_restart(sig_path):
            # Could not even write the signal -- settings are saved, tool runs on.
            messagebox.showerror(strings.t("dlg.restartfail.title", self.lang),
                                 strings.t("dlg.restartfail.body", self.lang))
            self.root.destroy()
            return
        self._set_rail_waiting()
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)  # reverted by destroy()
        deadline = time.monotonic() + restart_signal.RESTART_WAIT_SECONDS
        consumed = False    # flips once, when the tool deletes the signal (its ACK)

        def _poll():
            nonlocal deadline, consumed
            if not restart_signal.tool_is_running():
                # The mutex name is gone (the kernel frees it on the old instance's
                # exit): relaunch the fresh, re-configured instance.
                if not self._launch_tool():
                    messagebox.showerror(strings.t("dlg.startfail.title", self.lang),
                                         strings.t("dlg.startfail.body", self.lang))
                self.root.destroy()
                return
            # Tool still up. The instant the signal file is gone it has consumed the
            # request and committed to the clean shutdown, whose mid-recording salvage
            # can outlast the pre-ACK budget -- stretch the deadline once to the
            # post-ACK grace. Watched before tool_is_running goes False: consume leads
            # the shutdown, the mutex frees only at process exit.
            if not consumed and not restart_signal.signal_present(sig_path):
                consumed = True
                deadline = time.monotonic() + restart_signal.RESTART_SHUTDOWN_GRACE_SECONDS
            if time.monotonic() >= deadline:
                if not consumed:
                    # Retract the un-consumed request so a tool that recovers later (a #128
                    # wedge clearing, an AV lock releasing) can't consume the relic and shut
                    # itself down minutes after this dialog with nobody to relaunch it.
                    restart_signal.consume_restart_signal(sig_path)
                messagebox.showerror(strings.t("dlg.restarttimeout.title", self.lang),
                                     strings.t("dlg.restarttimeout.body", self.lang))
                self.root.destroy()
                return
            self.root.after(restart_signal.POLL_INTERVAL_MS, _poll)

        _poll()

    def _set_rail_waiting(self):
        """Freeze the rail for the #202 restart wait: disable both buttons and label
        the acting one 'Restarting…'. Handles either rail -- the wizard's next/back or
        everyday's save/cancel."""
        self._restarting = True   # from here update_rail / _render_rail leave the rail alone
        if self.first_run:
            acting, other = self.next_btn, self.back_btn
        else:
            acting, other = self.save_btn, self.cancel_btn
        acting.config(text=strings.t("btn.restarting", self.lang), state="disabled")
        other.config(state="disabled")


def _probe_viewable(root):
    """Best-effort `winfo_viewable()` as 0/1, or None on any fault (#203)."""
    try:
        return int(bool(root.winfo_viewable()))
    except Exception:
        return None


def _probe_rect(root):
    """Best-effort on-screen window rect as 'WxH+X+Y', or None on any fault (#203)."""
    try:
        return (f"{root.winfo_width()}x{root.winfo_height()}"
                f"+{root.winfo_rootx()}+{root.winfo_rooty()}")
    except Exception:
        return None


def _probe_foreground(root):
    """Best-effort 'is our window the OS foreground window' -> 'Y'/'N', or None (#203).

    Windows-only (ctypes), fully guarded, so it renders as '?' off-Windows or on any
    fault and the visible: line never depends on it. winfo_id() can be a child HWND, so
    walk to the root owner (GetAncestor GA_ROOT) before comparing; argtypes are pinned
    to c_void_p so a 64-bit HWND is not truncated to a C int. A private WinDLL instance
    (settings_instance's pattern) so pinning those argtypes never mutates the
    process-wide ctypes.windll.user32 other code shares."""
    try:
        if os.name != "nt":
            return None
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAncestor.restype = ctypes.c_void_p
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        GA_ROOT = 2
        hwnd = user32.GetAncestor(ctypes.c_void_p(root.winfo_id()), GA_ROOT)
        fg = user32.GetForegroundWindow()
        return "Y" if hwnd and fg and hwnd == fg else "N"
    except Exception:
        return None


def _bind_startup_timing(root, t_tk, t_size, t_construct, first_run):
    """Write startup-timing lines to thoughtborne.log at first window map (#195) and,
    since #203, at first paint, so the spawn-to-visible latency is diagnosable on a
    real machine. Quiet and file-only; everything guarded -- instrumentation must never
    delay or break the app. The app has no logger of its own, so a single plain append
    beside the tool's own 'Opened the settings app' line is the cheapest sink: no
    RotatingFileHandler (a two-process rotation would race), just one open-append at a
    rare user event. A line missed by the tool's own log rotation mid-append is
    harmless -- try/except swallows it and the check can simply be repeated.

    Two lines: `[SETTINGS] startup:` at Tk's internal first `<Map>` (unchanged), and
    `[SETTINGS] visible:` at the first paint activity in the window. Tk maps a window
    before the OS realizes and paints it, so `<Map>` alone is decoupled from being
    visible; the gap between the two `total` figures IS the map-to-visible latency #203
    chased. The signal is `<Expose>` on the root, which -- through the toplevel's
    bindtags -- fires for the first paint of *any* widget in the window, not strictly
    the toplevel's own paint, so it marks the earliest paint activity rather than a
    frame-accurate present. It is diagnostic, not a proof of visibility, but since it
    can only dispatch once the mainloop is free (i.e. after the storm drains) it
    honestly reflects the order of magnitude."""
    state = {"done": False, "expose_done": False, "t_map": None}

    def _on_first_map(_evt):
        if state["done"]:
            return
        state["done"] = True
        try:
            t_map = time.perf_counter()
            state["t_map"] = t_map
            parts = []
            if _SPAWN_TS is not None:
                parts.append(f"spawn->entry={_WALL_ENTRY - _SPAWN_TS:.2f}s")
            parts.append(f"import={_T_IMPORTS - _T_MODTOP:.2f}s")
            parts.append(f"tk.Tk={t_tk - _T_IMPORTS:.2f}s")
            parts.append(f"size={t_size - t_tk:.2f}s")
            parts.append(f"construct={t_construct - t_size:.2f}s")
            parts.append(f"first-map={t_map - t_construct:.2f}s")
            if _SPAWN_TS is not None:
                parts.append(f"total={time.time() - _SPAWN_TS:.2f}s")
            mode = "firstrun" if first_run else "settings"
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            line = f"{stamp} [SETTINGS] startup: {' '.join(parts)} mode={mode}\n"
            with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            pass

    def _on_first_expose(_evt):
        if state["expose_done"]:
            return
        state["expose_done"] = True
        try:
            t_expose = time.perf_counter()
            t_map = state["t_map"]
            map_to_expose = (t_expose - t_map) if t_map is not None else None
            total = (time.time() - _SPAWN_TS) if _SPAWN_TS is not None else None
            mode = "firstrun" if first_run else "settings"
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            line = settings_visibility.format_visible_line(
                stamp, map_to_expose, total,
                _probe_viewable(root), _probe_foreground(root), _probe_rect(root),
                mode)
            with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            pass

    try:
        root.bind("<Map>", _on_first_map, add="+")
        root.bind("<Expose>", _on_first_expose, add="+")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--first-run", action="store_true",
                        help="force first-run wizard mode; the app also auto-promotes "
                             "to it when no API key is stored yet (#163)")
    args, _ = parser.parse_known_args()

    # Single-instance guard (#196, D-009): keep the settings app to one window. A
    # second launch -- from either spawn path (Ctrl+Alt+G / --first-run, or the
    # Thoughtborne-Settings.bat double-click) -- brings the existing window to the
    # front and exits silently, rather than stacking a second independent editor of
    # .env / personal_settings.json (D-002). Focus IS the feedback for a GUI, so no
    # notice (unlike the tool's D-004 refuse). Checked before tk.Tk() so no window is
    # built only to be discarded. settings_instance is fail-open: any guard fault
    # just starts normally, never costing a launch.
    _handle, already = settings_instance.create_instance_mutex()
    if already:
        # Log the focus outcome (#203, D-009): the raise is best-effort and was
        # previously silent, so the log could not tell a successful raise from a
        # no-op. Same plain-append sink as the startup line, written before tk.Tk()
        # in this second process; fully guarded so instrumentation never blocks exit.
        outcome = settings_instance.focus_existing_settings_window()
        try:
            with open(config.LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                         f"[SETTINGS] focus-existing: {outcome}\n")
        except Exception:
            pass
        sys.exit(0)

    _enable_high_dpi()
    root = tk.Tk()
    t_tk = time.perf_counter()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass
    # The ttk theme is no longer pinned here: SettingsApp.__init__ applies clam +
    # our explicit styling as its first step (#155, D-010), so every entry point --
    # the app, the render harness, a test -- gets the same window.
    # Size before constructing the app so the geometry is in place even if __init__
    # pops a modal load-failure dialog; the sizing is pure DPI + screen, no widgets
    # needed.
    _size_window(root)
    t_size = time.perf_counter()

    # #163: open the wizard on the explicit flag (thoughtborne.py's keyless hook) OR
    # when no readable key is stored yet -- so the installer hand-off, which passes no
    # flag, lands on the guided wizard on a fresh keyless machine, while a re-run over a
    # keyed install still opens the plain settings dialog. read_env never raises.
    env = settings_io.read_env(config.SCRIPT_DIR / ".env")
    first_run = settings_io.resolve_first_run(args.first_run, env)
    SettingsApp(root, first_run=first_run)
    t_construct = time.perf_counter()

    _bind_startup_timing(root, t_tk, t_size, t_construct, first_run)
    root.mainloop()


if __name__ == "__main__":
    main()
