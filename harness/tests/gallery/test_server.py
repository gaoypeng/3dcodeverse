"""The local server: routing, path-traversal refusal, content types, live reload.

Offline by construction — the integration test binds an ephemeral loopback port
in a background thread and talks to it with ``http.client``.
"""

from __future__ import annotations

import http.client
import threading
from pathlib import Path

import pytest

from codeverse3d.addons.gallery.server import DEFAULT_HOST, GalleryApp, GalleryError, make_server
from codeverse3d.addons.gallery.urls import PathError, safe_join


@pytest.fixture
def app(gallery_tree: dict[str, Path]) -> GalleryApp:
    return GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])


# --------------------------------------------------------------------------- routing
def test_routes(app: GalleryApp):
    r = app.route("/")
    assert r.status == 200 and r.content_type.startswith("text/html")
    assert b"3dcodeverse gallery" in r.body
    assert app.route("/nope").status == 404

    # the detail of a broken / pending run does not crash
    r = app.route("/run/static_v9/half_written")
    assert r.status == 200 and b"broken" in r.body
    r = app.route("/run/static_v9/not_started")
    assert r.status == 200 and b"pending" in r.body

    # file, dir and code routes
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

    # unknown run / unknown route
    assert app.route("/file/nope/nope/record.json").status == 404


def test_traversal_absolute_paths_and_symlinks_out_of_the_run_are_refused(app: GalleryApp, tmp_path: Path):
    for bad in ("../../../../etc/passwd", "..%2f..%2fetc/passwd", "src/../../lamp_three/record.json"):
        r = app.route(f"/file/runs/wooden_chair_ab12cd34/{bad}")
        assert r.status in (403, 404), f"{bad} → {r.status}"
        assert b"root:x:" not in r.body
    # '//etc/passwd' collapses to 'etc/passwd' inside the run (404, not a read)
    assert app.route("/file/runs/wooden_chair_ab12cd34//etc/passwd").status == 404
    assert safe_join(app.roots[0], "wooden_chair_ab12cd34").is_dir()
    for bad in ("/etc/passwd", "../..", "a/../../b", "x\x00y"):
        with pytest.raises(PathError):
            safe_join(app.roots[0], bad)
    run = app.roots[0] / "wooden_chair_ab12cd34"
    outside = tmp_path / "secret.txt"
    outside.write_text("classified")
    (run / "escape").symlink_to(outside)
    r = app.route("/file/runs/wooden_chair_ab12cd34/escape")
    assert r.status == 403 and b"classified" not in r.body


def test_llm_html_and_svg_file_responses_are_sandboxed(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"]])
    run = gallery_tree["runs"] / "wooden_chair_ab12cd34"
    (run / "artifacts" / "page.html").write_text("<script>fetch('/api/index')</script>")
    (run / "artifacts" / "figure.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>")
    html = app.route("/file/runs/wooden_chair_ab12cd34/artifacts/page.html")
    assert html.status == 200 and html.content_type == "text/html; charset=utf-8"
    assert html.headers["Content-Security-Policy"] == "sandbox"
    svg = app.route("/file/runs/wooden_chair_ab12cd34/artifacts/figure.svg")
    assert svg.status == 200 and svg.content_type == "image/svg+xml"
    assert svg.headers["Content-Security-Policy"] == "sandbox"
    # images and code stay untouched — <img src=…> previews keep rendering
    png = app.route("/file/runs/wooden_chair_ab12cd34/artifacts/renders/r01/sheet.png")
    assert png.status == 200 and "Content-Security-Policy" not in png.headers
    src = app.route("/file/runs/wooden_chair_ab12cd34/src/model.py")
    assert src.status == 200 and "Content-Security-Policy" not in src.headers


# --------------------------------------------------------------------------- over a real socket
def test_over_a_loopback_socket(app: GalleryApp):
    with pytest.raises(GalleryError):
        make_server(app, "203.0.113.1", 0)  # TEST-NET-3: never assigned to this host
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
