# Thoughtborne

Hotkey-gesteuertes Voice-to-Text-Tool für Windows. Sprachaufnahmen werden in Echtzeit transkribiert und der Text direkt an der Cursor-Position eingefügt – in jeder Anwendung.

## Features

- **Hotkey-Steuerung**: Globale Tastenkombinationen, funktionieren in jeder Anwendung
- **Fünf APIs**: Soniox Live (WebSocket Streaming, Default), Soniox v2 (präzise), Soniox v4 (async REST), Groq Large (genauer, kostenlos nutzbar), Groq (am schnellsten, kostenlos nutzbar) – umschaltbar per Hotkey
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
| `Ctrl+Alt+L` | API wechseln (Soniox Live → Soniox v2 → Groq Large → Groq → Soniox v4) |
| `Ctrl+Alt+Ü` | Test mit `test_audio.mp3` |
| `Ctrl+Alt+4` | Programm beenden |

## Installation

1. **Dependencies installieren**
   ```bash
   pip install -r requirements.txt

   # Optional: Soniox (für hohe Genauigkeit)
   pip install soniox
   ```

2. **API-Keys einrichten**

   `.env` Datei erstellen/bearbeiten:
   ```
   GROQ_API_KEY=dein_groq_key
   SONIOX_API_KEY=dein_soniox_key
   ```

   API-Keys erhältlich bei:
   - Groq: https://console.groq.com/keys
   - Soniox: https://soniox.com

3. **Optional: Eigene Begriffe für die Spracherkennung hinterlegen**

   Soniox v4 und Soniox Live unterstützen einen "Context"-Mechanismus: Fachbegriffe, Eigennamen, häufig genutzte Wörter werden dem Modell als Hinweis mitgegeben. Das verbessert die Erkennung spürbar.

   ```bash
   cp personal_settings.example.json personal_settings.json
   ```
   Datei öffnen und eigene Terms im `vocabulary`-Block eintragen. Fehlt die Datei, läuft das Tool ohne Personalisierung.

4. **Starten**
   ```bash
   python thoughtborne.py
   ```
   Oder per `Thoughtborne.bat` (Windows).

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
├── requirements.txt        # Python Dependencies
├── requirements-optional.txt
├── Thoughtborne.bat             # Windows-Starter
├── thoughtborne-spec.md         # Projekt-Spezifikation
│
├── voice_archive/          # Archivierte Aufnahmen (auto-erstellt)
├── text_archive/           # Archivierte Transkripte (auto-erstellt)
├── thoughtborne.log       # Log-Datei (auto-erstellt)
│
├── _backups/               # Code-Backups (siehe _backups/BACKUP_README.md)
├── _research/              # STT-Recherchen (siehe _research/README.md)
└── _archive/               # Alte/obsolete Dateien
```

## Konfiguration

In `config.py` anpassbar:

| Setting | Default | Beschreibung |
|---------|---------|--------------|
| `DEFAULT_API` | `"soniox-live"` | Start-API (soniox/soniox-v4/soniox-live/groq/groq-large) |
| `LANGUAGE` | `"de"` | Sprache für Transkription |
| `MAX_PARALLEL_TRANSCRIPTIONS` | `3` | Max. parallele Verarbeitungen |
| `AUDIO_TRIM_END_MS` | `300` | Millisekunden am Ende trimmen (entfernt Hotkey-Klick) |
| `HOTKEYS` | siehe config.py | Alle Tastenkombinationen |

## Dependencies

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

**Optional:**
```
soniox>=1.0.0    # Für Soniox API (höhere Genauigkeit)
```

## Systemanforderungen

- **Plattform**: Windows
- **Python**: 3.7+
- **Mikrofon**: Zugriff erforderlich
- **Internet**: Für API-Zugriff

## API-Vergleich

| | Soniox v2 | Soniox v4 | Soniox Live | Groq Large | Groq |
|--|-----------|-----------|-------------|------------|------|
| **Geschwindigkeit** | ~4-6s | ~4-6s | ~0.5s nach Stop | ~1s | ~0.7s |
| **Genauigkeit** | Sehr gut | Sehr gut | Sehr gut | Gut–Sehr gut | Gut |
| **Geeignet für** | Fachbegriffe (Default) | Datei-Upload | Schnellstes Ergebnis | Kostenloser Einstieg | Schnelle Notizen |
| **Modell** | de_v2 (gRPC) | stt-async-v4 | stt-rt-v4 | Whisper Large V3 | Whisper Large V3 Turbo |
| **Hosting** | Soniox Cloud | Soniox Cloud | Soniox Cloud | Groq Cloud | Groq Cloud |
| **Context** | Nein | Ja | Ja | Nein | Nein |

**Default ist Soniox Live** – umschaltbar mit `Ctrl+Alt+L`.

**Kostenlos testen:** Beide Groq-Modelle laufen im kostenlosen Free Tier von Groq (Stand Juni 2026, pro Modell: 20 Anfragen/Minute, 2.000 Anfragen/Tag, 7.200 Audio-Sekunden/Stunde, 28.800 Audio-Sekunden/Tag) – damit lässt sich Thoughtborne ohne Bezahlung ausprobieren; Groq Large ist dabei die genauere, Groq die schnellste Option. Wer nur einen Groq-Key hat, stellt dazu in `config.py` `DEFAULT_API` auf `"groq-large"` oder `"groq"` um – mit dem Default `"soniox-live"` bricht der Start ohne `SONIOX_API_KEY` ab. Soniox erfordert eine Guthaben-Aufladung vor der ersten Nutzung.

## Troubleshooting

**PyAudio Installation (Windows):**
```bash
pip install pipwin
pipwin install pyaudio
```

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

Details: `_research/README.md`

## Architektur

```
Hotkey → Audio-Aufnahme → Transkription (Thread) → Output-Queue → Text-Einfügung
              ↓                    ↓
         voice_archive/      text_archive/

[Soniox Live: Audio wird parallel zur Aufnahme per WebSocket gestreamt]
```

Thread-Sicherheit durch Locks und atomare Operationen. Parallele Transkription möglich, Ausgabe erfolgt sequentiell in Aufnahme-Reihenfolge.
