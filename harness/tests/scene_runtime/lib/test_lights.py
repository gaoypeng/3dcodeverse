"""lights.js: panel lights ship init-fused, pre-aimed, and un-settleable.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_lights_lib.py).  The reference's claims are kept — RectAreaLight
renders BLACK without one-time LTC init, emits along local -Z, and needs a
glow mesh — and three things this port had to add are pinned with them,
because each one was a defect measured on OUR host, not a style preference:

1. COLOUR SPACE.  The Tanner Helland fit is an 8-bit *sRGB* triple and the
   reference handed it to `new THREE.Color(r, g, b)`, which writes its
   arguments into three's srgb-linear working space RAW.  Every class came out
   washed toward white: rendered through our pipeline the tungsten window read
   (158,152,145) — a grey card — and the fluorescent shop, the LED sign and the
   tungsten window were three copies of the same panel, which is precisely the
   "class SPLIT" the module exists to sell.  A hex colour already takes the
   sRGB decode inside three, so the fit had to as well; measured after,
   tungsten reads (239,205,148), saturation 0.176 -> 0.380.

2. SETTLE.  This host seats floating assets on the ground before the first
   frame (runtime_js/lib/host_placement.mjs).  A `PanelLight` is a group of
   meshes that floats by definition, so a window panel authored at y=4.4 on a
   wall was moved to y=0.60 — its own half-height — and rendered lying at the
   kerb.  `userData.placement = 'free'` is the documented opt-out and a
   luminaire is exactly what it is for.

3. NO POST CHAIN.  There is no bloom pass here, so the panel has to carry its
   own glow: an emissiveMap that keeps the rim off zero (a rim at zero uncovers
   the dark base colour and draws a black bezel round every lamp) and an
   additive spill quad *behind* the panel, where the depth test leaves only the
   halo outside its edge.
"""
from __future__ import annotations

import pytest
from _probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("lights.js",)


def _measure(script: str) -> dict:
    return measure(script, _LIBS)


_PROBE = """
import * as THREE from 'three';
import { makePanelLight, initRectAreaLights } from './lib/lights.js';

// LTC must be absent until the FIRST makePanelLight auto-inits it.
const before = !!(THREE.UniformsLib.LTC_FLOAT_1);
const g = makePanelLight(4, 2);
const after = !!(THREE.UniformsLib.LTC_FLOAT_1);
const ref1 = THREE.UniformsLib.LTC_FLOAT_1;
initRectAreaLights();
initRectAreaLights();
const stable = THREE.UniformsLib.LTC_FLOAT_1 === ref1;

const light = g.userData.light;
const panel = g.userData.panel;
const halo = g.userData.halo;

// Aiming contract: group.lookAt(target) must point the EMISSION
// (the raw light's local -Z) at the target.
const scene = new THREE.Scene();
scene.add(g);
g.position.set(0, 0, 0);
g.lookAt(new THREE.Vector3(0, 0, 7));
scene.updateMatrixWorld(true);
const m = light.matrixWorld.elements;
const emission = new THREE.Vector3(-m[8], -m[9], -m[10]).normalize();
const aimDot = emission.dot(new THREE.Vector3(0, 0, 1));

const custom = makePanelLight(3, 1, 0xffdca8, 11, { glow: 4 });
const bare = makePanelLight(2, 2, 0xffffff, 6, { panel: false });
const plain = makePanelLight(2, 1, 'sodium', 8, { halo: false });

// the emissive map is the diffuser profile: flat core, rim held off zero
const tex = panel.material.emissiveMap;
const N = tex.image.width;
const at = (u, v) => tex.image.data[((v * N + u) * 4)] / 255;

console.log(JSON.stringify({
  before, after, stable,
  isRect: light.isRectAreaLight === true,
  w: light.width, h: light.height,
  color: light.color.getHex(),
  intensity: light.intensity,
  aimDot,
  free: g.userData.placement,
  panelThere: !!panel,
  panelBehind: panel.position.z < 0 && panel.position.z > -0.05,
  glowHex: panel.material.emissive.getHex(),
  glowI: panel.material.emissiveIntensity,
  panelW: panel.geometry.parameters.width,
  panelH: panel.geometry.parameters.height,
  baseLin: panel.material.color.r,
  roughness: panel.material.roughness,
  texShared: makePanelLight(1, 1).userData.panel.material.emissiveMap === tex,
  texLinear: tex.colorSpace === THREE.NoColorSpace,
  texCore: at(N >> 1, N >> 1),
  texRim: at(0, N >> 1),
  texMid: at(Math.round(N * 0.88), N >> 1),
  // dither: an undithered smoothstep row is MONOTONE, so count the
  // sign changes along it — jitter is the only thing that makes any.
  texFlips: (() => {
    let f = 0, prev = 0;
    for (let u = 1; u < N; u++) {
      const d = at(u, N >> 1) - at(u - 1, N >> 1);
      if (d === 0) continue;
      if (prev !== 0 && Math.sign(d) !== prev) f++;
      prev = Math.sign(d);
    }
    return f;
  })(),
  haloThere: !!halo,
  haloBehindPanel: halo.position.z < panel.position.z,
  haloAdditive: halo.material.blending === THREE.AdditiveBlending,
  haloDepthWrite: halo.material.depthWrite,
  haloFog: halo.material.fog,
  haloOpacity: halo.material.opacity,
  haloHex: halo.material.color.getHex(),
  haloW: halo.geometry.parameters.width,
  haloH: halo.geometry.parameters.height,
  customColor: custom.userData.light.color.getHex(),
  customI: custom.userData.light.intensity,
  customGlowHex: custom.userData.panel.material.emissive.getHex(),
  customGlowI: custom.userData.panel.material.emissiveIntensity,
  bareNoPanel: bare.userData.panel === null,
  bareNoHalo: bare.userData.halo === null,
  plainNoHalo: plain.userData.halo === null,
  plainHasPanel: !!plain.userData.panel,
  name: g.name,
}));
"""


