"""scene_blender rendering (lane B, D1/D3/D8): Blender makes the pixels, the JS host measures them.

Real Blender + headless Chrome on a small fixture scene (``blender_scene_fixture``), rendered at
192x108 with 4 Cycles samples on the CPU (the GPU is shared on this box) — plus pure-python tests
of the GPU-slot policy with a fake driver.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from PIL import Image

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import Severity
from codeverse3d.conventions import from_authoring_frame, to_authoring_frame
from codeverse3d.proc import ProcResult, read_json_or_none
from codeverse3d.spatial import render_blender as rb
from codeverse3d.spatial.frame_metrics import frame_findings
from codeverse3d.spatial.render_scene import SceneRenderError, render_scene
from codeverse3d.workspace import Workspace
from tests.scene_runtime.blender_scene_fixture import blender_available, build_fixture
from tests.scene_runtime.conftest import needs_browser

needs_blender = pytest.mark.skipif(not blender_available(), reason="Blender binary required")
W, H = 192, 108
STAT_KEYS = {"mean_lum", "lum_std", "dark_frac", "blown_frac", "modal_frac"}


def _cpu_env(mp: pytest.MonkeyPatch, cache: Path) -> None:
    mp.setenv("C3D_RENDER__BLENDER_DEVICE", "cpu")
    mp.setenv("C3D_CACHE_DIR", str(cache))
    get_settings.cache_clear()


@pytest.fixture(scope="module")
def moving(tmp_path_factory):
    """The moving fixture, rendered once: 2 authored cameras x (0, 1.5) + the orbit rig."""
    root = tmp_path_factory.mktemp("sb_moving")
    with pytest.MonkeyPatch.context() as mp:
        _cpu_env(mp, root / "cache")
        ws = build_fixture(Workspace(root / "run").create(), moving=True)
        rs = rb.render_blender_scene(ws, root / "renders", width=W, height=H, samples=4)
        get_settings.cache_clear()
    return ws, rs, json.loads((root / "renders" / "metrics.json").read_text())


@pytest.mark.parametrize("v", [(1.0, 2.0, 3.0), (-4.5, 0.25, 7.0)])
def test_the_frame_round_trip_is_the_identity(v):
    z_up = to_authoring_frame(v, "blender")
    assert z_up == (v[0], -v[2], v[1])
    assert from_authoring_frame(z_up, "blender") == pytest.approx(v)
    assert from_authoring_frame(v, "scene_threejs") == v


@pytest.mark.blender
@pytest.mark.node
@needs_blender
@needs_browser
def test_a_fixture_scene_renders_two_cameras_at_two_times(moving):
    ws, rs, m = moving
    out = Path(rs.out_dir)
    authored = {(v.name, v.time_s) for v in rs.views if v.name in ("Front", "Side")}
    assert authored == {("Front", 0.0), ("Front", 1.5), ("Side", 0.0), ("Side", 1.5)}
    for v in rs.views:
        with Image.open(v.path) as im:
            assert im.size == (W, H)
    # the three overviews get pixels at t0 only; the eye-level rig is measured, never rendered
    rig = sorted((v.name, v.time_s) for v in rs.views if v.name.startswith(("overview", "eye")))
    assert rig == [("overview_back_left", 0.0), ("overview_front_right", 0.0), ("overview_top", 0.0)]
    checks = {c["name"]: c for c in m["camera_checks"]}
    assert set(checks) >= {"Front", "Side", "overview_top", "eye_front", "eye_right", "eye_back_left"}
    assert set(checks["Front"]) >= STAT_KEYS and not STAT_KEYS & set(checks["eye_front"])
    assert checks["Front"]["kind"] == "authored" and checks["eye_front"]["kind"] == "orbit"
    assert 0.05 < checks["Front"]["mean_lum"] < 0.95 and checks["Front"]["content_frac"] > 0.01
    # the authored camera came out of the .blend (Z-up) into the GLB frame: (0, -14, 4) -> (0, 4, 14)
    front = next(v for v in m["views"] if v["name"] == "Front")
    assert front["position"] == pytest.approx([0, 4, 14]) and front["lookAt"] == pytest.approx([0, 1, 0])
    assert front["fov"] == pytest.approx(40.107, abs=0.01)
    assert m["language"] == "scene_blender" and m["blender"]["device"] == "CPU" and m["blender"]["samples"] == 4
    assert m["blender"]["engine"] == "cycles" and len(m["blender"]["frame_ms"]) == 7
    assert "Blender" in rs.renderer and "cycles" in rs.renderer
    assert rs.console_errors == [] and rs.contact_sheet and Path(rs.contact_sheet).is_file()
    judged = {(v.name, v.time_s) for v in rs.views if v.judge}
    assert ("Front", 0.0) in judged and ("overview_top", 0.0) in judged
    assert [e["judge"] for e in json.loads((out / "views.json").read_text())].count(True) == len(judged)


@pytest.mark.blender
@pytest.mark.node
@needs_blender
@needs_browser
def test_metrics_schema_equals_the_threejs_one(moving, tmp_path):
    ws, _, bm = moving
    render_scene(ws, tmp_path / "three", width=W, height=H, fps_seconds=0.2)
    tm = json.loads((tmp_path / "three" / "metrics.json").read_text())
    assert set(bm) - set(tm) == {"language", "blender"}, "Blender metrics add only language + blender"
    assert set(tm) - set(bm) == set(), f"keys the three.js payload has and Blender's lacks: {set(tm) - set(bm)}"
    assert {k for v in bm["views"] for k in v} == {k for v in tm["views"] for k in v} - {"repaired_position"}
    b_checks = {c["name"]: set(c) for c in bm["camera_checks"]}
    t_checks = {c["name"]: set(c) for c in tm["camera_checks"]}
    for name in ("Front", "overview_top"):
        assert b_checks[name] == t_checks[name], name
    assert b_checks["eye_front"] == t_checks["eye_front"] - STAT_KEYS
    assert {k for r in bm["motion"] for k in r} == {k for r in tm["motion"] for k in r}


@pytest.mark.blender
@pytest.mark.node
@needs_blender
@needs_browser
def test_motion_is_measured_on_a_keyframed_object_and_not_on_a_static_scene(moving, tmp_path, monkeypatch):
    _, _, m = moving
    rows = {r["name"]: r for r in m["motion"]}
    assert rows["Front"]["moving"] and rows["Side"]["moving"]
    assert [f.data["kind"] for f in frame_findings(m).findings if f.data.get("kind") in ("motion", "no_motion")] == ["motion"]
    _cpu_env(monkeypatch, tmp_path / "cache")
    ws = build_fixture(Workspace(tmp_path / "still").create(), moving=False, probe=False)
    rb.render_blender_scene(ws, tmp_path / "r", orbit=False, width=W, height=H, samples=4)
    still = json.loads((tmp_path / "r" / "metrics.json").read_text())
    assert [r["moving"] for r in still["motion"]] == [False, False]
    assert max(r["changed_frac"] for r in still["motion"]) == 0.0   # a fixed seed: Cycles noise is not motion
    frozen = [f for f in frame_findings(still).findings if f.data.get("kind") == "no_motion"]
    assert frozen and frozen[0].severity == Severity.ERROR
    assert "keyframes" in frozen[0].fix_hint and "update(t, dt)" not in frozen[0].fix_hint   # the bpy words


def _half_png(path: Path) -> None:
    im = Image.new("RGB", (W, H), (0, 0, 0))
    im.paste((255, 255, 255), (W // 2, 0, W, H))
    im.save(path)


@pytest.mark.node
@needs_browser
def test_exposure_statistics_of_a_known_png_come_from_the_external_frame(tmp_path):
    """Half black, half white: mean 0.5, half near-black, half blown, the modal bucket 50 % — from
    the Blender PNG, not from the host's own (bright grey) render of the same camera."""
    ws = Workspace(tmp_path / "run").create()
    ws.src.mkdir(parents=True, exist_ok=True)
    (ws.src / "scene.js").write_text(
        "import * as THREE from 'three';\n"
        "export function createScene() {\n"
        "  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x888888);\n"
        "  const g = new THREE.Mesh(new THREE.PlaneGeometry(40, 40).rotateX(-Math.PI / 2), new THREE.MeshBasicMaterial({ color: 0x888888 }));\n"
        "  g.name = 'Ground'; scene.add(g);\n"
        "  return { scene, cameras: [{ name: 'K', position: [0, 3, 10], lookAt: [0, 0, 0], fov: 50 }] };\n}\n")
    out = tmp_path / "out"
    out.mkdir()
    _half_png(out / "K_t0.png")
    views = [{"name": "K", "kind": "authored", "path": "K_t0.png", "time_s": 0.0, "position": [0, 3, 10],
              "lookAt": [0, 0, 0], "fov": 50, "render_ms": 1}]
    (out / rb.EXTERNAL_NAME).write_text(json.dumps(views))
    specs = [{"name": "K", "kind": "authored", "position": [0, 3, 10], "lookAt": [0, 0, 0], "fov": 50},
             {"name": "Blank", "kind": "orbit", "position": [5, 3, 10], "lookAt": [0, 0, 0], "fov": 50}]
    metrics, err = rb._geometry_pass(ws, out, specs, times=(0.0,), width=W, height=H, timeout_s=90, scene_rel="src/scene.js")
    assert err == ""
    k = next(c for c in metrics["camera_checks"] if c["name"] == "K")
    assert k["mean_lum"] == pytest.approx(0.5, abs=0.02)
    assert k["dark_frac"] == pytest.approx(0.5, abs=0.03) and k["blown_frac"] == pytest.approx(0.5, abs=0.03)
    assert k["modal_frac"] == pytest.approx(0.5, abs=0.03) and k["lum_std"] == pytest.approx(0.5, abs=0.03)
    assert k["ground_frac"] > 0.2   # coverage still measured on the loaded scene
    blank = next(c for c in metrics["camera_checks"] if c["name"] == "Blank")
    assert not STAT_KEYS & set(blank), "no external frame → no luminance at all"
    assert [v["name"] for v in metrics["views"]] == ["K"]


