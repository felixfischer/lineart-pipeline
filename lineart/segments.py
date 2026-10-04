"""Stage 2b: flat-color segmentation of the smoothed Lab field.

K-means quantization of the strongly-smoothed Lab image yields flat, closed
color regions - texture is already gone, and every pixel belongs to exactly
one region, so all regions are fillable (the coloring-book property).

K-means alone has two defects that post-processing removes:

  * gradient halos: a smooth color transition (e.g. sky -> wave) is split
    into its own intermediate-color band. These bands are *thin strips*, so
    the strip-absorption pass dissolves them into their neighbour with the
    closest color - the boundary then sits at the true transition.
  * fragmentation: noisy patches of near-identical color survive as small
    islands; the small-region and similar-region merges dissolve those.

Post-processing order:
  1. morphological cleanup of single-pixel label noise,
  2. strip absorption (width below cfg.absorb_width px),
  3. merge of tiny regions into their dominant neighbor,
  4. fixed-point merge of adjacent near-identical regions (delta-E <
     cfg.de_merge).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray
from sklearn.cluster import MiniBatchKMeans


@dataclass
class Segmentation:
    labels: NDArray[np.int32]              # region id per pixel, dense 0..n-1
    colors_lab: NDArray[np.float32]        # (n, 3) CIE-Lab, L in 0..100

    @property
    def n(self) -> int:
        return int(self.labels.max()) + 1

    @property
    def colors_rgb(self) -> list[tuple[int, int, int]]:
        """sRGB color per region id."""
        return [tuple(int(v) for v in row)
                for row in lab_rows_to_rgb(self.colors_lab)]


# ------------------------------------------------------------------ colors

def _clamp_lab_to_u8(lab: NDArray[np.float32]) -> NDArray[np.uint8]:
    out = np.empty(lab.shape, dtype=np.uint8)
    out[..., 0] = (lab[..., 0] * 100.0).clip(0, 255)
    out[..., 1] = lab[..., 1].clip(0, 255)
    out[..., 2] = lab[..., 2].clip(0, 255)
    return out


def lab_to_rgb(lab: NDArray[np.float32]) -> NDArray[np.uint8]:
    """Lab (H,W,3) with L in 0..100 -> sRGB (H,W,3) uint8."""
    return cv2.cvtColor(_clamp_lab_to_u8(lab), cv2.COLOR_LAB2RGB)


def lab_rows_to_rgb(rows: NDArray[np.float32]) -> NDArray[np.uint8]:
    """Lab (N,3), L in 0..100 -> sRGB (N,3) uint8."""
    return cv2.cvtColor(_clamp_lab_to_u8(rows).reshape(-1, 1, 3),
                        cv2.COLOR_LAB2RGB).reshape(-1, 3)


def _delta_e(c1: np.ndarray, c2: np.ndarray) -> float:
    """CIE76 distance between two Lab colors."""
    return float(np.linalg.norm(c1 - c2))


def gradient_magnitude(lab: NDArray[np.float32]) -> NDArray[np.float32]:
    """L2 magnitude of the per-channel Scharr gradient of the Lab field."""
    gx = np.stack([cv2.Scharr(lab[..., c], cv2.CV_32F, 1, 0)
                   for c in range(3)], axis=-1)
    gy = np.stack([cv2.Scharr(lab[..., c], cv2.CV_32F, 0, 1)
                   for c in range(3)], axis=-1)
    return np.sqrt((gx ** 2 + gy ** 2).sum(axis=-1))


# ------------------------------------------------------------------ helpers

def _compact_and_color(labels: NDArray[np.int32],
                       lab: NDArray[np.float32]) -> Segmentation:
    """Dense relabel + mean Lab color per region (from the pixels)."""
    dense = np.unique(labels, return_inverse=True)[1]
    labels = dense.astype(np.int32)
    n = int(labels.max()) + 1
    flat_lab = lab.reshape(-1, 3)
    sums = np.zeros((n, 3), dtype=np.float64)
    np.add.at(sums, labels.ravel(), flat_lab)
    counts = np.bincount(labels.ravel(), minlength=n)
    means = sums / np.maximum(counts[:, None], 1.0)
    return Segmentation(labels=labels, colors_lab=means.astype(np.float32))


def _region_widths(labels: NDArray[np.int32]) -> NDArray[np.float32]:
    """Estimated stroke width per region: 2 * area / perimeter.

    Perimeter in pixel-edge units via P = 4*A - S, where S is the number of
    pixel-edges shared with the same region (each shared edge cancels two
    boundary edges).
    """
    n = int(labels.max()) + 1
    a = labels.ravel()
    area = np.bincount(a, minlength=n).astype(np.float64)
    shared = np.zeros(n, dtype=np.int64)
    up, down = labels[:-1, :], labels[1:, :]
    left, right = labels[:, :-1], labels[:, 1:]
    for x, y in ((up, down), (left, right)):
        m = x == y
        np.add.at(shared, x[m], 1)
        np.add.at(shared, y[m], 1)
    perim = 4.0 * area - shared            # boundary edges of region i
    widths = np.where(perim > 0, 2.0 * area / np.maximum(perim, 1), 0.0)
    return widths


def _adjacent_pairs(labels: NDArray[np.int32]) -> list[tuple[int, int]]:
    """Unique unordered label pairs that share at least one pixel edge."""
    up, down = labels[:-1, :], labels[1:, :]
    left, right = labels[:, :-1], labels[:, 1:]
    p1 = np.unique(np.stack([np.minimum(up, down).ravel(),
                              np.maximum(up, down).ravel()]).T, axis=0)
    p2 = np.unique(np.stack([np.minimum(left, right).ravel(),
                              np.maximum(left, right).ravel()]).T, axis=0)
    pairs = np.unique(np.concatenate([p1, p2], axis=0), axis=0)
    return [(int(i), int(j)) for i, j in pairs.tolist() if i != j]


def _neighbors_of(labels: NDArray[np.int32], i: int, n: int) -> np.ndarray:
    """Region ids adjacent to region i (by pixel edge)."""
    mask = (labels == i).astype(np.uint8)
    border = cv2.dilate(mask, np.ones((3, 3), np.uint8)) & ~mask
    if not border.any():
        return np.array([], dtype=np.int32)
    return np.unique(labels[border > 0])


def _cleanup_labels(labels: NDArray[np.int32], k: int) -> NDArray[np.int32]:
    """Open+close each class mask; refill pixels lost by the openings."""
    kern = np.ones((3, 3), np.uint8)
    out = np.full(labels.shape, -1, dtype=np.int32)
    for c in range(k):
        m = (labels == c).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kern)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kern)
        out[m > 0] = c
    missing = out < 0
    if missing.any():
        out = _relax_fill(out)
    return out


def _relax_fill(out: NDArray[np.int32], passes: int = 4) -> NDArray[np.int32]:
    """Fill -1 pixels from valid neighbours (4-connected, a few sweeps)."""
    shifts = ((0, 1), (0, -1), (1, 0), (-1, 0))
    for _ in range(passes):
        if not (out < 0).any():
            break
        for dy, dx in shifts:
            layer = np.roll(np.roll(out, -dy, axis=0), -dx, axis=1)
            good = (out < 0) & (layer >= 0)
            out[good] = layer[good]
    out[out < 0] = 0
    return out


# ------------------------------------------------------------------ stages

def segment(lab: NDArray[np.float32], cfg, seed: int | None = None) -> Segmentation:
    """Quantize the smoothed Lab image into clean, fillable color regions."""
    h, w = lab.shape[:2]
    flat = lab.reshape(-1, 3)
    k = int(min(cfg.n_clusters, lab.size // 50, 64))
    k = max(2, k)

    km = MiniBatchKMeans(
        n_clusters=k,
        batch_size=10000,
        n_init=1,
        max_iter=60,
        random_state=seed if seed is not None else cfg.seed,
    )
    labels = km.fit_predict(flat).astype(np.int32).reshape(h, w)
    labels = _cleanup_labels(labels, k)

    seg = _compact_and_color(labels, lab)
    seg = _absorb_strips(seg, lab, cfg)
    seg = _merge_small_regions(seg, lab, cfg)
    seg = _merge_similar_regions(seg, lab, cfg)
    return seg


def _absorb_strips(seg: Segmentation, lab: NDArray[np.float32],
                   cfg) -> Segmentation:
    """Dissolve thin intermediate regions (gradient halos, slivers).

    A region with estimated width < cfg.absorb_width px is absorbed into its
    closest-color neighbour *only if* it is a genuine intermediate band: its
    two largest neighbours must be different regions, and the strip must be
    perceptually closer to one of them than the two neighbours are to each
    other. Thin high-contrast structures (a white foam tendril flanked by
    navy on both sides) fail this test and are preserved.
    Iterated, because absorption can create new thin neighbours.
    """
    labels = seg.labels.copy()
    colors = seg.colors_lab.copy()

    def current_n() -> int:
        return int(labels.max()) + 1

    for _ in range(300):
        n = current_n()
        widths = _region_widths(labels)
        strips = [i for i in range(n)
                  if widths[i] < cfg.absorb_width and
                  (labels == i).sum() > 0]
        if not strips:
            break
        changed = False
        areas = np.bincount(labels.ravel(), minlength=n)
        for i in strips:
            neigh = _neighbors_of(labels, i, n)
            if neigh.size < 2:
                continue
            ordered = neigh[np.argsort(-areas[neigh])]
            a, b = int(ordered[0]), int(ordered[1])
            if a == b:
                continue
            de_ab = _delta_e(colors[a], colors[b])
            de_a = _delta_e(colors[i], colors[a])
            de_b = _delta_e(colors[i], colors[b])
            closer = a if de_a <= de_b else b
            de_closer = min(de_a, de_b)
            if de_closer <= 0.75 * de_ab:
                labels[labels == i] = closer
                ci = int(areas[i])
                colors[closer] = (
                    (colors[i] * ci + colors[closer] * areas[closer])
                    / max(ci + areas[closer], 1))
                changed = True
        if not changed:
            break
    return _compact_and_color(labels, lab)


def _merge_small_regions(seg: Segmentation, lab: NDArray[np.float32], cfg) -> Segmentation:
    """Regions smaller than cfg.min_region px are absorbed by a neighbor."""
    labels = seg.labels.copy()
    n = seg.n
    kern = np.ones((3, 3), np.uint8)
    for _ in range(80):
        counts = np.bincount(labels.ravel(), minlength=n)
        small = [i for i in range(n) if 0 < counts[i] < cfg.min_region]
        if not small:
            break
        for i in small:
            mask = (labels == i).astype(np.uint8)
            border = cv2.dilate(mask, kern) & ~mask
            if not border.any():
                target = int(np.argmax(counts))
            else:
                neigh = np.bincount(labels[border > 0], minlength=n)
                neigh[i] = 0
                target = int(np.argmax(neigh)) if neigh.sum() > 0 else int(np.argmax(counts))
            labels[mask] = target
        n = int(labels.max()) + 1
    return _compact_and_color(labels, lab)


def _merge_similar_regions(seg: Segmentation, lab: NDArray[np.float32], cfg) -> Segmentation:
    """Merge adjacent regions that should not be separate color areas.

    A pair is merged when
      * its color difference is tiny (delta-E < cfg.de_merge), or
      * the boundary is a *soft* transition - the gradient of the smoothed
        field at the border is below cfg.soft_grad, i.e. there is no real
        contour, just a gentle gradient that k-means happened to split
        (e.g. the beige sky of the Great Wave) - with a moderate color
        difference (delta-E < cfg.soft_de).

    Fixed-point iteration: after a merge, the new neighbours are compared
    again. Boundaries with a strong gradient (true contours: wave outlines,
    cypress silhouette, inked lines) always survive, so Van Gogh's distinct
    blue sky tones stay separate while gradient banding collapses.
    """
    labels = seg.labels.copy()
    colors = seg.colors_lab.copy()
    m = gradient_magnitude(lab)
    kern = np.ones((3, 3), np.uint8)

    for _ in range(500):
        pairs = _adjacent_pairs(labels)
        best_key, best_pair = (None, None)
        for i, j in pairs:
            de = _delta_e(colors[i], colors[j])
            hard = de < cfg.de_merge
            if not hard:
                mask_i = (labels == i).astype(np.uint8)
                border = cv2.dilate(mask_i, kern) & (labels == j)
                gmed = float(np.median(m[border > 0])) if border.any() else 0.0
                if not (gmed < cfg.soft_grad and de < cfg.soft_de):
                    continue
            key = (0, de) if hard else (1, gmed + de)
            if best_key is None or key < best_key:
                best_key, best_pair = key, (i, j)
        if best_pair is None:
            break
        i, j = best_pair
        ci, cj = int((labels == i).sum()), int((labels == j).sum())
        small, large = (i, j) if ci <= cj else (j, i)
        labels[labels == small] = large
        colors[large] = (colors[i] * ci + colors[j] * cj) / (ci + cj)
    return _compact_and_color(labels, lab)
