"""flock.js: a skein of birds (or a school of fish) animated entirely in
the vertex shader.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_flock_lib.py).  Their cost contract is kept verbatim in spirit —
one draw call at any count, a tick that moves exactly one float, a skein
strung down the route, a bounding box that states the volume the shader
flies through — because that contract is the whole reason the module
exists.  Their renderer-contract assertions are dropped, and the compile
check is staged as a SCENE (`_probe.compile_scene`).

THE PORT'S OWN LAWS, each one a frame rendered on our host and looked at
(fx/out/flock/{before,after,night,fish}):

1. THE BIRD IS LIT, NOT PAINTED.  The old fragment mixed the silhouette
   toward `vec3(1.0)` on the downstroke, so half the flock arrived as flat
   WHITE marks in the same instant, all the same value: measured over the
   bird pixels of the showcase frame, 836 distinct colours in 1757 core
   pixels, with the top four accounting for 13% of them in the close view.
   Colour now comes out of an irradiance — `skyColor` on the shaded side,
   `sunColor` where the wing faces the sun — and the same measurement
   gives 998 distinct in 1243, top four 3.3%.  Hue spread across the flock
   went 0.086 -> 0.164 (mid) and 0.018 -> 0.140 (close).

2. A SHADED BIRD IS NOT A HOLE.  `0x23262c` is a 1.5% albedo and the sky
   ambient was a fraction of the sky rather than its integral, which put
   bird cores at 7/255 — pure black against a 0.70 sky.  The default
   albedo is a rook's (~3.6%) and the ambient is an irradiance; core
   minimum measured 0.127 (mid) / 0.091 (close) with no pixel crushed.

3. THE TWO WINGS ROLL OPPOSITE WAYS.  A flash keyed on the beat alone
   lights every bird identically; mirrored roll is what makes a real skein
   shimmer through a turn, and it is why the sun has to reach the fragment
   in the bird's own frame (`vLit`), not as a single scalar.
"""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "materials.js", "flock.js")


def test_both_kinds_compile_on_the_headless_gpu():
    """One material carries the path, the formation, the wingbeat, the
    silhouette AND the lighting — only the GPU can say whether that GLSL is
    legal, and the fish branch is a second program nothing else builds."""
    code, out = compile_scene("""
import * as THREE from 'three';
import { makeFlock } from './lib/flock.js';

export const BOUNDS = { min: [-40, 0, -40], max: [40, 40, 40] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  scene.add(makeFlock({ count: 110, extent: 22, height: 14, speed: 7,
                        seed: 4 }));
  scene.add(makeFlock({ count: 60, kind: 'fish', extent: 8, height: 3,
                        seed: 5, sunColor: 0xffe0b0, skyColor: 0x2f5a6e }));
  const cameras = [
    { name: 'sky', position: [0, 3, 26], lookAt: [0, 12, 0], fov: 50 },
  ];
  return { scene, cameras, update() {} };
}
""", _LIBS)
    assert code == 0, out
    assert "WARN" not in out, out


def test_the_quad_keeps_position_at_the_origin():
    """A depth or AO pass redraws with an override material that ignores
    custom vertex shaders, so a billboard that keeps its quad in `position`
    burns a rectangle at the world origin."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const mesh = makeFlock({ count: 64 }).children[0];
const g = mesh.geometry;
console.log(JSON.stringify({
  posAllZero: Array.from(g.attributes.position.array).every((v) => v === 0),
  hasCorner: !!g.attributes.aCorner,
  cornerSpan: Math.max(...Array.from(g.attributes.aCorner.array)),
  instances: g.instanceCount,
  offs: g.attributes.iOff.count,
  extras: g.attributes.iExtra.count,
}));
""", _LIBS)
    assert out["posAllZero"] and out["hasCorner"]
    assert out["cornerSpan"] > 0, out
    assert out["instances"] == 64
    assert out["offs"] == 64 and out["extras"] == 64


def test_ten_thousand_birds_are_one_draw_call():
    """The whole bet: instances are free, meshes are not.  Ten thousand
    birds must cost the same submission — and the same four vertices — as a
    hundred, or the flock gets cut down until it reads as specks."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const shape = (n) => {
  const g = makeFlock({ count: n });
  const drawn = [];
  g.traverse((o) => { if (o.isMesh || o.isLine || o.isPoints) drawn.push(o); });
  const m = drawn[0];
  return {
    meshes: drawn.length,
    instanced: !!m.geometry.isInstancedBufferGeometry,
    verts: m.geometry.attributes.position.count,
    groups: m.geometry.groups.length,
    materials: [].concat(m.material).length,
    instances: m.geometry.instanceCount,
  };
};
console.log(JSON.stringify({ small: shape(100), big: shape(10000) }));
""", _LIBS)
    for k in ("small", "big"):
        assert out[k]["meshes"] == 1, out
        assert out[k]["instanced"] and out[k]["materials"] == 1, out
        # Geometry groups are per-material sub-draws: one draw call means
        # none of them.
        assert out[k]["groups"] == 0, out
    assert out["small"]["verts"] == out["big"]["verts"], out
    assert out["small"]["instances"] == 100
    assert out["big"]["instances"] == 10000


