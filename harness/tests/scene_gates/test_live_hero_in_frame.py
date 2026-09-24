"""Browser-backed: a Blender hero (GLB) in the scene but in no authored frame is an ERROR.

``glbCoverage`` renders each loaded GLB as a white mask against black solids (occlusion included)
per camera; the gate reads ``camera_checks[].glb_frac``.
"""

from __future__ import annotations

import math

import pytest

from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.frame_metrics import frame_gate_from_renders
from codeverse3d.spatial.render_scene import render_scene
from codeverse3d.workspace import Workspace
from tests.scene_runtime.conftest import needs_browser
from tests.scene_runtime.glb_fixture import write_box_glb

pytestmark = [pytest.mark.node, needs_browser]

URL = "/assets/hero_cube.glb"

# the hero hangs at (10, 6, 14): on the overview camera's sightline ((26, 14, 34) → (0, 1.5, 0)) and
# outside the pond and windmill shots' frusta; the wall stands halfway between the overview and it
WALL = """
  const wall = new THREE.Mesh(new THREE.BoxGeometry(30, 16, 0.5), new THREE.MeshStandardMaterial({ color: 0x888888 }));
  wall.name = 'StoneWall'; wall.position.set(18, 10, 24); wall.rotation.y = Math.atan2(16, 20); scene.add(wall);
"""


ROOM = """
  const shell = roomShell({ center: [10, 4, 14], extents: [12, 8, 12], thickness: 0.3 });
  scene.add(shell);
"""


# a camera NAMED for the hero, aimed 90 degrees away from it (loop 25's LanternDetail at the tower wall)
AWAY_CAMERA = "    { name: 'HeroCubeDetail', position: [10, 8, 30], lookAt: [40, 6, 30], fov: 45 },\n"


def _with_hero(ws: Workspace, *, hidden: bool, roofed: bool = False, away_camera: bool = False) -> None:
    write_box_glb(ws.public / "assets" / "hero_cube.glb", size=(4.0, 4.0, 4.0))
    scene = ws.src / "scene.js"
    text = scene.read_text()
    text = text.replace("export function createScene(", "export async function createScene(")
    text = text.replace("  ctx.env = env;\n", "  ctx.env = env;\n"
                        "  const hero = (await loaders.gltf.loadAsync('" + URL + "')).scene;\n"
                        "  hero.name = 'HeroCube'; hero.position.set(10, 6, 14);\n"
                        "  hero.traverse((o) => { if (o.geometry) o.geometry.computeVertexNormals(); });\n"
                        "  scene.add(hero);\n" + (WALL if hidden else "") + (ROOM if roofed else ""), 1)
    if away_camera:
        anchor = "    { name: 'windmill', position: [9, heightAt(9, 9) + 1.7, 9], lookAt: [12, 4.5, -2], fov: 50 },\n"
        assert anchor in text
        text = text.replace(anchor, anchor + AWAY_CAMERA, 1)
    if roofed:
        text = text.replace("import { buildEnv, heightAt, SUN_AZIMUTH_DEG } from './env.js';",
                            "import { buildEnv, heightAt, SUN_AZIMUTH_DEG } from './env.js';\nimport { roomShell } from './lib/environment.js';", 1)
        assert "roomShell(" in text
    assert "hero_cube.glb" in text and ("StoneWall" in text) == hidden
    scene.write_text(text)


def test_a_hero_behind_a_wall_in_every_authored_frame_is_an_error_and_the_rig_lifts_the_room_lid(starter_ws: Workspace):
    """A hero in a room shell, behind a wall from the overview: in the scene, in no authored frame → an ERROR.
    The overview rig hides the harness-injected shell's ceiling when the eye is above it; authored cameras keep it."""
    _with_hero(starter_ws, hidden=True, roofed=True)
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0,), width=640, height=360, fps_seconds=0.2)
    assert rs.console_errors == []
    m = read_json_or_none(out / "metrics.json")
    row = next(r for r in m["census"]["glb_assets"] if r["url"] == URL)
    assert row["in_scene"] is True and abs(row["size_m"] - 4.0) < 0.05, row
    assert "RoomShell" in [g["name"] for g in m["census"]["groups"]]                # the census counts the shell as built
    chk = {c["name"]: c for c in m["camera_checks"]}
    assert chk["overview"]["glb_frac"].get(URL, 0.0) < 0.002, chk["overview"]["glb_frac"]   # in the scene, hidden
    # the lid is lifted for the rig: a 4 m cube from 100 m up is a few pixels, but it is there
    assert chk["overview_top"]["glb_frac"].get(URL, 0.0) > 0.0005, chk["overview_top"]["glb_frac"]
    gate = frame_gate_from_renders(rs)
    unseen = [f for f in gate.findings if f.data["kind"] == "hero_unseen"]
    assert len(unseen) == 1 and unseen[0].target == "hero_cube.glb" and not gate.passed, [f.message for f in gate.findings]
    assert "overview 0.0%" in unseen[0].message
    # the sightline measure names the wall for the establishing shot (the wall sits ~47 % of the way)
    cut = [f for f in gate.findings if f.data["kind"] == "camera_target_blocked" and f.target == "overview"]
    assert cut and "StoneWall" in cut[0].message, [f.message for f in gate.findings]
    assert math.isfinite(cut[0].data["target_hit_m"])


def test_a_camera_named_for_the_hero_is_re_aimed_when_the_hero_is_out_of_its_frame(starter_ws: Workspace):
    """The host re-aims a hero-named camera at the hero's centre when it is outside the frustum, and logs it."""
    _with_hero(starter_ws, hidden=False, away_camera=True)
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0,), width=640, height=360, fps_seconds=0.2)
    assert rs.console_errors == []
    m = read_json_or_none(out / "metrics.json")
    chk = {c["name"]: c for c in m["camera_checks"]}
    assert chk["HeroCubeDetail"]["glb_frac"].get(URL, 0.0) > 0.01, chk["HeroCubeDetail"]["glb_frac"]
    # the census carries the host's naming rule for the gate (one rule, scene_host.mjs namesHero)
    assert chk["HeroCubeDetail"]["hero_for"] == [URL] and all(c["hero_for"] == [] for n, c in chk.items() if n != "HeroCubeDetail")
    aimed = [r for r in m["census"]["camera_repair"] if r.get("aimed_at")]
    assert aimed and aimed[0]["name"] == "HeroCubeDetail" and aimed[0]["aimed_at"] == URL, m["census"]["camera_repair"]
    # the other authored cameras are not named for it and keep their shots
    assert not [r for r in m["census"]["camera_repair"] if r.get("aimed_at") and r["name"] != "HeroCubeDetail"]
    gate = frame_gate_from_renders(rs)
    assert not [f for f in gate.findings if f.data["kind"] == "hero_unseen"], [f.message for f in gate.findings]
