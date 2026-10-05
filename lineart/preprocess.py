"""Stage 1: resizing, contrast normalisation and edge-preserving smoothing."""
from __future__ import annotations

import cv2
import numpy as np


def load_image(path) -> np.ndarray:
    """Load an image as 8-bit BGR, flattening alpha onto white."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError(f"cannot read image: {path}")
    return _to_bgr8(img)


def decode_image(data: bytes) -> np.ndarray:
    """Like ``load_image`` but from encoded bytes (e.g. an upload)."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("cannot decode image data")
    return _to_bgr8(img)


def _to_bgr8(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        a = img[..., 3:4].astype(np.float32) / 255.0
        img = (img[..., :3] * a + 255 * (1 - a)).astype(np.uint8)
    if img.dtype != np.uint8:
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return img


def resize_long_side(bgr: np.ndarray, long_side: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    s = long_side / max(h, w)
    if abs(s - 1) < 1e-3:
        return bgr.copy()
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
    return cv2.resize(bgr, (round(w * s), round(h * s)), interpolation=interp)


def normalize_contrast(bgr: np.ndarray, clip: float = 1.5) -> np.ndarray:
    """Mild CLAHE on the L channel; keeps hues, lifts flat/faded scans."""
    if clip <= 0:
        return bgr.copy()
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    lab[..., 0] = clahe.apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def smooth(bgr: np.ndarray, strength: float) -> np.ndarray:
    """Edge-preserving smoothing that flattens brush strokes and paper grain.

    Mean-shift filtering collapses texture into plateaus while keeping
    region borders sharp; a light bilateral pass removes residual speckle.
    ``strength`` ~ 0 (off) .. 1 (strong).
    """
    if strength <= 0:
        return bgr.copy()
    scale = max(bgr.shape[:2]) / 1600
    sp = max(3, int(round(6 + 14 * strength) * scale))
    sr = 10 + 25 * strength
    out = cv2.pyrMeanShiftFiltering(bgr, sp, sr, maxLevel=1)
    d = max(5, int(round(9 * scale)) | 1)
    return cv2.bilateralFilter(out, d, 20 + 30 * strength, d)


def to_lab(bgr: np.ndarray) -> np.ndarray:
    """Float Lab (L in 0..100, a/b roughly -128..127)."""
    return cv2.cvtColor(bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
