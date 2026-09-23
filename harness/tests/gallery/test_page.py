"""The rendered page: it parses, it is self-contained, and the filter is applied
server-side so a curl'd query string already shows the right runs."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from codeverse3d.addons.gallery.index import build_index
from codeverse3d.addons.gallery.page import build_static, render_index, render_static
from codeverse3d.addons.gallery.urls import StaticUrls, UrlMaker

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
        "source", "track", "wbr"}


class _Checker(HTMLParser):
    """Parses the document and collects every URL-bearing attribute + tag balance."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []
        self.stack: list[str] = []
        self.unbalanced: list[str] = []
        self.ids: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        d = dict(attrs)
        for key in ("src", "href", "action", "srcset", "poster", "data-src"):
            if d.get(key):
                self.urls.append(d[key])
        if d.get("id"):
            self.ids.append(d["id"])
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                self.unbalanced.append(tag)
        else:
            self.unbalanced.append(tag)


def _parse(markup: str) -> _Checker:
    c = _Checker()
    c.feed(markup)
    return c


def _assert_offline(markup: str) -> _Checker:
    c = _parse(markup)
    external = [u for u in c.urls if u.split(":", 1)[0].lower() in ("http", "https", "ftp")
                or u.startswith("//")]
    assert not external, f"external references: {external[:5]}"
    for bad in ("cdn.", "googleapis.com", "unpkg", "jsdelivr", "@import url(http"):
        assert bad not in markup, bad
    assert not c.unbalanced, f"unbalanced tags: {c.unbalanced[:5]}"
    return c


def test_static_build_parses_and_is_self_contained(gallery_tree: dict[str, Path], tmp_path: Path):
    out = tmp_path / "out" / "gallery.html"
    path, n, index = build_static([gallery_tree["runs"], gallery_tree["battery"]], out, embed=True)
    assert path == out and n == 6 and out.is_file()
    markup = out.read_text()
    checker = _assert_offline(markup)
    assert markup.startswith("<!doctype html>")
    assert "data:image/jpeg;base64," in markup            # --embed inlined the sheets
    assert any(u.startswith("file://") for u in checker.urls)  # links point at the run dirs
    assert "gallery-data" in checker.ids and "s-cost" in checker.ids
    assert markup.count("<article class='card") == 6
    for slug in ("wooden_chair_ab12cd34", "half_written", "not_started"):
        assert slug in markup
    assert "broken:" in markup and "pending:" in markup   # the two non-ok cards say why


def test_static_build_without_embed_is_small_and_links_files(gallery_tree: dict[str, Path], tmp_path: Path):
    out = tmp_path / "linked.html"
    build_static([gallery_tree["runs"]], out, embed=False)
    markup = out.read_text()
    _assert_offline(markup)
    assert "data:image/jpeg" not in markup
    assert "file://" in markup


def test_build_static_is_atomic(gallery_tree: dict[str, Path], tmp_path: Path):
    out = tmp_path / "g.html"
    build_static([gallery_tree["runs"]], out)
    first = out.read_text()
    build_static([gallery_tree["runs"], gallery_tree["battery"]], out)
    assert out.read_text() != first
    assert not list(tmp_path.glob("*.tmp"))  # no temp file left behind


def test_server_page_applies_the_filter_server_side(gallery_tree: dict[str, Path]):
    index = build_index([gallery_tree["runs"], gallery_tree["battery"]])
    markup = render_index(index, UrlMaker(), flt={"lang": "threejs"}, sort="score")
    _assert_offline(markup)
    # every card is still in the DOM (so the client can widen the filter without a reload)
    assert markup.count("<article class='card") == 6
    # ...but only the matching one is visible, and the summary counts only that one
    assert len(re.findall(r"<article class='card [^']*is-hidden' data-run=", markup)) == 5
    assert ">1 <span class='faint'>of 6</span><" in markup
    assert "<option value='threejs' selected>" in markup


def test_sections_counts_and_table_mode(gallery_tree: dict[str, Path]):
    index = build_index([gallery_tree["runs"], gallery_tree["battery"]])
    markup = render_index(index, UrlMaker(), view="table")
    assert "<body class='view-table'>" in markup
    assert "id='b-runs'" in markup and "id='b-static_v9'" in markup
    assert ">2 runs<" in markup and ">4 runs<" in markup
    assert len(re.findall(r"<tr class='[^']*' data-run=", markup)) == 6


def test_detail_links_are_offered_only_when_the_target_has_them(gallery_tree: dict[str, Path]):
    index = build_index([gallery_tree["runs"]])
    markup = render_index(index, UrlMaker())
    assert "/run/runs/wooden_chair_ab12cd34" in markup
    assert "/file/runs/wooden_chair_ab12cd34/artifacts/renders/r01/sheet.png" in markup
    assert "/code/runs/wooden_chair_ab12cd34/src" in markup
    assert "/viewer/runs/wooden_chair_ab12cd34/artifacts/r01/object.glb" in markup   # the picked round's
    # the static form never advertises server-only routes
    static = render_static(index, embed=False)
    assert "/run/runs/" not in static and "/viewer/" not in static


def test_html_escaping_of_a_hostile_prompt(tmp_path: Path):
    from tests.flywheel_cli.conftest import make_fake_run

    runs = tmp_path / "runs"
    make_fake_run(runs, "xss", prompt="a chair <script>alert(1)</script> & \"quotes\"")
    markup = render_index(build_index([runs]), UrlMaker())
    assert "<script>alert(1)</script>" not in markup
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in markup
    _assert_offline(markup)


def test_static_urls_of_an_entry(gallery_tree: dict[str, Path]):
    entry = build_index([gallery_tree["runs"]]).entries()[0]
    urls = StaticUrls(embed=False)
    assert urls.file(entry, "record.json").startswith("file://")
    assert urls.file(entry, "record.json").endswith("/record.json")
    assert not urls.has_detail