@pytest.fixture(scope="module")
def probe() -> dict:
    """One node launch of _PROBE, shared by every test that reads it."""
    return _measure(_PROBE)


def test_ltc_init_is_automatic_and_runs_exactly_once(probe):
    """Without RectAreaLightUniformsLib.init() the light renders black
    with no error; the wrapper installs it on first use and must never
    rebuild the LTC tables on later calls."""
    m = probe
    assert not m["before"], "probe invalid: LTC preexisted"
    assert m["after"], "makePanelLight did not init LTC"
    assert m["stable"], "second init rebuilt the LTC tables"


def test_group_lookat_aims_the_emission_at_the_target(probe):
    """RectAreaLight emits along local -Z while lookAt aims +Z; the
    wrapper pre-rotates so group.lookAt(target) points the EMISSION at
    the target — lose the flip and windows light the wrong wall."""
    m = probe
    assert m["aimDot"] > 0.999, f"emission misaimed: dot={m['aimDot']}"


def test_light_and_glow_panel_agree_and_options_are_alive(probe):
    """Fused contract: the panel carries the light's colour, sits just
    behind the emitting plane at its exact size, and every documented
    option reaches the objects — else glow and cast light disagree."""
    m = probe
    assert m["isRect"] and (m["w"], m["h"]) == (4, 2), m
    assert m["color"] == 0xffffff and m["intensity"] == 6, \
        f"measured defaults drifted: {m['color']:x}/{m['intensity']}"
    assert m["panelThere"] and m["panelBehind"], m
    assert (m["panelW"], m["panelH"]) == (4, 2), "panel != light size"
    assert m["glowHex"] == 0xffffff and m["glowI"] == 2.2, m
    assert m["customColor"] == 0xffdca8 and m["customI"] == 11, m
    assert m["customGlowHex"] == 0xffdca8 and m["customGlowI"] == 4, m
    assert m["bareNoPanel"], "panel:false still built a panel"
    assert m["bareNoHalo"], "panel:false left an orphan halo"
    assert m["name"] == "PanelLight"


def test_the_panel_is_exempt_from_the_host_settle_pass(probe):
    """A luminaire hangs where the author hung it.  This host seats
    floating assets before the first frame, and a PanelLight floats by
    definition: unmarked, a 1.5x1.2 window authored at y=4.4 came back
    at y=0.60 (its own half-height) and rendered lying at the kerb."""
    assert probe["free"] == "free", \
        "userData.placement lost — the host will settle panels to the ground"


