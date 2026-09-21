"""Index building over a synthetic runs tree (including a corrupt record)."""

from __future__ import annotations

import json
from pathlib import Path

from codeverse.flywheel.record import battery_label
from codeverse.gallery.index import build_index, default_roots, entry_for_dir
from codeverse.gallery.model import match, sort_entries, summarize


def test_build_index_sections_and_states(gallery_tree: dict[str, Path]):
    index = build_index([gallery_tree["runs"], gallery_tree["battery"]])
    assert [s.label for s in index.sections] == ["runs", "static_v9"]
    assert index.build_ms >= 0
    states = {e.slug: e.state for e in index.entries()}
    assert states == {"lamp_three": "ok", "wooden_chair_ab12cd34": "ok", "art_easy_hinge": "ok",
                      "ctrl_med_toaster": "ok", "half_written": "broken", "not_started": "pending"}
    broken = index.find("static_v9", "half_written")
    assert broken is not None and "record.json" in broken.error
    assert broken.prompt == "a stool" and broken.track == "static_object"  # spec still read
    pending = index.find("static_v9", "not_started")
    assert pending is not None and pending.prompt == "a bookshelf"
    # a broken/pending run still offers its workspace, and nothing raised
    assert "workspace" in {link.label for link in broken.links}


def test_entry_fields_and_links(gallery_tree: dict[str, Path]):
    entry = entry_for_dir("runs", gallery_tree["runs"] / "wooden_chair_ab12cd34")
    assert entry.state == "ok" and entry.passed is True and entry.tier == "A"
    assert entry.score == 0.80 and entry.baseline_score == 0.55 and entry.rounds == 2
    assert entry.best_round == 1 and entry.minutes is not None
    assert entry.sheet == "artifacts/renders/r01/sheet.png"
    labels = [link.label for link in entry.links]
    assert labels[:2] == ["workspace", "record.json"]
    assert "src/" in labels and "sheet" in labels and "glb" in labels
    assert all(not Path(link.rel).is_absolute() for link in entry.links)  # every link is run-relative
    assert [r.index for r in entry.round_rows] == [0, 1]
    assert entry.round_rows[1].sheet == "artifacts/renders/r01/sheet.png"
    assert entry.key == "runs/wooden_chair_ab12cd34"


def test_corrupt_record_does_not_raise(tmp_path: Path):
    run = tmp_path / "runs" / "boom"
    run.mkdir(parents=True)
    (run / "record.json").write_bytes(b"\x00\x01not json")
    index = build_index([tmp_path / "runs"])
    (entry,) = index.entries()
    assert entry.state == "broken" and entry.error


def test_valid_json_but_not_a_record(tmp_path: Path):
    run = tmp_path / "runs" / "wrong_shape"
    run.mkdir(parents=True)
    (run / "record.json").write_text(json.dumps({"hello": "world"}))
    (entry,) = build_index([tmp_path / "runs"]).entries()
    assert entry.state == "broken" and "invalid record.json" in entry.error


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
    assert [e.slug for e in entries if match(e, {"pass": "fail"})] == ["lamp_three"]
    assert {e.slug for e in entries if match(e, {"battery": "static_v9"})} == {
        "art_easy_hinge", "ctrl_med_toaster", "half_written", "not_started"}
    s = summarize([e for e in entries if e.state == "ok"])
    assert s.n == 4 and s.n_judged == 4 and s.n_passed == 3
    assert s.pass_rate == 0.75 and s.mean_score is not None and s.median_score is not None
    assert s.total_usd > 0 and s.usd_per_pass == round(s.total_usd / 3, 4)
    facets = index.facets()
    assert facets["track"] == ["articulated_object", "static_object"]
    assert facets["battery"] == ["runs", "static_v9"]


def test_default_roots_and_labels(gallery_tree: dict[str, Path]):
    base = gallery_tree["root"]
    roots = default_roots(base)
    assert [battery_label(r) for r in roots] == ["runs", "static_v9"]
    assert default_roots(base / "nope") == []


def test_duplicate_labels_are_disambiguated(tmp_path: Path):
    a, b = tmp_path / "a" / "runs", tmp_path / "b" / "runs"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    index = build_index([a, b])
    assert [s.label for s in index.sections] == ["runs", "runs#2"]


# --------------------------------------------------------------------------- run identity
def test_nested_battery_runs_get_distinct_findable_slugs(tmp_path: Path):
    """Two compare-style runs whose dirs are BOTH named ``run`` used to collapse into
    one gallery key; each now carries its RunId slug and both are findable."""
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
    """A battery cell's ``eval/`` judge workspace has a spec.json but no record.json —
    it is not a run (52 phantom entries per battery under the old spec-or-record rule).
    A spec-only DIRECT child of the root is still a legitimate pending card."""
    from tests.flywheel_cli.conftest import make_fake_run

    root = tmp_path / "compare_v9"
    make_fake_run(root / "cells" / "cmp_a_stool" / "armx", "run", prompt="a stool")
    eval_dir = root / "cells" / "cmp_a_stool" / "armx" / "eval"
    eval_dir.mkdir()
    (eval_dir / "spec.json").write_text(json.dumps({"prompt": "judge ws"}))
    (section,) = build_index([root]).sections
    assert [e.slug for e in section.entries] == ["cmp_a_stool__armx"]


def test_duplicate_slugs_within_a_root_are_disambiguated(tmp_path: Path):
    """Two rels that reduce to the same slug follow the label#2 precedent — a viewer
    must still see both runs (exporters fail loudly instead; the gallery is read-only)."""
    from tests.flywheel_cli.conftest import make_fake_run

    root = tmp_path / "mixed"
    make_fake_run(root / "runs", "x", prompt="first")
    make_fake_run(root / "cells" / "x", "run", prompt="second")
    (section,) = build_index([root]).sections
    assert [e.slug for e in section.entries] == ["x", "x#2"]  # cells/x/run sorts first
    assert {e.prompt for e in section.entries} == {"first", "second"}
