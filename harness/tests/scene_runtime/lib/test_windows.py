"""windows.js: a lit window has to be a ROOM, or a night city is decals.

Ported 2026-09-01 from the scene_multifile_graphics reference
(tests/test_windows_lib.py).  Their laws are kept as they stood — the pane
is a shallow box traced from the view direction, the tangent frame comes
from screen derivatives taken in UNIFORM control flow, a cell that is not
window-sized keeps its own surface, a lit room lifts radiance and not only
albedo, an unlit cell is dark glass and never a hole, every tunable is a
uniform so one patch kind compiles once, the pattern is seeded and still,
and the convenience finds the facade slot without patching the shared
material `materials.js` handed the rest of the city.

THE PORT'S OWN LAWS — each one a frame rendered on our host at
fx/out/windows/, looked at, and measured:

1. THE CELL ID MUST LIVE ON A WHOLE-NUMBER LATTICE.  `vWinKey` is one
   constant handed to every vertex, but a perspective-correct varying is
   (sum w/q)/(sum 1/q) and comes back a few ULPs apart per pixel.  The
   original hashed that raw key into `winB` and then hashed `winB` into
   the cell id: two chained astraHash21 calls, each amplifying an input
   wobble by about a thousand (measured in float32: one ULP at coordinate
   120 moves the hash 0.008).  On this GPU that turned `winOn` into a
   per-pixel coin flip and every facade rendered as STATIC — measured over
   the pane pixels of fx/out/windows/before/close_t1p5.png, mean
   |laplacian| 0.1584 with 19.4% of pixels past 0.06.  Rounding the key,
   the face weight and the hash draw to integers makes the id bit-exact
   per cell: 0.0102 and 3.8% after, a 15.5x drop, and the same hash on an
   integer lattice inside +-700 is still well spread (mean 0.504, sigma
   0.291, adjacent |dh| 0.333).

2. AN UNLIT PANE IS RADIANCE, NOT ALBEDO.  The reflected sky was written
   to `diffuseColor` only, where it is multiplied by the light landing on
   the wall — and at night that is nothing, so every dark pane collapsed
   to the hole in the facade the term exists to prevent (7.3% of the close
   facade crop under luminance 0.045, p05 0.027).  Adding it to
   `totalEmissiveRadiance` as well puts it at 0.9% and p05 0.103, with
   blown_frac still 0.

3. THE SKY A PANE MIRRORS IS THE SCENE'S.  It was the `cool` uniform flat,
   so a facade reflected the same pale blue at midnight as at noon.  It
   now reads `fogColor` where the scene has fog — the horizon's own hue —
   and splits at the reflected ray's horizon, so glass takes the darker
   ground below and the sky above.  `cool` stays as the floor for a scene
   with no fog.

4. HUE AND BRIGHTNESS VARIANCE INSIDE THE EFFECT.  A warm/cool lerp gives
   a facade exactly two bulbs and one level.  Each cell now draws its own
   small hue rotation, saturation and brightness, and about a third are
   dressed with a blind or a drawn curtain.  Measured over the lit pixels
   of the close crop, circular hue spread 0.597 -> 0.901.

5. NO RAZOR LINE ACROSS A PANE.  The blind was a hard `step` at one height
   on every floor of the building, which is the tell of a decal; it is a
   smoothstep hem now, and the shallow room ramps carry an LSB of hash
   grain against 8-bit banding.
"""

from __future__ import annotations

from tests.scene_runtime.lib._probe import LIB_DIR, SHADER_JS, compile_scene, measure

_LIBS = ("shader.js", "windows.js")
_LIB = LIB_DIR / "windows.js"

# A fresh window-interior patch, run through the two hooks of SHADER_JS.
_PATCHED = """
const patched = (opts) => {
  const sh = fake();
  patchWindowInteriors(new THREE.MeshStandardMaterial(), opts || {})
      .onBeforeCompile(sh);
  return sh;
};
"""

_PRELUDE = """
import * as THREE from 'three';
import { patchWindowInteriors, makeNightWindows } from './lib/windows.js';
""" + SHADER_JS + _PATCHED


def _measure(script: str, libs: tuple[str, ...] = _LIBS) -> dict:
    return measure(_PRELUDE + script, libs)


