"""Under water everything is a column, and every shortcut is silent.

``submerged.js`` ships three things a pool needs and one thing can go
wrong with each of them without an error.  An extinction that is one
scalar compiles and turns a floor grey instead of teal, because losing
red first IS the effect.  A ring that scales its own card compiles and
reads as a sprite popping, because a wave is a place on a surface, not a
growing decal.  A crack drawn from one noise call compiles and reads as
cracked paint, because ice forks.  And all three land on materials
``caustics.js``, ``waterside.js``, ``terrain_shade.js`` and ``aging.js``
are already on, where a shared uniform name is absorbed in silence and a
shared local takes the whole material down.

Ported 2026-09-01 from the scene_multifile_graphics reference test.  The
assertions about THEIR renderer contract are gone (their fixture-level
``scene_check`` too — our ``check_shaders.mjs`` boots ``src/scene.js``,
so the GPU pass is a scene of its own, test_patch_union.py's, which also
holds its names against every library it lands on).  What is new is the colour
half this host measured and this session added: the turbidity spectrum,
the veil's dither and its ``light``, and the FRESNEL the ice interior
now sits behind.
"""

from __future__ import annotations

import math
import re

from tests.scene_runtime.lib._probe import LIB_DIR, SHADER_JS, measure

_LIBS = ("shader.js", "submerged.js", "caustics.js", "waterside.js",
         "terrain_shade.js")
_LIB = LIB_DIR / "submerged.js"


def _measure(script: str) -> dict:
    return measure(SHADER_JS + script, _LIBS)


def _approx(x):
    """Local approx: one comparison does not justify an import."""
    class _A:  # pylint: disable=too-few-public-methods
        def __eq__(self, other):
            return abs(other - x) < 1e-9
    return _A()


def test_extinction_is_a_spectrum_and_red_goes_first():
    """The whole effect.  Water does not dim what is under it, it eats
    the RED out of it first — an order of magnitude faster than the blue
    — which is why a submerged rock goes teal and not grey.  So the
    coefficient is a vec3, the loss is Beer-Lambert on it, and the
    default scalar rides water's own spectrum.  A single float here
    would compile, run, and read as a rock in fog.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchUnderwater } from './lib/submerged.js';
const sh = fake();
const m = patchUnderwater(new THREE.MeshStandardMaterial());
m.onBeforeCompile(sh);
const u = (mat, k) => mat.userData.uniforms[k].value;
const strong = patchUnderwater(new THREE.MeshStandardMaterial(),
    { extinction: 0.9 });
const given = patchUnderwater(new THREE.MeshStandardMaterial(),
    { extinction: [0.5, 0.2, 0.05] });
const vec = patchUnderwater(new THREE.MeshStandardMaterial(),
    { extinction: new THREE.Vector3(0.4, 0.3, 0.2) });
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  ext: u(m, 'uSubExt'), strong: u(strong, 'uSubExt'),
  given: u(given, 'uSubExt'), vec: u(vec, 'uSubExt'),
  neg: u(patchUnderwater(new THREE.MeshStandardMaterial(),
      { extinction: -3 }), 'uSubExt'),
  color: u(m, 'uSubColor').getHex(),
}));
""")
    fs = out["fs"]
    ext = out["ext"]
    # Three coefficients, not one: the ratio IS the effect.
    assert "uniform vec3 uSubExt;" in fs
    assert ext["x"] > ext["y"] > ext["z"] > 0, ext
    assert ext["x"] > 3 * ext["y"], "red must die far faster than green"
    assert ext["y"] > 2 * ext["z"], "and green faster than blue"
    # Beer-Lambert on the albedo, exponential in the path.
    assert "vec3 subT = exp(-subK * ((subD + subView) * subMk));" in fs
    assert "diffuseColor.rgb *= subT;" in fs
    # A scalar scales the spectrum; a triple is taken as given.
    assert out["strong"]["x"] / ext["x"] == _approx(0.9 / 0.35)
    assert out["given"] == {"x": 0.5, "y": 0.2, "z": 0.05}
    assert out["vec"] == {"x": 0.4, "y": 0.3, "z": 0.2}
    # A negative coefficient would AMPLIFY with depth and blow out.
    assert all(v == 0 for v in out["neg"].values()), out["neg"]

    # And the numbers the shipped default actually produces.  Same
    # surface, three depths, one metre of view path per metre of depth.
    rgb = []
    for depth in (0.5, 2.0, 5.0):
        path = depth * 2
        rgb.append(tuple(
            math.exp(-ext[a] * path) for a in ("x", "y", "z")))
    for r, g, b in rgb:
        assert r < g < b, (r, g, b)
    # Red is not merely lower, it FALLS faster: by 5 m it is gone while
    # the blue has barely started.
    assert rgb[2][0] < 0.05 and rgb[2][2] > 0.35, rgb
    # Over the same three depths the red loses 23x of itself where the
    # blue loses 1.4x — that RATE gap is the whole look.
    assert rgb[0][0] / rgb[2][0] > 10 * (rgb[0][2] / rgb[2][2]), rgb


