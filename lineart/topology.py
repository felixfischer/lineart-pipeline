"""Stage 4a: planar boundary graph of a label map.

Region borders are traced on the *crack grid* (pixel corners), which gives
an exact planar graph:

* vertices  = pixel corners where >= 3 borders meet (junctions) plus the
              four image corners,
* chains    = maximal border runs between two junctions (or closed loops),
              each separating exactly two labels (``left`` / ``right``).

Every region outline is then a cycle of chains. Because fills and lines are
generated from the very same (smoothed) chains, they are congruent by
construction – no gaps, no offsets.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

OUTSIDE = -1

_DX = (1, 0, -1, 0)   # 0 = right, 1 = down, 2 = left, 3 = up
_DY = (0, 1, 0, -1)


@dataclass
class Chain:
    points: np.ndarray        # (n, 2) float, image coordinates of corners
    left: int                 # label on the left when walking start -> end
    right: int
    closed: bool
    start: int                # vertex id (== end for closed loops)
    end: int
    strength: float = 0.0     # filled later (line hierarchy)
    edge: float = 0.0         # mean edge probability along the chain
    beziers: list = field(default_factory=list)

    @property
    def on_frame(self) -> bool:
        return self.left == OUTSIDE or self.right == OUTSIDE


@dataclass
class Graph:
    chains: list[Chain]
    # region id -> list of rings; ring = list of (chain index, reversed?)
    rings: dict[int, list[list[tuple[int, bool]]]]
    width: int
    height: int


def build_graph(labels: np.ndarray) -> Graph:
    H, W = labels.shape
    P = np.pad(labels, 1, constant_values=OUTSIDE)
    Hp, Wp = P.shape
    # Horizontal crack at vertex row y between pixel rows y-1 and y,
    # spanning vertex x -> x+1. Vertical crack similarly.
    hE = np.zeros((Hp + 1, Wp), bool)
    hE[1:Hp, :] = P[:-1, :] != P[1:, :]
    vE = np.zeros((Hp, Wp + 1), bool)
    vE[:, 1:Wp] = P[:, :-1] != P[:, 1:]

    deg = np.zeros((Hp + 1, Wp + 1), np.int8)
    deg[:, :-1] += hE
    deg[:, 1:] += hE
    deg[:-1, :] += vE
    deg[1:, :] += vE
    node = deg >= 3
    for y, x in ((1, 1), (1, Wp - 1), (Hp - 1, 1), (Hp - 1, Wp - 1)):
        node[y, x] = True

    hE_l, vE_l, node_l, P_l = hE.tolist(), vE.tolist(), node.tolist(), P.tolist()
    hV = np.zeros_like(hE).tolist()
    vV = np.zeros_like(vE).tolist()
    VW = Wp + 1

    def has(x, y, d):
        if d == 0:
            return hE_l[y][x] if x < Wp else False
        if d == 2:
            return hE_l[y][x - 1] if x > 0 else False
        if d == 1:
            return vE_l[y][x] if y < Hp else False
        return vE_l[y - 1][x] if y > 0 else False

    def seen(x, y, d):
        if d == 0:
            return hV[y][x]
        if d == 2:
            return hV[y][x - 1]
        if d == 1:
            return vV[y][x]
        return vV[y - 1][x]

    def mark(x, y, d):
        if d == 0:
            hV[y][x] = True
        elif d == 2:
            hV[y][x - 1] = True
        elif d == 1:
            vV[y][x] = True
        else:
            vV[y - 1][x] = True

    def sides(x, y, d):
        if d == 0:
            return P_l[y - 1][x], P_l[y][x]
        if d == 2:
            return P_l[y][x - 1], P_l[y - 1][x - 1]
        if d == 1:
            return P_l[y][x], P_l[y][x - 1]
        return P_l[y - 1][x - 1], P_l[y - 1][x]

    def walk(x0, y0, d0):
        left, right = sides(x0, y0, d0)
        pts = [(x0, y0)]
        x, y, d = x0, y0, d0
        while True:
            mark(x, y, d)
            x += _DX[d]
            y += _DY[d]
            pts.append((x, y))
            if node_l[y][x] or (x == x0 and y == y0):
                break
            back = (d + 2) & 3
            for nd in (d, (d + 1) & 3, (d + 3) & 3):
                if nd != back and has(x, y, nd) and not seen(x, y, nd):
                    d = nd
                    break
            else:  # dead end cannot happen in a valid crack graph
                break
        closed = (x, y) == (x0, y0)
        arr = np.asarray(pts, np.float64) - 1.0   # back to image coordinates
        return Chain(arr, left, right, closed, y0 * VW + x0, y * VW + x)

    chains: list[Chain] = []
    ys, xs = np.nonzero(node)
    for y, x in zip(ys.tolist(), xs.tolist()):
        for d in range(4):
            if has(x, y, d) and not seen(x, y, d):
                chains.append(walk(x, y, d))
    # Remaining unvisited cracks form closed loops without junctions.
    for arr, dirs in ((hE, (0,)), (vE, (1,))):
        ys, xs = np.nonzero(arr)
        for y, x in zip(ys.tolist(), xs.tolist()):
            for d in dirs:
                if not seen(x, y, d):
                    chains.append(walk(x, y, d))

    return Graph(chains, _assemble_rings(chains), W, H)


def _assemble_rings(chains: list[Chain]) -> dict[int, list[list[tuple[int, bool]]]]:
    """Link chains into closed outlines per region (region on the left)."""
    out_edges: dict[int, dict[int, tuple[int, bool, int]]] = {}
    for i, c in enumerate(chains):
        if c.left != OUTSIDE:
            out_edges.setdefault(c.left, {})[c.start] = (i, False, c.end)
        if c.right != OUTSIDE:
            out_edges.setdefault(c.right, {})[c.end] = (i, True, c.start)
    rings: dict[int, list[list[tuple[int, bool]]]] = {}
    for region, edges in out_edges.items():
        todo = dict(edges)
        region_rings = []
        while todo:
            start, (ci, rev, nxt) = todo.popitem()
            ring = [(ci, rev)]
            guard = 0
            while nxt != start and nxt in todo and guard < 1_000_000:
                ci, rev, nxt2 = todo.pop(nxt)
                ring.append((ci, rev))
                nxt = nxt2
                guard += 1
            region_rings.append(ring)
        rings[region] = region_rings
    return rings
