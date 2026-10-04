# TASK: KI-gestützte Lineart- & Ausmalbuch-Pipeline (Raster zu SVG)

## 1. Zielsetzung & Kontext
Entwickle eine robuste, modular aufgebaute und hochqualitative Pipeline, die komplexe Rasterbilder (Fotos, kunstvolle Gemälde mit Pinselstrichen, Texturen und Schattierungen – siehe Testbilder in `source/`) in saubere, druckfertige Vektorgrafiken (**SVG**) für **Ausmalbücher (Coloring Pages)** transformiert.

Die Pipeline soll dabei zwei SVG-Varianten pro Motiv erzeugen:
1. **Reine Ausmalvorlage (`*.lines.svg`):** Klare, schwarze Konturlinien auf weißem/transparentem Grund zum Ausdrucken und Ausmalen.
2. **Kolorierte Vorschau (`*.color.svg`):** Dieselbe Vektorgrafik mit stilisierten, flächig gefüllten Farbbereichen (Flat-Color / Cel-Shading) als anschauliches Referenz- und Lösungsbild.

Als Agent hast du die volle Autonomie über Architektur, Programmiersprache (empfohlen: Python), Toolauswahl und Bibliotheken. Nutze alle dir zur Verfügung stehenden Methoden (klassische Bildverarbeitung, Deep-Learning-Modelle, Segmentierung, Farbraum-Quantisierung, heuristische Vektorisierung), um das bestmögliche visuelle Ergebnis zu erzielen.

Konsultiere als Ausgangspunkt und Referenz die Vorrecherche in [`docs/research.md`](docs/research.md).

---

## 2. Das Kernproblem & Qualitätskriterien
Herkömmliche Kantenfilter (wie naives `Canny-Edge` oder einfacher Schwellenwert direkt in `potrace`) scheitern an komplexen Bildern wie `starry-night.jpg` oder `great-wave.jpg`: Sie erzeugen ein unbrauchbares Chaos aus Pinselstrich-Fragmenten, Rauschen und offenen Linien.

### A. Kriterien für die Linien-Ausmalvorlage (`*.lines.svg`)
1. **Semantische Relevanz:** Erkennung echter Objektgrenzen und Hauptkonturen statt Textur-Rauschen und Farbschattierungen.
2. **Geschlossene Flächen & Linienzusammenhang:** Flächen sollten sich idealerweise mit Buntstiften oder "Bucket Fill" ausmalen lassen (Lückenschluss / Morphologie).
3. **Linienhierarchie & Stil:** 
   - Klare, durchgehende Konturlinien (keine doppelten Kantenränder oder ausgefransten Ränder).
   - Ausgewogene Dichte: Nicht zu überladen, aber mit ausreichend Details, um das Original wiederzuerkennen.
4. **Sauberes Vektor-SVG:**
   - Glatte Bézier-Kurven statt pixeliger Stufen (z. B. via `vtracer`, `potrace` mit Glättung oder ähnlichen Vektorisierern).
   - Bereinigte SVG-Struktur (optimierte Pfade, keine mikroskopischen Artefakt-Pfade).
   - Skalierbar und druckfähig (definierte `viewBox`, konsistente Strichstärken).

### B. Kriterien für die kolorierte Fassung (`*.color.svg`)
1. **Kongruenz zu den Konturen:** Die Farbflächen müssen exakt mit den Konturlinien abschließen (keine unschönen Versätze oder weiße Blitzer zwischen Farbe und Linie).
2. **Harmonische Farbquantisierung:** Flächige, stilisierte Farben (vereinfachte Farbpalette / Regionen-Durchschnitt oder K-Means/Segment-Farbe), die das Motiv lebendig darstellen, ohne fotorealistisches Rauschen zu erzeugen.
3. **Struktur:** Sauber getrennte Layer (z. B. `<g id="color-fills">` unter `<g id="line-art">`), sodass Linien und Farben im SVG unabhängig voneinander manipulierbar sind.

---

## 3. Erwartete Pipeline-Stufen
Implementiere eine Pipeline, die idealerweise folgende Phasen modular abbildet:

