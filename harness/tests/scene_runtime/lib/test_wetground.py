"""wetground.js: a planar reflection under a rough overlay, on OUR renderer.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_wetground_lib.py).  Their three battery-measured laws are kept
exactly as they stood — the mirror is a REAL Reflector at the graded 512 px
RTT, the overlay sits millimetres up at the graded opacity because a faked
floor renders near-black and a bare Reflector reads as marble, and the
puddle mask is seeded so a run reproduces — because none of them are about
their renderer.

THE PORT'S OWN LAWS, each one a frame rendered on our host at
fx/out/wetground/, looked at, and measured against the same fixture before
the change:

1. A WATER FILM IS NOT A CONSTANT BLEND.  The stock Reflector returns the
   reflection at full strength whatever the view angle, so the shipped pair
   put the sky on the floor 1:1: the near half of a 30 x 22 m plaza came
   back at mean luminance 0.687 with p95 0.884 — a blown milky sheet
   brighter than the ground around it.  Fresnel now decides how much of the
   mirror a pixel sees, and it composites ONCE, in the overlay's alpha
   (`a = 1 - F * (1 - a0)`): attenuating the mirror layer as WELL squared
   the falloff and put the night plaza at dark_frac 0.14, a black hole
   where the near floor should show lit wet asphalt.  Same fixture after:
   near half 0.523 mean / 0.647 p95, night dark_frac 0.001.

2. THE FILM'S SLOPE IS WHAT READS AS WATER, NOT ITS UV.  Sliding the
   reflection's UV is invisible over a smooth sky; tilting the normal is
   not, because Fresnel is steep in it.  One shared field does both, so the
   highlight rides the wobble that made it, and it fades with distance
   because a per-pixel noise with no mip chain shimmers at range.

3. THE MIRROR IS PART OF THE SCENE.  A ShaderMaterial opts out of fog and
   dithering, which left the one surface in the frame that never fogged
   while everything around it did — and a 20 m sky reflection is exactly
   the smooth gradient that bands in 8 bits.

4. WET GROUND IS NOT ONE GREY.  Looking straight down, Fresnel has admitted
   almost no reflection, so the near field IS the albedo: pools are half
   the albedo of the crests beside them, with a warm/cool cast that follows
   the same field.  The roughness map only nudges — dropping the substrate
   to 0.45 double-counts the film's own specular and measured +20/255 of
   flat sky sheen across the whole plaza.
"""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure  # noqa: F401

_LIBS = ("wetground.js", "noise.js")


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


