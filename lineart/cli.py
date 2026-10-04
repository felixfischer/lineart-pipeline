"""Command-line interface of the lineart pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, preset
from .runner import run_batch

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lineart",
        description="Convert photos/paintings into coloring-book SVGs "
                    "(line art + flat-color version).",
    )
    p.add_argument("-i", "--input", required=True,
                   help="image file(s) or a directory of images")
    p.add_argument("-o", "--output", default="output",
                   help="output directory for SVGs & artifacts (default: output)")
    p.add_argument("-m", "--mode", choices=["lines", "color", "both"],
                   default="both",
                   help="what to produce (default: both)")
    p.add_argument("-d", "--detail-level", choices=["low", "medium", "high"],
                   default="medium",
                   help="density preset (default: medium)")

    g = p.add_argument_group("tuning overrides")
    g.add_argument("--max-side", type=int, default=None,
                   help="longest edge of the working raster in px")
    g.add_argument("--sigma", type=float, default=None,
                   help="smoothing sigma before segmentation (px)")
    g.add_argument("--colors", type=int, default=None,
                   help="K-means cluster count (max flat colors)")
    g.add_argument("--absorb-width", type=float, default=None,
                   help="dissolve regions thinner than this (px)")
    g.add_argument("--de-min", type=float, default=None,
                   help="min delta-E for a boundary to become a line")
    g.add_argument("--de-merge", type=float, default=None,
                   help="delta-E below which adjacent segments merge")
    g.add_argument("--line-width", type=float, default=None,
                   help="half-width of lines in px")
    g.add_argument("--min-region", type=int, default=None,
                   help="smallest surviving region in px^2")
    g.add_argument("--seed", type=int, default=None,
                   help="K-means random seed (default: 42)")

    o = p.add_argument_group("output options")
    o.add_argument("--preview-width", type=int, default=None,
                   help="PNG preview width in px (default: 1400)")
    o.add_argument("--no-preview", action="store_true",
                   help="skip PNG preview rendering")
    o.add_argument("--no-report", action="store_true",
                   help="skip the HTML comparison report")
    o.add_argument("--debug", action="store_true",
                   help="write intermediate images to output/debug/")
    o.add_argument("-q", "--quiet", action="store_true")
    return p


def collect_images(input_path: Path) -> list[Path]:
    if input_path.is_dir():
        files = sorted(
            f for f in input_path.iterdir()
            if f.suffix.lower() in IMAGE_EXTS and not f.name.startswith(".")
        )
        if not files:
            raise SystemExit(f"no images found in {input_path}")
        return files
    if input_path.suffix.lower() in IMAGE_EXTS:
        return [input_path]
    raise SystemExit(
        f"unsupported input {input_path} (expected an image file or a directory)")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    cfg: Config = preset(args.detail_level)
    overrides = {
        "max_side": args.max_side,
        "sigma": args.sigma,
        "n_clusters": args.colors,
        "absorb_width": args.absorb_width,
        "de_min": args.de_min,
        "de_merge": args.de_merge,
        "line_width": args.line_width,
        "min_region": args.min_region,
        "seed": args.seed,
        "preview_width": args.preview_width,
    }
    cfg = cfg.with_overrides(
        **{k: v for k, v in overrides.items() if v is not None})

    images = collect_images(Path(args.input))
    out_dir = Path(args.output)

    run_batch(
        images,
        out_dir,
        cfg,
        mode=args.mode,
        debug=args.debug,
        make_previews=not args.no_preview,
        make_report=not args.no_report,
        verbose=not args.quiet,
    )

    if not args.quiet:
        print(f"\ndone – artifacts in {out_dir}/")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
