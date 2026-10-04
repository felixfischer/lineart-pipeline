"""Render SVG artifacts to PNG previews via the resvg CLI (or skip gracefully)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _resvg() -> str | None:
    return shutil.which("resvg")


def render_svg_to_png(svg_path: str | Path, png_path: str | Path,
                      width: int = 1400) -> bool:
    """Render an SVG file to PNG at the given width. Returns success flag."""
    exe = _resvg()
    if exe is None:
        return False
    svg_path = Path(svg_path)
    png_path = Path(png_path)
    try:
        subprocess.run(
            [exe, "-w", str(width), str(svg_path), str(png_path)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        return png_path.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
