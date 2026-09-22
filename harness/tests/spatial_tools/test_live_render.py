"""End-to-end through the real renderer (node + headless Chrome).  Marked ``node``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.spatial.registry import ToolContext, get_tool

pytestmark = pytest.mark.node


def test_render_views_and_silhouette_live(stool_ctx: ToolContext) -> None:
    pytest.importorskip("codeverse3d.spatial.render")
    obs = get_tool("render_views").call(stool_ctx, {"views": ["front", "top"], "size": 256})
    assert obs.ok, obs.text
    assert Path(obs.images[0]).is_file() and obs.numbers["views"] == ["front", "top"]
    sheet = Image.open(obs.images[0])
    assert sheet.size[0] > sheet.size[1]
    # the silhouette of the front view is its own perfect reference (rendered at the
    # tool's own 512 px — the D47 eye-level front (el 0) shows the legs edge-on, and a
    # 256→512 resample alone costs ~0.05 IoU on those thin features)
    sil = get_tool("render_views").call(stool_ctx, {"views": ["front"], "mode": "silhouette", "size": 512})
    assert sil.ok, sil.text
    ref = stool_ctx.workspace.root / "ref.png"
    Image.open(sil.images[-1]).convert("RGB").save(ref)
    spec = json.loads(stool_ctx.workspace.spec_path.read_text())
    spec["references"] = [{"path": "ref.png"}]
    stool_ctx.workspace.spec_path.write_text(json.dumps(spec))
    cmp_ = get_tool("compare_reference").call(stool_ctx, {"view": "front"})
    assert cmp_.ok, cmp_.text
    assert cmp_.numbers["iou"] > 0.97 and cmp_.numbers["reliable"]
