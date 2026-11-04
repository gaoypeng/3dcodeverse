"""Index building over a synthetic runs tree (including a corrupt record)."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.addons.gallery.index import build_index
from codeverse3d.addons.gallery.model import match, sort_entries, summarize


def test_filter_and_sort_and_summary(gallery_tree: dict[str, Path]):
    index = build_index([gallery_tree["runs"], gallery_tree["battery"]])
    entries = index.entries()
    assert [e.slug for e in sort_entries(entries, "score")][0] == "ctrl_med_toaster"  # 0.9
    threejs = [e for e in entries if match(e, {"lang": "threejs"})]
    assert [e.slug for e in threejs] == ["lamp_three"]
    assert [e.slug for e in entries if match(e, {"q": "TOASTER"})] == ["ctrl_med_toaster"]
    assert {e.slug for e in entries if match(e, {"battery": "static_v9"})} == {
        "art_easy_hinge", "ctrl_med_toaster", "half_written", "not_started"}
    s = summarize([e for e in entries if e.state == "ok"])
    assert s.n == 4 and s.n_judged == 4 and s.mean_score is not None and s.median_score is not None
    assert s.total_usd > 0
    facets = index.facets()
    assert facets["track"] == ["articulated_object", "static_object"]
    assert facets["battery"] == ["runs", "static_v9"]


