"""The fixed evaluator builds each cell with ITS language's runtime, not Blender's."""

from __future__ import annotations

from codeverse3d.contracts.common import Language


def test_the_evaluator_asks_for_the_cells_own_runtime(monkeypatch):
    """Measured 2026-08-26 on fancy_v1/tj: a three.js cell judged 0.589 in its run was then
    built by the Blender runtime at eval time — `MissingEntryFile: src/model.py` — and recorded
    build_failed 0.0.  A false zero, the b4ea4cc bug class one layer down."""
    import codeverse3d.languages as langs
    from bench._fixed_eval import FixedEvaluator

    asked: list[Language] = []

    class _RT:
        def __init__(self, lang): self.lang = lang

    monkeypatch.setattr(langs, "get_runtime", lambda lang: asked.append(Language(lang)) or _RT(Language(lang)))
    ev = FixedEvaluator("gemini:fake", n_samples=1)
    assert ev.runtime(Language.THREEJS).lang is Language.THREEJS
    assert ev.runtime("blender").lang is Language.BLENDER
    assert ev.runtime(Language.THREEJS).lang is Language.THREEJS, "cached per language, not one global"
    assert asked == [Language.THREEJS, Language.BLENDER], "each language's runtime is constructed once"


def test_graphics_cells_are_judged_on_their_frames(monkeypatch, tmp_path):
    """Measured 2026-08-26 (seed_v1): a graphics cell has frames, not a GLB, so the fixed
    evaluator returned before judging and every cell was `judge_error: no judgment`."""
    from types import SimpleNamespace

    from bench._fixed_eval import FixedEvaluator
    from codeverse3d.contracts.artifacts import BuildResult, GateReport, RenderSet, RenderView
    from codeverse3d.contracts.common import Track
    from codeverse3d.contracts.spec import ReferenceImage, Spec
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "run").create()
    png = tmp_path / "f.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    build = BuildResult(ok=True, language="glsl_shader", glb_path=None)
    ev = FixedEvaluator("fake:judge", n_samples=1)
    ev._runtimes[Language.GLSL_SHADER] = SimpleNamespace(lint=lambda ws: GateReport(gate="lint", passed=True), build=lambda ws, timeout_s: build)
    import bench._fixed_eval as fe
    import codeverse3d.tracks.graphics as gs

    monkeypatch.setattr(gs, "frames_render_set", lambda ws, b, i: RenderSet(views=[RenderView(name="t=0s", path=str(png))], renderer="fake"))
    monkeypatch.setattr(gs, "frame_stats_text", lambda ws: "frames=1")
    seen: dict[str, object] = {}

    class _J:
        def judge(self, inp):
            seen["inp"] = inp
            return SimpleNamespace(overall=0.71, passed=True)

    monkeypatch.setattr(fe.FixedEvaluator, "judge_for", lambda self, spec: (seen.__setitem__("spec", spec), _J())[1])
    spec = Spec(id="g", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="an aurora",
                references=[ReferenceImage(path=str(png), role="likeness")])
    out = ev.evaluate(ws, spec)
    assert out.error == "", out.error
    assert out.judgment.overall == 0.71
    assert seen["inp"].renders.views[0].path == str(png) and "frames=1" in seen["inp"].extra_context


def test_graphics_judge_is_the_track_rubric_with_the_photos():
    from bench._fixed_eval import FixedEvaluator
    from codeverse3d.contracts.common import Track
    from codeverse3d.contracts.spec import ReferenceImage, Spec
    from codeverse3d.judges.vlm_judge import LikenessJudge

    ev = FixedEvaluator("gemini:gemini-3.1-pro-preview", n_samples=2)
    with_photos = Spec(id="g", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="x", references=[ReferenceImage(path="/p.png")])
    j = ev.judge_for(with_photos)
    assert isinstance(j, LikenessJudge) and j.rubric.name == "shader_v2"
    plain = ev.judge_for(Spec(id="g", track=Track.GRAPHICS, language=Language.GLSL_SHADER, prompt="x"))
    assert plain.rubric.name == "shader_v2" and not isinstance(plain, LikenessJudge)
    obj = ev.judge_for(Spec(id="o", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="x"))
    assert obj.rubric.name == "static_object_v1"


def test_scene_cells_are_judged_on_their_cameras_and_orbit_frames(monkeypatch, tmp_path):
    """2026-09-07: the compare bench had no scene branch — a scene cell built (no GLB) and
    `evaluate` returned before judging.  A scene is judged on render_scene's frames (authored
    cameras + orbit rig, t = 0 and 1.5 s) with the scene_frames gate on the scene rubric."""
    from types import SimpleNamespace

    from bench._fixed_eval import FixedEvaluator
    from codeverse3d.contracts.artifacts import BuildResult, GateReport, RenderSet, RenderView
    from codeverse3d.contracts.common import Track
    from codeverse3d.contracts.spec import Constraints, Spec
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "run").create()
    png = tmp_path / "f.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    build = BuildResult(ok=True, language="scene_threejs", glb_path=None)
    ev = FixedEvaluator("fake:judge", n_samples=1)
    ev._runtimes[Language.SCENE_THREEJS] = SimpleNamespace(lint=lambda ws: GateReport(gate="lint", passed=True), build=lambda ws, timeout_s: build)
    import bench._fixed_eval as fe
    import codeverse3d.spatial.frame_metrics as fm
    import codeverse3d.spatial.render_scene as rs
    seen: dict[str, object] = {}
    monkeypatch.setattr(rs, "render_scene", lambda ws, out, **kw: (seen.__setitem__("kw", kw),
                        RenderSet(views=[RenderView(name="Establishing_t0", path=str(png))], renderer="fake"))[1])
    monkeypatch.setattr(fm, "frame_gate_from_renders", lambda src: GateReport(gate="scene_frames", passed=True))

    class _J:
        def judge(self, inp):
            seen["inp"] = inp
            return SimpleNamespace(overall=0.66, passed=False)
    monkeypatch.setattr(fe.FixedEvaluator, "judge_for", lambda self, spec: (seen.__setitem__("spec", spec), _J())[1])
    spec = Spec(id="s", track=Track.SCENE, language=Language.SCENE_THREEJS, prompt="a harbour",
                constraints=Constraints(must_have=["boats"]))
    out = ev.evaluate(ws, spec)
    assert out.error == "", out.error
    assert out.judgment.overall == 0.66
    assert seen["kw"]["orbit"] is True and tuple(seen["kw"]["times"]) == (0.0, 1.5)
    assert [g.gate for g in seen["inp"].gates] == ["lint", "scene_frames"]
    assert [a.text for a in seen["inp"].acceptance] == ["boats"]
