"""Pipeline configuration: detail presets and CLI overrides."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Config:
    """All tunables of one pipeline run.

    Units:
      max_side     – longest edge of the working raster (px)
      sigma        – smoothing sigma of the Gaussian before segmentation (px)
      n_clusters   – K-means cluster count of the flat-color segmentation
      absorb_width – thin intermediate regions narrower than this (px) are
                     absorbed into a neighbor (dissolves gradient halos)
      de_min       – optional line thinning: borders with color difference
                     below this are not drawn as lines (0 = draw all borders)
      de_merge     – adjacent segments with delta-E below this are merged
                     into a single region
      soft_grad    – boundaries whose smoothed-field gradient stays below
                     this are considered "soft transitions" (banding, not
                     real contours)
      soft_de      – max delta-E for merging two regions across a soft
                     boundary
      line_width   – half-width of the line mask in px (mask is dilated by this)
      turd         – potrace speckle suppression in px^2
      min_region   – smallest region area in px^2 (smaller -> merged into
                     neighbor)
    """

    max_side: int = 1400
    sigma: float = 8.0
    n_clusters: int = 28
    absorb_width: float = 8.0
    de_min: float = 2.0
    de_merge: float = 3.0
    soft_grad: float = 4.0
    soft_de: float = 8.0
    line_width: float = 3.5
    turd: int = 8
    min_region: int = 150
    seed: int = 42
    # rendering / output
    preview_width: int = 1400

    def with_overrides(self, **overrides: Any) -> "Config":
        return replace(self, **overrides)

    def describe(self) -> str:
        return (f"sigma={self.sigma:g} k={self.n_clusters} "
                f"absorb<{self.absorb_width:g}px dE_min={self.de_min:g} "
                f"dE_merge={self.de_merge:g} line={self.line_width:g}px "
                f"work<={self.max_side}px")


def preset(name: str) -> Config:
    """Named detail presets.

    low    – few, bold contours; poster-like flat colors (fast)
    medium – balanced contour density (default)
    high   – rich detail incl. stars / foam tendrils; thinner lines
    """
    if name == "low":
        return Config(
            max_side=1200,
            sigma=12.0,
            n_clusters=12,
            absorb_width=16.0,
            de_min=4.0,
            de_merge=3.5,
            soft_grad=5.0,
            soft_de=9.0,
            line_width=4.5,
            turd=12,
            min_region=600,
        )
    if name == "medium":
        return Config(
            max_side=1400,
            sigma=8.0,
            n_clusters=18,
            absorb_width=16.0,
            de_min=2.0,
            de_merge=3.0,
            soft_grad=4.0,
            soft_de=8.0,
            line_width=3.5,
            turd=8,
            min_region=400,
        )
    if name == "high":
        return Config(
            max_side=1600,
            sigma=5.5,
            n_clusters=24,
            absorb_width=10.0,
            de_min=0.0,
            de_merge=2.5,
            soft_grad=3.5,
            soft_de=7.0,
            line_width=2.5,
            turd=5,
            min_region=300,
        )
    raise ValueError(f"unknown detail level {name!r} (use low|medium|high)")
