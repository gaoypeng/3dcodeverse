"""Best-of-N candidates, reference images, motion checks, instruction compaction."""

from __future__ import annotations

import pytest
from PIL import Image

from codeverse3d.contracts.common import Language, Track
from codeverse3d.contracts.plan import ArticulatedPlan
from codeverse3d.contracts.spec import ReferenceImage
from codeverse3d.orchestrator import (
    RefineTask,
    RoundPolicy,
    build_refine_instructions,
    compact_instructions,
)
from codeverse3d.proc import EventLog
from codeverse3d.tracks.articulated_object import ArticulatedObjectTrack, expected_direction
from codeverse3d.tracks.candidates import CandidateRecord, rank_candidates
from codeverse3d.tracks.planner import plan_example
from codeverse3d.tracks.static_object import StaticObjectTrack
from codeverse3d.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakeRuntime,
    FakeServices,
    _planner,
)


# ----------------------------------------------------------------------------- pure logic
def test_round_policy_candidates_and_compaction():
    pol = RoundPolicy()
    assert pol.n_candidates == 1 and pol.parallel_min_tasks == 2
    tasks = [RefineTask(target=f"BackLeg_{i}", kind="gate:connectivity", instruction=f"leg {i} floats", priority=0, source="gate") for i in range(4)]
    tasks += [RefineTask(target="Seat", kind="geometry", instruction="thicker", priority=2)]
    tasks += [RefineTask(target=f"Spindle_{i}", kind="gate:contract", instruction="off", priority=0, source="gate") for i in range(3)]
    lines = compact_instructions(tasks, max_lines=6)
    assert len(lines) == 3 and lines[0].startswith("BackLeg: (a) [BackLeg_0]") and "(d) [BackLeg_3]" in lines[0]
    assert lines[1].startswith("Spindle:") and lines[2] == "[judge/geometry] Seat: thicker"
    many = [RefineTask(target=f"P{i}", kind="geometry", instruction="x", priority=i % 3) for i in range(10)]
    assert len(compact_instructions(many, max_lines=6)) == 6
    assert compact_instructions([]) == []


def test_build_refine_instructions_extra_and_instance_targets(chair_plan):
    from codeverse3d.contracts.artifacts import GateFinding, GateReport, Severity

    gate = GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="back_leg_1", message="floats", fix_hint="lower it")])
    extra = [RefineTask(target="overall", kind="reference", instruction="IoU 0.4", priority=1, source="gate")]
    tasks = build_refine_instructions(None, [gate], [], chair_plan, extra=extra)
    assert [t.target for t in tasks] == ["BackLeg_1", "overall"] and tasks[1].kind == "reference"


def test_rank_candidates():
    """Built > higher quick score > fewer gate errors > earlier — the one in-loop choice left."""
    recs = [CandidateRecord(index=0, label="c0", build_ok=True, score=0.62, gate_errors=2),
            CandidateRecord(index=1, label="c1", build_ok=True, score=0.62, gate_errors=0),
            CandidateRecord(index=2, label="c2", build_ok=False),
            CandidateRecord(index=3, label="c3", build_ok=True, score=0.70, gate_errors=5)]
    assert rank_candidates(recs) == [3, 1, 0, 2]
    tie = [CandidateRecord(index=0, label="c0", build_ok=True, score=0.6),
           CandidateRecord(index=1, label="c1", build_ok=True, score=0.6)]
    assert rank_candidates(tie) == [0, 1]


def test_expected_direction():
    cases = [
        ("drawer pulls out towards -Y", "-y"), ("lid opens upward", "up"), ("door swings to the left", "left"),
        ("drawer slides out", "front"), ("opens to the front", "front"), ("seat folds down", "down"),
        ("drawer pushes in", "back"), ("lid hinges up and out", None), ("spins around its axis", None),
        ("", None), ("handle turns", None), ("does not move up", None), ("lower drawer pulls out", "front"),
    ]
    assert [expected_direction(text) for text, _ in cases] == [expected for _, expected in cases]


# ----------------------------------------------------------------------------- best-of-N track run
class _CandidateJudge(FakeJudge):
    """Scores by which candidate workspace the renders come from; the main round gets 0.6."""

    def __init__(self, by_candidate: dict[str, float]):
        super().__init__(scores=(0.6,))
        self.by_candidate = by_candidate

    def judge(self, inp):
        path = inp.renders.views[0].path if inp.renders.views else ""
        for key, score in self.by_candidate.items():
            if f"/_cand/{key}/" in path:
                self.scores = [score]
                self.calls = []
                return super().judge(inp)
        self.scores, self.calls = [0.6], []
        return super().judge(inp)


def _writer_by_candidate(job, ws):
    tag = ws.root.parent.name + "/" + ws.root.name if ws.root.parent.name == "_cand" else "main"
    return {"src/model.py": f"import bpy  # {job.label} in {tag}\n"}


