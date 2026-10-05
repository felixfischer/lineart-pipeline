"""Builds compare.html from the variant dirs (run after run.sh)."""
import json, re
from pathlib import Path

here = Path(__file__).parent
# Variants = dirs holding stats.json. Label = the swept params parsed from the dir name.
variants = sorted(d.name for d in here.iterdir() if d.is_dir() and any(d.glob("*.stats.json")))
if not variants:
    raise SystemExit("no variant dirs with *.stats.json found — run run.sh first")
def label(v):
    m = re.match(r"ls(.+?)-cs(.+?)-sm(.+)$", v)
    return f"--label-sigma {m[1]} --curve-sigma {m[2]} --smoothing {m[3]}" if m else v
images = sorted({p.name.removesuffix(".stats.json")
                 for v in variants for p in (here / v).glob("*.stats.json")})
cards = []
for v in variants:
    for img in images:
        f = here / v / f"{img}.stats.json"
        if not f.exists():
            continue  # image not (yet) rendered for this variant — skip its card
        s = json.loads(f.read_text())
        sw = "".join(f'<i style="background:{c}"></i>' for c in s["palette"])
        cards.append(f'''<figure data-img="{img}">
<a href="{v}/{img}.lines.svg"><img class="lines" loading="lazy" src="{v}/{img}.lines.png"></a>
<a href="{v}/{img}.color.svg"><img class="color" loading="lazy" src="{v}/{img}.color.png"></a>
<figcaption><b>{v}</b> <code>{label(v)}</code><br>
{s["regions"]} Flächen · {s["chains"]} Linien · Linien-SVG {s["svg_bytes"]["lines"]//1024} KB<div class="sw">{sw}</div></figcaption></figure>''')

opts = "".join(f"<option>{i}</option>" for i in images)
(here / "compare.html").write_text(f'''<!doctype html><meta charset="utf-8"><title>Parameter-Vergleich</title>
<style>
body{{margin:0;padding:16px;font:14px system-ui;background:#f6f5f2;color:#1d1d1f}}
header{{position:sticky;top:0;background:#f6f5f2;padding:8px 0;display:flex;gap:16px;align-items:center;flex-wrap:wrap;z-index:1}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,var(--w,420px)),1fr));gap:14px}}
figure{{margin:0;background:#fff;border:1px solid #e2e0db;border-radius:8px;padding:8px}}
img{{width:100%;display:block}} figcaption{{font-size:12px;color:#555;margin-top:6px}}
.sw{{display:flex;gap:2px;margin-top:4px}} .sw i{{width:14px;height:14px;border-radius:3px}}
body.v-lines .color, body.v-color .lines{{display:none}}
</style>
<header>
<select id="img">{opts}</select>
<label><input type="radio" name="v" value="lines" checked> Linien</label>
<label><input type="radio" name="v" value="color"> Farbe</label>
<label>Größe <input type="range" id="w" min="250" max="1400" value="420"></label></header>
<main>{"".join(cards)}</main>
<script>
const f=()=>{{const i=img.value,v=document.querySelector('[name=v]:checked').value;
document.body.className='v-'+v;document.querySelectorAll('figure').forEach(e=>e.hidden=e.dataset.img!==i)}};
document.querySelectorAll('select,[name=v]').forEach(e=>e.onchange=f);
w.oninput=()=>document.querySelector('main').style.setProperty('--w',w.value+'px');f();
</script>''')
