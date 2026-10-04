# Lineart-Pipeline – Raster zu Ausmalbuch-SVGs

Eine modulare Python-Pipeline, die komplexe Rasterbilder (Fotos, Gemälde mit
Pinselstrichen, Texturen und Schattierungen) in saubere, druckfertige
Vektorgrafiken für **Ausmalbücher** überführt. Pro Bild werden zwei SVGs
erzeugt:

| Datei | Inhalt |
|---|---|
| `*.lines.svg` | Schwarze Konturlinien auf weißem/transparentem Grund – zum Ausdrucken und Ausmalen |
| `*.color.svg` | dieselbe Geometrie mit flächig gefüllten, gestylten Farben – als Referenz-/Lösungsbild |

Die SVGs sind kongruent aufgebaut: Beide teilen sich dasselbe
Koordinatensystem (`viewBox`), und die Farbflächen schließen exakt an den
Linien an – keine Versätze, keine weißen Blitzer zwischen Farbe und Linie.

Zu jedem Bild gibt es zusätzlich gerenderte PNG-Vorschauen und einen
HTML-Vergleichsreport (`output/index.html`: Original | Lines | Color).

## Installation

Voraussetzungen: Python ≥ 3.10 (empfohlen 3.12), `potrace`. `resvg` ist
optional (nur für die PNG-Vorschauen).

Ein Befehl erledigt alles (legt `.venv` an, installiert die Python-Pakete
und die nativen Binaries; macOS via Homebrew, Linux via apt):

```bash
./setup.sh
source .venv/bin/activate
```

Oder manuell:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # numpy, opencv-python-headless, pillow, scikit-learn
brew install potrace resvg          # macOS | Linux: apt install potrace resvg
```

## Nutzung

```bash
python pipeline.py -i source/ -o output/ -d medium
```

- `-i/--input` – eine Bilddatei oder ein Verzeichnis (`.jpg .jpeg .png .webp .bmp .tiff`)
- `-o/--output` – Zielverzeichnis (Default: `output/`)
- `-m/--mode` – `lines` | `color` | `both` (Default: `both`)
- `-d/--detail-level` – `low` | `medium` | `high` (Default: `medium`)

Beispiele:

```bash
# Einzelnes Bild, nur Lineart, ohne Vorschauen
python pipeline.py -i source/great-wave.jpg -m lines --no-preview

# Alle Bilder in high-Details nach output/high/
python pipeline.py -i source/ -o output/high -d high

# Feinjustierung einzelner Parameter (überschreibt den Preset-Wert)
python pipeline.py -i source/ --sigma 10 --colors 20 --min-region 800
```

Weitere Tuning-Parameter (siehe `python pipeline.py --help`):
`--max-side`, `--sigma`, `--colors`, `--absorb-width`, `--de-min`,
`--de-merge`, `--line-width`, `--min-region`, `--seed`, `--preview-width`,
`--debug` (schreibt Zwischenstufen nach `output/debug/`), `-q`.

Alternativ als installierbares CLI:

```bash
pip install .
lineart -i source/ -o output/
```

## Architektur & Entscheidungen

### Pipeline (reine CV – keine Modell-Downloads)

```
[ Eingabebild JPG/PNG ]
        │
        ▼
[ 1. preprocess.py ]  Downscale auf ≤ max_side px, zwei Glättungspfade:
                      (a) leichtes Bilateral-Blatt für Region-Farben,
                      (b) starker Gauß-Blur in CIE-Lab – löscht
                          Pinselstrich-/Papier-Textur (Periode 5–15 px),
                          behält makroskopische Farbfelder und Objekt-
                          grenzen bei
        │
        ▼
[ 2. segments.py ]    K-Means-Quantisierung (MiniBatchKMeans, Seed 42)
                      im Lab-Raum → flache Farb-Regionen. Post-Processing:
                      morphologisches Cleanup, Streifen-Absorption (dünne
                      Zwischensäume < absorb_width px werden in den
                      farbnächsten Nachbarn übernommen – mit Schutz für
                      dünne, hochkontrastige Strukturen wie helle
                      Schaum-Säume zwischen dunklen Bändern), Merge
                      kleiner Regionen (< min_region) und Fixpunkt-Merge
                      benachbarter Regionen mit ΔE < de_merge ODER
                      weichem Grenzgradienten (soft_grad + soft_de – löst
                      Banding im Himmel auf, ohne echte Konturen zu
                      verschlucken)
        │
        ▼
[ 3. edges.py ]       Grenzmasken aller überlebenden Regionen →
                      Linienmaske (um line_width px elliptisch
                      dilatiert, morphologisch bereinigt). Die
                      dilatierte Maske ist die EINZIGE Quelle der
                      Geometrie: Linien = die Maske selbst, Fills = ihre
                      Komplementärmenge
        │
        ▼
[ 4. vectorize.py ]   potrace (P4-PBM, -u 1) → glatte Bézier-Pfade
        │
        ▼
