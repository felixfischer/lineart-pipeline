"""Stage 2a/3: line mask from segment boundaries.

Lines are the *complete* borders of the final regions: every pixel that
separates two adjacent regions becomes a boundary pixel (optionally
thinned out for very weak color differences via cfg.de_min). Complete
borders guarantee closed, consistent strokes - the hallmark of a usable
coloring page - because the merge pass (not a pixel gate) decides which
regions exist.

The 1-px boundary is then dilated to the target line width. The dilated mask
is the single source of truth: fills are traced from its complement, so lines
and fills are congruent by construction.
"""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray

from .config import Config
from .segments import Segmentation


def _de_between(a: NDArray[np.int32], b: NDArray[np.int32],
                colors: NDArray[np.float32]) -> NDArray[np.float32]:
    """CIE76 delta-E of two segment-label arrays, elementwise (vectorized).

    colors[a] - colors[b] has shape (H, W, 3); norm over the last axis."""
    return np.linalg.norm(colors[a] - colors[b], axis=-1)


def boundary_map(seg: Segmentation, cfg: Config) -> NDArray[np.uint8]:
    """1-px boundary pixels between adjacent final regions.

    With cfg.de_min == 0 every region border is drawn (closed strokes);
    otherwise borders whose color difference is below de_min are skipped
    (an optional thinning control - fills are unaffected).
    """
    labels = seg.labels
    h, w = labels.shape

    up, down = labels[:-1, :], labels[1:, :]
    left, right = labels[:, :-1], labels[:, 1:]

    mask = np.zeros((h, w), dtype=np.uint8)
    if cfg.de_min <= 0:
        mask[:-1, :] |= (up != down).astype(np.uint8)
        mask[:, :-1] |= (left != right).astype(np.uint8)
        return mask
    colors = seg.colors_lab
    vu = (up != down) & (_de_between(up, down, colors) >= cfg.de_min)
    mask[:-1, :] |= vu.astype(np.uint8)          # boundary at the upper row
    vd = (left != right) & (_de_between(left, right, colors) >= cfg.de_min)
    mask[:, :-1] |= vd.astype(np.uint8)          # boundary at the left column
    return mask


def line_mask(seg: Segmentation, cfg: Config) -> tuple[NDArray[np.uint8], NDArray[np.uint8]]:
    """Return (thin_boundary, dilated_line_mask).

    dilated_line_mask covers the boundary +- cfg.line_width px; its complement
    is the set of fillable regions.
    """
    thin = boundary_map(seg, cfg)
    r = int(round(cfg.line_width))
    if r <= 0:
        return thin, thin.copy()
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    dilated = cv2.dilate(thin, kern)
    return thin, dilated


def clean_line_mask(dilated: NDArray[np.uint8], cfg: Config) -> NDArray[np.uint8]:
    """Drop isolated line specks (area < turd).

    Speck filtering happens on the dilated mask, which also enforces a minimum
    stroke size without any thinning/skeleton logic.
    """
    mask = dilated.copy()
    if cfg.turd > 1:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        keep = np.zeros(n, dtype=bool)
        keep[0] = True
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= cfg.turd
        mask[~keep[labels]] = 0
    return mask
