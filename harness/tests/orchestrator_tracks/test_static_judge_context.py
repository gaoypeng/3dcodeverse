"""The static track hands the plan's attach_to pairs to the connectivity gate, spelled in the
GLB's part names, so the gate's contact ledger can list each planned join as contact/open
(the judge's assembly_fit ground truth, ``prompt_builder.gates_section``)."""

from __future__ import annotations

from codeverse.contracts.artifacts import Measurement, PartMeasure
from codeverse.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse.spatial.contract import planned_joins
from codeverse.tracks.static_object import ObjectPipeline
from tests.orchestrator_tracks.test_integrations2 import _static_run


def _measurement(parts: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]]) -> Measurement:
    rows = [PartMeasure(name=n, bbox_min=lo, bbox_max=hi) for n, (lo, hi) in parts.items()]
    return Measurement(bbox_min=(0, 0, 0), bbox_max=(1, 1, 1), extents=(1, 1, 1), center=(0.5, 0.5, 0.5),
                       tri_count=12 * len(rows), n_meshes=len(rows), n_islands=len(rows), parts=rows)


def test_track_passes_attach_to_pairs_to_the_connectivity_gate(tmp_path, chair_plan, settings):
    _rec, _ws, _judge, services = _static_run(tmp_path, chair_plan, settings)
    # the fake build exports one node per plan part under the plan's own name (no instance copies)
    assert services.planned_edges == [[("FrontLeg", ("Seat",)), ("BackLeg", ("Seat",)), ("Backrest", ("BackLeg",)), ("Armrest", ("BackLeg",))]]


def test_planned_joins_spell_instances_as_the_glb_does_and_pair_every_touching_parent_copy(chair_plan):
    """Counted 2026-08-30 over the 342 stored static rounds with attach_to edges: 823 of 2 568
    edges name an instance part (``FrontLeg`` for ``FrontLeg_0``/``FrontLeg_1``).  Each child copy
    joins every copy of its parent it touches (the backrest spans both back legs), else the
    nearest — the cross product would call the far apron/leg pair of every chair OPEN."""
    leg = lambda x, z: ((x - 0.02, 0.0, z - 0.02), (x + 0.02, 0.41, z + 0.02))  # noqa: E731
    m = _measurement({
        "Seat": ((-0.21, 0.41, -0.2), (0.21, 0.45, 0.2)),
        "FrontLeg_0": leg(0.17, 0.16), "FrontLeg_1": leg(-0.17, 0.16),
        "BackLeg_0": ((0.15, 0.0, -0.18), (0.19, 0.82, -0.14)), "BackLeg_1": ((-0.19, 0.0, -0.18), (-0.15, 0.82, -0.14)),
        "Backrest": ((-0.2, 0.66, -0.185), (0.2, 0.78, -0.155)),
        "Armrest_0": ((0.18, 0.635, -0.17), (0.22, 0.665, 0.18)), "Armrest_1": ((-0.22, 0.635, -0.17), (-0.18, 0.665, 0.18)),
    })
    edges = planned_joins(chair_plan, m)
    assert edges == [('FrontLeg_0', ('Seat',)), ('FrontLeg_1', ('Seat',)), ('BackLeg_0', ('Seat',)), ('BackLeg_1', ('Seat',)), ('Backrest', ('BackLeg_0', 'BackLeg_1')), ('Armrest_0', ('BackLeg_0',)), ('Armrest_1', ('BackLeg_1',))]
    assert "BackLeg_1" not in dict(edges)["Armrest_0"]


def test_planned_joins_drop_what_the_mesh_does_not_have():
    plan = StaticPlan(
        object_name="Stool", summary="a stool", overall_bbox=BBox(center=(0, 0.2, 0), extents=(0.3, 0.4, 0.3)),
        parts=[PartPlan(name="Top", role="top", description="disc", bbox=BBox(center=(0, 0.39, 0), extents=(0.3, 0.02, 0.3))),
               PartPlan(name="Leg", role="leg", description="rod", bbox=BBox(center=(0, 0.19, 0), extents=(0.03, 0.38, 0.03)), attach_to="Top", instances=3),
               PartPlan(name="Ring", role="ring", description="brace", bbox=BBox(center=(0, 0.1, 0), extents=(0.2, 0.02, 0.2)), attach_to="Leg")])
    boxes = {"Top": ((-0.15, 0.38, -0.15), (0.15, 0.4, 0.15)), "Leg_0": ((-0.1, 0, -0.1), (-0.07, 0.38, -0.07)),
             "Leg_1": ((0.07, 0, -0.1), (0.1, 0.38, -0.07)), "Ring": ((-0.1, 0.09, -0.1), (0.1, 0.11, 0.1))}
    # two of three leg copies built; the ring's box overlaps both, so both are candidates for the gate to measure
    assert planned_joins(plan, _measurement(boxes)) == [('Leg_0', ('Top',)), ('Leg_1', ('Top',)), ('Ring', ('Leg_0', 'Leg_1'))]
    del boxes["Top"]  # the parent never made it into the export: no edge, the contract gate reports the missing part
    assert planned_joins(plan, _measurement(boxes)) == [('Ring', ('Leg_0', 'Leg_1'))]
    assert planned_joins(None, _measurement(boxes)) == [] and planned_joins(plan, None) == []


def test_judge_context_stays_empty_because_the_block_lives_in_gates_section(tmp_path, chair_plan):
    """The block is rendered from the stored GateReport, so ``3dcode judge <slug>`` on a recorded
    round gets it too; a judge_context copy would be a second source of the same facts."""
    from codeverse.contracts.artifacts import BuildResult
    from codeverse.workspace import Workspace

    assert ObjectPipeline().judge_context(Workspace(tmp_path / "w"), chair_plan, 0, BuildResult(ok=True, language="threejs"), []) == ""
