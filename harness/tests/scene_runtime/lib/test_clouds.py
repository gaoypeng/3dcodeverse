"""clouds.js — one instanced draw, deterministic, sun-lit, driftable.

Built 2026-08-04 in the reference: r184's Sky cloud branch grid-artifacts
at readable contrast, so hero clouds are this billboard layer.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_clouds_lib.py).  Their renderer-contract assertions are kept
(shader.js still injects the depth chunks, and OUR renderer has no post
chain, so the tone-map + colour-space tail matters more here, not less).
The port adds the three things this renderer needed:

* the layer opts OUT of three's fog blend and keeps three's fog
  UNIFORMS.  A deck sits 300-1500 m out, and a scene's FogExp2 is
  authored for scene-scale geometry: at the density a 120 m courtyard
  uses (0.0035) every puff resolved to exactly the fog colour — measured
  on the showcase render, the whole layer was invisible (cloud region
  (224,233,236) against a (232,239,242) sky).  It now mixes its own
  bounded aerial perspective toward `fogColor`;
* `sunDir` was documented and ignored — every puff was lit from straight
  up.  It drives the lit side now, and a BELOW-HORIZON sun (what
  `sunRig().sunDir` keeps reporting at night, by design) is substituted
  for the moon exactly as environment.js does, or the night deck is lit
  from under the ground;
* per-puff height inside its cluster (`iExtra.z`), so a cluster shades
  as one mass instead of as a bag of identically top-lit blobs.
"""
from __future__ import annotations

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("clouds.js",)

