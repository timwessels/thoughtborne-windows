"""
Externalized UI strings (DE/EN) for the settings/onboarding app (#144).

Pure stdlib: two flat string tables (`_EN`, `_DE`) keyed by dotted lookup keys,
plus `t(key, lang)` with a lang -> EN -> key-itself fallback chain. Kept apart
from `thoughtborne_settings.py` so the GUI holds no prose and both languages are
maintained side by side; `test_settings_io.py` imports this module off-Windows to
assert the two tables carry the exact same key set (a missing translation is a
test failure, not a silent English leak).

The only Windows-bound call lives inside `detect_ui_language()` behind a
try/except, so importing this module on plain Python never touches ctypes -- the
i18n completeness test depends on that.

Two wording contracts are load-bearing (mirror them if either source changes):
  - `engine.desc.*` EN equals `config.API_DISPLAY[api]["descriptor"]` (one engine
    descriptor, two surfaces -- the console lineup and this dropdown).
  - the hotkey-validator detail surfaced under `capture.invalid` /
    `hotkeys.status.warn_prefix` stays English in both languages (it is the same
    text `thoughtborne.log` prints); the localized headline carries the meaning.
"""

# =============================================================================
# String tables. _EN and _DE MUST carry the identical key set (verified by
# test_settings_io.check_i18n). Keys are grouped as in the spec's string table;
# `action.*` keys cover config.DEFAULT_HOTKEYS' 11 action names.
# =============================================================================
_EN = {
    # ---- shell & rail ----
    "app.title.settings": "Thoughtborne Settings",
    "app.title.firstrun": "Thoughtborne Setup",
    "welcome.heading": "Welcome to Thoughtborne",
    "welcome.sub": ("Let's get you set up — an API key, your hotkeys, a few "
                    "preferences. Three short pages, and everything here can be "
                    "changed later in this same window."),
    "lang.de": "Deutsch",
    "lang.en": "English",
    "btn.back": "Back",
    "btn.next": "Next",
    "btn.save": "Save",
    "btn.cancel": "Cancel",
    "btn.save_start": "Save & start Thoughtborne",
    "footer.next_start": "Changes take effect the next time Thoughtborne starts.",

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
        "actual use, no minimum. Around 25 hours of dictation a month comes to "
        "about $3. New accounts get no free starting credit: after signing up at "
        "console.soniox.com, first top up a small balance in the console — the key "
        "alone won't transcribe yet. Powers the two Soniox engines."),
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
    "test.invalid": "Key rejected — check for typos, or copy it fresh from the console.",
    "test.unreachable": (
        "Couldn't reach the server — check your internet connection. Saving works "
        "anyway; the key just wasn't verified."),
    "url.groq_keys": "https://console.groq.com/keys",
    "url.soniox_console": "https://console.soniox.com",

    # ---- hotkeys tab ----
    "hotkeys.tab": "Hotkeys",
    "hotkeys.intro": (
        "Thoughtborne is driven entirely by global hotkeys — they work in every "
        "application, whatever has focus; in return, each combo is reserved "
        "system-wide while the tool runs. Pick a preset as the base, then change "
        "any single action below if a combo clashes with something you use."),
    "hotkeys.preset.ctrl_alt.title": "Ctrl+Alt letters — the shipped default",
    "hotkeys.preset.ctrl_alt.body": (
        "Hold Ctrl+Alt and press a letter: W starts the recording, A/D/H/Y deliver "
        "the transcript, X cancels. Works on every keyboard, laptops included, and "
        "stays clear of the F-key row that IDEs use for debugging — the safe "
        "all-round choice. Applying this preset is also the way back to the "
        "defaults."),
    "hotkeys.preset.fkeys.title": "F-keys — one keypress, no chord",
    "hotkeys.preset.fkeys.body": (
        "Three F-keys, three families: F8 engine, F9 record, F10 deliver. The bare "
        "key is the everyday move (F9 starts, F10 inserts), Ctrl the important "
        "sibling (cancel, send, switch engine), Ctrl+Alt the rare technical one; "
        "housekeeping keys (history, self-test, exit) stay the same as in the "
        "letter preset. One caveat: the F5–F11 band carries debug actions in most "
        "IDEs — if you debug a lot, stay on the letter preset or override single "
        "keys. On a laptop, you may need to enable Fn-Lock (usually Fn+Esc) so "
        "F9/F10 fire directly."),
    "btn.use_preset": "Use this preset",
    "hotkeys.custom.heading": "Individual actions",
    "hotkeys.custom.body": (
        "Click Change next to an action, then press the new combo. Letters, "
        "digits, F1–F24 and the ü key work — F-keys also bare, everything else "
        "with Ctrl and/or Alt."),
    "btn.change_key": "Change…",
    "hotkeys.more_suffix": "(+{n} more)",
    "capture.prompt": "Press the new combo … (Esc cancels)",
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
    "action.test_transcription": "Self-test (bundled test audio)",
    "action.exit_program": "Exit Thoughtborne",

    # ---- behavior tab ----
    "behavior.tab": "Behavior",
    "behavior.engine.heading": "Engine at startup",
    "behavior.engine.body": (
        "The transcription engine Thoughtborne starts on. While the tool runs, "
        "Ctrl+Alt+L cycles through all four engines at any time — this setting "
        "only picks the starting point. The engines differ mainly in speed versus "
        "tidiness; the model lineup in the README has the details."),
    "engine.desc.soniox-live": "verbatim, instant",
    "engine.desc.soniox": "polished, takes longer",
    "engine.desc.groq-large": "accurate, free",
    "engine.desc.groq": "fast, free",
    "behavior.tray.heading": "Console out of the taskbar (tray)",
    "behavior.tray.body": (
        "The console is a status monitor — dictation keeps working with the window "
        "hidden. If the console runs in Windows Terminal (the default on Windows "
        "11), two of Terminal's own settings move it to the tray: open Terminal's "
        "settings (Ctrl+,), go to Interaction, and enable both \"Hide Terminal in "
        "the notification area when it is minimized\" and \"Always display an icon "
        "in the notification area\". Minimizing then sends the window to the tray; "
        "one click on the tray icon brings it back (Windows first parks new tray "
        "icons behind the ^ chevron — drag the icon into the visible tray once). "
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

    # ---- dialogs & warnings ----
    "dlg.discard.title": "Discard changes?",
    "dlg.discard.body": "There are unsaved changes. Close without saving?",
    "dlg.nokey.title": "No API key",
    "dlg.nokey.body": (
        "No API key is entered, and none was found on this PC — Thoughtborne can't "
        "transcribe without one. Save anyway?"),
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
                    "Einstellungen. Drei kurze Seiten, und alles hier lässt sich "
                    "später in genau diesem Fenster wieder ändern."),
    "lang.de": "Deutsch",
    "lang.en": "English",
    "btn.back": "Zurück",
    "btn.next": "Weiter",
    "btn.save": "Speichern",
    "btn.cancel": "Abbrechen",
    "btn.save_start": "Speichern & Thoughtborne starten",
    "footer.next_start": "Änderungen gelten ab dem nächsten Start von Thoughtborne.",

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
        "abgerechnet nach tatsächlicher Nutzung, ohne Minimum. Rund 25 Stunden "
        "Diktat im Monat ergeben etwa 3 $. Neue Konten bekommen kein Startguthaben: "
        "Nach der Registrierung auf console.soniox.com zuerst in der Console ein "
        "kleines Guthaben aufladen — der Key allein transkribiert noch nicht. "
        "Versorgt die beiden Soniox-Engines."),
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
    "test.invalid": "Key abgelehnt — auf Tippfehler prüfen oder frisch aus der Console kopieren.",
    "test.unreachable": (
        "Server nicht erreichbar — Internetverbindung prüfen. Speichern geht "
        "trotzdem; der Key wurde nur nicht geprüft."),
    "url.groq_keys": "https://console.groq.com/keys",
    "url.soniox_console": "https://console.soniox.com",

    # ---- hotkeys tab ----
    "hotkeys.tab": "Hotkeys",
    "hotkeys.intro": (
        "Thoughtborne wird komplett über globale Hotkeys gesteuert — sie "
        "funktionieren in jeder Anwendung, egal was gerade den Fokus hat; dafür "
        "ist jede Kombination systemweit reserviert, solange das Tool läuft. Als "
        "Basis ein Preset wählen und darunter einzelne Aktionen ändern, falls eine "
        "Kombination mit etwas kollidiert, das man nutzt."),
    "hotkeys.preset.ctrl_alt.title": "Ctrl+Alt-Buchstaben — der Auslieferungszustand",
    "hotkeys.preset.ctrl_alt.body": (
        "Ctrl+Alt halten und einen Buchstaben drücken: W startet die Aufnahme, "
        "A/D/H/Y liefern das Transkript ab, X bricht ab. Funktioniert auf jeder "
        "Tastatur, Laptops eingeschlossen, und lässt die F-Tasten-Reihe frei, die "
        "IDEs fürs Debuggen nutzen — die sichere Allround-Wahl. Dieses Preset "
        "anzuwenden ist zugleich der Weg zurück zu den Defaults."),
    "hotkeys.preset.fkeys.title": "F-Tasten — ein Tastendruck statt Griff",
    "hotkeys.preset.fkeys.body": (
        "Drei F-Tasten, drei Familien: F8 Engine, F9 Aufnahme, F10 Abliefern. Die "
        "blanke Taste ist der Alltag (F9 startet, F10 fügt ein), Ctrl der wichtige "
        "Geschwister-Fall (abbrechen, senden, Engine wechseln), Ctrl+Alt der "
        "seltene technische; die Verwaltungs-Tasten (History, Selbsttest, Beenden) "
        "bleiben wie im Buchstaben-Preset. Ein Vorbehalt: Das Band F5–F11 ist in "
        "den meisten IDEs mit Debug-Aktionen belegt — wer viel debuggt, bleibt "
        "beim Buchstaben-Preset oder passt einzelne Tasten an. Auf dem Laptop ggf. "
        "Fn-Lock aktivieren (meist Fn+Esc), damit F9/F10 direkt feuern."),
    "btn.use_preset": "Dieses Preset übernehmen",
    "hotkeys.custom.heading": "Einzelne Aktionen",
    "hotkeys.custom.body": (
        "Neben einer Aktion auf Ändern klicken und die neue Kombination drücken. "
        "Buchstaben, Ziffern, F1–F24 und die ü-Taste funktionieren — F-Tasten auch "
        "blank, alles andere mit Ctrl und/oder Alt."),
    "btn.change_key": "Ändern…",
    "hotkeys.more_suffix": "(+{n} weitere)",
    "capture.prompt": "Neue Kombination drücken … (Esc bricht ab)",
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
    "action.test_transcription": "Selbsttest (mitgeliefertes Test-Audio)",
    "action.exit_program": "Thoughtborne beenden",

    # ---- behavior tab ----
    "behavior.tab": "Verhalten",
    "behavior.engine.heading": "Engine beim Start",
    "behavior.engine.body": (
        "Die Transkriptions-Engine, mit der Thoughtborne startet. Im laufenden "
        "Betrieb schaltet Ctrl+Alt+L jederzeit durch alle vier Engines — diese "
        "Einstellung wählt nur den Startpunkt. Die Engines unterscheiden sich vor "
        "allem in Tempo gegen Sauberkeit; Details in der Modell-Aufstellung im "
        "README."),
    "engine.desc.soniox-live": "wortgetreu, sofort fertig",
    "engine.desc.soniox": "poliert, braucht länger",
    "engine.desc.groq-large": "genau, kostenlos",
    "engine.desc.groq": "schnell, kostenlos",
    "behavior.tray.heading": "Konsole aus der Taskleiste (Tray)",
    "behavior.tray.body": (
        "Die Konsole ist ein Status-Monitor — Diktieren funktioniert auch bei "
        "verstecktem Fenster. Läuft die Konsole in Windows Terminal (dem Standard "
        "unter Windows 11), erledigen zwei von Terminals eigenen Einstellungen den "
        "Umzug in den Tray: Terminals Einstellungen öffnen (Ctrl+,), zu "
        "Interaktion gehen und beide aktivieren — „Terminal bei Minimierung im "
        "Infobereich ausblenden“ und „Immer ein Symbol im Infobereich anzeigen“. "
        "Minimieren schickt das Fenster dann in den Tray; ein Klick aufs Tray-Icon "
        "holt es zurück (neue Tray-Icons parkt Windows zunächst hinter dem "
        "^-Ausklappmenü — das Icon einmal in den sichtbaren Bereich ziehen). Zwei "
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

    # ---- dialogs & warnings ----
    "dlg.discard.title": "Änderungen verwerfen?",
    "dlg.discard.body": "Es gibt ungespeicherte Änderungen. Ohne Speichern schließen?",
    "dlg.nokey.title": "Kein API-Key",
    "dlg.nokey.body": (
        "Es ist kein API-Key eingetragen, und es wurde keiner gefunden — ohne Key "
        "kann Thoughtborne nicht transkribieren. Trotzdem speichern?"),
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


def detect_ui_language() -> str:
    """Best-effort initial UI language: 'de' on a German Windows display language,
    else 'en'. The Windows path reads GetUserDefaultUILanguage (primary-language
    0x07 == LANG_GERMAN) via kernel32; all of it is guarded, so off-Windows -- or a
    missing DLL -- degrades to the process locale environment rather than raising
    (this function is imported and called by the off-Windows i18n test)."""
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        # The low 10 bits are the primary language id; 0x07 is German.
        return "de" if (langid & 0x3FF) == 0x07 else "en"
    except Exception:
        pass
    # Off-Windows / no kernel32: read the POSIX locale environment. (Env vars, not
    # the deprecated locale.getdefaultlocale, so this stays warning-free on 3.11+.)
    import os
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var)
        if value:
            return "de" if value.lower().startswith("de") else "en"
    return "en"
