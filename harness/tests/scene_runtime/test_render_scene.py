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
def test_driver_failures_raise(fake_runtime):
    with pytest.raises(SceneRenderError, match="driver exploded"):
        run_scene_script("crash.mjs", [], timeout_s=30)
    with pytest.raises(SceneRenderError, match="timed out"):
        run_scene_script("slow.mjs", [], timeout_s=1)


def test_missing_driver_raises(fake_runtime):
    with pytest.raises(SceneRenderError, match="missing node driver"):
        run_scene_script("nope.mjs", [], timeout_s=5)


@pytest.mark.node
@needs_browser
def test_camera_in_geometry_is_detected(starter_ws, monkeypatch):
    # this test asserts DETECTION, so the default-on camera repair must stand down
    # (the repair itself is covered by test_camera_repair.py)
    monkeypatch.setenv("CV3D_CAMERA_REPAIR", "0")
    cams = [CameraPlan(name="buried", position=(12.0, 2.0, -2.0), look_at=(12.0, 2.0, -10.0), fov=50)]  # inside the windmill tower
    out = starter_ws.renders_dir(1)
    render_scene(starter_ws, out, cameras=cams, orbit=False, times=(0.0,), fps_seconds=0, sheet=False)
    m = read_metrics(out)
    chk = m["camera_checks"][0]
    assert chk["camera_in_geometry"] is True


@needs_node
def test_driver_crash_after_metrics_degrades_to_renderset(fake_runtime, ws, tmp_path):
    """A crash after metrics yields a degraded RenderSet rather than losing frames."""
    (fake_runtime / "render_scene.mjs").write_text(
        FAKE_DRIVER.replace(
            "console.log(JSON.stringify({ ok: true, n_views: views.length, renderer: 'FakeGL' }));",
            "console.error('error: render failed: Cannot set properties of undefined');\nprocess.exit(2);",
        )
    )
    out = tmp_path / "out"
    rs = render_scene(ws, out, cameras=[CameraPlan(name="cam_a", position=(1, 2, 3), look_at=(0, 0, 0), fov=45)],
                      orbit=False, times=(0.0,), sheet=False)
    assert len(rs.views) == 1
    assert any("render_scene.mjs failed (exit 2)" in e for e in rs.console_errors)


@needs_node
def test_render_scene_clears_stale_metrics(fake_runtime, ws, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "metrics.json").write_text('{"views": [{"name": "stale", "path": "x.png", "position": [0,0,0], "lookAt": [0,0,0]}]}')
    rs = render_scene(ws, out, cameras=[CameraPlan(name="cam_a", position=(1, 2, 3), look_at=(0, 0, 0), fov=45)],
                      orbit=False, times=(0.0,), sheet=False)
    assert all(v.name != "stale" for v in rs.views)


@pytest.mark.node
@needs_browser
def test_update_throw_mid_render_yields_frames_and_console_error(starter_ws):
    """A late update error is recorded without aborting the remaining frames."""
    scene = starter_ws.src / "scene.js"
    src = scene.read_text()
    assert "function update(t, dt) {" in src
    scene.write_text(src.replace(
        "function update(t, dt) {",
        "function update(t, dt) {\n    if (t > 1.0) { const lanterns = []; lanterns[0].intensity = Math.sin(t); }",
        1,
    ))
    out = starter_ws.renders_dir(3)
    rs = render_scene(starter_ws, out, times=(0.0, 1.5), width=320, height=180, fps_seconds=0.3,
                      orbit_views=SCENE_VIEWS[:1])
    # every requested view rendered (3 authored + 1 orbit, at 2 times)
    assert len(rs.views) == 2 * 4, [v.name for v in rs.views]
    assert all(Path(v.path).is_file() for v in rs.views)
    assert any(e.startswith("update(t=") and "intensity" in e for e in rs.console_errors), rs.console_errors
    m = read_metrics(out)
    assert m["update_errors"] and "update() disabled" in m["update_errors"][0]


@needs_browser
def test_request_failure_line_filters_phantom_aborts():
    """Consumed or offsite request failures do not create phantom gate errors."""
    from tests.scene_runtime.conftest import run_node_json

    res = run_node_json(
        "import { requestFailureLine, createTimeoutMs } from './lib/host_page.mjs';\n"
        "console.log(JSON.stringify({\n"
        "  phantom: requestFailureLine('http://x/assets/a.glb', 'http://x', true, 'net::ERR_ABORTED'),\n"
        "  aborted: requestFailureLine('http://x/assets/a.glb', 'http://x', false, 'net::ERR_ABORTED'),\n"
        "  responded: requestFailureLine('http://x/assets/a.glb', 'http://x', true, 'net::ERR_FAILED'),\n"
        "  real: requestFailureLine('http://x/assets/a.glb', 'http://x', false, 'net::ERR_CONNECTION_REFUSED'),\n"
        "  offsite: requestFailureLine('http://cdn/other.js', 'http://x', false, 'net::ERR_FAILED'),\n"
        "  cs_default: createTimeoutMs('', 240000),\n"
        "  cs_flag: createTimeoutMs('3000', 240000),\n"
        "  cs_small_budget: createTimeoutMs('', 10000),\n"
        "}));\n"
    )
    assert res["phantom"] is None and res["aborted"] is None and res["responded"] is None
    assert res["real"] == "request failed: /assets/a.glb (net::ERR_CONNECTION_REFUSED)"
    assert res["offsite"] is None
    assert res["cs_default"] == 20000 and res["cs_flag"] == 3000 and res["cs_small_budget"] == 6000
