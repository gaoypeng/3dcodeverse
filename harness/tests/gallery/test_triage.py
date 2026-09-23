"""Triage: the verdict breakdown (disjoint and exhaustive — no pass/fail since 2026-09-22), the hero
view, label humanisation, compare / CSV routing and the neighbour links."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

import pytest

from codeverse3d.addons.gallery.compare import (
    CSV_COLUMNS,
)
from codeverse3d.addons.gallery.index import build_index, hero_view
from codeverse3d.addons.gallery.model import (
    VERDICTS,
    humanize_view,
    summarize,
)
from codeverse3d.addons.gallery.page import render_index
from codeverse3d.addons.gallery.server import GalleryApp
from codeverse3d.addons.gallery.urls import UrlMaker


# --------------------------------------------------------------------------- breakdown
def test_the_buckets_are_disjoint_and_sum_to_n(gallery_tree: dict[str, Path]):
    entries = build_index([gallery_tree["runs"], gallery_tree["battery"]]).entries()
    s = summarize(entries)
    assert s.n == 6
    assert set(s.breakdown) == set(VERDICTS)
    assert sum(s.breakdown.values()) == s.n
    # the tree holds 4 judged runs and 2 with no usable record
    assert s.breakdown == {"judged": 4, "unjudged": 0, "error": 2}
    # every entry lands in exactly one bucket
    assert sorted(e.verdict for e in entries) == sorted(
        k for k, n in s.breakdown.items() for _ in range(n))
    by_slug = {e.slug: e for e in entries}   # a corrupt record and a missing one are errors
    assert by_slug["half_written"].verdict == by_slug["not_started"].verdict == "error"
    assert by_slug["half_written"].score is None



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
    assert hero_view(ws, rec, 1) == ("", "", 0) and hero_view(ws, rec, None) == ("", "", 0)


# --------------------------------------------------------------------------- labels
@pytest.mark.parametrize(("raw", "human"), [
    ("front_right_high", "Front Right High"),
    ("front_right_34", "Front Right ¾"),
    ("view_front_right_34.png", "Front Right ¾"),
    ("t=2.5s", "t = 2.5 s"),
    ("pose_rest", "Pose · rest"),
    ("articulation_sheet", "Articulation poses"),
    ("preview.gif", "Animated preview"),
    ("", ""),
])
def test_view_names_are_humanised(raw: str, human: str):
    assert humanize_view(raw) == human


# --------------------------------------------------------------------------- multi-select
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


# --------------------------------------------------------------------------- csv export
def test_export_csv_without_a_selection_exports_the_current_filter(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])
    r = app.route("/export.csv", {"verdict": "error"})
    assert r.content_type.startswith("text/csv") and "attachment" in r.headers["Content-Disposition"]
    body = r.body.decode()
    assert body.splitlines()[0] == ",".join(CSV_COLUMNS)
    assert len(body.strip().splitlines()) == 3         # header + the two unusable runs
    assert "half_written" in body and "not_started" in body


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
