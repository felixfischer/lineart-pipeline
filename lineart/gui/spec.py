"""UI description of the pipeline stages: texts, parameters and views.

Parameter keys are ``Config`` field names; ``merge.<x>`` addresses fields of
``Config.merge`` (``segment.MergeParams``). ``flag`` is the matching CLI
option, so the GUI can show the equivalent command line.
"""
from __future__ import annotations


def P(key, label, help, kind="float", min=None, max=None, step=None, flag=None,
      advanced=False, options=None, unit=None, live=False):
    return {k: v for k, v in dict(
        key=key, label=label, help=help, kind=kind, min=min, max=max, step=step,
        flag=flag, advanced=advanced, options=options, unit=unit, live=live).items()
        if v is not None}


def V(id, label, help, kind="raster"):
    return dict(id=id, label=label, help=help, kind=kind)


STAGES = [
    dict(
        id="preprocess", title="Vorverarbeitung", short="Skalieren, Kontrast, Glätten",
        description=(
            "Das Bild wird auf die Arbeitsauflösung skaliert, der Helligkeitskontrast "
            "lokal ausgeglichen (CLAHE) und anschließend kantenerhaltend geglättet "
            "(Mean-Shift + Bilateral). Die Glättung macht aus Pinselstrichen, Papierstruktur "
            "und Rauschen flache Plateaus, lässt aber Objektgrenzen scharf."),
        tip=("Zum Experimentieren eine kleine Arbeitsauflösung (z. B. 800 px) wählen – "
             "alle Schritte rechnen dann ein Vielfaches schneller. Flächengrößen, Glättung "
             "und Strichstärken skalieren automatisch mit."),
        params=[
            P("work_size", "Arbeitsauflösung",
              "Lange Bildkante in Pixeln, auf der die gesamte Pipeline rechnet. "
              "Höher = feinere Details und präzisere Linien, aber deutlich langsamer.",
              "int", 400, 3200, 100, "--work-size", unit="px"),
            P("smoothing", "Rauschfilter",
              "Stärke der kantenerhaltenden Glättung. Höher = Pinselstriche und Texturen "
              "verschwinden, kleine Details aber auch. 0 = aus.",
              "float", 0, 1, 0.05, "--smoothing"),
            P("clahe_clip", "Kontrastausgleich",
              "CLAHE-Clip-Limit auf dem Helligkeitskanal. Hebt flaue oder vergilbte "
              "Vorlagen an, verstärkt aber auch Rauschen. 0 = aus.",
              "float", 0, 4, 0.1, "--clahe-clip", advanced=True),
        ],
        views=[
            V("original", "Original", "Eingabebild auf Arbeitsauflösung skaliert."),
            V("contrast", "Kontrast", "Nach dem lokalen Kontrastausgleich (CLAHE)."),
            V("smooth", "Geglättet", "Eingabe für Kantenerkennung und Segmentierung."),
        ],
    ),
    dict(
        id="edges", title="Kantenerkennung", short="DexiNed, global + gekachelt",
        description=(
            "Ein neuronales Netz (DexiNed) schätzt für jedes Pixel, wie wahrscheinlich dort "
            "eine Objektgrenze liegt. Es läuft zweimal: einmal auf dem ganzen Bild "
            "(globaler Pass – nur große Konturen) und einmal in überlappenden Kacheln "
            "(präzise, aber auch Textur). Das geometrische Mittel lässt Kachel-Details nur "
            "dort durch, wo auch global Struktur ist."),
        tip=("Die Kantenkarte bestimmt, wo später Linien entstehen können. Dunkle Bereiche "
             "ohne echte Kontur deuten auf Textur hin, die weggeglättet werden sollte."),
        params=[
            P("edge_backend", "Kantenmodell",
              "auto: DexiNed, bei fehlendem Modell klassischer Gradient. "
              "classical: Lab-Farbgradient ohne neuronales Netz (schnell, texturanfälliger).",
              "select", flag="--edge-backend",
              options=[["auto", "auto"], ["dexined", "DexiNed"], ["classical", "klassisch"]]),
            P("global_weight", "Gewicht globaler Pass",
              "Höher = nur großräumige Objektkonturen überleben. Niedriger = mehr "
              "Feindetails aus den Kacheln, aber auch mehr Pinselstrich-Kanten. "
              "Nur bei DexiNed wirksam.",
              "float", 0, 1, 0.05, "--global-weight"),
        ],
        views=[
            V("fused", "Kantenkarte", "Fusionierte Kantenwahrscheinlichkeit (dunkel = Kante)."),
            V("global", "Global", "DexiNed auf dem ganzen Bild in einem 640×480-Frame."),
            V("tiled", "Kacheln", "DexiNed auf überlappenden 640×480-Kacheln."),
            V("overlay", "Überlagerung", "Kantenkarte rot über dem geglätteten Bild."),
        ],
    ),
    dict(
        id="segment", title="Segmentierung", short="Watershed, Verschmelzen, Bereinigen",
        description=(
            "Ein Watershed zerlegt das Bild entlang der Kantengrate in tausende kleine Becken "
            "(Übersegmentierung). Danach werden benachbarte Becken verschmolzen – zuerst die "
            "mit der schwächsten Grenze und ähnlichster Farbe – bis die Ziel-Flächenzahl "
            "erreicht ist. Zu kleine oder zu dünne Flächen werden vom besten Nachbarn "
            "geschluckt, die Grenzen geglättet. Diese Regionsgrenzen sind später die Linien."),
        tip=("Wichtigster Schritt für die Dichte der Vorlage: Ziel-Flächenzahl und "
             "Mindestfläche bestimmen, wie detailliert das Ausmalbild wird."),
        params=[
            P("target_regions", "Ziel-Flächenzahl",
              "Ungefähre Anzahl ausmalbarer Flächen. Bei texturreichen Bildern wird der "
              "Wert automatisch bis zu 20 % reduziert.",
              "int", 20, 3000, 10, "--regions"),
            P("min_area", "Mindestfläche",
              "Flächen unter dieser Größe (in px², bezogen auf 1600 px Kantenlänge) werden "
              "dem passendsten Nachbarn zugeschlagen.",
              "int", 0, 3000, 10, "--min-area", unit="px²"),
            P("label_sigma", "Grenzglättung",
              "Radius der Gauß-Glättung der Regionsgrenzen im Raster. Höher = rundere "
              "Formen, weniger Treppen aus Pinselstrichen; spitze Details runden ab.",
              "float", 0, 10, 0.25, "--label-sigma", unit="px"),
            P("min_width", "Mindestbreite",
              "Schmale Splitterflächen mit geringerer mittlerer Breite (2·Fläche/Umfang) "
              "werden aufgelöst.",
              "float", 0, 12, 0.5, "--min-width", unit="px", advanced=True),
            P("merge.edge_weight", "Kante vs. Farbe",
              "Gewichtung beim Verschmelzen: 1 = nur Kantenstärke der gemeinsamen Grenze "
              "zählt, 0 = nur der Farbunterschied.",
              "float", 0, 1, 0.05, "--edge-weight", advanced=True),
            P("merge.color_scale", "Farbdistanz-Skala",
              "Farbabstand (ΔE in Lab), ab dem zwei Flächen als völlig verschieden gelten. "
              "Kleiner = Farbunterschiede verhindern Verschmelzen stärker.",
              "float", 4, 80, 1, "--color-scale", advanced=True),
        ],
        views=[
            V("borders", "Grenzen", "Regionsgrenzen über dem geglätteten Bild."),
            V("regions", "Regionen", "Jede Fläche in einer Zufallsfarbe."),
            V("means", "Mittelwerte", "Jede Fläche in ihrer mittleren Farbe."),
            V("overseg", "Übersegmentierung", "Watershed-Becken vor dem Verschmelzen."),
        ],
    ),
    dict(
        id="palette", title="Farbpalette", short="K-Means, gleiche Farben verschmelzen",
        description=(
            "Für jede Fläche wird eine robuste Mittelfarbe bestimmt; ein flächengewichtetes "
            "K-Means im Lab-Farbraum reduziert diese auf eine Palette. Benachbarte Flächen, "
            "die dieselbe Palettenfarbe erhalten und keine deutliche Kante trennt, werden "
            "zusammengelegt – das spart überflüssige Linien."),
        tip=("„Zusammenführung“ zeigt rot die Grenzen, die durch gleiche Farbe entfallen. "
             "Mehr Farben = weniger Verschmelzung und damit mehr Flächen."),
        params=[
            P("colors", "Farbanzahl", "Größe der Palette der kolorierten Fassung.",
              "int", 2, 64, 1, "--colors"),
            P("color_merge_edge", "Gleichfarbige verschmelzen",
              "Gleichfarbige Nachbarn werden verschmolzen, wenn die mittlere Kantenstärke "
              "ihrer Grenze unter diesem Wert liegt. 0 = nie, 1 = immer.",
              "float", 0, 1, 0.05, "--color-merge-edge"),
            P("chroma_boost", "Sättigung",
              "Multiplikator für die Buntheit der Palette. 1 = wie im Original.",
              "float", 0.5, 2, 0.05, "--chroma-boost"),
            P("seed", "Zufalls-Seed", "Startwert des K-Means (andere Palettenvarianten).",
              "int", 0, 999, 1, "--seed", advanced=True),
        ],
        views=[
            V("flat", "Palettenfarben", "Flächen in ihrer Palettenfarbe mit Grenzen."),
            V("merged", "Zusammenführung",
              "Schwarz: verbleibende Grenzen. Rot: durch gleiche Farbe entfernte Grenzen."),
        ],
    ),
    dict(
        id="vectorize", title="Vektorisierung", short="Grenzgraph, Glättung, Béziers",
        description=(
            "Die Regionsgrenzen werden als planarer Graph auf dem Pixelgitter verfolgt: "
            "Ketten zwischen Knotenpunkten, an denen drei oder mehr Flächen aneinanderstoßen. "
            "Jede Kette wird geglättet (Knoten bleiben fest), mit Douglas-Peucker vereinfacht "
            "und in Bézier-Kurven umgewandelt. Linien und Füllflächen nutzen exakt dieselben "
            "Kurven – deshalb gibt es keine Lücken zwischen Farbe und Linie."),
        tip=("„Raster vs. Kurve“ zeigt rot die Pixeltreppen und schwarz die geglätteten "
             "Kurven. Hineinzoomen, um die Glättung zu beurteilen."),
        params=[
            P("curve_sigma", "Kurvenglättung",
              "Gauß-Glättung entlang der Grenzketten. Grenzen ohne Kantenevidenz werden "
              "automatisch bis 2,5× stärker geglättet.",
              "float", 0, 6, 0.1, "--curve-sigma", unit="px", live=True),
            P("simplify_eps", "Vereinfachung",
              "Douglas-Peucker-Toleranz. Höher = weniger Stützpunkte, kleinere Dateien, "
              "eckigere Kurven.",
              "float", 0, 4, 0.05, "--simplify-eps", unit="px", live=True),
        ],
        views=[
            V("curves", "Kurven", "Alle Grenzkurven einheitlich dünn.", "svg"),
            V("compare", "Raster vs. Kurve",
              "Rot: Pixelgrenzen vor der Glättung. Schwarz: Bézier-Kurven.", "svg"),
        ],
    ),
    dict(
        id="style", title="Linienstil & Ausgabe", short="Strichstärken, SVG",
        description=(
            "Jede Grenze erhält eine Stärke aus Kantenevidenz (60 %) und Farbkontrast der "
            "angrenzenden Flächen (40 %). Grenzen über der Schwelle werden als kräftige "
            "Hauptlinien, die übrigen als dünnere Binnenlinien gezeichnet. Ergebnis sind die "
            "Ausmalvorlage und die kolorierte Fassung als SVG."),
        tip="Exportieren speichert SVGs, PNG-Vorschauen und die Konfiguration in den Ausgabeordner.",
        params=[
            P("line_width", "Strichstärke",
              "Multiplikator für alle Linien (Hauptlinie 3,2 px, Binnenlinie 1,8 px, "
              "Rahmen 4 px bei 1600 px).",
              "float", 0.2, 4, 0.05, "--line-width", live=True),
            P("major_threshold", "Schwelle Hauptlinien",
              "Ab dieser Grenzstärke wird eine Linie kräftig gezeichnet. "
              "0 = alle kräftig, 1 = alle dünn.",
              "float", 0, 1, 0.01, "--major-threshold", live=True),
        ],
        views=[
            V("lines", "Ausmalvorlage", "*.lines.svg – schwarze Konturen auf Weiß.", "svg"),
            V("color", "Koloriert", "*.color.svg – Farbflächen unter den Konturen.", "svg"),
            V("hierarchy", "Linienhierarchie",
              "Blau: Hauptlinien (kräftig). Orange: Binnenlinien (dünn).", "svg"),
        ],
    ),
]