_PROBE = """
import * as THREE from 'three';
import { makeClouds, makeCirrus, cloudTexture } from './lib/clouds.js';

const a = makeClouds({ seed: 11, preset: 'day' });
const b = makeClouds({ seed: 11, preset: 'day' });
const c = makeClouds({ seed: 12, preset: 'day' });
const offA = a.geometry.getAttribute('iOff').array;
const offB = b.geometry.getAttribute('iOff').array;
const offC = c.geometry.getAttribute('iOff').array;
let sameSeed = offA.length === offB.length;
if (sameSeed) {
  for (let i = 0; i < offA.length; i++) {
    if (offA[i] !== offB[i]) { sameSeed = false; break; }
  }
}
let diffSeed = offA.length !== offC.length;
if (!diffSeed) {
  for (let i = 0; i < offA.length; i++) {
    if (offA[i] !== offC[i]) { diffSeed = true; break; }
  }
}

const golden = makeClouds({ seed: 11, preset: 'golden' });
const t0 = a.material.uniforms.uTime.value;
a.userData.update(4.2);

// per-puff height within its own cluster
const ex = a.geometry.getAttribute('iExtra');
let hMin = 1e9, hMax = -1e9;
for (let i = 0; i < ex.count; i++) {
  hMin = Math.min(hMin, ex.getZ(i));
  hMax = Math.max(hMax, ex.getZ(i));
}

const tex = cloudTexture(7, 64);
const d = tex.image.data;
let alphaSum = 0, bottomRow = 0, topAlpha = 0;
for (let i = 3; i < d.length; i += 4) alphaSum += d[i];
for (let x = 0; x < 64; x++) {
  bottomRow += d[x * 4 + 3];
  topAlpha += d[((63 * 64) + x) * 4 + 3];
}

const cirrus = makeCirrus({ seed: 23, count: 8 });
const cirrusNight = makeCirrus({ seed: 23, count: 8, preset: 'night' });

// the sun actually reaches the shader, and a set sun becomes the moon
const day = makeClouds({ seed: 3, sunDir: new THREE.Vector3(-0.6, 0.55, -0.3) });
const set = makeClouds({ seed: 3, sunDir: new THREE.Vector3(0.62, -0.34, 0.21) });
const noSun = makeClouds({ seed: 3 });
const dayDir = day.material.uniforms.uSunDir.value.clone();
const setDir = set.material.uniforms.uSunDir.value.clone();

// Assembled by lib/shader.js, so they carry what three appends to its
// own fragment shaders: the tone map and the output colour space.
const layers = [a.material, cirrus.material];
const fromHelper = layers.every((m) => m.userData.astraShader === true);
const logdepth = layers.every(
    (m) => m.vertexShader.includes('<logdepthbuf_pars_vertex>')
        && m.vertexShader.includes('<logdepthbuf_vertex>')
        && m.fragmentShader.includes('<logdepthbuf_pars_fragment>')
        && m.fragmentShader.includes('<logdepthbuf_fragment>'));
const outChunks = layers.every(
    (m) => m.fragmentShader.includes('<tonemapping_fragment>')
        && m.fragmentShader.includes('<colorspace_fragment>'));
const fogBlend = layers.some(
    (m) => m.fragmentShader.includes('<fog_fragment>'));
const readsFog = layers.every(
    (m) => m.fragmentShader.includes('fogColor')
        && m.uniforms.fogColor !== undefined
        && m.uniforms.fogDensity !== undefined
        && m.fog === true);

console.log(JSON.stringify({
  sameSeed, diffSeed, fromHelper, logdepth, outChunks, fogBlend, readsFog,
  isSingleMesh: a.isMesh === true,
  instances: a.geometry.instanceCount,
  depthWrite: a.material.depthWrite,
  t0, tAfter: a.material.uniforms.uTime.value,
  sunHexDay: a.material.uniforms.uSunColor.value.getHex(),
  sunHexGolden: golden.material.uniforms.uSunColor.value.getHex(),
  clusterHMin: hMin, clusterHMax: hMax, extraItems: ex.itemSize,
  alphaMean: alphaSum / (64 * 64),
  bottomRowMean: bottomRow / 64,
  topRowMean: topAlpha / 64,
  dayDir: [dayDir.x, dayDir.y, dayDir.z],
  setDir: [setDir.x, setDir.y, setDir.z],
  noSunDir: noSun.material.uniforms.uSunDir.value.y,
  cirrusIsMesh: cirrus.isMesh === true,
  cirrusInstances: cirrus.geometry.instanceCount,
  cirrusTransparent: cirrus.material.transparent
      && !cirrus.material.depthWrite,
  cirrusHexDay: cirrus.material.uniforms.uSunColor.value.getHex(),
  cirrusHexNight: cirrusNight.material.uniforms.uSunColor.value.getHex(),
  cirrusGainDay: cirrus.material.uniforms.uLitGain.value,
  cirrusGainNight: cirrusNight.material.uniforms.uLitGain.value,
}));
"""

_SCENE = """
import * as THREE from 'three';
import { makeClouds, makeCirrus } from './lib/clouds.js';

export function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  const sunDir = new THREE.Vector3(-0.6, 0.62, -0.5).normalize();
  scene.add(makeClouds({ seed: 11, preset: 'day', sunDir }));
  scene.add(makeCirrus({ seed: 23, sunDir }));
  const cameras = [{ name: 'sky', position: [0, 3, 12], lookAt: [0, 40, 0] }];
  return { scene, cameras, update() {} };
}
"""


def _measure() -> dict:
    return measure(_PROBE, _LIBS)


def test_clouds_are_one_deterministic_instanced_mesh():
    m = _measure()
    assert m["isSingleMesh"], "cumulus must be ONE mesh (one draw call)"
    assert m["instances"] > 40, m["instances"]
    assert m["sameSeed"], "same seed must rebuild identical instances"
    assert m["diffSeed"], "different seed must move the field"
    assert m["depthWrite"] is False, "soft alpha needs depthWrite off"


def test_presets_change_the_sun_tint_and_update_drives_drift():
    m = _measure()
    assert m["sunHexDay"] != m["sunHexGolden"], "presets must differ"
    assert m["t0"] == 0 and abs(m["tAfter"] - 4.2) < 1e-9, (
        "userData.update(t) must drive the wind uniform"
    )


