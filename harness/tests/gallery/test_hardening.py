"""Serving LLM-authored HTML/SVG must be inert (CSP sandbox, honest content type),
and CSV cells must never reach a spreadsheet as formulas."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from codeverse3d.addons.gallery.compare import CSV_COLUMNS, csv_safe, export_csv
from codeverse3d.addons.gallery.index import build_index
from codeverse3d.addons.gallery.server import SANDBOXED_TYPES, GalleryApp
from codeverse3d.addons.gallery.urls import content_type
from tests.flywheel_cli.conftest import make_fake_run


# --------------------------------------------------------------------------- content types + CSP
def test_html_is_typed_html_not_guessed():
    assert content_type("a.html") == "text/html; charset=utf-8"
    assert content_type("a.htm") == "text/html; charset=utf-8"
    assert content_type("a.svg") == "image/svg+xml"
    assert {"text/html", "image/svg+xml"} == SANDBOXED_TYPES


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


# --------------------------------------------------------------------------- csv injection
def test_csv_safe_neutralises_formula_prefixes():
    assert csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert csv_safe("+1") == "'+1" and csv_safe("-1") == "'-1" and csv_safe("@cmd") == "'@cmd"
    assert csv_safe("a chair") == "a chair"
    assert csv_safe(3) == 3 and csv_safe(None) is None


def test_a_formula_prompt_round_trips_csv_escaped(tmp_path: Path):
    runs = tmp_path / "runs"
    make_fake_run(runs, "formula_prompt", prompt="=2+5|cmd", scores=(0.5, 0.9))
    text = export_csv(build_index([runs]).entries())
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == list(CSV_COLUMNS) and len(rows) == 2
    assert rows[1][CSV_COLUMNS.index("prompt")] == "'=2+5|cmd"
