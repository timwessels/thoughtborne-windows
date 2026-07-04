<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/thoughtborne-lockup-dark.svg">
    <img src="assets/logo/thoughtborne-lockup.svg" alt="Thoughtborne" width="420">
  </picture>
</p>
# Thoughtborne

**[English version](README.md)**

Hotkey-gesteuertes Voice-to-Text-Tool für Windows. Hotkey drücken, sprechen, zweiten Hotkey drücken — und das Transkript landet an der Cursor-Position in der gerade aktiven Anwendung, als wäre es getippt. Optimiert für Deutsch, gebaut zuallererst für einen Job: mit KI sprechen.

Der Qualitätsmaßstab ist einfach: Transkripte müssen **gut genug sein, um sie ungelesen an ein LLM zu schicken** — ohne Korrekturlese-Durchgang. Warum es das Tool gibt, was seine Entscheidungen leitet und was es bewusst nicht ist (kein poliertes GUI-Produkt, keine Cross-Platform-App, kein Abo-Dienst), steht in [VISION.md](VISION.md) (englisch). API-Keys bringt man selbst mit und zahlt nach Verbrauch — oder nimmt den kostenlosen Weg (siehe [Modell-Aufstellung](#die-modell-aufstellung)).

<!-- screenshot slot (#37): glanceable console screenshot drops in here -->

## Die Modell-Aufstellung

Vier Transkriptions-APIs, zur Laufzeit umschaltbar mit `Ctrl+Alt+L`. Die Aufstellung folgt dem, was im praktischen Einsatz für Deutsch am besten abschneidet, alle paar Monate neu bewertet ([VISION.md](VISION.md)). Zwei Jobs bleiben bewusst nebeneinander: wortgetreue Transkripte, die im Moment des Stopps fertig sind, und polierte, die sich wie Geschriebenes lesen ([die zwei Modi](VISION.md#two-modes-two-jobs-verbatim-vs-polished)).

| API | Kurz | Was sie tut | Geschwindigkeit | Key & Kosten |
|-----|------|-------------|-----------------|--------------|
| **Soniox Live** | wortgetreu · sofort fertig (Default) | Transkribiert während der Aufnahme — das Transkript ist im Moment des Stopps fertig, Füllwörter inklusive; ideal, um mit KI zu sprechen. | ~0,5 s nach Stopp | Soniox (Prepaid) |
| **Soniox** | poliert · braucht länger | Schickt das Audio nach dem Stopp und liefert Text, der sich wie Geschriebenes liest — saubere Interpunktion, keine Füllwörter; für E-Mails und Texte, die an Menschen gehen. | ~4–6 s (kurz) / ~10–40 s (lang) | Soniox (Prepaid) |
| **Groq Large** | genau · kostenlos | Die genauere der beiden kostenlosen Optionen — der empfohlene Weg, Thoughtborne ohne Bezahlung auszuprobieren. | ~1 s | Groq (Free Tier) |
| **Groq** | schnell · kostenlos | Die schnellste Option, für Notizen zwischendurch — Genauigkeit unterhalb der anderen drei. | ~0,7 s | Groq (Free Tier) |

**Der kostenlose Weg:** Beide Groq-Einträge laufen im Free Tier von Groq (Stand Juni 2026, pro Modell: 20 Anfragen/Minute, 2.000 Anfragen/Tag, 7.200 Audio-Sekunden/Stunde, 28.800 Audio-Sekunden/Tag) — damit lässt sich Thoughtborne ausprobieren, ohne irgendwen zu bezahlen. Soniox hat keinen Free Tier (Stand Juni 2026): Vor der ersten Nutzung ein kleines Prepaid-Guthaben aufladen; die Abrechnung ist nutzungsbasiert ([soniox.com/pricing](https://soniox.com/pricing)). Zur Größenordnung: Ein halbes Jahr intensiver persönlicher Nutzung — etwa 150 Stunden Transkription — hat rund 10–15 $ gekostet ([VISION.md](VISION.md)).

Engines, für Neugierige: `stt-rt-v4` (Soniox Live) · `de_v2` + `stt-async-v4` (Soniox — kurze Aufnahmen laufen über die synchrone v2-Engine, lange und der automatische Fallback über v4 async; welcher Pfad lief, muss einen beim Diktieren nicht kümmern) · `whisper-large-v3` (Groq Large) · `whisper-large-v3-turbo` (Groq).

## Voraussetzungen

- **Windows.** Das Tool ist bewusst Windows-only (globale Hotkeys, Audio-Aufnahme und Text-Einfügung sind Win32); für macOS gibt es einen Schwester-Port (siehe [Projekt & Links](#projekt--links)).
- **Ein Mikrofon**, mit erlaubtem Mikrofonzugriff in Windows (Einstellungen > Datenschutz und Sicherheit > Mikrofon).
- **Mindestens ein API-Key** — Groq (kostenlos) oder Soniox (Prepaid); siehe [API-Keys](#api-keys).
- **Internet.** Die Transkription läuft über die APIs; der erste Start lädt außerdem einmalig Python und die Dependencies.
- **Kein Python nötig** auf dem Standard-Weg — uv lädt automatisch ein passendes. (pip-Fallback: Python 3.10–3.13, nicht 3.14.)

## Installation

<!-- quick-start slot (#51): a guided setup path drops in here as the first option, when it exists -->

Drei Wege — einen wählen. Die Befehle funktionieren in PowerShell wie in cmd.

### Standard-Setup (uv)

Thoughtborne nutzt [uv](https://docs.astral.sh/uv/) als Python-Projektmanager: uv lädt automatisch ein passendes Python und alle Dependencies in ein lokales `.venv` — ein vorinstalliertes Python ist nicht nötig.

1. **uv installieren** (einmalig):

   ```
   winget install --id=astral-sh.uv -e
   ```

   Danach ein neues Terminal öffnen — ein bereits offenes Fenster sieht den PATH-Eintrag von winget noch nicht (`Thoughtborne.bat` findet uv ohnehin selbst).

   Ohne winget: die [uv-Installationsanleitung](https://docs.astral.sh/uv/getting-started/installation/) nutzen.

2. **Code holen:**

   ```
   git clone https://github.com/timwessels/thoughtborne-windows.git
   cd thoughtborne-windows
   ```

   Oder das ZIP von GitHub herunterladen und entpacken — der entpackte Ordner heißt `thoughtborne-windows-main`, das `cd` entsprechend anpassen.

3. **API-Keys einrichten:** `.env.example` als `.env` kopieren und mindestens einen Key eintragen — wo es die Keys gibt, steht unter [API-Keys](#api-keys).

   ```
   copy .env.example .env
   notepad .env
   ```

4. **Starten:**

   ```
   uv run thoughtborne.py
   ```

   Oder Doppelklick auf `Thoughtborne.bat` — sie startet das Tool über uv und bietet die uv-Installation an, falls uv fehlt. Der erste Start lädt einmalig Python und die Dependencies; danach hält uv alles automatisch aktuell — auch nach einem `git pull` mit neuen Dependencies sind keine manuellen Schritte nötig.

### Setup mit einem KI-Coding-Agenten

Wer mit einem KI-Coding-Agenten arbeitet (Claude Code, Cursor, Codex …), kann ihm das Setup übergeben — [`llms-install.md`](llms-install.md) führt den Agenten durch Installation, API-Keys und Selbsttest. Im geklonten Repo dem Agenten einfach sagen:

```text
Read llms-install.md and guide me through the setup. Ask before running commands.
```

`llms-install.md` ist gewöhnliches, menschenlesbares Markdown — wer mag, liest selbst hinein.

### Klassisch mit pip + venv (Fallback)

Ohne uv funktioniert der klassische Weg weiterhin. Wichtig: **Python 3.10–3.13, nicht 3.14** — PyAudio liefert für 3.14 noch keine vorkompilierten Wheels, die Installation bricht dort mit einem Build-Fehler ab.

```
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-optional.txt
python thoughtborne.py
```

Die optionale Datei installiert das Soniox-SDK. Ohne das SDK läuft der `soniox`-Slot vollständig über die v4-Engine — funktioniert, ist bei kurzen Aufnahmen nur langsamer. (Auf dem uv-Weg ist das SDK automatisch enthalten.)

## API-Keys

Die Keys gehören einem selbst — die Anmeldung läuft direkt beim Anbieter. Audio geht an die gewählte Transkriptions-API und nirgendwo darüber hinaus; Aufnahmen und Transkripte werden lokal archiviert, nirgendwo sonst. Jede integrierte API muss mindestens ein Opt-out vom Training auf Nutzerdaten bieten ([VISION.md](VISION.md)). Mindestens ein Key ist nötig — ganz ohne Key startet das Tool nicht und benennt präzise, welche Keys fehlen.

**Groq** (kostenlos): Auf [console.groq.com](https://console.groq.com) registrieren → API-Keys-Seite ([console.groq.com/keys](https://console.groq.com/keys)) → Key erstellen und sofort kopieren (er wird nur einmal angezeigt) → in die Zeile `GROQ_API_KEY=` der `.env` eintragen.

**Soniox** (Prepaid): Auf [soniox.com](https://soniox.com) registrieren → in der Console ([console.soniox.com](https://console.soniox.com)) ein kleines Prepaid-Guthaben aufladen (nötig, bevor die API funktioniert) → Key erstellen und kopieren → in die Zeile `SONIOX_API_KEY=` der `.env` eintragen.

Nur ein Groq-Key? Nichts umzustellen: Der Start überspringt die Soniox-Einträge automatisch, sagt es dazu und startet auf der ersten verfügbaren API. Wer stattdessen ohne diese Hinweise direkt auf Groq starten will, stellt in `config.py` `DEFAULT_API = "groq-large"` ein.

## Der erste Start

Das Tool starten — Doppelklick auf `Thoughtborne.bat` oder `uv run thoughtborne.py`. Ein Konsolenfenster öffnet sich mit einem Start-Banner, das die aktive API und die Hotkey-Liste zeigt; Details landen in `thoughtborne.log`.

Dann diktieren:

1. Ein beliebiges Textfeld fokussieren (das einfache Notepad eignet sich gut).
2. `Ctrl+Alt+W` drücken und einen Satz sprechen — die Konsole bestätigt die laufende Aufnahme.
3. `Ctrl+Alt+A` drücken — das Transkript erscheint an der Cursor-Position.

**Selbsttest:** `Ctrl+Alt+Ü` transkribiert die mitgelieferte `test_audio.mp3` über die aktive API und fügt das Ergebnis an der Cursor-Position ein (vorher ein Textfeld fokussieren) — so lässt sich am schnellsten prüfen, ob alles funktioniert.

Die eigenen Daten bleiben lokal: Jedes Diktat liegt in einem gemeinsamen `history/`-Ordner im Projektverzeichnis — Aufnahmen als MP3 in `history/audio/`, Transkripte in `history/transcripts/`, gepaart über den Zeitstempel. Das Start-Banner zeigt den Pfad und `Ctrl+Alt+6` öffnet den Ordner; beim Update von einer älteren Version werden die bisherigen Ordner `voice_archive/` und `text_archive/` beim ersten Start automatisch dorthin migriert. Schlägt eine Transkription fehl, wiederholt `Ctrl+Alt+R` sie aus der archivierten Aufnahme.

`Ctrl+Alt+4` beendet das Tool.

## Hotkeys

| Hotkey | Funktion |
|--------|----------|
| `Ctrl+Alt+W` | Aufnahme starten (geht auch, während eine vorherige Aufnahme noch transkribiert wird) |
| `Ctrl+Alt+A` | Stopp + an der Cursor-Position einfügen (simuliertes Tippen) |
| `Ctrl+Alt+D` | Stopp + an der Cursor-Position einfügen (über die Zwischenablage — schneller) |
| `Ctrl+Alt+H` | Stopp + einfügen + Enter drücken (Senden mit einem Tastendruck, für Chats) |
| `Ctrl+Alt+Y` | Stopp + nur transkribieren — später mit `A` oder `D` einfügen |
| `Ctrl+Alt+X` | Aufnahme abbrechen (nichts wird eingefügt) |
| `Ctrl+Alt+R` | Letzte fehlgeschlagene Transkription wiederholen (aus der archivierten Aufnahme) |
| `Ctrl+Alt+L` | Transkriptions-API wechseln (zyklisch: Soniox Live → Soniox → Groq Large → Groq) |
| `Ctrl+Alt+6` | Den Ordner mit Aufnahmen & Transkripten (`history/`) im Explorer öffnen |
| `Ctrl+Alt+Ü` | Selbsttest: die mitgelieferte `test_audio.mp3` transkribieren |
| `Ctrl+Alt+4` | Programm beenden |

Transkripte werden immer in Aufnahme-Reihenfolge eingefügt, auch wenn mehrere Aufnahmen parallel verarbeitet werden.

**`Ü` ohne deutsche Tastatur:** `Ü` ist auf dem deutschen QWERTZ-Layout eine eigene Taste (rechts von `P`). Auf anderen Layouts, falls der Selbsttest nicht auslöst, die Kombination im `HOTKEYS`-Dict in `config.py` umbelegen — die Einträge dort zeigen das Format.

## Anpassung

**Erkennungs-Vokabular** (empfohlen): `personal_settings.example.json` als `personal_settings.json` kopieren und den `vocabulary`-Block mit eigenen Namen, Fachbegriffen und häufigen Fremdwörtern füllen — sie werden dem Sprachmodell als Kontext mitgegeben und verbessern die Erkennung spürbar. Genutzt von allen Soniox-Engines — Soniox Live und beiden Pfaden des Soniox-Upload-Slots; die Groq-APIs ignorieren es. Fehlt die Datei, läuft das Tool einfach ohne Personalisierung.

```
copy personal_settings.example.json personal_settings.json
```

**Push-to-talk (optional):** ein zweiter Weg zu diktieren, für kurze, schnelle Einwürfe. **Links-Strg** tippen, loslassen, dann Links-Strg **drücken und halten** — die Aufnahme läuft, solange gehalten wird, und beim Loslassen wird das Transkript an der Cursor-Position eingefügt (genau wie bei den Hotkeys). Standardmäßig **aus**; einschalten im `push_to_talk`-Block der `personal_settings.json`. Weil Strg der Auslöser ist, sorgt ein zwingender AltGr-Filter dafür, dass deutsche QWERTZ-Zeichen (`@ \ { } [ ] | € ~`) ihn nie auslösen — AltGr ist `Strg+Alt`, und die Geste zählt nur ein *bloßes* Strg (kein Alt, keine andere Taste gedrückt), sodass auch `Strg+C` → `Strg+V` und jede andere Strg-Kombination unberührt bleiben. Im selben Block konfigurierbar: die Auslöser-Taste (`lctrl`, `rctrl` oder `lalt`), der Einfüge-Pfad (`clipboard` wie `D` ist der Standard; `type` wie `A` ist der Fallback für Apps, die Einfügen blockieren; außerdem `send` wie `H` oder `no_insert`) und die drei Zeit-Schwellen. **JetBrains-IDEs:** Doppel-Strg ist dort der Shortcut „Run Anything" und kollidiert daher, wenn eine IDE der IntelliJ-Familie den Fokus hat — die Option „Disable double modifier key shortcuts" in den Advanced Settings der IDE *aktivieren*, um diese Kollision abzuschalten. Hinweis: Wie alle Hotkeys erreicht auch Push-to-talk kein Fenster, das mit erhöhten Rechten (als Administrator) läuft — Windows blockiert dort die Eingabe von nicht-erhöhten Prozessen. Das ist dieselbe Grenze wie bei den bestehenden Hotkeys, keine neue Einschränkung.

**Einstellungen in `config.py`:** Die Konfiguration besteht bewusst aus einfachen Konstanten mit Kommentaren. Was die meisten anpassen:

- `DEFAULT_API` — die API beim Start (`"soniox-live"`, `"soniox"`, `"groq-large"`, `"groq"`).
- `LANGUAGE` — Default `"de"`. Englisch funktioniert (`"en"`), aber Artefakt-Filter und Tuning zielen auf Deutsch — ehrliche Erwartungen ([VISION.md](VISION.md)).
- `HOTKEYS` — alle Tastenkombinationen. Kollidiert eine mit einem anderen Programm, hier ändern; Sonderzeichen wie `#` und Nicht-ASCII-Buchstaben meiden (das etablierte `ü` ist die erprobte Ausnahme).

Weitere Einstellungen (parallele Transkriptionen, Audio-Trimming, …) sind als Kommentare direkt in `config.py` dokumentiert.

**Oder dem Coding-Agenten sagen.** Die Konfigurations-Strategie des Projekts ist lesbarer Code statt einer ausufernden Konfigurationsfläche ([VISION.md](VISION.md)): die gewünschte Änderung dem eigenen KI-Coding-Agenten beschreiben — [`AGENTS.md`](AGENTS.md) gibt ihm die Spielregeln für dieses Repo.

## Troubleshooting

**PyAudio-Installation schlägt fehl (pip-Weg).** PyAudio liefert offizielle Windows-Wheels für Python 3.10–3.13 — `pip install` braucht dort keinen Compiler. Ein Build-Fehler heißt meist Python 3.14: auf 3.13 wechseln oder den uv-Weg nutzen (uv wählt automatisch ein passendes Python).

**`python` öffnet den Microsoft Store.** Das ist der Store-Alias-Stub auf einem Rechner ohne Python — den `py`-Launcher nutzen (wie in den pip-Befehlen oben) oder den uv-Weg.

**`winget` nicht gefunden.** uv über die [offizielle Installationsanleitung](https://docs.astral.sh/uv/getting-started/installation/) installieren oder den pip-Weg nehmen.

**Das Tool startet, aber kein Audio / leere Transkripte.** Mikrofon-Berechtigung in Windows prüfen (Einstellungen > Datenschutz und Sicherheit > Mikrofon) und das Standard-Eingabegerät; `thoughtborne.log` protokolliert, welches Eingabegerät genutzt wurde.

**Ein Hotkey registriert sich nicht** (eine `FAILED:`-Zeile im Start-Log). Ein anderes Programm besitzt die Kombination bereits — globale Hotkeys sind in Windows exklusiv. Die Kombination im `HOTKEYS`-Dict in `config.py` ändern.

**Einfügen bewirkt in einem bestimmten Fenster nichts.** Die Ziel-App läuft mit erhöhten Rechten (als Administrator), und Windows blockiert simulierte Eingaben aus Prozessen ohne erhöhte Rechte. Thoughtborne ebenfalls mit erhöhten Rechten starten oder in Apps ohne erhöhte Rechte diktieren.

**Erster Start sehr langsam oder schlägt offline fehl.** uv lädt einmalig Python und die Dependencies; dafür braucht es dieses eine Mal Internet.

**API-Fehler.** Keys in der `.env` und die Internetverbindung prüfen; im Free Tier die [Limits](#die-modell-aufstellung) im Blick behalten.

## Projekt & Links

- [VISION.md](VISION.md) — warum es das Tool gibt, der Qualitätsmaßstab, was Entscheidungen leitet (englisch).
- [CHANGELOG.md](CHANGELOG.md) — was sich geändert hat, Release für Release.
- [LICENSE](LICENSE) — MIT.
- Für KI-Coding-Agenten: [AGENTS.md](AGENTS.md) (Arbeiten in diesem Repo) · [llms-install.md](llms-install.md) (geführtes Setup).
- **macOS:** Es gibt einen Schwester-Port — [thoughtborne-macos](https://github.com/timwessels/thoughtborne-macos): drei Transkriptions-APIs statt vier, sonst analog; as-is verfügbar.

Issues und Contributions sind willkommen. Thoughtborne ist seit Jahren das tägliche Arbeitswerkzeug des Maintainers und wird aktiv gepflegt.