def test_the_loss_runs_on_depth_and_on_the_ray_to_the_camera():
    """Depth alone is half of it.  Light falls through the column to the
    surface and then travels BACK OUT through it to the eye, so the far
    end of a pool floor is further gone than the near end at the same
    depth — that second leg is what makes a bay recede instead of merely
    darkening.  The eye ray counts only where it is under the surface,
    which is its own share of the climb to the camera, so a camera in
    the air and a camera in the water both come out right.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchUnderwater } from './lib/submerged.js';
const sh = fake();
patchUnderwater(new THREE.MeshStandardMaterial(), { level: 3 })
    .onBeforeCompile(sh);
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    assert "float subRise = max(cameraPosition.y - vAstraWorld.y, 1e-4);" in fs
    assert "float subWet = clamp(subD / subRise, 0.0, 1.0);" in fs
    assert "float subView = length(cameraPosition - vAstraWorld) * subWet;" in fs
    # Both legs, summed, before the exponential.
    assert "(subD + subView)" in fs


def test_nothing_above_the_water_line_is_touched():
    """A column that climbs the dry bank is the tell that it is a
    texture, and here it would tint the whole scene.  Both terms are
    gated by the same clamp: the depth is clamped at zero at the line
    and the ray's underwater share is ``depth / rise``, which is zero
    there too — so the transmittance is exactly 1.0 and the veil exactly
    0.0 above the line, not merely small, and with no step AT the line
    either.  And the patch writes in exactly two places, so it cannot
    leak past the line by some other route.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchUnderwater } from './lib/submerged.js';
const sh = fake();
const m = patchUnderwater(new THREE.MeshStandardMaterial(),
    { level: 2.5, murk: 0.4 });
m.onBeforeCompile(sh);
const fs = sh.fragmentShader;
console.log(JSON.stringify({
  fs, body: fs.slice(fs.indexOf('#include <color_fragment>')),
  level: m.userData.uniforms.uSubLevel.value,
  murk: m.userData.uniforms.uSubMurk.value,
  clampMurk: patchUnderwater(new THREE.MeshStandardMaterial(),
      { murk: 9 }).userData.uniforms.uSubMurk.value,
}));
""")
    fs, body = out["fs"], out["body"]
    assert out["level"] == 2.5 and out["murk"] == 0.4
    assert "float subD = max(uSubLevel - vAstraWorld.y, 0.0);" in fs
    # exp(-k * 0) is exactly 1 and 1 - 1 is exactly 0: above the line
    # the albedo is untouched and the veil contributes nothing.  The
    # dither and the hue break are both MULTIPLIERS on that zero, so
    # neither can resurrect it.
    assert "totalEmissiveRadiance += subVeil;" in fs
    # The murk multiplies the PATH, so it cannot resurrect a zero path
    # above the line, and it is clamped so it can never go negative.
    assert out["clampMurk"] == 1
    assert "float subS = uSubMurk * (astraFbm2(subQ, 3) - 0.44);" in fs
    assert "float subMk = 1.0 + subS;" in fs
    writes = re.findall(
        r"^\s*(diffuseColor|totalEmissiveRadiance|gl_FragColor|"
        r"roughnessFactor)[.\w]*\s*[-+*/]?=", body, re.M)
    assert writes == ["diffuseColor", "totalEmissiveRadiance"], writes
    # roughnessFactor does not exist at this hook, and gloss belongs to
    # waterside's wet patch anyway.
    assert "roughnessFactor" not in fs


def test_the_veil_is_what_kills_contrast_rather_than_darkness():
    """Under water two neighbouring albedos converge, and they converge
    on the WATER's colour, not on black.  That is one subtraction and
    one addition: the surface's own return is multiplied by the
    transmittance, and the column scatters back exactly what was lost,
    so the difference between two albedos falls by the transmittance
    while the mean walks to the water.  Dropping the veil would leave a
    submerged floor merely dark, which is the look of a scene that
    forgot to light it.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchUnderwater } from './lib/submerged.js';
const sh = fake();
const m = patchUnderwater(new THREE.MeshStandardMaterial(),
    { color: 0x123456 });
