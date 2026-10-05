"""End-to-end test on a synthetic image (classical edge backend, fast)."""
from pathlib import Path

import numpy as np

from lineart import segment as seg
from lineart import topology as topo
from lineart.pipeline import make_config, process_image
from lineart.verify import check_svg


def test_end_to_end(synthetic: Path, tmp_path: Path):
    cfg = make_config("medium", edge_backend="classical", work_size=400,
                      target_regions=12, colors=5)
    res = process_image(synthetic, tmp_path / "out", cfg)
    assert set(res.files) == {"lines", "color"}
    for f in res.files.values():
        chk = check_svg(f)
        assert chk["ok"], chk["errors"]
    # circle, rectangle, ellipse and background must survive as regions
    assert 4 <= res.stats["regions"] <= 12


def test_rings_close_and_cover_all_regions():
    labels = np.zeros((40, 50), np.int32)
    labels[5:20, 5:25] = 1
    labels[10:30, 20:45] = 2
    labels[22:28, 8:14] = 3          # island inside nothing -> touches 0 only
    labels = seg.split_components(seg.remove_diagonal_contacts(labels))
    g = topo.build_graph(labels)
    assert set(g.rings) == set(np.unique(labels).tolist())
    for region, rings in g.rings.items():
        for ring in rings:
            ends = []
            for ci, rev in ring:
                c = g.chains[ci]
                a, b = (c.end, c.start) if rev else (c.start, c.end)
                ends.append((a, b))
            # consecutive chains connect and the ring closes
            for (a0, b0), (a1, b1) in zip(ends, ends[1:] + ends[:1]):
                assert b0 == a1
