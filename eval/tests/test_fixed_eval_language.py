"""The fixed evaluator builds each cell with ITS language's runtime, not Blender's."""

from __future__ import annotations

from pathlib import Path

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
    monkeypatch.setattr(gs, "frame_stats_text", lambda ws, i=None: "frames=1")
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


def test_the_fixed_judge_reads_the_in_run_payload_for_the_same_round(monkeypatch, tmp_path):
    """N83 (audit 2026-09-24): the fixed judge built its JudgeInput by hand — no clay views, no
    GLB (so no D48 slices on a floating-part cell), connectivity without the language.  It is now
    ``round_input``: for one built GLB the fixed payload equals the in-run judge's
    (``steps._judge`` over ``ObjectPipeline``, no plan) except the acceptance list, the brief's
    ``must_have`` by design."""
    from types import SimpleNamespace

    import codeverse3d.spatial.connectivity as conn
    import codeverse3d.spatial.measure as meas
    import codeverse3d.spatial.render as rend
    from bench._fixed_eval import FixedEvaluator, acceptance_from_spec
    from codeverse3d.contracts.artifacts import (
        BuildResult,
        GateFinding,
        GateReport,
        Measurement,
        RenderSet,
        RenderView,
        Severity,
    )
    from codeverse3d.contracts.common import Track
    from codeverse3d.contracts.run import RoundRecord
    from codeverse3d.contracts.spec import Constraints, Spec
    from codeverse3d.tracks import steps
    from codeverse3d.tracks.common import Services
    from codeverse3d.tracks.static_object import ObjectPipeline
    from codeverse3d.workspace import Workspace

    ws = Workspace(tmp_path / "run").create()
    glb = ws.root / "artifacts" / "object.glb"
    glb.parent.mkdir(parents=True, exist_ok=True)
    glb.write_bytes(b"glTF")
    m = Measurement(bbox_min=(0, 0, 0), bbox_max=(1, 1, 1), extents=(1, 1, 1), center=(0.5, 0.5, 0.5),
                    tri_count=100, n_meshes=2, n_islands=2)
    monkeypatch.setattr(meas, "measure_glb", lambda p: m)
    monkeypatch.setattr(conn, "check_connectivity", lambda p, language="", planned_edges=(): GateReport(
        gate="connectivity", passed=False, findings=[GateFinding(gate="connectivity", severity=Severity.ERROR,
                                                                 target="Leg", message=f"Leg is floating ({language})")]))
    monkeypatch.setattr(rend, "render_glb", lambda p, d, *, views, mode="color", **k: RenderSet(
        views=[RenderView(name=v.name, path=str(Path(d) / f"view_{v.name}.png"), mode=mode) for v in views], renderer="fake"))
    build = BuildResult(ok=True, language="blender", glb_path=str(glb))
    lint = GateReport(gate="lint", passed=True)
    spec = Spec(id="t/stool", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a three-legged stool",
                constraints=Constraints(must_have=["three legs"]))
    seen: list = []

    class _Spy:
        def judge(self, inp):
            seen.append(inp)
            raise RuntimeError("captured")

    ev = FixedEvaluator("fake:judge", n_samples=1)
    ev._runtimes[Language.BLENDER] = SimpleNamespace(lint=lambda ws: lint, build=lambda ws, timeout_s: build)
    monkeypatch.setattr(FixedEvaluator, "judge_for", lambda self, spec: _Spy())
    ev.evaluate(ws, spec)

    # the loop's round 0 over the same build: steps._run_round's gate order, then steps._judge
    ctx = SimpleNamespace(spec=spec, plan=None, ws=ws, services=Services(), settings=ev.settings, language=Language.BLENDER,
                          judge=_Spy(), events=SimpleNamespace(emit=lambda *a, **k: None))
    pipe = ObjectPipeline()
    rec = RoundRecord(index=0, kind="baseline", build=build, measurement=pipe.measure(ctx, build))
    gates = [lint, *pipe.gates(ctx, 0, build, rec.measurement), *build.gates]
    rec.renders = pipe.render(ctx, 0, build, rec.measurement)
    steps._judge(ctx, pipe, 0, build, gates, rec, None, [])

    fixed, in_run = seen
    assert fixed.acceptance == acceptance_from_spec(spec) and in_run.acceptance == []
    assert fixed.model_dump(exclude={"acceptance"}) == in_run.model_dump(exclude={"acceptance"})
    # what the hand-built payload lacked
    assert fixed.geometry_views is not None and {v.mode for v in fixed.geometry_views.views} == {"clay"}
    assert fixed.glb_path == str(glb)
    assert "(blender)" in fixed.gates[1].errors[0].message
