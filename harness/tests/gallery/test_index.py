"""Index building over a synthetic runs tree (including a corrupt record)."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse3d.addons.gallery.index import build_index, entry_for_dir
from codeverse3d.addons.gallery.model import match, sort_entries, summarize


def test_a_card_links_the_picked_rounds_own_glb(tmp_path: Path):
    """artifacts/object.glb is the LAST round's: until a hand-over the card links the picked round's copy."""
    from codeverse3d.addons import select
    from tests.flywheel_cli.conftest import make_fake_run

    ws, _ = make_fake_run(tmp_path / "runs", "kept_rounds", scores=(0.9, 0.6))   # the pick is r00
    links = {ln.label: ln.rel for ln in entry_for_dir("runs", ws.root).links}
    assert links["glb"] == "artifacts/r00/object.glb"
    select.package(ws.root, 0)
    links = {ln.label: ln.rel for ln in entry_for_dir("runs", ws.root).links}
    assert links["glb"] == "deliverable/object.glb"


def test_filter_and_sort_and_summary(gallery_tree: dict[str, Path]):
    index = build_index([gallery_tree["runs"], gallery_tree["battery"]])
    entries = index.entries()
    assert [e.slug for e in sort_entries(entries, "score")][0] == "ctrl_med_toaster"  # 0.9
    assert [e.slug for e in sort_entries(entries, "name")] == sorted(e.slug for e in entries)
    by_cost = sort_entries(entries, "cost")
    assert by_cost[0].cost_usd >= by_cost[-1].cost_usd
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


# --------------------------------------------------------------------------- run identity
def test_nested_battery_runs_get_distinct_findable_slugs(tmp_path: Path):
    """Two compare-style runs whose dirs are both named ``run`` keep distinct, findable slugs."""
    from tests.flywheel_cli.conftest import make_fake_run

    root = tmp_path / "compare_v9"
    make_fake_run(root / "cells" / "cmp_a_stool" / "armx", "run", prompt="a stool")
    make_fake_run(root / "cells" / "cmp_b_lamp" / "armx", "run", prompt="a lamp")
    index = build_index([root])
    (section,) = index.sections
    assert sorted(e.slug for e in section.entries) == ["cmp_a_stool__armx", "cmp_b_lamp__armx"]
    a = index.find("compare_v9", "cmp_a_stool__armx")
    b = index.find("compare_v9", "cmp_b_lamp__armx")
    assert a is not None and a.prompt == "a stool"
    assert b is not None and b.prompt == "a lamp"
    assert a.key != b.key


def test_nested_eval_workspaces_are_not_counted_as_runs(tmp_path: Path):
    """A cell's ``eval/`` judge workspace (spec.json, no record.json) is not a run."""
    from tests.flywheel_cli.conftest import make_fake_run

    root = tmp_path / "compare_v9"
    make_fake_run(root / "cells" / "cmp_a_stool" / "armx", "run", prompt="a stool")
    eval_dir = root / "cells" / "cmp_a_stool" / "armx" / "eval"
    eval_dir.mkdir()
    (eval_dir / "spec.json").write_text(json.dumps({"prompt": "judge ws"}))
    (section,) = build_index([root]).sections
    assert [e.slug for e in section.entries] == ["cmp_a_stool__armx"]


def test_duplicate_slugs_within_a_root_are_disambiguated(tmp_path: Path):
    """Two rels that reduce to one slug follow the label#2 precedent — the viewer shows both."""
    from tests.flywheel_cli.conftest import make_fake_run

    root = tmp_path / "mixed"
    make_fake_run(root / "runs", "x", prompt="first")
    make_fake_run(root / "cells" / "x", "run", prompt="second")
    (section,) = build_index([root]).sections
    assert [e.slug for e in section.entries] == ["x", "x#2"]  # cells/x/run sorts first
    assert {e.prompt for e in section.entries} == {"first", "second"}