```
[ Eingabebild (JPG/PNG) ]
           │
           ▼
[ 1. Vorverarbeitung & Rauschunterdrückung ]
   (z. B. Bilateral Filter, Kanten-erhaltende Glättung, Kontrastspreizung)
           │
     ┌─────┴────────────────────────────────┐
     ▼                                      ▼
[ 2a. Semantische Konturenextraktion ]  [ 2b. Farbflächen-Segmentierung ]
(DexiNed, ControlNet-Lineart,           (Farbquantisierung, K-Means, Superpixel,
 semantische Edge-Modelle oder           Watershed oder SAM-Segment-Mittelung)
 morphologische Gradienten)                 │
     │                                      │
     ▼                                      │
[ 3. Bereinigung & Lückenschluss ]          │
(Skelettierung, Morpologisches Schließen,   │
 Noise-Filterung, Dünnung)                  │
     │                                      │
     ▼                                      ▼
[ 4. Vektorisierung & SVG-Generierung ] ◄───┘
   (Centerline/Contour-Tracing via VTracer / Potrace / svgwrite / picosvg)
     │
     ├───► [ Ausgabe A: *.lines.svg (Reine Ausmalvorlage) ]
     └───► [ Ausgabe B: *.color.svg (Kolorierte Flächen + Konturen) ]
           (+ Kontroll-Renderings / Vorschau-PNGs)
```

*Hinweis:* Es steht dir frei, Modelle lokal einzubinden (z. B. ONNX Runtime, PyTorch CPU/MPS) oder Fallbacks anzubieten, falls dedizierte Modell-Gewichte heruntergeladen werden müssen. Stelle sicher, dass die Pipeline reproduzierbar und ohne manuelle Eingriffe auf einem Entwickler-Rechner (Mac / Linux) durchläuft.

---

## 4. Konkrete Deliverables & Anforderungen

### A. Ausführbare CLI / Pipeline
Erstelle ein lauffähiges CLI-Tool (z. B. `pipeline.py` oder CLI via `typer`/`argparse`), das mindestens folgende Parameter unterstützt:
- `--input` / `-i`: Pfad zu einer Bilddatei oder einem Verzeichnis.
- `--output` / `-o`: Zielverzeichnis für SVGs und Artefakte.
- `--mode`: Auswahl des Ausgabeformats (`lines`, `color`, `both` – Default: `both`).
- Konfigurierbare Detailstufen (z. B. `--detail-level [low|medium|high]` oder Regler für Rauschfilterung, Farbanzahl und Liniendicke).

### B. Dependency- & Umgebungsmanagement
- Vollständige `pyproject.toml` oder `requirements.txt`.
- Ein automatisches Setup-Script oder Installationsbefehle, mit denen alle Werkzeuge (inklusive ggf. Binaries wie `vtracer` oder `potrace`, falls genutzt) sauber installiert werden.

### C. Benchmark & Evaluation
Wende die Pipeline auf die beiden mitgelieferten Testbilder an:
- `source/great-wave.jpg`
- `source/starry-night.jpg`

Speichere die Ergebnisse im Ordner `output/`:
- `great-wave.lines.svg` & `great-wave.color.svg`
- `starry-night.lines.svg` & `starry-night.color.svg`
- Zur schnellen Sichtprüfung: gerenderte Vorschaubilder (`.png`) oder einen kleinen HTML-Vergleichs-Report (`output/index.html`), der Original, Linien-SVG und Kolorierungs-SVG gegenüberstellt.

### D. Dokumentation (`README.md`)
Dokumentiere:
1. Wie die Pipeline installiert und ausgeführt wird.
2. Welche Architektur- und Toolentscheidungen getroffen wurden und warum.
3. Welche Stärken und bekannten Limitierungen das Verfahren aufweist.
4. Wie neue Bilder verarbeitet werden können.

---

## 5. Arbeitsweise für den Agenten
1. **Analysieren:** Prüfe das System, verfügbare Hardware (CPU, Apple Silicon MPS oder CUDA) und existierende Tools.
2. **Entscheiden & Prototyping:** Wähle die optimalen Libraries/Modelle. Baue zügig einen Prototyp und prüfe Zwischenergebnisse visuell.
3. **Iterieren & Verfeinern:** Validiere das Ergebnis an den beiden Testbildern. Gib dich nicht mit dem ersten groben Canny-Ergebnis zufrieden – optimiere Schwellenwerte, Lückenfüllung, Farbzuordnung und Vektorisierungs-Parameter, bis eine echte Malbuchqualität erreicht ist.
4. **Verifikation:** Überprüfe, dass die generierten SVGs syntaktisch valide sind, sich in Browsern korrekt öffnen lassen und druckfertig skalieren.