m.onBeforeCompile(sh);
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  color: m.userData.uniforms.uSubColor.value.getHex(),
  warned: (() => {
    const said = [];
    console.warn = (msg) => said.push(String(msg));
    patchUnderwater(new THREE.MeshStandardMaterial());
    const quiet = said.length;
    patchUnderwater(new THREE.MeshBasicMaterial({ name: 'Tile' }));
    patchUnderwater(new THREE.ShaderMaterial({ name: 'Ocean' }));
    return { quiet, said };
  })(),
}));
""")
    fs = out["fs"]
    assert out["color"] == 0x123456
    assert "vec3 subVeil = uSubColor * uSubLight * (1.0 - subT)" in fs
    # The veil is dimmed by the light that got this deep, and the blue
    # is what got this deep — so a lake bottom goes dark, not neon.
    # (Filtering it per CHANNEL is the more literal physics and was
    # measured worse on this host: the red of a green-blue water colour
    # is already near zero in linear, so the deep end clipped to one
    # channel — region saturation 0.930 -> 0.941, no visible gain.)
    assert "* exp(-uSubExt.b * subD);" in fs
    # The veil lands on LIGHT, so it survives in shadow — which is
    # where a submerged surface loses its contrast hardest.  Two
    # materials cannot carry that term, and both are named out loud.
    assert out["warned"]["quiet"] == 0
    said = out["warned"]["said"]
    assert len(said) == 3, said
    assert "Tile" in said[0] and "totalEmissiveRadiance" in said[0]
    # A raw ShaderMaterial is told BOTH things: it has no emissive term
    # and it has no <color_fragment> hook to be patched at either.
    assert "Ocean" in said[1] and "totalEmissiveRadiance" in said[1]
    assert "Ocean" in said[2] and "<color_fragment>" in said[2]


def test_the_column_has_a_colour_a_dither_and_a_lamp_to_scatter():
    """The three things this host's own frames asked for, added
    2026-09-01 and each measured on them.

    Turbidity is a HUE, not a second dimmer: the silt field that swings
    the path swings the extinction SPECTRUM (silt scatters green and
    drinks blue) and the veil's own colour with it, or a whole basin is
    one flat teal.  The swings are bounded so no coefficient can go
    negative and amplify with depth.  A basin is also a hundred pixels
    of a smooth exponential, which is where 8-bit output lays contours —
    measured: the longest constant run across the deep water fell from
    31 px to 7.  And the veil is scattered LIGHT that lands on the
    emissive term, so nothing in the scene dims it: without ``light``
    the same green glows through a night frame (measured: the night
    basin read (9, 73, 71) against a 0.25 frame and (7, 42, 53) once it
    was turned down — green above blue, which is a daylight pool).
    """
    out = _measure("""
import * as THREE from 'three';
import { patchUnderwater } from './lib/submerged.js';
const sh = fake();
const m = patchUnderwater(new THREE.MeshStandardMaterial(), { murk: 0.8 });
m.onBeforeCompile(sh);
const u = (mat, k) => mat.userData.uniforms[k].value;
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  vs: sh.vertexShader,
  light: u(m, 'uSubLight'),
  night: u(patchUnderwater(new THREE.MeshStandardMaterial(),
      { light: 0.22 }), 'uSubLight'),
  neg: u(patchUnderwater(new THREE.MeshStandardMaterial(),
      { light: -4 }), 'uSubLight'),
  lit: u(patchUnderwater(new THREE.MeshStandardMaterial(),
      { light: 2.5 }), 'uSubLight'),
}));
""")
    fs = out["fs"]
    # The spectrum rides the same field as the path, so it costs one
    # fbm and it drifts with the silt rather than being a static stain.
    assert "vec3 subK = uSubExt" in fs
    assert "* vec3(1.0, 1.0 - 0.30 * subS, 1.0 + 1.60 * subS);" in fs
    # murk <= 1 and the field bottoms out at -0.44, so subS >= -0.44 and
    # every coefficient stays positive: 1 - 0.30*(-0.44) and
    # 1 + 1.60*(-0.44) are both above zero, and so is the top end.
    # The veil's own colour breaks with the same field, which is where
    # the murk becomes VISIBLE — inside the exponent it only moves an
    # already-saturated exponential.
    assert ("subVeil = astraHueBreak(subQ" not in fs), "the field, not the p"
    assert "subVeil = astraHueBreak(subVeil, subQ, 1.0," in fs
    assert "0.30 + 0.9 * uSubMurk);" in fs
    # Dither, per pixel, on the veil — and in the FRAGMENT stage only:
    # gl_FragCoord does not exist in a vertex shader and this patch's
    # GLSL ships into both.
    assert "subVeil *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.07;" in fs
    assert "gl_FragCoord" not in out["vs"]
    # The lamp: 1 by default (so a daylight scene is unchanged), a
    # night scene turns it down, a lit pool can turn it up, and a
    # negative would SUBTRACT light and punch black holes in the water.
    assert out["light"] == 1
    assert out["night"] == 0.22
    assert out["neg"] == 0
    assert out["lit"] == 2.5
    assert "uniform float uSubLight;" in fs


def test_rings_expand_and_fade_with_age_instead_of_popping():
    """A ring on water is a place on the surface the wave has reached,
    and the four things that keep it from reading as a sprite are all
    measurable.  The CARD does not grow — its size is the drop's, never
    the age's — so what moves is the wavefront inside it.  The front
    DECELERATES, so a frozen frame catches rings bunched at the rim and
    wide apart near the impact instead of evenly spaced.  Amplitude
    falls as the same water spreads over a longer front, and the whole
    ring is born through a smoothstep so it cannot appear at full
    strength.  And it is a crest, a trailing crest and a DARK trough
    between them: a lone white circle is a decal.
    """
    out = _measure("""