def test_tick_rebuilds_no_per_instance_matrix():
    """The motion is in the shader: a tick may move exactly one float.
    Anything else — a rewritten offset buffer, a touched matrix, an
    InstancedMesh matrix upload — is the per-frame CPU cost this library
    exists to avoid, and it would not show up in a render."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const g = makeFlock({ count: 300, seed: 6 });
const mesh = g.children[0];
const snap = () => {
  const parts = [];
  g.traverse((o) => {
    parts.push(o.matrix.elements.join(','), o.matrixWorld.elements.join(','));
    const geo = o.geometry;
    if (!geo) return;
    for (const k of Object.keys(geo.attributes)) {
      parts.push(k + ':' + Array.from(geo.attributes[k].array).join(','));
    }
  });
  return parts.join('|');
};
const before = snap();
g.userData.tick(4.25);
console.log(JSON.stringify({
  unchanged: before === snap(),
  isInstancedMesh: !!mesh.isInstancedMesh,
  hasInstanceMatrix: !!mesh.instanceMatrix,
  uploads: Object.values(mesh.geometry.attributes)
      .map((a) => a.version).reduce((s, v) => s + v, 0),
  uTime: mesh.material.uniforms.uTime.value,
}));
""", _LIBS)
    assert out["unchanged"], out
    # An InstancedMesh would carry a matrix per bird to re-upload.
    assert not out["isInstancedMesh"] and not out["hasInstanceMatrix"]
    assert out["uploads"] == 0, out
    assert out["uTime"] == 4.25


def test_two_flocks_with_one_seed_are_identical():
    """Determinism is the contract for every shipped factory: a re-run that
    reshuffles the formation is a re-run nobody can compare.  The plumage
    variance rides `iExtra.x` through a shader hash, so this covers the
    colours as well as the formation."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const attrs = (s) => {
  const g = makeFlock({ seed: s, count: 50 }).children[0].geometry;
  return JSON.stringify([
    Array.from(g.attributes.iOff.array),
    Array.from(g.attributes.iExtra.array),
  ]);
};
console.log(JSON.stringify({
  same: attrs(3) === attrs(3), differs: attrs(3) !== attrs(9),
}));
""", _LIBS)
    assert out["same"] and out["differs"]


def test_the_birds_are_strung_out_along_the_path():
    """A skein, not a slab: each bird holds its own lag down the route (so
    it FOLLOWS through the turns) plus its own offset off it.  Lags that
    reached a full loop would wrap the skein onto its own tail."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const g = makeFlock({ count: 120, extent: 30, seed: 2 }).children[0].geometry;
const off = g.attributes.iOff.array;
const lag = [], side = [], up = [];
for (let i = 0; i < g.attributes.iOff.count; i++) {
  side.push(off[i * 3]); up.push(off[i * 3 + 1]); lag.push(off[i * 3 + 2]);
}
const span = (a) => Math.max(...a) - Math.min(...a);
const phases = Array.from(g.attributes.iExtra.array)
    .filter((_, i) => i % 4 === 0);
console.log(JSON.stringify({
  lagSpan: span(lag), sideSpan: span(side), upSpan: span(up),
  phaseSpan: span(phases),
}));
""", _LIBS)
    assert 0.05 < out["lagSpan"] < 1.0, out
    assert out["sideSpan"] > 0 and out["upSpan"] > 0, out
    # Individual phase: one flock beating in unison reads as one object —
    # and the phase is also the per-bird seed the plumage hashes off.
    assert out["phaseSpan"] > 5.0, out


def test_the_flock_advertises_the_volume_it_flies_through():
    """`position` is all zeros, so the bounds three would compute are a
    point at the origin — framing, culling and the zone census would all
    place a sky-wide flock at the world origin."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const g = makeFlock({ count: 40, extent: 50, height: 30 })
    .children[0].geometry;
const b = g.boundingBox;
console.log(JSON.stringify({
  min: [b.min.x, b.min.y, b.min.z].map((v) => +v.toFixed(1)),
  max: [b.max.x, b.max.y, b.max.z].map((v) => +v.toFixed(1)),
  radius: +g.boundingSphere.radius.toFixed(1),
}));
""", _LIBS)
    assert out["max"][0] > 45 and out["min"][0] < -45, out
    # Altitude: the whole box sits well above the ground it flies over.
    assert out["min"][1] > 10, out
    assert out["radius"] > 45, out


