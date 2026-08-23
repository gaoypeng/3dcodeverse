"""Best-of-N candidates, pairwise tie-breaks, reference images, motion checks, instruction compaction."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from codeverse.contracts.common import Language, Track
from codeverse.contracts.plan import ArticulatedPlan
from codeverse.contracts.run import RunStatus
from codeverse.contracts.spec import ReferenceImage
from codeverse.events import EventLog
from codeverse.orchestrator.candidates import CandidateRecord, decide_best, rank_candidates
from codeverse.orchestrator.rounds import (
    RefineTask,
    RoundPolicy,
    build_refine_instructions,
    compact_instructions,
)
from codeverse.tracks import get_track
from codeverse.tracks.articulated_object import ArticulatedObjectTrack
from codeverse.tracks.motion import expected_direction
from codeverse.tracks.planner import plan_example
from codeverse.tracks.static_object import StaticObjectTrack
from codeverse.workspace import Workspace
from tests.orchestrator_tracks.conftest import make_spec
from tests.orchestrator_tracks.fakes import (
    FakeAgent,
    FakeChatModel,
    FakeJudge,
    FakePairwise,
    FakeRuntime,
    FakeServices,
)


def _planner(plan_dict):
    return FakeChatModel(lambda req: plan_dict)


# ----------------------------------------------------------------------------- pure logic
def test_round_policy_candidates_and_compaction():
    pol = RoundPolicy()
    assert pol.n_candidates == 1 and pol.with_candidates(None) is pol and pol.with_candidates(3).n_candidates == 3
    assert pol.with_candidates(0).n_candidates == 1 and pol.parallel_min_tasks == 2
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
    from codeverse.contracts.artifacts import GateFinding, GateReport, Severity

    gate = GateReport(gate="connectivity", passed=False, findings=[
        GateFinding(gate="connectivity", severity=Severity.ERROR, target="back_leg_1", message="floats", fix_hint="lower it")])
    extra = [RefineTask(target="overall", kind="reference", instruction="IoU 0.4", priority=1, source="gate")]
    tasks = build_refine_instructions(None, [gate], [], chair_plan, extra=extra)
    assert [t.target for t in tasks] == ["BackLeg_1", "overall"] and tasks[1].kind == "reference"


def test_rank_candidates_and_decide_best():
    recs = [CandidateRecord(index=0, label="c0", build_ok=True, score=0.62, gate_errors=2),
            CandidateRecord(index=1, label="c1", build_ok=True, score=0.62, gate_errors=0),
            CandidateRecord(index=2, label="c2", build_ok=False),
            CandidateRecord(index=3, label="c3", build_ok=True, score=0.70, gate_errors=5)]
    assert rank_candidates(recs) == [3, 1, 0, 2]
    # clear delta → ordinary ranking
    assert decide_best(0.60, 0.70, margin=0.03, min_confidence=0.6, compare=None) == ("score", None)
    # within noise, no comparator → keep
    d, note = decide_best(0.60, 0.61, margin=0.03, min_confidence=0.6, compare=None, labels=("r00", "r01"))
    assert d == "keep" and note is not None and not note.accepted and "keep" in note.line()

    class R:
        def __init__(self, w, c):
            self.winner, self.confidence, self.reasons, self.usage, self.error = w, c, ["x"], None, ""

    d, note = decide_best(0.60, 0.61, margin=0.03, min_confidence=0.6, compare=lambda: R("b", 0.8))
    assert d == "pairwise" and note.accepted and note.winner == "b"
    d, note = decide_best(0.60, 0.61, margin=0.03, min_confidence=0.6, compare=lambda: R("b", 0.5))
    assert d == "keep" and not note.accepted
    d, note = decide_best(0.60, 0.61, margin=0.03, min_confidence=0.6, compare=lambda: R("a", 0.9))
    assert d == "keep"

    def boom():
        raise RuntimeError("judge down")

    d, note = decide_best(0.60, 0.61, margin=0.03, min_confidence=0.6, compare=boom)
    assert d == "keep" and "judge down" in note.error
    assert decide_best(None, 0.5, margin=0.03, min_confidence=0.6, compare=None) == ("score", None)


@pytest.mark.parametrize("text,expected", [
    ("drawer pulls out towards -Y", "-y"), ("lid opens upward", "up"), ("door swings to the left", "left"),
    ("drawer slides out", "front"), ("opens to the front", "front"), ("seat folds down", "down"),
    ("drawer pushes in", "back"), ("lid hinges up and out", None), ("spins around its axis", None),
    ("", None), ("handle turns", None), ("does not move up", None), ("lower drawer pulls out", "front"),
])
def test_expected_direction(text, expected):
    assert expected_direction(text) == expected


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


def test_best_of_two_baseline_selects_highest_quick_score(tmp_path, chair_plan, settings):
    spec = make_spec(language=Language.BLENDER, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "stool")
    agent = FakeAgent(_writer_by_candidate)
    judge = _CandidateJudge({"c0": 0.55, "c1": 0.72})
    services = FakeServices(pairwise=FakePairwise())
    track = StaticObjectTrack(services=services, judge=judge, agent=agent, planner_model=_planner(chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.BLENDER), n_candidates=2)
    rec = track.run(spec, ws)
    assert rec.status is RunStatus.PLATEAU and len(rec.rounds) == 1
    r0 = rec.rounds[0]
    assert "best-of-2: selected c1" in r0.notes and r0.score == pytest.approx(0.6)
    assert "_cand/c1" in (ws.src / "model.py").read_text()
    cands = json.loads((ws.root / "rounds" / "candidates.json").read_text())
    assert cands["selected"] == 1 and [c["score"] for c in cands["candidates"]] == [0.55, 0.72]
    assert cands["candidates"][1]["selected"] and cands["candidates"][0]["build_ok"]
    labels = sorted(j.label for j in agent.jobs)
    assert labels == ["baseline_c0", "baseline_c1"]
    kinds = [e["event"] for e in EventLog(ws.events_path).read()]
    assert kinds.count("candidate.done") == 2 and "candidate.selected" in kinds and "candidates.start" in kinds
    # both candidates charged: round usage > one generation + one judge
    assert r0.usage.cost_usd > 2 * agent.cost and rec.extra["n_candidates"] == 2 and rec.extra["candidates"]["selected"] == 1
    # the sub-workspaces are git-ignored and self-contained
    assert (ws.root / "_cand" / "c0" / "spec.json").is_file() and "_cand/" in (ws.root / ".gitignore").read_text()
    assert (ws.root / "_cand" / "c1" / "AGENTS.md").is_file()
    # the winner's trajectory was kept under the run workspace
    assert any(p.name.startswith("baseline_c1") for p in ws.trajectories.iterdir())
    # pairwise not consulted: scores differ by more than the margin
    assert services._pairwise.calls == []


def test_best_of_two_pairwise_tiebreak_overrides_ranking(tmp_path, chair_plan, settings):
    spec = make_spec(language=Language.BLENDER, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "stool2")
    judge = _CandidateJudge({"c0": 0.61, "c1": 0.60})
    pw = FakePairwise(verdicts=[("b", 0.9)])
    track = StaticObjectTrack(services=FakeServices(pairwise=pw), judge=judge, agent=FakeAgent(_writer_by_candidate),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.BLENDER), n_candidates=2)
    rec = track.run(spec, ws)
    assert len(pw.calls) == 1 and "pairwise c0 vs c1: c1 wins" in rec.rounds[0].notes
    assert "_cand/c1" in (ws.src / "model.py").read_text()
    ev = [e for e in EventLog(ws.events_path).read() if e["event"] == "pairwise.done"]
    assert ev and ev[0]["stage"] == "candidates" and ev[0]["accepted"] is True


def test_candidate_count_persists_for_resume_and_settings_default(tmp_path, chair_plan, settings):
    spec = make_spec(language=Language.BLENDER, max_rounds=0)
    ws = Workspace(tmp_path / "runs" / "stool3")
    track = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.5,)), agent=FakeAgent(_writer_by_candidate),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings,
                              runtime=FakeRuntime(Language.BLENDER), n_candidates=2)
    track.run(spec, ws)
    state = json.loads(ws.state_path.read_text())
    assert state["extra"]["n_candidates"] == 2
    # a resume without the flag reads the persisted width
    t2 = StaticObjectTrack(services=FakeServices(), judge=FakeJudge(scores=(0.5,)), agent=FakeAgent(_writer_by_candidate),
                           planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(Language.BLENDER))
    from codeverse.orchestrator.state import RunState

    ctx = t2.build_context(spec, ws, EventLog(ws.events_path), RunState.load(ws))
    assert ctx.policy.n_candidates == 2
    # settings default is honoured when nothing else says
    settings2 = settings.model_copy()
    object.__setattr__(settings2, "default_candidates", 3)
    t3 = StaticObjectTrack(services=FakeServices(), settings=settings2, runtime=FakeRuntime(Language.BLENDER))
    ctx3 = t3.build_context(spec, Workspace(tmp_path / "runs" / "fresh").create(), EventLog(tmp_path / "e.jsonl"), RunState())
    assert ctx3.policy.n_candidates == 3
    assert get_track("static_object", n_candidates=4)._n_candidates == 4


# ----------------------------------------------------------------------------- refine pairwise tie-break
def test_refine_round_within_margin_needs_pairwise_win(tmp_path, chair_plan, settings):
    spec = make_spec(language=Language.BLENDER, max_rounds=2)
    ws = Workspace(tmp_path / "runs" / "tie")
    agent = FakeAgent(lambda job, ws: {"src/model.py": f"import bpy  # {job.label} {job.extra.get('round')}\n"})
    pw = FakePairwise(verdicts=[("a", 0.8), ("b", 0.9)])
    # r01 scores +0.01 (noise) → pairwise says incumbent wins → best stays r00; r02 +0.02 → pairwise b wins → best r02
    judge = FakeJudge(scores=(0.60, 0.61, 0.62))
    track = StaticObjectTrack(services=FakeServices(pairwise=pw), judge=judge, agent=agent,
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(Language.BLENDER),
                              policy=RoundPolicy(max_rounds=2, target=0.9, plateau_window=5))
    rec = track.run(spec, ws)
    assert [r.score for r in rec.rounds] == pytest.approx([0.60, 0.61, 0.62])
    assert len(pw.calls) == 2 and rec.best_round == 2
    assert "pairwise r00 vs r01: r00 wins" in rec.rounds[1].notes and "→ keep" in rec.rounds[1].notes
    assert "pairwise r00 vs r02: r02 wins" in rec.rounds[2].notes and "→ replace" in rec.rounds[2].notes
    events = EventLog(ws.events_path).read()
    best_updates = [e["round"] for e in events if e["event"] == "best.updated"]
    assert best_updates == [0, 2]
    assert sum(1 for e in events if e["event"] == "pairwise.done") == 2
    # persisted round record carries the note + pairwise cost
    saved = json.loads((ws.root / "rounds" / "r01.json").read_text())
    assert "pairwise" in saved["notes"] and saved["usage"]["cost_usd"] > 0


def test_refine_round_clear_delta_skips_pairwise(tmp_path, chair_plan, settings):
    spec = make_spec(language=Language.BLENDER, max_rounds=1)
    ws = Workspace(tmp_path / "runs" / "clear")
    pw = FakePairwise()
    track = StaticObjectTrack(services=FakeServices(pairwise=pw), judge=FakeJudge(scores=(0.50, 0.70)),
                              agent=FakeAgent(lambda job, ws: {"src/model.py": f"import bpy  # {job.label}\n"}),
                              planner_model=_planner(chair_plan.model_dump(mode="json")), settings=settings, runtime=FakeRuntime(Language.BLENDER))
    rec = track.run(spec, ws)
    assert rec.best_round == 1 and pw.calls == []


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
    from codeverse.contracts.chat import ImagePart

    p0 = model.requests[0]
    imgs = [pt for pt in p0.messages[0].parts if isinstance(pt, ImagePart)]
    assert "REFERENCE IMAGES (1)" in p0.messages[0].text and imgs and imgs[0].path == str(ref)
    # silhouette gate recorded with the IoU, and the refine round got the reference task
    gate = next(g for g in rec.rounds[0].gates if g.gate == "reference_silhouette")
    assert gate.passed and gate.findings[0].data["iou"] == pytest.approx(0.42) and services.silhouette_calls
    assert any("silhouette IoU vs the reference image is 0.42" in i for i in rec.rounds[1].instructions)
    assert any("wider relative to its height" in i for i in rec.rounds[1].instructions)
    refine_prompt = model.requests[-1].messages[0].text
    assert "## Reference images" in refine_prompt and "compare_silhouette" not in refine_prompt  # single-shot: no tools


def test_reference_note_mentions_silhouette_tool_for_agents(tmp_path, chair_plan, settings):
    ref = tmp_path / "ref.png"
    Image.new("RGB", (64, 48)).save(ref)
    spec = make_spec(language=Language.BLENDER, max_rounds=0, references=[ReferenceImage(path=str(ref))])
    ws = Workspace(tmp_path / "runs" / "ref2")
    agent = FakeAgent(lambda job, ws: {"src/model.py": "import bpy\n"})
    track = StaticObjectTrack(services=FakeServices(silhouette_iou=0.8), agent=agent, planner_model=_planner(chair_plan.model_dump(mode="json")),
                              settings=settings, runtime=FakeRuntime(Language.BLENDER))
    rec = track.run(spec, ws)
    assert "compare_silhouette" in agent.jobs[0].prompt and str(ref) in agent.jobs[0].prompt
    gate = next(g for g in rec.rounds[0].gates if g.gate == "reference_silhouette")
    assert gate.findings[0].severity.value == "info"  # IoU 0.8 is fine → no refine task


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


# ----------------------------------------------------------------------------- per-part files via runtime.file_for_part
def test_expected_files_and_targets_follow_runtime_file_for_part(tmp_path, chair_plan, settings):
    from codeverse.conventions import to_snake
    from codeverse.tracks.prompting import file_for_target_factory
    from codeverse.tracks.static_object import expected_files

    class PartsRuntime(FakeRuntime):
        @staticmethod
        def file_for_part(name: str) -> str:
            return f"src/parts/{to_snake(name)}.py"

    spec = make_spec(language=Language.BLENDER, max_rounds=0)
    track = StaticObjectTrack(services=FakeServices(), settings=settings, runtime=PartsRuntime(Language.BLENDER))
    from codeverse.orchestrator.state import RunState

    ctx = track.build_context(spec, Workspace(tmp_path / "ws").create(), EventLog(tmp_path / "e.jsonl"), RunState())
    ctx.plan = chair_plan
    assert expected_files(ctx)[:3] == ["src/model.py", "src/parts/seat.py", "src/parts/front_leg.py"]
    fft = file_for_target_factory(ctx)
    assert fft("Seat") == ["src/parts/seat.py"] and fft("overall") == ["src/model.py"] and fft("BackLeg_1") == []


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
    """Hinged door: axis -z opens to the front (ok); axis +z → WRONG with the negated-axis fix."""
    import trimesh

    from codeverse.contracts.plan import BBox, JointPlan, PartPlan
    from codeverse.tracks.motion import default_motion_checks

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
    plan.joints[0].motion = "rotates"  # ambiguous → no gate
    assert default_motion_checks(robot(tmp_path / "none", 1), plan) is None