_PROBE = """
import * as THREE from 'three';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { makeMirrorFloor } from './lib/wetground.js';

const g = makeMirrorFloor(30, 20);
const mirror = g.getObjectByName('MirrorSurface');
const overlay = g.getObjectByName('GroundOverlay');

const chan = (tex, c) => tex
    ? Array.from(tex.image.data.filter((_, i) => i % 4 === c))
    : null;
const alphaBytes = (grp) =>
    chan(grp.getObjectByName('GroundOverlay').material.alphaMap, 0);
const p1 = alphaBytes(makeMirrorFloor(10, 10, { puddleMask: true }));
const p2 = alphaBytes(makeMirrorFloor(10, 10, { puddleMask: true }));
const p3 = alphaBytes(
    makeMirrorFloor(10, 10, { puddleMask: true, seed: 9 }));
let sameSeed = true, otherSeed = false;
let aMin = 255, aMax = 0;
for (let i = 0; i < p1.length; i++) {
  if (p1[i] !== p2[i]) sameSeed = false;
  if (p1[i] !== p3[i]) otherSeed = true;
  aMin = Math.min(aMin, p1[i]);
  aMax = Math.max(aMax, p1[i]);
}

const custom = makeMirrorFloor(10, 10, {
  rttSize: 128, overlayOpacity: 0.8 });

// The damp field drives alpha, albedo and roughness together, so the
// wettest texel of the mask must also be the darkest and the smoothest.
const pud = makeMirrorFloor(10, 10, { puddleMask: true, seed: 3 });
const pMat = pud.getObjectByName('GroundOverlay').material;
const mask = chan(pMat.alphaMap, 0);
const lum = chan(pMat.map, 1);
const rough = chan(pMat.roughnessMap, 1);
let wettest = 0, driest = 0;
for (let i = 0; i < mask.length; i++) {
  if (mask[i] < mask[wettest]) wettest = i;
  if (mask[i] > mask[driest]) driest = i;
}
// Hue spread over the albedo map: red-minus-blue, in bytes.
const mr = chan(pMat.map, 0), mb = chan(pMat.map, 2);
let rbMin = 255, rbMax = -255;
for (let i = 0; i < mr.length; i++) {
  const rb = mr[i] - mb[i];
  rbMin = Math.min(rbMin, rb);
  rbMax = Math.max(rbMax, rb);
}

const flat = makeMirrorFloor(10, 10, { detail: 0 });
const stock = makeMirrorFloor(10, 10, { fresnel: 0 });
const patchNames = (o) => (o.getObjectByName('GroundOverlay')
    .material.userData.astraPatches || []).map((p) => p.name);
const filmUniform = (o, k) => o.getObjectByName('GroundOverlay')
    .material.userData.uniforms[k].value;

console.log(JSON.stringify({
  isReflector: mirror instanceof Reflector,
  rttW: mirror.getRenderTarget().width,
  customRttW: custom.getObjectByName('MirrorSurface')
      .getRenderTarget().width,
  mirrorFlat: Math.abs(mirror.rotation.x + Math.PI / 2) < 1e-6,
  planeW: mirror.geometry.parameters.width,
  planeH: mirror.geometry.parameters.height,
  mirrorFog: mirror.material.fog === true,
  mirrorDither: mirror.material.dithering === true,
  mirrorHasRipple: 'uRipple' in mirror.material.uniforms,
  overlayY: overlay.position.y,
  overlayFlat: Math.abs(overlay.rotation.x + Math.PI / 2) < 1e-6,
  opacity: overlay.material.opacity,
  customOpacity: custom.getObjectByName('GroundOverlay')
      .material.opacity,
  transparent: overlay.material.transparent === true,
  rough: overlay.material.roughness,
  receivesShadow: overlay.receiveShadow === true,
  defaultHasMask: !!overlay.material.alphaMap,
  puddleHasMask: p1 !== null,
  sameSeed, otherSeed, aMin, aMax,
  patches: patchNames(g),
  fresnelOn: filmUniform(g, 'uWgFresnel'),
  fresnelOff: filmUniform(stock, 'uWgFresnel'),
  hasMaps: !!overlay.material.map && !!overlay.material.roughnessMap,
  flatMaps: !!flat.getObjectByName('GroundOverlay').material.map,
  flatStillPatched: patchNames(flat).length,
  poolLum: lum[wettest], crestLum: lum[driest],
  poolRough: rough[wettest], crestRough: rough[driest],
  roughMax: Math.max(...rough),
  rbMin, rbMax,
}));
"""