def test_the_default_plumage_is_an_albedo_a_camera_could_see():
    """`color` is multiplied by an irradiance now, so it has to be a
    REFLECTANCE.  0x23262c is 1.5% — darker than any bird, and it rendered
    as a hole in the sky.  The band asserted here is the library's own:
    non-emissive albedos live in 0.02..0.8 linear."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const u = (o) => makeFlock(o).children[0].material.uniforms;
const bird = u({ count: 4 }).uColor.value;
const fish = u({ count: 4, kind: 'fish' }).uColor.value;
console.log(JSON.stringify({
  bird: [bird.r, bird.g, bird.b], fish: [fish.r, fish.g, fish.b],
}));
""", _LIBS)
    for name in ("bird", "fish"):
        for c in out[name]:
            assert 0.02 <= c <= 0.8, (name, out[name])


def test_the_light_colours_are_the_caller_s_and_the_sun_is_a_direction():
    """Nothing bright on a bird is a literal: the sheen, the sunlit side
    and the backlit primaries all arrive in `sunColor`, and the shaded side
    in `skyColor`.  A scene that hands over its rig gets a flock lit by the
    same two lights as everything else in the frame."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const u = makeFlock({
  count: 8, sunDir: [0, -0.6, -0.8], sunColor: 0x804000, skyColor: 0x004080,
}).children[0].material.uniforms;
const d = u.uSun.value;
console.log(JSON.stringify({
  sun: [u.uSunCol.value.r, u.uSunCol.value.g, u.uSunCol.value.b],
  sky: [u.uSky.value.r, u.uSky.value.g, u.uSky.value.b],
  len: Math.hypot(d.x, d.y, d.z), below: d.y < 0,
}));
""", _LIBS)
    # Warm light, cool ambient, arriving as the caller set them.
    assert out["sun"][0] > out["sun"][2], out
    assert out["sky"][2] > out["sky"][0], out
    # A direction, normalized, and still under the horizon: that is what
    # switches the sun term off for a night flock.
    assert abs(out["len"] - 1.0) < 1e-6, out
    assert out["below"], out


def test_the_shading_reaches_the_fragment_in_the_bird_s_own_frame():
    """The two wings roll opposite ways through the stroke, so the fragment
    needs the sun against BOTH the bird's up axis and its wing axis — a
    single scalar can only make every bird flash at once.  That is the
    varying, and the fragment must be reading it rather than a literal
    white."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const m = makeFlock({ count: 8 }).children[0].material;
const fs = m.fragmentShader, vs = m.vertexShader;
console.log(JSON.stringify({
  varying: /varying\\s+vec4\\s+vLit/.test(vs) && /varying\\s+vec4\\s+vLit/.test(fs),
  writesBoth: /vLit\\s*=\\s*vec4\\(\\s*dot\\(ud, uSun\\), dot\\(sd, uSun\\)/.test(vs),
  readsBoth: /vLit\\.x/.test(fs) && /vLit\\.y/.test(fs),
  mirrored: /q\\.x < 0\\.0 \\? -1\\.0 : 1\\.0/.test(fs),
  litBySun: /uSunCol/.test(fs) && /uSky/.test(fs),
  noWhiteMix: !/mix\\(uColor, vec3\\(1\\.0\\)/.test(fs),
}));
""", _LIBS)
    assert out["varying"] and out["writesBoth"] and out["readsBoth"], out
    assert out["mirrored"], out
    assert out["litBySun"] and out["noWhiteMix"], out


def test_the_flock_stays_out_of_the_depth_and_shadow_passes():
    """A cloud of alpha-cut billboards drawn into a depth or AO buffer is a
    stack of solid floors, and a flock does not cast a shadow on the field
    it crosses."""
    out = measure("""
import { makeFlock } from './lib/flock.js';
const g = makeFlock({ count: 20 });
const bad = [];
g.traverse((o) => {
  if (!o.isMesh) return;
  if (o.castShadow) bad.push(o.name + ':castShadow');
  if (o.material.depthWrite) bad.push(o.name + ':depthWrite');
  if (!o.material.transparent) bad.push(o.name + ':opaque');
  if (!o.userData.astraNoOverride) bad.push(o.name + ':override');
});
console.log(JSON.stringify({ bad }));
""", _LIBS)
    assert out["bad"] == [], out


def test_tick_advances_the_shader_clock():
    out = measure("""
import { makeFlock } from './lib/flock.js';
const g = makeFlock({ count: 40 });
g.userData.tick(2.5);
const times = [];
g.traverse((o) => {
  for (const m of [].concat(o.material || [])) {
    if (m && m.uniforms && m.uniforms.uTime) times.push(m.uniforms.uTime.value);
  }
});
console.log(JSON.stringify({ times }));
""", _LIBS)
    assert out["times"] and all(v == 2.5 for v in out["times"])
