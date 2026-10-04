"""Stage 2b/3: region segmentation that *is* the line art.

The key design decision of this pipeline: lines are not traced from an edge
image. Instead the image is partitioned into regions and the lines are the
borders of that partition. This guarantees closed, bucket-fillable areas and
makes colour fills and lines congruent by construction.

1. Over-segmentation: marker-based watershed on the fused edge map. Basin
   borders sit on edge *ridges*, i.e. on the centre of a painted outline,
   which avoids the classic double-contour of gradient-based methods.
2. Region adjacency graph merging (greedy, priority queue): neighbours are
   merged when their shared border is weak (low mean edge probability) and
   their colours are similar.
3. Clean-up: tiny regions and thin slivers are absorbed by their best
   neighbour; diagonal-only pixel contacts are removed so every region is a
   simple 4-connected area (needed by the topology stage).
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

import cv2
import numpy as np
from scipy import ndimage as ndi
from skimage.feature import peak_local_max
from skimage.segmentation import watershed


# --------------------------------------------------------------------------
# Over-segmentation
# --------------------------------------------------------------------------

def oversegment(edges: np.ndarray, lab: np.ndarray, min_distance: int = 4,
                color_weight: float = 0.35) -> np.ndarray:
    """Watershed on edge probability blended with Lab colour gradient."""
    grad = np.zeros(edges.shape, np.float32)
    for c in range(3):
        gx = cv2.Sobel(lab[..., c], cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(lab[..., c], cv2.CV_32F, 0, 1, ksize=3)
        grad += gx * gx + gy * gy
    grad = np.sqrt(grad)
    grad /= np.percentile(grad, 99.5) + 1e-6
    relief = (1 - color_weight) * edges + color_weight * np.clip(grad, 0, 1)
    relief = cv2.GaussianBlur(relief, (0, 0), 1.0)
    peaks = peak_local_max(-relief, min_distance=min_distance,
                           exclude_border=False)
    markers = np.zeros(edges.shape, np.int32)
    markers[peaks[:, 0], peaks[:, 1]] = np.arange(1, len(peaks) + 1)
    return watershed(relief, markers).astype(np.int32) - 1


# --------------------------------------------------------------------------
# Region adjacency graph
# --------------------------------------------------------------------------

def _boundary_stats(labels: np.ndarray, edges: np.ndarray):
    """Per adjacent label pair: shared border length and summed edge value."""
    n = int(labels.max()) + 1
    keys, vals = [], []
    for a, b, ea, eb in (
        (labels[:, :-1], labels[:, 1:], edges[:, :-1], edges[:, 1:]),
        (labels[:-1, :], labels[1:, :], edges[:-1, :], edges[1:, :]),
    ):
        m = a != b
        lo = np.minimum(a[m], b[m]).astype(np.int64)
        hi = np.maximum(a[m], b[m]).astype(np.int64)
        keys.append(lo * n + hi)
        vals.append(np.maximum(ea[m], eb[m]))
    keys = np.concatenate(keys)
    vals = np.concatenate(vals)
    uniq, inv = np.unique(keys, return_inverse=True)
    length = np.bincount(inv)
    esum = np.bincount(inv, weights=vals)
    return uniq // n, uniq % n, length, esum


def _border_length(labels: np.ndarray) -> np.ndarray:
    n = int(labels.max()) + 1
    border = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    return np.bincount(border, minlength=n)


@dataclass
class MergeParams:
    threshold: float = 2.0      # hard stop for the merge cost (safety net)
    edge_weight: float = 0.85   # share of boundary strength in the cost
    color_scale: float = 22.0   # Lab ΔE that counts as "fully different"
    min_area: int = 120         # px; smaller regions are always absorbed
    min_width: float = 3.0      # px; mean width (2A/P) of slivers to absorb
    target_regions: int = 0     # stop once this many regions remain (0 = off)
    size_power: float = 0.25    # >0 favours merging small regions first


class RegionGraph:
    def __init__(self, labels: np.ndarray, edges: np.ndarray, lab: np.ndarray):
        self.labels = labels
        n = int(labels.max()) + 1
        flat = labels.ravel()
        self.area = np.bincount(flat, minlength=n).astype(np.float64)
        self.csum = np.stack([np.bincount(flat, lab[..., c].ravel(), n)
                              for c in range(3)], 1)
        self.blen = _border_length(labels).astype(np.float64)
        self.parent = np.arange(n)
        self.ref_area = 0.0
        self.alive = np.ones(n, bool)
        self.ver = np.zeros(n, np.int64)
        self.adj: list[dict[int, list[float]]] = [dict() for _ in range(n)]
        a, b, length, esum = _boundary_stats(labels, edges)
        for i, j, l, s in zip(a.tolist(), b.tolist(), length.tolist(), esum.tolist()):
            self.adj[i][j] = [l, s]
            self.adj[j][i] = [l, s]

    # -- helpers ----------------------------------------------------------
    def mean(self, i: int) -> np.ndarray:
        return self.csum[i] / self.area[i]

    def perimeter(self, i: int) -> float:
        return self.blen[i] + sum(v[0] for v in self.adj[i].values())

    def cost(self, i: int, j: int, p: MergeParams) -> float:
        l, s = self.adj[i][j]
        bmean = s / l
        de = float(np.linalg.norm(self.mean(i) - self.mean(j)))
        c = p.edge_weight * bmean + (1 - p.edge_weight) * min(1.0, de / p.color_scale)
        # Short shared borders between big regions are weak evidence for
        # merging (two areas merely touching at a corner).
        small = min(self.area[i], self.area[j])
        contact = l / max(1.0, np.sqrt(small))
        c *= 1.0 + 0.25 * max(0.0, 1.0 - contact)
        if p.size_power and self.ref_area:
            c *= float(np.clip(small / self.ref_area, 0.05, 20.0)) ** p.size_power
        return c

    def merge(self, i: int, j: int) -> int:
        """Merge j into i; return i."""
        self.area[i] += self.area[j]
        self.csum[i] += self.csum[j]
        self.blen[i] += self.blen[j]
        for k, (l, s) in self.adj[j].items():
            del self.adj[k][j]
            if k == i:
                continue
            if k in self.adj[i]:
                e = self.adj[i][k]
                e[0] += l
                e[1] += s
            else:
                self.adj[i][k] = [l, s]
                self.adj[k][i] = self.adj[i][k]
        self.adj[j] = {}
        self.alive[j] = False
        self.parent[j] = i
        self.ver[i] += 1
        self.ver[j] += 1
        return i

    # -- merging passes -----------------------------------------------------
    def merge_greedy(self, p: MergeParams) -> None:
        count = int(self.alive.sum())
        if p.target_regions:
            self.ref_area = float(self.area[self.alive].sum()) / p.target_regions
        heap = []
        for i in np.flatnonzero(self.alive).tolist():
            for j in self.adj[i]:
                if i < j:
                    heap.append((self.cost(i, j, p), i, j, 0, 0))
        heapq.heapify(heap)
        while heap:
            c, i, j, vi, vj = heapq.heappop(heap)
            if c > p.threshold or (p.target_regions and count <= p.target_regions):
                break
            if not (self.alive[i] and self.alive[j]) or vi != self.ver[i] or vj != self.ver[j]:
                continue
            if self.area[j] > self.area[i]:
                i, j = j, i
            r = self.merge(i, j)
            count -= 1
            for k in self.adj[r]:
                a, b = (r, k) if r < k else (k, r)
                heapq.heappush(heap, (self.cost(a, b, p), a, b,
                                      int(self.ver[a]), int(self.ver[b])))

    def absorb_small(self, p: MergeParams) -> None:
        """Absorb regions that are too small or too thin to colour in."""
        def bad(i: int) -> bool:
            if self.area[i] < p.min_area:
                return True
            return 2 * self.area[i] / max(1.0, self.perimeter(i)) < p.min_width

        heap = [(self.area[i], i, int(self.ver[i]))
                for i in np.flatnonzero(self.alive).tolist()]
        heapq.heapify(heap)
        while heap:
            _, i, v = heapq.heappop(heap)
            if not self.alive[i] or v != self.ver[i] or not self.adj[i] or not bad(i):
                continue
            # Prefer the neighbour with the weakest border / closest colour,
            # weighted by how much border is shared.
            best = min(self.adj[i], key=lambda k: self.cost(i, k, p) / np.sqrt(self.adj[i][k][0]))
            r = self.merge(best, i)
            heapq.heappush(heap, (self.area[r], r, int(self.ver[r])))

    def merge_where(self, same: np.ndarray, p: MergeParams, max_edge: float) -> None:
        """Merge neighbours flagged equal in ``same`` (by root id) whose border
        is weaker than ``max_edge``. Used after palette quantisation."""
        changed = True
        while changed:
            changed = False
            for i in np.flatnonzero(self.alive).tolist():
                if not self.alive[i]:
                    continue
                for k in list(self.adj[i]):
                    l, s = self.adj[i][k]
                    if same[i] == same[k] and s / l < max_edge:
                        a, b = (i, k) if self.area[i] >= self.area[k] else (k, i)
                        self.merge(a, b)
                        changed = True
                        break

    def relabel(self) -> np.ndarray:
        root = self.parent.copy()
        while True:
            nxt = root[root]
            if np.array_equal(nxt, root):
                break
            root = nxt
        alive = np.flatnonzero(self.alive)
        remap = np.full(len(root), -1, np.int64)
        remap[alive] = np.arange(len(alive))
        return remap[root][self.labels].astype(np.int32)


# --------------------------------------------------------------------------
# Topological clean-up
# --------------------------------------------------------------------------

def remove_diagonal_contacts(labels: np.ndarray, max_iter: int = 20) -> np.ndarray:
    """Eliminate 2x2 blocks where one label touches itself only diagonally.

    Such pinch points make a region's outline non-manifold. The fix flips
    one of the two off-diagonal pixels to the diagonal label.
    """
    lab = labels.copy()
    for _ in range(max_iter):
        a, b = lab[:-1, :-1], lab[:-1, 1:]
        c, d = lab[1:, :-1], lab[1:, 1:]
        m1 = (a == d) & (b != a) & (c != a)       # main diagonal pinch
        m2 = (b == c) & (a != b) & (d != b)       # anti diagonal pinch
        if not (m1.any() or m2.any()):
            break
        ys, xs = np.nonzero(m1)
        lab[ys, xs + 1] = lab[ys, xs]             # b := a
        ys, xs = np.nonzero(m2 & ~m1)
        lab[ys, xs] = lab[ys, xs + 1]             # a := b
    return lab


def smooth_labels(labels: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian label voting: each pixel takes the label with the highest
    blurred indicator. Removes brush-stroke jaggies along borders at the
    scale ``sigma`` while keeping the partition (and its junctions) intact.
    """
    if sigma <= 0:
        return labels
    H, W = labels.shape
    pad = int(3 * sigma) + 1
    best = np.full((H, W), -1.0, np.float32)
    out = labels.copy()
    for idx, sl in enumerate(ndi.find_objects(labels + 1)):
        if sl is None:
            continue
        y0, y1 = max(0, sl[0].start - pad), min(H, sl[0].stop + pad)
        x0, x1 = max(0, sl[1].start - pad), min(W, sl[1].stop + pad)
        m = (labels[y0:y1, x0:x1] == idx).astype(np.float32)
        v = cv2.GaussianBlur(m, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
        b = best[y0:y1, x0:x1]
        upd = v > b
        b[upd] = v[upd]
        out[y0:y1, x0:x1][upd] = idx
    return out


def split_components(labels: np.ndarray) -> np.ndarray:
    """Give every 4-connected component its own label."""
    out = np.zeros_like(labels)
    nxt = 0
    objs = ndi.find_objects(labels + 1)
    for idx, sl in enumerate(objs):
        if sl is None:
            continue
        mask = labels[sl] == idx
        comp, n = ndi.label(mask)
        sub = out[sl]
        sub[mask] = comp[mask] + nxt - 1
        nxt += n
    return out


def segment(edges: np.ndarray, lab_smooth: np.ndarray, lab_color: np.ndarray,
            p: MergeParams, min_distance: int = 4) -> tuple[np.ndarray, RegionGraph]:
    labels = oversegment(edges, lab_smooth, min_distance=min_distance)
    g = RegionGraph(labels, edges, lab_color)
    g.merge_greedy(p)
    g.absorb_small(p)
    return g.relabel(), g