# ----------------------------------------------------------------------------- reference images
def test_reference_images_wire_judge_prompts_and_silhouette(tmp_path, chair_plan, settings):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (64, 48), (10, 10, 10)).save(ref)
    spec = make_spec(language=Language.BLENDER, max_rounds=1, generator="single-shot:gemini:fake",
                     references=[ReferenceImage(path=str(ref), role="target", note="side view photo")])
    ws = Workspace(tmp_path / "runs" / "ref")
    model = FakeChatModel(lambda req: "=== FILE: src/model.py ===\nimport bpy\n=== END FILE ===")
    services = FakeServices(silhouette_iou=0.42)
    track = StaticObjectTrack(services=services, model=model, planner_model=_planner(chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.BLENDER))
    rec = track.run(spec, ws)
    # reference judge + rubric
    assert services.reference_judges and services.reference_judges[0][2] == "reference_v1" and rec.extra["rubric"] == "reference_v1"
    # prompts mention the references; single-shot attaches the image
    from codeverse3d.contracts.chat import ImagePart

    p0 = model.requests[0]
    imgs = [pt for pt in p0.messages[0].parts if isinstance(pt, ImagePart)]
    assert "REFERENCE IMAGES (1)" in p0.messages[0].text and imgs and imgs[0].path == str(ref)
    # silhouette gate recorded with the IoU, and the refine round got the reference task
    gate = next(g for g in rec.rounds[0].gates if g.gate == "reference_silhouette")
    assert gate.passed and gate.findings[0].data["iou"] == pytest.approx(0.42) and services.silhouette_calls
    assert any("silhouette IoU vs the reference image is 0.42" in i for i in rec.rounds[1].instructions)
    assert any("wider relative to its height" in i for i in rec.rounds[1].instructions)
    refine_prompt = model.requests[-1].messages[0].text
    assert "## Reference images" in refine_prompt and "compare_reference" not in refine_prompt  # single-shot: no tools


