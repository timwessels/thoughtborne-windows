"""
Externalized UI strings (DE/EN) for the settings/onboarding app (#144).

Pure stdlib: two flat string tables (`_EN`, `_DE`) keyed by dotted lookup keys,
plus `t(key, lang)` with a lang -> EN -> key-itself fallback chain. Kept apart
from `thoughtborne_settings.py` so the GUI holds no prose and both languages are
maintained side by side; `test_settings_io.py` imports this module off-Windows to
assert the two tables carry the exact same key set (a missing translation is a
test failure, not a silent English leak).

The app starts in English unless a stored `ui.language` says otherwise (D-015):
the tool itself speaks English, so the settings default matches; German is one
header-toggle away and the choice self-persists. The system display language is
deliberately not consulted (the `detect_ui_language()` that once did is gone),
which also keeps this module pure string tables -- importing it never touches
ctypes, and the i18n completeness test depends on that.

Two wording contracts are load-bearing (mirror them if either source changes):
  - `engine.desc.*` EN equals `config.API_DISPLAY[api]["descriptor"]` (one engine
    descriptor, two surfaces -- the console lineup and the settings engine radios).
  - the hotkey-validator detail surfaced under `capture.invalid` /
    `hotkeys.status.warn_prefix` stays English in both languages (it is the same
    text `thoughtborne.log` prints); the localized headline carries the meaning.
"""

