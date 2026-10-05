from pathlib import Path

import cv2
import numpy as np
import pytest


@pytest.fixture()
def synthetic(tmp_path: Path) -> Path:
    img = np.full((300, 400, 3), (200, 220, 235), np.uint8)
    cv2.circle(img, (120, 150), 70, (40, 80, 200), -1)
    cv2.rectangle(img, (220, 60), (360, 240), (60, 160, 60), -1)
    cv2.ellipse(img, (290, 150), (40, 25), 0, 0, 360, (20, 200, 240), -1)
    rng = np.random.default_rng(0)
    img = np.clip(img + rng.normal(0, 6, img.shape), 0, 255).astype(np.uint8)
    p = tmp_path / "shapes.png"
    cv2.imwrite(str(p), img)
    return p
