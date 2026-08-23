"""render_glb / render_turntable (node + headless Chrome) and offline guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from codeverse.conventions import OBJECT_VIEWS_QUICK, ViewPreset
from codeverse.spatial import render as render_mod
from codeverse.spatial.render import RenderError, render_glb, render_turntable


def test_render_glb_offline_validation(tmp_path: Path):
    with pytest.raises(RenderError):
        render_glb(tmp_path / "missing.glb", tmp_path / "out")
    fake = tmp_path / "x.glb"
    fake.write_bytes(b"glTF" + b"\0" * 16)
    with pytest.raises(RenderError, match="mode"):
        render_glb(fake, tmp_path / "out", mode="xray")
    with pytest.raises(RenderError, match="background"):
        render_glb(fake, tmp_path / "out", background="blue")
    with pytest.raises(RenderError, match="no views"):
        render_glb(fake, tmp_path / "out", views=[])


def test_cache_key_depends_on_params(tmp_path: Path):
    glb = tmp_path / "a.glb"
    glb.write_bytes(b"abc")
    k1 = render_mod._cache_key(glb, {"mode": "shaded", "w": 1})
    k2 = render_mod._cache_key(glb, {"mode": "clay", "w": 1})
    glb.write_bytes(b"abd")
    k3 = render_mod._cache_key(glb, {"mode": "shaded", "w": 1})
    assert len({k1, k2, k3}) == 3


def test_render_scene_delegates_or_raises(tmp_path: Path):
    from codeverse.workspace import Workspace
    try:
        import codeverse.spatial.render_scene  # noqa: F401
    except ImportError:
        with pytest.raises(NotImplementedError):
            render_mod.render_scene(Workspace(tmp_path), tmp_path / "o")


@pytest.mark.node
def test_render_views_modes_isolate_and_cache(stool_glb: Path, tmp_path: Path):
    out = tmp_path / "shaded"
    rs = render_glb(stool_glb, out, views=OBJECT_VIEWS_QUICK, width=256, height=256, use_cache=True)
    assert [v.name for v in rs.views] == [v.name for v in OBJECT_VIEWS_QUICK]
    assert rs.renderer and rs.contact_sheet and Path(rs.contact_sheet).is_file()
    for v in rs.views:
        assert Path(v.path).is_file() and v.mode == "shaded" and v.camera_position is not None and v.fov == 35
        with Image.open(v.path) as im:
            assert im.size == (256, 256)
    meta = json.loads((out / "views.json").read_text())
    assert meta["ok"] and len(meta["views"]) == 4
    # the front view: camera on +Z, looking at the bbox centre
    front = next(v for v in rs.views if v.name == "front")
    assert front.camera_position[2] > 0.5 and abs(front.camera_position[0]) < 1e-6
    # cached re-render lands in a new directory without calling node
    out2 = tmp_path / "shaded2"
    rs2 = render_glb(stool_glb, out2, views=OBJECT_VIEWS_QUICK, width=256, height=256)
    assert json.loads((out2 / "views.json").read_text()).get("from_cache") is True
    assert rs2.views[0].camera_position == rs.views[0].camera_position

    sil = render_glb(stool_glb, tmp_path / "sil", views=[ViewPreset("front", 0, 8)], mode="silhouette", width=128, height=128, sheet=False)
    with Image.open(sil.views[0].path) as im:
        px = list(im.convert("RGB").get_flattened_data()) if hasattr(im, "get_flattened_data") else list(im.convert("RGB").getdata())
        blacks = sum(1 for p in px if p == (0, 0, 0))
        whites = sum(1 for p in px if p == (255, 255, 255))
        assert blacks > 300 and whites > 3000 and blacks + whites > 0.97 * 128 * 128
    assert sil.contact_sheet is None

    iso = render_glb(stool_glb, tmp_path / "iso", views=[ViewPreset("front", 0, 8)], isolate=["Legs"], width=128, height=128, sheet=False)
    assert Path(iso.views[0].path).is_file()
    with pytest.raises(RenderError, match="isolate hid everything|nothing visible"):
        render_glb(stool_glb, tmp_path / "iso_bad", views=[ViewPreset("front", 0, 8)], isolate=["NotAPart"], width=64, height=64, sheet=False)

    ex = render_glb(stool_glb, tmp_path / "explode", views=[ViewPreset("front", 0, 8)], explode=0.5, mode="clay", width=128, height=128, sheet=False)
    assert Path(ex.views[0].path).is_file()


@pytest.mark.node
def test_render_turntable_gif(stool_glb: Path, tmp_path: Path):
    out = render_turntable(stool_glb, tmp_path / "tt.gif", n=6, width=96, height=96)
    assert out.suffix == ".gif" and out.is_file()
    with Image.open(out) as im:
        assert getattr(im, "n_frames", 1) == 6