import { makeRainRings } from './lib/submerged.js';
const m = makeRainRings({ count: 8 }).material;
console.log(JSON.stringify({
  vs: m.vertexShader, fs: m.fragmentShader,
}));
""")
    vs, fs = out["vs"], out["fs"]
    # The card is sized by the drop, not by its age: vAge must not
    # reach the vertex displacement at all.
    assert "* (uRingSize * iExtra.y);" in vs
    disp = vs[vs.rindex("transformed ="):]
    assert "vAge" not in disp, disp
    # The front travels, and decelerates.
    assert "float rgFront = 0.90 * pow(vAge, 0.62);" in fs
    assert "float rgA = (rgR - rgFront) / rgW;" in fs
    # Amplitude falls as the front lengthens, the ring dies with age,
    # and it is born soft rather than at full strength.
    assert "float rgFade = (1.0 - vAge) * smoothstep(0.0, 0.07, vAge)" in fs
    assert "/ (1.0 + 2.2 * rgFront);" in fs
    # Crest, capillary train, and the dark trough between them.
    assert "float rgCrest = exp(-rgA * rgA);" in fs
    assert "float rgTrain = 0.40 * exp(-rgB * rgB);" in fs
    assert "float rgDip = 0.30 * exp(-rgD * rgD);" in fs
    assert "mix(uRingTrough, uRingColor," in fs
    # Antialiased against its own gradient: a 5 cm crest 20 m off is
    # thinner than a pixel and would alias into a dotted circle.
    assert "float rgAA = max(fwidth(rgR), 0.002);" in fs
    assert "rgAA * 1.6);" in fs
    # And a ring thinner than a couple of pixels does not draw at all,
    # because at that size it can only alias into a hard little square.
    assert "rgFade *= smoothstep(rgAA * 1.2, rgAA * 3.5, rgFront);" in fs


def test_every_drop_catches_its_own_patch_of_sky():
    """A field of rings all one colour reads as printed.  The swing is
    per IMPACT — hashed on the same cycle index the relocation uses, so
    it changes when the drop lands somewhere new — and it is carried to
    the fragment as a varying rather than sampled per pixel, or one ring
    would come out speckled instead of one wave.  Measured on this
    host's frames it is a small effect (a peak of 3/255 over a 0.42
    field), which is why it is per drop and not per pixel: it is the
    difference between a rain field and a decal sheet, not a look.
    """
    out = _measure("""
import { makeRainRings } from './lib/submerged.js';
const m = makeRainRings({ count: 8 }).material;
console.log(JSON.stringify({ vs: m.vertexShader, fs: m.fragmentShader }));
""")
    vs, fs = out["vs"], out["fs"]
    assert "varying float vRgT;" in vs and "varying float vRgT;" in fs
    # Hashed on the cycle, so a relocated drop is a new drop — and centred,
    # so the field's mean colour is the one asked for.
    assert "vRgT = astraHash21(vec2(iExtra.z + 2.7, rgCyc)) - 0.5;" in vs
    # Warm one, cool the next, about whatever crest colour was given.
    assert "rgCol *= 1.0 + vRgT * vec3(0.34, 0.14, -0.24);" in fs
    # It rides the colour, never the alpha: a ring that varied its own
    # opacity would flicker as drops relocate.
    tail = fs[fs.index("vRgT * vec3"):]
    assert "a =" not in tail, tail


def test_the_ring_field_is_rain_and_survives_the_render_passes():
    """A field of transparent cards has two ways to wreck a frame that
    have nothing to do with how it looks.  An ambient-occlusion pass
    redraws the scene with an opaque override material, where every one
    of these is a solid wall, so the field must be kept out of the depth
    passes; and ``position`` is all zeros (the same override draws it
    raw), so without a stated bounding sphere three culls the whole
    field the moment the origin leaves frame.  Beyond that it has to be
    RAIN: the impacts relocate every cycle, or the same eight spots
    dimple forever, and their phases are spread so one still shows every
    radius at once.
    """
    out = _measure("""
