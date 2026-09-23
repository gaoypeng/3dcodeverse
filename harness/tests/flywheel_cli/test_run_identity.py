"""Run identity (RunId): the slug rule, and that nested batteries no longer collide.

The real eval/bench/out tree holds 396 run dirs with only 169 distinct basenames — 211 are
literally ``run`` (compare_backends ``cells/<id>/<arm>/run``, ab_plan
``arms/<arm>/cells/<id>/<slug>/run``).  Keying anything on ``ws.root.name`` therefore
made exports rmtree each other, blew the SQLite PRIMARY KEY, collapsed gallery entries
and bled captions/pairs across runs.  These tests pin the fix end to end.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from codeverse3d.addons.dataset.export import export_samples, load_captions
from codeverse3d.addons.dataset.index import build_index as build_sqlite_index
from codeverse3d.addons.dataset.pairs import build_pairs
from codeverse3d.addons.dataset.sample import SampleError
from codeverse3d.contracts.run import RunId
from codeverse3d.record.record import FoundRun, battery_label, iter_runs, run_id_for, unique_files
from tests.flywheel_cli.conftest import make_fake_run


# --------------------------------------------------------------------------- slug rule
def test_flat_layout_keeps_the_plain_basename():
    """Back-compat: a rel with no parent directories is the basename, unchanged."""
    assert RunId(battery="runs", rel="wooden_chair_ab12cd34").slug == "wooden_chair_ab12cd34"
    # retry cells keep their .attemptN suffix — it distinguishes them from the original
    assert RunId(battery="h2h_scene_v1",
                 rel="h2h_b01_neon_alley.attempt1").slug == "h2h_b01_neon_alley.attempt1"


def test_a_runs_wrapper_is_structural_noise():
    assert RunId(battery="articulated_v1_flash", rel="runs/art_easy_laptop").slug == "art_easy_laptop"


def test_compare_backends_layout_slug_arm_cell():
    rid = RunId(battery="compare_v1_full",
                rel="cells/cmp_easy_stool/harness_api-agent_gemini_gemini-3.7-flash/run")
    assert rid.slug == "cmp_easy_stool__harness_api-agent_gemini_gemini-3.7-flash"
    assert rid.cell == "cmp_easy_stool"
    assert rid.arm == "harness_api-agent_gemini_gemini-3.7-flash"


def test_ab_plan_layout_slug_arm_cell_and_attempt_suffix():
    rid = RunId(battery="plan_loop",
                rel="C0/arms/control/cells/arch_hard_bay_window/"
                    "harness_api-agent_gemini_gemini-3.7-flash/run")
    assert rid.slug == "C0__control__arch_hard_bay_window__harness_api-agent_gemini_gemini-3.7-flash"
    assert rid.arm == "control" and rid.cell == "arch_hard_bay_window"
    retry = RunId(battery="plan_loop",
                  rel="heldout/arms/control/cells/comp_hard_drafting_table/"
                      "harness_api-agent_gemini_gemini-3.7-flash.attempt1/run")
    assert retry.slug.endswith("harness_api-agent_gemini_gemini-3.7-flash.attempt1")


def test_battery_label_and_run_id_for(tmp_path: Path):
    runs = tmp_path / "bench" / "out" / "static_v9" / "runs"
    (runs / "stool_a1").mkdir(parents=True)
    assert battery_label(runs) == "static_v9"
    rid = run_id_for(runs, runs / "stool_a1")
    assert rid.battery == "static_v9" and rid.rel == "stool_a1" and rid.slug == "stool_a1"


def test_unique_files_skips_subruns_below_the_root_not_above_it(tmp_path: Path):
    """A scan rooted INSIDE ``_assets`` used to return [] — the skip read absolute parts."""
    root = tmp_path / "scene" / "_assets" / "lamp"
    (root / "_cand" / "c1").mkdir(parents=True)
    (root / "record.json").write_text("{}")
    (root / "_cand" / "c1" / "record.json").write_text("{}")
    assert unique_files(root, "record.json") == [root / "record.json"]


# --------------------------------------------------------------------------- shared tree
def _nested_battery(tmp_path: Path) -> Path:
    """ONE root, two real runs whose directories are BOTH named ``run``."""
    root = tmp_path / "compare_v9"
    make_fake_run(root / "cells" / "cmp_a_stool" / "armx", "run", prompt="a stool")
    make_fake_run(root / "cells" / "cmp_b_lamp" / "armx", "run", prompt="a lamp")
    return root


def _colliding_root(tmp_path: Path) -> Path:
    """Two rels that legitimately reduce to the SAME slug ``x``."""
    root = tmp_path / "mixed"
    make_fake_run(root / "runs", "x")             # runs/x            -> slug x
    make_fake_run(root / "cells" / "x", "run")    # cells/x/run       -> slug x
    return root


def test_nested_run_identity_survives_every_flywheel_consumer(tmp_path: Path):
    """Build the colliding-basename corpus once, then exercise every reader."""
    root = _nested_battery(tmp_path)
    found = list(iter_runs(root))
    assert all(isinstance(f, FoundRun) for f in found)
    assert all(f.ws.root.name == "run" for f in found)  # the basename really is degenerate
    assert sorted(f.run_id.slug for f in found) == ["cmp_a_stool__armx", "cmp_b_lamp__armx"]

    # Export keeps both identities and their distinct payloads.
    out = tmp_path / "ds"
    rep = export_samples(root, out)
    assert rep.n_exported == 2 and rep.n_indexed == 2, rep.skipped
    a = out / "static_object" / "blender" / "cmp_a_stool__armx"
    b = out / "static_object" / "blender" / "cmp_b_lamp__armx"
    assert a.is_dir() and b.is_dir()
    assert json.loads((a / "meta.json").read_text())["prompt"] == "a stool"
    assert json.loads((b / "meta.json").read_text())["prompt"] == "a lamp"
    keys = {json.loads(line)["key"] for line in (out / "metadata.jsonl").read_text().splitlines()}
    assert keys == {"cmp_a_stool__armx", "cmp_b_lamp__armx"}

    # SQLite keeps the same rel/arm/cell mapping.
    db = tmp_path / "idx.sqlite"
    assert build_sqlite_index(root, db) == 2
    con = sqlite3.connect(db)
    rows = con.execute("SELECT slug, rel, arm, cell FROM runs ORDER BY slug").fetchall()
    con.close()
    assert rows == [
        ("cmp_a_stool__armx", "cells/cmp_a_stool/armx/run", "armx", "cmp_a_stool"),
        ("cmp_b_lamp__armx", "cells/cmp_b_lamp/armx/run", "armx", "cmp_b_lamp"),
    ]

    # Pair rows use the resolved slug, never the degenerate directory basename.
    out = tmp_path / "pairs.jsonl"
    n = build_pairs(root, out, min_delta=0.05)
    assert n == 2  # one preference pair per run (0.55 → 0.80)
    runs = {json.loads(line)["run"] for line in out.read_text().splitlines()}
    assert runs == {"cmp_a_stool__armx", "cmp_b_lamp__armx"}

    # Caption sidecars are equally isolated.
    from codeverse3d.addons.dataset.captions import caption_sample
    from tests.flywheel_cli.test_captions import GOOD
    from tests.orchestrator_tracks.fakes import FakeChatModel

    side = tmp_path / "caps"
    by_slug = {f.run_id.slug: f for f in found}
    a = by_slug["cmp_a_stool__armx"]
    b = by_slug["cmp_b_lamp__armx"]
    caption_sample(a.ws, a.record, "fake:fake", model=FakeChatModel([GOOD]),
                   out_dir=side, slug=a.run_id.slug)
    assert (side / "cmp_a_stool__armx.json").is_file()
    assert not (side / "run.json").exists(), "the degenerate basename side-car is the bleed"
    assert load_captions(a.ws, a.record, side, slug=a.run_id.slug)["detailed"] == GOOD["detailed"]
    assert load_captions(b.ws, b.record, side, slug=b.run_id.slug) == {}, \
        "the un-captioned run must not pick up its neighbour's side-car"


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
