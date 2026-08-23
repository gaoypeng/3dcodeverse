"""render_scene: offline parsing with a fake node driver + real browser render."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from codeverse.contracts.plan import CameraPlan
from codeverse.conventions import SCENE_VIEWS
from codeverse.spatial import render_scene as rs_mod
from codeverse.spatial.render_scene import (
    SceneRenderError,
    read_metrics,
    render_scene,
    run_scene_script,
)
from tests.scene_runtime.conftest import needs_browser, needs_node

FAKE_DRIVER = r"""
import fs from 'node:fs';
import path from 'node:path';
const a = process.argv.slice(2);
const get = (k) => { const i = a.indexOf(k); return i >= 0 ? a[i + 1] : undefined; };
const out = get('--out');
fs.mkdirSync(out, { recursive: true });
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const cams = get('--cameras');
const names = cams === 'authored' ? ['authored_a'] : JSON.parse(cams).map((c) => c.name);
if (get('--orbit-views') !== 'none') for (const v of JSON.parse(get('--orbit-views'))) names.push(v.name);
const times = get('--times').split(',').map(Number);
const views = [];
for (const t of times) for (const n of names) {
  const file = `${n}_t${t}.png`;
  fs.writeFileSync(path.join(out, file), png);
  views.push({ name: n, kind: 'authored', path: file, time_s: t, position: [1, 2, 3], lookAt: [0, 0, 0], fov: 50 });
}
const metrics = { ok: true, renderer: 'FakeGL', gpu: false, views, console_errors: ['oops'], shader_errors: [{stage: 'fragment', message: 'bad', source_line: 'x = y;'}], fps: { fps: 42.5 }, census: { totals: { meshes: 1 } }, camera_checks: [] };
fs.writeFileSync(path.join(out, 'metrics.json'), JSON.stringify(metrics));
console.log('noise line');
console.log(JSON.stringify({ ok: true, n_views: views.length, renderer: 'FakeGL' }));
"""


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    rt = tmp_path / "runtime_js"
    rt.mkdir()
    (rt / "render_scene.mjs").write_text(FAKE_DRIVER)
    (rt / "crash.mjs").write_text("console.log(JSON.stringify({ok:false,error:'driver exploded'})); process.exit(2);")
    (rt / "slow.mjs").write_text("setTimeout(() => {}, 60000);")
    monkeypatch.setattr(rs_mod, "runtime_js_dir", lambda: rt)
    # C1's run_node resolves the script path we pass, but sets NODE_PATH from settings; fine for a fake
    return rt


@needs_node
def test_render_scene_parses_driver_output(fake_runtime, ws, tmp_path):
    out = tmp_path / "out"
    cams = [CameraPlan(name="cam_a", position=(1, 2, 3), look_at=(0, 0, 0), fov=45)]
    rs = render_scene(ws, out, cameras=cams, orbit=True, times=(0.0, 1.5), orbit_views=SCENE_VIEWS[:2], sheet=True)
    assert len(rs.views) == 2 * 3
    assert {v.name for v in rs.views} == {"cam_a", SCENE_VIEWS[0].name, SCENE_VIEWS[1].name}
    assert rs.views[0].time_s == 0.0 and rs.views[-1].time_s == 1.5
    assert rs.views[0].camera_position == (1.0, 2.0, 3.0) and rs.views[0].fov == 50
    assert rs.fps == 42.5 and rs.renderer == "FakeGL"
    assert rs.console_errors[0] == "oops" and rs.console_errors[1].startswith("shader[fragment]")
    assert rs.contact_sheet and Path(rs.contact_sheet).is_file()
    assert Image.open(rs.contact_sheet).size[0] > 100
    assert read_metrics(out)["census"]["totals"]["meshes"] == 1


@needs_node
def test_driver_crash_raises(fake_runtime):
    with pytest.raises(SceneRenderError, match="driver exploded"):
        run_scene_script("crash.mjs", [], timeout_s=30)


@needs_node
def test_driver_timeout_raises(fake_runtime):
    with pytest.raises(SceneRenderError, match="timed out"):
        run_scene_script("slow.mjs", [], timeout_s=1)


def test_missing_driver_raises(fake_runtime):
    with pytest.raises(SceneRenderError, match="missing node driver"):
        run_scene_script("nope.mjs", [], timeout_s=5)


@pytest.mark.node
@needs_browser
def test_real_render_of_example_scene(starter_ws):
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0, 1.5), width=640, height=360, fps_seconds=0.5)
    assert rs.console_errors == []
    names = {v.name for v in rs.views}
    assert {"overview", "pond_low", "windmill"} <= names
    assert {v.name for v in SCENE_VIEWS} <= names
    assert len(rs.views) == 2 * (3 + len(SCENE_VIEWS))
    assert rs.fps and rs.fps > 5
    assert rs.renderer
    im = Image.open(rs.views[0].path)
    assert im.size == (640, 360)
    # not black, not blown: a lit scene
    px = list(im.convert("L").resize((32, 18)).getdata())
    assert 20 < sum(px) / len(px) < 235
    assert rs.contact_sheet and Path(rs.contact_sheet).is_file()
    m = read_metrics(out)
    assert m["census"]["totals"]["triangles"] > 1000
    checks = {c["name"]: c for c in m["camera_checks"]}
    assert not checks["overview"]["camera_in_geometry"]
    assert checks["overview"]["dark_frac"] < 0.2 and checks["overview"]["blown_frac"] < 0.2
    # animation actually changes the frame between t=0 and t=1.5
    a = Image.open(next(v.path for v in rs.views if v.name == "windmill" and v.time_s == 0.0)).convert("L")
    b = Image.open(next(v.path for v in rs.views if v.name == "windmill" and v.time_s == 1.5)).convert("L")
    diff = sum(1 for x, y in zip(a.getdata(), b.getdata(), strict=True) if abs(x - y) > 12)
    assert diff > 100


@pytest.mark.node
@needs_browser
def test_camera_in_geometry_is_detected(starter_ws):
    cams = [CameraPlan(name="buried", position=(12.0, 2.0, -2.0), look_at=(12.0, 2.0, -10.0), fov=50)]  # inside the windmill tower
    out = starter_ws.renders_dir(1)
    render_scene(starter_ws, out, cameras=cams, orbit=False, times=(0.0,), fps_seconds=0, sheet=False)
    m = read_metrics(out)
    chk = m["camera_checks"][0]
    assert chk["camera_in_geometry"] is True
