"""``docs/architecture.html`` must stay valid, self-contained and true to the code.

The page is hand-authored and hand-maintained (see its footer).  These tests are the
guard rails: it has to parse, work offline, keep every relative link and ``data:`` URI
alive, cover every track and every backend id, and — the one that actually catches
drift — list *exactly* the tools that ``codeverse.spatial.registry`` currently has.
"""

from __future__ import annotations

import base64
import binascii
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

import codeverse.spatial.tools  # noqa: F401  — importing registers every tool
from codeverse.agents.registry import KINDS as AGENT_KINDS
from codeverse.contracts.common import Track
from codeverse.models.registry import PROVIDERS
from codeverse.spatial.registry import list_tools

DOCS = Path(__file__).resolve().parents[2] / "docs"
PAGE = DOCS / "architecture.html"
MAX_BYTES = 8 * 1024 * 1024

#: HTML void elements — never on the open-tag stack.
VOID = frozenset(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"])
#: elements whose content is not markup (html.parser already treats these as CDATA)
RAW_TEXT = frozenset({"script", "style"})
#: mime types we allow inside a data: URI on this page
DATA_MIME = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"})
#: the non-agent generation backends the page must document alongside AGENT_KINDS
EXTRA_BACKEND_IDS = ("single-shot",)


class Page(HTMLParser):
    """Collects everything the assertions below need in one pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.links: list[str] = []          # href/src values worth resolving
        self.data_uris: list[str] = []
        self.imgs: list[dict[str, str]] = []
        self.title = ""
        self._in_title = False
        # tool-table scraping
        self._in_tool_table = False
        self._in_tool_body = False
        self._cell = 0
        self._grab_code = False
        self.tool_names: list[str] = []

    # -- structure -------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag not in VOID and tag not in RAW_TEXT:
            self.stack.append(tag)
        self._note(tag, a)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._note(tag, {k: (v or "") for k, v in attrs})

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID or tag in RAW_TEXT:
            return
        if tag not in self.stack:
            self.errors.append(f"stray </{tag}> at line {self.getpos()[0]}")
            return
        while self.stack:
            open_tag = self.stack.pop()
            if open_tag == tag:
                break
            self.errors.append(f"</{tag}> closes an unclosed <{open_tag}> at line {self.getpos()[0]}")
        if tag == "title":
            self._in_title = False
        if tag == "table":
            self._in_tool_table = False
        if tag == "tbody":
            self._in_tool_body = False

    # -- content ---------------------------------------------------------
    def _note(self, tag: str, a: dict[str, str]) -> None:
        if "id" in a:
            self.ids.add(a["id"])
        for key in ("href", "src"):
            val = a.get(key, "").strip()
            if not val:
                continue
            if val.startswith("data:"):
                self.data_uris.append(val)
            elif not val.startswith(("#", "mailto:", "javascript:")):
                self.links.append(val)
        if tag == "img":
            self.imgs.append(a)
        if tag == "title" and not self.title:
            self._in_title = True
        if tag == "table" and a.get("id") == "tool-table":
            self._in_tool_table = True
        if tag == "tbody" and self._in_tool_table:
            self._in_tool_body = True
        if self._in_tool_body:
            if tag == "tr":
                self._cell = 0
            elif tag in ("td", "th"):
                self._cell += 1
            elif tag == "code" and self._cell == 1:
                self._grab_code = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._grab_code:
            self.tool_names.append(data.strip())
            self._grab_code = False


@pytest.fixture(scope="module")
def raw() -> str:
    assert PAGE.is_file(), f"{PAGE} is missing"
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page(raw: str) -> Page:
    p = Page()
    p.feed(raw)
    p.close()
    return p


def test_exists_and_parses(page: Page) -> None:
    assert not page.errors, "malformed HTML: " + "; ".join(page.errors[:5])
    unclosed = [t for t in page.stack if t not in ("html", "body")]
    assert not unclosed, f"unclosed tags: {unclosed}"
    assert page.title.strip(), "the page needs a <title>"
    assert "3dcodeverse" in page.title


def test_file_stays_small() -> None:
    size = PAGE.stat().st_size
    assert size < MAX_BYTES, f"{size / 1e6:.1f} MB — keep the page under {MAX_BYTES / 1e6:.0f} MB"


def test_works_offline_no_external_references(raw: str) -> None:
    """No http(s) reference outside <code>/<pre> text — the page must open by double-click."""
    stripped = re.sub(r"<(code|pre)\b.*?</\1>", " ", raw, flags=re.S | re.I)
    hits = re.findall(r"https?://\S{0,60}", stripped)
    assert not hits, f"external references outside code text: {hits[:5]}"


def test_data_uris_are_well_formed(page: Page) -> None:
    assert len(page.data_uris) >= 5, "the page should embed its screenshots as data: URIs"
    for uri in page.data_uris:
        m = re.fullmatch(r"data:([\w.+-]+/[\w.+-]+);base64,([A-Za-z0-9+/]+={0,2})", uri)
        assert m, f"malformed data: URI (first 80 chars): {uri[:80]!r}"
        mime, payload = m.group(1), m.group(2)
        assert mime in DATA_MIME, f"unexpected data: URI mime {mime}"
        try:
            blob = base64.b64decode(payload, validate=True)
        except binascii.Error as e:  # pragma: no cover - only on a corrupted page
            pytest.fail(f"data: URI is not valid base64 ({mime}): {e}")
        assert len(blob) > 512, f"data: URI payload is suspiciously small ({len(blob)} B)"
        if mime == "image/jpeg":
            assert blob[:2] == b"\xff\xd8", "JPEG data: URI has no SOI marker"
        elif mime == "image/png":
            assert blob[:8] == b"\x89PNG\r\n\x1a\n", "PNG data: URI has no signature"


def test_every_image_has_alt_text(page: Page) -> None:
    missing = [i.get("src", "")[:40] for i in page.imgs if not i.get("alt", "").strip()]
    assert not missing, f"{len(missing)} <img> without alt text"


def test_relative_links_resolve(page: Page) -> None:
    broken = []
    for href in page.links:
        target = (DOCS / href.split("#", 1)[0]).resolve()
        if not target.exists():
            broken.append(href)
    assert not broken, f"relative links that do not resolve from docs/: {broken}"


def test_in_page_anchors_resolve(raw: str, page: Page) -> None:
    anchors = {m.group(1) for m in re.finditer(r'href="#([^"]+)"', raw)}
    missing = sorted(a for a in anchors if a not in page.ids)
    assert not missing, f"href=\"#…\" targets with no matching id: {missing}"


def test_a_section_per_track(page: Page) -> None:
    for track in Track:
        assert f"track-{track.value}" in page.ids, f"no section for track {track.value}"


def test_a_section_per_backend_id(page: Page) -> None:
    expected = [*PROVIDERS, *AGENT_KINDS, *EXTRA_BACKEND_IDS]
    for backend in expected:
        assert f"backend-{backend}" in page.ids, f"no section for backend id {backend!r}"


def test_tool_table_matches_the_registry(page: Page) -> None:
    """The catalogue in the page must be exactly what the registry offers today."""
    registered = sorted(t.name for t in list_tools())
    documented = sorted(page.tool_names)
    assert documented, "no rows found in <table id=\"tool-table\">"
    missing = sorted(set(registered) - set(documented))
    extra = sorted(set(documented) - set(registered))
    assert not missing and not extra, (
        "docs/architecture.html §06 has drifted from codeverse.spatial.registry — "
        f"missing rows: {missing}; stale rows: {extra}"
    )
    assert len(documented) == len(registered), f"duplicate tool rows: {documented}"
