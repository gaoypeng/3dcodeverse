"""The local server: routing, path-traversal refusal, content types, live reload.

Offline by construction — the integration test binds an ephemeral loopback port
in a background thread and talks to it with ``http.client``.
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from codeverse.addons.gallery.server import (
    DEFAULT_HOST,
    GalleryApp,
    GalleryError,
    make_server,
    resolve_host,
)
from codeverse.addons.gallery.urls import PathError, content_type, safe_join


@pytest.fixture
def app(gallery_tree: dict[str, Path]) -> GalleryApp:
    return GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])


# --------------------------------------------------------------------------- routing
def test_index_and_api_routes(app: GalleryApp):
    r = app.route("/")
    assert r.status == 200 and r.content_type.startswith("text/html")
    assert b"3dcode gallery" in r.body
    assert app.route("/index.html").status == 200
    assert app.route("/healthz").body == b"ok"
    api = json.loads(app.route("/api/runs", {"lang": "threejs"}).body)
    assert api["total"] == 6 and api["n"] == 1 and api["runs"][0]["slug"] == "lamp_three"
    summary = json.loads(app.route("/api/summary", {"battery": "runs"}).body)
    assert summary["n"] == 2
    assert app.route("/api/nope").status == 404


def test_detail_page(app: GalleryApp):
    r = app.route("/run/runs/wooden_chair_ab12cd34")
    assert r.status == 200
    page = r.body.decode()
    for anchor in ("id='rounds'", "id='judge'", "id='renders'", "id='code'", "id='cost'"):
        assert anchor in page, anchor
    assert "thicken the legs" in page          # the judge's improvement plan
    assert "primitive_cube_add" in page        # the code viewer inlined the entry file
    assert app.route("/run/runs/nope").status == 404
    assert app.route("/run/runs").status == 404


def test_detail_of_a_broken_run_does_not_crash(app: GalleryApp):
    r = app.route("/run/static_v9/half_written")
    assert r.status == 200 and b"broken" in r.body
    r = app.route("/run/static_v9/not_started")
    assert r.status == 200 and b"pending" in r.body


def test_file_dir_and_code_routes(app: GalleryApp):
    base = "/file/runs/wooden_chair_ab12cd34"
    rec = app.route(f"{base}/record.json")
    assert rec.status == 200 and rec.content_type == "application/json; charset=utf-8"
    assert rec.path is not None and rec.path.name == "record.json"
    png = app.route(f"{base}/artifacts/renders/r01/sheet.png")
    assert png.status == 200 and png.content_type == "image/png"
    glb = app.route(f"{base}/artifacts/object.glb")
    assert glb.status == 200 and glb.content_type == "model/gltf-binary"
    src = app.route(f"{base}/src/model.py")
    assert src.status == 200 and src.content_type == "text/plain; charset=utf-8"
    code = app.route("/code/runs/wooden_chair_ab12cd34/src/model.py")
    assert code.status == 200 and b"primitive_cube_add" in code.body and b"class='ln'" in code.body
    listing = app.route("/dir/runs/wooden_chair_ab12cd34/src")
    assert listing.status == 200 and b"model.py" in listing.body
    assert app.route(f"{base}/artifacts").status == 200          # a file route onto a dir lists it
    assert app.route(f"{base}/does/not/exist.png").status == 404


@pytest.mark.parametrize("bad", [
    "../../../../etc/passwd",
    "..%2f..%2fetc/passwd",
    "%2e%2e/%2e%2e/etc/passwd",
    "src/../../../../etc/passwd",
    "src/../../lamp_three/record.json",
    "....//....//etc/passwd",
])
def test_path_traversal_is_refused(app: GalleryApp, bad: str):
    r = app.route(f"/file/runs/wooden_chair_ab12cd34/{bad}")
    assert r.status in (403, 404), f"{bad} → {r.status}"
    assert b"root:x:" not in r.body


def test_absolute_paths_are_refused(app: GalleryApp):
    # '//etc/passwd' collapses to 'etc/passwd' inside the run (404, not a read)
    assert app.route("/file/runs/wooden_chair_ab12cd34//etc/passwd").status == 404
    assert safe_join(app.roots[0], "wooden_chair_ab12cd34").is_dir()
    with pytest.raises(PathError):
        safe_join(app.roots[0], "/etc/passwd")
    with pytest.raises(PathError):
        safe_join(app.roots[0], "../..")
    with pytest.raises(PathError):
        safe_join(app.roots[0], "a/../../b")
    with pytest.raises(PathError):
        safe_join(app.roots[0], "x\x00y")


def test_symlink_out_of_the_run_is_refused(app: GalleryApp, tmp_path: Path):
    run = app.roots[0] / "wooden_chair_ab12cd34"
    outside = tmp_path / "secret.txt"
    outside.write_text("classified")
    (run / "escape").symlink_to(outside)
    r = app.route("/file/runs/wooden_chair_ab12cd34/escape")
    assert r.status == 403 and b"classified" not in r.body


def test_unknown_run_and_unknown_route(app: GalleryApp):
    assert app.route("/file/nope/nope/record.json").status == 404
    assert app.route("/wat").status == 404
    assert app.route("/file").status == 404


def test_vendor_and_viewer(app: GalleryApp):
    from codeverse.addons.gallery.viewer import viewer_available

    v = app.route("/viewer/runs/wooden_chair_ab12cd34/artifacts/object.glb")
    if viewer_available():
        assert v.status == 200 and b"importmap" in v.body
        js = app.route("/vendor/three/build/three.module.js")
        assert js.status == 200 and js.content_type.startswith("text/javascript")
        assert app.route("/vendor/three/../../../etc/passwd").status == 404
        assert app.route("/vendor/nope.js").status == 404
    else:  # runtime_js/node_modules not installed
        assert v.status == 501


def test_content_type_table():
    assert content_type("a.glb") == "model/gltf-binary"
    assert content_type("a.frag") == "text/plain; charset=utf-8"
    assert content_type("a.js") == "text/plain; charset=utf-8"
    assert content_type("a.gif") == "image/gif"
    assert content_type("a.unknownext") == "application/octet-stream"


# --------------------------------------------------------------------------- host guard
def test_host_guard():
    assert resolve_host(None) == DEFAULT_HOST
    assert resolve_host("") == DEFAULT_HOST
    assert resolve_host("localhost") == "localhost"
    assert resolve_host("0.0.0.0", explicit=True) == "0.0.0.0"  # noqa: S104 - the point of the test
    with pytest.raises(GalleryError):
        resolve_host("0.0.0.0")  # noqa: S104
    with pytest.raises(GalleryError):
        resolve_host("192.168.1.5")


# --------------------------------------------------------------------------- reload
def test_reload_picks_up_a_run_written_after_startup(gallery_tree: dict[str, Path]):
    from tests.flywheel_cli.conftest import make_fake_run

    app = GalleryApp([gallery_tree["runs"]], reload=True)
    assert len(app.index.entries()) == 2
    make_fake_run(gallery_tree["runs"], "brand_new", prompt="a new stool")
    app.route("/")
    assert len(app.index.entries()) == 3
    assert app.route("/run/runs/brand_new").status == 200


def test_no_reload_still_finds_a_new_run_by_url(gallery_tree: dict[str, Path]):
    from tests.flywheel_cli.conftest import make_fake_run

    app = GalleryApp([gallery_tree["runs"]], reload=False)
    make_fake_run(gallery_tree["runs"], "late_arrival", prompt="a late stool")
    assert app.route("/run/runs/late_arrival").status == 200


# --------------------------------------------------------------------------- over a real socket
def test_over_a_loopback_socket(app: GalleryApp):
    httpd = make_server(app, DEFAULT_HOST, 0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection(DEFAULT_HOST, port, timeout=10)

        def get(path: str):
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type"), resp.read()

        status, ctype, body = get("/")
        assert status == 200 and ctype.startswith("text/html") and b"wooden_chair" in body
        status, ctype, body = get("/file/runs/wooden_chair_ab12cd34/artifacts/renders/r01/sheet.png")
        assert status == 200 and ctype == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n"
        status, ctype, _ = get("/file/runs/wooden_chair_ab12cd34/artifacts/object.glb")
        assert status == 200 and ctype == "model/gltf-binary"
        status, ctype, body = get("/api/runs?tier=A")
        assert status == 200 and ctype.startswith("application/json") and json.loads(body)["n"] >= 1
        status, _, body = get("/file/runs/wooden_chair_ab12cd34/..%2f..%2f..%2fetc%2fpasswd")
        assert status in (403, 404) and b"root:x:" not in body
        conn.request("HEAD", "/")
        head = conn.getresponse()
        assert head.status == 200 and head.read() == b"" and int(head.getheader("Content-Length")) > 0
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_make_server_reports_a_bad_bind(app: GalleryApp):
    with pytest.raises(GalleryError):
        make_server(app, "203.0.113.1", 0)  # TEST-NET-3: never assigned to this host