[ 5. svgbuild.py ]    *.lines.svg:  <g id="line-art"> (schwarz)
                      *.color.svg:  <g id="color-fills"> unter
                                    <g id="line-art">
                      – saubere, unabhängig manipulierbare Layer
        │
        ▼
[ 6. render.py + report.py ]  resvg-PNG-Vorschauen + output/index.html
```

Zuordnung zu den Phasen aus `PROMPT.md` / `docs/research.md`:
„Semantische Konturen-Extraktion“ (2a) und „Farbflächen-Segmentierung“
(2b) sind hier ein und derselbe Schritt – die Regionengrenzen der
Segmentierung *sind* die Konturen. „Bereinigung & Lückenschluss“ (3)
ist in der Regions-Post-Processing gesteckt: störende Segmente werden
absorbiert/ge-merged, womit geschlossene Flächen entstehen, die mit
Bucket-Fill ausmalbar sind. „Vektorisierung & SVG-Generierung“ (4)
entspricht den Stufen 4–5.

### Warum keine Deep-Learning-Modelle (SAM / DexiNed / SD / VTracer)?

`docs/research.md` skizziert einen modellbasierten Weg (SAM → DexiNed →
Stable Diffusion → VTracer). Dagegen wurde bewusst entschieden:

1. **Reproduzierbarkeit & Portabilität** – keine hundert-MB-
   Gewichts-Downloads, kein MPS/CUDA-/RAM-Engpass, deterministisches
   Ergebnis (Seed=42). Die Pipeline läuft auf jedem Mac/Linux in
   5–12 s pro Bild.
2. **Kontrollierbarkeit** – klassische Stufen lassen sich einzeln
   parametrisieren und visuell prüfen (`--debug`); ein Bildgenerator
   wäre ein Black-Box-Schritt mit pro Lauf variierendem Stil.
3. **Kongruenz per Konstruktion** – das entscheidendste Qualitäts-
   Kriterium (Farbflächen schließen exakt an die Konturen an) ergibt
   sich hier aus derselben Maske für Linien und Fills; bei zwei
   unabhängigen Modellen (Kantenmodell + Segmentierungsmodell) müssten
   solche Blitzer mühsam nachkorrigiert werden.
4. **Kosten** – keine API-Abhängigkeiten (Strategie B aus der
   Vorrecherche).

Die Qualität an den beiden Referenzbildern wurde iterativ gegen die
Qualitätskriterien in `PROMPT.md` optimiert (geschlossene Flächen,
durchgehende Konturen, wiedererkennbares Motiv, sauberes SVG).

### Kern-Designentscheidungen

- **Kontur-Politik:** Es werden *komplette, geschlossene Grenzen aller
  überlebenden Regionen* gezeichnet (kein pixelweises ΔE-Gating).
  Folge: durchgehende, nicht ausgefranste Linien, die in
  Bucket-Fill-Apps zuverlässig gefüllt werden. `de_min` kann selektiv
  schwache Grenzen unterdrücken (0 = alle zeichnen).
- **Streifen-Absorption:** dünne Zwischensäume (Gradienten-Halos) unter
  `absorb_width` px werden in den farbnächsten Nachbarn aufgenommen –
  aber nur, wenn die Struktur nicht „dünn und hochkontrastig“ ist
  (z. B. weißer Schaum zwischen zwei dunklen Bändern bleibt erhalten).
- **Kongruenz per Konstruktion:** dilatierte Linienmaske = einzige
  Geometrie-Quelle; Fills werden aus ihrer Komplementärmenge
  abgeleitet. Versätze zwischen Farbe und Linie sind ausgeschlossen.
- **CIE-Lab + K-Means:** Farbdifferenzen in wahrnehmungsnahen Einheiten
  (ΔE); die Region-Farbe ist der Cluster-Durchschnitt (gestyltes
  Flat-Color statt fotorealistischem Rauschen).

## Presets

| Preset | Zweck | σ | k | absorb | min-Region | Linie |
|---|---|---|---|---|---|---|
| `low` | wenige, kräftige Konturen, posterartig (schnell) | 12.0 | 12 | 16 px | 600 px² | 4.5 px |
| `medium` | ausgewogene Konturdichte (Default) | 8.0 | 18 | 16 px | 400 px² | 3.5 px |
| `high` | reiche Detailfülle inkl. Sterne/Schaumfäden | 5.5 | 24 | 10 px | 300 px² | 2.5 px |

(`max_side`: 1200/1400/1600 px; alle Werte in `lineart/config.py`.)
Jeder Wert ist per CLI übersteuerbar.

## Ergebnis an den Testbildern

| Bild | Preset | Regionen | Laufzeit |
|---|---|---|---|
| great-wave (2383×1685) | low / medium / high | 6 / 6 / 12 | ~5 / 6 / 9 s |
| starry-night (1920×1521) | low / medium / high | 5 / 5 / 7 | ~4 / 7 / 13 s |

(Laufzeiten: Apple-M2-Pro; Artefakte in `output/`, reproduzierbar über
`python pipeline.py -i source/ -o output/ -d medium`.)

## Stärken

- **Geschlossene, druckfertige Flächen** – jede Region ist eine
  geschlossene Bézier-Fläche; keine offenen Linien, kein
  Fragmente-Rauschen.
- **Kongruenz von Lines und Color** – garantiert durch gemeinsame
  Quellmaske (kein Nachjustieren nötig).
- **Deterministisch & schnell** – keine Modelle, 5–13 s/Bild, fester
  Seed.
- **Skalierbar** – definierte `viewBox`, einheitliche Linienstärken,
  saubere SVG-Struktur (je ein `<g>` pro Layer, XML-syntaktisch validiert).
- **Tunierbar** – drei Presets + individuelle Parameter-Overrides +
  `--debug`-Zwischenstufen; HTML-Report zur visuellen Prüfung.
- **Abhängigkeitsarm** – nur numpy/OpenCV/Pillow/scikit-learn + potrace
  (Laufzeit) und optional resvg (Vorschauen).

## Bekannte Limitierungen

- **Segmentbasierte Konturen:** Linien sind *Regionengrenzen*; Details
  *innerhalb* einer großen, farblich homogenen Region (z. B.
  Wolkenstrukturen innerhalb eines einheitlichen Himmels) erzeugen
  keine Kontur. Mehr `--colors` plus kleineres `--sigma` hilft, kostet
  aber Dichte/Rauschen (s. Trade-off unten).
- **Preset-Tuning ist bildspezifisch:** Der wichtigste Regler
  (`sigma` bzw. `absorb-width`) ist ein Kompromiss: Hoher σ beruhigt
  den Sternen-Nachthimmel, spült aber bei der großen Welle die
  Wolkenbänder aus dem Himmel. Die Presets sind auf die beiden
  Referenzbilder optimiert; neue Bilder erfordern gelegentlich
  Einzeljustierung (Hinweise unten).
- **Flat-Color-Ästhetik:** Cluster-Durchschnittsfarben sind gestylt
  (teils gedämpfter als das Original) – gewollt (Ausmalbuch), aber
  nicht originaltreu.
- **Banding-Residuen:** In stark graduierten Himmeln kann trotz
  Soft-Boundary-Merge schwaches Banding übrig bleiben (besonders bei
  `high`).
- **Performance:** Der Fixpunkt-Merge ist O(Regionen²) pro Iteration –
  bei sehr großen `k` (> 40) und hochauflösenden Inputs spürbar
  langsamer (aktuell unproblematisch).
- **Keine semantische Linien-Hierarchie:** Alle Grenzen werden gleich
  breit gezeichnet; „Hauptkontur vs. Detail“ wird über den Dichte-
  Preset, nicht über Linienstärken gesteuert.

## Neue Bilder verarbeiten

1. **Schnellpfad:** `python pipeline.py -i neues-bild.jpg -o output/` –
   für die meisten Fälle mit `medium` brauchbar.
2. **Bei Rauschen im Himmel/Wasser (viele kleine Blasen/Bänder):**
   - `--sigma +2…4` (stärkere Glättung des Segmentierfelds) und/oder
     `--absorb-width +8…12` (dünne Streifen verschmelzen).
   - `--de-merge` leicht erhöhen, wenn ähnliche Töne zusammenfließen
     sollen.
3. **Bei zu wenigen Details:** `--colors +4…8`, `--sigma -2`,
   `--absorb-width -4`, `--min-region` senken.
4. **Für großen Druck: Raster auflösen** – `--max-side 2000` (SVG
   skaliert trotzdem beliebig; erhöht Speicher/Laufzeit).
5. **Prüfen:** `output/index.html` im Browser öffnen; bei Abweichungen
   die Parameter oben neu einstellen. `--debug` liefert
   `output/debug/<name>/` mit `smoothed.png` (Segmentierfeld) und
   `segments.png` (Flachfarben vor der Linienbildung) zur Diagnose.

## Projektstruktur

```
pipeline.py            – CLI-Einstiegspunkt
lineart/
  config.py            – Config-Dataclass + Presets (low/medium/high)
  preprocess.py        – Stufe 1: Load, Resize, Bilateral + Lab-Gauß
  segments.py          – Stufe 2: K-Means + Absorption/Merge-Post-Processing
  edges.py             – Stufe 3: Grenzmasken → Linienmaske
  vectorize.py         – Stufe 4: potrace-Bézier-Tracing
  svgbuild.py          – Stufe 5: SVG-Assembly (Layer-Kongruenz)
  render.py            – resvg-PNG-Vorschauen
  report.py            – HTML-Vergleichsreport
  runner.py            – Orchestrierung + Debug-Artefakte
  cli.py               – argparse-CLI
pyproject.toml / requirements.txt / setup.sh
docs/research.md       – Vorrecherche (modellbasierter Ansatz)
PROMPT.md              – Aufgabenstellung
source/                – Testbilder
output/                – Artefakte (git-ignored)
```
