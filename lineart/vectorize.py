"""Stage 4: bitmap masks -> smooth Bezier paths via potrace.

potrace runs in its default SVG mode. Its output wraps the paths in a Y-flipped
group (``translate(0,H) scale(1,-1)``); we keep the path data untouched and
re-apply that transform when assembling the final SVG, so no path rewriting
is needed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from numpy.typing import NDArray


def _potrace() -> str:
    exe = shutil.which("potrace")
    if exe is None:
        raise RuntimeError(
            "potrace not found in PATH. Install it: `brew install potrace` "
            "(macOS) or `sudo apt-get install potrace` (Debian/Ubuntu)."
        )
    return exe


def _write_pbm(mask: NDArray[np.uint8], path: Path) -> None:
    """Write a binary PBM (P4) file: mask pixels are black (traced by potrace)."""
    h, w = mask.shape
    rows = [np.packbits(r, bitorder="big").tobytes() for r in mask]
    with open(path, "wb") as f:
        f.write(f"P4\n{w} {h}\n".encode("ascii"))
        f.write(b"".join(rows))


def trace_mask(mask: NDArray[np.uint8], h: int, w: int,
               opt_tolerance: float = 0.2, alpha_max: float = 1.0,
               turd: int = 2) -> list[str]:
    """Vectorize a binary mask and return potrace path `d` attribute values.

    Returned paths are in potrace's Y-flipped coordinate system (origin in the
    lower-left); the caller wraps them in the corresponding transform group.
    """
    binmask = (np.asarray(mask, dtype=np.uint8) > 0).astype(np.uint8)
    if not binmask.any():
        return []

    with tempfile.TemporaryDirectory(prefix="lineart-") as td:
        td = Path(td)
        pbm = td / "mask.pbm"
        svg = td / "mask.svg"
        _write_pbm(binmask, pbm)
        # NOTE: -u must stay 1 so that potrace's SVG coordinate space matches
        # the pixel viewBox. With -u 10 potrace emits path coordinates in
        # 0.1 px units while the viewBox stays in px, which breaks any
        # transform we add afterwards.
        subprocess.run(
            [
                _potrace(),
                "-s",                        # SVG backend
                "-u", "1",                   # 1 px coordinate precision
                "-t", str(max(1, turd)),     # speckle suppression
                "-a", f"{alpha_max:.2f}",    # corner detection (1.0 = smooth)
                "-O", f"{opt_tolerance:.2f}",
                "-o", str(svg),
                str(pbm),
            ],
            check=True,
            capture_output=True,
        )
        text = svg.read_text(encoding="utf-8")

    return re.findall(r'd="([^"]+)"', text)


def y_flip_group(h: int) -> str:
    """The potrace-style Y-flip transform for a mask of height h."""
    return f'transform="translate(0 {h}) scale(1 -1)"'
