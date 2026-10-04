"""Model weight management (download + checksum verification)."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

WEIGHTS_DIR = Path(os.environ.get(
    "LINEART_WEIGHTS", Path(__file__).resolve().parent.parent / "weights"))

DEXINED = {
    "file": "dexined_2024sep.onnx",
    # opencv_zoo export of DexiNed (MIT license), stored via Git LFS.
    "urls": [
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
        "models/edge_detection_dexined/edge_detection_dexined_2024sep.onnx",
        "https://huggingface.co/opencv/edge_detection_dexined/resolve/main/"
        "edge_detection_dexined_2024sep.onnx",
    ],
    "sha256": "a50d01dc8481549c7dedb9eb3e0123b810a016520df75e4669a504609982cdd0",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure_dexined() -> Path:
    """Return path to a float32 DexiNed ONNX model, downloading if needed.

    The opencv_zoo export uses block-wise int8 DequantizeLinear nodes that
    onnxruntime rejects, so the weights are folded to fp32 once and cached.
    """
    fp32 = WEIGHTS_DIR / DEXINED["file"].replace(".onnx", "_fp32.onnx")
    if fp32.exists():
        return fp32
    src = _download_dexined()
    log.info("Folding quantized weights to fp32 -> %s", fp32)
    _dequantize_onnx(src, fp32)
    return fp32


def _dequantize_onnx(src: Path, dst: Path) -> None:
    import numpy as np
    import onnx
    from onnx import helper, numpy_helper

    m = onnx.load(str(src))
    g = m.graph
    init = {i.name: numpy_helper.to_array(i) for i in g.initializer}
    folded: dict[str, "np.ndarray"] = {}
    keep = []
    for n in g.node:
        if n.op_type == "DequantizeLinear" and all(i in init for i in n.input):
            a = {x.name: helper.get_attribute_value(x) for x in n.attribute}
            q = init[n.input[0]].astype(np.float32)
            s = init[n.input[1]].astype(np.float32)
            z = init[n.input[2]].astype(np.float32) if len(n.input) > 2 else np.zeros_like(s)
            ax, bs = a.get("axis", 1), a.get("block_size", 0)
            if s.size == 1:
                y = (q - z.reshape(())) * s.reshape(())
            elif bs and s.ndim == q.ndim:
                idx = range(q.shape[ax])
                s = np.repeat(s, bs, axis=ax).take(idx, axis=ax)
                z = np.repeat(z, bs, axis=ax).take(idx, axis=ax)
                y = (q - z) * s
            else:
                shp = [1] * q.ndim
                shp[ax] = -1
                y = (q - z.reshape(shp)) * s.reshape(shp)
            folded[n.output[0]] = y
        elif n.op_type == "Reshape" and n.input[0] in folded and n.input[1] in init:
            folded[n.output[0]] = folded.pop(n.input[0]).reshape(init[n.input[1]])
        else:
            keep.append(n)
    used = {i for n in keep for i in n.input}
    new_init = [i for i in g.initializer if i.name in used]
    new_init += [numpy_helper.from_array(v.astype(np.float32), k)
                 for k, v in folded.items() if k in used]
    del g.node[:]
    g.node.extend(keep)
    del g.initializer[:]
    g.initializer.extend(new_init)
    tmp = dst.with_suffix(".part")
    onnx.save(m, str(tmp))
    tmp.replace(dst)


def _download_dexined() -> Path:
    path = WEIGHTS_DIR / DEXINED["file"]
    if path.exists() and _sha256(path) == DEXINED["sha256"]:
        return path
    WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    last_err: Exception | None = None
    for url in DEXINED["urls"]:
        try:
            log.info("Downloading DexiNed weights from %s", url)
            with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            if _sha256(tmp) != DEXINED["sha256"]:
                raise ValueError("checksum mismatch")
            tmp.replace(path)
            return path
        except Exception as exc:  # try next mirror
            last_err = exc
            log.warning("Download failed from %s: %s", url, exc)
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"Could not obtain DexiNed weights: {last_err}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(ensure_dexined())
