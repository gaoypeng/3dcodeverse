"""compare_backends on an articulated battery: two-file one-shot answers, articulated_v1 fixed judge."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._fixed_eval import FixedEvaluator, rubric_for  # noqa: E402
from bench._oneshot import (  # noqa: E402
    MODEL_FILE,
    URDF_FILE,
    minimal_contract,
    oneshot_prompt,
    repair_prompt,
    write_answer_files,
)
from bench.run_bench import Battery  # noqa: E402
from codeverse3d.contracts.artifacts import BuildResult, GateReport  # noqa: E402
from codeverse3d.contracts.common import Language, Track  # noqa: E402
from codeverse3d.contracts.spec import Constraints, Spec  # noqa: E402
from codeverse3d.workspace import Workspace  # noqa: E402

PROMPTS = REPO / "bench" / "prompts"
PY = "import bpy\nprint(1)\n"
XML = '<?xml version="1.0"?>\n<robot name="x"><link name="base"/></robot>\n'
ENVELOPE = f"=== FILE: {MODEL_FILE} ===\n{PY}=== END FILE ===\n=== FILE: {URDF_FILE} ===\n{XML}=== END FILE ===\n"


def _urdf_spec() -> Spec:
    return Spec(id="t/lamp", track=Track.ARTICULATED_OBJECT, language=Language.URDF_BLENDER,
                prompt="an architect lamp with two hinged arms",
                constraints=Constraints(must_have=["two revolute joints"], dimensions_m={"height": 0.6}))


def test_the_fixed_judge_is_the_class_the_loop_would_use():
    """_fixed_eval picked LikenessJudge by hand for graphics only: a scene or an object cell with
    reference photos was judged by a plain VlmJudge the loop never uses for it."""
    from codeverse3d.contracts.spec import ReferenceImage
    from codeverse3d.judges.vlm_judge import LikenessJudge, ReferenceJudge, VlmJudge

    ev = FixedEvaluator("gemini:x")
    refs = [ReferenceImage(path="ref.png")]
    lamp = _urdf_spec()
    scene = Spec(id="t/s", track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="a harbour at dusk")
    shader = Spec(id="t/g", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="neon rain")
    assert type(ev.judge_for(lamp)) is VlmJudge
    assert type(ev.judge_for(lamp.model_copy(update={"references": refs}))) is ReferenceJudge
    assert type(ev.judge_for(scene.model_copy(update={"references": refs}))) is LikenessJudge
    assert type(ev.judge_for(shader.model_copy(update={"references": refs}))) is LikenessJudge


def test_urdf_oneshot_prompt_carries_the_frame_recipe_and_the_two_file_envelope():
    p = oneshot_prompt(_urdf_spec())
    assert "hand-written URDF" in p and "architect lamp" in p and "MUST HAVE: two revolute joints" in p
    for rule in ("rpy=\"0 0 0\"", "pivot_child − frame_parent", "−pivot_link", "meshes/<link>.glb", "lower ≤ 0 ≤ upper"):
        assert rule in p, rule
    assert f"=== FILE: {MODEL_FILE} ===" in p and f"=== FILE: {URDF_FILE} ===" in p
    # no harness help: no worked example, no cookbook, no skeleton
    assert "pedal" not in p.lower() and "cookbook" not in p.lower() and "PIVOT[" not in p
    assert minimal_contract(Language.URDF_BLENDER) in p
    # the static contract is untouched by the language switch
    assert minimal_contract() == minimal_contract(Language.BLENDER) and URDF_FILE not in minimal_contract()


def test_write_answer_files_writes_both(tmp_path):
    ws = Workspace(tmp_path / "ws")
    ws.create()
    paths = write_answer_files(ws, ENVELOPE, Language.URDF_BLENDER)
    assert [p.relative_to(ws.root).as_posix() for p in paths] == [MODEL_FILE, URDF_FILE]
    assert (ws.root / URDF_FILE).read_text() == XML and (ws.root / MODEL_FILE).read_text() == PY


def test_urdf_repair_prompt_carries_both_previous_files():
    build = BuildResult(ok=False, language="urdf_blender", error_type="FKMismatch",
                        error_message="link 'arm' visual origin should be -0.1 0 0.3", error_file=URDF_FILE, error_line=9)
    p = repair_prompt(_urdf_spec(), {MODEL_FILE: PY, URDF_FILE: XML}, build, GateReport(gate="lint:urdf", passed=True), attempt=1)
    assert "FKMismatch" in p and "corrected files" in p
    assert f"PREVIOUS `{MODEL_FILE}`" in p and f"PREVIOUS `{URDF_FILE}`" in p and "```xml" in p


def test_rubric_for_is_the_one_track_to_rubric_mapping():
    """PR #1 shipped `RUBRIC_BY_TRACK` in _compare_report beside main's `judge_for`; one mechanism now:
    rubric_for(spec | track) = TRACK_INFO's rubric, used by the evaluator default, judge_for and the report."""
    import bench._compare_report as report
    from codeverse3d.contracts.common import TRACK_INFO

    lang = {Track.STATIC_OBJECT: Language.BLENDER, Track.ARTICULATED_OBJECT: Language.URDF_BLENDER,
            Track.SCENE: Language.SCENE_THREEJS, Track.GRAPHICS: Language.GLSL_SHADER}
    for t in Track:
        assert rubric_for(t) == TRACK_INFO[t].rubric
        assert rubric_for(Spec(id="x", track=t, language=lang[t], prompt="x")) == TRACK_INFO[t].rubric
    assert report.battery_rubric({"battery": {"track": "articulated_object"}}) == "articulated_v1"
    assert report.battery_rubric({}) == "static_object_v1" == report.battery_rubric({"battery": {"track": "nope"}})
    # a static-battery evaluator still judges an articulated cell on the articulated rubric ...
    ev = FixedEvaluator("gemini:x", n_samples=1)
    assert ev.judge_for(_urdf_spec()).rubric.name == "articulated_v1"
    assert ev.judge_for(_urdf_spec()) is ev.judge_for(_urdf_spec()), "one judge per rubric, cached"
    assert ev.judge.rubric.name == "static_object_v1"
    # ... unless the caller pinned a rubric for every cell
    assert FixedEvaluator("gemini:x", rubric="asset_v1").judge_for(_urdf_spec()).rubric.name == "asset_v1"


