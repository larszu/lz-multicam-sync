# lz-multicam-sync

Sync-Engine für die Postproduktion: Kameras und Audiorecorder eines Drehtags automatisch auf eine gemeinsame Timeline legen – per Audio, Timecode und Dateireihenfolge. Funktionale Referenz ist Syncaila 3; das Ziel ist nicht der Nachbau, sondern ein nachvollziehbareres und genaueres Ergebnis.

## Download

Unter [Releases](https://github.com/larszu/lz-multicam-sync/releases): **Windows-EXE** (ffmpeg ist dabei) und **macOS-DMG** (Apple Silicon; braucht ffmpeg: `brew install ffmpeg`). Die App ist nicht signiert – unter macOS beim ersten Start Rechtsklick → Öffnen, unter Windows „Weitere Informationen → Trotzdem ausführen“.

Doppelklick öffnet das Fenster: FCPXML oder Medienordner wählen, „Synchronisieren“, das Ergebnis liegt als `… - lzsync.fcpxml` daneben. Mit Argumenten aufgerufen ist dieselbe Datei das Kommandozeilenwerkzeug.

## Kommandozeile

```
lzsync analyze "Projekt.fcpxml" --json sync.json     # FCPXML/.fcpxmld aus Resolve oder FCP
lzsync analyze /Volumes/DRIVE/01_FOOTAGE             # oder direkt ein Medienordner
lzsync compare "Projekt - lzsync.fcpxml" "Projekt - Syncaila.fcpxml"
```

Ergebnis: `… - lzsync.fcpxml` (in Resolve importieren), eine Übersicht im Terminal und optional die Sync-Map als JSON.

## Was anders ist als bei Syncaila

| | Syncaila 3 | lzsync |
|---|---|---|
| Zeitmodell | Versatz je Clip | **Uhrmodell je Gerät**: Versatz + Gangabweichung (ppm), global gelöst |
| Lösung | Clip für Clip, mehrere Durchläufe | **ein Gleichungssystem für alle Treffer** (robuste kleinste Quadrate), falsche Treffer fallen als Ausreißer heraus |
| Timecode | wird genutzt | **Rec-Run vs. Free-Run automatisch erkannt**, Mitternachtssprung entfaltet |
| Kurze Clips | bleiben „Not synced“ | **Suche im engen Fenster** zwischen den Nachbardateien – auch 1-s-Clips werden exakt gefunden und von zwei Geräten gegengeprüft |
| Material außerhalb des Drehs | landet am Timeline-Anfang | bleibt draußen, mit Hinweis |
| Teilgruppen ohne Verbindung | — | bleiben **in sich synchron** und liegen als Block hinter der Timeline |
| Audio-Clips | — | **sub-frame-genau** (In-Punkt auf 1/48000 s) |
| Begründung | Match-Qualität | je Clip Methode, Konfidenz, Messpunkte, Restfehler; je Gerät TC-Modus, Drift, TC-Versatz zur Referenz |

## Wie es arbeitet

1. **Inseln:** Clips, deren Zeitbezug feststeht, bilden eine Insel – bei Free-Run-TC alle Clips einer Kamera (bei > 30 min Pause neue Insel), bei Rec-Run-TC jede Datei für sich. Rec-Run erkennt die Engine daran, dass der TC jeder Datei genau dort weiterläuft, wo die vorige endete.
2. **Grobabgleich:** Log-Energie-Hüllkurve bei 100 Hz, Trend entfernt (pegel- und mikrofonunabhängig). Jede Insel gegen jede andere eines anderen Geräts mit maskierter, normierter Kreuzkorrelation – Lücken zwischen Clips zählen nicht. Treffer nur mit klarer Spitze (z-Wert und Abstand zur zweitbesten).
3. **Feinabgleich:** GCC-PHAT auf dem Rohaudio (8 kHz) an bis zu 40 Stellen der Überlappung, Sub-Sample-Spitze, robuste Geradenanpassung gegen Ausreißer.
4. **Globaler Solver:** jede Messung ist eine Gleichung `global(b, t_b) = global(a, t_a)`, jedes Gerät hat eine Uhr `global = Versatz + t · (1 + ppm)`. Dazu schwache Hinweise aus Timecode, Uhrzeit im Dateinamen/`creation_time` und `--jammed`. Gelöst mit Huber-IRLS; Ausreißer werden verworfen.
5. **Prüfung:** Eine Kamera kann nicht zwei Dateien gleichzeitig aufnehmen – Überlappungen verwerfen den schwächeren Treffer. Danach Kurzclip-Suche im Fenster und Session-Ausreißer.
6. **Ausgabe:** FCPXML 1.10, eine Spur je Gerät (Video oben, reine Audiorecorder unten). Rollen kennzeichnen `Chronologie`, `Teilgruppe` und `Nicht synchron`, Notizen am Clip nennen den Grund.

## Stand

- Synthetische Drehs mit bekannter Wahrheit (Drift ±40 ppm, Hall, Tiefpass, Rauschen, Rec-Run, Mitternacht, 1-s-Clips, Fremdmaterial): Fehler unter 1 ms, Drift auf < 1 ppm.
- Echter Multicam-Dreh (90 Clips, 11 Geräte), nur Metadaten, weil die Medien nicht angeschlossen waren: TC-Modus aller Kameras richtig erkannt, Anordnung innerhalb jeder Free-Run-Kamera wie bei Syncaila bis auf ≤ 0,25 s – der Rest ist Uhrendrift, die erst der Audio-Lauf misst. Details: [docs/syncaila-analyse.md](docs/syncaila-analyse.md).
- **Offen:** Audio-Lauf gegen das echte Material und Vergleich mit dem Syncaila-Ergebnis (`lzsync compare`), sobald das Medienlaufwerk angeschlossen ist.

## Aus dem Quelltext

```
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/lzsync-gui            # Fenster
sh packaging/build_mac.sh       # App + DMG nach dist/
```

Braucht `ffmpeg`/`ffprobe` (neben der App, im PATH oder unter /opt/homebrew/bin). Dekodiertes Audio wird zwischengespeichert (macOS `~/Library/Caches/lzsync`, Windows `%LOCALAPPDATA%\lzsync`). Ein v-Tag baut EXE und DMG per GitHub Actions und hängt sie an das Release.

## Lizenz

MIT. Die Windows-EXE enthält ffmpeg (GPL, [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)).

## Optionen

| Option | Wirkung |
|---|---|
| `--remap ALT=NEU` | Pfadpräfix ersetzen, wenn das Laufwerk anders heißt |
| `--reference GERÄT` | Referenzuhr festlegen (Standard: längste Aufnahme im Audio-Graphen) |
| `--jammed A,B,C` | Geräte mit gemeinsam gejammtem TC, verbindet sie auch ohne Audio |
| `--channels first` | nur ersten Kanal statt Mix (gegen Phasenauslöschung) |
| `--no-audio` | nur Timecode und Metadaten |
| `--split-gap S` | Pause in Sekunden, ab der eine Free-Run-Kamera eine neue Insel beginnt |

Hintergrund und Quellen: [docs/forschung.md](docs/forschung.md).