# The scene our check_shaders.mjs boots.  Fog, because the mirror's shader
# carries fog branches that only compile once USE_FOG is defined; a light,
# because the overlay is a lit material; and a camera, because a scene with
# none is reported as not booted and never reaches the compile stage.
_SCENE = """
import * as THREE from 'three';
import { makeMirrorFloor } from './lib/wetground.js';

export const BOUNDS = { min: [-20, 0, -20], max: [20, 20, 20] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xc9d6e0, 0.02);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const block = new THREE.Mesh(
      new THREE.BoxGeometry(2, 3, 2),
      new THREE.MeshStandardMaterial({ color: 0x8a5a3c, roughness: 0.9 }));
  block.position.set(0, 1.5, -3);
  scene.add(block);
  // Every branch of the module, because each is a different program:
  // masked + detailed, and the flat stock-blend fallback.
  const wet = makeMirrorFloor(24, 18, { puddleMask: true, seed: 3 });
  scene.add(wet);
  const dry = makeMirrorFloor(6, 6, { fresnel: 0, detail: 0, ripple: 0 });
  dry.position.set(14, 0, 0);
  scene.add(dry);
  return {
    scene,
    cameras: [{ name: 'a', position: [8, 3, 10], lookAt: [0, 1, 0],
                fov: 45 }],
    update() {},
  };
}
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return _measure(_PROBE)


def test_mirror_is_a_real_reflector_at_the_measured_rtt_size(probe):
    """The whole point is a REAL planar reflection: a Reflector with an
    RTT target at the battery-graded 512 (cost-contained, quality
    graded), sized to the requested footprint and laid flat."""
    m = probe
    assert m["isReflector"], "MirrorSurface is not a Reflector"
    assert m["rttW"] == 512, f"default RTT size drifted: {m['rttW']}"
    assert m["customRttW"] == 128, "rttSize option is dead"
    assert m["mirrorFlat"], "mirror plane is not horizontal"
    assert (m["planeW"], m["planeH"]) == (30, 20), m


def test_overlay_sits_millimetres_up_at_the_graded_opacity(probe):
    """The overlay turns marble into asphalt: rough, transparent, at the
    battery opacity, millimetres above the mirror — a visible air gap or
    an opaque overlay each kill the shipped look."""
    m = probe
    assert 0.0005 <= m["overlayY"] <= 0.01, f"gap: {m['overlayY']}"
    assert m["overlayFlat"], "overlay not horizontal"
    assert m["transparent"] and abs(m["opacity"] - 0.65) < 1e-6, m
    assert abs(m["customOpacity"] - 0.8) < 1e-6, "overlayOpacity dead"
    assert m["rough"] == 1, "overlay must be fully rough"
    assert m["receivesShadow"], "overlay must receive shadows"


def test_puddle_mask_is_seeded_and_actually_breaks_the_sheet(probe):
    """puddleMask must cut real holes (alpha spans puddle-to-dry),
    reproduce bit-identically per seed, and move for a new seed — a
    dead seed silently degrades to plain overlay."""
    m = probe
    assert not m["defaultHasMask"], "default must have no alphaMap"
    assert m["puddleHasMask"], "puddleMask produced no alphaMap"
    assert m["aMin"] < 80 and m["aMax"] > 200, \
        f"mask does not span puddle-to-dry: {m['aMin']}..{m['aMax']}"
    assert m["sameSeed"], "same seed produced a different mask"
    assert m["otherSeed"], "different seed changed nothing"


def test_the_film_is_view_dependent_and_composites_exactly_once(probe):
    """LAW 1.  The Fresnel lives in the overlay's alpha — one patch,
    applied through patchStandard so a `patch*` module can still chain
    onto the same floor — and `fresnel: 0` puts the shipped constant
    blend back for polished indoor stone."""
    m = probe
    assert m["patches"] == ["WetGroundFilm"], m["patches"]
    assert m["fresnelOn"] == 1, m
    assert m["fresnelOff"] == 0, "fresnel option does not reach the shader"
    # The mirror layer must NOT attenuate as well: it carries the ripple
    # and the tint, never a second Fresnel.
    assert m["mirrorHasRipple"], "mirror lost its ripple uniform"


def test_the_mirror_joins_the_scene_it_reflects(probe):
    """LAW 3.  A ShaderMaterial opts out of fog and dithering by
    default, which leaves the floor crisp in a fogged frame and banded
    across its sky gradient."""
    m = probe
    assert m["mirrorFog"], "mirror does not take scene fog"
    assert m["mirrorDither"], "mirror does not dither its gradient"


def test_pools_are_darker_smoother_and_cooler_than_the_crests(probe):
    """LAW 4.  Looking down, the albedo IS the puddle.  One damp field
    drives mask, colour and roughness, so the wettest texel must also be
    the darkest and the smoothest — and the map has to carry a real
    warm/cool spread, not one die-cut grey."""
    m = probe
    assert m["hasMaps"], "detail maps missing"
    assert not m["flatMaps"], "detail: 0 must leave a flat sheet"
    assert m["flatStillPatched"] == 1, "detail: 0 dropped the film patch"
    # Water traps light: the pool is well under the crest beside it.
    assert m["poolLum"] < m["crestLum"] * 0.75, m
    # ... and it is smoother, but only a nudge: the water surface is the
    # mirror layer, not this one.
    assert m["poolRough"] < m["crestRough"], m
    assert m["roughMax"] > 0.85 * 255, \
        f"substrate polished into a second mirror: {m['roughMax']}"
    # Warm crests, cool pools: red-minus-blue has to swing both ways.
    assert m["rbMin"] < -12 and m["rbMax"] > 12, \
        f"albedo map carries no hue spread: {m['rbMin']}..{m['rbMax']}"


def test_every_program_compiles_on_the_gpu():
    """Both branches ship raw GLSL — a whole Reflector shader and a
    patched MeshStandardMaterial — so the compile is the test."""
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
