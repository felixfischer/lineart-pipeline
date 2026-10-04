"""SVG verification: well-formedness, structure and fill coverage."""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

SVG_NS = "{http://www.w3.org/2000/svg}"
_NUM = re.compile(r"nan|inf", re.I)


def check_svg(path: Path, coverage: bool = True) -> dict:
    """Return a dict of checks; raises nothing, reports problems in 'errors'."""
    errors: list[str] = []
    info: dict = {"file": str(path)}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return {"file": str(path), "ok": False, "errors": [f"XML parse error: {exc}"]}
    if root.tag != f"{SVG_NS}svg":
        errors.append("root element is not <svg>")
    vb = root.get("viewBox")
    if not vb or len(vb.split()) != 4:
        errors.append("missing/invalid viewBox")
    paths = root.iter(f"{SVG_NS}path")
    n_paths = 0
    for p in paths:
        n_paths += 1
        d = p.get("d", "")
        if _NUM.search(d):
            errors.append(f"non-finite coordinate in path {p.get('id')}")
    info["paths"] = n_paths
    groups = {g.get("id") for g in root.iter(f"{SVG_NS}g")}
    info["groups"] = sorted(g for g in groups if g)
    if "line-art" not in groups:
        errors.append("missing <g id='line-art'>")
    if path.name.endswith(".color.svg"):
        if "color-fills" not in groups:
            errors.append("missing <g id='color-fills'>")
        else:
            order = [g.get("id") for g in root if g.tag == f"{SVG_NS}g"]
            if order.index("color-fills") > order.index("line-art"):
                errors.append("color-fills must be below line-art")
        if coverage:
            gap = fill_gap_ratio(path)
            if gap is not None:
                info["fill_gap_ratio"] = gap
                if gap > 1e-4:
                    errors.append(f"colour fills leave {gap:.4%} of the canvas uncovered")
    info["errors"] = errors
    info["ok"] = not errors
    return info


def fill_gap_ratio(path: Path) -> float | None:
    """Render only the colour fills (no background, no lines) and return the
    fraction of transparent pixels, i.e. holes between adjacent regions."""
    try:
        import cairosvg
        from PIL import Image
    except Exception:  # pragma: no cover - optional dependency
        return None
    ET.register_namespace("", SVG_NS[1:-1])
    tree = ET.parse(path)
    root = tree.getroot()
    for el in list(root):
        if el.get("id") in ("line-art", "background"):
            root.remove(el)
    png = cairosvg.svg2png(bytestring=ET.tostring(root))
    alpha = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))[..., 3]
    return float((alpha < 128).mean())
