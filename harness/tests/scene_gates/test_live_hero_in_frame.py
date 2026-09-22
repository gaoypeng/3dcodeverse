"""Browser-backed: a Blender hero (GLB) in the scene but in no authored frame is an ERROR.

cmp6's crypt (2026-09-09): the athanor stood behind a squat pillar in every authored shot; the
judge called the HERO "a massive untextured grey box" and two refine rounds rebuilt the wrong
thing.  ``glbCoverage`` renders each loaded GLB as a white mask against black solids
(occlusion included) per camera; the gate reads ``camera_checks[].glb_frac``.
"""

from __future__ import annotations

import math

import pytest

from codeverse3d.spatial.frame_metrics import HERO_MIN_FRAC, frame_gate_from_renders
from codeverse3d.spatial.render_scene import read_metrics, render_scene
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


def test_a_hero_in_an_authored_frame_passes(starter_ws: Workspace):
    _with_hero(starter_ws, hidden=False)
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0,), width=640, height=360, fps_seconds=0.2)
    assert rs.console_errors == []
    m = read_metrics(out)
    rows = {r["url"]: r for r in m["census"]["glb_assets"]}
    assert rows[URL]["in_scene"] is True and abs(rows[URL]["size_m"] - 4.0) < 0.05, rows[URL]
    chk = {c["name"]: c for c in m["camera_checks"]}
    seen = chk["overview"]["glb_frac"][URL]
    assert seen > HERO_MIN_FRAC, chk["overview"]["glb_frac"]                            # the establishing shot sees the cube
    others = [chk[n]["glb_frac"].get(URL, 0.0) for n in ("pond_low", "windmill")]
    assert max(others) < seen, (seen, others)                                          # the other shots look away from it
    gate = frame_gate_from_renders(rs)
    assert not [f for f in gate.findings if f.data["kind"].startswith("hero_")], [f.message for f in gate.findings]


def test_a_hero_behind_a_wall_in_every_authored_frame_is_an_error(starter_ws: Workspace):
    _with_hero(starter_ws, hidden=True)
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0,), width=640, height=360, fps_seconds=0.2)
    m = read_metrics(out)
    chk = {c["name"]: c for c in m["camera_checks"]}
    assert chk["overview"]["glb_frac"].get(URL, 0.0) < 0.002, chk["overview"]["glb_frac"]   # in the scene, behind the wall
    gate = frame_gate_from_renders(rs)
    unseen = [f for f in gate.findings if f.data["kind"] == "hero_unseen"]
    assert len(unseen) == 1 and unseen[0].target == "hero_cube.glb" and not gate.passed, [f.message for f in gate.findings]
    assert "overview 0.0%" in unseen[0].message
    # the sightline measure names the wall for the establishing shot (the wall sits ~47 % of the way)
    cut = [f for f in gate.findings if f.data["kind"] == "camera_target_blocked" and f.target == "overview"]
    assert cut and "StoneWall" in cut[0].message, [f.message for f in gate.findings]
    assert math.isfinite(cut[0].data["target_hit_m"])


def test_the_overview_rig_lifts_the_room_shells_lid_but_an_authored_camera_keeps_it(starter_ws: Workspace):
    """cmp7's bakery (2026-09-09): the pairwise judge saw the harness interior as "a tiny fragment
    in the overviews" — a closed box.  The rig hides the harness-injected shell's ceiling when
    the eye is above it; the authored cameras (and the census) keep the room as built."""
    _with_hero(starter_ws, hidden=False, roofed=True)
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0,), width=640, height=360, fps_seconds=0.2)
    assert rs.console_errors == []
    m = read_metrics(out)
    chk = {c["name"]: c for c in m["camera_checks"]}
    # the lid is lifted for the rig: a 4 m cube from 100 m up is a few pixels, but it is there
    assert chk["overview_top"]["glb_frac"].get(URL, 0.0) > 0.0005, chk["overview_top"]["glb_frac"]
    # the authored overview looks at the room from outside and above: the roof stays, the cube is hidden
    assert chk["overview"]["glb_frac"].get(URL, 0.0) < 0.002, chk["overview"]["glb_frac"]
    names = [g["name"] for g in m["census"]["groups"]]
    assert "RoomShell" in names                                                     # the census counts the shell as built


def test_a_camera_named_for_the_hero_is_re_aimed_when_the_hero_is_out_of_its_frame(starter_ws: Workspace):
    """Loop 25's lighthouse (2026-09-09): `LanternDetail` shot the tower wall for three rounds
    while `hero_unseen` read 0.0 % each time — cameras belong to the plan and no session moved
    it.  The host re-aims a hero-named camera at the hero's centre when that centre is outside
    the frustum, and the repair log says so."""
    _with_hero(starter_ws, hidden=False, away_camera=True)
    out = starter_ws.renders_dir(0)
    rs = render_scene(starter_ws, out, times=(0.0,), width=640, height=360, fps_seconds=0.2)
    assert rs.console_errors == []
    m = read_metrics(out)
    chk = {c["name"]: c for c in m["camera_checks"]}
    assert chk["HeroCubeDetail"]["glb_frac"].get(URL, 0.0) > 0.01, chk["HeroCubeDetail"]["glb_frac"]
    aimed = [r for r in m["census"]["camera_repair"] if r.get("aimed_at")]
    assert aimed and aimed[0]["name"] == "HeroCubeDetail" and aimed[0]["aimed_at"] == URL, m["census"]["camera_repair"]
    # the other authored cameras are not named for it and keep their shots
    assert not [r for r in m["census"]["camera_repair"] if r.get("aimed_at") and r["name"] != "HeroCubeDetail"]
    gate = frame_gate_from_renders(rs)
    assert not [f for f in gate.findings if f.data["kind"] == "hero_unseen"], [f.message for f in gate.findings]