def test_puff_texture_has_body_and_a_flat_base():
    m = _measure()
    assert 8 < m["alphaMean"] < 120, (
        f"puff alpha off the usable band: {m['alphaMean']}"
    )
    # DataTexture row 0 = uv bottom: the base cut must leave it empty.
    assert m["bottomRowMean"] < 2, (
        f"cloud base is not flat: {m['bottomRowMean']}"
    )


def test_a_cluster_carries_its_own_height_gradient():
    """Every billboard wearing the same top-lit gradient is what makes a
    cumulus read as a bag of blobs: the shading needs to know which puffs
    are the crown and which are the shaded underside of the same mass."""
    m = _measure()
    assert m["extraItems"] == 3, "iExtra must carry the in-cluster height"
    assert m["clusterHMin"] < 0.12 and m["clusterHMax"] > 0.88, (
        f"in-cluster height must span base to crown: "
        f"{m['clusterHMin']}..{m['clusterHMax']}"
    )


def test_the_layer_is_lit_by_the_rig_and_a_set_sun_becomes_the_moon():
    """`sunDir` reached the doc-comment and nothing else, so a low sun and
    a noon sun painted the same cloud.  And `sunRig().sunDir` keeps
    reporting the authored sun after it sets — passed straight through,
    every night puff is lit from under the ground."""
    m = _measure()
    assert m["dayDir"][1] > 0.5, (
        f"a daylight sunDir must reach the shader: {m['dayDir']}"
    )
    assert m["setDir"][1] > 0.4, (
        f"a set sun must be replaced by a moon ABOVE the horizon: "
        f"{m['setDir']}"
    )
    assert m["setDir"][0] < 0, "the moon rises opposite the sun"
    assert m["noSunDir"] == 1.0, "no sunDir keeps the old top-lit look"


def test_every_layer_is_assembled_by_the_shader_helper():
    """A hand-built ShaderMaterial gets neither of the two chunks three
    appends to its own fragment shaders.  THIS renderer has no post
    chain, so the tone map and the sRGB encode happen in that tail: a
    shader missing it renders dark and untonemapped next to every
    built-in in every frame, not just in previews."""
    m = _measure()
    assert m["fromHelper"], (
        "a cloud layer was not built by lib/shader.js makeShaderMaterial "
        "(no userData.astraShader)"
    )
    assert m["logdepth"], "a cloud layer dropped the logdepthbuf chunks"
    assert m["outChunks"], (
        "a cloud layer is missing tonemapping_fragment / "
        "colorspace_fragment — it renders dark and untonemapped"
    )


def test_the_deck_keeps_the_fog_colour_without_taking_the_fog_blend():
    """Scene fog is authored for scene-scale geometry; a cloud deck is
    kilometres out.  Taking three's blend erases the layer (measured);
    ignoring fog entirely gives the sticker look.  So: no fog_fragment,
    but the fog uniforms live and `material.fog` stays true so the
    renderer keeps them current."""
    m = _measure()
    assert not m["fogBlend"], (
        "the deck took three's fog blend: at a courtyard's fog density "
        "every puff resolves to exactly the fog colour"
    )
    assert m["readsFog"], (
        "the deck must still read fogColor/fogDensity (material.fog "
        "true) — its haze and its sky bounce come from the scene"
    )


def test_cirrus_is_one_transparent_billboard_mesh_graded_by_preset():
    # Sprites rendered as black slabs under the post chain (manhattan
    # run); cirrus rides the proven instanced billboard shader.
    m = _measure()
    assert m["cirrusIsMesh"], "cirrus must be ONE instanced mesh"
    assert m["cirrusInstances"] > 8
    assert m["cirrusTransparent"]
    assert m["cirrusHexDay"] != m["cirrusHexNight"], (
        "cirrus colour was fixed at daylight ice: over a night rig that "
        "band reads as a light leak"
    )
    assert m["cirrusGainNight"] < m["cirrusGainDay"]


def test_every_cloud_shader_compiles_on_the_gpu():
    code, out = compile_scene(_SCENE, _LIBS)
    assert code == 0, out
