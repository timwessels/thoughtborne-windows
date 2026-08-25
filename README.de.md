<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo/thoughtborne-lockup-dark.svg">
    <img src="assets/logo/thoughtborne-lockup.svg" alt="Thoughtborne" width="420">
  </picture>
</p>
# Thoughtborne

**[English version](README.md)** · **Website: [thoughtborne.app](https://thoughtborne.app)**

Hotkey-gesteuertes Voice-to-Text-Tool für Windows. Hotkey drücken, sprechen, zweiten Hotkey drücken — und das Transkript landet an der Cursor-Position in der gerade aktiven Anwendung, als wäre es getippt. Optimiert für Deutsch, gebaut zuallererst für einen Job: mit KI sprechen.

Der Qualitätsmaßstab ist einfach: Transkripte müssen **gut genug sein, um sie ungelesen an ein LLM zu schicken** — ohne Korrekturlese-Durchgang. Warum es das Tool gibt, was seine Entscheidungen leitet und was es bewusst nicht ist (kein poliertes GUI-Produkt, keine Cross-Platform-App, kein Abo-Dienst), steht in [VISION.md](VISION.md) (englisch). API-Keys bringt man selbst mit und zahlt nach Verbrauch — oder nimmt den kostenlosen Weg (siehe [Modell-Aufstellung](#die-modell-aufstellung)).

<!-- screenshot slot (#37): glanceable console screenshot drops in here -->

## Die Modell-Aufstellung

Vier Transkriptions-APIs, zur Laufzeit umschaltbar mit `Ctrl+Alt+L`. Die Aufstellung folgt dem, was im praktischen Einsatz für Deutsch am besten abschneidet, alle paar Monate neu bewertet ([VISION.md](VISION.md)). Die Engines unterscheiden sich vor allem entlang einer Achse — Tempo gegen Sauberkeit: manche sind im Moment des Stopps fertig und bleiben nah am Gesprochenen, andere brauchen etwas länger und lesen sich etwas sauberer ([verschiedene Engines, verschiedene Stärken](VISION.md#different-engines-different-strengths)).

| API | Kurz | Was sie tut | Geschwindigkeit | Key & Kosten |
|-----|------|-------------|-----------------|--------------|
| **Soniox Live** | wortgetreu · sofort fertig (Default) | Transkribiert während der Aufnahme — das Transkript ist im Moment des Stopps fertig, nah an dem, wie du wirklich gesprochen hast (nur die reinen "ähm"/"äh" werden herausgefiltert); ideal, um mit KI zu sprechen. | ~0,5 s nach Stopp | Soniox (Prepaid) |
| **Soniox** | poliert · braucht länger | Schickt das Audio nach dem Stopp und liefert Text, der sich wie Geschriebenes liest — saubere Interpunktion, keine Füllwörter; für E-Mails und Texte, die an Menschen gehen. | ~5–40 s (Datei-Upload + Polling) | Soniox (Prepaid) |
| **Groq Whisper Large v3** | genau · kostenlos | Die genauere der beiden kostenlosen Optionen — der empfohlene Weg, Thoughtborne ohne Bezahlung auszuprobieren. | ~1 s | Groq (Free Tier) |
| **Groq Whisper Turbo v3** | schnell · kostenlos | Die schnellste Option, für Notizen zwischendurch — Genauigkeit unterhalb der anderen drei. | ~0,7 s | Groq (Free Tier) |

**Der kostenlose Weg:** Beide Groq-Einträge laufen im Free Tier von Groq (Stand Juni 2026, pro Modell: 20 Anfragen/Minute, 2.000 Anfragen/Tag, 7.200 Audio-Sekunden/Stunde, 28.800 Audio-Sekunden/Tag) — damit lässt sich Thoughtborne ausprobieren, ohne irgendwen zu bezahlen. Soniox hat keinen Free Tier (Stand Juli 2026): Ein kleines Prepaid-Guthaben aufladen und dann nach Verbrauch zahlen ([soniox.com/pricing](https://soniox.com/pricing)) — 0,12 $ pro Stunde Audio beim Echtzeit-Default (Soniox Live), 0,10 $ bei asynchronen Datei-Uploads (Soniox). In der Praxis bleibt das gering: Der Maintainer diktiert rund 25 Stunden Audio im Monat (Schnitt der letzten sechs Monate), was etwa 3 $ ergibt; leichtere regelmäßige Nutzung liegt eher bei einem Dollar. Kein Abo: ein Bruchteil dessen, was Abo-Diktier-Tools kosten (etwa 12–15 $ im Monat), und man zahlt nur für das, was man tatsächlich nutzt ([VISION.md](VISION.md)).

Engines, für Neugierige: `stt-rt-v5` (Soniox Live) · `stt-async-v5` (Soniox — die polierte Datei-Upload-Engine) · `whisper-large-v3` (Groq Whisper Large v3) · `whisper-large-v3-turbo` (Groq Whisper Turbo v3).

## Voraussetzungen

- **Windows.** Das Tool ist bewusst Windows-only (globale Hotkeys, Audio-Aufnahme und Text-Einfügung sind Win32); für macOS gibt es einen Schwester-Port (siehe [Projekt & Links](#projekt--links)).
- **Ein Mikrofon**, mit erlaubtem Mikrofonzugriff in Windows (Einstellungen > Datenschutz und Sicherheit > Mikrofon).
- **Mindestens ein API-Key** — Groq (kostenlos) oder Soniox (Prepaid); siehe [API-Keys](#api-keys).
- **Internet.** Die Transkription läuft über die APIs; der erste Start lädt außerdem einmalig Python und die Dependencies.
- **Kein Python nötig** auf dem Standard-Weg — uv lädt automatisch ein passendes. (pip-Fallback: Python 3.10–3.12 oder 3.13.1+, nicht 3.14.)

## Installation

<!-- quick-start (#51): guided installer -->

### Schnellstart

Der schnellste Einstieg ist der Installer — er braucht kein git und kein manuell installiertes Python. Ein Terminal öffnen und eine Zeile einfügen.

**PowerShell:**

```
irm https://github.com/timwessels/thoughtborne-windows/releases/latest/download/setup.ps1 | iex
```

**cmd** (funktioniert in PowerShell wie in cmd):

```
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://github.com/timwessels/thoughtborne-windows/releases/latest/download/setup.ps1 | iex"
```

Unklar, welche Konsole gerade offen ist? Die cmd-Form nutzen — sie läuft in beiden.

Das Skript installiert [uv](https://docs.astral.sh/uv/) (einen Python-Projektmanager) falls nötig, lädt Thoughtborne nach `%LOCALAPPDATA%\Programs\Thoughtborne`, legt den Startmenü-Eintrag **Thoughtborne** an, registriert das Programm unter Einstellungen > Installierte Apps und startet das Tool — was bei einer frischen Installation ohne Key den Einrichtungs-Assistenten öffnet, in dem man einen Anbieter wählt und einen API-Key einfügt. Kein Adminrecht an irgendeinem Punkt. Das Skript ist offen — [vorher hineinlesen](https://github.com/timwessels/thoughtborne-windows/releases/latest/download/setup.ps1), wer mag.

**Oder: herunterladen und doppelklicken.** Lieber keinen Befehl einfügen? `thoughtborne.zip` vom [neuesten Release](https://github.com/timwessels/thoughtborne-windows/releases/latest) herunterladen, im Explorer Rechtsklick → **Alle extrahieren**, dann im entpackten Ordner **`setup.bat`** doppelklicken — dasselbe Setup läuft von dort. Ein Sicherheitsdialog erscheint (eine unsignierte Datei aus dem Internet); der hervorgehobene Standard-Button ist **Abbrechen**, also nicht einfach Enter drücken, sondern bewusst auf **Ausführen** klicken. Die entpackten Dateien sind nur ein Träger für `setup.bat`, das die aktuelle Release-Version übers Netz lädt — dieser Weg braucht also ebenfalls Internet, ein Offline-Versuch scheitert genau hier. *Nur* `setup.bat` ausführen: Der Ordner enthält auch `Thoughtborne.bat`, und ein Doppelklick darauf aus dem Downloads-Ordner würde eine fehlplatzierte Kopie mit eigener `.env`, eigenem `history/` und eigenem `.venv` starten (es warnt vorher, die Warnung lässt sich aber wegklicken). Ist das Setup fertig, startet es Thoughtborne selbst; danach startet der Startmenü-Eintrag **Thoughtborne** das Tool.

**Auf einem verwalteten oder Firmengerät** kann eine maschinenweite Gruppenrichtlinie die Ausführungsrichtlinie des Skripts überschreiben und es blockieren. Schlägt der Installer dort fehl, einen der manuellen Wege unten nutzen oder die IT-Abteilung fragen.

**Smart App Control** kann unsignierte Installationsskripte blockieren — die ZIP-Spur genauso wie den Einzeiler. Nachsehen unter Windows-Sicherheit > App- und Browsersteuerung > Smart App Control; ist die Funktion eingeschaltet, lässt sie sich für die Installation ausschalten und danach wieder einschalten (Letzteres nur auf Windows 11 24H2 oder neuer mit den Updates ab April 2026 und nur bei aktivierten optionalen Diagnosedaten — sonst bleibt sie bis zum Zurücksetzen oder Neuinstallieren von Windows aus).

**Aktualisieren.** Den Einzeiler erneut ausführen oder `setup.bat` im Installationsordner — kein manueller Download nötig. Das Update holt die aktuelle Release-Version selbst und behält Aufnahmen, Keys und Einstellungen.

**Deinstallieren.** Thoughtborne registriert sich wie jedes andere Programm: **Einstellungen > Installierte Apps > Thoughtborne > Deinstallieren**. Das entfernt die Programmdateien und den Startmenü-Eintrag und behält Aufnahmen, Transkripte und API-Key — es sei denn, man setzt das Häkchen, das sie mitlöscht. Kein Adminrecht.

**Bereits einen Git-Clone in Benutzung?** Der Installer legt eine *separate* Kopie unter `%LOCALAPPDATA%\Programs\Thoughtborne` an, mit eigener `.env`, eigenem `history/` und eigenem `.venv` — die vorhandenen Daten bleiben unberührt, und der Clone läuft unverändert weiter (weiter mit `git pull` aktualisieren). Zum Umstieg zuerst `.env`, `personal_settings.json` und `history/` in den neuen Ordner kopieren.

**Python lieber selbst einrichten oder von einem Git-Clone aus arbeiten?** Die Wege unten sind die manuellen Alternativen zum Installer oben — einen wählen. Die Befehle funktionieren in PowerShell wie in cmd.

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

   Oder das Quell-ZIP von GitHub herunterladen und entpacken (der grüne **Code**-Button — nicht das Release-Asset `thoughtborne.zip` oben); der entpackte Ordner heißt `thoughtborne-windows-main`, das `cd` entsprechend anpassen.

3. **API-Keys einrichten:** `.env.example` als `.env` kopieren und mindestens einen Key eintragen — wo es die Keys gibt, steht unter [API-Keys](#api-keys). Oder diesen Schritt überspringen: Beim ersten Start ohne Key öffnet sich [die Einstellungs-App](#die-einstellungs-app) und führt hindurch.

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

Ohne uv funktioniert der klassische Weg weiterhin. Wichtig: **Python 3.10–3.12 oder 3.13.1+, nicht 3.14** — PyAudio liefert für 3.14 noch keine vorkompilierten Wheels, die Installation bricht dort mit einem Build-Fehler ab.

```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python thoughtborne.py
```

## Die Einstellungs-App

Die Konfiguration hat eine grafische Eingangstür: eine kleine Einstellungs-App — ein Fenster, das zugleich Erststart-Assistent und alltäglicher Einstellungsdialog ist, auf Deutsch oder Englisch (umschaltbar im Kopfbereich). Beim ersten Start ohne API-Key öffnet Thoughtborne sie automatisch und führt durch drei Seiten — Transkriptionsanbieter & API-Key (mit Live-Prüfung „Key testen"), Hotkeys (Push-to-talk an oder aus, zwei Ein-Klick-Preset-Schemata oder jede Kombination pro Aktion per Tastendruck aufnehmen), Verhalten (Standard-Engine, dazu Wegweiser für Tray und Admin-Fenster). Später, sobald ein Key gespeichert ist, öffnest du dasselbe Fenster jederzeit aus dem laufenden Tool mit **`Ctrl+Alt+G`** — es ist Teil von Thoughtborne, kein eigenes Programm. Speichern mit vorhandenem Key heißt **Speichern & neu starten**: Es speichert, startet das Tool für dich neu, und die Änderungen greifen sofort — du musst nicht daran denken, es selbst neu zu starten (eine laufende Aufnahme wird vorher gesichert und lässt sich nach dem Neustart mit dem Wiederhol-Hotkey erneut transkribieren). Beendet sich das Tool nicht innerhalb weniger Sekunden, sagt sie das und lässt es unangetastet weiterlaufen; es wird nie etwas erzwungen geschlossen.

Die App schreibt dieselben zwei Dateien, die sich auch von Hand pflegen lassen — `.env` (die Keys) und `personal_settings.json` (Hotkeys, Standard-Engine, Push-to-talk) — und bearbeitet sie chirurgisch: Handgepflegtes wie das Erkennungs-Vokabular und jeder Kommentar bleiben unangetastet, und Hotkeys landen nur dort in der Datei, wo sie von den Defaults abweichen. Ein laufendes Thoughtborne übernimmt Änderungen beim nächsten Start (kein Live-Reload). Alles, was die App tut, bleibt von Hand editierbar — sie ist ein Frontend für die genannten Dateien, kein Ersatz (siehe [Anpassung](#anpassung)).

## API-Keys

Die Keys gehören einem selbst — die Anmeldung läuft direkt beim Anbieter. Audio geht an die gewählte Transkriptions-API und nirgendwo darüber hinaus; Aufnahmen und Transkripte werden lokal archiviert, nirgendwo sonst. Jede integrierte API muss mindestens ein Opt-out vom Training auf Nutzerdaten bieten ([VISION.md](VISION.md)). Mindestens ein Key ist nötig — ganz ohne Key startet das Tool nicht und benennt präzise, welche Keys fehlen.

**Groq** (kostenlos): Auf [console.groq.com](https://console.groq.com) registrieren → API-Keys-Seite ([console.groq.com/keys](https://console.groq.com/keys)) → Key erstellen und sofort kopieren (er wird nur einmal angezeigt) → in die Zeile `GROQ_API_KEY=` der `.env` eintragen.

**Soniox** (Prepaid): Auf [soniox.com](https://soniox.com) registrieren → in der Console ([console.soniox.com](https://console.soniox.com)) ein kleines Prepaid-Guthaben aufladen (nötig, bevor die API funktioniert) → Key erstellen und kopieren → in die Zeile `SONIOX_API_KEY=` der `.env` eintragen.

Nur ein Groq-Key? Nichts umzustellen: Der Start überspringt die Soniox-Einträge automatisch, zeigt sie in der Engine-Übersicht ausgegraut und startet auf der ersten verfügbaren API. Wer stattdessen ohne diese Hinweise direkt auf Groq starten will, trägt `"api": "groq-large"` in den `defaults`-Block der `personal_settings.json` ein (unten) — nur das gilt als *konfiguriert* und schlägt damit auch die Engine, die sich Thoughtborne vom letzten Wechsel gemerkt hat; `DEFAULT_API = "groq-large"` in der `config.py` funktioniert ebenfalls, hat gegenüber einer gemerkten Engine aber das Nachsehen.

## Der erste Start

Das Tool starten — über den Startmenü-Eintrag **Thoughtborne** (wer den Installer genutzt hat), per Doppelklick auf `Thoughtborne.bat` oder mit `uv run thoughtborne.py`. Ein Konsolenfenster öffnet sich mit einem Start-Banner, das die aktive API und die Hotkey-Liste zeigt; Details landen in `thoughtborne.log`.

**Tipp — per Tastatur starten.** Wer mit dem Einzeiler oder ZIP installiert hat, findet den Eintrag **Thoughtborne** bereits im Startmenü — um ihn per Tastendruck zu starten, Rechtsklick darauf → **Eigenschaften** → ins Feld **Tastenkombination** klicken und eine freie Kombination drücken (`Ctrl+Alt+1` ist frei; keiner der In-App-Hotkeys nutzt es), und ein Druck startet das Tool. Bei einem Git-Clone gibt es noch keinen Startmenü-Eintrag: eine Windows-Verknüpfung auf `Thoughtborne.bat` anlegen, im Startmenü oder auf dem Desktop ablegen (Windows berücksichtigt Tastenkürzel nur dort) und ihr dasselbe Kürzel geben. Diese Verknüpfung auf `C:\Windows\System32\cmd.exe /c "C:\Pfad\zu\Thoughtborne.bat"` statt direkt auf die `.bat` zeigen lassen — der Start ist identisch, aber der Eintrag bietet dann per Rechtsklick auch *Als Administrator ausführen* an, was eine Verknüpfung direkt auf die `.bat` nie bekommt (der Eintrag des Installers nutzt diese Form bereits). Siehe den Admin-Fenster-Hinweis unter Troubleshooting.

Dann diktieren:

1. Ein beliebiges Textfeld fokussieren (das einfache Notepad eignet sich gut).
2. `Ctrl+Alt+W` drücken und einen Satz sprechen — die Konsole bestätigt die laufende Aufnahme.
3. `Ctrl+Alt+A` drücken — das Transkript erscheint an der Cursor-Position.

**Selbsttest:** `Ctrl+Alt+T` transkribiert die mitgelieferte `test_audio.mp3` über die aktive API und fügt das Ergebnis an der Cursor-Position ein (vorher ein Textfeld fokussieren) — so lässt sich am schnellsten prüfen, ob alles funktioniert.

Die eigenen Daten bleiben lokal: Jedes Diktat liegt in einem gemeinsamen `history/`-Ordner im Projektverzeichnis — Aufnahmen als MP3 in `history/audio/`, Transkripte in `history/transcripts/`, gepaart über den Zeitstempel. Jeder Dateiname trägt zusätzlich ein Engine-Kürzel — `SonLive-v5`, `Son-v5`, `GWhisperTur-v3` oder `GWhisperLar-v3` —, das die erzeugende Engine benennt (nie transkribierte Aufnahmen behalten den reinen Zeitstempel-Namen). Das Start-Banner zeigt den Pfad und `Ctrl+Alt+6` öffnet den Ordner; beim Update von einer älteren Version werden die bisherigen Ordner `voice_archive/` und `text_archive/` beim ersten Start automatisch dorthin migriert. Schlägt eine Transkription fehl, wiederholt `Ctrl+Alt+R` sie aus der archivierten Aufnahme — und zwar mit der gerade gewählten Engine, sofern diese eine Datei erneut einlesen kann, sodass sich eine vorübergehend gestörte API umgehen lässt, indem man per `Ctrl+Alt+L` die Engine wechselt und erneut `Ctrl+Alt+R` drückt. An eine nicht transkribierte Aufnahme erinnert das Tool nur einmal — beim nächsten Start danach; anschließend bleibt sie per `Ctrl+Alt+R` abrufbar, ohne erneut daran zu erinnern. Kommt die Standard-Engine leer zurück, ohne dass dabei etwas schiefging, enthielt die Aufnahme keine Sprache — sie bleibt in `history/` erhalten, und das Tool sagt das ehrlich, statt eine sinnlose Wiederholung anzubieten.

`Ctrl+Alt+4` beendet das Tool.

## Hotkeys

| Hotkey | Funktion |
|--------|----------|
| `Ctrl+Alt+W` | Aufnahme starten (geht auch, während eine vorherige Aufnahme noch transkribiert wird) |
| `Ctrl+Alt+A` | Stopp + an der Cursor-Position einfügen (über die Zwischenablage — schneller) |
| `Ctrl+Alt+D` | Stopp + einfügen + Enter drücken (Senden mit einem Tastendruck, für Chats) |
| `Ctrl+Alt+H` | Stopp + an der Cursor-Position einfügen (simuliertes Tippen) |
| `Ctrl+Alt+Y` | Stopp + nur transkribieren — später mit `A` oder `H` einfügen |
| `Ctrl+Alt+X` | Aufnahme abbrechen (nichts wird eingefügt) |
| `Ctrl+Alt+R` | Letzte fehlgeschlagene Transkription wiederholen (aus der archivierten Aufnahme) |
| `Ctrl+Alt+L` | Transkriptions-API wechseln (zyklisch: Soniox Live → Soniox → Groq Whisper Large v3 → Groq Whisper Turbo v3); die gewählte Engine wird gemerkt und startet beim nächsten Mal |
| `Ctrl+Alt+6` | Den Ordner mit Aufnahmen & Transkripten (`history/`) im Explorer öffnen |
| `Ctrl+Alt+G` | Die Einstellungs-App öffnen (Zahnrad) |
| `Ctrl+Alt+T` | Selbsttest: die mitgelieferte `test_audio.mp3` transkribieren |
| `Ctrl+Alt+4` | Programm beenden |

Transkripte werden immer in Aufnahme-Reihenfolge eingefügt, auch wenn mehrere Aufnahmen parallel verarbeitet werden.

**Der Tipp-Einfügeweg hat eine Längenbegrenzung.** `Ctrl+Alt+H` fügt per simuliertem Tippen ein — der Ausweichweg für Apps, die Einfügen (Paste) blockieren. Windows verwirft bei sehr langem simuliertem Tippen den Großteil der Zeichen still, sobald die Ziel-App nicht mehr hinterherkommt; deshalb ist dieser Weg bewusst auf **4.000 Zeichen** begrenzt (rund sieben Minuten ununterbrochenes Diktat). Oberhalb der Grenze endet der getippte Text mit einem kurzen Hinweis in eckigen Klammern — verloren geht nichts: das vollständige Transkript bleibt in `history/` und lässt sich in einem Stück über den Zwischenablage-Hotkey (`Ctrl+Alt+A`) einfügen. Die Zwischenablage-Wege (`A`/`D`) sind nicht betroffen.

## Anpassung

Das meiste davon lässt sich auch grafisch in [der Einstellungs-App](#die-einstellungs-app) erledigen — sie schreibt genau die unten beschriebenen Dateien, beides ist frei kombinierbar.

**Erkennungs-Vokabular** (empfohlen): `personal_settings.example.json` als `personal_settings.json` kopieren und den `vocabulary`-Block mit eigenen Namen, Fachbegriffen und häufigen Fremdwörtern füllen — sie werden dem Sprachmodell als Kontext mitgegeben und verbessern die Erkennung spürbar. Genutzt von allen Soniox-Engines — Soniox Live und dem Soniox-Upload-Slot; die Groq-APIs ignorieren es. Fehlt die Datei, läuft das Tool einfach ohne Personalisierung.

```
copy personal_settings.example.json personal_settings.json
```

**Push-to-talk (optional):** ein zweiter Weg zu diktieren, für kurze, schnelle Einwürfe. **Links-Strg** tippen, loslassen, dann gleich darauf Links-Strg **drücken und halten** — die Aufnahme läuft, solange gehalten wird, und beim Loslassen wird das Transkript an der Cursor-Position eingefügt (genau wie bei den Hotkeys). Standardmäßig **aus**; einschalten in der Einstellungs-App (`Ctrl+Alt+G`) im Tab **Hotkeys**, wo die Geste erklärt wird, oder über `enabled` im `push_to_talk`-Block der `personal_settings.json`. Weil Strg der Auslöser ist, sorgt ein zwingender AltGr-Filter dafür, dass deutsche QWERTZ-Zeichen (`@ \ { } [ ] | € ~`) ihn nie auslösen — AltGr ist `Strg+Alt`, und die Geste zählt nur ein *bloßes* Strg (kein Alt, keine andere Taste gedrückt), sodass auch `Strg+C` → `Strg+V` und jede andere Strg-Kombination unberührt bleiben. Im selben Block konfigurierbar: die Auslöser-Taste (`lctrl`, `rctrl` oder `lalt`), der Einfüge-Pfad (`clipboard` wie `A` ist der Standard; `type` wie `H` ist der Fallback für Apps, die Einfügen blockieren; außerdem `send` wie `D` oder `no_insert`) und die drei Zeit-Schwellen. **JetBrains-IDEs:** Doppel-Strg ist dort der Shortcut „Run Anything" und kollidiert daher, wenn eine IDE der IntelliJ-Familie den Fokus hat — die Option „Disable double modifier key shortcuts" in den Advanced Settings der IDE *aktivieren*, um diese Kollision abzuschalten. Hinweis: Wie alle Hotkeys erreicht auch Push-to-talk kein Fenster, das mit erhöhten Rechten (als Administrator) läuft — Windows blockiert dort die Eingabe von nicht-erhöhten Prozessen. Das ist dieselbe Grenze wie bei den bestehenden Hotkeys, keine neue Einschränkung.

**Soniox-Live-Endpointing (optional):** Ein standardmäßig inaktiver `soniox_endpointing`-Block in der `personal_settings.json` justiert, wann die Soniox-Live-Engine einen gesprochenen Satz als beendet ansieht (Endpoint-Erkennung) — etwa wie lange sie bei einer Pause wartet, bevor sie den Satz abschließt. Die Felder, ihre Wertebereiche und die von Soniox dokumentierte Ausgangsempfehlung fürs Diktieren stehen im Kommentar des Blocks in `personal_settings.example.json`; ohne den Block wird nichts gesendet und es gelten unverändert die Soniox-Defaults.

**Hotkeys & Standard-Engine (optional):** Die Tastenkombinationen und die beim Start gewählte Engine lassen sich in der `personal_settings.json` überschreiben, ohne Code zu ändern — ein `hotkeys`-Block (Teil-Überschreibung nach Aktionsnamen; F-Tasten `f1`–`f24` sind erlaubt, auch modifier-lose wie `f9`) und ein `defaults`-Block (`"api"`, eine der vier Engines). `config.py` behält die eingebauten Defaults; eine gelistete Aktion oder eine gültige `api` ersetzt sie, und eine unbekannte Aktion, eine nicht parsbare Kombination, eine Kollision oder eine unbekannte Engine wird mit einer Warnung in `thoughtborne.log` ignoriert, sodass das Tool immer startet. Die Kommentare in `personal_settings.example.json` listen die gültigen Aktionsnamen und das Format.

**Die zuletzt gewählte Engine wird gemerkt (automatisch):** Unabhängig von diesem Block merkt sich Thoughtborne, auf welche Engine per `Ctrl+Alt+L` gewechselt wurde, und startet beim nächsten Mal darauf — ganz ohne Einstellung. Festgehalten wird dieser eine Wert in `runtime_state.json` neben dem Log: vom Tool selbst geschrieben, keine Einstellungsdatei und gefahrlos löschbar (beim nächsten Wechsel steht sie wieder da). Gemerkt wird nur ein selbst ausgelöster Wechsel; überspringt der Start eine Engine, weil deren Key fehlt, ist das eine Störung und keine Entscheidung — und wird nie festgehalten. Eine selbst gesetzte `defaults.api` behält das letzte Wort: Die bewusste Festlegung schlägt das Gedächtnis, das nur dort einspringt, wo nichts konfiguriert ist. Die Einstellungs-App passt dazu. Ihr Feld **Engine beim Start** bietet zwei ausdrückliche Modi: *mit der zuletzt gewählten Engine starten* — die gemerkte, schreibgeschützt angezeigt — oder *immer mit einer festen Engine starten*, was eine `defaults.api` schreibt, die das Gedächtnis schlägt. Eine feste Engine bleibt dann über Neustarts hinweg bestehen, auch nach einem späteren `Ctrl+Alt+L`-Wechsel (den Standard eingeschlossen); zurück auf *zuletzt gewählt* entfernt die Festlegung. Das Feld unangetastet zu lassen ändert in beiden Modi nichts.

**Konsole aus der Taskleiste (optional):** Thoughtborne läuft in einem Konsolenfenster, das wie jedes andere in der Taskleiste sitzt. Um es aus dem Weg zu räumen — läuft weiter, ein Klick zurück —, wenn dieses Fenster in **Windows Terminal** läuft (dem Standard-Konsolenhost auf aktuellem Windows 11), erledigen das zwei von Terminals eigenen Einstellungen, ohne Zusatz-Tool und ohne Admin-Rechte. Terminals Einstellungen öffnen (`Ctrl+,`) und unter **Interaktion** beide aktivieren:

- „Terminal bei Minimierung im Infobereich ausblenden" (`minimizeToNotificationArea`) — Minimieren schickt das Fenster dann in den Infobereich (den System-Tray) statt in die Taskleiste: Der Taskleisten-Button verschwindet und alles läuft weiter, das Diktieren eingeschlossen (es ist hotkey-gesteuert und funktioniert bei verstecktem Fenster).
- „Immer ein Symbol im Infobereich anzeigen" (`alwaysShowNotificationIcon`) — hält ein dauerhaftes Tray-Icon als Anker bereit: Ein Einzelklick stellt das Fenster wieder her, ein Rechtsklick listet es auf.

Einmaliger Handgriff: Windows steckt ein neues Tray-Icon zunächst ins Überlauf-Ausklappmenü (das `^`-Chevron) — das Terminal-Icon einmal von dort in den sichtbaren Tray-Bereich ziehen, damit es erreichbar bleibt. Zwei ehrliche Grenzen: Beide Einstellungen sind **global**, wirken also auf *jedes* Windows-Terminal-Fenster (und Minimieren trayt immer das ganze Fenster, nie einen einzelnen Tab) — belanglos, wenn man das Terminal sonst nicht nutzt, eine bewusste Wahl, wenn doch; und der Weg braucht Windows Terminal — unter dem klassischen `conhost`-Host (ältere Setups oder wenn man das Standard-Terminal umgestellt hat) gibt es diese Schalter nicht. Unter Windows 10 ist Terminal installierbar und als Standard-Terminal setzbar. Die GUI-Schalter sind der saubere Weg — Terminals `settings.json` von Hand zu editieren ist nicht nötig.

**Einstellungen in `config.py`:** Die Konfiguration besteht bewusst aus einfachen Konstanten mit Kommentaren. Was die meisten anpassen:

- `DEFAULT_API` — die API beim Start, wenn weder eine `defaults`-Überschreibung noch eine gemerkte Engine greift (`"soniox-live"`, `"soniox"`, `"groq-large"`, `"groq"`); ohne Code-Änderung überschreibbar im `defaults`-Block der `personal_settings.json` (oben).
- `LANGUAGE` — Default `"de"`. Englisch funktioniert (`"en"`), aber Artefakt-Filter und Tuning zielen auf Deutsch — ehrliche Erwartungen ([VISION.md](VISION.md)).
- `HOTKEYS` — die Standard-Tastenkombinationen. Zum Ändern besser den `hotkeys`-Block der `personal_settings.json` nutzen (oben); `config.py` hält die Defaults. Sonderzeichen wie `#` und Nicht-ASCII-Buchstaben meiden: Sie können in manche Apps hineingetippt werden und haben keinen festen Tastencode, werden also beim Start gegen das aktive Tastaturlayout aufgelöst. Alle mitgelieferten Defaults sind einfache Buchstaben, Ziffern oder F-Tasten; `ü` wird weiterhin akzeptiert, wenn man es möchte.

Weitere Einstellungen (parallele Transkriptionen, Audio-Trimming, …) sind als Kommentare direkt in `config.py` dokumentiert.

**Oder dem Coding-Agenten sagen.** Die Konfigurations-Strategie des Projekts ist lesbarer Code statt einer ausufernden Konfigurationsfläche ([VISION.md](VISION.md)): die gewünschte Änderung dem eigenen KI-Coding-Agenten beschreiben — [`AGENTS.md`](AGENTS.md) gibt ihm die Spielregeln für dieses Repo.

## Troubleshooting

**PyAudio-Installation schlägt fehl (pip-Weg).** PyAudio liefert offizielle Windows-Wheels für Python 3.10–3.13 — `pip install` braucht dort keinen Compiler. Ein Build-Fehler heißt meist Python 3.14: auf 3.13 wechseln oder den uv-Weg nutzen (uv wählt automatisch ein passendes Python).

**Die Einstellungs-App öffnet sich unter Python 3.13.0 nicht.** Genau dieser CPython-Patch hat einen Fehler, der tkinter in einer virtuellen Umgebung unter Windows zerstört — das Einstellungs-/Onboarding-Fenster stürzt beim Start ab (`Can't find a usable init.tcl`); das Diktier-Tool selbst ist nicht betroffen. Python 3.13.1 oder neuer nutzen, oder ein beliebiges 3.10–3.12; jede Version außer 3.13.0 ist in Ordnung. Der uv-Weg umgeht das bereits (er baut auf 3.13.0 keine venv); auf dem pip-Weg den Interpreter selbst wählen (z. B. `py -3.12 -m venv .venv`).

**`python` öffnet den Microsoft Store.** Das ist der Store-Alias-Stub auf einem Rechner ohne Python — den `py`-Launcher nutzen (wie in den pip-Befehlen oben) oder den uv-Weg.

**`winget` nicht gefunden.** uv über die [offizielle Installationsanleitung](https://docs.astral.sh/uv/getting-started/installation/) installieren oder den pip-Weg nehmen.

**Das Tool startet, aber kein Audio / leere Transkripte.** Mikrofon-Berechtigung in Windows prüfen (Einstellungen > Datenschutz und Sicherheit > Mikrofon) und das Standard-Eingabegerät; `thoughtborne.log` protokolliert, welches Eingabegerät genutzt wurde.

**Ein Hotkey registriert sich nicht** (eine `FAILED:`-Zeile im Start-Log). Ein anderes Programm besitzt die Kombination bereits — globale Hotkeys sind in Windows exklusiv. Die Kombination im `hotkeys`-Block der `personal_settings.json` ändern (`config.py` hält die Defaults) — siehe [Anpassung](#anpassung).

**Einfügen bewirkt in einem bestimmten Fenster nichts.** Die Ziel-App läuft mit erhöhten Rechten (als Administrator), und Windows' User Interface Privilege Isolation (UIPI) blockiert simulierte Eingaben aus einem nicht-erhöhten Prozess — das Transkript entsteht, landet aber nirgends, das Diktat scheint also zu verschwinden. Um in Admin-Fenster zu diktieren, Thoughtborne selbst mit erhöhten Rechten starten: Rechtsklick auf `Thoughtborne.bat` im Explorer — oder auf den Startmenü-Eintrag **Thoughtborne** (der Installer legt ihn in der `cmd.exe /c`-Form an, sodass *Als Administrator ausführen* per Rechtsklick angeboten wird; eine selbst angelegte Verknüpfung braucht dieselbe Form — siehe den Start-Tipp) — und *Als Administrator ausführen* wählen; der UAC-Dialog nennt *Windows-Befehlsprozessor*, weil Windows Batch-Dateien über `cmd.exe` ausführt. Es kann immer nur eine Instanz laufen — globale Hotkeys sind in Windows exklusiv. Startet man Thoughtborne, während es schon läuft, bemerkt das zweite Fenster das, zeigt einen kurzen Hinweis und schließt sich selbst; die laufende Instanz behält die Tasten. Vor dem Start einer erhöhten Kopie ist also nichts aufzuräumen — soll aber die *erhöhte* die Tasten halten, zuerst die laufende Instanz schließen (`Strg+Alt+4`), dann erhöht starten.

**Erster Start sehr langsam oder schlägt offline fehl.** uv lädt einmalig Python und die Dependencies; dafür braucht es dieses eine Mal Internet.

**API-Fehler.** Keys in der `.env` und die Internetverbindung prüfen; im Free Tier die [Limits](#die-modell-aufstellung) im Blick behalten.

## Projekt & Links

- [thoughtborne.app](https://thoughtborne.app) — die Projekt-Website.
- [VISION.md](VISION.md) — warum es das Tool gibt, der Qualitätsmaßstab, was Entscheidungen leitet (englisch).
- [CHANGELOG.md](CHANGELOG.md) — was sich geändert hat, Release für Release.
- [LICENSE](LICENSE) — MIT.
- Für KI-Coding-Agenten: [AGENTS.md](AGENTS.md) (Arbeiten in diesem Repo) · [llms-install.md](llms-install.md) (geführtes Setup).
- **macOS:** Es gibt einen Schwester-Port — [thoughtborne-macos](https://github.com/timwessels/thoughtborne-macos): drei Transkriptions-APIs statt vier, sonst analog; as-is verfügbar.

Issues und Contributions sind willkommen. Thoughtborne ist seit Jahren das tägliche Arbeitswerkzeug des Maintainers und wird aktiv gepflegt.
