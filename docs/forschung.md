# Quellen und Einordnung

lzsync ist eigenständig geschrieben; aus den Projekten unten stammt kein Code. Sie dienten als Vergleich für Verfahren. Alle Repos wurden am 06.10.2026 per GitHub-API auf Existenz geprüft; bis auf SoundFingerprinting sind es Kleinstprojekte (0–9 Sterne), Reife unbekannt.

| Repo | Lizenz | Bezug zu lzsync |
|---|---|---|
| [Eureka175/ChronoSync](https://github.com/Eureka175/ChronoSync) | MIT | gleiche Grundidee: Zeitabbildung je Spur, Drift, Graph-Solver |
| [ogra/double-ender-sync](https://github.com/ogra/double-ender-sync) | MIT | Konfidenz aus Spitzenhöhe, Eindeutigkeit, NCC/GCC-PHAT-Abgleich |
| [Bormotoon/WhisperSync](https://github.com/Bormotoon/WhisperSync) | k. A. | Wortanker per Whisper als zusätzliche Evidenz (in lzsync noch nicht) |
| [jianshuo/polysync](https://github.com/jianshuo/polysync) | MIT | Energie-Hüllkurve als Grobstufe |
| [AddictedCS/soundfingerprinting](https://github.com/AddictedCS/soundfingerprinting) | MIT | Fingerprinting – Kandidat für sehr große Projekte |
| [thinkvp/Syncitol](https://github.com/thinkvp/Syncitol) | k. A. | Premiere-Anbindung |
| [jojopas/syncdrop](https://github.com/jojopas/syncdrop), [yinstagram/davinci-waveform-sync](https://github.com/yinstagram/davinci-waveform-sync), [vitaly-zdanevich/shotcut-multicam-sync](https://github.com/vitaly-zdanevich/shotcut-multicam-sync), [ericabooth/multicam-resolve-skill](https://github.com/ericabooth/multicam-resolve-skill), [keywork/obs-av-sync](https://github.com/keywork/obs-av-sync) | MIT/GPL-2.0 | einfache Kreuzkorrelation, NLE-Ausgabe |

Verfahren:
- Knapp & Carter (1976), *The generalized correlation method for estimation of time delay* – GCC-PHAT.
- Padfield (2012), *Masked Object Registration in the Fourier Domain* – maskierte normierte Kreuzkorrelation (Lücken zwischen Clips).
- Wang (2003), *An Industrial-Strength Audio Search Algorithm* – Fingerprinting (noch nicht eingebaut).

## Nächste Ausbaustufen

1. Audio-Lauf gegen das echte Material des Referenzdrehs und Vergleich mit dem Referenzergebnis.
2. Mehrband-Hüllkurve für sehr leises Kameraaudio (synthetisch bei −10 dB SNR noch Fehltreffer-frei, aber ohne Treffer).
3. Fingerprint-Vorauswahl der Paare für Projekte mit Tausenden Clips.
4. Sprachanker (Whisper) als dritte, unabhängige Evidenz.
5. Ausgabe als OTIO/AAF, Oberfläche zum Prüfen der markierten Clips.