def test_the_window_is_a_box_interior_traced_from_the_view_direction():
    """The whole point: a ray from the surface point into a shallow box per
    cell, shaded where it lands, so the room slides with the camera.  The
    frame it is traced in comes from the screen derivatives because a
    facade is any box face at any orientation and carries no tangent — and
    the derivatives are taken in UNIFORM control flow, since dFdx inside a
    branch is undefined and would tear the frame per pixel."""
    out = _measure("""
const sh = patched();
console.log(JSON.stringify({ fs: sh.fragmentShader, vs: sh.vertexShader }));
""")
    fs = out["fs"]
    # The slab test itself: two walls, one floor/ceiling pair, and a back
    # wall `depth` behind the glass — the nearest is what is seen.
    assert "vec3 astraWinRoom(vec3 o, vec3 d, float depth, out vec3 face)" in fs
    assert "vec3 tHi = (vec3(1.0, 1.0, 0.0) - o) / ds;" in fs
    assert "vec3 tLo = (vec3(0.0, 0.0, -depth) - o) / ds;" in fs
    assert "float t = min(min(tm.x, tm.y), tm.z);" in fs
    assert "face = step(tm, vec3(t));" in fs
    # The direction is the VIEW ray, in the surface's own frame.
    assert "vec3 winV = normalize(cameraPosition - vWinW);" in fs
    assert "mat3 winTbn = astraWinFrame(vWinW, vWinUv, winNn, winMpu);" in fs
    assert "dot(winV, winTbn[0])" in fs
    assert "vec3 dp1 = dFdx(p), dp2 = dFdy(p);" in fs
    # Which wall was hit decides the shade: back wall full, side walls
    # dimmer, ceiling lit and floor dark.
    assert "float winShade = winHitFace.z + winHitFace.x * 0.45" in fs
    assert "mix(0.22, 0.8, step(0.5, winHit.y))" in fs
    # No branch anywhere: the pane is a mask mixed in at the end, so every
    # derivative above is taken with the whole quad in step.
    assert "discard" not in fs
    assert "if (" not in fs.split("void main")[-1]
    # The grid is measured in UV and the room is one cell wide.
    assert "vec2 winG = vWinUv * uWinGrid;" in fs
    assert "const vec2 ASTRA_WIN_EDGE" in fs


