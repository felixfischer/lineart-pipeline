"""``python -m lineart.gui`` – start the local web GUI."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .server import serve


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="lineart-gui",
        description="Interactive GUI: inspect every pipeline stage and tune its parameters.")
    ap.add_argument("image", nargs="?", type=Path, help="image to open at start")
    ap.add_argument("-s", "--source", type=Path, default=Path("source"),
                    help="directory listed in the image picker (default: source/)")
    ap.add_argument("-o", "--output", type=Path, default=Path("output"),
                    help="export directory (default: output/)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(message)s")
    if args.image is not None and not args.image.is_file():
        ap.error(f"image not found: {args.image}")
    serve(args.source, args.output, args.host, args.port, not args.no_browser, args.image)
    return 0


if __name__ == "__main__":
    sys.exit(main())
