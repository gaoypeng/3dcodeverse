"""render_scene: offline parsing with a fake node driver + real browser render."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.contracts.plan import CameraPlan
from codeverse3d.conventions import SCENE_VIEWS
from codeverse3d.spatial import render_scene as rs_mod
from codeverse3d.spatial.render_scene import (
    SceneRenderError,
    probe_env_args,
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
    monkeypatch.setenv("C3D_CAMERA_REPAIR", "0")
    cams = [CameraPlan(name="buried", position=(12.0, 2.0, -2.0), look_at=(12.0, 2.0, -10.0), fov=50)]  # inside the windmill tower
    out = starter_ws.renders_dir(1)
    render_scene(starter_ws, out, cameras=cams, orbit=False, times=(0.0,), fps_seconds=0, sheet=False)
    m = read_metrics(out)
    chk = m["camera_checks"][0]
    assert chk["camera_in_geometry"] is True
    # repair off → everything renders from the AUTHORED camera and no repair is recorded
    assert "camera_repair" not in (m.get("census") or {})
    assert all("repaired_position" not in v for v in m["views"])


@pytest.mark.node
@needs_browser
def test_camera_repair_is_observable_in_census_and_views(starter_ws, monkeypatch):
    """Review-3 S5 (V8): with the default-ON repair, a buried camera leaves a
    census.camera_repair row and the view records where the pixels really came
    from — repair used to fire AFTER census capture and vanish."""
    monkeypatch.delenv("C3D_CAMERA_REPAIR", raising=False)
    cams = [CameraPlan(name="buried", position=(12.0, 2.0, -2.0), look_at=(12.0, 2.0, -10.0), fov=50)]  # inside the windmill tower
    out = starter_ws.renders_dir(2)
    render_scene(starter_ws, out, cameras=cams, orbit=False, times=(0.0,), fps_seconds=0, sheet=False)
    m = read_metrics(out)
    reps = (m.get("census") or {}).get("camera_repair")
    assert reps and reps[0]["name"] == "buried" and reps[0]["moved_back_m"] + reps[0]["moved_up_m"] > 0
    view = next(v for v in m["views"] if v["name"] == "buried")
    assert view["position"] == [12.0, 2.0, -2.0]              # authored camera stays the record
    assert view.get("repaired_position") and view["repaired_position"] != view["position"]
    assert m["camera_checks"][0]["camera_in_geometry"] is False   # the effective camera is clear


@needs_node
def test_camera_repair_telemetry_survives_the_python_reader(fake_runtime, ws, tmp_path):
    """The python side keeps metrics-only telemetry intact: census.camera_repair and
    per-view repaired_position stay readable, RenderView keeps the authored camera."""
    (fake_runtime / "render_scene.mjs").write_text(FAKE_DRIVER.replace(
        "const metrics = {",
        "for (const v of views) v.repaired_position = [1, 2.6, 4.1];\nconst metrics = {",
    ).replace(
        "census: { totals: { meshes: 1 } }",
        "census: { totals: { meshes: 1 }, camera_repair: [{ name: 'authored_a', moved_back_m: 1.1, moved_up_m: 0.6, inside_before: ['BarCounter'] }] }",
    ).replace(
        "fs.writeFileSync(path.join(out, 'metrics.json'), JSON.stringify(metrics));",
        "fs.writeFileSync(path.join(out, 'metrics.json'), JSON.stringify(metrics));\nfs.writeFileSync(path.join(out, 'views.json'), JSON.stringify(views));",
    ))
    out = tmp_path / "out"
    rs = render_scene(ws, out, cameras=[CameraPlan(name="cam_a", position=(1, 2, 3), look_at=(0, 0, 0), fov=45)],
                      orbit=False, times=(0.0,), sheet=False)
    assert rs.views[0].camera_position == (1.0, 2.0, 3.0)
    m = read_metrics(out)
    assert m["census"]["camera_repair"][0]["name"] == "authored_a"
    assert m["views"][0]["repaired_position"] == [1, 2.6, 4.1]
    # the judge-flag rewrite of views.json must not strip the telemetry rider
    entries = json.loads((out / "views.json").read_text())
    assert entries[0]["repaired_position"] == [1, 2.6, 4.1] and "judge" in entries[0]


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


# The post chain cannot read this grade, so it is never built and every frame renders RAW
# (a boot-time host warning); the box refuses the coverage instrument's flat-white mask pass
# (a warning pushed while the camera checks run, i.e. AFTER boot).
_HOST_WARNING_SCENE = """
import * as THREE from 'three';
export function createScene() {
  const scene = new THREE.Scene();
  scene.add(new THREE.AmbientLight(0xffffff, 1.0));
  Object.defineProperty(scene.userData, 'grade', { get() { throw new Error('grade unreadable'); } });
  const box = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial());
  box.name = 'Box';
  box.onBeforeRender = (r, s) => { if (s.overrideMaterial && s.overrideMaterial.isMeshBasicMaterial) throw new Error('no masks'); };
  scene.add(box);
  return { scene, cameras: [{ name: 'cam', position: [0, 1, 4], lookAt: [0, 0, 0], fov: 50 }], update() {} };
}
"""


@pytest.mark.node
@needs_browser
def test_host_warnings_reach_metrics_and_the_log(ws, caplog):
    """Host warnings were copied into boot.host_warnings once, at the END of boot, and nothing
    read even that copy: a scene rendered and judged without its post chain, or with the
    coverage instrument (content_small / hero_unseen) switched off, left no trace at all."""
    ws.src.mkdir(parents=True, exist_ok=True)
    (ws.src / "scene.js").write_text(_HOST_WARNING_SCENE)
    out = ws.renders_dir(0)
    with caplog.at_level(logging.WARNING, logger=rs_mod.__name__):
        rs = render_scene(ws, out, orbit=False, times=(0.0,), width=320, height=180, fps_seconds=0, sheet=False)
    warnings = read_metrics(out)["host_warnings"]
    assert any(w.startswith("post chain unavailable") and "grade unreadable" in w for w in warnings), warnings
    assert any(w.startswith("coverage failed for cam") and "no masks" in w for w in warnings), warnings
    logged = [r.getMessage() for r in caplog.records]
    assert any("post chain unavailable" in m for m in logged) and any("coverage failed for cam" in m for m in logged)
    assert [v.name for v in rs.views] == ["cam"]   # the frame itself still renders


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
        "  cs_default: createTimeoutMs(240000),\n"
        "  cs_small_budget: createTimeoutMs(10000),\n"
        "}));\n"
    )
    assert res["phantom"] is None and res["aborted"] is None and res["responded"] is None
    assert res["real"] == "request failed: /assets/a.glb (net::ERR_CONNECTION_REFUSED)"
    assert res["offsite"] is None
    assert res["cs_default"] == 20000 and res["cs_small_budget"] == 6000


# ---------------------------------------------------------------- env flags (review-3 S4)
_FLAG_WORDS = ("--no-settle", "--camera-repair", "--auto-exposure")


def _flags(args):
    return sorted(a for a in args if a in _FLAG_WORDS)


@pytest.mark.parametrize(("env", "expect"), [
    ({}, ["--camera-repair"]),
    ({"C3D_CAMERA_REPAIR": "0"}, []),
    ({"C3D_CAMERA_REPAIR": "false"}, []),
    ({"C3D_CAMERA_REPAIR": "off"}, []),
    ({"C3D_CAMERA_REPAIR": "no"}, []),
    ({"C3D_CAMERA_REPAIR": "true"}, ["--camera-repair"]),
    ({"C3D_SETTLE": "0"}, ["--camera-repair", "--no-settle"]),
    ({"C3D_SETTLE": "false"}, ["--camera-repair", "--no-settle"]),
    ({"C3D_AUTO_EXPOSURE": "1"}, ["--auto-exposure", "--camera-repair"]),
    ({"C3D_AUTO_EXPOSURE": "true"}, ["--auto-exposure", "--camera-repair"]),
    ({"C3D_AUTO_EXPOSURE": "garbage"}, ["--camera-repair"]),
])
def test_probe_env_args_speaks_the_canonical_flag_words(monkeypatch, env, expect):
    """C3D_CAMERA_REPAIR=false must DISABLE, C3D_SETTLE=false must disable,
    C3D_AUTO_EXPOSURE=true must enable — the raw '0'/'1' compares silently
    ignored every other word the doc'd env_flag vocabulary accepts."""
    for k in ("C3D_SETTLE", "C3D_CAMERA_REPAIR", "C3D_AUTO_EXPOSURE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert _flags(probe_env_args()) == expect


def test_every_driver_invocation_carries_the_env_flags(monkeypatch, ws, tmp_path):
    """Review-3 S4 (V7c): the combined single-boot build probes under the SAME
    settle / camera-repair / auto-exposure flags as the standalone probe and
    render paths — it used to pass none of them."""
    import codeverse3d.languages.scene_threejs as st
    import codeverse3d.spatial.probes as probes_mod

    captured: dict[str, list[str]] = {}

    def fake_run(script, args, *, timeout_s=0.0, cwd=None):
        captured[script] = list(map(str, args))
        raise SceneRenderError("captured")

    monkeypatch.setattr(probes_mod, "run_scene_script", fake_run)
    monkeypatch.setattr(rs_mod, "run_scene_script", fake_run)
    for k in ("C3D_SETTLE", "C3D_CAMERA_REPAIR", "C3D_AUTO_EXPOSURE", "C3D_POST"):
        monkeypatch.delenv(k, raising=False)

    # defaults: camera repair ON everywhere, settle on (no flag), exposure off
    probes_mod.probe_scene(ws)
    assert _flags(captured["probe_scene.mjs"]) == ["--camera-repair"]
    st.probe_and_preflight(ws, timeout_s=5.0)
    assert _flags(captured["probe_scene.mjs"]) == ["--camera-repair"], "combined build lost the default-ON policy"
    with pytest.raises(SceneRenderError):
        render_scene(ws, tmp_path / "out_flags", cameras=[CameraPlan(name="c", position=(1, 2, 3), look_at=(0, 0, 0), fov=45)],
                     orbit=False, times=(0.0,), sheet=False)
    assert _flags(captured["render_scene.mjs"]) == ["--camera-repair"]

    # the A/B words reach every path, including the combined build
    monkeypatch.setenv("C3D_SETTLE", "0")
    monkeypatch.setenv("C3D_CAMERA_REPAIR", "false")
    st.probe_and_preflight(ws, timeout_s=5.0)
    assert _flags(captured["probe_scene.mjs"]) == ["--no-settle"]
    probes_mod.probe_scene(ws)
    assert _flags(captured["probe_scene.mjs"]) == ["--no-settle"]
