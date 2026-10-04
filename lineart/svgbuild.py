"""Stage 4b: assemble the final SVG documents from traced paths.

Both output files share one coordinate system (the working raster size) and a
``viewBox``, so they scale identically and print cleanly:

  ``*.lines.svg``  – white background + ``<g id="line-art">`` (black fills)
  ``*.color.svg``  – white background + ``<g id="color-fills">`` (flat colors)
                     + ``<g id="line-art">`` on top
"""

from __future__ import annotations

from .vectorize import y_flip_group


def _svg_header(w: int, h: int, title: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMid meet">\n'
        f'  <title>{title}</title>\n'
    )


def _bg_rect(w: int, h: int) -> str:
    return f'  <rect id="background" width="{w}" height="{h}" fill="#ffffff"/>\n'


def _paths_group(gid: str, h: int, paths: list[tuple[str, str]]) -> str:
    """paths: list of (d, fill_attr). Wrapped in the potrace Y-flip group."""
    if not paths:
        return f'  <g id="{gid}"/>\n'
    body = "\n".join(
        f'    <path {fill} d="{d}"/>'
        for d, fill in paths
    )
    return (
        f'  <g id="{gid}" {y_flip_group(h)} stroke="none">\n'
        f"{body}\n"
        f"  </g>\n"
    )


def build_lines_svg(w: int, h: int, line_paths: list[str], title: str) -> str:
    paths = [(d, 'fill="#000000"') for d in line_paths]
    return (
        _svg_header(w, h, title)
        + _bg_rect(w, h)
        + _paths_group("line-art", h, paths)
        + "</svg>\n"
    )


def build_color_svg(w: int, h: int,
                    fill_paths: list[tuple[str, str]],
                    line_paths: list[str], title: str) -> str:
    """fill_paths: (d, '#rrggbb') tuples, bottom-up order."""
    filled = [(d, f'fill="{color}"') for d, color in fill_paths]
    lines = [(d, 'fill="#000000"') for d in line_paths]
    return (
        _svg_header(w, h, title)
        + _bg_rect(w, h)
        + _paths_group("color-fills", h, filled)
        + _paths_group("line-art", h, lines)
        + "</svg>\n"
    )
