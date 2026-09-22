"""Tool-level tests with fakes for the sibling packages (runtime, renderer)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import codeverse3d.spatial.tools  # noqa: F401  (registers all tools)
from codeverse3d.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.spatial.registry import ToolContext, get_tool, list_tools
from codeverse3d.workspace import Workspace

#: tools every workspace gets (no track/language restriction)
CORE_TOOLS = {"build", "measure", "render_views", "render_sheet", "isolate", "cross_section", "check_connectivity",
              "check_contract", "compare_reference"}
#: track- / language-scoped tools (documented; keep in sync when registering a new one)
SCOPED_TOOLS = {
    "joint_sweep",  # articulated_object
    "shader_probe", "scene_probe", "scene_views", "check_placement",  # scene_threejs
    "gl_probe", "gl_frames",  # graphics (glsl_shader / opengl_python)
}
EXPECTED_TOOLS = CORE_TOOLS | SCOPED_TOOLS


def test_registry_has_every_tool() -> None:
    registered = {t.name for t in list_tools()}
    assert registered == EXPECTED_TOOLS, (
        f"registry drifted: unexpected {sorted(registered - EXPECTED_TOOLS)}, "
        f"missing {sorted(EXPECTED_TOOLS - registered)} — update CORE_TOOLS/SCOPED_TOOLS above")
    static_blender = {t.name for t in list_tools(track="static_object", language="blender")}
    assert "joint_sweep" not in static_blender and "gl_probe" not in static_blender
    scene = {t.name for t in list_tools(track="scene", language="scene_threejs")}
    assert "shader_probe" in scene
    graphics = {t.name for t in list_tools(track="graphics", language="glsl_shader")}
    assert {"gl_probe", "gl_frames"} <= graphics and "scene_probe" not in graphics
    # V11a: the object-GLB toolset never reaches scene/graphics agents — their builds
    # never write artifacts/object.glb, so every one of these was a dead-end refusal
    object_glb_tools = {"measure", "check_connectivity", "check_contract", "cross_section", "isolate",
                        "render_views", "render_sheet", "compare_reference"}
    assert not object_glb_tools & scene and not object_glb_tools & graphics
    assert "build" in scene and "build" in graphics  # build itself stays universal
    articulated = {t.name for t in list_tools(track="articulated_object", language="urdf_blender")}
    assert object_glb_tools | {"joint_sweep"} <= articulated  # articulated keeps the object toolset
    for t in list_tools():
        assert t.schema()["type"] == "object" and t.description


def test_measure_tool(stool_ctx: ToolContext) -> None:
    obs = get_tool("measure").call(stool_ctx, {})
    assert obs.ok and "Leg ×4" in obs.text and obs.numbers["n_parts"] == 5
    obs = get_tool("measure").call(stool_ctx, {"parts": ["Seat"]})
    assert obs.ok and list(obs.numbers["parts"]) == ["Seat"]
    obs = get_tool("measure").call(stool_ctx, {"parts": ["Nope"]})
    assert not obs.ok and "Seat" in obs.text  # didactic: lists available parts


def test_tools_without_glb(tmp_ws: Workspace) -> None:
    ctx = ToolContext(workspace=tmp_ws, language="blender")
    for name in ("measure", "check_connectivity", "cross_section", "render_views", "isolate"):
        obs = get_tool(name).call(ctx, {"part": "x"} if name == "isolate" else {})
        # a missing artefact IS a failure: the tool could not run at all
        assert not obs.ok and obs.failed and "run `build` first" in obs.text, name


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


def test_check_contract_tool(stool_ctx: ToolContext) -> None:
    obs = get_tool("check_contract").call(stool_ctx, {})
    assert not obs.ok and "plan.json not found" in obs.text
    plan = StaticPlan(object_name="Stool", summary="s", overall_bbox=BBox(center=(0, 0, 0.225), extents=(0.4, 0.4, 0.45)),
                      parts=[PartPlan(name="Seat", role="r", description="d", bbox=BBox(center=(0, 0, 0.43), extents=(0.4, 0.4, 0.04))),
                             PartPlan(name="Leg", role="r", description="d", bbox=BBox(center=(0, 0, 0.205), extents=(0.04, 0.04, 0.41)), instances=4),
                             PartPlan(name="Backrest", role="r", description="d", bbox=BBox(center=(0, 0.18, 0.6), extents=(0.4, 0.03, 0.3)))])
    stool_ctx.workspace.write_json(stool_ctx.workspace.plan_path, plan)
    obs = get_tool("check_contract").call(stool_ctx, {})
    assert not obs.ok and "Backrest" in obs.text and "missing" in obs.text


# --------------------------------------------------------------------------- build with a fake runtime
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


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, rt: _FakeRuntime) -> None:
    import codeverse3d.languages as langs

    monkeypatch.setattr(langs, "get_runtime", lambda language: rt)


def test_build_tool_success(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    glb = stool_ctx.workspace.artifacts / "object.glb"
    _patch_runtime(monkeypatch, _FakeRuntime(glb=glb))
    obs = get_tool("build").call(stool_ctx, {})
    assert obs.ok and obs.text.startswith("BUILD OK") and "Leg ×4" in obs.text
    assert "lint warnings" in obs.text and "uses bpy.ops.render" in obs.text and "build warnings" in obs.text
    assert str(stool_ctx.workspace.root) not in obs.text
    assert json.loads((stool_ctx.workspace.artifacts / "build.json").read_text())["ok"] is True
    assert (stool_ctx.workspace.artifacts / "measurement.json").is_file()


def test_build_tool_failure_is_error_first(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch, _FakeRuntime(build_ok=False))
    obs = get_tool("build").call(stool_ctx, {})
    assert not obs.ok
    first = obs.text.splitlines()[0]
    assert first.startswith("BUILD FAILED: NameError: name 'bpyx' is not defined at src/model.py:7")
    assert "l59" in obs.text and "l10" not in obs.text  # ≤ 30-line stderr tail
    assert str(stool_ctx.workspace.root) not in obs.text
    assert obs.numbers["error_line"] == 7


def test_build_tool_lint_blocks(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime(monkeypatch, _FakeRuntime(lint_errors=True))
    obs = get_tool("build").call(stool_ctx, {})
    assert not obs.ok and obs.text.startswith("LINT FAILED") and "close the bracket" in obs.text
    assert obs.numbers["stage"] == "lint"


def test_a_negative_verdict_is_not_a_tool_failure(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ok`` is the VERDICT, ``failed`` is 'the tool could not run', and only ``failed``
    reaches the model as an MCP error.  Over 217 recorded gemini-cli sessions the old
    ``is_error = not ok`` reported 63% of 1404 joint_sweep calls and 23% of 2073 builds as
    broken calls, and the model retries a broken call at ~117k prompt tokens each.  Both
    directions here, plus the rule that makes it safe: the FAIL verdict LEADS the text."""
    import codeverse3d.spatial.tools as ts

    ws = stool_ctx.workspace
    ws.write_json(ws.plan_path, _stool_plan_with_missing_backrest())
    for name, head in (("check_connectivity", "connectivity: FAIL"), ("check_contract", "contract: FAIL")):
        obs = get_tool(name).call(stool_ctx, {})
        assert not obs.ok and not obs.failed, name          # the gate ran and answered FAIL
        assert obs.text.startswith(head), obs.text

    # the other direction: a tool that could not run stays an error
    obs = get_tool("measure").call(stool_ctx, {"parts": ["Nope"]})           # ToolUsageError
    assert not obs.ok and obs.failed and "Seat" in obs.text
    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(ts, "measure_glb", boom)
    obs = get_tool("measure").call(stool_ctx, {})
    assert not obs.ok and obs.failed and "measure failed: RuntimeError: boom" in obs.text
    monkeypatch.undo()

    _patch_runtime(monkeypatch, _FakeRuntime(build_ok=False))               # code that will not run
    obs = get_tool("build").call(stool_ctx, {})
    assert not obs.ok and not obs.failed and obs.text.startswith("BUILD FAILED")
    _patch_runtime(monkeypatch, _FakeRuntime(lint_errors=True))
    obs = get_tool("build").call(stool_ctx, {})
    assert not obs.ok and not obs.failed and obs.text.startswith("LINT FAILED")


