"""Stage 4b: chain smoothing, Bézier fitting and SVG generation."""
from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

import cv2
import numpy as np

from .topology import Chain, Graph


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def _gauss_kernel(sigma: float) -> np.ndarray:
    r = max(1, int(round(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    return k / k.sum()


def smooth_chain(pts: np.ndarray, closed: bool, sigma: float) -> np.ndarray:
    """Gaussian smoothing along the chain; open chains keep their endpoints.

    Crack-grid staircases become smooth curves while junction positions stay
    fixed, so neighbouring chains still meet exactly.
    """
    n = len(pts)
    if sigma <= 0 or n < 4:
        return pts.copy()
    k = _gauss_kernel(sigma)
    r = len(k) // 2
    if closed:
        body = pts[:-1]
        m = len(body)
        if m < 3:
            return pts.copy()
        idx = np.arange(-r, m + r) % m
        ext = body[idx]
        sm = np.stack([np.convolve(ext[:, i], k, "valid") for i in (0, 1)], 1)
        return np.vstack([sm, sm[:1]])
    # Point reflection about the endpoints keeps start/end and tangents.
    r = min(r, n - 1)
    k = _gauss_kernel(min(sigma, r / 3)) if r < len(k) // 2 else k
    r = len(k) // 2
    head = 2 * pts[0] - pts[1:r + 1][::-1]
    tail = 2 * pts[-1] - pts[-r - 1:-1][::-1]
    ext = np.vstack([head, pts, tail])
    sm = np.stack([np.convolve(ext[:, i], k, "valid") for i in (0, 1)], 1)
    sm[0], sm[-1] = pts[0], pts[-1]
    return sm


def simplify(pts: np.ndarray, closed: bool, eps: float) -> np.ndarray:
    if len(pts) <= 2 or eps <= 0:
        return pts
    if closed:
        body = pts[:-1].astype(np.float32).reshape(-1, 1, 2)
        out = cv2.approxPolyDP(body, eps, True).reshape(-1, 2).astype(np.float64)
        if len(out) < 3:
            return pts
        return np.vstack([out, out[:1]])
    out = cv2.approxPolyDP(pts.astype(np.float32).reshape(-1, 1, 2), eps, False)
    out = out.reshape(-1, 2).astype(np.float64)
    out[0], out[-1] = pts[0], pts[-1]
    return out


def catmull_rom(pts: np.ndarray, closed: bool, tension: float = 1.0) -> list[tuple]:
    """Convert a polyline to C1 cubic Bézier segments through its points."""
    n = len(pts)
    if n < 2:
        return []
    if n == 2:
        a, b = pts
        return [(a, a + (b - a) / 3, a + 2 * (b - a) / 3, b)]
    segs = []
    if closed:
        body = pts[:-1]
        m = len(body)
        for i in range(m):
            p0, p1, p2, p3 = body[(i - 1) % m], body[i], body[(i + 1) % m], body[(i + 2) % m]
            segs.append((p1, p1 + tension * (p2 - p0) / 6, p2 - tension * (p3 - p1) / 6, p2))
        return segs
    for i in range(n - 1):
        p1, p2 = pts[i], pts[i + 1]
        p0 = pts[i - 1] if i > 0 else p1
        p3 = pts[i + 2] if i + 2 < n else p2
        segs.append((p1, p1 + tension * (p2 - p0) / 6, p2 - tension * (p3 - p1) / 6, p2))
    return segs


def fit_chains(graph: Graph, sigma: float, eps: float) -> None:
    for c in graph.chains:
        if c.on_frame:
            # Frame borders are straight; just keep their corners.
            pts = simplify(c.points, c.closed, 0.01)
            c.beziers = [(a, a, b, b) for a, b in zip(pts[:-1], pts[1:])]
            continue
        # Borders without edge evidence (colour gradients, flat-area watershed
        # lines) have no precise location and are smoothed harder.
        weak = 1.0 - min(1.0, c.edge / 0.35)
        sm = smooth_chain(c.points, c.closed, sigma * (1.0 + 1.5 * weak))
        c.beziers = catmull_rom(simplify(sm, c.closed, eps), c.closed)


def chain_strength(graph: Graph, edges: np.ndarray, colors: dict[int, np.ndarray],
                   color_scale: float = 40.0) -> None:
    """Score how 'important' each border is (edge evidence + colour contrast)."""
    H, W = edges.shape
    for c in graph.chains:
        if c.on_frame:
            c.strength = 1.0
            continue
        p = np.clip(np.round(c.points).astype(int), 0, [W - 1, H - 1])
        e = float(edges[p[:, 1], p[:, 0]].mean())
        c.edge = e
        de = float(np.linalg.norm(colors[c.left] - colors[c.right])) / color_scale
        c.strength = 0.6 * e + 0.4 * min(1.0, de)


# --------------------------------------------------------------------------
# SVG output
# --------------------------------------------------------------------------

def _f(v: float) -> str:
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _pt(p) -> str:
    return f"{_f(p[0])} {_f(p[1])}"


def _segs_d(segs, reverse: bool, move: bool) -> str:
    if reverse:
        segs = [(d, c, b, a) for a, b, c, d in reversed(segs)]
    out = [f"M{_pt(segs[0][0])}"] if move else []
    for a, b, c, d in segs:
        if np.allclose(a, b) and np.allclose(c, d):
            out.append(f"L{_pt(d)}")
        else:
            out.append(f"C{_pt(b)} {_pt(c)} {_pt(d)}")
    return "".join(out)


def region_path(graph: Graph, region: int) -> str:
    parts = []
    for ring in graph.rings.get(region, []):
        first = True
        for ci, rev in ring:
            segs = graph.chains[ci].beziers
            if not segs:
                continue
            parts.append(_segs_d(segs, rev, first))
            first = False
        if not first:
            parts.append("Z")
    return "".join(parts)


@dataclass
class LineStyle:
    major_width: float = 3.2
    minor_width: float = 1.8
    frame_width: float = 4.0
    major_threshold: float = 0.42
    color: str = "#000000"
    major_color: str | None = None   # per-level colours (GUI inspection view)
    minor_color: str | None = None


def _line_groups(graph: Graph, style: LineStyle, indent: str = "  ") -> list[str]:
    major, minor = [], []
    for c in graph.chains:
        if c.on_frame or not c.beziers:
            continue
        (major if c.strength >= style.major_threshold else minor).append(
            _segs_d(c.beziers, False, True))
    W, H = graph.width, graph.height
    hw = style.frame_width / 2

    def stroke(c):
        return f' stroke="{c}"' if c else ""

    out = [
        f'{indent}<g id="line-art" fill="none" stroke="{style.color}" '
        'stroke-linecap="round" stroke-linejoin="round">',
        f'{indent}  <path id="lines-minor"{stroke(style.minor_color)} '
        f'stroke-width="{_f(style.minor_width)}" '
        f'd="{"".join(minor)}"/>',
        f'{indent}  <path id="lines-major"{stroke(style.major_color)} '
        f'stroke-width="{_f(style.major_width)}" '
        f'd="{"".join(major)}"/>',
        f'{indent}  <rect id="frame" x="{_f(hw)}" y="{_f(hw)}" width="{_f(W - 2 * hw)}" '
        f'height="{_f(H - 2 * hw)}" stroke-width="{_f(style.frame_width)}"/>',
        f"{indent}</g>",
    ]
    return out


def _header(W: int, H: int, title: str) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        f"  <title>{escape(title)}</title>",
    ]


def lines_svg(graph: Graph, style: LineStyle, title: str) -> str:
    W, H = graph.width, graph.height
    out = _header(W, H, title)
    out.append(f'  <rect id="background" width="{W}" height="{H}" fill="#ffffff"/>')
    out += _line_groups(graph, style)
    out.append("</svg>")
    return "\n".join(out) + "\n"


def color_svg(graph: Graph, style: LineStyle, colors: dict[int, str],
              palette_index: dict[int, int], title: str, background: str) -> str:
    W, H = graph.width, graph.height
    out = _header(W, H, title)
    out.append(f'  <rect id="background" width="{W}" height="{H}" fill="{background}"/>')
    out.append('  <g id="color-fills" stroke="none">')
    for region in sorted(graph.rings):
        d = region_path(graph, region)
        if d:
            out.append(f'    <path id="r{region}" data-color="{palette_index[region]}" '
                       f'fill="{colors[region]}" d="{d}"/>')
    out.append("  </g>")
    out += _line_groups(graph, style)
    out.append("</svg>")
    return "\n".join(out) + "\n"
