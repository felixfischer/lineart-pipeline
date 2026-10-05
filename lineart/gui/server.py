"""Local web GUI: a small JSON API around ``Session`` plus static files.

Only the standard library is used (``http.server``); the pipeline runs in
one background worker thread. A new request supersedes a running one: the
worker abandons it at the next stage boundary.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
import traceback
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import preprocess as pp
from ..cli import IMAGE_EXT
from ..pipeline import PRESETS, config_dict, config_from_dict, make_config, render_svgs
from . import spec
from .session import STAGE_IDS, Cancelled, Session

log = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 80 * 1024 * 1024


class Worker(threading.Thread):
    def __init__(self, session: Session):
        super().__init__(daemon=True, name="pipeline-worker")
        self.session = session
        self.cond = threading.Condition()
        self.gen = 0
        self.pending = None           # (gen, cfg, upto)
        self.running = False
        self.stage: str | None = None
        self.error: str | None = None

    def submit(self, cfg, upto: int) -> None:
        with self.cond:
            self.gen += 1
            self.pending = (self.gen, cfg, upto)
            self.error = None
            self.cond.notify()

    @property
    def busy(self) -> bool:
        return self.running or self.pending is not None

    def run(self) -> None:
        while True:
            with self.cond:
                while self.pending is None:
                    self.cond.wait()
                gen, cfg, upto = self.pending
                self.pending = None
                self.running = True
            try:
                self.session.compute(cfg, upto, cancelled=lambda: self.gen != gen,
                                     progress=lambda s: setattr(self, "stage", s))
            except Cancelled:
                pass
            except Exception as exc:   # report to the UI, keep the worker alive
                log.error("pipeline error:\n%s", traceback.format_exc())
                self.error = f"{type(exc).__name__}: {exc}"
            finally:
                self.running = False
                self.stage = None


class App:
    def __init__(self, source_dir: Path, output_dir: Path):
        self.source_dir = source_dir
        self.output_dir = output_dir
        self.session = Session()
        self.worker = Worker(self.session)
        self.worker.start()
        self.cfg = make_config("medium")
        self.preset = "medium"

    def sources(self) -> list[str]:
        if not self.source_dir.is_dir():
            return []
        return sorted(p.name for p in self.source_dir.iterdir()
                      if p.suffix.lower() in IMAGE_EXT)

    def status(self) -> dict:
        s = self.session
        img = None
        if s.src is not None:
            h, w = s.src.shape[:2]
            img = dict(name=s.name, width=w, height=h,
                       path=str(s.source_path) if s.source_path else None)
        return dict(image=img, busy=self.worker.busy, stage=self.worker.stage,
                    error=self.worker.error, stages=s.stage_states(self.cfg),
                    config=config_dict(self.cfg), preset=self.preset)

    def meta(self) -> dict:
        return dict(stages=spec.STAGES, presets={k: config_dict(make_config(k)) for k in PRESETS},
                    sources=self.sources(), source_dir=str(self.source_dir),
                    output_dir=str(self.output_dir.resolve()))


def _safe_name(name: str) -> str:
    stem = Path(name).stem
    return re.sub(r"[^\w.-]+", "_", stem).strip("._") or "bild"


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "lineart-gui"

        def log_message(self, fmt, *args):   # keep the console quiet
            log.debug("%s - %s", self.address_string(), fmt % args)

        # -- helpers ------------------------------------------------------
        def _send(self, body: bytes, ctype: str, status=HTTPStatus.OK, headers=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status=HTTPStatus.OK):
            self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json", status)

        def _error(self, msg: str, status=HTTPStatus.BAD_REQUEST):
            self._json({"error": msg}, status)

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_UPLOAD:
                raise ValueError("Datei zu groß")
            return self.rfile.read(n)

        # -- routes -------------------------------------------------------
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            try:
                if not parts:
                    return self._static("index.html")
                if parts[0] == "static" and len(parts) == 2:
                    return self._static(parts[1])
                if parts[:2] == ["api", "meta"]:
                    return self._json(app.meta())
                if parts[:2] == ["api", "status"]:
                    return self._json(app.status())
                if parts[:2] == ["api", "view"] and len(parts) == 4:
                    res = app.session.view(parts[2], parts[3])
                    if res is None:
                        return self._error("noch nicht berechnet", HTTPStatus.NOT_FOUND)
                    # URLs carry the cache key, so responses never change.
                    return self._send(res[1], res[0],
                                      headers={"Cache-Control": "private, max-age=3600"})
                if parts[:2] == ["api", "download"] and len(parts) == 3:
                    return self._download(parts[2])
            except KeyError as exc:
                return self._error(str(exc), HTTPStatus.NOT_FOUND)
            except Exception as exc:
                log.error("GET %s failed:\n%s", self.path, traceback.format_exc())
                return self._error(f"{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)
            self._error("not found", HTTPStatus.NOT_FOUND)

        def do_POST(self):
            parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
            try:
                if parts[:2] == ["api", "upload"]:
                    name = urllib.parse.unquote(self.headers.get("X-Filename", "upload"))
                    app.session.load(_safe_name(name), pp.decode_image(self._body()))
                    return self._json(app.status())
                data = json.loads(self._body() or b"{}")
                if parts[:2] == ["api", "open"]:
                    return self._open(data)
                if parts[:2] == ["api", "config"]:
                    return self._config(data)
                if parts[:2] == ["api", "export"]:
                    return self._export(data)
            except ValueError as exc:
                return self._error(str(exc))
            except Exception as exc:
                log.error("POST %s failed:\n%s", self.path, traceback.format_exc())
                return self._error(f"{type(exc).__name__}: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)
            self._error("not found", HTTPStatus.NOT_FOUND)

        def _static(self, name: str):
            f = STATIC / name
            if f.parent != STATIC or not f.is_file():
                return self._error("not found", HTTPStatus.NOT_FOUND)
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(f.read_bytes(), ctype, headers={"Cache-Control": "no-cache"})

        def _open(self, data):
            name = data.get("file", "")
            if name not in app.sources():
                raise ValueError(f"unbekannte Datei: {name}")
            path = app.source_dir / name
            app.session.load(path.stem, pp.load_image(path), path)
            self._json(app.status())

        def _config(self, data):
            if "preset" in data:
                if data["preset"] not in PRESETS:
                    raise ValueError("unbekanntes Preset")
                app.preset = data["preset"]
            if "config" in data:
                app.cfg = config_from_dict(data["config"], make_config(app.preset))
            upto = data.get("run")
            if upto is not None and app.session.src is not None:
                idx = STAGE_IDS.index(upto) if isinstance(upto, str) else int(upto)
                app.worker.submit(app.cfg, idx)
            self._json(app.status())

        def _export(self, data):
            mode = data.get("mode", "both")
            if mode not in ("lines", "color", "both"):
                raise ValueError("mode")
            res = app.session.export(app.cfg, app.output_dir, mode, bool(data.get("preview", True)))
            self._json(res)

        def _download(self, kind: str):
            run = app.session.final_run(app.cfg)
            if run is None:
                return self._error("Pipeline ist nicht vollständig berechnet", HTTPStatus.CONFLICT)
            if kind == "config":
                body = (json.dumps(config_dict(app.cfg), indent=2) + "\n").encode()
                fname, ctype = f"{run.name}.config.json", "application/json"
            else:
                body = render_svgs(run, kind)[kind].encode()
                fname, ctype = f"{run.name}.{kind}.svg", "image/svg+xml"
            self._send(body, ctype, headers={
                "Content-Disposition": f'attachment; filename="{fname}"'})

    return Handler


def serve(source_dir: Path, output_dir: Path, host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = True, image: Path | None = None) -> None:
    app = App(source_dir, output_dir)
    if image is None and app.sources():
        image = app.source_dir / app.sources()[0]
    if image is not None:
        app.session.load(image.stem, pp.load_image(image), image)
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{host if host not in ('0.0.0.0', '') else 'localhost'}:{httpd.server_port}/"
    print(f"Lineart GUI läuft auf {url}  (Strg+C beendet)", flush=True)
    if open_browser:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
