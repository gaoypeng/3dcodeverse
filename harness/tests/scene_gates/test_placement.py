"""scene_placement gate (2026-08-26): census placement table → findings, hints, caps, routing.

Pure python over a fake census dict — the node-level measurement is exercised in
``tests/scene_runtime/test_placement_census.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from codeverse.contracts.artifacts import BuildResult, Severity
from codeverse.contracts.common import Language
from codeverse.contracts.plan import BBox, CameraPlan, ScenePlan, ZonePlan
from codeverse.judges.caps import apply_caps
from codeverse.judges.rubrics import load_rubric
from codeverse.orchestrator import build_refine_instructions
from codeverse.spatial.scene_placement import (
    GATE,
    check_placement,
    infer_indoor,
    placement_findings,
    placement_gate_safe,
    placement_table_text,
)
from codeverse.tracks.prompting import file_for_target_factory
from codeverse.tracks.scene import ScenePipeline


def _row(name, zone="Yard", gap=0.0, support="Ground", sunk=0.0, into="", water=False, attached=(), h=1.0, exempt=""):
    return {"name": name, "zone": zone, "exempt": exempt, "meshes": 1, "columns": 3,
            "bbox": {"min": [0, 0, 0], "max": [1, h, 1], "size": [1, h, 1]}, "ground_gap_m": gap, "support": support,
            "sunk_m": sunk, "sunk_into": into, "on_water": water, "attached": list(attached)}


def _table(*rows, pairs=(), **over):
    t = {"assets": list(rows), "interpenetrations": list(pairs), "total": len(rows),
         "checked": sum(1 for r in rows if not r["exempt"]), "truncated": False, "exempt": {}, "ground_y": 0.0, "notes": []}
    t.update(over)
    return t


def _by_kind(report, kind):
    return [f for f in report.findings if f.data.get("kind") == kind]


def test_supported_and_exempt_rows_pass_with_a_summary_line():
    r = placement_findings(_table(_row("Crate"), _row("Bird", exempt="free"), _row("Ground", zone="Environment", exempt="backdrop")))
    assert r.gate == GATE and r.passed
    assert [f.data["kind"] for f in r.findings] == ["summary"]
    assert "1 assets checked (3 placed" in r.findings[0].message and "0 floating, 0 sunken" in r.findings[0].message


def test_floating_severity_by_gap_then_by_count():
    r = placement_findings(_table(_row("Lantern", gap=0.3), _row("Cup", gap=0.08, support="Table")))
    fl = _by_kind(r, "floating")
    assert {f.target: f.severity for f in fl} == {"Yard/Lantern": Severity.ERROR, "Yard/Cup": Severity.WARN}
    lantern = next(f for f in fl if f.target == "Yard/Lantern")
    assert "floating 0.30 m above Ground" in lantern.message and "lower Yard/Lantern by 0.30 m" in lantern.fix_hint
    assert "userData.placement = 'free'" in lantern.fix_hint
    assert not r.passed
    # more than FLOATING_ERROR_COUNT small hovers → every one is an ERROR
    many = placement_findings(_table(*[_row(f"P{i}", gap=0.07) for i in range(4)]))
    assert all(f.severity == Severity.ERROR for f in _by_kind(many, "floating")) and not many.passed


def test_floating_but_attached_is_only_a_warning():
    r = placement_findings(_table(_row("Sign", gap=0.9, attached=("Post",))))
    (f,) = _by_kind(r, "floating")
    assert f.severity == Severity.WARN and "touches Post" in f.message and "mounted on Post" in f.fix_hint
    assert r.passed


def test_indoor_tolerance_is_tighter():
    t = _table(_row("Vase", gap=0.03, support="Table"))
    assert _by_kind(placement_findings(t, indoor=False), "floating") == []
    assert _by_kind(placement_findings(t, indoor=False), "unsupported")[0].severity == Severity.WARN
    assert _by_kind(placement_findings(t, indoor=True), "floating")[0].severity == Severity.WARN
    assert infer_indoor("a cosy cabin interior at dusk") and not infer_indoor("mountain meadow at dawn")


def test_sunken_thresholds_height_fraction_and_name_words():
    r = placement_findings(_table(
        _row("Bench", sunk=0.15, into="Terrain", h=0.9),          # WARN: > 0.10 m
        _row("Statue", sunk=0.35, into="Terrain", h=2.0),         # WARN: > 0.30 m but only 17 % of its height
        _row("Lantern", sunk=0.4, into="Terrain", h=0.6),         # ERROR: > 0.30 m and > 25 %
        _row("Rock", sunk=0.45, into="Terrain", h=1.2),           # starter-scene rock: 37 % buried is fine
        _row("Boulder", sunk=0.8, into="Terrain", h=1.0),         # 80 % buried → ERROR even for a rock
        _row("PondBasin", sunk=1.4, into="Terrain", h=1.2),       # dug in by definition
        _row("Jetty", sunk=0.97, into="Terrain", h=1.1, water=True),   # posts in the pond bed
    ))
    sev = {f.target: f.severity for f in _by_kind(r, "sunken")}
    assert sev == {"Yard/Bench": Severity.WARN, "Yard/Statue": Severity.WARN, "Yard/Lantern": Severity.ERROR, "Yard/Boulder": Severity.ERROR}
    lantern = next(f for f in r.findings if f.target == "Yard/Lantern")
    assert "sunken 0.40 m into Terrain" in lantern.message and "raise Yard/Lantern by 0.40 m" in lantern.fix_hint


def test_interpenetration_pairs_and_error_above_sixty_percent():
    pairs = [{"a": "CrateA", "b": "CrateB", "zone_a": "Yard", "zone_b": "Yard", "aabb_overlap": 0.5, "inside_frac": 0.25, "samples": 24},
             {"a": "Post", "b": "Wall", "zone_a": "Yard", "zone_b": "House", "aabb_overlap": 0.9, "inside_frac": 0.6, "samples": 16}]
    r = placement_findings(_table(_row("CrateA"), _row("CrateB"), pairs=pairs))
    inter = _by_kind(r, "interpenetration")
    # worst first: ERROR before WARN
    assert [(f.target, f.severity) for f in inter] == [("Yard/Post", Severity.ERROR), ("Yard/CrateA", Severity.WARN)]
    assert "interpenetration: Yard/CrateA and Yard/CrateB overlap (50%" in inter[1].message
    assert "move Yard/Post out of House/Wall" in inter[0].fix_hint


def test_findings_are_capped_per_kind_and_the_summary_counts_them():
    r = placement_findings(_table(*[_row(f"Pebble{i}", gap=0.06 + i * 0.01) for i in range(12)]))
    fl = _by_kind(r, "floating")
    assert len(fl) == 8 and fl[0].data["gap_m"] >= fl[-1].data["gap_m"]     # worst first
    assert any(f.data.get("kind") == "truncated" and "4 more floating" in f.message for f in r.findings)
    assert "8 floating" in r.findings[0].message


def test_probe_error_is_a_warning_that_passes():
    r = placement_findings({"error": "boom"})
    assert r.passed and r.findings[0].severity == Severity.WARN and "placement probe failed: boom" in r.findings[0].message


def test_scene_v1_floating_part_cap_fires_on_a_placement_error():
    rubric = load_rubric("scene_v1")
    floating = placement_findings(_table(_row("Lantern", gap=0.3)))
    res = apply_caps(rubric, 0.9, [floating], {}, [])
    assert res.overall == 0.6 and {c.rule for c in res.caps_applied} == {"floating_part", "floating_or_sunken_asset"}
    assert "Lantern" in res.caps_applied[0].evidence
    sunken = placement_findings(_table(_row("Lantern", sunk=0.4, into="Terrain", h=0.6)))
    assert apply_caps(rubric, 0.9, [sunken], {}, []).overall == 0.6
    # WARN-level findings (a mounted sign, a small hover) never cap
    warn = placement_findings(_table(_row("Sign", gap=0.9, attached=("Post",)), _row("Cup", gap=0.08)))
    assert apply_caps(rubric, 0.9, [warn], {}, []).overall == 0.9


def _plan():
    bb = BBox(center=(0, 0, 0), extents=(10, 5, 10))
    return ScenePlan(title="t", summary="s", setting="meadow", mood="calm", bounds=bb, environment="sunny",
                     zones=[ZonePlan(name="Yard", description="d", bbox=bb, contents=[]), ZonePlan(name="House", description="d", bbox=bb, contents=[])],
                     assets=[], cameras=[CameraPlan(name="overview", position=(1, 1, 1), look_at=(0, 0, 0), fov=50, purpose="p")],
                     animation=[], effects=[])


def test_error_findings_become_zone_routed_refine_tasks_one_per_asset():
    plan = _plan()
    r = placement_findings(_table(_row("Lantern", gap=0.3), _row("Bench", gap=0.4), _row("Vase", zone="House", gap=0.5)))
    ctx = SimpleNamespace(runtime=SimpleNamespace(), plan=plan, language=Language.SCENE_THREEJS)
    tasks = build_refine_instructions(None, [r], [], plan, file_for_target=file_for_target_factory(ctx))
    assert sorted((t.target, tuple(t.files)) for t in tasks) == [("House/Vase", ("src/zones/house.js",)), ("Yard/Bench", ("src/zones/yard.js",)),
                                                                 ("Yard/Lantern", ("src/zones/yard.js",))]
    assert all(t.kind == f"gate:{GATE}" and "lower " in t.instruction for t in tasks)


def test_pipeline_gates_append_placement_after_census_and_never_raise():
    plan = _plan()
    ctx = SimpleNamespace(runtime=SimpleNamespace(), plan=plan)
    build = BuildResult(ok=True, language="scene_threejs", census={"totals": {"meshes": 3}, "placement": _table(_row("Lantern", gap=0.3))})
    gates = ScenePipeline().gates(ctx, 0, build, None)
    assert [g.gate for g in gates] == ["scene_census", GATE] and not gates[-1].passed
    # no table (scene did not boot / old driver) → no placement gate at all
    assert [g.gate for g in ScenePipeline().gates(ctx, 0, BuildResult(ok=False, language="scene_threejs", census={}), None)] == []
    # a broken table is a WARN, not an exception
    broken = placement_gate_safe({"placement": {"assets": [{"no_name": 1}]}}, plan=plan)
    assert broken is not None and broken.passed and "placement probe failed" in broken.findings[0].message
    assert placement_gate_safe({}, plan=plan) is None


def test_check_placement_reads_the_last_census_and_the_table_text(tmp_path):
    from codeverse.workspace import Workspace

    ws = Workspace(tmp_path / "run").create()
    ws.artifacts.mkdir(parents=True, exist_ok=True)
    table = _table(_row("Lantern", gap=0.3), _row("Rock", sunk=0.3, into="Terrain", h=1.0))
    (ws.artifacts / "census.json").write_text(json.dumps({"totals": {}, "placement": table}))
    ws.plan_path.write_text(json.dumps({"setting": "a cabin interior", "environment": "", "title": "x"}))
    r = check_placement(ws)
    assert not r.passed and "indoor tolerance 2 cm" in r.findings[0].message
    text = placement_table_text(table)
    assert "Yard/Lantern | +0.300 | Ground | 0.000 | - | - | -" in text and "Yard/Rock | +0.000 | Ground | 0.300 | Terrain" in text