def test_articulated_cells_add_the_joint_sweep_and_the_pose_sheet(monkeypatch, tmp_path):
    """evaluate() on an articulated spec: the sweep gate joins the gates, the pose views join the
    renders, and the judge is the cell's (articulated_v1) — keyed on the SPEC's track."""
    from types import SimpleNamespace

    import codeverse3d.spatial.connectivity as conn
    import codeverse3d.spatial.measure as meas
    import codeverse3d.spatial.render as rend
    import codeverse3d.tracks.articulated_object as art
    from codeverse3d.contracts.artifacts import RenderSet, RenderView

    ws = Workspace(tmp_path / "ws").create()
    glb = tmp_path / "m.glb"
    glb.write_bytes(b"glTF")
    build = BuildResult(ok=True, language="urdf_blender", glb_path=str(glb))
    ev = FixedEvaluator("fake:judge", n_samples=1)  # a static-default evaluator, deliberately
    ev._runtimes[Language.URDF_BLENDER] = SimpleNamespace(lint=lambda ws: GateReport(gate="lint", passed=True),
                                                          build=lambda ws, timeout_s: build)
    monkeypatch.setattr(meas, "measure_glb", lambda p: None)
    monkeypatch.setattr(conn, "check_connectivity", lambda p: GateReport(gate="connectivity", passed=True))
    monkeypatch.setattr(rend, "render_glb", lambda p, d, **k: RenderSet(views=[RenderView(name="front", path=str(glb))], renderer="fake"))
    sweep = GateReport(gate="joint_sweep", passed=True)
    pose = RenderView(name="articulation_sheet", path=str(glb))
    seen: dict[str, object] = {}
    monkeypatch.setattr(art, "default_joint_sweep", lambda ws, plan, out: (seen.__setitem__("plan", plan), (sweep, [pose]))[1])

    class _J:
        rubric = SimpleNamespace(name="articulated_v1")

        def judge(self, inp):
            seen["inp"] = inp
            return SimpleNamespace(overall=0.5, passed=False)

    monkeypatch.setattr(FixedEvaluator, "judge_for", lambda self, spec: (seen.__setitem__("spec", spec), _J())[1])
    out = ev.evaluate(ws, _urdf_spec())
    assert out.error == "", out.error
    assert seen["plan"] is None, "no plan: every arm is swept alike"
    assert seen["spec"].track is Track.ARTICULATED_OBJECT and out.judgment.overall == 0.5
    assert [g.gate for g in out.gates] == ["lint", "connectivity", "joint_sweep"]
    assert [v.name for v in out.renders.views] == ["front", "articulation_sheet"]
    assert [v.name for v in seen["inp"].renders.views] == ["front", "articulation_sheet"]


def test_main_builds_the_evaluator_from_the_battery(monkeypatch, tmp_path):
    from bench import compare_backends as cb

    seen = {}

    def fake_run_matrix(battery_path, out_dir, arms, opts, deps, on_result=None):
        seen["track"], seen["language"], seen["rubric"] = deps.evaluator.track, deps.evaluator.language, deps.evaluator.rubric
        (Path(out_dir)).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "results.jsonl").write_text("")

    monkeypatch.setattr(cb, "run_matrix", fake_run_matrix)
    rc = cb.main(["--prompts", str(PROMPTS / "articulated_v2.yaml"), "--arms", "oneshot:gemini:x",
                  "--out", str(tmp_path / "out"), "--no-preflight", "--judge-samples", "3"])
    assert rc == 0
    assert seen == {"track": Track.ARTICULATED_OBJECT, "language": Language.URDF_BLENDER, "rubric": "articulated_v1"}
    b = Battery.load(PROMPTS / "articulated_v2.yaml")
    assert b.track is Track.ARTICULATED_OBJECT and all(p.must_have for p in b.prompts)
