"""GUI backend: stage cache invalidation, views and the HTTP API."""
import json
import threading
import time
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from lineart import preprocess as pp
from lineart.cli import main as cli_main
from lineart.gui import spec
from lineart.gui.server import App, make_handler
from lineart.gui.session import STAGE_IDS, Session
from lineart.pipeline import config_dict, make_config


def _cfg(**kw):
    return make_config("medium", edge_backend="classical", work_size=400,
                       target_regions=12, colors=5, **kw)


def test_only_downstream_stages_recompute(synthetic: Path):
    s = Session()
    s.load("shapes", pp.load_image(synthetic), synthetic)
    cfg = _cfg()
    s.compute(cfg, len(STAGE_IDS) - 1)
    assert [x["status"] for x in s.stage_states(cfg)] == ["fresh"] * 6

    cfg2 = replace(cfg, line_width=2.0)
    assert [x["status"] for x in s.stage_states(cfg2)] == ["fresh"] * 5 + ["stale"]
    cfg3 = replace(cfg, merge=replace(cfg.merge, edge_weight=0.5))
    assert [x["status"] for x in s.stage_states(cfg3)] == ["fresh"] * 2 + ["stale"] * 4

    before = [snap["run"] for snap in s.snaps]
    ran = []
    s.compute(cfg2, 5, progress=ran.append)
    assert ran == ["style"]
    assert all(a is b["run"] for a, b in zip(before[:5], s.snaps[:5]))


def test_every_view_renders(synthetic: Path):
    s = Session()
    s.load("shapes", pp.load_image(synthetic), synthetic)
    s.compute(_cfg(), 5)
    for st in spec.STAGES:
        for v in st["views"]:
            ctype, body = s.view(st["id"], v["id"])
            assert body and ctype.startswith("image/")
            if v["kind"] == "svg":
                assert body.startswith(b"<")


def test_spec_matches_pipeline():
    cfg = config_dict(make_config())
    assert [st["id"] for st in spec.STAGES] == STAGE_IDS
    for st in spec.STAGES:
        for p in st["params"]:
            a, _, b = p["key"].partition(".")
            assert a in cfg and (not b or b in cfg[a]), p["key"]


@pytest.fixture()
def server(synthetic: Path, tmp_path: Path):
    app = App(synthetic.parent, tmp_path / "out")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", app
    httpd.shutdown()


def _req(url, data=None, headers=None):
    body = None if data is None else data if isinstance(data, bytes) else json.dumps(data).encode()
    r = urllib.request.urlopen(urllib.request.Request(url, body, headers or {}), timeout=60)
    raw = r.read()
    return json.loads(raw) if r.headers.get_content_type() == "application/json" else raw


def test_http_api_roundtrip(server, synthetic: Path, tmp_path: Path):
    base, app = server
    meta = _req(f"{base}/api/meta")
    assert meta["sources"] == ["shapes.png"]
    assert b"Lineart Studio" in _req(f"{base}/")

    st = _req(f"{base}/api/upload", synthetic.read_bytes(), {"X-Filename": "my%20pic.png"})
    assert st["image"]["name"] == "my_pic"
    cfg = config_dict(_cfg())
    st = _req(f"{base}/api/config", {"config": cfg, "preset": "medium", "run": "style"})
    for _ in range(300):
        if not st["busy"]:
            break
        time.sleep(0.1)
        st = _req(f"{base}/api/status")
    assert st["error"] is None
    assert [x["status"] for x in st["stages"]] == ["fresh"] * 6

    key = st["stages"][-1]["key"]
    assert _req(f"{base}/api/view/style/lines?k={key}").startswith(b"<?xml")
    assert b'id="color-fills"' in _req(f"{base}/api/download/color")

    res = _req(f"{base}/api/export", {"mode": "both", "preview": False})
    assert all(c["ok"] for c in res["checks"].values()), res["checks"]
    out = tmp_path / "out"
    assert (out / "my_pic.config.json").exists()

    # The exported config reproduces the GUI result via the CLI.
    cli_out = tmp_path / "cli"
    assert cli_main(["-i", str(synthetic), "-o", str(cli_out), "--config",
                     str(out / "my_pic.config.json"), "--no-preview", "--no-report", "-q"]) == 0
    cli_svg = (cli_out / "shapes.lines.svg").read_text()
    assert cli_svg == (out / "my_pic.lines.svg").read_text().replace("my_pic", "shapes")