import * as THREE from 'three';
import { makeRainRings } from './lib/submerged.js';
const mesh = makeRainRings({ area: 30, count: 200, y: 1.5, size: 0.8 });
const g = mesh.geometry;
const ph = g.attributes.iExtra.array;
const phases = [];
for (let i = 0; i < 200; i++) phases.push(ph[i * 3]);
phases.sort((a, b) => a - b);
const num = makeRainRings({ area: 12, count: 4 });
const obj = makeRainRings({ area: { x: 0, z: 0, w: 12, d: 12 }, count: 4 });
console.log(JSON.stringify({
  vs: mesh.material.vertexShader,
  name: mesh.name, order: mesh.renderOrder,
  noOverride: mesh.userData.astraNoOverride,
  casts: mesh.castShadow,
  transparent: mesh.material.transparent,
  depthWrite: mesh.material.depthWrite,
  radius: g.boundingSphere.radius,
  posZero: Array.from(g.attributes.position.array).every((v) => v === 0),
  hasCorner: !!g.attributes.aCorner,
  instances: g.instanceCount,
  ys: [g.attributes.iOff.array[1], g.attributes.iOff.array[4]],
  lo: phases[0], hi: phases[199],
  numArea: Array.from(num.geometry.attributes.iOff.array).every(Number.isFinite),
  sameArea: Array.from(num.geometry.attributes.iOff.array).join(',')
      === Array.from(obj.geometry.attributes.iOff.array).join(','),
  jit: mesh.material.uniforms.uRingJit.value,
}));
""")
    assert out["name"] == "RainRings"
    # Out of the AO/override passes, and not casting a shadow either.
    assert out["noOverride"] is True and out["casts"] is False
    assert out["transparent"] and out["depthWrite"] is False
    assert out["order"] == 2
    # A real radius, big enough for the field it covers (a 30 m square
    # of 0.8 m rings 1.5 m up), and geometry three can cull honestly.
    assert out["posZero"] and out["hasCorner"]
    assert out["instances"] == 200
    assert out["radius"] > 21, out["radius"]
    assert out["radius"] < 40, "a radius that large stops culling at all"
    # Lying just above the water, not in it.
    assert all(abs(y - 1.512) < 1e-6 for y in out["ys"]), out["ys"]
    # Phases spread over the whole cycle: one still shows all radii.
    assert out["lo"] < 0.05 and out["hi"] > 0.95, (out["lo"], out["hi"])
    # `area: 12` is a square, not an undefined width — a truthy number
    # read as an object put every ring at NaN in rain.js.
    assert out["numArea"] and out["sameArea"]
    # Impacts relocate every cycle, inside their own share of the rect.
    vs = out["vs"]
    assert "float rgCyc = floor(rgP);" in vs
    assert "astraHash21(vec2(iExtra.z, rgCyc))" in vs
    assert "* uRingJit;" in vs
    assert abs(out["jit"] - math.sqrt(900 / 200)) < 1e-6, out["jit"]


def test_ice_cracks_branch_instead_of_being_one_noise_call():
    """The one thing that separates ice from cracked paint.  A crack
    leaves an origin and FORKS, so the pattern doubles its ray count
    every step outward with the phase scaled to match — which keeps
    every existing ray exactly where it was and grows a new one between
    them — and cross-fades the two levels so the new arm narrows in
    rather than switching on.  Written with a per-level phase instead,
    the rays would jump to new angles at every ring and the network
    would be concentric bands; written with one fbm it would be a smear.
    The nesting is arithmetic, so it can be measured.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchThinIce } from './lib/submerged.js';