def test_the_diffuser_map_is_a_shared_linear_profile_that_never_hits_zero(probe):
    """No bloom pass here, so the panel's own falloff is the glow.  The
    map must be a LINEAR multiplier (a decoded sRGB one would lift the
    rim and flatten the core), one texture across every panel, and its
    rim must stay off zero: a rim at zero uncovers the dark base colour
    and draws a black bezel around every lamp in the scene."""
    m = probe
    assert m["texLinear"], "emissiveMap would be sRGB-decoded"
    assert m["texShared"], "a new diffuser texture per panel light"
    assert m["texCore"] > 0.9, m["texCore"]
    assert 0.25 < m["texRim"] < 0.45, f"rim {m['texRim']}: bezel or no falloff"
    assert m["texMid"] < m["texCore"], "no roll-off at all"
    assert m["texFlips"] > 4, \
        f"monotone ramp ({m['texFlips']} flips) — an 8-bit gradient that bands"


def test_the_halo_is_additive_spill_hidden_behind_its_own_panel(probe):
    """The spill quad sits BEHIND the panel so the depth test hides its
    blown centre and leaves only the glow around the edges; additive and
    fog-exempt, because additive fog ADDS the fog colour instead of
    fading the glow into it."""
    m = probe
    assert m["haloThere"] and m["haloBehindPanel"], m
    assert m["haloAdditive"] and not m["haloDepthWrite"], m
    assert m["haloFog"] is False, "additive halo would add the fog colour"
    assert m["haloOpacity"] == 0.6, m["haloOpacity"]
    assert m["haloHex"] == m["glowHex"], "halo disagrees with the light"
    assert m["haloW"] > m["panelW"] and m["haloH"] > m["panelH"], m
    # aspect kept: a 4x2 panel must not get a square halo
    assert m["haloW"] > m["haloH"], m
    assert m["plainNoHalo"] and m["plainHasPanel"], "halo:false killed the panel"


def test_light_classes_separate_and_order_by_temperature():
    """The realism of a night street is its light classes SEPARATING —
    sodium amber, tungsten homes, fluorescent shops, cool LED.  So the
    class table must actually order by temperature (red/blue ratio
    falls as kelvin rises), the classes a scene mixes must be visibly
    apart, and 'moon' must lean blue: moonlight photographs blue, and a
    black-body 4100 K would hand a night scene a WARM moon."""
    out = _measure("""
import { kelvinColor, LIGHT_CLASSES, makePanelLight }
  from './lib/lights.js';
const ratio = (c) => c.r / Math.max(c.b, 1e-4);
const classes = {};
for (const [k, v] of Object.entries(LIGHT_CLASSES)) {
  classes[k] = { kelvin: v.kelvin, ratio: ratio(v.color),
                 hex: v.color.getHexString() };
}
const shop = makePanelLight(2, 1, 'fluorescent');
console.log(JSON.stringify({
  classes,
  k2000: ratio(kelvinColor(2000)),
  k6500: ratio(kelvinColor(6500)),
  clampLow: kelvinColor(200).getHexString()
      === kelvinColor(1000).getHexString(),
  panelHex: shop.userData.light.color.getHexString(),
  fluorHex: LIGHT_CLASSES.fluorescent.color.getHexString(),
  moonBlue: LIGHT_CLASSES.moon.color.b > LIGHT_CLASSES.moon.color.r,
}));
""")
    cl = out["classes"]
    # Warmer class, higher red/blue ratio — strictly, through the range.
    order = ["sodium", "tungsten", "halogen", "fluorescent",
             "metal_halide", "led_cool"]
    ratios = [cl[k]["ratio"] for k in order]
    assert all(a > b for a, b in zip(ratios, ratios[1:], strict=False)), cl
    # The two classes one street mixes must be visibly apart.  The
    # reference put led_cool under 1.05; decoding the fit from sRGB
    # deepens every ratio (its own point), and 6300 K lands at 1.10.
    assert cl["sodium"]["ratio"] > 2.5, cl
    assert cl["led_cool"]["ratio"] < 1.15, cl
    assert out["k2000"] > out["k6500"], out
    assert out["clampLow"], out
    # A class NAME is a colour anywhere a colour goes.
    assert out["panelHex"] == out["fluorHex"], out
    assert out["moonBlue"], out


