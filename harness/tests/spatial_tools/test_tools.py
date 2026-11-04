"""Tool-level tests with fakes for the sibling packages (runtime, renderer)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import codeverse3d.spatial.tools  # noqa: F401  (registers all tools)
from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    Severity,
)
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.spatial.registry import ToolContext, get_tool
from codeverse3d.workspace import Workspace


def test_measure_tool(stool_ctx: ToolContext) -> None:
    obs = get_tool("measure").call(stool_ctx, {})
    assert obs.ok and "Leg ×4" in obs.text and obs.numbers["n_parts"] == 5
    obs = get_tool("measure").call(stool_ctx, {"parts": ["Seat"]})
    assert obs.ok and list(obs.numbers["parts"]) == ["Seat"]
    obs = get_tool("measure").call(stool_ctx, {"parts": ["Nope"]})
    assert not obs.ok and "Seat" in obs.text  # didactic: lists available parts


def test_connectivity_and_section_tools(stool_ctx: ToolContext) -> None:
    obs = get_tool("check_connectivity").call(stool_ctx, {})
    # the 5 mm gap is along GLB +y (up) → written in the Blender frame as +z, labelled
    assert not obs.ok and "Leg_3" in obs.text
    assert "translate 'Leg_3' by (+0.0000, +0.0000, +0.0050) m (blender frame: Z-up, -Y front)" in obs.text
    assert (stool_ctx.workspace.gates_dir(0) / "connectivity_tool.json").is_file()
    obs = get_tool("cross_section").call(stool_ctx, {"axis": "y", "at": 0.5})
    assert obs.ok and len(obs.images) == 1 and Path(obs.images[0]).is_file() and obs.numbers["n_loops"] == 4
    assert str(stool_ctx.workspace.root) not in obs.text
    obs = get_tool("cross_section").call(stool_ctx, {"axis": "q"})
    assert not obs.ok and "x|y|z" in obs.text


class _FakeRuntime:
    def __init__(self, *, lint_errors: bool = False, build_ok: bool = True, glb: Path | None = None, root: Path | None = None):
        self.lint_errors, self.build_ok, self.glb, self.root = lint_errors, build_ok, glb, root

    def lint(self, ws):
        f = [GateFinding(gate="lint:blender", severity=Severity.WARN, target=str(ws.src / "model.py"), message="uses bpy.ops.render", fix_hint="delete it")]
        if self.lint_errors:
            f.append(GateFinding(gate="lint:blender", severity=Severity.ERROR, target=str(ws.src / "model.py"), message="SyntaxError line 3", fix_hint="close the bracket"))
        return GateReport(gate="lint:blender", passed=not self.lint_errors, findings=f)

    def build(self, ws, *, timeout_s=None):
        """Publishes its result as build.json — the contract every real runtime keeps."""
        if self.build_ok:
            res = BuildResult(ok=True, language="blender", glb_path=str(self.glb), duration_ms=12, census={"warnings": ["[EXPORT_WARN] no materials"]})
        else:
            res = BuildResult(ok=False, language="blender", error_type="NameError", error_message="name 'bpyx' is not defined",
                              error_file=str(ws.src / "model.py"), error_line=7, stderr_tail="\n".join(f"l{i}" for i in range(60)) + f"\n  File \"{ws.src}/model.py\", line 7")
        ws.write_json(ws.artifacts / "build.json", res)
        return res


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, rt: object) -> None:
    import codeverse3d.languages as langs

    monkeypatch.setattr(langs, "get_runtime", lambda language: rt)


def test_a_negative_verdict_is_not_a_tool_failure(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ok`` is the verdict, ``failed`` is 'the tool could not run' (only that is an MCP error); FAIL leads the text."""
    ws = stool_ctx.workspace
    _patch_runtime(monkeypatch, _FakeRuntime(glb=ws.artifacts / "object.glb"))     # a build that works leads BUILD OK
    obs = get_tool("build").call(stool_ctx, {})
    assert obs.ok and obs.text.startswith("BUILD OK") and "Leg ×4" in obs.text and str(ws.root) not in obs.text
    ws.write_json(ws.plan_path, _stool_plan_with_missing_backrest())
    for name, head in (("check_connectivity", "connectivity: FAIL"), ("check_contract", "contract: FAIL")):
        obs = get_tool(name).call(stool_ctx, {})
        assert not obs.ok and not obs.failed, name          # the gate ran and answered FAIL
        assert obs.text.startswith(head), obs.text

    # the other direction: a tool that could not run stays an error
    obs = get_tool("measure").call(stool_ctx, {"parts": ["Nope"]})           # ToolUsageError
    assert not obs.ok and obs.failed and "Seat" in obs.text
    _patch_runtime(monkeypatch, _FakeRuntime(build_ok=False))               # code that will not run
    obs = get_tool("build").call(stool_ctx, {})
    assert not obs.ok and not obs.failed and obs.text.startswith("BUILD FAILED")
    assert obs.text.splitlines()[0].startswith("BUILD FAILED: NameError: name 'bpyx' is not defined at src/model.py:7")
    assert "l59" in obs.text and "l10" not in obs.text
    assert str(stool_ctx.workspace.root) not in obs.text and obs.numbers["error_line"] == 7