const sh = fake();
patchThinIce(new THREE.MeshStandardMaterial()).onBeforeCompile(sh);
const fs = sh.fragmentShader;
// The shipped ray-distance function, transcribed: rays are where it
// touches zero, so scanning for zeros IS scanning for rays.
const ray = (a, n, j) => {
  const x = (a * 0.15915494 + j) * n;
  return Math.abs(x - Math.floor(x + 0.5)) / n;
};
const rays = (n, j) => {
  const found = [];
  const N = 200000;
  for (let i = 0; i < N; i++) {
    const a = -Math.PI + (2 * Math.PI * i) / N;
    const p = ray(a - 2 * Math.PI / N, n, j);
    const c = ray(a, n, j);
    const q = ray(a + 2 * Math.PI / N, n, j);
    if (c < p && c <= q) found.push(a);
  }
  return found;
};
const j = 0.37;
const at3 = rays(3, j), at6 = rays(6, j);
const near = (x, list) => list.some((y) => Math.abs(x - y) < 1e-3);
console.log(JSON.stringify({
  fs,
  n3: at3.length, n6: at6.length,
  nested: at3.every((a) => near(a, at6)),
  rayCalls: (fs.match(/astraIceRay\\(/g) || []).length,
  crackCalls: (fs.match(/astraIceCracks\\(/g) || []).length,
}));
""")
    fs = out["fs"]
    # The phase is added BEFORE the ray count multiplies it, which is
    # what makes level n a subset of level 2n.
    assert "float x = (atan(d.y, d.x) * 0.15915494 + j) * n;" in fs
    assert "float g = abs(x - k) / n * 6.2831853 * max(length(d), 0.18);" in fs
    # Doubling, and the same j for both levels.
    assert "float n = 3.0 * exp2(lv);" in fs
    assert "vec2 ra = astraIceRay(d, n, j);" in fs
    assert "vec2 rb = astraIceRay(d, n * 2.0, j);" in fs
    assert "float g = mix(ra.x, rb.x, f);" in fs
    assert "float lv = floor(r * 3.0);" in fs
    assert "float f = fract(r * 3.0);" in fs
    # Measured: twice as many rays one level out, and every old one is
    # still there.  That is a fork, not a redraw.
    assert out["n3"] == 3 and out["n6"] == 6, (out["n3"], out["n6"])
    assert out["nested"], "a branch keeps its parent"
    # And no two arms are the same length, or the fork is a snowflake.
    assert "float reach = mix(astraHash21(id + ra.y * 0.37 + 5.0)," in fs
    assert "reach = 0.30 + 0.55 * reach;" in fs
    assert "* (1.0 - smoothstep(reach * 0.55, reach, r));" in fs
    # One definition, called twice per layer and two layers deep, so
    # the cell grid does not read as a grid — and the dark fissure core
    # is taken from the SAME field by a smoothstep rather than a third
    # and fourth call.
    assert out["rayCalls"] == 3, out["rayCalls"]
    assert out["crackCalls"] == 3, "one helper, two rotated layers"
    assert "float iceCr = max(astraIceCracks(iceP, iceW)," in fs
    assert "astraIceCracks(iceQ, iceW * 0.55));" in fs
    assert "float iceCore = smoothstep(0.55, 0.98, iceCr);" in fs
    assert "iceC *= 1.0 - 0.55 * iceCore;" in fs
    # Crooked, not ruled, and antialiased against its own gradient.
    assert "iceP += (astraNoise2(iceP * 0.9 + 5.7) - 0.5) * 0.35;" in fs
    # A crack is 2 cm of ice at any density, so its width is stated in
    # metres and scaled by the density rather than left in cell units.
    assert "float iceW = max(uIceDens * 0.022," in fs
    assert "max(fwidth(iceP.x), fwidth(iceP.y)) * 1.3);" in fs


def test_thin_ice_has_a_depth_a_rim_a_gloss_and_a_fresnel():
    """Ice over water is a SLAB, and three layers at three depths are
    what say so.  The interior is sampled where the eye ray arrives
    after refracting at the surface and running down by the thickness,
    so it slides against the surface as the camera moves — the cracks
    are sampled unrefracted and therefore sit above it.  Thickness also
    tints, at 1.6 per metre: ice is CLEAR, and at the 3.0 this shipped
    with a 10 cm sheet mixed a quarter of a pale mint over the dark
    water and reached an albedo of 0.25 linear, which on a sunlit
    horizontal plane is 200/255 before a crack is drawn.

    And the interior sits behind a FRESNEL while the two surface layers
    do not, because only what the air-ice interface let in ever reached
    the bubbles.  Our renderer adds that reflection itself as specular,
    so without the term the sheet is paid for the sky twice — measured
    here at an 11 deg view: a flat plate at 200/255 with no visible
    crack.  Gloss can only be a MATERIAL value (the hook runs before
    roughness exists), so frostier ice is duller through
    composeRoughness.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchThinIce } from './lib/submerged.js';
const sh = fake();
const m = patchThinIce(new THREE.MeshStandardMaterial({ roughness: 0.8 }),
    { thickness: 0.2, crackDensity: 1.5, frost: 0.5, rim: 0.3,
      color: 0x445566 });
m.onBeforeCompile(sh);
const u = (mat, k) => mat.userData.uniforms[k].value;
const glassy = patchThinIce(
    new THREE.MeshStandardMaterial({ roughness: 0.8 }), { frost: 0 });
const frosty = patchThinIce(
    new THREE.MeshStandardMaterial({ roughness: 0.8 }), { frost: 1 });
console.log(JSON.stringify({
  fs: sh.fragmentShader, vs: sh.vertexShader,
  thick: u(m, 'uIceThick'), dens: u(m, 'uIceDens'),
  frost: u(m, 'uIceFrost'), rim: u(m, 'uIceRim'),
  color: u(m, 'uIceColor').getHex(),
  zeroThick: u(patchThinIce(new THREE.MeshStandardMaterial(),
      { thickness: 0 }), 'uIceThick'),
  zeroRim: u(patchThinIce(new THREE.MeshStandardMaterial(),
      { rim: 0 }), 'uIceRim'),
  rough: m.roughness, glassy: glassy.roughness, frosty: frosty.roughness,
  base: m.userData.astraRoughness.base,
}));
""")
    fs, vs = out["fs"], out["vs"]
    assert out["thick"] == 0.2 and out["dens"] == 1.5
    assert out["frost"] == 0.5 and out["rim"] == 0.3
    assert out["color"] == 0x445566
    # Refraction at the surface, then down by the thickness: that
    # parallax IS the depth, and eta < 1 so refract() cannot return the
    # zero vector and there is no total-internal-reflection case.
    assert "vec3 iceRf = refract(iceV, iceN, 0.763);" in fs
    assert "iceN = faceforward(iceN, iceV, iceN);" in fs
    assert "+ iceRf.xz * (uIceThick / max(abs(iceRf.y), 0.25));" in fs
    assert "float iceB = astraFbm2(icePar * 2.6 + uIceSeed, 3);" in fs
    # The cracks take the UNREFRACTED point, so they stay on top.
    assert "vec2 iceP = vAstraWorld.xz * uIceDens + uIceSeed;" in fs
    # Thickness tints as a column, so thin ice is not thick ice — and
    # 10 cm of it is nearly nothing, which is what 1.6 per metre says.
    assert "float iceK = 1.0 - exp(-uIceThick * 1.6);" in fs
    assert "vec3 iceC = mix(diffuseColor.rgb, uIceColor, iceK);" in fs
    # The FRESNEL, and the ORDER around it: interior first and behind
    # it, cracks and frost after and in front of it.
    assert "float iceFz = astraFresnel(iceN, -iceV, 5.0);" in fs
    assert "iceC *= 1.0 - 0.78 * iceFz;" in fs
    assert fs.index("iceFz;") < fs.index("float iceCr ="), "cracks are surface"
    assert fs.index("iceFz;") < fs.index("float iceFr ="), "frost is on top"
    assert fs.index("astraHueBreak(iceC") < fs.index("iceFz;"), "bubbles are in"
    # Broken colour in all three layers: bubble cloud, crack lip, frost.
    assert "iceC = astraHueBreak(iceC, icePar * 1.4, 1.0, 0.30);" in fs
    assert "vec3(0.87, 0.93, 0.96) * (0.84 + 0.30 * iceB)," in fs
    assert "vec3 iceFc = astraHueBreak(" in fs
    assert "vAstraWorld.xz, 0.8, 0.22);" in fs
    # The rim is the sheet's own border, torn by noise.  The uv comes
    # through this patch's own varying: three declares vUv only when
    # the material happens to carry a map.
    assert "varying vec2 vAstraIceUv;" in vs and "vAstraIceUv = uv;" in vs
    assert "float iceE = min(min(vAstraIceUv.x, 1.0 - vAstraIceUv.x)," in fs
    assert "* uIceRim * 0.9;" in fs
    assert "float iceFr = astraContact(iceE, uIceRim) * uIceFrost;" in fs
    # A sheet is a wide smooth ramp, so it carries its own dither too.
    assert "iceC *= 1.0 + (astraHash21(gl_FragCoord.xy) - 0.5) * 0.03;" in fs
    assert "gl_FragCoord" not in vs
    # Zeros that would divide or smoothstep over an empty band.
    assert out["zeroThick"] > 0 and out["zeroRim"] > 0
    # Gloss through composeRoughness only, off the material's TRUE
    # base, so re-applying cannot compound it.
    assert out["base"] == 0.8
    assert out["glassy"] < out["frosty"] < 0.8, out
    assert abs(out["glassy"] - 0.8 * 0.18) < 1e-9
    assert abs(out["frosty"] - 0.8 * 0.80) < 1e-9
    assert "roughnessFactor" not in fs


def test_the_whole_chain_shares_one_main_without_a_redefinition():
    """The load-bearing case: a pool floor wears the rock projection,
    the waterline, this column and the caustic net at once.
    patchStandard chains by name, so every body must survive in call
    order, the cache key must name the whole chain (the first material
    to compile a key decides the source for all of them), the world
    varyings must be declared once however many patches ask for them,
    and re-applying a patch must retune it rather than stack a second.
    Order matters here beyond compiling: the column runs BEFORE the net,
    so the net is tinted by the already-extinguished albedo.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchUnderwater } from './lib/submerged.js';
