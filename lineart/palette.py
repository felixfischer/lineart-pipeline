"""Stage 2b (colour): flat palette for the region map."""
from __future__ import annotations

import cv2
import numpy as np
from sklearn.cluster import KMeans


def region_means(labels: np.ndarray, lab: np.ndarray) -> np.ndarray:
    n = int(labels.max()) + 1
    flat = labels.ravel()
    area = np.bincount(flat, minlength=n).astype(np.float64)
    means = np.stack([np.bincount(flat, lab[..., c].ravel(), n) for c in range(3)], 1)
    return means / np.maximum(area, 1)[:, None]


def region_medians_robust(labels: np.ndarray, lab: np.ndarray) -> np.ndarray:
    """Trimmed per-region colour (drops the 20% most deviating pixels), which
    is less polluted by left-over outline pixels than a plain mean."""
    means = region_means(labels, lab)
    d = np.linalg.norm(lab - means[labels], axis=2)
    n = len(means)
    flat = labels.ravel()
    order = np.lexsort((d.ravel(), flat))
    counts = np.bincount(flat, minlength=n)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    keep = np.zeros(flat.size, bool)
    rank = np.arange(flat.size) - np.repeat(starts, counts)
    keep[order] = rank < np.maximum(1, (0.8 * np.repeat(counts, counts)).astype(int))
    lab_flat = lab.reshape(-1, 3)
    kept_lab = lab_flat[keep]
    kept_lbl = flat[keep]
    s = np.stack([np.bincount(kept_lbl, kept_lab[:, c], n) for c in range(3)], 1)
    k = np.bincount(kept_lbl, minlength=n)
    return s / np.maximum(k, 1)[:, None]


def quantize(region_lab: np.ndarray, area: np.ndarray, k: int,
             chroma_boost: float = 1.1, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Area-weighted k-means over region colours.

    Returns (palette_lab[k,3], assignment[n_regions]). Weighting by area makes
    the palette follow what dominates visually; small accents still get their
    own cluster if they are far from everything else.
    """
    k = int(min(k, len(region_lab)))
    # sqrt-weighting keeps small but distinct accents (stars, windows) alive.
    w = np.sqrt(np.maximum(area, 1))
    km = KMeans(n_clusters=k, n_init=8, random_state=seed)
    km.fit(region_lab, sample_weight=w)
    pal = km.cluster_centers_.copy()
    pal[:, 1:] *= chroma_boost
    pal[:, 0] = np.clip(pal[:, 0], 0, 100)
    pal[:, 1:] = np.clip(pal[:, 1:], -127, 127)
    return pal, km.labels_


def lab_to_hex(lab: np.ndarray) -> list[str]:
    lab = np.asarray(lab, np.float32).reshape(-1, 1, 3)
    bgr = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR).reshape(-1, 3)
    rgb = np.clip(np.round(bgr[:, ::-1] * 255), 0, 255).astype(int)
    return ["#%02x%02x%02x" % tuple(c) for c in rgb]
