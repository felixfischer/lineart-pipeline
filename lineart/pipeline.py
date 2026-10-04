"""Pipeline orchestration: image -> region map -> planar graph -> SVGs."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

from . import edges as edges_mod
from . import palette as pal_mod
from . import preprocess as pp
from . import segment as seg
from . import topology as topo
from . import vectorize as vec

log = logging.getLogger(__name__)


@dataclass
class Config:
    work_size: int = 1600          # long side of the working raster (px)
    smoothing: float = 0.5         # 0..1 edge-preserving pre-smoothing
    global_weight: float = 0.5     # share of the coarse DexiNed pass
    target_regions: int = 600      # approx. number of colourable areas
    min_area: int = 180            # px at work_size; smaller areas absorbed
    min_width: float = 3.0         # px; thinner slivers absorbed
    colors: int = 18               # palette size of the colour version
    label_sigma: float = 3.0       # label-map border smoothing (px)
    curve_sigma: float = 1.6       # Gaussian smoothing of vector borders (px)
    simplify_eps: float = 0.6      # Douglas-Peucker tolerance (px)
    line_width: float = 1.0        # multiplier for all stroke widths
    edge_backend: str = "auto"     # auto | dexined | classical
    seed: int = 0
    merge: seg.MergeParams = field(default_factory=seg.MergeParams)


PRESETS = {
    "low": dict(smoothing=0.7, target_regions=280, min_area=420, min_width=4.0,
                colors=12, label_sigma=4.0, curve_sigma=2.0, line_width=1.15),
    "medium": dict(),
    "high": dict(smoothing=0.35, target_regions=1100, min_area=90, min_width=2.5,
                 colors=26, label_sigma=2.0, curve_sigma=1.3, line_width=0.85),
}


def make_config(detail: str = "medium", **overrides) -> Config:
    cfg = Config(**PRESETS[detail])
    overrides = {k: v for k, v in overrides.items() if v is not None}
    return replace(cfg, **overrides)


@dataclass
class Result:
    name: str
    files: dict
    stats: dict


def _has_pinch(labels: np.ndarray) -> bool:
    a, b, c, d = labels[:-1, :-1], labels[:-1, 1:], labels[1:, :-1], labels[1:, 1:]
    return bool((((a == d) & (b != a) & (c != a)) | ((b == c) & (a != b) & (d != b))).any())


def _clean_topology(labels, E, lab, p: seg.MergeParams) -> np.ndarray:
    tiny = replace(p, min_area=max(8, p.min_area // 4), min_width=0.0)
    for _ in range(4):
        labels = seg.split_components(seg.remove_diagonal_contacts(labels))
        g = seg.RegionGraph(labels, E, lab)
        g.absorb_small(tiny)
        labels = g.relabel()
        if not _has_pinch(labels):
            return labels
    return seg.split_components(seg.remove_diagonal_contacts(labels, max_iter=100))


def process_image(path: Path, out_dir: Path, cfg: Config, mode: str = "both",
                  save_debug: bool = False) -> Result:
    t0 = time.time()
    name = path.stem
    timings = {}

    def tick(stage):
        timings[stage] = round(time.time() - t0 - sum(timings.values()), 2)
        log.info("[%s] %s done (%.1fs)", name, stage, timings[stage])

    # 1. Pre-processing ------------------------------------------------------
    src = pp.load_image(path)
    bgr = pp.normalize_contrast(pp.resize_long_side(src, cfg.work_size))
    scale2 = (max(bgr.shape[:2]) / 1600.0) ** 2
    smooth = pp.smooth(bgr, cfg.smoothing)
    lab_smooth = pp.to_lab(smooth)
    lab_color = pp.to_lab(cv2.bilateralFilter(bgr, 9, 30, 9))
    tick("preprocess")

    # 2a. Semantic edges --------------------------------------------------
    E = edges_mod.edge_map(smooth, lab_smooth, cfg.edge_backend, cfg.global_weight)
    # Texture level: painterly images have edge responses everywhere (median
    # ~0.45 for Starry Night vs ~0.05 for a woodblock print). Region borders
    # of such images follow brush strokes and are smoothed more strongly.
    texture = float(np.clip(np.median(E) / 0.4, 0.0, 1.0))
    tick("edges")

    # 2b/3. Segmentation + merging + clean-up ------------------------------
    p = replace(cfg.merge, target_regions=int(cfg.target_regions * (1 - 0.2 * texture)),
                min_area=int(cfg.min_area * scale2), min_width=cfg.min_width)
    labels, _ = seg.segment(E, lab_smooth, lab_color, p)
    lsig = cfg.label_sigma * (1.0 + 0.5 * texture) * max(bgr.shape[:2]) / 1600.0
    labels = _clean_topology(seg.smooth_labels(labels, lsig), E, lab_color, p)
    tick("segment")

    # 2b. Palette quantisation; neighbours that end up with the same palette
    # colour and have no real edge between them are merged (fewer lines).
    means = pal_mod.region_medians_robust(labels, lab_color)
    area = np.bincount(labels.ravel())
    palette, assign = pal_mod.quantize(means, area, cfg.colors, seed=cfg.seed)
    g = seg.RegionGraph(labels, E, lab_color)
    g.merge_where(assign, p, max_edge=0.4)
    labels = _clean_topology(seg.smooth_labels(g.relabel(), lsig), E, lab_color, p)
    means = pal_mod.region_medians_robust(labels, lab_color)
    area = np.bincount(labels.ravel())
    assign = np.argmin(((means[:, None, :] - palette[None]) ** 2).sum(2), 1)
    pal_hex = pal_mod.lab_to_hex(palette)
    tick("palette")

    # 4. Vectorisation ----------------------------------------------------
    graph = topo.build_graph(labels)
    vec.chain_strength(graph, E, {i: palette[a] for i, a in enumerate(assign)})
    vec.fit_chains(graph, cfg.curve_sigma, cfg.simplify_eps)
    w = cfg.line_width * max(bgr.shape[:2]) / 1600.0
    style = vec.LineStyle(major_width=3.2 * w, minor_width=1.8 * w, frame_width=4.0 * w)
    tick("vectorize")

    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    if mode in ("lines", "both"):
        f = out_dir / f"{name}.lines.svg"
        f.write_text(vec.lines_svg(graph, style, f"{name} – coloring page"), encoding="utf-8")
        files["lines"] = f
    if mode in ("color", "both"):
        f = out_dir / f"{name}.color.svg"
        colors = {i: pal_hex[a] for i, a in enumerate(assign)}
        bg_idx = int(np.argmax(np.bincount(assign, weights=area, minlength=len(palette))))
        svg = vec.color_svg(graph, style, colors, {i: int(a) for i, a in enumerate(assign)},
                            f"{name} – colored preview", pal_hex[bg_idx])
        f.write_text(svg, encoding="utf-8")
        files["color"] = f
    if save_debug:
        dbg = out_dir / "debug"
        dbg.mkdir(exist_ok=True)
        cv2.imwrite(str(dbg / f"{name}.edges.png"), 255 - (E * 255).astype(np.uint8))
        cv2.imwrite(str(dbg / f"{name}.smooth.jpg"), smooth)
    tick("write")

    stats = {
        "work_size": [int(bgr.shape[1]), int(bgr.shape[0])],
        "texture": round(texture, 3),
        "regions": int(labels.max() + 1),
        "chains": len(graph.chains),
        "palette": pal_hex,
        "timings_s": timings,
        "svg_bytes": {k: v.stat().st_size for k, v in files.items()},
    }
    (out_dir / f"{name}.stats.json").write_text(json.dumps(stats, indent=2))
    return Result(name, files, stats)


def config_dict(cfg: Config) -> dict:
    return asdict(cfg)
