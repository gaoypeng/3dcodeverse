"""The ``compare_reference`` spatial tool (side-by-side sheet + IoU, no model call)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import codeverse3d.spatial.tools  # noqa: F401  (registers every tool)
from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.reference import SYNTH_NOTE
from codeverse3d.spatial.registry import ToolContext, get_tool
from codeverse3d.workspace import Workspace
from tests.spatial_tools.conftest import build_stool


def _ref_png(path: Path) -> Path:
    im = Image.new("RGB", (512, 512), (245, 245, 243))
    ImageDraw.Draw(im).rectangle([150, 90, 360, 440], fill=(60, 40, 25))
    im.save(path)
    return path


@pytest.fixture
def ctx(tmp_ws: Workspace, tmp_path: Path, monkeypatch) -> ToolContext:
    (tmp_ws.artifacts / "object.glb").write_bytes(build_stool(tmp_path / "stool.glb").read_bytes())
    ref = _ref_png(tmp_path / "ref.png")
    tmp_ws.spec_path.write_text(json.dumps({
        "id": "t", "track": "static_object", "language": "blender", "prompt": "a stool",
        "references": [{"path": str(ref), "role": "target", "note": f"{SYNTH_NOTE} [front view, x]"}]}))

    def fake_render(_ctx, glb, *, views, mode="shaded", size=512, **kw):
        out = []
        for v in views:
            p = tmp_path / f"{mode}_{v.name}.png"
            im = Image.new("RGB", (size, size), (255, 255, 255))
            ImageDraw.Draw(im).rectangle([size * 0.25, size * 0.2, size * 0.75, size * 0.9],
                                         fill=(0, 0, 0) if mode == "silhouette" else (150, 90, 40))
            im.save(p)
            out.append(RenderView(name=v.name, path=str(p), width=size, height=size, mode=mode))
        return RenderSet(views=out, renderer="fake")

    monkeypatch.setattr("codeverse3d.spatial.tools.cached_render_glb", fake_render)
    return ToolContext(workspace=tmp_ws, language="blender", track="static_object")


def test_compare_reference_builds_a_side_by_side_sheet(ctx: ToolContext):
    obs = get_tool("compare_reference").call(ctx, {"view": "front"})
    assert obs.ok
    assert obs.numbers["view"] == "front" and 0.0 <= obs.numbers["iou"] <= 1.0
    assert len(obs.images) == 3 and all(Path(p).is_file() for p in obs.images)
    with Image.open(obs.images[0]) as sheet:  # 3 tiles side by side
        assert sheet.width > sheet.height
    assert "part inventory" in obs.text and "counts" in obs.text
    assert "SYNTHESIZED" in obs.text and "follow the BRIEF" in obs.text


def test_usage_errors_are_didactic(ctx: ToolContext):
    obs = get_tool("compare_reference").call(ctx, {"view": "nope"})
    assert not obs.ok and "unknown view" in obs.text
    obs = get_tool("compare_reference").call(ctx, {"reference_index": 9})
    assert not obs.ok and "out of range" in obs.text


def test_no_references_says_so(tmp_ws: Workspace, tmp_path: Path):
    (tmp_ws.artifacts / "object.glb").write_bytes(build_stool(tmp_path / "s.glb").read_bytes())
    tmp_ws.spec_path.write_text(json.dumps({"id": "t", "track": "static_object", "language": "blender", "prompt": "a stool"}))
    obs = get_tool("compare_reference").call(ToolContext(workspace=tmp_ws, language="blender"), {})
    assert not obs.ok and "no reference images" in obs.text
