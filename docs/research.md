# KI-gestützte Pipeline für Ausmalbilder

**Ziel:** Konvertierung von komplexen Pixelbildern (z. B. Gemälden mit Pinselstrichen, Texturen, Schattierungen) in saubere, druckfertige Vektor-Umrisse für Ausmalbücher.

---

## 1. Die moderne 4-Stufen-Pipeline

Herkömmliche Methoden (Canny-Edge, Thresholding) scheitern an Texturen. Eine intelligente Pipeline kombiniert Semantic Understanding mit Vektorisierung:

1. **Segmentierung & Fokus (SAM - Segment Anything Model):**
   * *Zweck:* Trennt das Hauptmotiv vom Hintergrund, um unruhige Hintergrundlinien im Ausmalbuch zu vermeiden.
2. **Semantische Kantenerkennung (Lineart Extraktion):**
   * *Zweck:* Erkennt "echte" Objektgrenzen und ignoriert Pinselstriche oder Rauschen.
   * *Tools:* DexiNed, ControlNet Lineart Preprocessor, Anime-Lineart-Modelle.
3. **Generative Stilisierung & Bereinigung (Image-to-Image):**
   * *Zweck:* Schließt Lücken, erzwingt einen einheitlichen "Coloring Book"-Stil (klare schwarze Linien auf weißem Grund).
   * *Tools:* Stable Diffusion + ControlNet.
4. **Intelligente Vektorisierung:**
   * *Zweck:* Konvertierung der Pixel-Skizze in glatte, skalierbare Bézier-Kurven für den Druck.
   * *Tools:* VTracer (modern, fließend), DiffVG (KI-basiert), Potrace (klassisch).

---

## 2. Hardware-Check: Was läuft auf dem Entwickler-Laptop?

*   **Segmentierung (SAM):** ✅ Lokal möglich (MobileSAM benötigt nur 1-2 GB RAM).
*   **Kanten-Extraktion (DexiNed):** ✅ Lokal möglich (Sehr kleine Modelle, CPU-kompatibel).
*   **Vektorisierung (VTracer):** ✅ Lokal möglich (Reine CPU-Berechnung, extrem schnell).
*   **Generative Bereinigung (Stable Diffusion):** ⚠️ **Flaschenhals.** Benötigt starke GPU (6-12 GB VRAM) oder modernes Apple Silicon (M1/M2/M3 mit 16GB+ RAM) via Metal. Auf reinen CPU-Laptops zu langsam für Batch-Verarbeitung.

---

## 3. Die drei Umsetzungs-Strategien

Je nach verfügbarer Hardware bieten sich für einen Entwickler drei konkrete Lösungswege an:

### Strategie A: Der "Lightweight & Local" Ansatz (CPU-Only)
*   **Ablauf:** SAM (Fokus) ➔ DexiNed (Kanten) ➔ OpenCV (Morphologische Filter statt KI-Bereinigung) ➔ VTracer.
*   **Vorteil:** Läuft auf jedem Laptop komplett lokal, kostenlos und in wenigen Sekunden pro Bild.
*   **Nachteil:** Linienverbindungen sind eventuell etwas unsauberer als bei der generativen KI-Methode.

### Strategie B: Der Hybrid-Ansatz (Lokal + Cloud-API)
*   **Ablauf:** Vorbereitung lokal (SAM, Crop) ➔ API-Call zu Replicate/RunPod für Stable Diffusion (Stilisierung) ➔ Vektorisierung lokal (VTracer).
*   **Vorteil:** Maximale Qualität ohne teure eigene Hardware; Laptop wird geschont.
*   **Nachteil:** Verursacht API-Kosten (im Cent-Bereich pro Bild); benötigt Internetverbindung.

### Strategie C: Der "Heavy Local" Ansatz (MacBook Pro oder Gaming-Laptop)
*   **Ablauf:** Die volle 4-Stufen-Pipeline läuft lokal.
*   **Voraussetzung:** Apple Silicon M-Chip (ab 16 GB Unified Memory, besser 32 GB) oder dedizierte Nvidia-GPU (ab 8 GB VRAM).
*   **Vorteil:** Keine Cloud-Kosten, höchste Qualität, voller Datenschutz.