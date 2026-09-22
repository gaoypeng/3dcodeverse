"""aging.js: the three weather marks, and the chain they land in.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_aging_lib.py).  Their renderer-contract assertions are dropped;
the physics claims, the shared-name contract and the option-is-a-uniform law
are kept, and the tone structure this port added (a rim/body/core triple on
the runs, three tones on the rust, two on the dust) is pinned here so a later
edit cannot quietly flatten an effect back to one colour.

These patches exist to go ON TOP of something — a wall that already wears
`patchMicroBreakup` and `patchEdgeWear`, a bank that already wears a triplanar
and a waterline.  `patchStandard` absorbs a duplicate uniform SILENTLY (the
second declaration is dropped and that patch then reads whatever its neighbour
set) while a duplicate local inside main is a compile error, so the shared
contract is asserted first: one world base, no name any neighbour owns, every
option a uniform rather than baked GLSL.  (The names, and the whole stack on
a GPU, are asserted with every sibling library's in test_patch_union.py.)
"""
from __future__ import annotations

import pytest
from _probe import LIB_DIR, SHADER_JS, _find, _main_body, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "surface_wear.js", "aging.js")

_LIB_SRC = (LIB_DIR / "aging.js").read_text(encoding="utf-8")

# The patch only exists inside onBeforeCompile, so every probe hands it
# the two chunks patchStandard replaces and reads back what it wrote.
_PRELUDE = SHADER_JS + """
import * as THREE from 'three';
import { patchDripStains, patchRust, patchDust } from './lib/aging.js';
import { patchMicroBreakup } from './lib/surface_wear.js';

const std = (o = {}) => new THREE.MeshStandardMaterial(
    Object.assign({ color: 0x8b8478, roughness: 0.7 }, o));
"""


def _probe(body: str) -> dict:
    return measure(_PRELUDE + body, _LIBS)


def test_the_three_patches_chain_without_losing_each_other():
    """All three on one material is the ordinary case — a wall that streaks but
    never dusts is half a wall.  patchStandard chains them, but it repeats
    whatever it is handed: the shared world base and its three helpers must be
    emitted ONCE (a second function body is a compile error), the base must run
    FIRST or every fragment body reads a varying nobody wrote, and re-applying
    a patch has to retune its uniforms instead of injecting a second copy."""
    out = _probe("""
const m = std();
patchDripStains(m, { strength: 0.2 });
patchRust(m, { strength: 0.5 });
patchDust(m, { strength: 0.1 });
patchDripStains(m, { strength: 0.44 });
const s = compile(m);
const dripOnly = std();
patchDripStains(dripOnly);
console.log(JSON.stringify({
  key: m.customProgramCacheKey(), dripKey: dripOnly.customProgramCacheKey(),
  drip: s.fragmentShader.includes('mix(diffuseColor.rgb, drDirt, drAmt)'),
  rust: s.fragmentShader.includes('mix(diffuseColor.rgb, rsCol, rsAmt)'),
  dust: s.fragmentShader.includes('duK * uDustAmt'),
  dripAmt: m.userData.uniforms.uDripAmt.value,
  rustAmt: m.userData.uniforms.uRustAmt.value,
  dustAmt: m.userData.uniforms.uDustAmt.value,
  worldBody: count(s.vertexShader, 'vec4 agP ='),
  axesFn: count(s.fragmentShader, 'vec3 astraAgeAxes(vec3 n) {'),
  streakFn: count(s.fragmentShader, 'float astraAgeStreak('),
  curvFn: count(s.fragmentShader, 'float astraAgeCurv('),
  dripLine: count(s.fragmentShader, 'float drF ='),
  varyVs: count(s.vertexShader, 'varying vec3 vAstraWorld;'),
  varyFs: count(s.fragmentShader, 'varying vec3 vAstraWorldN;'),
}));
""")
    assert out["drip"] and out["rust"] and out["dust"], "a patch was lost"
    assert out["worldBody"] == 1 and out["axesFn"] == 1
    assert out["streakFn"] == 1 and out["curvFn"] == 1
    assert out["varyVs"] == 1 and out["varyFs"] == 1
    # Every option is a uniform, so re-applying retunes in place.
    assert out["dripLine"] == 1 and out["dripAmt"] == 0.44
    assert out["rustAmt"] == 0.5 and out["dustAmt"] == 0.1
    assert out["key"] == ("astra:aging:base+aging:drip+aging:rust"
                          "+aging:dust"), out["key"]
    # A longer chain must not collide with a shorter one's program.
    assert out["dripKey"] == "astra:aging:base+aging:drip"