def test_a_cell_that_is_not_window_sized_keeps_its_own_surface():
    """three's BoxGeometry gives EVERY box face a full 0..1 UV whatever its
    size, so on a facade merged from many boxes — which is what `block()`
    and `tower()` are — a UV grid paints the whole window pattern onto each
    0.34 m mullion and each spandrel band.  The shader can tell the
    difference only by metres per unit of UV, which the tangent frame
    already has to compute; under about a metre per cell the member is
    handed back untouched.  The same goes once a pixel spans a third of a
    cell: past that the room can only alias into sparkle."""
    out = _measure("""
const sh = patched();
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    assert "float det = max(abs(du1.x * du2.y - du2.x * du1.y), 1e-12);" in fs
    assert "mpu = vec2(length(dp1 * du2.y - dp2 * du1.y)," in fs
    assert "length(dp2 * du1.x - dp1 * du2.x)) / det;" in fs
    assert "winPane *= smoothstep(0.55, 1.0, min(winMpu.x / uWinGrid.x," in fs
    assert "winPane *= 1.0 - smoothstep(0.3, 0.8, max(winAa.x, winAa.y));" in fs


def test_the_cell_id_is_a_whole_number_lattice_or_the_facade_is_static():
    """PORT LAW 1, and the reason this module did not ship as it arrived.

    `vWinKey` is a constant handed to every vertex of the draw, but a
    perspective-correct varying is (sum w/q)/(sum 1/q) and comes back a few
    ULPs apart per pixel.  astraHash21 amplifies an input wobble by about a
    thousand, so hashing the raw key into `winB` and then hashing `winB`
    into the cell id chains two amplifications and `winOn` — a step() on
    that hash — becomes a per-pixel coin flip.  It rendered as static on
    this GPU.  Every term of the id is therefore rounded: the key, the face
    weight, and the hash draw off the key.  Nothing here is cosmetic; drop
    one floor() and the facade fizzes again."""
    out = _measure("""
const sh = patched();
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    assert "vec3 winKq = floor(vWinKey * 16.0 + 0.5);" in fs
    assert ("float winFace = dot(floor(winNn * 4.0 + 0.5),"
            " vec3(11.0, 29.0, 53.0));") in fs
    assert ("float winB = floor(astraHash21(winKq.xz + winKq.y) * 29.0)"
            " + winFace;") in fs
    assert "vec2 winId = floor(winG)" in fs
    assert "vec2(uWinSeed + winB, floor(uWinSeed * 1.7) - winB);" in fs
    # The furniture noise enters its lattice through the cell HASHES, not
    # through the id, so its coordinate stays small enough to interpolate.
    assert "vec2 winNz = winHit.xy * 3.0 + vec2(winHt, winHl) * 37.0;" in fs
    assert "winShade *= mix(0.55, 1.0, astraNoise2(winNz));" in fs


def test_the_seed_offset_is_a_far_apart_whole_number():
    """The seed rides the same lattice, so it has to be an integer too — a
    fractional offset puts every cell id between two lattice points, where
    the hash is far more sensitive to the last bit of its inputs than to
    the cell.  And it is hashed rather than used raw because seeds 7 and 8
    offsetting the lattice by one would merely TRANSLATE the pattern by one
    window, which reads as the same building twice."""
    out = _measure("""
const seedOf = (s) => patchWindowInteriors(
    new THREE.MeshStandardMaterial(), { seed: s })
    .userData.uniforms.uWinSeed.value;
const many = [];
for (let s = 0; s < 64; s++) many.push(seedOf(s));
console.log(JSON.stringify({
  same: seedOf(7) === seedOf(7),
  differs: seedOf(7) !== seedOf(8),
  gap: Math.abs(seedOf(7) - seedOf(8)),
  dflt: seedOf(undefined) === seedOf(1),
  finite: Number.isFinite(seedOf(123456789)),
  allInt: many.every((v) => Number.isInteger(v)),
  bigInt: Number.isInteger(seedOf(123456789)),
  spread: new Set(many).size,
  max: Math.max(...many),
}));
""")
    assert out["same"], "the same seed must relight the same windows"
    assert out["differs"] and out["gap"] > 1, "not a one-cell shift"
    assert out["dflt"], "the default seed is 1, not an unseeded draw"
    assert out["finite"] and out["bigInt"], "a large seed stays a usable int"
    assert out["allInt"], "a fractional offset puts the id off the lattice"
    assert out["spread"] >= 48, "64 seeds must not collapse onto a handful"
    assert out["max"] < 512, "and the lattice stays where the hash is spread"


def test_a_lit_room_lifts_the_radiance_not_only_the_albedo():
    """`fragmentBody` lands after <color_fragment>, where `diffuseColor` is
    albedo: a room written there alone can only ever be a pale wall,
    however bright the number.  The lift therefore also goes on
    `totalEmissiveRadiance` — legal at this hook only because three
    declares it BEFORE the hook and (without an emissive map) never
    overwrites it after.  A three upgrade that moves that line breaks the
    effect with no error, so the order is asserted on three's own source."""
    out = _measure("""
import { ShaderLib, ShaderChunk } from 'three';
const src = ShaderLib.standard.fragmentShader;
const sh = patched();
console.log(JSON.stringify({
  fs: sh.fragmentShader,
  declaredBeforeHook: src.indexOf('vec3 totalEmissiveRadiance = emissive;')
      < src.indexOf('#include <color_fragment>'),
  emissiveMapOnly: /#ifdef USE_EMISSIVEMAP[^]*?totalEmissiveRadiance \\*=/
      .test(ShaderChunk.emissivemap_fragment),
  usedAfterHook: src.indexOf('#include <color_fragment>')
      < src.lastIndexOf('totalEmissiveRadiance'),
}));
""")
    assert out["declaredBeforeHook"], "the hook is too late to declare it"
    assert out["usedAfterHook"], "and it must still reach outgoingLight"
    assert out["emissiveMapOnly"], "only an emissive map may rescale it"
    fs = out["fs"]
    assert "totalEmissiveRadiance += max(winRoom + winGrain, 0.0)" in fs
    assert "* (uWinEmissive * winOn * winPane);" in fs
    # Gated by both: an unlit room radiates nothing, and neither does the
    # spandrel between the panes.
    assert "float winOn = step(astraHash21(winId), uWinLit);" in fs
    assert "float winPane = winE.x * winE.y;" in fs


def test_an_unlit_cell_is_dark_glass_and_never_a_hole():
    """PORT LAW 2 and 3, on top of theirs.  The failure that makes a night
    facade read as a punched card is the windows that are off going to flat
    black — and writing the reflection to `diffuseColor` alone IS that
    failure on a night host, because albedo is multiplied by the light
    landing on the wall and at night there is none.  The reflection is
    added as radiance as well.  The sky it reflects is the scene's own: the
    fog colour where there is fog, split at the reflected horizon so the
    pane takes the ground below it and the sky above."""
    out = _measure("""
const sh = patched();
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    assert "vec3 winSky = uWinCool;" in fs
    assert "#ifdef USE_FOG" in fs and "mix(uWinCool, fogColor, 0.7);" in fs
    assert "vec3 winRefl = reflect(-winV, winNn);" in fs
    assert "mix(0.34, 1.0, smoothstep(-0.30, 0.30, winRefl.y))" in fs
    assert "vec3 winDark = winRoom * 0.06 + uWinCool * 0.015" in fs
    assert "+ winSky * (0.05 + 0.6 * winFr);" in fs
    # The radiance half, gated on the pane being OFF.
    assert "totalEmissiveRadiance += (winSky * (0.06 + 0.9 * winFr)" in fs
    assert "* ((1.0 - winOn) * winPane);" in fs
    # Lit or unlit, the pane is a MIX over the facade albedo underneath.
    assert "max(mix(winDark, winRoom * 0.35, winOn) + winGrain * 0.25, 0.0)" in fs
    assert "discard" not in fs
    # A reflection off a wall the fog colour cannot be read from must still
    # be glass, so the `cool` floor is unconditional.
    assert fs.index("vec3 winSky = uWinCool;") < fs.index("#ifdef USE_FOG")


def test_no_two_rooms_are_the_same_room():
    """PORT LAW 4 and 5.  A warm/cool lerp gives a facade exactly two bulbs
    at one level, which is a stencil however well the ray traced it.  Every
    cell draws its own hue rotation, its own saturation and its own
    brightness off the tungsten-to-daylight axis, and about a third are
    dressed — a blind hung over the top of the pane (a smoothstep hem, not
    the razor line the original cut across every floor at one height), or a
    curtain drawn right across, which scatters and turns the box into an
    even panel.  Three INDEPENDENT hashes, because reusing one would tie
    the bright rooms to the warm ones."""
    out = _measure("""
const sh = patched();
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    for draw in ("float winHt = astraHash21(winId + 3.3);",
                 "float winHf = astraHash21(winId + 19.7);",
                 "float winHl = astraHash21(winId + 41.1);"):
        assert draw in fs, draw
    assert "winTint = astraHueShift(winTint, (winHl - 0.5) * 0.18);" in fs
    assert "mix(0.72, 1.24, astraHash21(winId + 7.9))" in fs
    assert ("float winLevel = mix(0.30, 1.15,"
            " winHl * winHl * (3.0 - 2.0 * winHl));") in fs
    assert "vec3 winRoom = winTint * (winShade * winLevel);" in fs
    # The fittings.
    assert "winShade *= mix(1.0, 0.24, step(0.74, winHf)" in fs
    assert "* smoothstep(0.46, 0.64, winQ.y));" in fs, "a hem, not a razor"
    assert "float winCurt = step(winHf, 0.30) * 0.85;" in fs
    assert "winShade = mix(winShade, 0.52 + 0.34 * (1.0 - winQ.y), winCurt);" in fs
    # The fitting is on the ceiling, so the room falls off downward.
    assert "winShade *= mix(0.58, 1.14, smoothstep(0.0, 1.0, winHit.y));" in fs
    # One LSB of grain against 8-bit banding on those shallow ramps.
    assert ("vec3 winGrain = vec3((astraHash21(gl_FragCoord.xy) - 0.5)"
            " * 0.004);") in fs


def test_every_tunable_is_a_uniform_with_the_documented_defaults():
    """Two facades tuned differently must still share one compiled program,
    which is only true while every option is a uniform: the FIRST material
    to compile a cache key decides the GLSL for every material that shares
    it, so a baked-in option value would silently become everybody's.  The
    grid is rounded because a fractional cell count splits the last column
    at the UV seam."""
    out = _measure("""
const val = (m) => {
  const o = {};
  for (const k of Object.keys(m.userData.uniforms)) {
    const v = m.userData.uniforms[k].value;
    o[k] = (v && v.isColor) ? v.getHex() : ((v && v.isVector2)
        ? [v.x, v.y] : v);
  }
  return o;
};
const d = patchWindowInteriors(new THREE.MeshStandardMaterial());
const t = patchWindowInteriors(new THREE.MeshStandardMaterial(), {
  rows: 12.4, cols: 5, depth: 1.2, lit: 1.6, emissive: 0.4,
  warm: new THREE.Color(0x112233), cool: 0x445566,
});
const a = patched({ rows: 3, lit: 0.9 });
const b = patched({ rows: 30, lit: 0.1, depth: 4 });
console.log(JSON.stringify({
  d: val(d), t: val(t),
  sameGlsl: a.fragmentShader === b.fragmentShader
      && a.vertexShader === b.vertexShader,
  key: d.customProgramCacheKey(),
  gridA: a.uniforms.uWinGrid.value.y, gridB: b.uniforms.uWinGrid.value.y,
  marked: !!d.userData.astraShader,
}));
""")
    assert out["d"]["uWinGrid"] == [6, 8], "cols x rows, per unit of UV"
    assert out["d"]["uWinDepth"] == 0.6 and out["d"]["uWinLit"] == 0.35
    assert out["d"]["uWinEmissive"] == 1.6
    assert out["d"]["uWinWarm"] == 0xffc98a
    assert out["d"]["uWinCool"] == 0x9fc0e8
    assert out["t"]["uWinGrid"] == [5, 12], "a fractional cell count rounds"
    assert out["t"]["uWinDepth"] == 1.2 and out["t"]["uWinEmissive"] == 0.4
    assert out["t"]["uWinLit"] == 1, "a fraction of the windows, clamped"
    assert out["t"]["uWinWarm"] == 0x112233
    assert out["t"]["uWinCool"] == 0x445566
    assert out["sameGlsl"], "one patch kind must compile to one source"
    assert out["gridA"] == 3 and out["gridB"] == 30
    assert out["key"] == "astra:windows:interior"
    assert out["marked"], "tickShaders finds patches by this flag"


def test_the_lit_pattern_is_seeded_and_reproducible():
    """A re-render must not relight a different set of windows.  The pattern
    is a hash of the cell index offset by the seed, and nothing else: no
    `uTime` reaches the fragment stage, so it cannot flicker between
    frames, and no `Math.random` reaches the JS."""
    out = _measure("""
const sh = patched({ seed: 7 });
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    assert "vec2 winId = floor(winG)" in fs
    assert "float winOn = step(astraHash21(winId), uWinLit);" in fs
    assert "uTime" not in fs, "a seeded pattern may not move with time"
    src = _LIB.read_text(encoding="utf-8")
    assert "Math.random" not in src and "Date.now" not in src
    # Its GLSL head ships only in the FRAGMENT stage; dFdx there would fail
    # every vertex shader in the engine at once.
    assert "dFdx" in src and "vertexHead: WIN_VARYINGS" in src


def test_buildings_sharing_one_material_still_light_differently():
    """`materials.js` returns ONE instance to every caller that asked for
    the same look, so a district of blocks shares a facade material and a
    per-material seed would light every tower identically.  The lit hash
    also takes the draw's own world origin, which is constant across a mesh
    and therefore safe to hash per cell, plus the quantised normal so the
    four faces of one box differ from each other."""
    out = _measure("""
const sh = patched();
const vs = sh.vertexShader, fs = sh.fragmentShader;
console.log(JSON.stringify({
  vs, fs,
  vertexAfterHook: vs.indexOf('#include <begin_vertex>')
      < vs.indexOf('vWinW ='),
  fragAfterHook: fs.indexOf('#include <color_fragment>')
      < fs.indexOf('diffuseColor.rgb ='),
  fragOnly: !vs.includes('dFdx') && fs.includes('dFdx'),
  fragDefine: fs.includes('#define ASTRA_FRAG'),
  vertexDefine: vs.includes('#define ASTRA_FRAG'),
}));
""")
    vs, fs = out["vs"], out["fs"]
    assert out["vertexAfterHook"] and out["fragAfterHook"]
    assert out["fragOnly"], "a derivative in the vertex stage breaks all"
    assert out["fragDefine"] and not out["vertexDefine"]
    # The per-draw key: the mesh origin in world space, and the instance
    # origin on an InstancedMesh, where `transformed` is still local.
    assert "vec4 winO = vec4(0.0, 0.0, 0.0, 1.0);" in vs
    assert "#ifdef USE_INSTANCING" in vs
    assert "winP = instanceMatrix * winP;" in vs
    assert "winO = instanceMatrix * winO;" in vs
    assert "vWinKey = (modelMatrix * winO).xyz;" in vs
    assert "astraHash21(winKq.xz + winKq.y)" in fs
    assert "float winFace = dot(floor(winNn * 4.0 + 0.5)" in fs
    # A varying declared in one stage only is a link failure whose symptom
    # is the patch simply not drawing.
    for decl in ("varying vec2 vWinUv;", "varying vec3 vWinW;",
                 "varying vec3 vWinN;", "varying vec3 vWinKey;"):
        assert decl in vs and decl in fs, decl


def test_the_convenience_finds_the_facade_and_spares_the_shared_material():
    """A `block()` is a group of merged meshes — walls, trim, glazing,
    reveals — so a caller reaching for `mesh.material` would window the
    cornice or miss entirely.  And its wall material is the SHARED instance
    `materials.js` hands to every caller that asked for plaster, so
    patching it in place would put windows on every plastered surface in
    the scene.  It is cloned first, with its userData reset: clone()
    deep-copies userData but not `onBeforeCompile`, so a carried-over patch
    chain would name patches in the cache key that no longer run."""
    out = _measure("""
import * as MAT from './lib/materials.js';
import { block } from './lib/building.js';
let s = 5;
const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
const b = block({ w: 14, d: 12, h: 20, style: 'masonry', rand, lit: 0.4 });
const before = b.getObjectByName('Walls').material;
const trimBefore = b.getObjectByName('Trim').material;
let meshes = 0, verts = 0;
b.traverse((o) => { if (o.isMesh) {
  meshes++; verts += o.geometry.attributes.position.count; } });
const same = makeNightWindows(b, { seed: 4 }) === b;
let after = 0, afterVerts = 0, patchedMats = 0;
const seen = new Set();
b.traverse((o) => { if (o.isMesh) {
  after++; afterVerts += o.geometry.attributes.position.count;
  for (const m of [].concat(o.material)) {
    if (seen.has(m.uuid)) continue;
    seen.add(m.uuid);
    if (m.userData.astraPatches) patchedMats++;
  } } });
const walls = b.getObjectByName('Walls').material;
console.log(JSON.stringify({
  same, meshes, after, addedVerts: afterVerts - verts, patchedMats,
  wallsPatched: !!walls.userData.astraPatches,
  wallsKey: walls.customProgramCacheKey(),
  cloned: walls !== before,
  sharedSpared: !before.userData.astraPatches,
  cacheSpared: !MAT.plaster().userData.astraPatches,
  trimUntouched: b.getObjectByName('Trim').material === trimBefore
      && !trimBefore.userData.astraPatches,
  cleanUserData: !walls.userData.shared,
}));
""", ("shader.js", "windows.js", "materials.js", "building.js"))
    assert out["same"], "it returns the object, so it can be added inline"
    assert out["wallsPatched"] and out["wallsKey"] == "astra:windows:interior"
    assert out["cloned"] and out["cleanUserData"]
    assert out["sharedSpared"], "the shared instance must not be patched"
    assert out["cacheSpared"], "nor the next caller asking for plaster"
    assert out["trimUntouched"], "a cornice does not get windows"
    assert out["patchedMats"] == 1, "one facade material, one draw call"
    # No geometry at all: the depth is in the shader, which is the whole
    # reason this can run over a district.
    assert out["after"] == out["meshes"] and out["addedVerts"] == 0


def test_a_tower_leaves_with_no_more_materials_than_it_arrived_with():
    """`tower()` stacks `block()` tiers whose walls came from `materials.js`,
    so several tiers arrive holding the SAME instance.  Patching per mesh
    would clone it per tier and pay a material (and a uniform upload) for
    each; the convenience patches a source material once and hands the same
    result to every mesh that had it.

    The reference asserted a count of exactly one here.  That is a fact
    about their `block()`, not about this module: ours tints each tier's
    wall into one of `materials.js`'s nine hue buckets, so a three-tier
    tower arrives with two.  The law that belongs to windows.js is that the
    number does not GROW."""
    out = _measure("""
import { tower } from './lib/building.js';
let s = 9;
const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
const t = tower({ w: 20, d: 18, h: 90, style: 'glass', rand, lit: 0.5 });
const wallsOf = () => {
  const out = [];
  t.traverse((o) => { if (o.isMesh && /Walls/.test(o.name)) {
    out.push(o.material); } });
  return out;
};
const before = new Set(wallsOf().map((m) => m.uuid)).size;
makeNightWindows(t, { seed: 2 });
const walls = wallsOf();
console.log(JSON.stringify({
  tiers: walls.length,
  before,
  after: new Set(walls.map((m) => m.uuid)).size,
  patched: walls.every((m) => !!m.userData.astraPatches),
}));
""", ("shader.js", "windows.js", "materials.js", "building.js"))
    assert out["tiers"] >= 3, "a tower steps in, so it has tiers"
    assert out["before"] < out["tiers"], "tiers do share their wall material"
    assert out["after"] == out["before"], "one patch per source material"
    assert out["patched"], "and every tier's wall carries it"


# The scene our check_shaders.mjs boots: fog, so USE_FOG is defined and the
# `winSky` branch actually compiles, and a camera, because a scene with none
# is reported as not booted and never reaches the compile stage at all.
_SCENE = """
import * as THREE from 'three';
import { block, tower } from './lib/building.js';
import { makeNightWindows, patchWindowInteriors } from './lib/windows.js';

export const BOUNDS = { min: [-60, 0, -60], max: [60, 100, 60] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x0b0f1a, 0.004);
  scene.add(new THREE.HemisphereLight(0x22304a, 0x0a0c10, 0.6));
  let s = 11;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);

  const b = block({ w: 16, d: 12, h: 24, style: 'masonry', rand, lit: 0.4 });
  makeNightWindows(b, { rows: 9, cols: 7, lit: 0.45, seed: 3 });
  b.position.set(-30, 0, 0);
  scene.add(b);

  const t = tower({ w: 20, d: 18, h: 90, style: 'glass', rand, lit: 0.5 });
  makeNightWindows(t, { depth: 0.9, seed: 8, emissive: 2.4,
                        warm: new THREE.Color(0xffc07a), cool: 0x8fb4ff });
  scene.add(t);

  // Instanced, because `transformed` is still object-space at the vertex
  // hook: the USE_INSTANCING branch has to compile too.
  const slabs = new THREE.InstancedMesh(new THREE.BoxGeometry(9, 26, 9),
      new THREE.MeshStandardMaterial({ color: 0x2b3038 }), 6);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 6; i++) {
    m4.makeTranslation(30 + i * 12, 13, (rand() - 0.5) * 40);
    slabs.setMatrixAt(i, m4);
  }
  patchWindowInteriors(slabs.material, { seed: 5, rows: 12, cols: 4 });
  scene.add(slabs);

  return {
    scene,
    cameras: [{ name: 'a', position: [40, 20, 70], lookAt: [0, 20, 0],
                fov: 45 }],
    update() {},
  };
}
"""


def test_it_compiles_on_the_real_renderer_over_a_real_building():
    """The only witness that counts.  Everything above reads a string;
    whether the GPU accepts a mat3 built from screen derivatives, an `out`
    parameter, a slab test per fragment, a `reflect` off an interpolated
    normal, a read of `fogColor` from an injected body and a write to
    `totalEmissiveRadiance` cannot be asserted from source — and a patch
    that does not compile is worth nothing.  Over a real block(), a real
    tower() and an InstancedMesh, in a FOGGED scene so the `winSky` branch
    is the one that gets built."""
    code, out = compile_scene(
        _SCENE, ("shader.js", "materials.js", "building.js", "windows.js"))
    assert code == 0, out
    assert "OK every program compiled" in out
    # A patched built-in keeps the built-in's own depth and fog chunks, so
    # nothing here may be flagged: a WARN means this facade would hold full
    # contrast while the city recedes.
    assert "DISCARDED" not in out and "WARN " not in out
