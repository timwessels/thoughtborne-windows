"""
Graphical settings + first-run onboarding app for Thoughtborne (#144).

One tkinter window that doubles as the first-run wizard (rail: Back / Next /
"Save & start") and the everyday settings dialog (rail: Save / Cancel) -- the two
modes differ ONLY by the `--first-run` CLI flag; the three tabs (Provider ->
Hotkeys -> Behavior) are identical in both ("one window, one face"). German or
English, switchable in the header.

Pure stdlib: tkinter + ctypes + threading + queue + subprocess + webbrowser. This
module holds NO IO or validation logic of its own -- every file read/write, key
check, hotkey decode/validate/collision check, and preset is a call into the CP1
modules (`settings_io`, `key_check`, `config`, `settings_strings`). Tk is not
thread-safe, so the "Test key" round-trip runs on a daemon worker and marshals its
result back through a `queue.Queue` polled by `root.after` -- widgets are only ever
touched on the UI thread.

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
import queue
import subprocess
import threading
import webbrowser

import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont

import config
import key_check
import settings_io
import settings_strings as strings
from hotkey_parse import parse_hotkey_lexical
from key_check import KeyStatus

# ---- colors (glyph + color + text together; red stays for the rejected key) ----
LINK_COLOR = "#0B5CAB"
TEXT_COLOR = "black"
GREEN = "#107C10"
RED = "#C42B1C"
GREY = "#6D6D6D"
AMBER = "#8A6D00"

# CreateProcess flag exists only on Windows; 0 is a harmless no-op elsewhere so a
# stray import off-Windows can't fail at module load.
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)

_TAB_KEYS = ("provider.tab", "hotkeys.tab", "behavior.tab")


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
    base_w, base_h = 780, 800
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

        # Re-render registries: simple text-bearing widgets and link widgets.
        self._text_widgets = []     # (widget, string-key)
        self._link_widgets = []     # (widget, text-key, url-key)

        # per-provider widget handles + state
        self._entries = {}
        self._reveal_btns = {}
        self._test_btns = {}
        self._indicators = {}
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

        self.dirty = False
        self._lang_toggled = False

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

        api = None
        dblk = personal.get("defaults")
        if isinstance(dblk, dict):
            api = dblk.get("api")
        if api not in config.AVAILABLE_APIS:
            api = config.BUILTIN_DEFAULT_API
        self.engine_index = config.AVAILABLE_APIS.index(api)

        ui = personal.get("ui")
        self._had_ui_block = isinstance(ui, dict)
        lang = ui.get("language") if self._had_ui_block else None
        if lang not in ("de", "en"):
            lang = strings.detect_ui_language()
        self.lang = lang

        # tk vars (root already exists)
        self.lang_var = tk.StringVar(value=self.lang)
        env = settings_io.read_env(config.SCRIPT_DIR / ".env")
        self.groq_var = tk.StringVar(value=env.get("GROQ_API_KEY", ""))
        self.soniox_var = tk.StringVar(value=env.get("SONIOX_API_KEY", ""))
        # A readable key is already stored iff read_env surfaced one. Used by the
        # pre-save "no key" check and the "Save & start" launch guard: a blank field
        # never clobbers a stored key (settings_io), so an empty field on top of a
        # stored key is NOT keyless. (An unreadable/ANSI .env reads as no keys here;
        # that rarer case is caught downstream -- write_env aborts such a save.)
        self._had_stored_key = bool(env.get("GROQ_API_KEY", "").strip()
                                    or env.get("SONIOX_API_KEY", "").strip())

        # fonts derived from the theme default
        self.default_font = tkfont.nametofont("TkDefaultFont")
        self.heading_font = self.default_font.copy()
        self.heading_font.configure(weight="bold")
        self.window_heading_font = self.default_font.copy()
        self.window_heading_font.configure(weight="bold",
                                           size=abs(self.default_font.cget("size")) + 2)

        self._build_ui()

        # Field-edit traces added AFTER prefill so the initial fill isn't "dirty".
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

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ helpers
    def _reg(self, widget, key):
        """Register a text-bearing widget for language re-render and set it now."""
        self._text_widgets.append((widget, key))
        widget.config(text=strings.t(key, self.lang))
        return widget

    def _prose(self, parent, key, **kw):
        """A left-justified explainer label that wraps to its own width."""
        lbl = ttk.Label(parent, justify="left", **kw)
        self._reg(lbl, key)

        def _wrap(event, w=lbl):
            # width, not wraplength, is parent-driven (fill='x'), so this changes
            # only the label's height -- no resize loop.
            w.configure(wraplength=max(event.width - 8, 120))
        lbl.bind("<Configure>", _wrap)
        return lbl

    def _link(self, parent, text_key, url_key):
        """A blue, underlined, hand-cursor label that opens url_key in a browser."""
        lbl = tk.Label(parent, fg=LINK_COLOR, cursor="hand2")
        font = tkfont.Font(font=lbl.cget("font"))
        font.configure(underline=True)
        lbl.configure(font=font)
        self._link_widgets.append((lbl, text_key, url_key))
        lbl.config(text=strings.t(text_key, self.lang))
        lbl.bind("<Button-1>",
                 lambda e, uk=url_key: webbrowser.open(strings.t(uk, self.lang)))
        return lbl

    def _mark_dirty(self):
        self.dirty = True

    # -------------------------------------------------------------- UI assembly
    def _build_ui(self):
        # rail first (pinned bottom), then header (top), then notebook (fills).
        self._build_rail()
        self._build_header()
        self.warn_strip = ttk.Label(self.root, foreground=AMBER, wraplength=740,
                                     justify="left")
        # Register it so render_all() re-renders its text on a language toggle; its
        # only text is warn.corrupt, and it stays unpacked (invisible) until a corrupt
        # settings file packs it into view below.
        self._reg(self.warn_strip, "warn.corrupt")
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(side="top", fill="both", expand=True, padx=8, pady=(2, 0))
        self._tab_frames = [
            self._build_provider_tab(),
            self._build_hotkeys_tab(),
            self._build_behavior_tab(),
        ]
        for frame, key in zip(self._tab_frames, _TAB_KEYS):
            self.notebook.add(frame, text=strings.t(key, self.lang))
        self.notebook.bind("<<NotebookTabChanged>>", self.update_rail)

    def _build_header(self):
        header = ttk.Frame(self.root, padding=(12, 10, 12, 6))
        header.pack(side="top", fill="x")
        lang_frame = ttk.Frame(header)
        lang_frame.pack(side="right", anchor="ne")
        # The two radio labels name themselves and never translate.
        ttk.Radiobutton(lang_frame, text=strings.t("lang.de", "de"), value="de",
                        variable=self.lang_var, command=self._on_lang).pack(side="left")
        ttk.Radiobutton(lang_frame, text=strings.t("lang.en", "en"), value="en",
                        variable=self.lang_var, command=self._on_lang).pack(side="left")
        self.title_lbl = ttk.Label(header, font=self.window_heading_font)
        self._reg(self.title_lbl,
                  "welcome.heading" if self.first_run else "app.title.settings")
        self.title_lbl.pack(side="top", anchor="w")
        if self.first_run:
            self._prose(header, "welcome.sub").pack(side="top", anchor="w", fill="x",
                                                    pady=(4, 0))

    def _build_rail(self):
        rail = ttk.Frame(self.root, padding=(12, 6, 12, 10))
        rail.pack(side="bottom", fill="x")
        if self.first_run:
            self.next_btn = ttk.Button(rail, command=self._on_next)
            self.next_btn.pack(side="right")
            self.back_btn = ttk.Button(rail, command=self._on_back)
            self.back_btn.pack(side="right", padx=(0, 6))
        else:
            self.cancel_btn = ttk.Button(rail, command=self._on_close)
            self.cancel_btn.pack(side="right")
            self.save_btn = ttk.Button(rail, command=lambda: self._save(False))
            self.save_btn.pack(side="right", padx=(0, 6))
            self.footer_lbl = ttk.Label(rail, foreground=GREY)
            self.footer_lbl.pack(side="left")

    # ---- provider tab ----
    def _build_provider_tab(self):
        f = ttk.Frame(self.notebook, padding=12)
        h = ttk.Label(f, font=self.heading_font)
        self._reg(h, "provider.keys.heading")
        h.pack(anchor="w")
        self._prose(f, "provider.keys.body").pack(fill="x", pady=(2, 6))
        self._prose(f, "provider.lanes.body").pack(fill="x", pady=(0, 8))
        # Groq first (the free "try it now" lane), Soniox second (carries default).
        self._build_provider_card(f, "groq", "provider.groq.heading",
                                  "provider.groq.body", "url.groq_keys",
                                  "provider.field.groq")
        self._build_provider_card(f, "soniox", "provider.soniox.heading",
                                  "provider.soniox.body", "url.soniox_console",
                                  "provider.field.soniox")
        self._prose(f, "provider.keep_note", foreground=GREY).pack(fill="x", pady=(6, 0))
        return f

    def _build_provider_card(self, parent, provider, heading_key, body_key,
                             url_key, field_key):
        card = ttk.LabelFrame(parent, padding=10)
        self._reg(card, heading_key)
        card.pack(fill="x", pady=6)
        self._prose(card, body_key).pack(fill="x", pady=(0, 4))
        self._link(card, url_key, url_key).pack(anchor="w", pady=(0, 6))
        flbl = ttk.Label(card)
        self._reg(flbl, field_key)
        flbl.pack(anchor="w")
        row = ttk.Frame(card)
        row.pack(fill="x", pady=(2, 2))
        var = self.groq_var if provider == "groq" else self.soniox_var
        entry = ttk.Entry(row, textvariable=var, show="•")
        entry.pack(side="left", fill="x", expand=True)
        self._entries[provider] = entry
        rbtn = ttk.Button(row, width=10, command=lambda p=provider: self._toggle_reveal(p))
        rbtn.pack(side="left", padx=4)
        self._reveal_btns[provider] = rbtn
        tbtn = ttk.Button(row, command=lambda p=provider: self._test_key(p))
        self._reg(tbtn, "btn.test_key")
        tbtn.pack(side="left")
        self._test_btns[provider] = tbtn
        ind = ttk.Label(card)
        ind.pack(anchor="w", pady=(2, 0))
        self._indicators[provider] = ind

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
        # disabled). Then mark the form dirty.
        self._test_gen[provider] += 1
        self._test_state[provider] = None
        self._test_btns[provider].config(state="normal")
        self._render_indicator(provider)
        self._mark_dirty()

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
        f = ttk.Frame(self.notebook, padding=12)
        self._prose(f, "hotkeys.intro").pack(fill="x")
        prow = ttk.Frame(f)
        prow.pack(fill="x", pady=8)
        self._build_preset_card(prow, "ctrl_alt", "hotkeys.preset.ctrl_alt.title",
                                "hotkeys.preset.ctrl_alt.body", side="left")
        self._build_preset_card(prow, "fkeys", "hotkeys.preset.fkeys.title",
                                "hotkeys.preset.fkeys.body", side="right")

        ch = ttk.Label(f, font=self.heading_font)
        self._reg(ch, "hotkeys.custom.heading")
        ch.pack(anchor="w", pady=(6, 0))
        self._prose(f, "hotkeys.custom.body").pack(fill="x", pady=(0, 4))

        grid = ttk.Frame(f)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        for r, name in enumerate(config.DEFAULT_HOTKEYS):
            al = ttk.Label(grid)
            self._reg(al, f"action.{name}")
            al.grid(row=r, column=0, sticky="w", padx=(0, 8), pady=1)
            cl = tk.Label(grid, anchor="w", foreground=TEXT_COLOR, takefocus=True)
            cl.grid(row=r, column=1, sticky="w", padx=8)
            cl.bind("<KeyPress>", lambda e, n=name: self._on_capture_key(e, n))
            cl.bind("<Escape>", lambda e, n=name: self._on_capture_escape(e, n))
            cl.bind("<FocusOut>", lambda e, n=name: self._on_capture_focusout(e, n))
            self._combo_labels[name] = cl
            cb = ttk.Button(grid, command=lambda n=name: self._arm(n))
            self._reg(cb, "btn.change_key")
            cb.grid(row=r, column=2, sticky="e", pady=1)

        self.capture_lbl = ttk.Label(f, foreground=AMBER, justify="left")
        self.capture_lbl.pack(fill="x", pady=(4, 2))
        self.status_lbl = ttk.Label(f, justify="left")
        self.status_lbl.pack(fill="x")
        return f

    def _build_preset_card(self, parent, which, title_key, body_key, side):
        card = ttk.LabelFrame(parent, padding=8)
        self._reg(card, title_key)
        pad = (0, 4) if side == "left" else (4, 0)
        card.pack(side=side, fill="both", expand=True, padx=pad)
        self._prose(card, body_key).pack(fill="x")
        btn = ttk.Button(card, command=lambda w=which: self._apply_preset(w))
        self._reg(btn, "btn.use_preset")
        btn.pack(anchor="w", pady=(6, 0))

    def _apply_preset(self, which):
        self.hotkeys_state = (settings_io.preset_ctrl_alt() if which == "ctrl_alt"
                              else settings_io.preset_fkeys())
        self._disarm()
        self._mark_dirty()
        self._render_hotkey_grid()
        self._render_hotkey_status()

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
        lbl = self._combo_labels[name]
        if self._armed == name:
            lbl.config(text=strings.t("capture.prompt", self.lang), foreground=LINK_COLOR)
        else:
            lbl.config(text=self._pretty_combo(self.hotkeys_state[name]),
                       foreground=TEXT_COLOR)

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
        self._mark_dirty()
        self._render_hotkey_status()
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
        f = ttk.Frame(self.notebook, padding=12)
        eh = ttk.Label(f, font=self.heading_font)
        self._reg(eh, "behavior.engine.heading")
        eh.pack(anchor="w")
        self._prose(f, "behavior.engine.body").pack(fill="x", pady=(2, 4))
        self.engine_combo = ttk.Combobox(f, state="readonly")
        self.engine_combo.pack(anchor="w", fill="x")
        self.engine_combo.bind("<<ComboboxSelected>>", self._on_engine)

        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=10)

        th = ttk.Label(f, font=self.heading_font)
        self._reg(th, "behavior.tray.heading")
        th.pack(anchor="w")
        self._prose(f, "behavior.tray.body").pack(fill="x", pady=(2, 4))
        tbtn = ttk.Button(f, command=self._open_terminal)
        self._reg(tbtn, "btn.open_terminal")
        tbtn.pack(anchor="w")

        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=10)

        ah = ttk.Label(f, font=self.heading_font)
        self._reg(ah, "behavior.admin.heading")
        ah.pack(anchor="w")
        self._prose(f, "behavior.admin.body").pack(fill="x", pady=(2, 4))
        self._link(f, "behavior.admin.link", "url.admin_recipe").pack(anchor="w")
        return f

    def _render_engine_combo(self):
        values = [f"{config.API_DISPLAY[a]['label']} — "
                  f"{strings.t('engine.desc.' + a, self.lang)}"
                  for a in config.AVAILABLE_APIS]
        self.engine_combo.config(values=values)
        self.engine_combo.current(self.engine_index)

    def _on_engine(self, event=None):
        # Track selection by index into AVAILABLE_APIS, never by parsing the string.
        self.engine_index = self.engine_combo.current()
        self._mark_dirty()

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
        self._lang_toggled = True
        self._mark_dirty()
        self.render_all()

    def update_rail(self, event=None):
        """Recompute the first-run rail. Settings mode is static (a no-op) but the
        binding stays wired so there is one code path, not scattered mode forks."""
        if not self.first_run:
            return
        idx = self.notebook.index("current")
        last = len(self._tab_frames) - 1
        self.back_btn.config(state="disabled" if idx == 0 else "normal")
        self.next_btn.config(
            text=strings.t("btn.next" if idx < last else "btn.save_start", self.lang))

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
        if self.first_run:
            self.back_btn.config(text=strings.t("btn.back", self.lang))
            self.update_rail()
        else:
            self.save_btn.config(text=strings.t("btn.save", self.lang))
            self.cancel_btn.config(text=strings.t("btn.cancel", self.lang))
            self.footer_lbl.config(text=strings.t("footer.next_start", self.lang))

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
        self._render_engine_combo()
        self._render_hotkey_grid()
        self._render_hotkey_status()
        self._render_rail()
        self.root.title(strings.t(
            "app.title.firstrun" if self.first_run else "app.title.settings", self.lang))

    # ------------------------------------------------------------- save / close
    def _save(self, start_after=False):
        # A key is present if one is entered OR one is already stored (a blank field
        # never clobbers a stored key -- settings_io). So an empty field on top of a
        # stored key is NOT keyless, and the "no key" warning must not fire there.
        has_key = bool(self.groq_var.get().strip() or self.soniox_var.get().strip()
                       or self._had_stored_key)
        # Pre-save checks (order matters): no key at all, then hotkey warnings.
        if not has_key:
            if not messagebox.askyesno(strings.t("dlg.nokey.title", self.lang),
                                       strings.t("dlg.nokey.body", self.lang)):
                return
        if self._hotkey_warnings():
            if not messagebox.askyesno(strings.t("dlg.hotkeywarn.title", self.lang),
                                       strings.t("dlg.hotkeywarn.body", self.lang)):
                return

        # Write rule for ui.language: persist iff toggled this session or the file
        # already carried a ui block -- else None, so a no-choice user stays clean.
        ui_lang = self.lang if (self._lang_toggled or self._had_ui_block) else None
        try:
            settings_io.write_env(
                config.SCRIPT_DIR / ".env",
                {"GROQ_API_KEY": self.groq_var.get(),
                 "SONIOX_API_KEY": self.soniox_var.get()},
                example_path=config.SCRIPT_DIR / ".env.example")
            settings_io.write_personal_settings(
                config.SCRIPT_DIR / "personal_settings.json",
                hotkeys_effective=self.hotkeys_state,
                default_api=config.AVAILABLE_APIS[self.engine_index],
                example_path=config.SCRIPT_DIR / "personal_settings.example.json",
                ui_language=ui_lang)
        except Exception as e:
            # Atomic writes + abort-on-unreadable (CP1) mean no file is left half-
            # written or corrupted. .env is written before personal_settings.json, so
            # a failure of the second still leaves the first's (valid) update on disk --
            # hence the message speaks of atomicity, not "nothing was overwritten".
            messagebox.showerror(
                strings.t("dlg.savefail.title", self.lang),
                strings.t("dlg.savefail.body", self.lang) + "\n\n" + str(e))
            return

        self.dirty = False
        # "Save & start" only launches the tool when a key is present: with none, the
        # tool would immediately re-detect no key, relaunch this wizard and exit -- a
        # visible bounce. Just save and close instead; the user stays in control and
        # starts the tool once a key is in place.
        if start_after and has_key and not self._launch_tool():
            # The save already succeeded; say so, then close.
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

    def _on_close(self):
        if self.dirty:
            if not messagebox.askyesno(strings.t("dlg.discard.title", self.lang),
                                       strings.t("dlg.discard.body", self.lang)):
                return
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--first-run", action="store_true",
                        help="open in first-run wizard mode (set by the no-key hook)")
    args, _ = parser.parse_known_args()

    _enable_high_dpi()
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
    except Exception:
        pass
    try:
        ttk.Style().theme_use("vista")   # native Windows look; harmless if absent
    except Exception:
        pass
    # Size before constructing the app so the geometry is in place even if __init__
    # pops a modal load-failure dialog; the sizing is pure DPI + screen, no widgets
    # needed.
    _size_window(root)

    SettingsApp(root, first_run=args.first_run)
    root.mainloop()


if __name__ == "__main__":
    main()
