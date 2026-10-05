"""Interactive pipeline session: per-stage result cache and stage views.

Every stage result is cached under a key that hashes the image and all
parameters of this and all upstream stages. Changing a parameter therefore
only recomputes the stage that reads it and the stages after it. Results
of outdated keys are kept (and shown as "stale") until they are replaced.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path

import cv2
import numpy as np

from .. import palette as pal_mod
from .. import report, verify
from .. import vectorize as vec
from ..pipeline import STAGES, Config, Run, config_dict, render_svgs, run_stage, write_outputs

STAGE_IDS = [s[0] for s in STAGES]


class Cancelled(Exception):
    pass


class Session:
    def __init__(self):
        self.lock = threading.RLock()
        self.name: str | None = None
        self.src: np.ndarray | None = None
        self.source_path: Path | None = None
        self.image_id = ""
        self.snaps: list[dict | None] = [None] * len(STAGES)
        self._views: dict = {}

    # -- image ------------------------------------------------------------
    def load(self, name: str, src: np.ndarray, source_path: Path | None = None) -> None:
        with self.lock:
            self.name, self.src, self.source_path = name, src, source_path
            self.image_id = hashlib.sha1(src.tobytes()).hexdigest()[:16]
            self.snaps = [None] * len(STAGES)
            self._views.clear()

    # -- cache keys ---------------------------------------------------------
    def keys(self, cfg: Config) -> list[str]:
        h = hashlib.sha1(self.image_id.encode())
        out = []
        for _, _, names in STAGES:
            vals = {n: _plain(getattr(cfg, n)) for n in names}
            h.update(json.dumps(vals, sort_keys=True).encode())
            out.append(h.hexdigest()[:12])
        return out

    def stage_states(self, cfg: Config) -> list[dict]:
        keys = self.keys(cfg) if self.src is not None else [""] * len(STAGES)
        out = []
        with self.lock:
            for sid, key, snap in zip(STAGE_IDS, keys, self.snaps):
                if snap is None:
                    out.append(dict(id=sid, status="missing"))
                    continue
                out.append(dict(id=sid, key=snap["key"],
                                status="fresh" if snap["key"] == key else "stale",
                                time=snap["run"].timings.get(sid), info=snap["info"]))
        return out

    # -- computation ----------------------------------------------------------
    def compute(self, cfg: Config, upto: int, cancelled=lambda: False,
                progress=lambda stage: None) -> None:
        """Bring stages 0..upto up to date with ``cfg``; raises ``Cancelled``
        between stages when ``cancelled()`` turns true."""
        with self.lock:
            if self.src is None:
                return
            keys = self.keys(cfg)
            prev = Run(self.name, self.src)
            image_id = self.image_id
        for i, sid in enumerate(STAGE_IDS[:upto + 1]):
            snap = self.snaps[i]
            if snap is not None and snap["key"] == keys[i]:
                prev = snap["run"]
                continue
            if cancelled():
                raise Cancelled()
            progress(sid)
            run = copy.copy(prev)
            run.timings = dict(prev.timings)
            run_stage(run, sid, cfg)
            info = stage_info(sid, run, cfg)
            with self.lock:
                if image_id != self.image_id:   # image switched meanwhile
                    raise Cancelled()
                self.snaps[i] = dict(key=keys[i], run=run, info=info)
            prev = run

    def final_run(self, cfg: Config) -> Run | None:
        snap = self.snaps[-1]
        if snap is None or snap["key"] != self.keys(cfg)[-1]:
            return None
        return snap["run"]

    # -- views ------------------------------------------------------------
    def view(self, stage: str, view: str) -> tuple[str, bytes] | None:
        """Render a view of the cached result of ``stage`` (fresh or stale)."""
        with self.lock:
            snap = self.snaps[STAGE_IDS.index(stage)]
            if snap is None:
                return None
            ck = (stage, view, snap["key"])
            if ck not in self._views:
                if len(self._views) > 64:
                    self._views.clear()
                self._views[ck] = render_view(stage, view, snap["run"])
            return self._views[ck]

    # -- export -----------------------------------------------------------
    def export(self, cfg: Config, out_dir: Path, mode: str, preview: bool) -> dict:
        run = self.final_run(cfg)
        if run is None:
            raise RuntimeError("Pipeline ist nicht vollständig berechnet")
        res = write_outputs(run, out_dir, mode)
        orig = out_dir / f"{run.name}.original.jpg"
        report.save_original(self.source_path or self.src, orig)
        cfg_file = out_dir / f"{run.name}.config.json"
        cfg_file.write_text(json.dumps(config_dict(cfg), indent=2) + "\n")
        files, checks = [str(orig), str(cfg_file)], {}
        for kind, svg in res.files.items():
            files.append(str(svg))
            if preview and report.render_png(svg, svg.with_suffix(".png")):
                files.append(str(svg.with_suffix(".png")))
            chk = verify.check_svg(svg)
            checks[kind] = dict(ok=chk["ok"], errors=chk["errors"],
                                fill_gap_ratio=chk.get("fill_gap_ratio"))
        files.append(str(out_dir / f"{run.name}.stats.json"))
        return dict(files=sorted(files), checks=checks, stats=res.stats)


def _plain(v):
    return asdict(v) if is_dataclass(v) else v


# --------------------------------------------------------------------------
# Stage info (numbers shown next to the parameters)
# --------------------------------------------------------------------------

def stage_info(stage: str, run: Run, cfg: Config) -> dict:
    if stage == "preprocess":
        h, w = run.bgr.shape[:2]
        sh, sw = run.src.shape[:2]
        return {"Original": f"{sw}×{sh} px", "Arbeitsgröße": f"{w}×{h} px"}
    if stage == "edges":
        info = {"Modell": {"dexined": "DexiNed", "classical": "klassisch"}[run.edge_parts["backend"]],
                "Texturmaß": f"{run.texture:.2f}",
                "Median Kantenkarte": f"{float(np.median(run.E)):.3f}"}
        if "fallback_reason" in run.edge_parts:
            info["Hinweis"] = "DexiNed nicht verfügbar – klassischer Fallback"
        return info
    if stage == "segment":
        p = run.merge_params
        return {"Flächen": int(run.seg_labels.max() + 1),
                "Watershed-Becken": int(run.overseg.max() + 1),
                "Effektives Ziel": p.target_regions,
                "Effektive Mindestfläche": f"{p.min_area} px²",
                "Effektive Grenzglättung": f"{run.label_sigma_eff:.2f} px"}
    if stage == "palette":
        counts = np.bincount(run.assign, minlength=len(run.palette))
        return {"Flächen": int(run.labels.max() + 1),
                "Vor Farbverschmelzung": run.regions_before_merge,
                "palette": [dict(hex=h, regions=int(c)) for h, c in zip(run.pal_hex, counts)]}
    if stage == "vectorize":
        inner = [c for c in run.graph.chains if not c.on_frame]
        return {"Grenzketten": len(inner),
                "Bézier-Segmente": sum(len(c.beziers) for c in inner),
                "Pixel-Stützpunkte": sum(len(c.points) for c in inner)}
    if stage == "style":
        inner = [c for c in run.graph.chains if not c.on_frame]
        major = sum(c.strength >= run.style.major_threshold for c in inner)
        sizes = {k: len(v.encode()) for k, v in render_svgs(run).items()}
        return {"Hauptlinien": major, "Binnenlinien": len(inner) - major,
                "Strich Haupt / Binnen": f"{run.style.major_width:.1f} / "
                                         f"{run.style.minor_width:.1f} px",
                "lines.svg": f"{sizes['lines'] / 1024:.0f} KB",
                "color.svg": f"{sizes['color'] / 1024:.0f} KB"}
    return {}


# --------------------------------------------------------------------------
# View rendering
# --------------------------------------------------------------------------

def _png(img: np.ndarray) -> tuple[str, bytes]:
    return "image/png", cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 1])[1].tobytes()


def _jpg(img: np.ndarray) -> tuple[str, bytes]:
    return "image/jpeg", cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()


def _svg(doc: str) -> tuple[str, bytes]:
    return "image/svg+xml", doc.encode("utf-8")


def _borders(labels: np.ndarray) -> np.ndarray:
    b = np.zeros(labels.shape, bool)
    b[:, 1:] |= labels[:, 1:] != labels[:, :-1]
    b[1:, :] |= labels[1:, :] != labels[:-1, :]
    return b


def _edge_img(e: np.ndarray) -> np.ndarray:
    return 255 - (np.clip(e, 0, 1) * 255).astype(np.uint8)


def _lab_to_bgr(lab: np.ndarray) -> np.ndarray:
    bgr = cv2.cvtColor(np.asarray(lab, np.float32).reshape(-1, 1, 3), cv2.COLOR_LAB2BGR)
    return np.clip(bgr.reshape(-1, 3) * 255, 0, 255).astype(np.uint8)


def _faded(bgr: np.ndarray, amount: float = 0.55) -> np.ndarray:
    return cv2.addWeighted(bgr, 1 - amount, np.full_like(bgr, 255), amount, 0)


def render_view(stage: str, view: str, run: Run) -> tuple[str, bytes]:
    if stage == "preprocess":
        img = {"original": run.resized, "contrast": run.bgr, "smooth": run.smooth}[view]
        return _jpg(img)

    if stage == "edges":
        if view == "fused":
            return _png(_edge_img(run.E))
        if view in ("global", "tiled"):
            m = run.edge_parts.get(view)
            if m is None:   # classical backend has no separate passes
                return _png(_edge_img(np.zeros_like(run.E)))
            return _png(_edge_img(m / (np.percentile(m, 99.7) + 1e-6)))
        if view == "overlay":
            base = _faded(cv2.cvtColor(cv2.cvtColor(run.smooth, cv2.COLOR_BGR2GRAY),
                                       cv2.COLOR_GRAY2BGR), 0.4).astype(np.float32)
            a = np.clip(run.E, 0, 1)[..., None] ** 0.8
            red = np.array([30, 30, 220], np.float32)
            return _jpg((base * (1 - a) + red * a).astype(np.uint8))

    if stage == "segment":
        if view == "borders":
            img = run.smooth.copy()
            img[_borders(run.seg_labels)] = (0, 0, 0)
            return _jpg(img)
        if view == "regions":
            n = int(run.seg_labels.max()) + 1
            colors = np.random.default_rng(7).integers(50, 240, (n, 3), dtype=np.uint8)
            img = colors[run.seg_labels]
            img[_borders(run.seg_labels)] = (30, 30, 30)
            return _png(img)
        if view == "means":
            means = pal_mod.region_means(run.seg_labels, run.lab_color)
            img = _lab_to_bgr(means)[run.seg_labels]
            img[_borders(run.seg_labels)] = (40, 40, 40)
            return _png(img)
        if view == "overseg":
            img = _faded(run.smooth, 0.3)
            img[_borders(run.overseg)] = (0, 0, 200)
            return _jpg(img)

    if stage == "palette":
        img = _lab_to_bgr(run.palette)[run.assign][run.labels]
        final = _borders(run.labels)
        if view == "flat":
            img[final] = (30, 30, 30)
            return _png(img)
        if view == "merged":
            img = _faded(img, 0.45)
            # Former borders not near a final one (the post-merge re-smoothing
            # shifts borders slightly), drawn bold so they stand out.
            near = cv2.dilate(final.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
            removed = (_borders(run.seg_labels) & ~near).astype(np.uint8)
            img[cv2.dilate(removed, np.ones((3, 3), np.uint8)).astype(bool)] = (40, 40, 230)
            img[final] = (0, 0, 0)
            return _png(img)

    if stage == "vectorize":
        return _svg(_curves_svg(run, compare=view == "compare"))

    if stage == "style":
        if view in ("lines", "color"):
            return _svg(render_svgs(run, view)[view])
        if view == "hierarchy":
            style = copy.copy(run.style)
            style.major_color, style.minor_color = "#1f5fd1", "#f08a24"
            return _svg(vec.lines_svg(run.graph, style, "line hierarchy"))

    raise KeyError(f"unknown view {stage}/{view}")


def _curves_svg(run: Run, compare: bool) -> str:
    g = run.graph
    W, H = g.width, g.height
    sw = max(g.width, g.height) / 1600.0
    curves, raw = [], []
    for c in g.chains:
        if c.on_frame or not c.beziers:
            continue
        curves.append(vec._segs_d(c.beziers, False, True))
        if compare:
            pts = c.points
            raw.append("M" + "L".join(vec._pt(p) for p in pts))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}">',
           f'<rect width="{W}" height="{H}" fill="#fff"/>',
           '<g fill="none" stroke-linecap="round" stroke-linejoin="round">']
    if compare:
        out.append(f'<path stroke="#e0352b" stroke-width="{vec._f(1.0 * sw)}" '
                   f'd="{"".join(raw)}"/>')
    out.append(f'<path stroke="#000" stroke-width="{vec._f((0.8 if compare else 1.4) * sw)}" '
               f'd="{"".join(curves)}"/>')
    out.append("</g></svg>")
    return "\n".join(out)
