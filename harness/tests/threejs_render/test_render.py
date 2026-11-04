"""render_glb (node + headless Chrome) and offline guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.conventions import OBJECT_VIEWS_QUICK, ViewPreset
from codeverse3d.spatial.render import render_glb


@pytest.mark.node
def test_render_views_modes_isolate_and_cache(stool_glb: Path, tmp_path: Path):
    out = tmp_path / "shaded"
    rs = render_glb(stool_glb, out, views=OBJECT_VIEWS_QUICK, width=256, height=256, use_cache=True)
    assert [v.name for v in rs.views] == [v.name for v in OBJECT_VIEWS_QUICK]
    assert rs.renderer and rs.contact_sheet and Path(rs.contact_sheet).is_file()
    for v in rs.views:
        assert Path(v.path).is_file() and v.mode == "shaded" and v.camera_position is not None and v.fov == 35
    assert json.loads((out / "views.json").read_text())["ok"]
    front = next(v for v in rs.views if v.name == "front")       # camera on +Z
    assert front.camera_position[2] > 0.5 and abs(front.camera_position[0]) < 1e-6
    # cached re-render lands in a new directory without calling node
    out2 = tmp_path / "shaded2"
    rs2 = render_glb(stool_glb, out2, views=OBJECT_VIEWS_QUICK, width=256, height=256)
    assert json.loads((out2 / "views.json").read_text()).get("from_cache") is True
    assert rs2.views[0].camera_position == rs.views[0].camera_position

    sil = render_glb(stool_glb, tmp_path / "sil", views=[ViewPreset("front", 0, 8)], mode="silhouette", width=128, height=128, sheet=False)
    with Image.open(sil.views[0].path) as im:
        px = list(im.convert("L").getdata())
        # silhouette mode must be separable (compare_silhouette thresholds it)
        dark = sum(1 for v in px if v < 32)
        light = sum(1 for v in px if v > 223)
        mid = len(px) - dark - light
        assert dark > 300 and light > 3000
        assert mid < 0.08 * len(px), f"silhouette is not separable: {mid} mid-tone px of {len(px)}"
    assert sil.contact_sheet is None
