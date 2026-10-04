"""Pipeline orchestration: one image -> line/color SVGs + previews."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import edges, preprocess, render, report, segments, svgbuild, vectorize
from .config import Config


@dataclass
class RunResult:
    name: str
    lines_svg: Path
    color_svg: Path
    lines_png: Path | None
    color_png: Path | None
    orig_size: tuple[int, int]
    work_size: tuple[int, int]
    n_regions: int
    elapsed: float


def run_one(image_path: str | Path, out_dir: Path, cfg: Config,
            mode: str = "both", debug: bool = False,
            make_previews: bool = True) -> RunResult:
    image_path = Path(image_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = image_path.stem
    t0 = time.time()

    # ---- 1. preprocess ----------------------------------------------------
    prep = preprocess.prepare(str(image_path), cfg)
    H, W = prep.img_bgr.shape[:2]

    # ---- 2b. segmentation ----------------------------------------------------
    seg = segments.segment(prep.lab, cfg)
    if debug:
        _save_debug(out_dir / "debug" / name, prep, seg, cfg)

    # ---- 2a/3. line mask ---------------------------------------------------
    thin, dilated = edges.line_mask(seg, cfg)
    lines_mask = edges.clean_line_mask(dilated, cfg)

    # ---- fills: union per color, cut by the dilated line mask --------------
    colors_rgb = seg.colors_rgb
    color_masks: dict[tuple[int, int, int], np.ndarray] = {}
    for rid in range(seg.n):
        m = (seg.labels == rid) & (lines_mask == 0)
        if m.any():
            key = colors_rgb[rid]
            color_masks[key] = (
                color_masks.get(key, np.zeros_like(m)) | m
            )
    # trace larger colors first (better z-order would be arbitrary anyway:
    # masks are disjoint, order only affects file readability)
    ordered = sorted(color_masks.items(),
                     key=lambda kv: int(kv[1].sum()), reverse=True)

    # ---- 4. vectorize --------------------------------------------------------
    line_paths = vectorize.trace_mask(lines_mask, H, W, turd=cfg.turd)
    fill_paths: list[tuple[str, str]] = []
    for (r, g, b), m in ordered:
        for d in vectorize.trace_mask(m, H, W, turd=cfg.turd):
            fill_paths.append((d, f"#{r:02x}{g:02x}{b:02x}"))

    # ---- 4b. assemble SVGs ---------------------------------------------------
    lines_svg_path = out_dir / f"{name}.lines.svg"
    color_svg_path = out_dir / f"{name}.color.svg"
    if mode in ("lines", "both"):
        lines_svg_path.write_text(
            svgbuild.build_lines_svg(W, H, line_paths, f"{name} – coloring lines"),
            encoding="utf-8")
    if mode in ("color", "both"):
        color_svg_path.write_text(
            svgbuild.build_color_svg(W, H, fill_paths, line_paths,
                                     f"{name} – colored"),
            encoding="utf-8")

    # ---- previews --------------------------------------------------------------
    lines_png = color_png = None
    if make_previews:
        if mode in ("lines", "both"):
            p = out_dir / f"{name}.lines.png"
            lines_png = p if render.render_svg_to_png(
                lines_svg_path, p, cfg.preview_width) else None
        if mode in ("color", "both"):
            p = out_dir / f"{name}.color.png"
            color_png = p if render.render_svg_to_png(
                color_svg_path, p, cfg.preview_width) else None

    return RunResult(
        name=name,
        lines_svg=lines_svg_path if mode in ("lines", "both") else Path(),
        color_svg=color_svg_path if mode in ("color", "both") else Path(),
        lines_png=lines_png,
        color_png=color_png,
        orig_size=prep.orig_size,
        work_size=(W, H),
        n_regions=seg.n,
        elapsed=time.time() - t0,
    )


def run_batch(input_paths: list[Path], out_dir: Path, cfg: Config,
              mode: str = "both", debug: bool = False,
              make_previews: bool = True, make_report: bool = True,
              verbose: bool = True) -> list[RunResult]:
    results: list[RunResult] = []
    for i, p in enumerate(input_paths, 1):
        if verbose:
            print(f"[{i}/{len(input_paths)}] {p.name} ...", flush=True)
        r = run_one(p, out_dir, cfg, mode=mode, debug=debug,
                    make_previews=make_previews)
        if verbose:
            bits = [f"{r.lines_svg.name}"] if r.lines_svg else []
            if r.color_svg:
                bits.append(r.color_svg.name)
            print(f"    -> {' + '.join(bits)}"
                  f"  ({r.n_regions} regions, {r.elapsed:.1f}s)", flush=True)
        results.append(r)

    if make_report and results:
        import shutil
        orig_dir = out_dir / "originals"

        def rel(p: Path | None) -> str:
            if p is None:
                return ""
            try:
                return str(Path(p).relative_to(out_dir))
            except ValueError:
                return Path(p).name

        entries = []
        for r in results:
            src = next((p for p in input_paths if p.stem == r.name), None)
            dst = None
            if src is not None:
                orig_dir.mkdir(parents=True, exist_ok=True)
                dst = orig_dir / src.name
                if src.resolve() != dst.resolve():
                    shutil.copy2(src, dst)
            entries.append({
                "name": r.name,
                "original": rel(dst) if src is not None else "",
                "lines": rel(r.lines_svg),
                "color": rel(r.color_svg),
                "lines_png": rel(r.lines_png),
                "color_png": rel(r.color_png),
                "orig_size": f"{r.orig_size[0]}×{r.orig_size[1]}",
                "work_size": f"{r.work_size[0]}×{r.work_size[1]}",
            })
        report.write_report(out_dir, entries, title="Lineart pipeline",
                            meta=f"{cfg.describe()} – {len(results)} image(s)")
    return results


def _save_debug(d: Path, prep, seg: segments.Segmentation, cfg: Config) -> None:
    d.mkdir(parents=True, exist_ok=True)
    lab_rgb = segments.lab_to_rgb(prep.lab)
    cv2.imwrite(str(d / "smoothed.png"),
                cv2.cvtColor(lab_rgb, cv2.COLOR_RGB2BGR))
    colored = segments.lab_to_rgb(
        seg.colors_lab[seg.labels.reshape(-1)]
        .reshape(seg.labels.shape + (3,)))
    cv2.imwrite(str(d / "segments.png"),
                cv2.cvtColor(colored, cv2.COLOR_RGB2BGR))
    counts = np.bincount(seg.labels.ravel(), minlength=seg.n)
    widths = segments._region_widths(seg.labels)
    top = sorted(counts.tolist(), reverse=True)[:10]
    print(f"    [debug] {seg.n} regions, top sizes: {top}, "
          f"widths: {np.round(widths, 1).tolist()}", flush=True)