@pytest.mark.blender
@needs_blender
def test_a_held_gpu_slot_sends_a_real_render_to_the_cpu(tmp_path, monkeypatch):
    monkeypatch.setenv("C3D_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("C3D_RENDER__BLENDER_GPU_SLOTS", "1")
    monkeypatch.setenv("C3D_RENDER__BLENDER_GPU_WAIT_S", "0.2")
    get_settings.cache_clear()
    ws = build_fixture(Workspace(tmp_path / "run").create(), probe=False)
    out = tmp_path / "out"
    out.mkdir()
    job = {"out": str(out), "result": str(out / rb.RESULT_NAME), "engine": "cycles", "samples": 2, "width": 64,
           "height": 36, "times": [0.0], "authored": True, "cameras": []}
    held = rb.gpu_slots().take(1.0)
    try:
        res = rb.render_frames(ws.artifacts / "scene.blend", out, job, timeout_s=120)
    finally:
        rb.Slots.give(held)
    assert res["device"] == "CPU" and "busy" in res["fallback"] and len(res["frames"]) == 2
    free = rb.render_frames(ws.artifacts / "scene.blend", out, job, timeout_s=120)
    assert free["device"] == "GPU" or free["fallback"] == "no CUDA device"


def test_a_missing_blend_is_an_empty_renderset_that_says_why(tmp_path):
    ws = Workspace(tmp_path / "run").create()
    rs = rb.render_blender_scene(ws, tmp_path / "out")
    assert rs.views == [] and "scene.blend" in rs.console_errors[0]
    assert read_json_or_none(tmp_path / "out" / "metrics.json")["language"] == "scene_blender"


# --------------------------------------------------------------------------- the slot policy (no Blender)
class FakeDriver:
    """Stands in for ``_run_driver``: records the device of every run, can fail / time out on the GPU,
    and checks the slot is HELD while a GPU run is out."""

    def __init__(self, gpu: str = "ok"):
        self.gpu = gpu
        self.devices: list[str] = []
        self.slot_free_during_gpu: bool | None = None

    def __call__(self, blend, job, out_dir, *, device, timeout_s, settings):
        self.devices.append(device)
        if device == "GPU":
            probe = rb.gpu_slots(settings).try_take()
            self.slot_free_during_gpu = probe is not None
            if probe is not None:
                rb.Slots.give(probe)
            if self.gpu == "crash":
                return ProcResult(1, "", "CUDA error: out of memory", False, 5), None
            if self.gpu == "timeout":
                return ProcResult(-9, "", "", True, 5), None
        return ProcResult(0, "", "", False, 5), {"device": device, "frames": [], "device_fallback": ""}


@pytest.fixture
def one_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("C3D_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("C3D_RENDER__BLENDER_GPU_SLOTS", "1")
    monkeypatch.setenv("C3D_RENDER__BLENDER_GPU_WAIT_S", "0.3")
    get_settings.cache_clear()
    return tmp_path


def test_a_free_slot_renders_on_the_gpu_and_holds_the_slot_meanwhile(one_slot, monkeypatch):
    fake = FakeDriver()
    monkeypatch.setattr(rb, "_run_driver", fake)
    res = rb.render_frames(one_slot / "x.blend", one_slot, {}, timeout_s=10)
    assert fake.devices == ["GPU"] and res["device"] == "GPU" and res["fallback"] == ""
    assert fake.slot_free_during_gpu is False, "the one slot must be held while the GPU render runs"
    assert rb.gpu_slots().busy() == 0, "and handed back after it"


def test_all_slots_busy_falls_back_to_the_cpu_after_the_wait(one_slot, monkeypatch):
    fake = FakeDriver()
    monkeypatch.setattr(rb, "_run_driver", fake)
    held = rb.gpu_slots().take(1.0)
    try:
        t0 = time.monotonic()
        res = rb.render_frames(one_slot / "x.blend", one_slot, {}, timeout_s=10)
        waited = time.monotonic() - t0
    finally:
        rb.Slots.give(held)
    assert fake.devices == ["CPU"] and res["device"] == "CPU"
    assert "busy" in res["fallback"] and 0.25 <= waited < 3.0 and res["slot_wait_ms"] >= 250


def test_a_gpu_render_that_dies_is_rendered_again_on_the_cpu(one_slot, monkeypatch):
    fake = FakeDriver(gpu="crash")
    monkeypatch.setattr(rb, "_run_driver", fake)
    res = rb.render_frames(one_slot / "x.blend", one_slot, {}, timeout_s=10)
    assert fake.devices == ["GPU", "CPU"] and res["device"] == "CPU"
    assert "GPU render failed" in res["fallback"] and "out of memory" in res["fallback"]
    assert rb.gpu_slots().busy() == 0


def test_a_timeout_is_the_scene_not_the_device_no_cpu_retry(one_slot, monkeypatch):
    fake = FakeDriver(gpu="timeout")
    monkeypatch.setattr(rb, "_run_driver", fake)
    with pytest.raises(SceneRenderError, match="timed out"):
        rb.render_frames(one_slot / "x.blend", one_slot, {}, timeout_s=10)
    assert fake.devices == ["GPU"] and rb.gpu_slots().busy() == 0


def test_device_cpu_never_touches_a_slot(one_slot, monkeypatch, switch):
    switch("C3D_RENDER__BLENDER_DEVICE", "cpu")
    fake = FakeDriver()
    monkeypatch.setattr(rb, "_run_driver", fake)
    held = rb.gpu_slots().take(1.0)
    try:
        t0 = time.monotonic()
        res = rb.render_frames(one_slot / "x.blend", one_slot, {}, timeout_s=10)
    finally:
        rb.Slots.give(held)
    assert fake.devices == ["CPU"] and res["fallback"] == "" and time.monotonic() - t0 < 0.2
