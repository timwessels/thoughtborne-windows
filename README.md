# Thoughtborne

Hotkey-gesteuertes Voice-to-Text-Tool für Windows. Sprachaufnahmen werden in Echtzeit transkribiert und der Text direkt an der Cursor-Position eingefügt – in jeder Anwendung.

> **Note:** This README is currently German-only — Thoughtborne is a German-first dictation tool, and an English README is planned. For the project's purpose and direction in English, see [VISION.md](VISION.md).

> **macOS:** Es gibt einen Schwester-Port: [thoughtborne-macos](https://github.com/timwessels/thoughtborne-macos) – drei statt vier Transkriptions-APIs, sonst analog.

## Features

- **Hotkey-Steuerung**: Globale Tastenkombinationen, funktionieren in jeder Anwendung
- **Vier APIs**: Soniox Live (WebSocket Streaming, Default), Soniox (Datei-Upload: kurze Aufnahmen präzise via v2 sync, lange via v4 async – mit automatischem v4-Fallback), Groq Large (genauer, kostenlos nutzbar), Groq (am schnellsten, kostenlos nutzbar) – umschaltbar per Hotkey
- **Parallele Verarbeitung**: Neue Aufnahme starten während vorherige noch transkribiert wird
- **Sequentielle Ausgabe**: Texte werden in Aufnahme-Reihenfolge eingefügt
- **Zwei Einfügemethoden**: Keyboard-Simulation oder Clipboard (schneller)
- **Send-Funktion**: Text einfügen und automatisch Enter drücken (für Chat-Eingaben)
- **Audio-Archivierung**: Alle Aufnahmen und Transkripte werden automatisch gespeichert
- **Deutsch als Default**: Optimiert für deutsche Sprache

## Hotkeys

| Hotkey | Funktion |
|--------|----------|
| `Ctrl+Alt+W` | Aufnahme starten |
| `Ctrl+Alt+A` | Stopp + Text einfügen (Keyboard) |
| `Ctrl+Alt+D` | Stopp + Text einfügen (Clipboard, schneller) |
| `Ctrl+Alt+H` | Stopp + Text einfügen + Enter (für Chats) |
| `Ctrl+Alt+Y` | Stopp + nur verarbeiten (später mit A/D einfügen) |
| `Ctrl+Alt+X` | Aufnahme abbrechen |
| `Ctrl+Alt+R` | Letzte fehlgeschlagene Transkription wiederholen |
| `Ctrl+Alt+L` | API wechseln (Soniox Live → Soniox → Groq Large → Groq) |
| `Ctrl+Alt+Ü` | Test mit `test_audio.mp3` |
| `Ctrl+Alt+4` | Programm beenden |

## Installation

1. **uv installieren** (einmalig)

   Thoughtborne nutzt [uv](https://docs.astral.sh/uv/) als Python-Projektmanager: uv lädt automatisch ein passendes Python und alle Dependencies in ein lokales `.venv` – ein vorinstalliertes Python ist nicht nötig.

   ```bash
   winget install --id=astral-sh.uv -e
   ```

   Ohne winget: [uv-Installationsanleitung](https://docs.astral.sh/uv/getting-started/installation/)

2. **Repository holen**

   ```bash
   git clone https://github.com/timwessels/thoughtborne-windows.git
   cd thoughtborne-windows
   ```
   Oder das ZIP von GitHub herunterladen und entpacken.

3. **API-Keys einrichten**

   Die Vorlage `.env.example` als `.env` kopieren und die Keys eintragen:
   ```
   GROQ_API_KEY=dein_groq_key
   SONIOX_API_KEY=dein_soniox_key
   ```

   API-Keys erhältlich bei:
   - Groq: https://console.groq.com/keys
   - Soniox: https://soniox.com

4. **Optional: Eigene Begriffe für die Spracherkennung hinterlegen**

   Soniox Live und der v4-Pfad des Soniox-Upload-Slots (lange Aufnahmen, Fallback) unterstützen einen "Context"-Mechanismus: Fachbegriffe, Eigennamen, häufig genutzte Wörter werden dem Modell als Hinweis mitgegeben. Das verbessert die Erkennung spürbar.

   ```bash
   cp personal_settings.example.json personal_settings.json
   ```
   Datei öffnen und eigene Terms im `vocabulary`-Block eintragen. Fehlt die Datei, läuft das Tool ohne Personalisierung.

5. **Starten**

   ```bash
   uv run thoughtborne.py
   ```
   Oder Doppelklick auf `Thoughtborne.bat` – sie startet das Tool über uv und bietet die uv-Installation an, falls uv fehlt. Beim ersten Start lädt uv einmalig Python und alle Dependencies (Internetverbindung nötig). Danach hält uv alles automatisch aktuell – auch nach einem `git pull` mit neuen Dependencies sind keine manuellen Schritte nötig.

### Alternative: klassisch mit pip + venv

Ohne uv funktioniert der klassische Weg weiterhin. Wichtig: **Python 3.10–3.13, nicht 3.14** – PyAudio liefert für 3.14 noch keine vorgebauten Wheels, die Installation bricht dort mit einem Build-Fehler ab.

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-optional.txt   # Soniox-SDK (schneller v2-sync-Pfad, siehe Dependencies)
python thoughtborne.py
```

## Projektstruktur

```
Thoughtborne/
├── thoughtborne.py              # Hauptprogramm, Hotkey-Handling
├── audio_handler.py        # Audio-Aufnahme, WAV/MP3-Konvertierung
├── transcriber.py          # API-Abstraktion (Soniox, Groq)
├── output_handler.py       # Text-Ausgabe, Clipboard-Operationen
├── config.py               # Konfiguration, Hotkeys, API-Settings
├── hotkey_manager.py       # Win32 RegisterHotKey API
├── .env                    # API-Keys (nicht in Git)
├── pyproject.toml          # Projekt-Metadaten + Dependencies (uv)
├── uv.lock                 # Gepinnte Dependency-Versionen (uv)
├── requirements.txt        # Python Dependencies (pip-Fallback)
├── requirements-optional.txt
├── Thoughtborne.bat             # Windows-Starter
├── test_audio.mp3               # Beispiel-Audio für den Selbsttest (Ctrl+Alt+Ü)
│
├── voice_archive/          # Archivierte Aufnahmen (auto-erstellt)
├── text_archive/           # Archivierte Transkripte (auto-erstellt)
├── thoughtborne.log       # Log-Datei (auto-erstellt)
└── .venv/                  # Python-Umgebung (auto-erstellt von uv)
```

## Konfiguration

In `config.py` anpassbar:

| Setting | Default | Beschreibung |
|---------|---------|--------------|
| `DEFAULT_API` | `"soniox-live"` | Start-API (soniox-live/soniox/groq-large/groq) |
| `LANGUAGE` | `"de"` | Sprache für Transkription |
| `MAX_PARALLEL_TRANSCRIPTIONS` | `3` | Max. parallele Verarbeitungen |
| `AUDIO_TRIM_END_MS` | `300` | Millisekunden am Ende trimmen (entfernt Hotkey-Klick) |
| `HOTKEYS` | siehe config.py | Alle Tastenkombinationen |

## Dependencies

Mit uv werden alle Dependencies automatisch installiert und über `uv.lock` auf reproduzierbare Versionen gepinnt – inklusive des Soniox-SDKs. Die folgenden Listen beschreiben den pip-Weg:

**Erforderlich:**
```
groq>=0.4.0
pyaudio>=0.2.11
keyboard>=0.13.5
soundfile>=0.12.1
numpy>=1.21.0
pyperclip>=1.8.2
python-dotenv>=1.0.0
pyautogui>=0.9.54
httpx>=0.28.0
websockets>=15.0.0
```

**Optional** (im uv-Weg automatisch enthalten):
```
soniox>=1.10.1,<2    # Soniox-SDK für den schnellen v2-sync-Pfad des Soniox-Slots (2.x ist inkompatibel);
                     # ohne SDK läuft der Slot vollständig über v4 async (funktioniert, aber langsamer)
```

## Systemanforderungen

- **Plattform**: Windows
- **Python**: 3.10–3.13 (mit uv automatisch – uv lädt ein passendes Python; 3.14 wird noch nicht unterstützt, da PyAudio dafür keine Wheels liefert)
- **Mikrofon**: Zugriff erforderlich
- **Internet**: Für API-Zugriff

## API-Vergleich

| | Soniox | Soniox Live | Groq Large | Groq |
|--|--------|-------------|------------|------|
| **Geschwindigkeit** | ~4-6s (kurz) / ~10-40s (lang, async) | ~0.5s nach Stop | ~1s | ~0.7s |
| **Genauigkeit** | Sehr gut | Sehr gut | Gut–Sehr gut | Gut |
| **Geeignet für** | Fachbegriffe, polierter Text | Schnellstes Ergebnis (Default) | Kostenloser Einstieg | Schnelle Notizen |
| **Modell** | de_v2 (gRPC) + stt-async-v4 | stt-rt-v4 | Whisper Large V3 | Whisper Large V3 Turbo |
| **Hosting** | Soniox Cloud | Soniox Cloud | Groq Cloud | Groq Cloud |
| **Context** | Nein (kurz) / Ja (lang) | Ja | Nein | Nein |

**Default ist Soniox Live** – umschaltbar mit `Ctrl+Alt+L`.

Der Soniox-Slot arbeitet zweistufig: Aufnahmen unter 58 Sekunden laufen über die schnelle, synchrone v2-API; Aufnahmen ab 58 Sekunden sowie der automatische Fallback bei einem v2-Ausfall laufen über die v4-async-REST-API. Welcher Pfad lief, steht im Log – fürs Diktieren muss man den Unterschied nicht kennen.

**Kostenlos testen:** Beide Groq-Modelle laufen im kostenlosen Free Tier von Groq (Stand Juni 2026, pro Modell: 20 Anfragen/Minute, 2.000 Anfragen/Tag, 7.200 Audio-Sekunden/Stunde, 28.800 Audio-Sekunden/Tag) – damit lässt sich Thoughtborne ohne Bezahlung ausprobieren; Groq Large ist dabei die genauere, Groq die schnellste Option. Wer nur einen Groq-Key hat, muss nichts umstellen: Thoughtborne startet automatisch auf der ersten API, deren Key vorhanden ist, und nennt die übersprungenen Einträge beim Start. Wer ohne diese Hinweise direkt auf Groq starten will, stellt in `config.py` `DEFAULT_API` auf `"groq-large"` oder `"groq"` um. Soniox erfordert eine Guthaben-Aufladung vor der ersten Nutzung.

## Troubleshooting

**PyAudio-Installation schlägt fehl (pip-Weg):**

PyAudio liefert offizielle Windows-Wheels für Python 3.10–3.13 – `pip install pyaudio` braucht dort keinen Compiler. Bricht die Installation mit einem Build-Fehler ab, läuft vermutlich Python 3.14: auf Python 3.13 wechseln oder den uv-Weg nutzen (uv wählt automatisch ein passendes Python).

**Kein Audio-Input:**
- Mikrofon-Berechtigungen prüfen
- Standard-Audiogerät in Windows prüfen
- `thoughtborne.log` auf Geräteliste prüfen

**API-Fehler:**
- API-Keys in `.env` prüfen
- Internetverbindung prüfen
- API-Limits beachten

## Forschung: Bessere deutsche Modelle

Recherche und Batch-Vergleiche (249 Dateien, Feb 2026) zeigen die Stärken der verschiedenen Modelle:

| Modell | WER Deutsch | Stärken |
|--------|-------------|---------|
| Whisper Large V3 (Standard) | ~5.0% | Breite Sprachunterstützung |
| Soniox de_v2 | ~7.0% | Zuverlässigste Fachbegriff-/Namenerkennung |

(Interne Batch-Vergleiche; die ausführlichen Ergebnisse sind nicht Teil des Repos.)

## Architektur

```
Hotkey → Audio-Aufnahme → Transkription (Thread) → Output-Queue → Text-Einfügung
              ↓                    ↓
         voice_archive/      text_archive/

[Soniox Live: Audio wird parallel zur Aufnahme per WebSocket gestreamt]
```

Thread-Sicherheit durch Locks und atomare Operationen. Parallele Transkription möglich, Ausgabe erfolgt sequentiell in Aufnahme-Reihenfolge.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
