"""Stage 2a: semantic edge probability maps.

Primary backend is DexiNed (ONNX, opencv_zoo export, CPU via onnxruntime).
The network has a fixed 640x480 input, so it is run at two scales:

* ``global``  – the whole image letterboxed into one 640x480 frame. At this
  scale brush strokes are below the receptive field and only large object
  contours survive.
* ``tiled``   – overlapping 640x480 tiles over the working image, which
  localises contours precisely.

The fused map is a weighted geometric blend: tiled edges are only kept where
the global pass also sees structure, which suppresses painterly texture.

If the model is unavailable, a classical fallback (multi-scale colour
gradient on a smoothed image) is used so the pipeline always runs.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .models import ensure_dexined

log = logging.getLogger(__name__)

_NET_W, _NET_H = 640, 480
_MEAN_BGR = np.array([103.5, 116.2, 123.6], dtype=np.float32)


class DexiNed:
    def __init__(self, model_path: Path, threads: int = 0):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.log_severity_level = 3
        if threads:
            so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(
            str(model_path), so, providers=ort.get_available_providers()
        )
        self.input_name = self.sess.get_inputs()[0].name

    def _infer(self, bgr: np.ndarray) -> np.ndarray:
        """Run one 640x480 BGR frame, return edge probability in [0, 1]."""
        x = bgr.astype(np.float32) - _MEAN_BGR
        x = x.transpose(2, 0, 1)[None]
        outs = self.sess.run(None, {self.input_name: x})
        probs = [1.0 / (1.0 + np.exp(-o[0, 0])) for o in outs]
        # Average of all side outputs + fused output (as in opencv_zoo "ave"),
        # which is less brittle than the fused output alone.
        return np.mean(probs, axis=0).astype(np.float32)

    def global_pass(self, bgr: np.ndarray) -> np.ndarray:
        h, w = bgr.shape[:2]
        s = min(_NET_W / w, _NET_H / h)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
        small = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        top, left = (_NET_H - nh) // 2, (_NET_W - nw) // 2
        frame = cv2.copyMakeBorder(
            small, top, _NET_H - nh - top, left, _NET_W - nw - left,
            cv2.BORDER_REFLECT_101,
        )
        p = self._infer(frame)[top:top + nh, left:left + nw]
        return cv2.resize(p, (w, h), interpolation=cv2.INTER_CUBIC)

    def tiled_pass(self, bgr: np.ndarray, overlap: int = 96) -> np.ndarray:
        h, w = bgr.shape[:2]
        pad_h, pad_w = max(0, _NET_H - h), max(0, _NET_W - w)
        if pad_h or pad_w:
            bgr = cv2.copyMakeBorder(bgr, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT_101)
        H, W = bgr.shape[:2]
        acc = np.zeros((H, W), np.float32)
        wsum = np.zeros((H, W), np.float32)
        # Feathered blending window to hide tile seams.
        wy = np.minimum(np.arange(_NET_H) + 1, _NET_H - np.arange(_NET_H)).astype(np.float32)
        wx = np.minimum(np.arange(_NET_W) + 1, _NET_W - np.arange(_NET_W)).astype(np.float32)
        win = np.minimum(np.minimum.outer(wy, wx), overlap) / overlap + 1e-3
        ys = _starts(H, _NET_H, overlap)
        xs = _starts(W, _NET_W, overlap)
        for y in ys:
            for x in xs:
                p = self._infer(bgr[y:y + _NET_H, x:x + _NET_W])
                acc[y:y + _NET_H, x:x + _NET_W] += p * win
                wsum[y:y + _NET_H, x:x + _NET_W] += win
        return (acc / wsum)[:h, :w]


def _starts(total: int, size: int, overlap: int) -> list[int]:
    if total <= size:
        return [0]
    step = size - overlap
    n = int(np.ceil((total - size) / step)) + 1
    return [round(i * (total - size) / (n - 1)) for i in range(n)]


def classical_edges(lab: np.ndarray) -> np.ndarray:
    """Fallback: multi-scale Lab gradient magnitude, normalised to [0, 1]."""
    acc = np.zeros(lab.shape[:2], np.float32)
    for sigma in (1.5, 3.0, 6.0):
        blur = cv2.GaussianBlur(lab, (0, 0), sigma)
        g = np.zeros_like(acc)
        for c in range(3):
            gx = cv2.Sobel(blur[..., c], cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(blur[..., c], cv2.CV_32F, 0, 1, ksize=3)
            g += gx * gx + gy * gy
        acc += np.sqrt(g) * sigma
    acc /= np.percentile(acc, 99.5) + 1e-6
    return np.clip(acc, 0, 1)


@lru_cache(maxsize=2)
def _dexined(threads: int) -> DexiNed:
    return DexiNed(ensure_dexined(), threads=threads)


def edge_map(bgr: np.ndarray, lab: np.ndarray, backend: str = "auto",
             global_weight: float = 0.5, threads: int = 0,
             parts: dict | None = None) -> np.ndarray:
    """Return fused edge probability map in [0, 1] at the size of ``bgr``.

    If ``parts`` is given it receives the backend actually used and, for
    DexiNed, the raw ``global`` and ``tiled`` maps (for inspection).
    """
    parts = {} if parts is None else parts
    if backend in ("auto", "dexined"):
        try:
            net = _dexined(threads)
            g = net.global_pass(bgr)
            t = net.tiled_pass(bgr)
            parts.update({"backend": "dexined", "global": g, "tiled": t})
            # Geometric blend: fine localisation from the tiles, gated by the
            # coarse/semantic evidence of the global pass.
            g_blur = cv2.GaussianBlur(g, (0, 0), max(bgr.shape[:2]) / 400)
            g_n = g_blur / (g_blur.max() + 1e-6)
            fused = (t ** (1 - global_weight)) * (g_n ** global_weight)
            return np.clip(fused / (np.percentile(fused, 99.7) + 1e-6), 0, 1)
        except Exception as exc:  # pragma: no cover - depends on environment
            if backend == "dexined":
                raise
            log.warning("DexiNed unavailable (%s); using classical edges", exc)
            parts["fallback_reason"] = str(exc)
    parts["backend"] = "classical"
    return classical_edges(lab)
