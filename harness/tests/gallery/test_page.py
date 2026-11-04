"""The rendered page: it parses, it is self-contained, and the filter is applied
server-side so a curl'd query string already shows the right runs."""

from __future__ import annotations

import re
from pathlib import Path

from codeverse3d.addons.gallery.page import build_static, render_index, render_static
from codeverse3d.addons.gallery.urls import UrlMaker


def _assert_offline(markup: str) -> list[str]:
    urls = re.findall(r"""(?:src|href|action|srcset|poster)=['"]([^'"]+)""", markup)
    external = [u for u in urls if u.split(":", 1)[0].lower() in ("http", "https", "ftp") or u.startswith("//")]
    assert not external, f"external references: {external[:5]}"
    for bad in ("cdn.", "googleapis.com", "unpkg", "jsdelivr", "@import url(http"):
        assert bad not in markup, bad
    return urls


def test_static_and_server_pages(gallery_tree: dict[str, Path], tmp_path: Path):
    """The static build parses and is self-contained; the server page filters server-side and offers
    detail links only where the target has them; the static form never advertises server-only routes."""
    out = tmp_path / "out" / "gallery.html"
    path, n, index = build_static([gallery_tree["runs"], gallery_tree["battery"]], out, embed=True)
    assert path == out and n == 6 and out.is_file()
    markup = out.read_text()
    urls = _assert_offline(markup)
    assert markup.startswith("<!doctype html>")
    assert "data:image/jpeg;base64," in markup            # --embed inlined the sheets
    assert any(u.startswith("file://") for u in urls)  # links point at the run dirs
    assert "id='gallery-data'" in markup or 'id="gallery-data"' in markup
    assert markup.count("<article class='card") == 6
    assert "broken:" in markup and "pending:" in markup   # the two non-ok cards say why

    markup = render_index(index, UrlMaker(), flt={"lang": "threejs"}, sort="score")
    _assert_offline(markup)
    # every card is still in the DOM (so the client can widen the filter without a reload)
    assert markup.count("<article class='card") == 6
    # ...but only the matching one is visible, and the summary counts only that one
    assert len(re.findall(r"<article class='card [^']*is-hidden' data-run=", markup)) == 5
    assert ">1 <span class='faint'>of 6</span><" in markup
    assert "vc-passed" not in markup and "PASS RATE" not in markup.upper()   # no pass/fail since 2026-09-22

    markup = render_index(index, UrlMaker())
    assert "/run/runs/wooden_chair_ab12cd34" in markup and "/viewer/runs/wooden_chair_ab12cd34/artifacts/r01/object.glb" in markup
    static = render_static(index, embed=False)
    assert "/run/runs/" not in static and "/viewer/" not in static