def test_build_that_leaves_no_readable_glb_is_a_failure(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime reported success and left nothing measurable: the tool could not
    answer (a real error), and the headline must not read BUILD OK."""
    _patch_runtime(monkeypatch, _FakeRuntime(glb=stool_ctx.workspace.artifacts / "gone.glb"))
    obs = get_tool("build").call(stool_ctx, {})
    assert not obs.ok and obs.failed
    assert obs.text.startswith("BUILD PRODUCED NO USABLE GLB") and "GLB unreadable" in obs.text


def test_build_failure_refuses_stale_glb(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed build after a success: the old object.glb survives as evidence but
    glb_path refuses to treat it as current (build.json says ok:false)."""
    _patch_runtime(monkeypatch, _FakeRuntime(glb=stool_ctx.workspace.artifacts / "object.glb"))
    assert get_tool("build").call(stool_ctx, {}).ok
    assert get_tool("measure").call(stool_ctx, {}).ok  # ok:true status → still usable
    _patch_runtime(monkeypatch, _FakeRuntime(build_ok=False))
    assert not get_tool("build").call(stool_ctx, {}).ok
    assert json.loads((stool_ctx.workspace.artifacts / "build.json").read_text())["ok"] is False
    assert (stool_ctx.workspace.artifacts / "object.glb").is_file()  # evidence stays on disk
    for name in ("measure", "render_views", "check_connectivity"):
        obs = get_tool(name).call(stool_ctx, {})
        assert not obs.ok and "build FAILED" in obs.text, name


def test_build_lint_fail_refuses_stale_glb(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lint refusal is the latest build status: the previous build.json
    (ok: true) + object.glb are no longer readable as current."""
    _patch_runtime(monkeypatch, _FakeRuntime(glb=stool_ctx.workspace.artifacts / "object.glb"))
    assert get_tool("build").call(stool_ctx, {}).ok
    _patch_runtime(monkeypatch, _FakeRuntime(lint_errors=True))
    obs = get_tool("build").call(stool_ctx, {})
    assert not obs.ok and obs.text.startswith("LINT FAILED")
    last = json.loads((stool_ctx.workspace.artifacts / "build.json").read_text())
    assert last["ok"] is False and last["error_type"] == "LintError"
    obs = get_tool("measure").call(stool_ctx, {})
    assert not obs.ok and "build FAILED" in obs.text


def test_hand_placed_glb_without_build_status_still_measures(stool_ctx: ToolContext) -> None:
    """No build.json → glb_path stays permissive: first-measure flows and
    hand-assembled (test/import) workspaces keep working."""
    assert not (stool_ctx.workspace.artifacts / "build.json").exists()
    assert get_tool("measure").call(stool_ctx, {}).ok


def _fake_render_glb(glb, out_dir, *, views=None, mode="shaded", width=768, height=768, isolate=None, explode=0.0, sheet=True, background="studio", **_):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rviews = []
    for v in views or []:
        p = out_dir / f"view_{v.name}.png"
        im = Image.new("RGB", (width, height), (255, 255, 255))
        ImageDraw.Draw(im).rectangle([width // 4, height // 4, 3 * width // 4, 3 * height // 4], fill=(0, 0, 0))
        im.save(p)
        rviews.append(RenderView(name=v.name, path=str(p), mode=mode, width=width, height=height, camera_position=(1.0, 1.0, 1.0), fov=35))
    sheet_p = None
    if sheet:
        sheet_p = out_dir / "sheet.png"
        Image.new("RGB", (64, 64), (200, 200, 200)).save(sheet_p)
    _fake_render_glb.calls += 1
    return RenderSet(views=rviews, contact_sheet=str(sheet_p) if sheet_p else None, renderer="fake")


_fake_render_glb.calls = 0


@pytest.fixture
def fake_renderer(monkeypatch: pytest.MonkeyPatch):
    import codeverse3d.spatial.render as render

    _fake_render_glb.calls = 0
    monkeypatch.setattr(render, "render_glb", _fake_render_glb)
    return _fake_render_glb


def test_render_views_cached(stool_ctx: ToolContext, fake_renderer) -> None:
    obs = get_tool("render_views").call(stool_ctx, {})
    assert obs.ok and obs.images[0].endswith("sheet.png") and len(obs.images) == 5  # sheet + 4 views
    assert obs.numbers["views"] == ["front_right_high", "back_left_high", "front", "top"]
    assert "artifacts/tool_renders/r00_" in obs.text and str(stool_ctx.workspace.root) not in obs.text
    again = get_tool("render_views").call(stool_ctx, {})
    # same args → the same deterministic out_dir, which is what lets render_glb's OWN cache
    # (sha256 of the glb + CACHE_VERSION + rig signature) skip the work.  It is reached every
    # time: tool_common used to keep a second size+mtime marker here and short-circuit above
    # it, which served stale PNGs.  This fake renderer has no cache, hence two calls.
    assert again.images == obs.images and fake_renderer.calls == 2
    obs = get_tool("render_views").call(stool_ctx, {"views": ["front", "back", "left", "right", "top"]})
    assert obs.ok and len(obs.images) == 1  # > 4 views → sheet only
    obs = get_tool("render_views").call(stool_ctx, {"views": ["frontal"]})
    assert not obs.ok and "front_right_high" in obs.text
    obs = get_tool("render_views").call(stool_ctx, {"mode": "xray"})
    assert not obs.ok and "shaded" in obs.text
    # 'depth' was advertised from the first commit and never drawn by any renderer:
    # a usage error with the mode list, not a RenderError from deep inside the rig
    obs = get_tool("render_views").call(stool_ctx, {"mode": "depth"})
    assert not obs.ok and "shaded" in obs.text and "failed" not in obs.text


def test_render_tool_with_no_view_is_a_tool_failure(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    """A renderer that comes back with nothing left the agent no picture and no verdict:
    ``failed`` (an MCP error), not an ordinary result whose text happens to say '0 view(s)'."""
    import codeverse3d.spatial.render as render

    monkeypatch.setattr(render, "render_glb", lambda *a, **k: RenderSet(views=[], renderer="fake"))
    for name, args in (("render_views", {}), ("render_sheet", {}), ("isolate", {"part": "Leg_3"})):
        obs = get_tool(name).call(stool_ctx, args)
        assert not obs.ok and obs.failed, name
        assert obs.text.startswith("RENDER PRODUCED NO VIEWS"), name


def test_render_modes_match_the_js_rig() -> None:
    """One mode tuple: contracts.RENDER_MODES ↔ runtime_js/render_glb.mjs MODES ↔ the arg schema."""
    import re

    from codeverse3d.config import get_settings
    from codeverse3d.contracts.artifacts import RENDER_MODES

    src = (get_settings().runtime_js_dir() / "render_glb.mjs").read_text()
    m = re.search(r"const MODES = \[([^\]]*)\]", src)
    assert m and tuple(re.findall(r"'([a-z]+)'", m.group(1))) == RENDER_MODES
    assert "depth" not in RENDER_MODES
    for name in ("render_views", "render_sheet"):
        assert get_tool(name).schema()["properties"]["mode"]["description"] == " | ".join(RENDER_MODES)


def test_render_sheet_and_isolate(stool_ctx: ToolContext, fake_renderer) -> None:
    obs = get_tool("render_sheet").call(stool_ctx, {"mode": "wire"})
    assert obs.ok and len(obs.images) == 1 and obs.numbers["n_views"] == 14
    obs = get_tool("isolate").call(stool_ctx, {"part": "Leg_3"})
    assert obs.ok and obs.numbers["part"] == "Leg_3" and "| Leg_3 |" in obs.text
    obs = get_tool("isolate").call(stool_ctx, {"part": "Nope"})
    assert not obs.ok and "Leg_3" in obs.text


def test_scene_tools_with_fake_siblings(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse3d.languages.scene_threejs as st
    import codeverse3d.spatial.tools as ts
    from codeverse3d.spatial.probes import SceneProbeResult

    shaders = GateReport.of("shader_preflight", [GateFinding(
        gate="shader_preflight", severity=Severity.ERROR, target="src/shaders/water.js:12",
        message="ERROR: 0:12: 'vUv' undeclared", fix_hint="declare varying vec2 vUv")])
    failed = GateReport(gate="scene_probe", passed=False, findings=[
        GateFinding(gate="scene_probe", severity=Severity.ERROR, target="src/scene.js", message="console: boom")])

    def fake_probe_scene(ws, **kw):
        return SceneProbeResult(gate=failed, census={"meshes": 12, "lights": 2}, ok=True, findings=["[error] src/scene.js: console: boom"])

    # shader_probe reports the BUILD's probe + preflight (one boot), never a driver of its own
    monkeypatch.setattr(st, "probe_and_preflight", lambda ws, **kw: (GateReport.of("scene_probe"), shaders, {}))
    monkeypatch.setattr(ts, "probe_scene", fake_probe_scene)
    obs = get_tool("shader_probe").call(stool_ctx, {})
    assert not obs.ok and not obs.failed and "vUv" in obs.text and "declare varying" in obs.text
    assert obs.text.startswith("shader probe: FAIL")        # a shader that will not compile is a verdict
    # a scene that never booted has no preflight: the probe's report says why
    skipped = GateReport(gate="shader_preflight", passed=False)
    boot = GateReport.of("scene_probe", [GateFinding(gate="scene_probe", severity=Severity.ERROR, target="src/scene.js",
                                                     message="[import] SyntaxError: Unexpected token")])
    monkeypatch.setattr(st, "probe_and_preflight", lambda ws, **kw: (boot, skipped, {}))
    obs = get_tool("shader_probe").call(stool_ctx, {})
    assert not obs.ok and not obs.failed and "SyntaxError" in obs.text
    # the driver died: the tool could not run
    died = GateReport.of("scene_probe", [GateFinding(gate="scene_probe", severity=Severity.ERROR, target="src/scene.js",
                                                     message="scene probe could not run: timeout", data={"harness_failure": True})])
    monkeypatch.setattr(st, "probe_and_preflight", lambda ws, **kw: (died, skipped, {}))
    obs = get_tool("shader_probe").call(stool_ctx, {})
    assert obs.failed and "could not run" in obs.text
    obs = get_tool("scene_probe").call(stool_ctx, {})
    # ok = the gate verdict; failed = the probe TOOL could not run.  The gate fails on
    # agent-fixable findings here, so the observation is a FAIL, not a tool error
    assert not obs.ok and not obs.failed
    assert obs.text.startswith("scene probe: FAIL")
    assert "meshes=12" in obs.text and "boom" in obs.text and obs.numbers["census"]["meshes"] == 12
    monkeypatch.setattr(ts, "probe_scene", lambda ws, **kw: SceneProbeResult(gate=failed, errors=["driver died"], ok=False))
    obs = get_tool("scene_probe").call(stool_ctx, {})
    assert not obs.ok and obs.failed and "driver died" in obs.text
    obs = get_tool("joint_sweep").call(stool_ctx, {})
    assert not obs.ok and obs.failed and "run `build`" in obs.text   # no robot.urdf in a static workspace


# --------------------------------------------------------------------------- build without a GLB (scene / graphics)
class _NoGlbRuntime(_FakeRuntime):
    """Build succeeds with glb_path=None (scene_threejs / graphics runtimes)."""

    def __init__(self, language: str, census: dict, extra_paths: dict | None = None):
        super().__init__()
        self.language, self.census, self.extra_paths = language, census, extra_paths or {}

    def build(self, ws, *, timeout_s=None):
        return BuildResult(ok=True, language=self.language, glb_path=None, duration_ms=9,
                           census=self.census, extra_paths=self.extra_paths)


def test_build_tool_scene_reports_probe_census(tmp_ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = ToolContext(workspace=tmp_ws, language="scene_threejs", track="scene")
    rt = _NoGlbRuntime("scene_threejs", {"meshes": 12, "lights": 2, "fps": 58.0},
                       {"scene_probe": str(tmp_ws.artifacts / "scene_probe.json")})
    _patch_runtime(monkeypatch, rt)
    obs = get_tool("build").call(ctx, {})
    assert obs.ok, obs.text
    assert "no GLB path" not in obs.text
    assert "scene probe census" in obs.text and "meshes=12" in obs.text
    assert obs.numbers["census"]["fps"] == 58.0


def test_build_tool_graphics_reports_frames(tmp_ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse3d.languages._gl_common as gl_build  # the one metrics reader (gl_build is a shim over it)
    from codeverse3d.spatial.frame_stats import FrameStat, SequenceStats

    ctx = ToolContext(workspace=tmp_ws, language="glsl_shader", track="graphics")
    rt = _NoGlbRuntime("glsl_shader", {"renderer": "moderngl"},
                       {"frames": str(tmp_ws.artifacts / "frames"), "sheet": str(tmp_ws.artifacts / "frames_sheet.png")})
    _patch_runtime(monkeypatch, rt)
    def _frame(t: float, path: str) -> FrameStat:
        return FrameStat(time=t, path=path, mean_lum=0.4, std_lum=0.2, pct_black=0.01, pct_blown=0.01,
                         colourfulness=0.3, edge_density=0.05)

    stats = SequenceStats(frames=[_frame(0.0, "f0.png"), _frame(1.0, "f1.png")], mean_diff=0.1)
    gate = GateReport(gate="gl_frames", passed=True)
    monkeypatch.setattr(gl_build, "read_metrics", lambda ws: (stats, gate))
    obs = get_tool("build").call(ctx, {})
    assert obs.ok, obs.text
    assert "no GLB path" not in obs.text
    assert "frames=2" in obs.text and "sheet:" in obs.text
    assert obs.numbers["n_frames"] == 2 and obs.numbers["gate_errors"] == 0
    # a failing gl_frames gate flips ok
    bad = GateReport(gate="gl_frames", passed=False,
                     findings=[GateFinding(gate="gl_frames", severity=Severity.ERROR, message="static image",
                                           data={"kind": "static"})])
    monkeypatch.setattr(gl_build, "read_metrics", lambda ws: (stats, bad))
    obs = get_tool("build").call(ctx, {})
    assert not obs.ok and "static image" in obs.text


class _GlRuntime(_NoGlbRuntime):
    """Graphics runtime whose build takes the gl tools' kwargs (times / preview / size)."""

    def build(self, ws, **_kw):
        return BuildResult(ok=True, language=self.language, glb_path=None, duration_ms=9,
                           census=self.census, extra_paths=self.extra_paths)


def test_gl_tools_lead_with_the_frame_gate_verdict(tmp_ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shader compiled and the frames rendered, and the ``gl_frames`` gate still failed.
    All three tools that print an "… OK" headline over that gate (``build`` on a graphics
    workspace, ``gl_probe``, ``gl_frames``) must put the FAIL verdict ABOVE it — otherwise
    the only verdict the model reads is "PROBE OK" — and none of them is a tool failure:
    the code ran, and re-running it blind is exactly the retry this costs money."""
    import codeverse3d.languages._gl_common as gl_build  # the one metrics reader
    from codeverse3d.spatial.frame_stats import FrameStat, SequenceStats

    ctx = ToolContext(workspace=tmp_ws, language="glsl_shader", track="graphics")
    _patch_runtime(monkeypatch, _GlRuntime("glsl_shader", {"renderer": "moderngl"}))
    stats = SequenceStats(frames=[FrameStat(time=0.0, path="f0.png", mean_lum=0.4, std_lum=0.2, pct_black=0.01,
                                            pct_blown=0.01, colourfulness=0.3, edge_density=0.05)], mean_diff=0.0)
    bad = GateReport(gate="gl_frames", passed=False, findings=[
        GateFinding(gate="gl_frames", severity=Severity.ERROR, message="static image", data={"kind": "static"},
                    fix_hint="animate with u_time")])
    monkeypatch.setattr(gl_build, "read_metrics", lambda ws: (stats, bad))
    for name, args, ok_headline in (("build", {}, "BUILD OK"), ("gl_probe", {}, "PROBE OK"),
                                    ("gl_frames", {"times": [0.0, 1.0]}, "FRAMES OK")):
        obs = get_tool(name).call(ctx, args)
        assert not obs.ok and not obs.failed, name
        assert obs.text.splitlines()[0].startswith("FRAME GATE: FAIL — 1 error(s)"), (name, obs.text)
        assert "do not re-run blind" in obs.text.splitlines()[0], name
        assert ok_headline in obs.text and "static image" in obs.text, name
        assert obs.numbers["gate_errors"] == 1, name


def test_load_plan_recognises_graphics_plan(tmp_ws: Workspace) -> None:
    from codeverse3d.contracts.plan import GraphicsPlan, PassPlan
    from codeverse3d.spatial.tool_common import load_plan

    plan = GraphicsPlan(title="Neon rain", summary="s", style="cyberpunk",
                        passes=[PassPlan(name="Rain", description="drops")])
    tmp_ws.write_json(tmp_ws.plan_path, plan)
    loaded = load_plan(tmp_ws.plan_path)
    assert isinstance(loaded, GraphicsPlan) and loaded.title == "Neon rain"


# --------------------------------------------------------------------------- the gate tools on a stool
def _stool_plan_with_missing_backrest() -> StaticPlan:
    return StaticPlan(object_name="Stool", summary="s", overall_bbox=BBox(center=(0, 0, 0.225), extents=(0.4, 0.4, 0.45)),
                      parts=[PartPlan(name="Seat", role="r", description="d", bbox=BBox(center=(0, 0, 0.43), extents=(0.4, 0.4, 0.04))),
                             PartPlan(name="Leg", role="r", description="d", bbox=BBox(center=(0, 0, 0.205), extents=(0.04, 0.04, 0.41)), instances=4),
                             PartPlan(name="Backrest", role="r", description="d", bbox=BBox(center=(0, 0.18, 0.6), extents=(0.4, 0.03, 0.3)))])


def test_connectivity_tool_resolves_instance_names_like_the_track_does(stool_ctx: ToolContext) -> None:
    """The plan says ``Leg`` attaches to ``Seat``; the GLB has ``Leg_0..3``.  The tool used to
    hand the gate the raw plan names, so the agent-facing ledger listed ``Leg`` under
    planned_unresolved while the track's gate resolved it (965 of 2 666 raw names over 357
    stored rounds, 2026-08-30).  One resolver now: ``spatial.contract.planned_joins``."""
    ws = stool_ctx.workspace
    ws.write_json(ws.plan_path, StaticPlan(
        object_name="Stool", summary="s", overall_bbox=BBox(center=(0, 0, 0.225), extents=(0.4, 0.4, 0.45)),
        parts=[PartPlan(name="Seat", role="r", description="d", bbox=BBox(center=(0, 0, 0.43), extents=(0.4, 0.4, 0.04))),
               PartPlan(name="Leg", role="r", description="d", bbox=BBox(center=(0, 0, 0.205), extents=(0.04, 0.04, 0.41)), attach_to="Seat", instances=4)]))
    get_tool("check_connectivity").call(stool_ctx, {})
    report = json.loads((ws.gates_dir(0) / "connectivity_tool.json").read_text())
    ledger = next(f["data"] for f in report["findings"] if "planned" in (f.get("data") or {}))
    assert ledger["planned_unresolved"] == []
    assert {(a, b) for a, b, *_ in ledger["planned"]} == {(f"Leg_{i}", "Seat") for i in range(4)}
    assert {row[3] for row in ledger["planned"] if row[0] == "Leg_3"} == {"open"}   # the 5 mm floating leg


def test_a_failed_build_tells_the_agent_WHY_not_to_build_again(tmp_ws):
    """After a failed build the GLB is absent (staging publishes nothing), and the
    old order reported 'does not exist yet — run `build` first' to an agent that had
    just built: art_med_tool_chest burned its remaining turns on that (2026-08-27)."""
    from codeverse3d.spatial.tool_common import ToolUsageError, glb_path

    ctx = ToolContext(workspace=tmp_ws, language="urdf_blender", track="articulated_object")
    tmp_ws.artifacts.mkdir(parents=True, exist_ok=True)
    tmp_ws.write_json(tmp_ws.artifacts / "build.json",
                      BuildResult(ok=False, language="urdf_blender", error_type="RestPenetration",
                                  error_message="links interpenetrate by 140.0 mm at lid/base"))
    with pytest.raises(ToolUsageError) as e:
        glb_path(ctx)
    msg = str(e.value)
    assert "FAILED" in msg and "interpenetrate" in msg
    assert "run `build` first" not in msg, "a build that ran and failed is not 'not built yet'"