def test_the_kelvin_fit_is_decoded_from_srgb_like_every_other_colour():
    """The fit is an 8-bit sRGB triple.  Written raw into three's
    srgb-linear working space it states a gamma-encoded value as a
    linear one and every class washes toward white — measured on our
    renderer, the tungsten window panel read (158,152,145), a grey card
    beside a fluorescent one that read (218,215,214).  A hex colour
    takes the decode inside three, so the fit must take it too or the
    module's two colour paths disagree with each other.

    The floor exists for the same reason: below 1900 K the fit snaps
    blue to a hard 0, a discontinuity in the fit rather than physics,
    and a dead channel makes a sodium pool clip to a pure hue and band.
    """
    out = _measure("""
import * as THREE from 'three';
import { kelvinColor, LIGHT_CLASSES } from './lib/lights.js';
// The Helland fit for 2700 K, in the 0-255 sRGB the formula produces.
const t = 27;
const raw = [255,
             99.4708025861 * Math.log(t) - 161.1195681661,
             138.5177312231 * Math.log(t - 10) - 305.0447927307];
const asLinear = new THREE.Color().setRGB(
    raw[0] / 255, raw[1] / 255, raw[2] / 255, THREE.SRGBColorSpace);
const c = kelvinColor(2700);
// the hex path, which three has always decoded
const hex = new THREE.Color(0xffffff);
const white = kelvinColor(6600);
console.log(JSON.stringify({
  decoded: [c.r, c.g, c.b],
  expect: [asLinear.r, asLinear.g, asLinear.b],
  hexWhiteR: hex.r,
  whiteR: white.r,
  sodiumB: LIGHT_CLASSES.sodium.color.b,
  moonR: LIGHT_CLASSES.moon.color.r,
}));
""")
    got, want = out["decoded"], out["expect"]
    assert all(abs(a - b) < 1e-4 for a, b in zip(got, want, strict=True)), (got, want)
    # the decode is what deepens the colour: raw, green would sit at 0.65
    assert got[1] < 0.45, f"2700 K green {got[1]:.3f} — fit written raw"
    # white still means white on both paths, so the default is unmoved
    assert abs(out["whiteR"] - out["hexWhiteR"]) < 1e-6, out
    # no dead channel anywhere in the table
    assert 0 < out["sodiumB"] < 0.01, out["sodiumB"]
    # moon was ALREADY a hex and must not have moved
    assert abs(out["moonR"] - 0.4620) < 5e-3, out["moonR"]


_SCENE = """
import * as THREE from 'three';
import { makePanelLight } from './lib/lights.js';
export const BOUNDS = { min: [-8, 0, -8], max: [8, 8, 8] };
export function heightAt() { return 0; }
export async function createScene() {
  const scene = new THREE.Scene();
  const wall = new THREE.Mesh(new THREE.BoxGeometry(6, 4, 0.3),
      new THREE.MeshStandardMaterial({ color: 0x8a8378, roughness: 0.9 }));
  wall.position.set(0, 2, -2);
  scene.add(wall);
  const shop = makePanelLight(2.4, 1.2, 'fluorescent', 9);
  shop.position.set(0, 2.2, -1.6);
  shop.lookAt(0, 1.6, 4);
  scene.add(shop);
  const fill = makePanelLight(3, 2, 'tungsten', 5, { panel: false });
  fill.position.set(2, 2, 2);
  fill.lookAt(0, 2, -2);
  scene.add(fill);
  const cameras = [{ name: 'a', position: [0, 2, 5], lookAt: [0, 2, -2], fov: 45 }];
  return { scene, cameras, update() {} };
}
"""


def test_a_panel_light_compiles_on_the_gpu():
    """RectAreaLight is its own shader permutation (the LTC path), and a
    missing LTC table is silent — black, no error.  Boot a lit scene
    headless so the permutation is proved to build on this host.
    """
    code, out = compile_scene(_SCENE, _LIBS, timeout_s=120.0)
    assert code == 0, out
