"""HTML comparison report: original vs lines vs color, side by side.

All artifact paths passed in are relative to the output directory, so the
report works when opened from anywhere inside that directory.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Comparison report – {title}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         margin: 0; background: #f4f4f2; color: #1a1a1a; }}
  header {{ padding: 24px 32px; background: #1a1a1a; color: #fff; }}
  header h1 {{ margin: 0 0 4px; font-size: 20px; font-weight: 600; }}
  header p  {{ margin: 0; opacity: .7; font-size: 13px; }}
  main {{ padding: 24px 32px 64px; max-width: 1500px; margin: 0 auto; }}
  .card {{ background: #fff; border: 1px solid #e2e2df; border-radius: 10px;
          overflow: hidden; margin-bottom: 28px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .card h2 {{ margin: 0; padding: 12px 18px; font-size: 14px; font-weight: 600;
             background: #fafaf8; border-bottom: 1px solid #ececea;
             display: flex; justify-content: space-between; align-items: center; }}
  .card h2 .meta {{ font-weight: 400; opacity: .55; font-size: 12px; }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px;
          background: #ececea; }}
  .cell {{ background: #fff; display: flex; flex-direction: column; }}
  .cell img {{ display: block; width: 100%; height: auto; }}
  .cell .label {{ padding: 8px 12px; font-size: 12px; color: #666;
                 border-top: 1px solid #f0f0ee; display: flex;
                 justify-content: space-between; }}
  .cell .label a {{ color: #0a66c2; text-decoration: none; font-size: 12px; }}
  @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>Lineart pipeline – comparison report</h1>
  <p>{meta}</p>
</header>
<main>
{cards}
</main>
</body>
</html>
"""


def write_report(out_dir: Path, entries: list[dict], title: str,
                 meta: str = "") -> Path:
    """entries: dicts with keys
    name, original, lines, color, lines_png, color_png,
    orig_size, work_size – all paths relative to ``out_dir``."""
    cards = []
    for e in entries:
        cells = [
            _cell(e.get("original"), "Original (input)", ""),
            _cell(e.get("lines_png") or e.get("lines"),
                  "Lines – .lines.svg", e.get("lines")),
            _cell(e.get("color_png") or e.get("color"),
                  "Color – .color.svg", e.get("color")),
        ]
        cards.append(f"""  <div class="card">
    <h2>{e['name']} <span class="meta">{e.get('orig_size','')} &rarr; {e.get('work_size','')}</span></h2>
    <div class="grid">
{''.join(cells)}
    </div>
  </div>""")

    html = _TEMPLATE.format(title=title, meta=meta, cards="\n".join(cards))
    out = out_dir / "index.html"
    out.write_text(html, encoding="utf-8")
    return out


def _cell(src, label: str, link) -> str:
    if src:
        img_html = f'<img src="{Path(src)}" alt="{label}"/>'
    else:
        img_html = ('<div style="height:120px;display:flex;align-items:center;'
                    'justify-content:center;color:#999;font-size:13px;">'
                    'not available</div>')
    link_html = f'<a href="{Path(link)}">open SVG &nearr;</a>' if link else ""
    return f"""      <div class="cell">
        {img_html}
        <div class="label"><span>{label}</span>{link_html}</div>
      </div>"""
