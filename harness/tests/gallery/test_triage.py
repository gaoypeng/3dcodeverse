"""The triage affordances: the status breakdown, the hero view, label humanisation,
multi-select routing (compare / CSV) and the detail page's neighbour links.

The invariant worth a test is the arithmetic one: the four verdict buckets are
disjoint and exhaustive, so the strip's "n runs shown = a + b + c + d" is true for
*any* selection — including one holding half-written records.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from codeverse.gallery.compare import (
    CSV_COLUMNS,
    MAX_COMPARE,
    export_csv,
    parse_keys,
    render_compare,
)
from codeverse.gallery.index import build_index, hero_view
from codeverse.gallery.model import VERDICTS, humanize_view, match, summarize, verdict_breakdown
from codeverse.gallery.page import render_index
from codeverse.gallery.server import GalleryApp
from codeverse.gallery.urls import UrlMaker


# --------------------------------------------------------------------------- breakdown
def test_the_four_buckets_are_disjoint_and_sum_to_n(gallery_tree: dict[str, Path]):
    entries = build_index([gallery_tree["runs"], gallery_tree["battery"]]).entries()
    s = summarize(entries)
    assert s.n == 6
    assert set(s.breakdown) == set(VERDICTS)
    assert sum(s.breakdown.values()) == s.n
    # the tree holds 3 passing, 1 failing, and 2 runs with no usable record
    assert s.breakdown == {"passed": 3, "failed": 1, "unjudged": 0, "error": 2}
    # every entry lands in exactly one bucket
    assert sorted(e.verdict for e in entries) == sorted(
        k for k, n in s.breakdown.items() for _ in range(n))


def test_a_run_with_no_record_is_error_not_unjudged(gallery_tree: dict[str, Path]):
    entries = build_index([gallery_tree["battery"]]).entries()
    by_slug = {e.slug: e for e in entries}
    assert by_slug["half_written"].verdict == "error"   # corrupt record
    assert by_slug["not_started"].verdict == "error"    # no record at all
    assert by_slug["half_written"].passed is None       # ...and still unjudged in the old sense


def test_breakdown_of_an_empty_selection_is_all_zeroes():
    b = verdict_breakdown([])
    assert b == dict.fromkeys(VERDICTS, 0) and sum(b.values()) == 0
    assert summarize([]).n == 0


def test_the_page_states_the_breakdown_and_it_adds_up(gallery_tree: dict[str, Path]):
    index = build_index([gallery_tree["runs"], gallery_tree["battery"]])
    markup = render_index(index, UrlMaker())
    for bucket in VERDICTS:
        assert f"id='vc-{bucket}'" in markup
    assert "runs shown" in markup
    # the old un-addable "pass rate / not ok" pair of tiles is gone
    assert "PASS RATE" not in markup.upper()


# --------------------------------------------------------------------------- verdict filter
@pytest.mark.parametrize(("verdict", "n"), [("passed", 3), ("failed", 1), ("error", 2),
                                            ("unjudged", 0)])
def test_verdict_filter_selects_exactly_its_bucket(gallery_tree: dict[str, Path], verdict: str, n: int):
    entries = build_index([gallery_tree["runs"], gallery_tree["battery"]]).entries()
    kept = [e for e in entries if match(e, {"verdict": verdict})]
    assert len(kept) == n and all(e.verdict == verdict for e in kept)


def test_verdict_filter_reaches_the_api_and_the_page(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])
    payload = json.loads(app.route("/api/runs", {"verdict": "error"}).body)
    assert payload["n"] == 2 and {r["state"] for r in payload["runs"]} == {"broken", "pending"}
    page = app.route("/", {"verdict": "failed"}).body.decode()
    assert "<option value='failed' selected>" in page


# --------------------------------------------------------------------------- hero view
def test_the_card_shows_one_hero_view_not_the_contact_sheet(gallery_tree: dict[str, Path]):
    entry = next(e for e in build_index([gallery_tree["runs"]]).entries()
                 if e.slug == "wooden_chair_ab12cd34")
    assert entry.hero.endswith("view_front.png")     # "front" is in HERO_PREFERENCE
    assert entry.hero_label == "Front"
    assert entry.n_views == 2
    assert entry.sheet.endswith("sheet.png") and entry.card_image == entry.hero
    markup = render_index(build_index([gallery_tree["runs"]]), UrlMaker())
    assert "view_front.png" in markup
    assert "⊞ 2" in markup                            # the sheet is one disclosure away


def test_hero_falls_back_to_the_sheet_when_a_round_has_no_views(tmp_path: Path):
    from tests.flywheel_cli.conftest import make_fake_run

    ws, rec = make_fake_run(tmp_path / "runs", "noviews")
    for rnd in rec.rounds:
        rnd.renders = None
    assert hero_view(ws, rec) == ("", "", 0)


# --------------------------------------------------------------------------- labels
@pytest.mark.parametrize(("raw", "human"), [
    ("front_right_high", "Front Right High"),
    ("back_left_low", "Back Left Low"),
    ("bottom", "Bottom"),
    ("front_right_34", "Front Right ¾"),
    ("view_front_right_34.png", "Front Right ¾"),
    ("back_left_34", "Back Left ¾"),
    ("low_front_left", "Low Front Left"),
    ("front", "Front"),
    ("t=2.5s", "t = 2.5 s"),
    ("t=0s", "t = 0 s"),
    ("pose_rest", "Pose · rest"),
    ("pose_door_hinge@upper", "Pose · door_hinge@upper"),
    ("articulation_sheet", "Articulation poses"),
    ("preview.gif", "Animated preview"),
    ("", ""),
])
def test_view_names_are_humanised(raw: str, human: str):
    assert humanize_view(raw) == human


def test_render_tiles_use_a_gradient_caption_not_a_black_bar(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"]])
    page = app.route("/run/runs/wooden_chair_ab12cd34").body.decode()
    assert "<figcaption>Front</figcaption>" in page and "<figcaption>Top</figcaption>" in page
    assert "linear-gradient(to top,var(--overlay),transparent)" in page


# --------------------------------------------------------------------------- multi-select
def test_parse_keys_keeps_order_and_drops_duplicates():
    assert parse_keys("a/b, c/d ,a/b") == ["a/b", "c/d"]
    assert parse_keys("") == [] and parse_keys("  ,  ") == []


def test_compare_route_renders_only_the_selected_runs(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])
    r = app.route("/compare", {"runs": "runs/wooden_chair_ab12cd34,static_v9/ctrl_med_toaster"})
    assert r.status == 200
    page = r.body.decode()
    assert page.count("<td class='head'>") == 2
    assert "wooden_chair_ab12cd34" in page and "ctrl_med_toaster" in page
    assert "lamp_three" not in page
    assert "Δ vs baseline" in page
    assert "never starts, resumes or deletes" in page   # the read-only stance is stated


def test_compare_of_nothing_explains_itself(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"]])
    page = app.route("/compare").body.decode()
    assert "nothing to compare" in page and app.route("/compare").status == 200


def test_compare_reports_unknown_keys_and_caps_the_width(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])
    page = app.route("/compare", {"runs": "runs/wooden_chair_ab12cd34,runs/ghost"}).body.decode()
    assert "unknown: runs/ghost" in page
    many = ",".join(["runs/wooden_chair_ab12cd34"] * 3)  # dedup keeps one
    assert app.route("/compare", {"runs": many}).status == 200
    entries = app.index.entries()
    assert len(render_compare(entries[:MAX_COMPARE], UrlMaker()).split("<td class='head'>")) - 1 \
        == min(MAX_COMPARE, len(entries))


def test_query_links_survive_a_duplicate_root_label(gallery_tree: dict[str, Path]):
    """``build_index`` mints ``runs#2``; an unquoted ``#`` ends the URL in the browser."""
    app = GalleryApp([gallery_tree["runs"], gallery_tree["runs"]])
    key = "runs#2/wooden_chair_ab12cd34"
    compare = app.route("/compare", {"runs": key}).body.decode()
    href = compare.split("href='/export.csv?", 1)[1].split("'", 1)[0]
    assert "#" not in href and parse_qs(href) == {"runs": [key]}
    detail = app.route("/run/runs#2/wooden_chair_ab12cd34").body.decode()
    assert "href='/?battery=runs%232'" in detail


