"""Preview PNG rendering and the HTML comparison report."""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import cv2

log = logging.getLogger(__name__)


def render_png(svg: Path, png: Path, width: int = 1600) -> bool:
    try:
        import cairosvg
    except Exception as exc:  # pragma: no cover - depends on system cairo
        log.warning("cairosvg unavailable (%s); skipping PNG preview", exc)
        return False
    cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=width)
    return True


def save_original(src, dst: Path, long_side: int = 1600) -> None:
    """``src`` is an image path or an already decoded BGR array."""
    img = cv2.imread(str(src)) if isinstance(src, (str, Path)) else src
    h, w = img.shape[:2]
    s = min(1.0, long_side / max(h, w))
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dst), img, [cv2.IMWRITE_JPEG_QUALITY, 88])


_CSS = """
:root{--bg:#f6f5f2;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e2e0db}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececef;--muted:#9a9aa2;--card:#1e1e22;--line:#2e2e34}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1500px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:0}
.sub{color:var(--muted);margin:0 0 28px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:28px}
.head{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:baseline;margin-bottom:14px}
.stats{color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
@media (max-width:900px){.grid{grid-template-columns:1fr}}
figure{margin:0}
figure img{width:100%;height:auto;display:block;border:1px solid var(--line);border-radius:6px;background:#fff}
figcaption{font-size:13px;color:var(--muted);margin-top:6px;display:flex;justify-content:space-between;gap:8px}
figcaption a{color:inherit}
.swatches{display:flex;flex-wrap:wrap;gap:3px;margin-top:12px}
.swatches span{width:22px;height:22px;border-radius:4px;border:1px solid rgba(0,0,0,.15)}
.cmp{position:relative;margin-top:16px;max-width:900px}
.cmp img{width:100%;display:block;border-radius:6px}
.cmp .top{position:absolute;inset:0;clip-path:inset(0 50% 0 0)}
.cmp input{width:100%;margin-top:6px}
"""


def write_report(out_dir: Path, results: list) -> Path:
    blocks = []
    for r in results:
        name = html.escape(r.name)
        st = r.stats
        sizes = st.get("svg_bytes", {})
        stats = (f"{st['regions']} Flächen · {len(st['palette'])} Farben · "
                 f"{st['work_size'][0]}×{st['work_size'][1]} px · "
                 f"Laufzeit {sum(st['timings_s'].values()):.1f}s · "
                 + " · ".join(f"{k}.svg {v / 1024:.0f} KB" for k, v in sizes.items()))
        sw = "".join(f'<span style="background:{c}" title="{c}"></span>' for c in st["palette"])
        figs = [f'<figure><img src="{name}.original.jpg" alt="Original" loading="lazy">'
                f'<figcaption><span>Original</span></figcaption></figure>']
        for kind, label in (("lines", "Ausmalvorlage"), ("color", "Kolorierte Vorschau")):
            if kind in r.files:
                figs.append(
                    f'<figure><img src="{name}.{kind}.svg" alt="{label}" loading="lazy">'
                    f'<figcaption><span>{label}</span>'
                    f'<span><a href="{name}.{kind}.svg">SVG</a>'
                    + (f' · <a href="{name}.{kind}.png">PNG</a>' if (out_dir / f"{name}.{kind}.png").exists() else "")
                    + "</span></figcaption></figure>")
        cmp = ""
        if "color" in r.files:
            cmp = (f'<div class="cmp"><img src="{name}.original.jpg" alt="">'
                   f'<img class="top" src="{name}.color.svg" alt="">'
                   '<input type="range" min="0" max="100" value="50" aria-label="Vergleich" '
                   'oninput="this.previousElementSibling.style.clipPath='
                   "'inset(0 '+(100-this.value)+'% 0 0)'\"></div>")
        blocks.append(
            f'<section><div class="head"><h2>{name}</h2><span class="stats">{stats}</span></div>'
            f'<div class="grid">{"".join(figs)}</div><div class="swatches">{sw}</div>{cmp}</section>')
    doc = (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Lineart Report</title><style>{_CSS}</style></head><body><main>"
        "<h1>Lineart-Pipeline – Ergebnisse</h1>"
        '<p class="sub">Original · Ausmalvorlage (<code>*.lines.svg</code>) · '
        "kolorierte Vorschau (<code>*.color.svg</code>). Der Schieberegler blendet "
        "zwischen Original und Kolorierung.</p>"
        + "".join(blocks) + "</main></body></html>\n")
    path = out_dir / "index.html"
    path.write_text(doc, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(
        {r.name: r.stats for r in results}, indent=2, ensure_ascii=False))
    return path