def test_render_modes_match_the_js_rig() -> None:
    """contracts.RENDER_MODES == runtime_js/render_glb.mjs MODES."""
    import re

    from codeverse3d.config import get_settings
    from codeverse3d.contracts.artifacts import RENDER_MODES

    m = re.search(r"const MODES = \[([^\]]*)\]", (get_settings().runtime_js_dir() / "render_glb.mjs").read_text())
    assert m and tuple(re.findall(r"'([a-z]+)'", m.group(1))) == RENDER_MODES


def test_scene_tools_with_fake_siblings(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse3d.spatial.tools as ts
    from codeverse3d.spatial.probes import SceneProbeResult

    shaders = GateReport.of("shader_preflight", [GateFinding(
        gate="shader_preflight", severity=Severity.ERROR, target="src/shaders/water.js:12",
        message="ERROR: 0:12: 'vUv' undeclared", fix_hint="declare varying vec2 vUv")])
    failed = GateReport(gate="scene_probe", passed=False, findings=[
        GateFinding(gate="scene_probe", severity=Severity.ERROR, target="src/scene.js", message="console: boom")])

    def fake_probe_scene(ws, **kw):
        return SceneProbeResult(gate=failed, census={"meshes": 12, "lights": 2}, ok=True, findings=["[error] src/scene.js: console: boom"])

    monkeypatch.setattr(ts, "run_probe", lambda ws, **kw: (GateReport.of("scene_probe"), shaders, {}))
    obs = get_tool("shader_probe").call(stool_ctx, {})
    assert not obs.ok and not obs.failed and obs.text.startswith("shader probe: FAIL") and "declare varying" in obs.text
    scene_ctx = ToolContext(workspace=stool_ctx.workspace, language="scene_threejs", track="scene")
    _patch_runtime(monkeypatch, SimpleNamespace(probe=fake_probe_scene))   # D101: via the language's SceneRuntime
    obs = get_tool("scene_probe").call(scene_ctx, {})
    assert not obs.ok and not obs.failed and obs.text.startswith("scene probe: FAIL")
    assert "meshes=12" in obs.text and obs.numbers["census"]["meshes"] == 12
    obs = get_tool("joint_sweep").call(stool_ctx, {})
    assert not obs.ok and obs.failed and "run `build`" in obs.text   # no robot.urdf in a static workspace


class _GlRuntime:
    """A graphics build: ok, no GLB (the gl tools pass times / preview / size kwargs)."""

    def __init__(self, language: str, census: dict, extra_paths: dict):
        self.language, self.census, self.extra_paths = language, census, extra_paths

    def lint(self, ws):
        return GateReport(gate="lint", passed=True, findings=[])

    def build(self, ws, **_kw):
        return BuildResult(ok=True, language=self.language, glb_path=None, duration_ms=9,
                           census=self.census, extra_paths=self.extra_paths)


def test_gl_tools_lead_with_the_frame_gate_verdict(tmp_ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed ``gl_frames`` gate leads the text above every "… OK" headline, and is a verdict, not a tool failure."""
    import codeverse3d.languages._gl_common as gl_build  # the one metrics reader
    from codeverse3d.spatial.frame_stats import FrameStat, SequenceStats

    ctx = ToolContext(workspace=tmp_ws, language="glsl_shader", track="graphics")
    _patch_runtime(monkeypatch, _GlRuntime("glsl_shader", {"renderer": "moderngl"},
                                           {"frames": str(tmp_ws.artifacts / "frames"),
                                            "sheet": str(tmp_ws.artifacts / "frames_sheet.png")}))

    def _frame(t: float, path: str) -> FrameStat:
        return FrameStat(time=t, path=path, mean_lum=0.4, std_lum=0.2, pct_black=0.01, pct_blown=0.01,
                         colourfulness=0.3, edge_density=0.05)

    stats = SequenceStats(frames=[_frame(0.0, "f0.png")], mean_diff=0.0)
    bad = GateReport(gate="gl_frames", passed=False, findings=[
        GateFinding(gate="gl_frames", severity=Severity.ERROR, message="static image", data={"kind": "static"},
                    fix_hint="animate with u_time")])
    monkeypatch.setattr(gl_build, "read_metrics", lambda ws: (stats, bad))
    for name, args, ok_headline in (("build", {}, "BUILD OK"), ("gl_frames", {"times": [0.0, 1.0]}, "FRAMES OK")):
        obs = get_tool(name).call(ctx, args)
        assert not obs.ok and not obs.failed, name
        assert obs.text.splitlines()[0].startswith("FRAME GATE: FAIL — 1 error(s)"), (name, obs.text)
        assert "do not re-run blind" in obs.text.splitlines()[0], name
        assert ok_headline in obs.text and "static image" in obs.text, name
        assert obs.numbers["gate_errors"] == 1, name


def test_gl_frames_renders_the_judged_times_by_default(tmp_ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression (audit 2026-09-24 N17): the tool rendered its own (0, 1, 2.5, 4, 6) whatever the
    loop; on a 2 s loop the judge sees (0, .5, 1, 1.5, 2) — the build's `judge_times(duration)`."""
    from codeverse3d.contracts.plan import GraphicsPlan, PassPlan
    from codeverse3d.languages.glsl_shader import GlslShaderRuntime
    from codeverse3d.spatial.gl_render import GlResult

    seen: list[list[float]] = []

    class _Host:
        def render_fragment_shader(self, frag_src, out_dir, *, times, **kw):
            seen.append([float(t) for t in times])
            return GlResult(ok=False, mode="shader", stage="compile", error_type="Error", error_message="stop here")

    tmp_ws.write_json(tmp_ws.plan_path, GraphicsPlan(
        title="T", summary="s", style="st", passes=[PassPlan(name="Sky", description="d")], motion="m",
        key_visuals=["k"], duration_s=2.0))
    (tmp_ws.src / "shader.frag").write_text("void mainImage(out vec4 fragColor, in vec2 fragCoord) { fragColor = vec4(1.0); }\n")
    _patch_runtime(monkeypatch, GlslShaderRuntime(host=_Host()))
    obs = get_tool("gl_frames").call(ToolContext(workspace=tmp_ws, language="glsl_shader", track="graphics"), {})
    assert seen == [[0.0, 0.5, 1.0, 1.5, 2.0]], obs.text


def _stool_plan_with_missing_backrest() -> StaticPlan:
    return StaticPlan(object_name="Stool", summary="s", overall_bbox=BBox(center=(0, 0, 0.225), extents=(0.4, 0.4, 0.45)),
                      parts=[PartPlan(name="Seat", role="r", description="d", bbox=BBox(center=(0, 0, 0.43), extents=(0.4, 0.4, 0.04))),
                             PartPlan(name="Leg", role="r", description="d", bbox=BBox(center=(0, 0, 0.205), extents=(0.04, 0.04, 0.41)), instances=4),
                             PartPlan(name="Backrest", role="r", description="d", bbox=BBox(center=(0, 0.18, 0.6), extents=(0.4, 0.03, 0.3)))])


class _SceneRuntime:
    """A scene build whose probe raised ``errors`` (the census was taken: only the verdict differs)."""

    def __init__(self, errors: list[GateFinding]) -> None:
        self.errors = errors

    def lint(self, ws):
        return GateReport(gate="lint:scene_blender", passed=True, findings=[])

    def build(self, ws, *, timeout_s=None):
        first = self.errors[0]
        res = BuildResult(ok=False, language="scene_blender", error_type=first.data.get("kind") or first.gate,
                          error_message=first.message, error_file=first.target or "", census={"lights": 0, "meshes": 12},
                          gates=[GateReport.of("scene_probe", self.errors)])
        ws.write_json(ws.artifacts / "build.json", res)
        return res


def test_a_session_build_reports_an_unlit_env_above_build_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """D112: a session build whose only ERROR is the unlit env leads with SCENE UNLIT above BUILD OK; any other ERROR is BUILD FAILED."""
    ctx = ToolContext(workspace=Workspace(tmp_path / "ws").create(), language="scene_blender", track="scene")
    unlit = GateFinding(gate="scene_probe", severity=Severity.ERROR, target="src/env.py", data={"kind": "unlit"},
                        message="scene has no lamps and no world: it renders black")
    _patch_runtime(monkeypatch, _SceneRuntime([unlit]))
    obs = get_tool("build").call(ctx, {})
    first, second = obs.text.splitlines()[:2]
    assert obs.ok and not obs.failed and obs.numbers["env_unlit"] is True
    assert first.startswith("SCENE UNLIT") and "src/env.py" in first and "renders black" in first
    assert second.startswith("BUILD OK")

    zone = GateFinding(gate="scene_probe", severity=Severity.ERROR, target="src/zones/quay.py:12",
                       message="zone quay raised NameError: name 'x' is not defined", data={"kind": "zone"})
    _patch_runtime(monkeypatch, _SceneRuntime([zone, unlit]))
    obs = get_tool("build").call(ctx, {})
    assert not obs.ok and obs.text.startswith("BUILD FAILED") and "SCENE UNLIT" not in obs.text
