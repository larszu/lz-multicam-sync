# Was Syncaila mit einem echten Multicam-Dreh gemacht hat

Grundlage: `tests/fixtures/syncaila_input.fcpxml` (Export aus Resolve, alle Clips je Kamera hintereinander) und `tests/fixtures/syncaila_output.fcpxml` (Syncaila 3, „synced“). 90 Clips, 11 Geräte. Geräte-, Datei- und Pfadnamen sind anonymisiert.

Für jeden Clip wurde `Timeline-Position − Timecode` berechnet. Ist der Wert innerhalb einer Kamera konstant, hat die Kamera Free-Run-TC; springt er, läuft der TC nur bei Aufnahme (Rec-Run).

| Gerät | TC | Befund in Syncailas Ergebnis |
|---|---|---|
| TENTACLE_1/2/3 | free-run, gejammt | untereinander 17 ms Spreizung → Syncaila hat per Audio sub-frame verfeinert |
| FX3_E | free-run | 19,46 s neben den Tentacles (nicht gejammt); 0,2 s Unterschied zwischen den zwei Dateien → Drift ≈ 28 ppm |
| FX3_C | free-run | Versatz wandert über 10 000 s um 0,2 s → Drift ≈ 20 ppm, Syncaila korrigiert je Clip |
| FX3_D | free-run | **TC springt über Mitternacht** (86 254 s → 1 616 s); D0001 (0,96 s) und D0002 (4,8 s) „Not synced“ und an den Timeline-Anfang gelegt, obwohl ihr TC die Position kennt |
| INSTA_01 | free-run | ±0,14 s Streuung innerhalb der Kamera; zwei Kurzclips (0,2 s / 1,5 s) „Not synced“ |
| INSTA_02 | free-run | DJI_…0033 (1,4 s) wurde 3 h vor dem Dreh aufgenommen und trotzdem an Position 0 gelegt |
| FX3_A, FX3_B, A7IV_FIXED | **rec-run** | TC läuft lückenlos von Datei zu Datei, die Pausen kennt nur das Audio; C3524 (2,4 s) „Not synced“, zwischen die Nachbarn geschätzt |

## Folgerungen für lzsync

- Rec-Run/Free-Run muss je Kamera erkannt werden – bei Rec-Run sagt der TC nichts über Pausen.
- Mitternachtssprung entfalten.
- Drift ist real (20–30 ppm bei den FX3) und über einen Drehtag 0,2 s groß – ein Versatz je Gerät reicht nicht, ein Uhrmodell je Gerät schon.
- Kurzclips sind Syncailas Schwachstelle: Sie werden nicht gesucht. lzsync sucht sie im Fenster, das Nachbardateien oder TC vorgeben.
- Material außerhalb des Drehs gehört nicht an den Timeline-Anfang.

## Prüfung ohne Medien

`lzsync analyze tests/fixtures/syncaila_input.fcpxml --no-audio` erkennt alle TC-Modi richtig, entfaltet den Mitternachtssprung und legt die Clips jeder Free-Run-Kamera wie Syncaila (Abweichung ≤ 0,25 s, Rest = Drift). Automatisch geprüft in `tests/test_fcpxml.py`.

## Mit Medien (offen)

```
lzsync analyze tests/fixtures/syncaila_input.fcpxml -o ours.fcpxml --json ours.json
lzsync compare ours.fcpxml tests/fixtures/syncaila_output.fcpxml --anchor T1_0001A.wav
```

Erwartung: Abweichung ≤ 1 Frame bei allen Clips, die beide synchronisiert haben; zusätzlich gefundene Kurzclips; Drift je Kamera in der Übersicht.
