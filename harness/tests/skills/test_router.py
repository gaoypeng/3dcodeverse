"""Routing: the table, the cap, the priorities, the coverage guarantee (T5).

Nobody declares ``skills: [...]``; if the derivation is wrong, the wrong sheet is in the
prompt and the cost is paid anyway.  So every law in design §5.2 is a test here.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from codeverse.conventions import LANGUAGE_FRAME
from codeverse.skills.model import EVIDENCE_INHERITED
from codeverse.skills.registry import QUIET_KINDS
from codeverse.skills.router import plan_signals, select, skills_for
from tests.skills.conftest import write_bundle

TRACK_OF = {
    "blender": "static_object", "cadquery": "static_object", "threejs": "static_object",
    "urdf_blender": "articulated_object", "scene_threejs": "scene",
    "glsl_shader": "graphics", "opengl_python": "graphics",
}


def part(name="Leg", instances=1, symmetry="none", children=()):
    return NS(name=name, instances=instances, symmetry=symmetry, children=list(children))


def static_plan(n=3, **kw):
    return NS(parts=[part(f"P{i}", **kw) for i in range(n)], summary="a chair", style_notes="")


# --------------------------------------------------------------------------- signals
def test_plan_signals_read_the_typed_fields():
    s = plan_signals(NS(parts=[part(instances=4), part(symmetry="mirror_x", children=[NS(name="Burr")])],
                        joints=[NS(type="revolute")], summary="", effects=[]))
    assert s["n_parts"] == 2 and s["multi_part"] is True
    assert s["has_instances"] and s["has_symmetry"] and s["has_assemblies"] and s["has_joints"]
    assert s["joint_types"] == ["revolute"]


def test_plan_signals_of_a_missing_plan_are_all_false_not_an_exception():
    s = plan_signals(None)
    assert s["n_parts"] == 0 and not s["multi_part"] and not s["has_custom_shader"]


def test_custom_shader_is_detected_from_effects_or_from_words():
    assert plan_signals(NS(effects=[NS(kind="glsl_material", description="water")]))["has_custom_shader"]
    assert plan_signals(NS(summary="a raymarched tunnel"))["has_custom_shader"]
    assert not plan_signals(NS(summary="a wooden chair", parts=[part()]))["has_custom_shader"]


# --------------------------------------------------------------------------- the laws
def test_every_track_language_pair_routes_at_least_one_skill_at_baseline(library):
    """Coverage is a test, not a hope (design §5.2 law 6)."""
    for language in LANGUAGE_FRAME:
        track = TRACK_OF[language]
        got = skills_for(track, language, "baseline", plan=static_plan(), library=library, allow_unverified=True)
        assert got, f"{track}/{language} routes nothing at baseline"


def test_no_input_combination_exceeds_the_cap(library):
    every_finding = ["connectivity/interpenetration", "contract/part_bbox", "contract/instance_bbox",
                     "joint_sweep/link_overlap", "motion_direction/wrong_axis", "scene_frames/dark_or_flat",
                     "scene_frames/camera_placement", "gl_frames/motion_or_detail", "lint/part_not_imported",
                     "shader/compile_or_binding"]
    kinds = ("baseline", "part", "detail", "refine", "repair", "rebuild", "rewrite", "env", "zone",
             "asset", "asset_fix", "compose", "reference")
    for language, track in TRACK_OF.items():
        for kind in kinds:
            for findings in ((), every_finding):
                got = select(track, language, kind, signals=plan_signals(static_plan(instances=2, symmetry="mirror_x")),
                             findings=findings, library=library, max_skills=5, allow_unverified=True)
                assert len(got) <= 5, (track, language, kind, [s.name for s in got])


def test_a_repair_round_spends_its_budget_on_what_broke(library):
    got = select("static_object", "blender", "repair",
                 signals=plan_signals(static_plan()),
                 findings=["connectivity/interpenetration", "contract/part_bbox"],
                 library=library, max_skills=2)
    assert [s.name for s in got] == ["cv3d-part-contact", "cv3d-bbox-contract"]
    assert all(s.gate_fired for s in got)
    assert "R2" in got[0].rules and "connectivity/interpenetration" in got[0].reason


def test_the_cap_cuts_the_low_priority_tail_not_the_gate_fired_head(library):
    full = select("articulated_object", "urdf_blender", "refine", signals=plan_signals(static_plan(instances=2)),
                  findings=["joint_sweep/link_overlap"], library=library, max_skills=99)
    capped = select("articulated_object", "urdf_blender", "refine", signals=plan_signals(static_plan(instances=2)),
                    findings=["joint_sweep/link_overlap"], library=library, max_skills=2)
    assert [s.name for s in capped] == [s.name for s in full[:2]]
    assert capped[0].priority >= capped[-1].priority


def test_quiet_kinds_attach_nothing_unless_a_gate_fired(library):
    """Law 4: asset / asset_fix / reference sessions are short and narrow.

    A gate-fired row still reaches them when its own kind filter allows it — R15/R17/R19
    are kind-agnostic by design, R2/R4 are not, and this pins both halves."""
    for kind in QUIET_KINDS:
        assert select("scene", "scene_threejs", kind, signals=plan_signals(static_plan()), library=library) == []
        fired = select("scene", "scene_threejs", kind, signals=plan_signals(static_plan()),
                       findings=["scene_frames/dark_or_flat"], library=library)
        assert [s.name for s in fired] == ["cv3d-scene-lighting"]
        # R2 is scoped to repair/refine/rebuild, so a connectivity finding does NOT
        # reopen an asset session
        assert select("static_object", "blender", kind, signals=plan_signals(static_plan()),
                      findings=["connectivity/floating_part"], library=library) == []


def test_inherited_unverified_bundles_are_off_until_the_switch_says_otherwise(library_dir):
    from codeverse.skills import all_skills

    write_bundle(library_dir, "cv3d-cadquery-forms", evidence=EVIDENCE_INHERITED)
    lib = all_skills(library_dir, strict=True)
    off = skills_for("static_object", "cadquery", "baseline", plan=static_plan(), library=lib)
    on = skills_for("static_object", "cadquery", "baseline", plan=static_plan(), library=lib, allow_unverified=True)
    assert "cv3d-cadquery-forms" not in [s.name for s in off]
    assert "cv3d-cadquery-forms" in [s.name for s in on]


def test_a_single_part_plan_does_not_get_the_contact_sheet(library):
    one = skills_for("static_object", "blender", "baseline", plan=static_plan(1), library=library)
    many = skills_for("static_object", "blender", "baseline", plan=static_plan(3), library=library)
    assert "cv3d-part-contact" not in [s.name for s in one]
    assert "cv3d-part-contact" in [s.name for s in many]


def test_repeats_skill_needs_instances_or_symmetry(library):
    plain = skills_for("static_object", "blender", "baseline", plan=static_plan(), library=library)
    mirrored = skills_for("static_object", "blender", "baseline", plan=static_plan(symmetry="mirror_x"), library=library)
    assert "cv3d-repeats-and-mirrors" not in [s.name for s in plain]
    assert "cv3d-repeats-and-mirrors" in [s.name for s in mirrored]


def test_language_rows_do_not_leak_across_languages(library):
    blender = [s.name for s in skills_for("static_object", "blender", "baseline", plan=static_plan(), library=library)]
    assert "cv3d-blender-forms" in blender and "cv3d-cadquery-forms" not in blender
    graphics = [s.name for s in skills_for("graphics", "glsl_shader", "baseline", library=library)]
    assert graphics == ["cv3d-glsl-craft"]


def test_scene_gate_findings_route_the_matching_scene_skill(library):
    for finding, want in (("scene_frames/dark_or_flat", "cv3d-scene-lighting"),
                          ("scene_frames/camera_placement", "cv3d-scene-composition"),
                          ("gl_frames/motion_or_detail", "cv3d-scene-motion")):
        got = select("scene", "scene_threejs", "repair", findings=[finding], library=library, max_skills=1)
        assert got and got[0].name == want, finding


def test_routing_is_deterministic_and_ordered_by_priority(library):
    args = dict(signals=plan_signals(static_plan(instances=2)), findings=["contract/instance_bbox"], library=library)
    runs = [[(s.name, s.priority) for s in select("static_object", "blender", "refine", **args)] for _ in range(5)]
    assert all(r == runs[0] for r in runs)
    assert [p for _, p in runs[0]] == sorted((p for _, p in runs[0]), reverse=True)


def test_a_route_row_without_a_bundle_is_skipped_not_a_crash(library_dir):
    from codeverse.skills import all_skills

    (library_dir / "cv3d-blender-forms" / "SKILL.md").unlink()
    lib = all_skills(library_dir)
    got = [s.name for s in skills_for("static_object", "blender", "baseline", plan=static_plan(), library=lib)]
    assert got and "cv3d-blender-forms" not in got


def test_selection_carries_the_rule_and_the_reason(library):
    got = select("static_object", "blender", "baseline", signals=plan_signals(static_plan()), library=library)
    by_name = {s.name: s for s in got}
    assert by_name["cv3d-part-contact"].rules == ("R1",)
    assert by_name["cv3d-part-contact"].reason.startswith("R1: ")
    assert not by_name["cv3d-part-contact"].gate_fired


@pytest.mark.parametrize("max_skills", [0, 1, 3])
def test_max_skills_is_honoured_exactly(library, max_skills):
    got = select("articulated_object", "urdf_blender", "baseline", signals=plan_signals(static_plan(instances=2)),
                 library=library, max_skills=max_skills)
    assert len(got) == min(max_skills, 5)
