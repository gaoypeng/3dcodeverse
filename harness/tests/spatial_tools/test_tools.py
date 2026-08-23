"""Tool-level tests with fakes for the sibling packages (runtime, renderer)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import codeverse.spatial.tools  # noqa: F401  (registers all tools)
from codeverse.contracts.artifacts import (
    BuildResult,
    GateFinding,
    GateReport,
    RenderSet,
    RenderView,
    Severity,
)
from codeverse.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse.spatial.registry import ToolContext, get_tool, list_tools
from codeverse.workspace import Workspace

EXPECTED_TOOLS = {"build", "measure", "render_views", "render_sheet", "isolate", "cross_section", "check_connectivity",
                  "check_contract", "compare_silhouette", "joint_sweep", "shader_probe", "scene_probe", "scene_views", "read_cookbook"}


def test_registry_has_every_tool() -> None:
    assert {t.name for t in list_tools()} == EXPECTED_TOOLS
    assert "joint_sweep" not in {t.name for t in list_tools(track="static_object", language="blender")}
    assert "shader_probe" in {t.name for t in list_tools(track="scene", language="scene_threejs")}
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
        assert not obs.ok and "run `build` first" in obs.text, name


def test_connectivity_and_section_tools(stool_ctx: ToolContext) -> None:
    obs = get_tool("check_connectivity").call(stool_ctx, {})
    assert not obs.ok and "Leg_3" in obs.text and "translate 'Leg_3' by (+0.0000, +0.0050, +0.0000)" in obs.text
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
        if self.build_ok:
            return BuildResult(ok=True, language="blender", glb_path=str(self.glb), duration_ms=12, census={"warnings": ["[EXPORT_WARN] no materials"]})
        return BuildResult(ok=False, language="blender", error_type="NameError", error_message="name 'bpyx' is not defined",
                           error_file=str(ws.src / "model.py"), error_line=7, stderr_tail="\n".join(f"l{i}" for i in range(60)) + f"\n  File \"{ws.src}/model.py\", line 7")

    def contract_doc(self):
        return "# Fake contract\n\n## Export\nthe harness exports.\n"


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, rt: _FakeRuntime) -> None:
    import codeverse.languages as langs

    monkeypatch.setattr(langs, "get_runtime", lambda language: rt)


def test_build_tool_success(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    glb = stool_ctx.workspace.artifacts / "object.glb"
    _patch_runtime(monkeypatch, _FakeRuntime(glb=glb))
    obs = get_tool("build").call(stool_ctx, {})
    assert obs.ok and obs.text.startswith("BUILD OK") and "Leg ×4" in obs.text
    assert "lint warnings" in obs.text and "uses bpy.ops.render" in obs.text and "build warnings" in obs.text
    assert str(stool_ctx.workspace.root) not in obs.text
    assert (stool_ctx.workspace.artifacts / "build_last.json").is_file()
    assert json.loads((stool_ctx.workspace.artifacts / "build_last.json").read_text())["ok"] is True
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


def test_build_unavailable_runtime(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse.spatial.tool_common as tc

    def boom(module, attr):
        raise tc.ToolUnavailable(f"{module} not importable")

    monkeypatch.setattr(tc, "lazy", boom)
    monkeypatch.setattr("codeverse.spatial.tools.lazy", boom)
    obs = get_tool("build").call(stool_ctx, {})
    assert not obs.ok and obs.text.startswith("tool build unavailable:")


# --------------------------------------------------------------------------- rendering with a fake renderer
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
    import codeverse.spatial.render as render

    _fake_render_glb.calls = 0
    monkeypatch.setattr(render, "render_glb", _fake_render_glb)
    return _fake_render_glb


def test_render_views_cached(stool_ctx: ToolContext, fake_renderer) -> None:
    obs = get_tool("render_views").call(stool_ctx, {})
    assert obs.ok and obs.images[0].endswith("sheet.png") and len(obs.images) == 5  # sheet + 4 views
    assert obs.numbers["views"] == ["front_right_34", "back_left_34", "front", "top"]
    assert "artifacts/tool_renders/r00_" in obs.text and str(stool_ctx.workspace.root) not in obs.text
    get_tool("render_views").call(stool_ctx, {})
    assert fake_renderer.calls == 1  # second call served from the on-disk cache
    obs = get_tool("render_views").call(stool_ctx, {"views": ["front", "back", "left", "right", "top"]})
    assert obs.ok and len(obs.images) == 1  # > 4 views → sheet only
    obs = get_tool("render_views").call(stool_ctx, {"views": ["frontal"]})
    assert not obs.ok and "front_right_34" in obs.text
    obs = get_tool("render_views").call(stool_ctx, {"mode": "xray"})
    assert not obs.ok and "shaded" in obs.text


def test_render_sheet_and_isolate(stool_ctx: ToolContext, fake_renderer) -> None:
    obs = get_tool("render_sheet").call(stool_ctx, {"mode": "wire"})
    assert obs.ok and len(obs.images) == 1 and obs.numbers["n_views"] == 8
    obs = get_tool("isolate").call(stool_ctx, {"part": "Leg_3"})
    assert obs.ok and obs.numbers["part"] == "Leg_3" and "| Leg_3 |" in obs.text
    obs = get_tool("isolate").call(stool_ctx, {"part": "Nope"})
    assert not obs.ok and "Leg_3" in obs.text


def test_compare_silhouette_tool(stool_ctx: ToolContext, fake_renderer) -> None:
    obs = get_tool("compare_silhouette").call(stool_ctx, {})
    assert not obs.ok and "no reference images" in obs.text
    ref = stool_ctx.workspace.root / "ref.png"
    im = Image.new("RGB", (300, 300), (250, 250, 250))
    ImageDraw.Draw(im).rectangle([60, 60, 240, 240], fill=(20, 20, 20))
    im.save(ref)
    spec = json.loads(stool_ctx.workspace.spec_path.read_text())
    spec["references"] = [{"path": "ref.png"}]
    stool_ctx.workspace.spec_path.write_text(json.dumps(spec))
    obs = get_tool("compare_silhouette").call(stool_ctx, {"view": "front"})
    assert obs.ok and obs.numbers["iou"] > 0.95 and len(obs.images) == 2 and Path(obs.images[0]).is_file()
    obs = get_tool("compare_silhouette").call(stool_ctx, {"reference_index": 3})
    assert not obs.ok and "out of range" in obs.text


def test_renderer_unavailable(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse.spatial.tool_common as tc

    def boom(module, attr):
        raise tc.ToolUnavailable(f"{module} not importable")

    monkeypatch.setattr(tc, "lazy", boom)
    obs = get_tool("render_views").call(stool_ctx, {})
    assert not obs.ok and obs.text.startswith("tool render_views unavailable:")


# --------------------------------------------------------------------------- cookbook + scene tools
def test_read_cookbook(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse.prompts as prompts

    md = "# Blender cookbook\n\nintro\n\n## Export\nharness exports\n\n## Pitfalls\n- no bpy.ops\n" + "x" * 7000
    monkeypatch.setattr(prompts, "load_text", lambda rel: md if rel == "blender/cookbook.md" else (_ for _ in ()).throw(FileNotFoundError(rel)))
    obs = get_tool("read_cookbook").call(stool_ctx, {})
    assert obs.ok and obs.text.startswith("[prompts/blender/cookbook.md]") and obs.numbers["truncated"] and "sections: Blender cookbook | Export | Pitfalls" in obs.text
    assert len(obs.text) < 6500
    obs = get_tool("read_cookbook").call(stool_ctx, {"section": "pitfalls"})
    assert obs.ok and "- no bpy.ops" in obs.text and "harness exports" not in obs.text
    obs = get_tool("read_cookbook").call(stool_ctx, {"section": "materials"})
    assert not obs.ok and "Export" in obs.text


def test_read_cookbook_falls_back_to_contract(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse.prompts as prompts

    monkeypatch.setattr(prompts, "load_text", lambda rel: (_ for _ in ()).throw(FileNotFoundError(rel)))
    _patch_runtime(monkeypatch, _FakeRuntime())
    obs = get_tool("read_cookbook").call(stool_ctx, {"section": "export"})
    assert obs.ok and "the harness exports" in obs.text and "contract_doc()" in obs.text


def test_scene_tools_degrade_when_unavailable(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse.spatial.tool_common as tc
    import codeverse.spatial.tools_scene as ts

    def boom(module, attr):
        raise tc.ToolUnavailable(f"{module} not importable")

    monkeypatch.setattr(ts, "lazy", boom)
    for name in ("joint_sweep", "shader_probe", "scene_probe", "scene_views"):
        obs = get_tool(name).call(stool_ctx, {})
        assert not obs.ok and obs.text.startswith(f"tool {name} unavailable:"), name


def test_scene_tools_with_fake_siblings(stool_ctx: ToolContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import codeverse.spatial.tools_scene as ts

    calls = {}

    def fake_check_shaders(ws):
        return GateReport(gate="shaders", passed=False, findings=[GateFinding(gate="shaders", severity=Severity.ERROR, target="src/shaders/water.js", message="ERROR: 0:12: 'vUv' undeclared", fix_hint="declare varying vec2 vUv")])

    def fake_probe_scene(ws, **kw):
        return {"census": {"meshes": 12, "lights": 2}, "fps": 58.0, "errors": []}

    def fake_joint_sweep(ws, *, n_random=8, seed=0, render=True, out_dir=None, joint=None, expected_direction=None):
        calls["n_random"], calls["joint"] = n_random, joint
        from codeverse.spatial.registry import Observation
        return Observation(ok=True, text="sweep ok", numbers={"poses": n_random})

    def fake_lazy(module, attr):
        return {"check_shaders": fake_check_shaders, "probe_scene": fake_probe_scene, "joint_sweep_observation": fake_joint_sweep}[attr]

    monkeypatch.setattr(ts, "lazy", fake_lazy)
    obs = get_tool("shader_probe").call(stool_ctx, {})
    assert not obs.ok and "vUv" in obs.text and "declare varying" in obs.text
    obs = get_tool("scene_probe").call(stool_ctx, {})
    assert obs.ok and "meshes=12" in obs.text and obs.numbers["fps"] == 58.0
    obs = get_tool("joint_sweep").call(stool_ctx, {"joints": ["hinge"], "n_samples": 5})
    assert obs.ok and calls == {"n_random": 5, "joint": "hinge"}
