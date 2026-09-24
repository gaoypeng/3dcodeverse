"""Routing on a synthetic library: which sheet each plan signal and gate finding attaches (T5, design §5.2)."""

from __future__ import annotations

from types import SimpleNamespace as NS

from codeverse3d.skills.model import EVIDENCE_INHERITED
from codeverse3d.skills.registry import QUIET_KINDS, plan_signals, select
from tests.skills.conftest import write_bundle


def part(name="Leg", instances=1, symmetry="none", children=()):
    return NS(name=name, instances=instances, symmetry=symmetry, children=list(children))


def static_plan(n=3, **kw):
    return NS(parts=[part(f"P{i}", **kw) for i in range(n)], summary="a chair", style_notes="")


def skills_for(track, language, kind, *, plan=None, **kw):
    return [s.skill for s in select(track, language, kind, signals=plan_signals(plan), **kw)]


def test_a_repair_round_spends_its_budget_on_what_broke(library):
    got = select("static_object", "blender", "repair",
                 signals=plan_signals(static_plan()),
                 findings=["connectivity/penetration", "contract/part_bbox"],
                 library=library, max_skills=2)
    assert [s.name for s in got] == ["c3d-part-contact", "c3d-bbox-contract"]
    assert all(s.gate_fired for s in got)
    assert "R2" in got[0].rules and "connectivity/penetration" in got[0].reason


def test_quiet_kinds_attach_nothing_unless_a_gate_fired(library):
    """Law 4: quiet sessions get only kind-agnostic gate-fired rows (R15/R17/R19, not R2/R4)."""
    for kind in QUIET_KINDS:
        assert select("scene", "scene_threejs", kind, signals=plan_signals(static_plan()), library=library) == []
        fired = select("scene", "scene_threejs", kind, signals=plan_signals(static_plan()),
                       findings=["scene_frames/dark_frame"], library=library)
        assert [s.name for s in fired] == ["c3d-scene-lighting"]
        # R2 is scoped to repair/refine/rebuild, so a connectivity finding does NOT
        # reopen an asset session
        assert select("static_object", "blender", kind, signals=plan_signals(static_plan()),
                      findings=["connectivity/floating"], library=library) == []


def test_inherited_unverified_bundles_are_off_until_the_switch_says_otherwise(library_dir):
    from codeverse3d.skills import all_skills

    write_bundle(library_dir, "c3d-cadquery-forms", evidence=EVIDENCE_INHERITED)
    lib = all_skills(library_dir, strict=True)
    off = skills_for("static_object", "cadquery", "baseline", plan=static_plan(), library=lib)
    on = skills_for("static_object", "cadquery", "baseline", plan=static_plan(), library=lib, allow_unverified=True)
    assert "c3d-cadquery-forms" not in [s.name for s in off]
    assert "c3d-cadquery-forms" in [s.name for s in on]


def test_scene_gate_findings_route_the_matching_scene_skill(library):
    for finding, want in (("scene_frames/dark_frame", "c3d-scene-lighting"),
                          ("scene_frames/camera_below_high_ground", "c3d-scene-composition"),
                          ("scene_frames/no_motion", "c3d-scene-motion")):
        got = select("scene", "scene_threejs", "repair", findings=[finding], library=library, max_skills=1)
        assert got and got[0].name == want, finding