# =============================================================================
# String tables. _EN and _DE MUST carry the identical key set (verified by
# test_settings_io.check_i18n). Keys are grouped as in the spec's string table;
# `action.*` keys cover config.DEFAULT_HOTKEYS' 12 action names.
# =============================================================================
_EN = {
    # ---- shell & rail ----
    "app.title.settings": "Thoughtborne Settings",
    "app.title.firstrun": "Thoughtborne Setup",
    "welcome.heading": "Welcome to Thoughtborne",
    "welcome.sub": ("Let's get you set up — an API key, your hotkeys, a few "
                    "preferences. Everything here can be changed later in this "
                    "same window."),
    "lang.de": "Deutsch",
    "lang.en": "English",
    "btn.back": "Back",
    "btn.next": "Next",
    "btn.save": "Save",
    "btn.cancel": "Cancel",
    "btn.save_close": "Save & close",
    "btn.save_restart": "Save & restart",
    "btn.restarting": "Restarting…",

    # ---- welcome / overview tab ----
    "welcome.tab": "Overview",
    "welcome.intro.heading": "What Thoughtborne does",
    "welcome.intro.body": (
        "Thoughtborne turns speech into text in any Windows program. You dictate; "
        "the transcript appears right where your cursor is, as if you had typed it. "
        "It is built above all for talking to AI — the text should be good enough to "
        "send to a language model unread."),
    "welcome.loop.body": (
        "The whole loop: click into a text field, press {start}, speak, then press "
        "{stop} — and the text is there. It works in every application."),
    "welcome.loop.link": "▸ How you dictate, in detail",
    "welcome.console.heading": "The console is a status display",
    "welcome.console.body": (
        "The black console window is a status monitor — it shows what Thoughtborne "
        "is doing while you dictate. You control everything through hotkeys, so in "
        "normal use you never type into it; dictation keeps working even with the "
        "window hidden. The one exception is a failed start: then the console asks "
        "you to press Enter to close it."),
    "welcome.byok.link": "▸ Provider & API key",
    "welcome.next.heading": "Set things up",
    "welcome.step1.heading": "1 — Get an API key",
    "welcome.step1.body": (
        "This is the one thing dictation can't work without. Thoughtborne has no "
        "subscription and no middleman — you use your own account at a "
        "transcription provider and pay only for what you use. Two lanes, and one "
        "key is enough to start: Groq is free, so you can try Thoughtborne without "
        "paying anyone; Soniox is the quality lane and carries the default engine. "
        "The Provider tab walks you through getting a key and pasting it in."),
    "welcome.step2.heading": "2 — Choose your hotkeys",
    "welcome.step2.body": (
        "The shipped Ctrl+Alt scheme is the author's own setup — built for "
        "one-handed dictation. Keep it, or change any combo on the Hotkeys tab."),
    "welcome.step3.heading": "3 — Optional: Startup & windows",
    "welcome.step3.body": (
        "Optional polish: which engine Thoughtborne starts on, and how to tuck the "
        "console into the tray."),
    "welcome.link.hotkeys": "▸ Hotkeys",
    "welcome.link.behavior": "▸ Startup & windows",
    "welcome.link.readme": "▸ Full README (opens in your browser)",
    "url.readme": "https://github.com/timwessels/thoughtborne-windows",

    # ---- provider tab ----
    "provider.tab": "Provider & API key",
    "provider.keys.heading": "What is an API key?",
    "provider.keys.body": (
        "An API key is a personal access code for a cloud service — a long string "
        "of characters, created once in the provider's web console and pasted in "
        "below. Thoughtborne uses it to send your recordings to the transcription "
        "service: usage runs on your own account, directly with the provider — no "
        "middleman, no subscription. The key is stored only on this PC, in the "
        ".env file in the Thoughtborne folder, and is sent nowhere except to the "
        "provider it belongs to."),
    "provider.lanes.body": (
        "One key is enough to start. Groq is the free lane — transcription on "
        "Groq's free tier costs nothing, so you can try Thoughtborne without "
        "paying anyone. Soniox is the quality lane and carries the default engine "
        "(Soniox Live). With both keys, all four engines are available, switchable "
        "while dictating (Ctrl+Alt+L)."),
    "provider.groq.heading": "Groq — the free lane",
    "provider.groq.body": (
        "Free for transcription, no credit card needed (as of July 2026). Sign up "
        "at console.groq.com, create a key on the API-keys page and paste it here "
        "— it is shown only once, so copy it right away. The free tier's limits — "
        "about 2 hours of audio per clock-hour and 8 per day, shared across your "
        "Groq account — leave plenty of room for personal dictation. Powers the "
        "two Groq Whisper engines."),
    "provider.soniox.heading": "Soniox — the quality lane (default engine)",
    "provider.soniox.body": (
        "Pay-as-you-go, no subscription (as of July 2026): $0.12 per hour of audio "
        "on the real-time default (Soniox Live), $0.10 on file uploads — billed by "
        "actual use (you pay only for the audio you send). Around 25 hours of "
        "dictation a month comes to about $3. New accounts get no free starting "
        "credit: after signing up at console.soniox.com, first top up a small "
        "balance in the console — the key alone won't transcribe yet. Powers the "
        "two Soniox engines."),
    "provider.field.groq": "Groq API key",
    "provider.field.soniox": "Soniox API key",
    "provider.reveal.show": "Show",
    "provider.reveal.hide": "Hide",
    "provider.keep_note": (
        "Saving never deletes a key — a cleared field leaves the stored key "
        "untouched. To remove a key, edit the .env file directly."),
    "btn.test_key": "Test key",
    "test.testing": "Testing…",
    "test.valid": "Key works",
    "test.valid.soniox_balance": (
        "This checks the key, not your balance — a new Soniox account must add "
        "credit first, or the first dictation fails with a payment error."),
    "test.invalid": "Key rejected — check for typos, or copy it fresh from the console.",
    "test.inconclusive": (
        "The server refused the check — that's not a network problem, and not a "
        "verdict on the key: it may well work for dictation. Saving works anyway; "
        "the key just wasn't verified."),
    "test.unreachable": (
        "Couldn't reach the server — check your internet connection. Saving works "
        "anyway; the key just wasn't verified."),
    "url.groq_keys": "https://console.groq.com/keys",
    "url.soniox_console": "https://console.soniox.com",

    # ---- hotkeys tab ----
    "hotkeys.tab": "Hotkeys",
    "hotkeys.intro": (
        "Thoughtborne is driven by global hotkeys — they work in every "
        "application, whatever has focus; in return, each combo is reserved "
        "system-wide while the tool runs. Below you first decide whether "
        "push-to-talk joins them as a second way to dictate; then pick a preset as "
        "the base and change any single action if a combo clashes with something "
        "you use."),
    "hotkeys.ptt.heading": "Push-to-talk — hold a key while you speak",
    "hotkeys.ptt.body": (
        "A second way to dictate, made for short bursts: tap the left Ctrl key, let "
        "go, then press it again right away and hold it. Recording runs for as long "
        "as you keep it held, and letting go inserts the transcript at the cursor — "
        "the same delivery the stop hotkeys perform. The first tap is what makes it "
        "safe: a single Ctrl press never starts anything, so Ctrl+C, Ctrl+V and "
        "every other Ctrl combination are left alone. On a German keyboard AltGr is "
        "filtered out as well (Windows reports it as Ctrl+Alt), so typing "
        "@ \\ { } [ ] | € ~ never starts a recording."),
    "hotkeys.ptt.off": "Off — dictate with the hotkeys only",
    "hotkeys.ptt.on": "On — double-tap and hold Ctrl to dictate",
    "hotkeys.ptt.fine": (
        "Off by default: while it is on, Thoughtborne watches every Ctrl press. The "
        "trigger key (left Ctrl, right Ctrl or left Alt), how the text is inserted "
        "and the three timings stay hand-editable in the \"push_to_talk\" block of "
        "personal_settings.json. One collision worth knowing: in JetBrains IDEs a "
        "double Ctrl opens \"Run Anything\" — enable \"Disable double modifier key "
        "shortcuts\" in the IDE's Advanced Settings if that gets in the way."),
    "hotkeys.presets.heading": "Two presets to choose from",
    "hotkeys.preset.ctrl_alt.title": "Ctrl+Alt letters — the shipped default",
    "hotkeys.preset.ctrl_alt.body": (
        "Hold Ctrl+Alt and press a letter: W starts the recording, A/D/H/Y deliver "
        "the transcript, X cancels. This is the author's own setup — Ctrl+Alt "
        "combinations are rarely claimed by other Windows programs, and they all "
        "sit on the left half of the keyboard, so start and stop work one-handed "
        "while the right hand stays on the mouse. Works on every keyboard, laptops "
        "included, and stays clear of the F-key row that IDEs use for debugging — "
        "the safe all-round choice. The F-keys preset below goes one step further: "
        "recording on a single keypress. Applying this preset is also the way back "
        "to the defaults."),
    "hotkeys.preset.fkeys.title": "F-keys — one keypress, no chord",
    "hotkeys.preset.fkeys.body": (
        "Three F-keys, three families: F8 engine, F9 record, F10 deliver. The bare "
        "key is the everyday move (F9 starts, F10 inserts), Ctrl the important "
        "sibling (cancel, send, switch engine), Ctrl+Alt the rare technical one; "
        "housekeeping keys (history, self-test, exit) stay the same as in the "
        "letter preset."),
    "hotkeys.preset.fkeys.caveat": (
        "One caveat: the F5–F11 band carries debug actions in most IDEs — if you "
        "debug a lot, stay on the letter preset or override single keys. On a "
        "laptop, you may need to enable Fn-Lock (usually Fn+Esc) so F9/F10 fire "
        "directly."),
    "btn.use_preset": "Use this preset",
    "hotkeys.custom.heading": "Individual actions",
    "hotkeys.custom.body": (
        "Click Change next to an action, then press the new combo. Letters, "
        "digits, F1–F24 and the ü key work — F-keys also bare, everything else "
        "with Ctrl and/or Alt."),
    "hotkeys.col.action": "Action",
    "hotkeys.col.combo": "Shortcut",
    "hotkeys.capture_limit": (
        "While Thoughtborne is running, a combo it already holds can't be captured "
        "here — Windows fires the action instead of passing the key press through. "
        "Quit Thoughtborne ({exit_key}) first, or pick a combo it doesn't use."),
    "btn.change_key": "Change…",
    "hotkeys.more_suffix": "(+{n} more)",
    "capture.prompt": "Press the combo … (Esc)",
    "capture.unbindable": "This key can't be bound — use letters, digits, F1–F24 or ü.",
    "capture.need_modifier": (
        "Letters and digits need Ctrl and/or Alt — only F-keys work bare."),
    "capture.invalid": "Not a usable combo ({detail}).",
    "capture.collision": "Already used by: {action}",
    "hotkeys.status.ok": "All hotkeys are valid — no collisions.",
    "hotkeys.status.warn_prefix": "Hotkey problems (the defaults stay for these):",
    "action.start_recording": "Start recording",
    "action.stop_recording_keyboard": "Stop + insert (simulated typing)",
    "action.stop_recording_clipboard": "Stop + insert (clipboard paste)",
    "action.stop_recording_send": "Stop + insert + Enter (send)",
    "action.stop_recording_no_insert": "Stop + transcribe only (insert later)",
    "action.cancel_recording": "Cancel recording",
    "action.retry_last_failed": "Retry last failed transcription",
    "action.switch_api": "Switch engine",
    "action.open_history": "Open history folder",
    "action.open_settings": "Open settings (gear)",
    "action.test_transcription": "Self-test (bundled test audio)",
    "action.exit_program": "Exit Thoughtborne",

    # ---- behavior tab ----
    "behavior.tab": "Startup & windows",
    "behavior.engine.heading": "Engine at startup",
    "behavior.engine.body": (
        "How Thoughtborne chooses its engine at startup — pick one of two modes. "
        "Keep starting on the engine you last switched to with Ctrl+Alt+L "
        "(Thoughtborne remembers it for you), or always start on one fixed engine "
        "no matter what you switch to while dictating. Either way, Ctrl+Alt+L still "
        "cycles through all four engines at any time while the tool runs. The "
        "engines differ mainly in speed versus tidiness; the model lineup in the "
        "README has the details."),
    "behavior.engine.mode.remember": "Start with the engine I last switched to (Ctrl+Alt+L)",
    "behavior.engine.mode.fixed": "Always start with:",
    "behavior.engine.remember.current": "Currently remembered: {engine}",
    "behavior.engine.remember.none": (
        "No switch recorded yet — starts on the built-in default ({engine})"),
    "behavior.engine.keyless": (
        "▸  Enter an API key on the “Provider & API key” tab to enable dictation."),
    "engine.desc.soniox-live": "verbatim, instant",
    "engine.desc.soniox": "polished, takes longer",
    "engine.desc.groq-large": "accurate, free",
    "engine.desc.groq": "fast, free",
    "behavior.vocab.heading": "Recognition vocabulary",
    "behavior.vocab.body": (
        "Recurring proper names, technical terms or acronyms are recognized more "
        "reliably on the Soniox engines once you list them: edit the \"vocabulary\" "
        "section of personal_settings.json in the Thoughtborne folder. The Groq "
        "engines ignore it."),
    "behavior.tray.heading": "Console out of the taskbar (tray)",
    "behavior.tray.body": (
        "The console is a status monitor — dictation keeps working with the window "
        "hidden. If the console runs in Windows Terminal (the default on Windows "
        "11), two of Terminal's own settings move it to the tray: open Terminal's "
        "settings (Ctrl+,), go to Interaction, and enable both \"Hide Terminal in "
        "the notification area when it is minimized\" and \"Always display an icon "
        "in the notification area\"."),
    "behavior.tray.body2": (
        "Minimizing then sends the window to the tray; "
        "one click on the tray icon brings it back (Windows first parks new tray "
        "icons behind the ^ chevron — drag the icon into the visible tray once)."),
    "behavior.tray.body3": (
        "Two honest limits: both toggles affect every Windows Terminal window, and "
        "they don't exist under the classic conhost. Thoughtborne deliberately "
        "doesn't change Terminal's settings for you — the button below just takes "
        "you there."),
    "btn.open_terminal": "Open Windows Terminal",
    "behavior.tray.no_wt": (
        "Windows Terminal was not found on this system — the tray route needs it "
        "(free in the Microsoft Store)."),
    "behavior.admin.heading": "Dictating into admin windows",
    "behavior.admin.body": (
        "Hotkeys and text insertion can't reach a window that runs as "
        "administrator — Windows blocks input from non-elevated processes there. "
        "The fix is to start Thoughtborne itself elevated; the short recipe is in "
        "the README under Troubleshooting."),
    "behavior.admin.link": "README — Troubleshooting",
    "url.admin_recipe": "https://github.com/timwessels/thoughtborne-windows#troubleshooting",

    # ---- done / closing tab ----
    "done.tab": "How you dictate",
    "done.heading.firstrun": "Done — here's how you dictate",
    "done.heading.settings": "How you dictate",
    "done.loop.body": (
        "Click into any text field, press {start}, speak, then press {stop} — the "
        "transcript appears at the cursor. It works in every application; the "
        "console only shows what is happening."),
    "done.controls.body": (
        "{exit_key} quits Thoughtborne. {settings_key} reopens this settings window "
        "while the tool is running."),
    "done.startkey.body": (
        "To start Thoughtborne with one key press: in the Start menu, right-click "
        "the Thoughtborne entry → Properties → click the \"Shortcut key\" field and "
        "press a free combo (e.g. Ctrl+Alt+1). Windows honors it only for Start-menu "
        "and Desktop shortcuts."),
    "done.threewindow.body": (
        "Any leftover black setup window can be closed — Thoughtborne runs from its "
        "own console."),

    # ---- dialogs & warnings ----
    "dlg.nokey.title": "No API key",
    "dlg.nokey.body": (
        "No API key is entered, and none was found on this PC — Thoughtborne can't "
        "transcribe without one. Your hotkeys and preferences will still be saved. "
        "You can add a key any time — reopen settings from the running tool with "
        "Ctrl+Alt+G. Save and close now?"),
    "dlg.hotkeywarn.title": "Hotkey problems",
    "dlg.hotkeywarn.body": (
        "Some hotkeys would be ignored at startup — the defaults stay in force for "
        "them (details on the Hotkeys tab). Save anyway?"),
    "dlg.savefail.title": "Saving failed",
    "dlg.savefail.body": (
        "The settings could not be saved. Each file is written atomically — swapped "
        "in only once complete — so none is left half-written or corrupted; a file "
        "may be locked or unreadable. The technical detail:"),
    "dlg.loadfail.title": "Settings couldn't be read",
    "dlg.loadfail.body": (
        "Your saved settings could not be read — Thoughtborne opened with the "
        "defaults, and nothing has been changed. A file may be locked or in an "
        "unexpected encoding; saving stays blocked until it can be read again. The "
        "technical detail:"),
    "dlg.startfail.title": "Start failed",
    "dlg.startfail.body": (
        "The settings were saved, but Thoughtborne could not be started from here "
        "— start it via Thoughtborne.bat."),
    "dlg.restarttimeout.title": "Not restarted",
    "dlg.restarttimeout.body": (
        "Your settings were saved. But Thoughtborne did not close within a few "
        "seconds, so it was not restarted — the changes take effect the next time it "
        "starts. Please close or restart it yourself (by default Ctrl+Alt+4 in its "
        "console, or close its window)."),
    "dlg.restartfail.title": "Not restarted",
    "dlg.restartfail.body": (
        "Your settings were saved, but the restart request could not be written, so "
        "Thoughtborne keeps running as it is. The changes take effect the next time "
        "it starts — please restart it yourself."),
    "warn.corrupt": (
        "personal_settings.json exists but could not be parsed — saving from here "
        "will replace it with a clean file. To rescue hand-edited content (e.g. "
        "vocabulary), fix the file in a text editor first."),
}