import { patchCaustics } from './lib/caustics.js';
import { patchShoreWet } from './lib/waterside.js';
import { patchTriplanar } from './lib/terrain_shade.js';
const sh = fake();
const m = new THREE.MeshStandardMaterial();
patchTriplanar(m);
patchShoreWet(m, { level: 1.2 });
patchUnderwater(m, { level: 1.2, seed: 4 });
patchCaustics(m, { level: 1.2, depthFade: 2.8 });
patchUnderwater(m, { level: 1.4, seed: 4 });  // retune, not a 5th patch
m.onBeforeCompile(sh);
const fs = sh.fragmentShader, vs = sh.vertexShader;
const seen = {};
for (const name of locals(mains(sh))) seen[name] = (seen[name] || 0) + 1;
console.log(JSON.stringify({
  key: m.customProgramCacheKey(),
  level: m.userData.uniforms.uSubLevel.value,
  order: fs.indexOf('subT') < fs.indexOf('cauNet'),
  tri: fs.includes('mix(uTriA, uTriB'),
  sub: fs.includes('diffuseColor.rgb *= subT;'),
  cau: fs.includes('totalEmissiveRadiance += cauTint'),
  varyingVs: count(vs, /varying vec3 vAstraWorld;/g),
  varyingFs: count(fs, /varying vec3 vAstraWorld;/g),
  utilOnce: count(fs, /float astraFbm2\\(/g),
  timeOnce: count(fs, /uniform float uTime;/g),
  uniforms: ['uTriA', 'uWetY', 'uSubExt', 'uCauLevel', 'uTime']
      .every((k) => !!sh.uniforms[k]),
  dupLocals: Object.keys(seen).filter((k) => seen[k] > 1),
}));
""")
    assert out["tri"] and out["sub"] and out["cau"], out
    assert out["key"] == ("astra:terrain:world+terrain:triplanar"
                          "+waterside:base+waterside:shoreWet"
                          "+submerged:base+submerged:underwater"
                          "+caustics:net")
    # The column runs first, so patchCaustics tints its net with the
    # albedo this has already taken the red out of.
    assert out["order"], "bodies run in call order"
    assert out["level"] == 1.4, "re-applying retunes in place"
    assert out["uniforms"], "every patch's uniforms reach the program"
    # The world varyings are shared with the neighbours BY NAME on
    # purpose, so the pair is declared once for the four of them.
    assert out["varyingVs"] == 1 and out["varyingFs"] == 1
    assert out["utilOnce"] == 1 and out["timeOnce"] == 1
    assert out["dupLocals"] == [], out["dupLocals"]


def test_it_is_deterministic_stage_safe_and_tickable():
    """A pool must come back identical on a re-render, so every seed is
    arithmetic and never a Math.random; the patches' GLSL ships into the
    VERTEX stage too, where a fragment-only builtin would fail every
    program in the engine at once; and every one of these rides uTime,
    so ONE ``tickShaders(scene, t)`` has to reach all of them — an
    un-advanced uTime is a frozen silt and a frozen ring.
    """
    out = _measure("""
import * as THREE from 'three';
import { patchUnderwater, patchThinIce, makeRainRings }
    from './lib/submerged.js';
import { tickShaders } from './lib/shader.js';
const a = fake(), b = fake();
const opts = { level: 1.5, seed: 9, extinction: 0.5 };
const ma = patchUnderwater(new THREE.MeshStandardMaterial(), opts);
const mb = patchUnderwater(new THREE.MeshStandardMaterial(), opts);
patchThinIce(ma, { seed: 9 }); patchThinIce(mb, { seed: 9 });
ma.onBeforeCompile(a);
mb.onBeforeCompile(b);
const seed = (m, k) => m.userData.uniforms[k].value;
const s8 = patchUnderwater(new THREE.MeshStandardMaterial(), { seed: 8 });
const r1 = makeRainRings({ seed: 4, count: 6 });
const r2 = makeRainRings({ seed: 4, count: 6 });
const r3 = makeRainRings({ seed: 5, count: 6 });
const arr = (m) => Array.from(m.geometry.attributes.iOff.array);

// One tick, every material: a patched floor and the ring field.
const g = new THREE.Group();
const floor = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), ma);
g.add(floor, r1);
const n = tickShaders(g, 7.5);
console.log(JSON.stringify({
  same: a.fragmentShader === b.fragmentShader
      && a.vertexShader === b.vertexShader,
  seedA: seed(ma, 'uSubSeed'), seedB: seed(mb, 'uSubSeed'),
  seed8: seed(s8, 'uSubSeed'),
  iceSeed: seed(ma, 'uIceSeed'),
  ringsSame: arr(r1).join(',') === arr(r2).join(','),
  ringsDiffer: arr(r1).join(',') !== arr(r3).join(','),
  ticked: n,
  matTime: ma.userData.uniforms.uTime.value,
  ringTime: r1.material.uniforms.uTime.value,
  updateTime: (() => { r1.userData.update(2.25);
      return r1.material.uniforms.uTime.value; })(),
  vsBody: a.vertexShader.slice(a.vertexShader.indexOf('void main')),
  vsHead: a.vertexShader.slice(0, a.vertexShader.indexOf('void main')),
}));
""")
    assert out["same"], "one seed, one shader"
    assert out["seedA"] == out["seedB"]
    # A seed must MOVE the lattice, not shift it by a cell — a one-cell
    # offset is the same pool drawn twice.
    assert abs(out["seed8"]["x"] - out["seedA"]["x"]) > 5, out["seed8"]
    assert out["seedA"] != out["iceSeed"] or out["seedA"]["x"] > 0
    assert out["ringsSame"] and out["ringsDiffer"]
    # One call reaches the patched material AND the ring field.
    assert out["ticked"] == 2, out["ticked"]
    assert out["matTime"] == 7.5 and out["ringTime"] == 7.5
    assert out["updateTime"] == 2.25, "its own update still works"
    # Nothing of these patches reaches the vertex stage but the world
    # position and the uv: fwidth there is only GLSL_UTIL's, behind
    # ASTRA_FRAG, and the crack helpers are fragment work.
    assert "fwidth" not in out["vsBody"]
    assert "astraIceCracks" not in out["vsHead"]
    assert "astraIceRay" not in out["vsHead"]

    src = _LIB.read_text(encoding="utf-8")
    assert "Math.random" not in src
    assert "Date.now" not in src
    assert src.count("\nexport ") == 3
    for name in ("patchUnderwater", "makeRainRings", "patchThinIce"):
        assert "export function " + name in src
    assert max(len(ln) for ln in src.splitlines()) <= 80
