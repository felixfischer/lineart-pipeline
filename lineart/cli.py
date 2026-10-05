"""Command line interface."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

from . import report, verify
from .pipeline import PRESETS, config_from_dict, make_config, process_image

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def _inputs(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_EXT)
    if path.is_file():
        return [path]
    raise SystemExit(f"input not found: {path}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="lineart",
        description="Convert raster images into coloring-book SVGs "
                    "(*.lines.svg) and flat-colour previews (*.color.svg).")
    ap.add_argument("-i", "--input", required=True, type=Path,
                    help="image file or directory of images")
    ap.add_argument("-o", "--output", default=Path("output"), type=Path,
                    help="output directory (default: output/)")
    ap.add_argument("--mode", choices=("lines", "color", "both"), default="both")
    ap.add_argument("-d", "--detail-level", choices=tuple(PRESETS), default="medium",
                    help="preset for region count, palette size and smoothing")
    g = ap.add_argument_group("fine tuning (override the preset)")
    # Defaults shown are for -d medium; an unset flag inherits the active preset.
    g.add_argument("--regions", type=int, dest="target_regions",
                   help="target number of colourable areas (medium: 600)")
    g.add_argument("--colors", type=int,
                   help="palette size of the colour version (medium: 18)")
    g.add_argument("--smoothing", type=float,
                   help="edge-preserving noise filter strength 0..1 (medium: 0.5; 0 = off)")
    g.add_argument("--min-area", type=int,
                   help="smallest area in px, at 1600 px (medium: 180)")
    g.add_argument("--label-sigma", type=float,
                   help="border smoothing radius in px (medium: 3.0; 0 = off)")
    g.add_argument("--curve-sigma", type=float,
                   help="vector curve smoothing in px (medium: 1.6; 0 = off)")
    g.add_argument("--line-width", type=float,
                   help="stroke width multiplier (medium: 1.0)")
    g.add_argument("--work-size", type=int,
                   help="working resolution, long side in px (default 1600)")
    g.add_argument("--edge-backend", choices=("auto", "dexined", "classical"))
    g.add_argument("--seed", type=int)
    a = ap.add_argument_group("advanced (defaults are tuned; see the GUI for their effect)")
    a.add_argument("--clahe-clip", type=float, help="contrast normalisation strength (0 = off)")
    a.add_argument("--global-weight", type=float,
                   help="share of the coarse DexiNed pass in the edge map 0..1")
    a.add_argument("--min-width", type=float, help="absorb slivers thinner than this (px)")
    a.add_argument("--edge-weight", type=float,
                   help="merge cost: share of border edge strength vs. colour 0..1")
    a.add_argument("--color-scale", type=float,
                   help="merge cost: Lab ΔE that counts as fully different")
    a.add_argument("--chroma-boost", type=float, help="palette saturation multiplier")
    a.add_argument("--color-merge-edge", type=float,
                   help="merge same-colour neighbours whose border is weaker than this 0..1")
    a.add_argument("--simplify-eps", type=float, help="Douglas-Peucker tolerance (px)")
    a.add_argument("--major-threshold", type=float,
                   help="border strength from which a line is drawn bold 0..1")
    ap.add_argument("--config", type=Path,
                    help="JSON config (e.g. exported from the GUI); flags override it")
    ap.add_argument("--no-preview", action="store_true", help="skip PNG previews")
    ap.add_argument("--no-report", action="store_true", help="skip output/index.html")
    ap.add_argument("--debug", action="store_true", help="write intermediate maps")
    ap.add_argument("-q", "--quiet", action="store_true")
    return ap


def _apply_flags(cfg, args):
    """Explicit command line flags win over preset and --config."""
    cfg = replace(cfg, **{k: v for k, v in dict(
        target_regions=args.target_regions, colors=args.colors, smoothing=args.smoothing,
        min_area=args.min_area, label_sigma=args.label_sigma, curve_sigma=args.curve_sigma,
        line_width=args.line_width, work_size=args.work_size, edge_backend=args.edge_backend,
        seed=args.seed, clahe_clip=args.clahe_clip, global_weight=args.global_weight,
        min_width=args.min_width, chroma_boost=args.chroma_boost,
        color_merge_edge=args.color_merge_edge, simplify_eps=args.simplify_eps,
        major_threshold=args.major_threshold).items() if v is not None})
    merge = {k: v for k, v in dict(edge_weight=args.edge_weight,
                                   color_scale=args.color_scale).items() if v is not None}
    return replace(cfg, merge=replace(cfg.merge, **merge)) if merge else cfg


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(message)s")
    cfg = make_config(args.detail_level)
    if args.config:
        cfg = config_from_dict(json.loads(args.config.read_text()), cfg)
    cfg = _apply_flags(cfg, args)
    out: Path = args.output
    out.mkdir(parents=True, exist_ok=True)
    results, failed = [], 0
    for img in _inputs(args.input):
        res = process_image(img, out, cfg, mode=args.mode, save_debug=args.debug)
        report.save_original(img, out / f"{res.name}.original.jpg")
        for kind, svg in res.files.items():
            if not args.no_preview:
                report.render_png(svg, svg.with_suffix(".png"))
            chk = verify.check_svg(svg)
            status = "ok" if chk["ok"] else "FAILED: " + "; ".join(chk["errors"])
            logging.info("  %s: %d paths, %s", svg.name, chk.get("paths", 0), status)
            failed += not chk["ok"]
        results.append(res)
    if results and not args.no_report:
        logging.info("report: %s", report.write_report(out, results))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