_DE = {
    # ---- shell & rail ----
    "app.title.settings": "Thoughtborne-Einstellungen",
    "app.title.firstrun": "Thoughtborne-Einrichtung",
    "welcome.heading": "Willkommen bei Thoughtborne",
    "welcome.sub": ("Kurz einrichten — ein API-Key, die Hotkeys, ein paar "
                    "Einstellungen. Alles hier lässt sich später in genau diesem "
                    "Fenster wieder ändern."),
    "lang.de": "Deutsch",
    "lang.en": "English",
    "btn.back": "Zurück",
    "btn.next": "Weiter",
    "btn.save": "Speichern",
    "btn.cancel": "Abbrechen",
    "btn.save_close": "Speichern & schließen",
    "btn.save_restart": "Speichern & neu starten",
    "btn.restarting": "Wird neu gestartet…",

    # ---- welcome / overview tab ----
    "welcome.tab": "Überblick",
    "welcome.intro.heading": "Was Thoughtborne macht",
    "welcome.intro.body": (
        "Thoughtborne macht aus Sprache Text — in jedem Windows-Programm. Du "
        "diktierst, und das Transkript erscheint genau dort, wo dein Cursor steht, "
        "als hättest du es getippt. Gebaut ist es vor allem fürs Sprechen mit KI: "
        "Der Text soll gut genug sein, um ihn ungelesen an ein Sprachmodell zu "
        "schicken."),
    "welcome.loop.body": (
        "Der ganze Ablauf: in ein Textfeld klicken, {start} drücken, sprechen, dann "
        "{stop} drücken — und der Text steht da. Das funktioniert in jeder "
        "Anwendung."),
    "welcome.loop.link": "▸ So diktierst du, ausführlich",
    "welcome.console.heading": "Die Konsole ist eine Statusanzeige",
    "welcome.console.body": (
        "Das schwarze Konsolenfenster ist ein Status-Monitor — es zeigt, was "
        "Thoughtborne gerade tut, während du diktierst. Gesteuert wird alles über "
        "Hotkeys; im normalen Betrieb tippst du also nie hinein, und Diktieren "
        "funktioniert auch bei verstecktem Fenster. Die einzige Ausnahme ist ein "
        "fehlgeschlagener Start: Dann bittet die Konsole dich, zum Schließen Enter "
        "zu drücken."),
    "welcome.byok.link": "▸ Anbieter & API-Key",
    "welcome.next.heading": "Jetzt einrichten",
    "welcome.step1.heading": "1 — Einen API-Key holen",
    "welcome.step1.body": (
        "Ohne ihn funktioniert Diktieren nicht — er ist die einzige Voraussetzung. "
        "Thoughtborne hat kein Abo und keinen Zwischenhändler — du nutzt dein "
        "eigenes Konto bei einem Transkriptionsanbieter und zahlst nur, was du "
        "verbrauchst. Zwei Wege, ein Key genügt zum Start: Groq ist kostenlos, "
        "damit lässt sich Thoughtborne ausprobieren, ohne jemanden zu bezahlen; "
        "Soniox ist der Qualitäts-Weg und trägt die Standard-Engine. Der "
        "Anbieter-Tab führt dich durch — Key holen und eintragen."),
    "welcome.step2.heading": "2 — Hotkeys wählen",
    "welcome.step2.body": (
        "Das mitgelieferte Ctrl+Alt-Schema ist das eigene Setup des Autors — "
        "gebaut für einhändiges Diktieren. Übernehmen oder auf dem Hotkeys-Tab "
        "jede Kombi anpassen."),
    "welcome.step3.heading": "3 — Optional: Start & Fenster",
    "welcome.step3.body": (
        "Optionaler Feinschliff: mit welcher Engine Thoughtborne startet und wie "
        "du die Konsole in den Tray schickst."),
    "welcome.link.hotkeys": "▸ Hotkeys",
    "welcome.link.behavior": "▸ Start & Fenster",
    "welcome.link.readme": "▸ Vollständiges README (öffnet im Browser)",
    "url.readme": ("https://github.com/timwessels/thoughtborne-windows/blob/"
                   "main/README.de.md"),

    # ---- provider tab ----
    "provider.tab": "Anbieter & API-Key",
    "provider.keys.heading": "Was ist ein API-Key?",
    "provider.keys.body": (
        "Ein API-Key ist ein persönlicher Zugangscode für einen Cloud-Dienst — "
        "eine lange Zeichenkette, die man einmal in der Web-Konsole des Anbieters "
        "erstellt und unten einfügt. Thoughtborne schickt damit die Aufnahmen an "
        "den Transkriptionsdienst: Die Nutzung läuft über das eigene Konto, direkt "
        "beim Anbieter — kein Zwischenhändler, kein Abo. Der Key liegt nur auf "
        "diesem PC, in der Datei .env im Thoughtborne-Ordner, und geht an niemanden "
        "außer an den Anbieter, zu dem er gehört."),
    "provider.lanes.body": (
        "Ein Key genügt für den Start. Groq ist der kostenlose Weg — Transkription "
        "im Free Tier von Groq kostet nichts, damit lässt sich Thoughtborne "
        "ausprobieren, ohne irgendwen zu bezahlen. Soniox ist der Qualitäts-Weg "
        "und trägt die Standard-Engine (Soniox Live). Mit beiden Keys stehen alle "
        "vier Engines bereit, umschaltbar beim Diktieren (Ctrl+Alt+L)."),
    "provider.groq.heading": "Groq — der kostenlose Weg",
    "provider.groq.body": (
        "Für Transkription kostenlos, keine Kreditkarte nötig (Stand Juli 2026). "
        "Auf console.groq.com registrieren, auf der API-Keys-Seite einen Key "
        "erstellen und hier einfügen — er wird nur einmal angezeigt, also gleich "
        "kopieren. Die Free-Tier-Limits — rund 2 Stunden Audio pro Stunde und 8 "
        "pro Tag, geteilt über das ganze Groq-Konto — lassen fürs persönliche "
        "Diktieren viel Luft. Versorgt die beiden Groq-Whisper-Engines."),
    "provider.soniox.heading": "Soniox — der Qualitäts-Weg (Standard-Engine)",
    "provider.soniox.body": (
        "Zahlung nach Verbrauch, kein Abo (Stand Juli 2026): 0,12 $ pro Stunde "
        "Audio beim Echtzeit-Default (Soniox Live), 0,10 $ bei Datei-Uploads — "
        "abgerechnet nach tatsächlicher Nutzung (du zahlst nur für das gesendete "
        "Audio). Rund 25 Stunden Diktat im Monat ergeben etwa 3 $. Neue Konten "
        "bekommen kein Startguthaben: Nach der Registrierung auf console.soniox.com "
        "zuerst in der Console ein kleines Guthaben aufladen — der Key allein "
        "transkribiert noch nicht. Versorgt die beiden Soniox-Engines."),
    "provider.field.groq": "Groq-API-Key",
    "provider.field.soniox": "Soniox-API-Key",
    "provider.reveal.show": "Anzeigen",
    "provider.reveal.hide": "Verbergen",
    "provider.keep_note": (
        "Speichern löscht nie einen Key — ein geleertes Feld lässt den "
        "gespeicherten Key unangetastet. Zum Entfernen den Key direkt in der Datei "
        ".env löschen."),
    "btn.test_key": "Key testen",
    "test.testing": "Teste…",
    "test.valid": "Key funktioniert",
    "test.valid.soniox_balance": (
        "Das prüft den Key, nicht dein Guthaben — ein neues Soniox-Konto muss erst "
        "Guthaben aufladen, sonst scheitert das erste Diktat mit einem Zahlungsfehler."),
    "test.invalid": "Key abgelehnt — auf Tippfehler prüfen oder frisch aus der Console kopieren.",
    "test.inconclusive": (
        "Der Server hat die Prüfung abgelehnt — das ist kein Netzwerkproblem und kein "
        "Urteil über den Key: Zum Diktieren kann er trotzdem funktionieren. Speichern "
        "geht ohnehin; der Key wurde nur nicht geprüft."),
    "test.unreachable": (
        "Server nicht erreichbar — Internetverbindung prüfen. Speichern geht "
        "trotzdem; der Key wurde nur nicht geprüft."),
    "url.groq_keys": "https://console.groq.com/keys",
    "url.soniox_console": "https://console.soniox.com",

    # ---- hotkeys tab ----
    "hotkeys.tab": "Hotkeys",
    "hotkeys.intro": (
        "Thoughtborne wird über globale Hotkeys gesteuert — sie "
        "funktionieren in jeder Anwendung, egal was gerade den Fokus hat; dafür "
        "ist jede Kombination systemweit reserviert, solange das Tool läuft. "
        "Darunter zuerst die Entscheidung, ob Push-to-talk als zweiter Weg zum "
        "Diktieren dazukommt; danach als Basis ein Preset wählen und einzelne "
        "Aktionen ändern, falls eine Kombination mit etwas kollidiert, das man "
        "nutzt."),
    "hotkeys.ptt.heading": "Push-to-talk — beim Sprechen eine Taste halten",
    "hotkeys.ptt.body": (
        "Ein zweiter Weg zu diktieren, gedacht für kurze Einwürfe: die linke "
        "Ctrl-Taste kurz antippen, loslassen, dann gleich darauf erneut drücken und "
        "halten. Solange sie gehalten wird, läuft die Aufnahme; beim Loslassen wird "
        "das Transkript an der Cursorposition eingefügt — genau wie bei den "
        "Stopp-Hotkeys. Das erste Antippen ist der Schutz: ein einzelner "
        "Ctrl-Druck löst nie etwas aus, Ctrl+C, Ctrl+V und jede andere "
        "Ctrl-Kombination bleiben unberührt. Auf deutschen Tastaturen wird "
        "zusätzlich AltGr herausgefiltert (Windows meldet es als Ctrl+Alt), sodass "
        "@ \\ { } [ ] | € ~ nie eine Aufnahme starten."),
    "hotkeys.ptt.off": "Aus — nur über die Hotkeys diktieren",
    "hotkeys.ptt.on": "An — Ctrl doppelt tippen und halten zum Diktieren",
    "hotkeys.ptt.fine": (
        "Standardmäßig aus: solange es an ist, wertet Thoughtborne jeden Ctrl-Druck "
        "aus. Trigger-Taste (linke Ctrl, rechte Ctrl oder linke Alt), der "
        "Einfüge-Weg und die drei Zeitschwellen bleiben im Block „push_to_talk“ in "
        "personal_settings.json von Hand einstellbar. Eine Kollision, die man kennen "
        "sollte: In JetBrains-IDEs öffnet doppeltes Ctrl „Run Anything“ — wenn das "
        "stört, in den Advanced Settings der IDE „Disable double modifier key "
        "shortcuts“ aktivieren."),
    "hotkeys.presets.heading": "Zwei Presets zur Wahl",
    "hotkeys.preset.ctrl_alt.title": "Ctrl+Alt-Buchstaben — der Auslieferungszustand",
    "hotkeys.preset.ctrl_alt.body": (
        "Ctrl+Alt halten und einen Buchstaben drücken: W startet die Aufnahme, "
        "A/D/H/Y liefern das Transkript ab, X bricht ab. Das ist das eigene Setup "
        "des Autors — Ctrl+Alt-Kombinationen sind von anderen Windows-Programmen "
        "selten belegt, und sie liegen alle auf der linken Tastaturhälfte: Start "
        "und Stopp gehen einhändig, die rechte Hand bleibt an der Maus. "
        "Funktioniert auf jeder Tastatur, Laptops eingeschlossen, und lässt die "
        "F-Tasten-Reihe frei, die IDEs fürs Debuggen nutzen — die sichere "
        "Allround-Wahl. Das F-Tasten-Preset unten geht noch einen Schritt weiter: "
        "Aufnahme mit einem einzelnen Tastendruck. Dieses Preset anzuwenden ist "
        "zugleich der Weg zurück zu den Defaults."),
    "hotkeys.preset.fkeys.title": "F-Tasten — ein Tastendruck statt Griff",
    "hotkeys.preset.fkeys.body": (
        "Drei F-Tasten, drei Familien: F8 Engine, F9 Aufnahme, F10 Abliefern. Die "
        "blanke Taste ist der Alltag (F9 startet, F10 fügt ein), Ctrl der wichtige "
        "Geschwister-Fall (abbrechen, senden, Engine wechseln), Ctrl+Alt der "
        "seltene technische; die Verwaltungs-Tasten (History, Selbsttest, Beenden) "
        "bleiben wie im Buchstaben-Preset."),
    "hotkeys.preset.fkeys.caveat": (
        "Ein Vorbehalt: Das Band F5–F11 ist in "
        "den meisten IDEs mit Debug-Aktionen belegt — wer viel debuggt, bleibt "
        "beim Buchstaben-Preset oder passt einzelne Tasten an. Auf dem Laptop ggf. "
        "Fn-Lock aktivieren (meist Fn+Esc), damit F9/F10 direkt feuern."),
    "btn.use_preset": "Dieses Preset übernehmen",
    "hotkeys.custom.heading": "Einzelne Aktionen",
    "hotkeys.custom.body": (
        "Neben einer Aktion auf Ändern klicken und die neue Kombination drücken. "
        "Buchstaben, Ziffern, F1–F24 und die ü-Taste funktionieren — F-Tasten auch "
        "blank, alles andere mit Ctrl und/oder Alt."),
    "hotkeys.col.action": "Aktion",
    "hotkeys.col.combo": "Tastenkombination",
    "hotkeys.capture_limit": (
        "Solange Thoughtborne läuft, lässt sich eine bereits belegte Kombination "
        "hier nicht aufnehmen — Windows löst stattdessen die Aktion aus, statt den "
        "Tastendruck durchzureichen. Vorher Thoughtborne beenden ({exit_key}) oder "
        "eine unbelegte Kombination wählen."),
    "btn.change_key": "Ändern…",
    "hotkeys.more_suffix": "(+{n} weitere)",
    "capture.prompt": "Kombination drücken … (Esc)",
    "capture.unbindable": (
        "Diese Taste lässt sich nicht belegen — Buchstaben, Ziffern, F1–F24 oder ü "
        "verwenden."),
    "capture.need_modifier": (
        "Buchstaben und Ziffern brauchen Ctrl und/oder Alt — nur F-Tasten gehen "
        "ohne."),
    "capture.invalid": "Keine verwendbare Kombination ({detail}).",
    "capture.collision": "Schon vergeben an: {action}",
    "hotkeys.status.ok": "Alle Hotkeys sind gültig — keine Kollisionen.",
    "hotkeys.status.warn_prefix": "Hotkey-Probleme (für diese bleiben die Defaults):",
    "action.start_recording": "Aufnahme starten",
    "action.stop_recording_keyboard": "Stopp + einfügen (simuliertes Tippen)",
    "action.stop_recording_clipboard": "Stopp + einfügen (Zwischenablage)",
    "action.stop_recording_send": "Stopp + einfügen + Enter (senden)",
    "action.stop_recording_no_insert": "Stopp + nur transkribieren (später einfügen)",
    "action.cancel_recording": "Aufnahme abbrechen",
    "action.retry_last_failed": "Letzte fehlgeschlagene Transkription wiederholen",
    "action.switch_api": "Engine wechseln",
    "action.open_history": "History-Ordner öffnen",
    "action.open_settings": "Einstellungen öffnen (Zahnrad)",
    "action.test_transcription": "Selbsttest (mitgeliefertes Test-Audio)",
    "action.exit_program": "Thoughtborne beenden",

    # ---- behavior tab ----
    "behavior.tab": "Start & Fenster",
    "behavior.engine.heading": "Engine beim Start",
    "behavior.engine.body": (
        "Wie Thoughtborne beim Start seine Engine wählt — zwei Modi zur Wahl. "
        "Entweder mit der Engine starten, auf die du zuletzt per Ctrl+Alt+L "
        "gewechselt hast (Thoughtborne merkt sie sich), oder immer mit einer festen "
        "Engine starten, egal was du beim Diktieren umschaltest. In beiden Fällen "
        "wechselt Ctrl+Alt+L im laufenden Betrieb jederzeit durch alle vier Engines. "
        "Die Engines unterscheiden sich vor allem in Tempo gegen Sauberkeit; Details "
        "in der Modell-Aufstellung im README."),
    "behavior.engine.mode.remember": "Mit der zuletzt per Ctrl+Alt+L gewählten Engine starten",
    "behavior.engine.mode.fixed": "Immer starten mit:",
    "behavior.engine.remember.current": "Zurzeit gemerkt: {engine}",
    "behavior.engine.remember.none": (
        "Noch kein Wechsel gemerkt — startet auf dem Standard ({engine})"),
    "behavior.engine.keyless": (
        "▸  Trage auf dem Tab „Anbieter & API-Key“ einen API-Key ein, "
        "um das Diktieren zu aktivieren."),
    "engine.desc.soniox-live": "wortgetreu, sofort fertig",
    "engine.desc.soniox": "poliert, braucht länger",
    "engine.desc.groq-large": "genau, kostenlos",
    "engine.desc.groq": "schnell, kostenlos",
    "behavior.vocab.heading": "Erkennungs-Vokabular",
    "behavior.vocab.body": (
        "Wiederkehrende Eigennamen, Fachbegriffe oder Abkürzungen erkennen die "
        "Soniox-Engines zuverlässiger, wenn sie hinterlegt sind: dazu den Abschnitt "
        "„vocabulary“ in personal_settings.json im Thoughtborne-Ordner bearbeiten. "
        "Die Groq-Engines ignorieren ihn."),
    "behavior.tray.heading": "Konsole aus der Taskleiste (Tray)",
    "behavior.tray.body": (
        "Die Konsole ist ein Status-Monitor — Diktieren funktioniert auch bei "
        "verstecktem Fenster. Läuft die Konsole in Windows Terminal (dem Standard "
        "unter Windows 11), erledigen zwei von Terminals eigenen Einstellungen den "
        "Umzug in den Tray: Terminals Einstellungen öffnen (Ctrl+,), zu "
        "Interaktion gehen und beide aktivieren — „Terminal bei Minimierung im "
        "Infobereich ausblenden“ und „Immer ein Symbol im Infobereich anzeigen“."),
    "behavior.tray.body2": (
        "Minimieren schickt das Fenster dann in den Tray; ein Klick aufs Tray-Icon "
        "holt es zurück (neue Tray-Icons parkt Windows zunächst hinter dem "
        "^-Ausklappmenü — das Icon einmal in den sichtbaren Bereich ziehen)."),
    "behavior.tray.body3": (
        "Zwei "
        "ehrliche Grenzen: Beide Schalter wirken auf jedes Windows-Terminal-"
        "Fenster, und unter dem klassischen conhost gibt es sie nicht. "
        "Thoughtborne ändert Terminals Einstellungen bewusst nicht selbst — der "
        "Button unten führt nur hin."),
    "btn.open_terminal": "Windows Terminal öffnen",
    "behavior.tray.no_wt": (
        "Windows Terminal wurde auf diesem System nicht gefunden — der Tray-Weg "
        "braucht es (kostenlos im Microsoft Store)."),
    "behavior.admin.heading": "In Admin-Fenster diktieren",
    "behavior.admin.body": (
        "Hotkeys und Text-Einfügung erreichen kein Fenster, das als Administrator "
        "läuft — Windows blockiert dort Eingaben von nicht-erhöhten Prozessen. Die "
        "Lösung: Thoughtborne selbst mit erhöhten Rechten starten; das kurze "
        "Rezept steht im README unter Troubleshooting."),
    "behavior.admin.link": "README — Troubleshooting",
    "url.admin_recipe": ("https://github.com/timwessels/thoughtborne-windows/blob/"
                         "main/README.de.md#troubleshooting"),

    # ---- done / closing tab ----
    "done.tab": "So diktierst du",
    "done.heading.firstrun": "Fertig — so diktierst du",
    "done.heading.settings": "So diktierst du",
    "done.loop.body": (
        "In ein Textfeld klicken, {start} drücken, sprechen, dann {stop} drücken — "
        "das Transkript erscheint an der Eingabemarke. Das funktioniert in jeder "
        "Anwendung; die Konsole zeigt nur an, was gerade passiert."),
    "done.controls.body": (
        "{exit_key} beendet Thoughtborne. {settings_key} öffnet dieses "
        "Einstellungsfenster wieder, solange das Tool läuft."),
    "done.startkey.body": (
        "Um Thoughtborne per Tastendruck zu starten: im Startmenü den Eintrag "
        "Thoughtborne rechtsklicken → Eigenschaften → ins Feld „Tastenkombination“ "
        "klicken und eine freie Kombination drücken (z. B. Ctrl+Alt+1). Windows "
        "berücksichtigt sie nur bei Startmenü- und Desktop-Verknüpfungen."),
    "done.threewindow.body": (
        "Ein noch offenes schwarzes Setup-Fenster kannst du schließen — Thoughtborne "
        "läuft in seiner eigenen Konsole."),

    # ---- dialogs & warnings ----
    "dlg.nokey.title": "Kein API-Key",
    "dlg.nokey.body": (
        "Es ist kein API-Key eingetragen, und es wurde keiner gefunden — ohne Key "
        "kann Thoughtborne nicht transkribieren. Deine Hotkeys und Einstellungen "
        "werden trotzdem gespeichert. Einen Key kannst du jederzeit ergänzen — öffne "
        "die Einstellungen aus dem laufenden Tool mit Ctrl+Alt+G. Jetzt speichern und "
        "schließen?"),
    "dlg.hotkeywarn.title": "Hotkey-Probleme",
    "dlg.hotkeywarn.body": (
        "Einige Hotkeys würden beim Start ignoriert — für sie blieben die Defaults "
        "in Kraft (Details im Hotkeys-Tab). Trotzdem speichern?"),
    "dlg.savefail.title": "Speichern fehlgeschlagen",
    "dlg.savefail.body": (
        "Die Einstellungen konnten nicht gespeichert werden. Jede Datei wird atomar "
        "geschrieben — erst im Ganzen ersetzt —, sodass keine halb geschrieben oder "
        "beschädigt zurückbleibt; möglicherweise ist eine Datei gesperrt oder nicht "
        "lesbar. Das technische Detail:"),
    "dlg.loadfail.title": "Einstellungen nicht lesbar",
    "dlg.loadfail.body": (
        "Die gespeicherten Einstellungen konnten nicht gelesen werden — "
        "Thoughtborne startete mit den Standardwerten, und es wurde nichts "
        "geändert. Möglicherweise ist eine Datei gesperrt oder in einer "
        "unerwarteten Kodierung; Speichern bleibt blockiert, bis sie wieder lesbar "
        "ist. Das technische Detail:"),
    "dlg.startfail.title": "Start fehlgeschlagen",
    "dlg.startfail.body": (
        "Die Einstellungen wurden gespeichert, aber Thoughtborne ließ sich von "
        "hier nicht starten — bitte über Thoughtborne.bat starten."),
    "dlg.restarttimeout.title": "Nicht neu gestartet",
    "dlg.restarttimeout.body": (
        "Deine Einstellungen wurden gespeichert. Thoughtborne hat sich aber nicht "
        "innerhalb weniger Sekunden beendet und wurde deshalb nicht neu gestartet — "
        "die Änderungen gelten ab dem nächsten Start. Bitte beende oder starte es "
        "selbst neu (standardmäßig Strg+Alt+4 in seiner Konsole, oder das Fenster "
        "schließen)."),
    "dlg.restartfail.title": "Nicht neu gestartet",
    "dlg.restartfail.body": (
        "Deine Einstellungen wurden gespeichert, aber die Neustart-Anfrage konnte "
        "nicht geschrieben werden — Thoughtborne läuft unverändert weiter. Die "
        "Änderungen gelten ab dem nächsten Start; bitte starte es selbst neu."),
    "warn.corrupt": (
        "personal_settings.json existiert, ließ sich aber nicht parsen — Speichern "
        "ersetzt sie durch eine saubere Datei. Um handgepflegte Inhalte (z. B. "
        "Vokabular) zu retten, die Datei vorher in einem Texteditor reparieren."),
}

_TABLES = {"en": _EN, "de": _DE}


def available_languages() -> tuple:
    """The UI languages this module carries, DE first (the tool's own default)."""
    return ("de", "en")


def t(key: str, lang: str = "de") -> str:
    """Look up `key` in `lang`, falling back to English, then to the key itself.

    The key-itself last resort means a mistyped or not-yet-translated key renders
    visibly (as its dotted name) instead of raising -- a missing string is never a
    crash. An unknown `lang` resolves against the English table."""
    table = _TABLES.get(lang, _EN)
    value = table.get(key)
    if value is not None:
        return value
    value = _EN.get(key)          # EN fallback
    if value is not None:
        return value
    return key                    # last resort: the visible key name