# ----------------------------------------------------------------------------- articulated motion gate
def test_articulated_motion_direction_gate_feeds_refine_and_judge(tmp_path, settings):
    spec = make_spec(Track.ARTICULATED_OBJECT, Language.URDF_BLENDER, max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "drawer2")
    plan = ArticulatedPlan.model_validate(plan_example(Track.ARTICULATED_OBJECT))
    agent = FakeAgent(lambda job, ws: {"src/model.py": f"import bpy  # {job.label}\n", "src/robot.urdf": "<robot name='x'/>\n"})
    judge = FakeJudge(scores=(0.6, 0.9), targets=("Drawer",))
    track = ArticulatedObjectTrack(services=FakeServices(motion_errors=1), judge=judge, agent=agent,
                                   planner_model=_planner(plan.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(Language.URDF_BLENDER))
    rec = track.run(spec, ws)
    motion = next(g for g in rec.rounds[0].gates if g.gate == "motion_direction")
    assert not motion.passed and motion.errors[0].target == "DrawerSlide"
    assert any(i.startswith("[gate/gate:motion_direction] DrawerSlide") and "negate the axis" in i for i in rec.rounds[1].instructions)
    assert "Motion direction (harness FK check): 1 WRONG" in judge.calls[0].extra_context
    assert any(v.name == "pose_DrawerSlide_upper" for v in rec.rounds[0].renders.views)


def test_crashed_candidate_is_retried_once(tmp_path, chair_plan, settings):
    spec = make_spec(language=Language.BLENDER, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "retry")
    seen: dict[str, int] = {}

    def writer(job, ws_):
        seen[job.label] = seen.get(job.label, 0) + 1
        if job.label == "baseline_c0" and seen[job.label] == 1:
            raise RuntimeError("503 high demand")
        return _writer_by_candidate(job, ws_)

    judge = _CandidateJudge({"c0": 0.75, "c1": 0.55})
    track = StaticObjectTrack(services=FakeServices(), judge=judge, agent=FakeAgent(writer), planner_model=_planner(chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.BLENDER), n_candidates=2)
    rec = track.run(spec, ws)
    assert seen == {"baseline_c0": 2, "baseline_c1": 1}
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert "candidate.retry" in kinds and "candidate.failed" not in kinds
    assert "best-of-2: selected c0" in rec.rounds[0].notes and "_cand/c0" in (ws.src / "model.py").read_text()


def test_default_motion_checks_on_real_urdf(tmp_path):
    import trimesh

    from codeverse3d.contracts.plan import BBox, JointPlan, PartPlan
    from codeverse3d.tracks.articulated_object import default_motion_checks

    def robot(root, axis_z):
        ws = Workspace(root).create()
        meshes = ws.artifacts / "meshes"
        for name, c, e in (("Body", (0, 0, 0.4), (0.6, 0.4, 0.8)), ("Door", (0, -0.21, 0.4), (0.58, 0.02, 0.78))):
            m = trimesh.creation.box(e)
            m.apply_translation(c)
            meshes.mkdir(parents=True, exist_ok=True)
            (meshes / f"{name}.glb").write_bytes(m.export(file_type="glb"))
        (ws.artifacts / "robot.urdf").write_text(f"""<robot name="cab">
<link name="Body"><visual><origin xyz="0 0 0"/><geometry><mesh filename="meshes/Body.glb"/></geometry></visual>
  <collision><origin xyz="0 0 0"/><geometry><mesh filename="meshes/Body.glb"/></geometry></collision></link>
<link name="Door"><visual><origin xyz="0.29 0.2 0"/><geometry><mesh filename="meshes/Door.glb"/></geometry></visual>
  <collision><origin xyz="0.29 0.2 0"/><geometry><mesh filename="meshes/Door.glb"/></geometry></collision></link>
<joint name="door_hinge" type="revolute"><parent link="Body"/><child link="Door"/>
  <origin xyz="-0.29 -0.2 0"/><axis xyz="0 0 {axis_z}"/><limit lower="0" upper="1.57" effort="10" velocity="1"/></joint>
</robot>""")
        return ws

    bbox = BBox(center=(0, 0, 0.4), extents=(0.6, 0.4, 0.8))
    plan = ArticulatedPlan(object_name="Cabinet", summary="s", overall_bbox=bbox, root_link="Body",
                           parts=[PartPlan(name="Body", role="r", description="d", bbox=bbox),
                                  PartPlan(name="Door", role="r", description="d", bbox=bbox, attach_to="Body")],
                           joints=[JointPlan(name="DoorHinge", type="revolute", parent="Body", child="Door", axis=(0, 0, 1),
                                             pivot=(-0.29, -0.2, 0.4), lower=0, upper=1.57, motion="door swings open to the front")],
                           acceptance=[])
    good = default_motion_checks(robot(tmp_path / "good", -1), plan)
    assert good is not None and good.passed and good.findings[0].severity.value == "info"
    bad = default_motion_checks(robot(tmp_path / "bad", 1), plan)
    assert bad is not None and not bad.passed and bad.errors[0].target == "DoorHinge"
    assert "WRONG" in bad.errors[0].message and '<axis xyz="-0 -0 -1"/>' in bad.errors[0].fix_hint
    # orthogonal (the swiss-knife class): a prismatic joint sliding +x when the plan says
    # up — cos = 0 exactly, and the hint states the computed axis verbatim
    import copy
    ortho = robot(tmp_path / "ortho", 1)
    u = (ortho.artifacts / "robot.urdf").read_text()
    u = u.replace('type="revolute"', 'type="prismatic"').replace('<axis xyz="0 0 1"/>', '<axis xyz="1 0 0"/>')
    (ortho.artifacts / "robot.urdf").write_text(u)
    slide_plan = copy.deepcopy(plan)
    slide_plan.joints[0].type = "prismatic"
    slide_plan.joints[0].motion = "slides straight up"
    o = default_motion_checks(ortho, slide_plan)
    assert o is not None and not o.passed
    # URDF space is Z-up: 'up' is +z there (the harness converts frames at the boundary)
    assert "computed from the pivot" in o.errors[0].fix_hint and '<axis xyz="0 0 1"/>' in o.errors[0].fix_hint
    assert o.errors[0].data.get("suggested_axis") == [0.0, 0.0, 1.0]

    plan.joints[0].motion = "rotates"  # ambiguous → no gate
    assert default_motion_checks(robot(tmp_path / "none", 1), plan) is None


def test_an_object_candidate_still_uses_the_cheap_rig(tmp_path):
    """A GLB candidate goes through the reduced quick rig, never pipeline.render."""
    from types import SimpleNamespace

    from codeverse3d.tracks.candidates import OBJECT_VIEWS_QUICK, QUICK_PX, quick_render

    seen = {}

    class _Services:
        def render_object(self, glb, out_dir, *, views, width, height):
            seen.update(glb=glb, views=views, width=width, height=height)
            return SimpleNamespace(views=[])

    class _WS:
        def renders_dir(self, i): return tmp_path

    class _Ctx:
        ws = _WS()
        services = _Services()
        extra = {"pose_views": ["pose-view"]}  # what ArticulatedPipeline.gates() parked for render()

    class _Pipeline:
        def render(self, *a, **k):  # must not be reached
            raise AssertionError("an object candidate must not use pipeline.render")

    got = quick_render(_Ctx(), 0, SimpleNamespace(glb_path="/x/object.glb"), "M", pipeline=_Pipeline())
    assert got.views == ["pose-view"]  # the quick rig still carries the articulated pose views to the judge
    assert seen["views"] == OBJECT_VIEWS_QUICK and seen["width"] == QUICK_PX
