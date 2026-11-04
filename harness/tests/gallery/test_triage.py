"""Triage: the verdict breakdown (disjoint and exhaustive — no pass/fail since 2026-09-22), the hero
view, label humanisation, compare / CSV routing and the neighbour links."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.addons.gallery.compare import (
    CSV_COLUMNS,
)
from codeverse3d.addons.gallery.model import (
    VERDICTS,
    summarize,
)
from codeverse3d.addons.gallery.page import render_index
from codeverse3d.addons.gallery.server import GalleryApp
from codeverse3d.addons.gallery.urls import UrlMaker


def test_breakdown_compare_and_csv_over_both_roots(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"], gallery_tree["battery"]])
    # the breakdown: disjoint and exhaustive
    entries = app.index.entries()
    s = summarize(entries)
    assert s.n == 6
    assert set(s.breakdown) == set(VERDICTS) and sum(s.breakdown.values()) == s.n
    assert s.breakdown == {"judged": 4, "unjudged": 0, "error": 2}
    by_slug = {e.slug: e for e in entries}   # a corrupt record and a missing one are errors
    assert by_slug["half_written"].verdict == by_slug["not_started"].verdict == "error"
    assert by_slug["half_written"].score is None

    # multi-select: the compare route renders only the selected runs
    r = app.route("/compare", {"runs": "runs/wooden_chair_ab12cd34,static_v9/ctrl_med_toaster"})
    assert r.status == 200
    page = r.body.decode()
    assert page.count("<td class='head'>") == 2
    assert "wooden_chair_ab12cd34" in page and "ctrl_med_toaster" in page
    assert "lamp_three" not in page
    r = app.route("/compare", {"runs": "static_v9/half_written,static_v9/not_started"})
    assert r.status == 200 and b"half_written" in r.body   # survives runs with no record

    # csv export without a selection exports the current filter
    r = app.route("/export.csv", {"verdict": "error"})
    assert r.content_type.startswith("text/csv") and "attachment" in r.headers["Content-Disposition"]
    body = r.body.decode()
    assert body.splitlines()[0] == ",".join(CSV_COLUMNS)
    assert len(body.strip().splitlines()) == 3         # header + the two unusable runs
    assert "half_written" in body and "not_started" in body


def test_hero_view_empty_compare_and_neighbours_over_one_root(gallery_tree: dict[str, Path]):
    app = GalleryApp([gallery_tree["runs"]])
    # the card shows one hero view, not the contact sheet
    entry = app.index.find("runs", "wooden_chair_ab12cd34")
    assert entry.hero.endswith("view_front.png")     # "front" is in HERO_PREFERENCE
    assert entry.n_views == 2 and entry.card_image == entry.hero
    markup = render_index(app.index, UrlMaker())
    assert "view_front.png" in markup

    page = app.route("/compare").body.decode()
    assert "nothing to compare" in page and app.route("/compare").status == 200

    # detail pages link to the neighbouring runs
    ordered = app.neighbours(entry)
    assert ordered[0] is None                          # highest score in the battery
    assert ordered[1].slug == "lamp_three"
    prev, nxt = app.neighbours(app.index.find("runs", "lamp_three"))
    assert prev.slug == "wooden_chair_ab12cd34" and nxt is None
    page = app.route("/run/runs/wooden_chair_ab12cd34").body.decode()
    assert "/run/runs/lamp_three" in page and "next →" in page