def test_the_runs_come_down_world_y_whatever_the_surface_is_doing():
    """The whole claim of the drip patch.  A planar UV would streak a wall and
    smear a pipe; an object-space axis would tilt with the mesh.  So the field
    is projected on the three WORLD planes, and the two upright projections
    both take world Y as their second axis — compressed by a lift, which is
    what makes a feature many times taller than it is wide.  The projection a
    fragment gets is chosen by the world NORMAL, so orientation decides WHICH
    plane, and never which way is down.  Nothing here may read a UV."""
    out = _probe("""
const m = std();
patchDripStains(m, { scale: 0.6 });
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    # The vertical axis of both upright projections is p.y — world Y over
    # `scale` — and the up-facing one gets no lift at all.
    _find(r"float astraAgeStreak\(vec3 p, vec3 w, float lift\) \{\s*"
          r"return astraNoise2\(vec2\(p\.z, p\.y \* lift\) \+ [\d.]+\)"
          r" \* w\.x\s*"
          r"\+ astraNoise2\(p\.xz \+ [\d.]+\) \* w\.y\s*"
          r"\+ astraNoise2\(vec2\(p\.x, p\.y \* lift\) \+ [\d.]+\)"
          r" \* w\.z;", fs)
    body = _main_body(fs)
    # World position over the scale, and weights from the world normal: a
    # leaning panel picks a different plane, not a different down.
    _find(r"vec3 drP = vAstraWorld / uDripScale \+ uDripSeed;", body)
    _find(r"vec3 drN = normalize\(vAstraWorldN\);", body)
    _find(r"vec3 drW = astraAgeAxes\(drN\);", body)
    lift = _find(r"float drA = astraAgeStreak\(drP, drW, ([\d.]+)\);", body)
    assert 0.02 <= float(lift.group(1)) <= 0.2, "the runs are not stretched"
    # The drag sample sits HIGHER in world Y and is faded in, which is what
    # tapers a run downward instead of ending it in a line.
    drag = _find(r"float drB = astraAgeStreak\(drP \+ vec3\(0\.0, ([\d.]+),"
                 r" 0\.0\), drW, [\d.]+\);", body)
    assert float(drag.group(1)) > 0
    _find(r"float drF = max\(drA, drB \* 0\.\d+\)", body)
    # Only faces that can shed take a run: a level top and a level soffit
    # both get nothing.
    _find(r"float drFlow = 1\.0 - smoothstep\([\d.]+, [\d.]+,"
          r" abs\(drN\.y\)\);", body)
    assert "vUv" not in body and "uv" not in body


def test_a_run_is_a_pale_rim_around_a_dark_core_not_one_smudge():
    """What this port changed, and the reason the runs read at all.  Three
    cuts on one field, not two: the outer rim takes the surface's OWN albedo
    lifted (rain leaches a wall before it dirties it, and a colour named here
    would belong to no wall in particular), the body and the core take the
    grime.  The rim must be the band the body does NOT cover, or the two mixes
    land on the same pixels and cancel — which is exactly what the first
    attempt did, and it lost contrast instead of gaining it.

    The fwidth cap is the other half: it is a PIXEL guard, and at the old 0.5
    it swallowed both threshold bands on any grazing surface and turned every
    run into an airbrushed smear."""
    out = _probe("""
const m = std();
patchDripStains(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    body = _main_body(out["fs"])
    aa = _find(r"float drAA = clamp\(fwidth\(drF\), 0\.0, ([\d.]+)\);", body)
    assert float(aa.group(1)) <= 0.10, "the AA cap can swallow a band whole"
    lo = _find(r"float drWash = smoothstep\(([\d.]+) - drAA,"
               r" ([\d.]+) \+ drAA, drF\);", body)
    mid = _find(r"float drBody = smoothstep\(([\d.]+) - drAA,"
                r" ([\d.]+) \+ drAA, drF\);", body)
    hi = _find(r"float drCore = smoothstep\(([\d.]+) - drAA,"
               r" ([\d.]+) \+ drAA, drF\);", body)
    # Three bands stacked, each strictly inside the last.
    assert (float(lo.group(1)) < float(mid.group(1)) < float(hi.group(1))
            and float(lo.group(2)) <= float(mid.group(1))
            and float(mid.group(2)) <= float(hi.group(1)))
    # The rim is the wash MINUS the body — the frame, never the fill.
    _find(r"float drRim = drWash \* \(1\.0 - drBody\);", body)
    # The pale side is the surface's own colour lifted, not a colour named
    # in this file: it has to belong to whatever it is running on.
    salt = _find(r"vec3 drSalt = diffuseColor\.rgb \* ([\d.]+)"
                 r" \+ ([\d.]+);", body)
    assert float(salt.group(1)) > 1.0 and float(salt.group(2)) > 0
    _find(r"mix\(diffuseColor\.rgb, drSalt,\s*clamp\(drRim \* [\d.]+"
          r" \* drG, 0\.0, 1\.0\)\)", body)
    # ...and the dark side is the grime, hue-broken so two runs off one
    # sill are never the same brown.
    _find(r"vec3 drDirt = astraHueBreak\(uDripColor,", body)
    _find(r"float drAmt = clamp\(\([\d.]+ \* drBody \+ [\d.]+ \* drCore\)"
          r" \* drG,\s*0\.0, 1\.0\);", body)
    _find(r"mix\(diffuseColor\.rgb, drDirt, drAmt\);", body)


def test_a_sill_puts_the_runs_under_an_edge_instead_of_everywhere():
    """Water comes over a lip; it does not appear evenly down a facade.  A
    fragment cannot see the surface above it, so the lip is `from`, a world Y —
    and the gate is a WORLD Y DIFFERENCE, so it holds on a wall, a pipe and a
    leaning panel alike.  Each run starts a little lower than its neighbour and
    dies at its own length, both from the field, so no ruled line appears
    across the wall.  Omitting `from` must not bake a different shader: it is
    the same GLSL with the gate uniform at zero."""
    out = _probe("""
const sill = std();
patchDripStains(sill, { from: 3.8 });
const open = std();
patchDripStains(open, { scale: 2.5, strength: 0.9 });
const sa = compile(sill), sb = compile(open);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  sameKey: sill.customProgramCacheKey() === open.customProgramCacheKey(),
  sameFs: sa.fragmentShader === sb.fragmentShader,
  fs: sa.fragmentShader,
  sillFrom: u(sill).uDripFrom.value, sillGate: u(sill).uDripGate.value,
  openFrom: u(open).uDripFrom.value, openGate: u(open).uDripGate.value,
  openScale: u(open).uDripScale.value, openAmt: u(open).uDripAmt.value,
}));
""")
    assert out["sameKey"] and out["sameFs"], "the sill was baked in"
    assert out["sillFrom"] == 3.8 and out["sillGate"] == 1
    assert out["openGate"] == 0, "no sill must not gate"
    assert out["openScale"] == 2.5 and out["openAmt"] == 0.9
    body = _main_body(out["fs"])
    # Metres BELOW the sill, jittered per run so the tops stagger.
    height = _find(r"float (\w+) = uDripFrom - vAstraWorld\.y"
                   r" - drB \* uDripScale;", body)
    length = _find(r"float (\w+) = mix\([\d.]+, [\d.]+, drA\)"
                   r" \* uDripScale;", body)
    gate = _find(rf"float (\w+) = smoothstep\(0\.0, uDripScale \* [\d.]+,"
                 rf" {height.group(1)}\)\s*"
                 rf"\* \(1\.0 - smoothstep\({length.group(1)} \* [\d.]+,"
                 rf" {length.group(1)}, {height.group(1)}\)\);", body)
    # mix, not an if: the ungated case runs the same instructions, and the
    # sill, the shedding test and the strength ride ONE factor so the rim
    # and the grime cannot drift apart.
    _find(rf"float drG = mix\(1\.0, {gate.group(1)}, uDripGate\)"
          r" \* drFlow \* uDripAmt;", body)


def test_rust_needs_the_water_to_sit_somewhere_and_bleeds_down():
    """Corrosion sprayed evenly is a red object.  It has to start where water
    cannot leave — what faces up, and the concave lee a shape shelters — and
    then continue DOWN the face below.  The bleed is the map being read from
    world XZ only: a vertical column of surface shares one value, so what eats
    a rim goes on eating the side under it, which is the one way a fragment
    that cannot see upward knows what drained onto it.  The lee is the SIGNED
    curvature: the convex half is where wear happens, not where water sits."""
    out = _probe("""
const m = std();
patchRust(m, { scale: 1.2 });
console.log(JSON.stringify({
  fs: compile(m).fragmentShader,
  scale: m.userData.uniforms.uRustScale.value,
  color: m.userData.uniforms.uRustColor.value.getHex(),
}));
""")
    fs = out["fs"]
    body = _main_body(fs)
    # The patch map: world XZ, no Y at all.
    field = _find(r"float (\w+) = astraFbm2\((\w+)\.xz, \d\);", body)
    _find(rf"vec3 {field.group(2)} = vAstraWorld / uRustScale"
          r" \+ uRustSeed;", body)
    # Signed curvature from the surface's own derivatives, and only the
    # CONCAVE half counts.
    curv = _find(r"float (\w+) = astraAgeCurv\(rsN, vAstraWorld\);", body)
    _find(r"float astraAgeCurv\(vec3 n, vec3 p\) \{\s*"
          r"vec3 dx = dFdx\(p\), dy = dFdy\(p\);", fs)
    radius = _find(r"float (\w+) = 1\.0 / uRustScale;", body)
    lee = _find(rf"float (\w+) = smoothstep\({radius.group(1)} \* [\d.]+,"
                rf" {radius.group(1)} \* [\d.]+, -{curv.group(1)}\);", body)
    # Pooling: up-facing plus that lee, together.
    sit = _find(rf"float (\w+) = clamp\(smoothstep\(-?[\d.]+, [\d.]+,"
                rf" rsN\.y\)\s*\+ {lee.group(1)} \* [\d.]+, 0\.0, 1\.0\);",
                body)
    # The run: on the faces that shed, ended by a field whose vertical
    # period is metres and which is dragged downward.
    _find(r"vec3 (\w+) = vec3\(rsP\.x, vAstraWorld\.y \* [\d.]+,"
          r" rsP\.z\) \* [\d.]+;", body)
    _find(r"float rsH = astraAgeStreak\(rsQ \+ vec3\(0\.0, [\d.]+, 0\.0\),"
          r" rsA, [\d.]+\);", body)
    _find(r"float rsB = max\(rsG, rsH \* 0\.\d+\);", body)
    down = _find(r"float (\w+) = 1\.0 - smoothstep\([\d.]+, [\d.]+,"
                 r" abs\(rsN\.y\)\);", body)
    run = _find(rf"float (\w+) = {down.group(1)}\s*"
                r"\* \([\d.]+ \+ [\d.]+ \* smoothstep\([\d.]+, [\d.]+,"
                r" rsB\)\);", body)
    _find(rf"clamp\({sit.group(1)} \+ {run.group(1)}, 0\.0, 1\.0\)", body)
    assert out["scale"] == 1.2
    # An iron oxide: red-dominant, not a bright orange — and with a blue
    # channel it can actually use. 0x70320f put blue at 0.005 linear, so
    # the pit tone under it went to black and the patch read as a bruise.
    r, g, b = (out["color"] >> 16, (out["color"] >> 8) & 255,
               out["color"] & 255)
    assert r > g > b and 0x50 <= r <= 0xa0, hex(out["color"])
    assert b >= 0x20, hex(out["color"])


def test_rust_is_three_tones_graded_by_the_field_not_one_flat_orange():
    """What this port changed on the rust.  A single colour scaled 0.45..1.05
    gives a small subject — a 2.6 m tank, a pipe — one dark bruise: the tone
    has no hue anywhere, so on anything cool-coloured it reads as a shadow.
    Corrosion is a stack, so three tones ride the streak field: a near-black
    pitted crust in its lee, the oxide through the body, an ochre bloom where
    the crust is thinnest.  All three are derived from the one `color` option,
    so a caller still tunes it with one value."""
    out = _probe("""
const m = std();
patchRust(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    body = _main_body(out["fs"])
    # Hue-broken across the patch: not one brown for the whole tank.
    _find(r"vec3 rsBase = astraHueBreak\(uRustColor, rsP\.xz,"
          r" [\d.]+, [\d.]+\);", body)
    pit = _find(r"vec3 rsPit = rsBase \* vec3\(([\d.]+), ([\d.]+),"
                r" ([\d.]+)\);", body)
    glow = _find(r"vec3 rsGlow = min\(rsBase \* vec3\(([\d.]+), ([\d.]+),"
                 r" ([\d.]+)\), vec3\(([\d.]+)\)\);", body)
    # The pit is darker than the oxide, the bloom lighter, and the bloom
    # is warmer than the oxide — it lifts green more than red, which is
    # what turns an iron red into an ochre rather than a pink.
    assert all(float(pit.group(i)) < 1.0 for i in (1, 2, 3))
    assert all(float(glow.group(i)) > 1.0 for i in (1, 2, 3))
    assert float(glow.group(2)) > float(glow.group(1))
    # ...and it stays a plausible albedo: nothing here is emissive.
    assert float(glow.group(4)) <= 0.85
    # Graded ACROSS the patch by the streak field, not confined to the
    # boundary — the boundary of a small patch is a few pixels wide.
    _find(r"vec3 rsCol = mix\(rsPit, rsBase, smoothstep\([\d.]+, [\d.]+,"
          r" rsB\)\);", body)
    _find(r"rsCol = mix\(rsCol, rsGlow, smoothstep\([\d.]+, [\d.]+, rsB\)"
          r" \* [\d.]+\s*\+ clamp\(rsK \* \(1\.0 - rsK\) \* [\d.]+,"
          r" 0\.0, 1\.0\) \* [\d.]+\);", body)
    _find(r"diffuseColor\.rgb = mix\(diffuseColor\.rgb, rsCol, rsAmt\);",
          body)


def test_dust_settles_along_the_up_vector_it_was_given():
    """Dust is gravity, and gravity is an option here: a listing hull, a tipped
    crate or a scene authored Z-up dusts the faces that actually point up, not
    the ones with a positive world Y.  So `up` is a UNIFORM — two materials
    with different ups share one compiled program — the film is a cosine
    against it, and a degenerate vector falls back to +Y rather than painting
    NaN over the object."""
    out = _probe("""
const yUp = std();
patchDust(yUp);
const zUp = std();
patchDust(zUp, { up: new THREE.Vector3(0, 0, 4), strength: 0.9 });
const arrUp = std();
patchDust(arrUp, { up: [3, 4, 0] });
const dead = std();
patchDust(dead, { up: [0, 0, 0] });
const sa = compile(yUp), sb = compile(zUp);
const u = (m) => m.userData.uniforms.uDustUp.value.toArray();
console.log(JSON.stringify({
  sameKey: yUp.customProgramCacheKey() === zUp.customProgramCacheKey(),
  sameFs: sa.fragmentShader === sb.fragmentShader,
  y: u(yUp), z: u(zUp), arr: u(arrUp), dead: u(dead),
  amt: zUp.userData.uniforms.uDustAmt.value,
  fs: sa.fragmentShader,
}));
""")
    assert out["sameKey"] and out["sameFs"], "the up vector was baked in"
    assert out["y"] == [0, 1, 0]
    assert out["z"] == [0, 0, 1], "an unnormalised up must be normalised"
    assert [round(v, 4) for v in out["arr"]] == [0.6, 0.8, 0]
    assert out["dead"] == [0, 1, 0], "a zero up must fall back, not NaN"
    assert out["amt"] == 0.9
    body = _main_body(out["fs"])
    lay = _find(r"float (\w+) = smoothstep\([\d.]+, [\d.]+,\s*"
                r"dot\(duN, normalize\(uDustUp\)\)\);", body)
    # Swept off what sticks out, held in what is sheltered — the same
    # signed curvature, read the other way round.
    hold = _find(r"float (\w+) = 1\.0 - [\d.]+ \* smoothstep\([\d.]+,"
                 r" [\d.]+, duC\)\s*\+ [\d.]+ \* smoothstep\([\d.]+,"
                 r" [\d.]+, -duC\);", body)
    _find(rf"float (\w+) = clamp\({lay.group(1)} \* {hold.group(1)}", body)
    _find(r"mix\(diffuseColor\.rgb, duCol,\s*duK \* uDustAmt\)", body)


def test_the_dust_film_is_two_tones_at_one_value():
    """What this port changed on the dust.  One grey mixed over every upward
    face is a coat of paint: the coarse grit that fell out first is warm, the
    fine powder the sky keeps lighting is cool, and they split on the same
    field that already sets the coverage.  The two tones must sit at the SAME
    value, so the film still lightens a surface evenly and only the hue moves
    under it — a warm/cool pair with a value difference would read as a stain
    instead of a settling."""
    out = _probe("""
const m = std();
patchDust(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    body = _main_body(out["fs"])
    _find(r"vec3 duCol = astraHueBreak\(uDustColor, duP\.xz,"
          r" [\d.]+, [\d.]+\);", body)
    pair = _find(r"duCol = mix\(duCol \* vec3\(([\d.]+), ([\d.]+), ([\d.]+)\),"
                 r"\s*duCol \* vec3\(([\d.]+), ([\d.]+), ([\d.]+)\),"
                 r"\s*smoothstep\([\d.]+, [\d.]+, duB\)\);", body)
    veil = [float(pair.group(i)) for i in (1, 2, 3)]
    drift = [float(pair.group(i)) for i in (4, 5, 6)]
    # The veil is the cool one, the drift the warm one.
    assert veil[2] > veil[0] and drift[0] > drift[2]
    # Same value: Rec.709 luminance of the two multipliers within 3%.
    def lum(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    assert abs(lum(veil) - lum(drift)) < 0.03, (veil, drift)
    # A pale settled film, never a dark one: the option is an albedo.
    assert all(0.7 <= v <= 1.3 for v in veil + drift)


def test_an_option_is_a_uniform_and_never_baked_into_the_source():
    """three caches programs by key and the FIRST material to compile a key
    decides the GLSL every material sharing it gets.  Two materials that differ
    only in options must therefore compile to the same source, or the second
    silently wears the first's settings — for these three that would mean one
    building's sill height, patch size and dust colour imposed on every other.
    Same key, byte-identical GLSL, different uniform values."""
    out = _probe("""
const a = std();
patchDripStains(a, { strength: 0.35, scale: 0.6, seed: 1 });
patchRust(a, { strength: 0.4, scale: 1.2, seed: 1 });
patchDust(a, { strength: 0.3, seed: 1 });
const b = std();
patchDripStains(b, { strength: 0.9, scale: 4, from: -12.5, seed: 99,
                     color: new THREE.Color(0x112233) });
patchRust(b, { strength: 0.05, scale: 0.2, seed: 99,
               color: new THREE.Color(0x445566) });
patchDust(b, { strength: 0.8, seed: 99, up: [0, -1, 0],
               color: new THREE.Color(0x778899) });
const sa = compile(a), sb = compile(b);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  sameKey: a.customProgramCacheKey() === b.customProgramCacheKey(),
  sameVs: sa.vertexShader === sb.vertexShader,
  sameFs: sa.fragmentShader === sb.fragmentShader,
  aDrip: [u(a).uDripAmt.value, u(a).uDripScale.value, u(a).uDripFrom.value],
  bDrip: [u(b).uDripAmt.value, u(b).uDripScale.value, u(b).uDripFrom.value],
  aRust: [u(a).uRustAmt.value, u(a).uRustScale.value],
  bRust: [u(b).uRustAmt.value, u(b).uRustScale.value],
  aDust: [u(a).uDustAmt.value, u(a).uDustUp.value.toArray()],
  bDust: [u(b).uDustAmt.value, u(b).uDustUp.value.toArray()],
  colors: [u(b).uDripColor.value.getHex(), u(b).uRustColor.value.getHex(),
           u(b).uDustColor.value.getHex()],
}));
""")
    assert out["sameKey"] and out["sameVs"] and out["sameFs"]
    assert out["aDrip"] == [0.35, 0.6, 0] and out["bDrip"] == [0.9, 4, -12.5]
    assert out["aRust"] == [0.4, 1.2] and out["bRust"] == [0.05, 0.2]
    assert out["aDust"] == [0.3, [0, 1, 0]]
    assert out["bDust"] == [0.8, [0, -1, 0]]
    assert out["colors"] == [0x112233, 0x445566, 0x778899]


def test_one_seed_lays_the_same_weather_every_time():
    """A render is re-run — for a fix round, for a video, for the judge — and
    the weather must not move between takes.  The seed reaches the GPU only as
    a noise-space OFFSET (a uniform, because baking it would hand material
    one's seed to every material sharing the key), so one seed must give one
    set of offsets, two seeds must not land on the same patches, and the three
    effects must be decorrelated from each other or every run would come down
    the middle of a rust patch.  The offsets must also differ from
    surface_wear's for the same seed, or the stains would ride that library's
    blotches."""
    out = _probe("""
const mk = (seed) => {
  const m = std();
  patchDripStains(m, { seed });
  patchRust(m, { seed });
  patchDust(m, { seed });
  patchMicroBreakup(m, { seed });
  return m;
};
const a = mk(7), b = mk(7), c = mk(8);
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  aDrip: u(a).uDripSeed.value.toArray(),
  bDrip: u(b).uDripSeed.value.toArray(),
  cDrip: u(c).uDripSeed.value.toArray(),
  aRust: u(a).uRustSeed.value.toArray(),
  cRust: u(c).uRustSeed.value.toArray(),
  aDust: u(a).uDustSeed.value.toArray(),
  aMicro: u(a).uMicroSeed.value.toArray(),
  sameSrc: compile(a).fragmentShader === compile(c).fragmentShader,
}));
""")
    assert out["aDrip"] == out["bDrip"], "the same seed moved the runs"
    assert out["aRust"] != out["cRust"] and out["aDrip"] != out["cDrip"]
    assert out["aDrip"] != out["aRust"] != out["aDust"] != out["aDrip"]
    assert out["aDrip"] != out["aMicro"], "stains ride the breakup's blobs"
    assert all(0 <= v <= 48 for v in out["aDrip"] + out["aRust"])
    # Same GLSL for both seeds: the seed is a uniform, not source.
    assert out["sameSrc"]
    assert "Math.random" not in _LIB_SRC


def test_every_effect_is_matte_and_composes_on_the_material():
    """`fragmentBody` lands after <color_fragment> and therefore BEFORE
    <roughnessmap_fragment> declares roughnessFactor, so no patch here can
    touch a per-pixel gloss.  Dirt, rust and dust are all matte, so each RAISES
    the material's roughness — through composeRoughness, so the three own one
    factor each and a refine round that re-runs the same build cannot compound
    them into a chalk.  A factor is also capped at what leaves the surface
    fully rough, since roughness means nothing past 1.

    The factors this port raised: at the old 0.22 a fully rusted steel tank
    moved from roughness 0.45 to 0.50 and went on mirroring the sky, which
    washed the oxide out of the frame.  A crust is matte, so a full-strength
    rust now buys real roughness."""
    out = _probe("""
const m = std({ roughness: 0.5 });
patchDust(m, { strength: 1 });
const r1 = m.roughness;
patchRust(m, { strength: 1 });
const r2 = m.roughness;
patchRust(m, { strength: 1 });
const r3 = m.roughness;
patchRust(m, { strength: 0 });
const r4 = m.roughness;
const hard = std({ roughness: 0.95 });
patchDust(hard, { strength: 1 });
const tank = std({ roughness: 0.45, metalness: 0.25 });
patchRust(tank, { strength: 1 });
const basic = new THREE.MeshBasicMaterial({ color: 0x445566 });
patchDripStains(basic);
patchDust(basic);
console.log(JSON.stringify({
  r1, r2, r3, r4, base: m.userData.astraRoughness.base,
  factors: Object.keys(m.userData.astraRoughness.factors).sort(),
  hard: hard.roughness, tank: tank.roughness,
  basicRough: basic.roughness === undefined,
  basicPatched: !!basic.userData.astraPatches.length,
}));
""")
    # Every patch raises roughness and none of them re-bases the others.
    assert out["base"] == 0.5
    assert out["r1"] > 0.5 and out["r2"] > out["r1"]
    assert out["r3"] == out["r2"], "re-applying compounded the roughening"
    assert out["r4"] == out["r1"], "strength 0 must remove the factor"
    assert out["factors"] == ["aging:dust", "aging:rust"]
    # A surface that already starts rough cannot be pushed past 1.
    assert out["hard"] == pytest.approx(1.0)
    # ...and a semi-gloss tank goes properly matte under full corrosion.
    assert out["tank"] >= 0.7, out["tank"]
    # A material with no roughness at all is patched and left alone.
    assert out["basicRough"] and out["basicPatched"]