def test_compare_survives_a_run_with_no_record(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["battery"]])
    r = app.route("/compare", {"runs": "static_v9/half_written,static_v9/not_started"})
    assert r.status == 200 and b"half_written" in r.body


def test_bulk_bar_is_offered_on_the_server_but_not_in_the_static_build(gallery_tree: dict[str, Path]):
    from codeverse.gallery.page import render_static

    index = build_index([gallery_tree["runs"]])
    served = render_index(index, UrlMaker())
    assert "id='selbar'" in served and "id='sel-compare'" in served
    assert "no re-run from the browser" in served      # the refusal is written down
    assert "id='selbar'" not in render_static(index, embed=False)


# --------------------------------------------------------------------------- csv export
def test_export_csv_route_and_columns(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])
    r = app.route("/export.csv", {"runs": "runs/wooden_chair_ab12cd34"})
    assert r.status == 200 and r.content_type.startswith("text/csv")
    assert "attachment" in r.headers["Content-Disposition"]
    lines = r.body.decode().strip().splitlines()
    assert lines[0] == ",".join(CSV_COLUMNS) and len(lines) == 2
    assert "wooden_chair_ab12cd34" in lines[1] and ",passed," in lines[1]


def test_export_csv_without_a_selection_exports_the_current_filter(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])
    body = app.route("/export.csv", {"verdict": "error"}).body.decode()
    assert len(body.strip().splitlines()) == 3         # header + the two unusable runs
    assert "half_written" in body and "not_started" in body


def test_csv_quotes_a_hostile_prompt(tmp_path: Path):
    from tests.flywheel_cli.conftest import make_fake_run

    runs = tmp_path / "runs"
    make_fake_run(runs, "comma", prompt='a chair, "quoted", \nnewline')
    text = export_csv(build_index([runs]).entries())
    assert len(list(__import__("csv").reader(__import__("io").StringIO(text)))) == 2


# --------------------------------------------------------------------------- neighbours
def test_detail_pages_link_to_the_neighbouring_runs(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"]])
    ordered = app.neighbours(app.index.find("runs", "wooden_chair_ab12cd34"))
    assert ordered[0] is None                          # highest score in the battery
    assert ordered[1].slug == "lamp_three"
    prev, nxt = app.neighbours(app.index.find("runs", "lamp_three"))
    assert prev.slug == "wooden_chair_ab12cd34" and nxt is None
    page = app.route("/run/runs/wooden_chair_ab12cd34").body.decode()
    assert "/run/runs/lamp_three" in page and "next →" in page


def test_neighbours_of_a_run_outside_the_index_are_empty(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"]])
    entry = app.index.find("runs", "lamp_three").model_copy(update={"battery": "nope"})
    assert app.neighbours(entry) == (None, None)
