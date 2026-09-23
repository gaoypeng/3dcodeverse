"""Run identity (RunId): the slug rule — 211 of 396 recorded run dirs are literally named ``run``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codeverse3d.addons.dataset.export import export_samples
from codeverse3d.addons.dataset.index import build_index as build_sqlite_index
from codeverse3d.addons.dataset.sample import SampleError
from codeverse3d.contracts.run import RunId
from codeverse3d.record.record import unique_files
from tests.flywheel_cli.conftest import make_fake_run


def test_the_slug_rule_and_the_battery_layouts():
    for battery, rel, slug in [
        ("runs", "wooden_chair_ab12cd34", "wooden_chair_ab12cd34"),
        ("h2h_scene_v1", "h2h_b01_neon_alley.attempt1", "h2h_b01_neon_alley.attempt1"),   # a retry keeps its suffix
        ("articulated_v1_flash", "runs/art_easy_laptop", "art_easy_laptop"),               # runs/ is noise
        ("compare_v1_full", "cells/cmp_easy_stool/harness_x/run", "cmp_easy_stool__harness_x"),
    ]:
        assert RunId(battery=battery, rel=rel).slug == slug, rel
    # compare_backends: cell then arm
    rid = RunId(battery="compare_v1_full", rel="cells/cmp_easy_stool/harness_x/run")
    assert (rid.cell, rid.arm) == ("cmp_easy_stool", "harness_x")
    # ab_plan: slug, arm, cell and the attempt suffix
    rid = RunId(battery="plan_loop",
                rel="C0/arms/control/cells/arch_hard_bay_window/"
                    "harness_api-agent_gemini_gemini-3.7-flash/run")
    assert rid.slug == "C0__control__arch_hard_bay_window__harness_api-agent_gemini_gemini-3.7-flash"
    assert rid.arm == "control" and rid.cell == "arch_hard_bay_window"
    retry = RunId(battery="plan_loop",
                  rel="heldout/arms/control/cells/comp_hard_drafting_table/"
                      "harness_api-agent_gemini_gemini-3.7-flash.attempt1/run")
    assert retry.slug.endswith("harness_api-agent_gemini_gemini-3.7-flash.attempt1")


def test_unique_files_skips_subruns_below_the_root_not_above_it(tmp_path: Path):
    """A scan rooted inside ``_assets`` once returned [] — the skip read absolute parts."""
    root = tmp_path / "scene" / "_assets" / "lamp"
    (root / "_cand" / "c1").mkdir(parents=True)
    (root / "record.json").write_text("{}")
    (root / "_cand" / "c1" / "record.json").write_text("{}")
    assert unique_files(root, "record.json") == [root / "record.json"]


# --------------------------------------------------------------------------- shared tree
def _colliding_root(tmp_path: Path) -> Path:
    """Two rels that legitimately reduce to the SAME slug ``x``."""
    root = tmp_path / "mixed"
    make_fake_run(root / "runs", "x")             # runs/x            -> slug x
    make_fake_run(root / "cells" / "x", "run")    # cells/x/run       -> slug x
    return root


def test_slug_collisions_fail_before_export_or_index_publication(tmp_path: Path):
    root = _colliding_root(tmp_path)
    out = tmp_path / "ds"
    with pytest.raises(SampleError, match="duplicate sample id") as export_error:
        export_samples(root, out)
    msg = str(export_error.value)
    assert "cells/x/run" in msg and "runs/x" in msg, "both run dirs must be named"
    assert list(out.rglob("meta.json")) == [], "nothing may be written on a collision"

    with pytest.raises(sqlite3.IntegrityError) as index_error:
        build_sqlite_index(root, tmp_path / "collision.sqlite")
    msg = str(index_error.value)
    assert "duplicate run slug 'x'" in msg
    assert "cells/x/run" in msg and "runs/x" in msg
