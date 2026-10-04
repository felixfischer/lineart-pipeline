"""Stage 1: load, resize, denoise and convert to Lab for segmentation."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from .config import Config


@dataclass
class Prepared:
    """Working-resolution image data shared across pipeline stages."""

    img_bgr: NDArray[np.uint8]        # working resolution, BGR, uint8
    img_smooth: NDArray[np.uint8]     # lightly smoothed BGR (for region colors)
    lab: NDArray[np.float32]          # strongly smoothed CIE-Lab, float32
    gray: NDArray[np.uint8]           # grayscale of working image
    original_path: str
    orig_size: tuple[int, int]        # (w, h) of the source image
    work_size: tuple[int, int]        # (w, h) of the working image


def _to_lab(rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    # OpenCV's L channel is scaled by 255/100; rescale to true 0-100
    lab[..., 0] /= 100.0
    return lab


def prepare(path: str, cfg: Config) -> Prepared:
    """Load an image, downscale to the working size and smooth it.

    Two smoothing passes:
      * ``img_smooth`` – light bilateral pass, used later for vivid region colors
      * ``lab``        – strong isotropic blur in Lab. Brushstroke / paper
        texture (period ~5-15 px) is annihilated while macro color fields and
        object boundaries survive. This is what makes segmentation semantic.
    """
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(f"cannot read image: {path}")

    h, w = bgr.shape[:2]
    scale = min(1.0, cfg.max_side / float(max(h, w)))
    if scale < 1.0:
        new_size = (max(2, round(w * scale)), max(2, round(h * scale)))
        bgr = cv2.resize(bgr, new_size, interpolation=cv2.INTER_AREA)

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    # light, edge-preserving pass for color sampling
    img_smooth = cv2.bilateralFilter(bgr, d=5, sigmaColor=25.0, sigmaSpace=3.0)

    # strong smooth in Lab for the segmentation field
    sigma = max(1.5, cfg.sigma)
    lab = _to_lab(rgb)
    lab_blur = cv2.GaussianBlur(lab, (0, 0), sigma, 0)

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return Prepared(
        img_bgr=bgr,
        img_smooth=img_smooth,
        lab=lab_blur,
        gray=gray,
        original_path=path,
        orig_size=(w, h),
        work_size=(bgr.shape[1], bgr.shape[0]),
    )
