"""Pipeline orchestration: image -> region map -> planar graph -> SVGs.

The pipeline is split into stages (``STAGES``) that each read the results of
the previous ones from a ``Run`` and add their own. The CLI runs them in one
go; the GUI (``lineart.gui``) runs them individually and caches each stage's
result so that only stages downstream of a changed parameter are recomputed.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field, fields, replace
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
    clahe_clip: float = 1.5        # CLAHE clip limit on L (0 = off)
    smoothing: float = 0.5         # 0..1 edge-preserving pre-smoothing
    global_weight: float = 0.5     # share of the coarse DexiNed pass
    target_regions: int = 600      # approx. number of colourable areas
    min_area: int = 180            # px at work_size; smaller areas absorbed
    min_width: float = 3.0         # px; thinner slivers absorbed
    colors: int = 18               # palette size of the colour version
    chroma_boost: float = 1.1      # saturation multiplier of the palette
    color_merge_edge: float = 0.4  # same-colour neighbours merge below this edge
    label_sigma: float = 3.0       # label-map border smoothing (px)
    curve_sigma: float = 1.6       # Gaussian smoothing of vector borders (px)
    simplify_eps: float = 0.6      # Douglas-Peucker tolerance (px)
    line_width: float = 1.0        # multiplier for all stroke widths
    major_threshold: float = 0.42  # border strength from which a line is "major"
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


def config_dict(cfg: Config) -> dict:
    return asdict(cfg)


def config_from_dict(d: dict, base: Config | None = None) -> Config:
    """Inverse of ``config_dict``; unknown keys are rejected, missing ones
    keep the value of ``base`` (default: the ``medium`` preset)."""
    base = base or Config()
    known = {f.name for f in fields(Config)}
    unknown = set(d) - known
    if unknown:
        raise ValueError(f"unknown config keys: {', '.join(sorted(unknown))}")
    out = {k: _coerce(getattr(base, k), v) for k, v in d.items() if k != "merge"}
    if "merge" in d:
        m = d["merge"]
        bad = set(m) - {f.name for f in fields(seg.MergeParams)}
        if bad:
            raise ValueError(f"unknown config keys: merge.{', merge.'.join(sorted(bad))}")
        out["merge"] = replace(base.merge, **{k: _coerce(getattr(base.merge, k), v)
                                              for k, v in m.items()})
    return replace(base, **out)


def _coerce(ref, v):
    """JSON (e.g. from the browser) writes 3.0 as 3; keep the field types."""
    if isinstance(ref, bool) or not isinstance(ref, (int, float)):
        return v
    return type(ref)(v)


@dataclass
class Result:
    name: str
    files: dict
    stats: dict


@dataclass
class Run:
    """Intermediate results of one image, filled in stage by stage.

    Stages only ever *add* fields, so a shallow ``copy.copy`` after each
    stage is a valid snapshot to restart the following stages from.
    """
    name: str
    src: np.ndarray
    timings: dict = field(default_factory=dict)
    # 1. preprocess
    resized: np.ndarray | None = None      # BGR at work size, before CLAHE
    bgr: np.ndarray | None = None          # after contrast normalisation
    smooth: np.ndarray | None = None       # edge-preserving smoothed BGR
    lab_smooth: np.ndarray | None = None
    lab_color: np.ndarray | None = None    # lightly filtered, for colours
    # 2a. edges
    E: np.ndarray | None = None            # fused edge probability [0, 1]
    edge_parts: dict | None = None         # backend, global/tiled DexiNed maps
    texture: float = 0.0
    # 2b/3. segmentation
    merge_params: seg.MergeParams | None = None
    label_sigma_eff: float = 0.0
    overseg: np.ndarray | None = None      # watershed result before merging
    seg_labels: np.ndarray | None = None
    # palette
    labels: np.ndarray | None = None       # final region map
    palette: np.ndarray | None = None      # Lab, (k, 3)
    pal_hex: list | None = None
    assign: np.ndarray | None = None       # region -> palette index
    area: np.ndarray | None = None
    regions_before_merge: int = 0
    # 4. vectorisation / line style
    graph: topo.Graph | None = None
    style: vec.LineStyle | None = None

    @property
    def scale(self) -> float:
        """Working size relative to the 1600 px reference."""
        return max(self.bgr.shape[:2]) / 1600.0


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


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------

def stage_preprocess(run: Run, cfg: Config) -> None:
    run.resized = pp.resize_long_side(run.src, cfg.work_size)
    run.bgr = pp.normalize_contrast(run.resized, cfg.clahe_clip)
    run.smooth = pp.smooth(run.bgr, cfg.smoothing)
    run.lab_smooth = pp.to_lab(run.smooth)
    run.lab_color = pp.to_lab(cv2.bilateralFilter(run.bgr, 9, 30, 9))


def stage_edges(run: Run, cfg: Config) -> None:
    parts: dict = {}
    run.E = edges_mod.edge_map(run.smooth, run.lab_smooth, cfg.edge_backend,
                               cfg.global_weight, parts=parts)
    run.edge_parts = parts
    # Texture level: painterly images have edge responses everywhere (median
    # ~0.45 for Starry Night vs ~0.05 for a woodblock print). Region borders
    # of such images follow brush strokes and are smoothed more strongly.
    run.texture = float(np.clip(np.median(run.E) / 0.4, 0.0, 1.0))


def stage_segment(run: Run, cfg: Config) -> None:
    t = run.texture
    p = replace(cfg.merge, target_regions=int(cfg.target_regions * (1 - 0.2 * t)),
                min_area=int(cfg.min_area * run.scale ** 2), min_width=cfg.min_width)
    run.merge_params = p
    run.overseg = seg.oversegment(run.E, run.lab_smooth)
    g = seg.RegionGraph(run.overseg, run.E, run.lab_color)
    g.merge_greedy(p)
    g.absorb_small(p)
    # (Expression order kept from v1 so results stay bit-identical.)
    run.label_sigma_eff = cfg.label_sigma * (1.0 + 0.5 * t) * max(run.bgr.shape[:2]) / 1600.0
    run.seg_labels = _clean_topology(seg.smooth_labels(g.relabel(), run.label_sigma_eff),
                                     run.E, run.lab_color, p)


def stage_palette(run: Run, cfg: Config) -> None:
    # Palette quantisation; neighbours that end up with the same palette
    # colour and have no real edge between them are merged (fewer lines).
    p, lsig, E, lab = run.merge_params, run.label_sigma_eff, run.E, run.lab_color
    labels = run.seg_labels
    run.regions_before_merge = int(labels.max() + 1)
    means = pal_mod.region_medians_robust(labels, lab)
    area = np.bincount(labels.ravel())
    palette, assign = pal_mod.quantize(means, area, cfg.colors, cfg.chroma_boost, seed=cfg.seed)
    g = seg.RegionGraph(labels, E, lab)
    g.merge_where(assign, p, max_edge=cfg.color_merge_edge)
    labels = _clean_topology(seg.smooth_labels(g.relabel(), lsig), E, lab, p)
    means = pal_mod.region_medians_robust(labels, lab)
    run.labels = labels
    run.area = np.bincount(labels.ravel())
    run.assign = np.argmin(((means[:, None, :] - palette[None]) ** 2).sum(2), 1)
    run.palette = palette
    run.pal_hex = pal_mod.lab_to_hex(palette)


def stage_vectorize(run: Run, cfg: Config) -> None:
    graph = topo.build_graph(run.labels)
    vec.chain_strength(graph, run.E, {i: run.palette[a] for i, a in enumerate(run.assign)})
    vec.fit_chains(graph, cfg.curve_sigma, cfg.simplify_eps)
    run.graph = graph


def stage_style(run: Run, cfg: Config) -> None:
    w = cfg.line_width * max(run.bgr.shape[:2]) / 1600.0
    run.style = vec.LineStyle(major_width=3.2 * w, minor_width=1.8 * w, frame_width=4.0 * w,
                              major_threshold=cfg.major_threshold)


# (name, function, Config fields the stage reads). The GUI uses the field
# lists to decide which cached stages a parameter change invalidates.
STAGES = (
    ("preprocess", stage_preprocess, ("work_size", "clahe_clip", "smoothing")),
    ("edges", stage_edges, ("edge_backend", "global_weight")),
    ("segment", stage_segment, ("target_regions", "min_area", "min_width", "label_sigma",
                                "merge")),
    ("palette", stage_palette, ("colors", "chroma_boost", "color_merge_edge", "seed")),
    ("vectorize", stage_vectorize, ("curve_sigma", "simplify_eps")),
    ("style", stage_style, ("line_width", "major_threshold")),
)


def run_stage(run: Run, name: str, cfg: Config) -> None:
    fn = next(f for n, f, _ in STAGES if n == name)
    t0 = time.time()
    fn(run, cfg)
    run.timings[name] = round(time.time() - t0, 2)
    log.info("[%s] %s done (%.1fs)", run.name, name, run.timings[name])


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def render_svgs(run: Run, mode: str = "both") -> dict[str, str]:
    """SVG documents of a finished run, keyed by ``lines`` / ``color``."""
    name, graph, style = run.name, run.graph, run.style
    out = {}
    if mode in ("lines", "both"):
        out["lines"] = vec.lines_svg(graph, style, f"{name} – coloring page")
    if mode in ("color", "both"):
        colors = {i: run.pal_hex[a] for i, a in enumerate(run.assign)}
        bg_idx = int(np.argmax(np.bincount(run.assign, weights=run.area,
                                           minlength=len(run.palette))))
        out["color"] = vec.color_svg(graph, style, colors,
                                     {i: int(a) for i, a in enumerate(run.assign)},
                                     f"{name} – colored preview", run.pal_hex[bg_idx])
    return out


def write_outputs(run: Run, out_dir: Path, mode: str = "both",
                  save_debug: bool = False) -> Result:
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for kind, svg in render_svgs(run, mode).items():
        f = out_dir / f"{run.name}.{kind}.svg"
        f.write_text(svg, encoding="utf-8")
        files[kind] = f
    if save_debug:
        dbg = out_dir / "debug"
        dbg.mkdir(exist_ok=True)
        cv2.imwrite(str(dbg / f"{run.name}.edges.png"), 255 - (run.E * 255).astype(np.uint8))
        cv2.imwrite(str(dbg / f"{run.name}.smooth.jpg"), run.smooth)
    timings = dict(run.timings)
    # Line style is part of writing the SVGs (as before the stage split).
    timings["write"] = round(timings.pop("style", 0.0) + time.time() - t0, 2)
    stats = {
        "work_size": [int(run.bgr.shape[1]), int(run.bgr.shape[0])],
        "texture": round(run.texture, 3),
        "regions": int(run.labels.max() + 1),
        "chains": len(run.graph.chains),
        "palette": run.pal_hex,
        "timings_s": timings,
        "svg_bytes": {k: v.stat().st_size for k, v in files.items()},
    }
    (out_dir / f"{run.name}.stats.json").write_text(json.dumps(stats, indent=2))
    return Result(run.name, files, stats)


def process_image(path: Path, out_dir: Path, cfg: Config, mode: str = "both",
                  save_debug: bool = False) -> Result:
    run = Run(path.stem, pp.load_image(path))
    for name, _, _ in STAGES:
        run_stage(run, name, cfg)
    return write_outputs(run, out_dir, mode, save_debug)
