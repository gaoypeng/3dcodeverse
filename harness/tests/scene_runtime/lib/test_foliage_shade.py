"""foliage_shade.js: the three cues that make a plant read as alive.

Ported from the reference suite (tests/test_foliage_shade_lib.py) 2026-09-01.
Every one of these effects is a ``patchStandard`` injection, so nothing here
can be checked by calling a function: the value of the patch IS the GLSL it
writes, and a wrong sign or a dropped clamp is a frame that looks lit rather
than an exception.  So these tests read the injected source and assert the
chain that carries the intent — a front-lit leaf that gets nothing added, a
base that cannot move, a band measured from the plant's own origin — and then
hand the whole thing to the GPU.

Two changes from the reference.  The last test hands ``_probe.compile_scene``
a one-line ``src/scene.js`` wrapper around the fixture — the USE_INSTANCING
branch is preprocessor-guarded and a real InstancedMesh program is the only
thing that ever compiles it.  And the port's own pigment break-up (the
flat-green fix, measured on fx/out/foliage_shade) is pinned by the two tests
at the end.
"""

from __future__ import annotations

import json
import re

from tests.scene_runtime.lib._probe import LIB_DIR, compile_scene, measure

_LIBS = ("shader.js", "foliage_shade.js")

_LIB_SRC = (LIB_DIR / "foliage_shade.js").read_text(encoding="utf-8")

# The patch only exists inside onBeforeCompile, so every probe hands it the
# two chunks patchStandard replaces and reads back what it wrote.
_PRELUDE = """
import * as THREE from 'three';
import { patchLeafSSS, patchWind, patchRootContact }
    from './lib/foliage_shade.js';

const std = () => new THREE.MeshStandardMaterial({ color: 0x557733 });

function compile(mat) {
  const shader = {
    vertexShader: 'void main() {\\n#include <begin_vertex>\\n}',
    fragmentShader: 'void main() {\\n#include <color_fragment>\\n}',
    uniforms: {},
  };
  mat.onBeforeCompile(shader);
  return shader;
}
"""

# ONE leaf material on an InstancedMesh AND a plain Mesh: the instanced
# program is the only place the USE_INSTANCING branch of the world-space
# helpers is ever compiled.
_FIXTURE = """
import * as THREE from 'three';
import { patchLeafSSS, patchWind, patchRootContact }
    from './lib/foliage_shade.js';

export function build() {
  const g = new THREE.Group();

  const leaf = new THREE.MeshStandardMaterial({
    color: 0x4e7a35, flatShading: true, side: THREE.DoubleSide,
  });
  patchLeafSSS(leaf, { sunDir: new THREE.Vector3(0.3, 0.62, -0.72) });
  patchWind(leaf, { strength: 0.3, height: 5 });

  const crown = new THREE.InstancedMesh(
      new THREE.IcosahedronGeometry(1.1, 1), leaf, 12);
  const m = new THREE.Matrix4();
  for (let i = 0; i < 12; i++) {
    m.compose(
        new THREE.Vector3((i % 4) * 3 - 4.5, 4 + (i % 3) * 0.7,
                          Math.floor(i / 4) * 3 - 3),
        new THREE.Quaternion().setFromEuler(new THREE.Euler(0, i * 0.7, 0)),
        new THREE.Vector3(1.2, 0.9, 1.1));
    crown.setMatrixAt(i, m);
  }
  crown.instanceMatrix.needsUpdate = true;
  crown.frustumCulled = false;
  g.add(crown);

  const card = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 1.6), leaf);
  card.position.set(2.6, 3.2, 2.4);
  g.add(card);

  // Base at y = 0 — the contract patchRootContact measures height from.
  const bark = new THREE.MeshStandardMaterial({ color: 0x6b5540 });
  patchWind(bark, {
    strength: 0.06, height: 5, dir: new THREE.Vector2(1, 0),
  });
  patchRootContact(bark, { band: 0.6, darken: 0.5 });
  const stem = new THREE.CylinderGeometry(0.28, 0.5, 5, 12);
  stem.translate(0, 2.5, 0);
  const trunk = new THREE.Mesh(stem, bark);
  trunk.castShadow = true;
  g.add(trunk);
  return g;
}
"""

# The wrapper check_shaders.mjs boots: our host asks for createScene(), the
# reference's tool did not.
_SCENE = """
import * as THREE from 'three';
import { build } from './fixture.js';

export const BOUNDS = { min: [-10, 0, -10], max: [10, 10, 10] };
export function heightAt() { return 0; }

export async function createScene() {
  const scene = new THREE.Scene();
  scene.add(new THREE.DirectionalLight(0xffffff, 2));
  scene.add(build());
  return {
    scene,
    cameras: [{ name: 'a', position: [8, 4, 8], lookAt: [0, 3, 0] }],
    update() {},
  };
}
"""


def _probe(body: str) -> dict:
    return measure(_PRELUDE + body, _LIBS)


def _find(pattern: str, src: str) -> re.Match:
    m = re.search(pattern, src)
    assert m, f"{pattern} not in\n{src}"
    return m


def test_the_leaf_glow_fires_only_with_the_sun_behind_the_leaf():
    """The whole point of the patch: light from BEHIND the surface. The term
    is a dot against the NEGATED sun so it peaks when the eye looks into the
    sun through the leaf, clamped at zero so a front-lit leaf is left exactly
    as it was, and every later line may only SCALE it — one addition anywhere
    in that chain and the crown glows at noon with the sun at the camera's
    back."""
    out = _probe("""
const m = std();
patchLeafSSS(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    back = _find(r"float (\w+) = max\(dot\((\w+), -uLeafSun\), 0\.0\);", fs)
    # The view vector really is surface -> eye, in world space.
    _find(rf"vec3 {back.group(2)} = normalize\(cameraPosition"
          r" - vAstraFol\);", fs)
    term = _find(rf"float (\w+) = uLeafAmt \* pow\({back.group(1)},"
                 r" uLeafPow\);", fs)
    lt = term.group(1)
    uses = [ln.strip() for ln in fs.splitlines()
            if re.search(rf"\b{lt}\b", ln) and f"float {lt} =" not in ln]
    assert uses, "the transmission term is computed and never used"
    for line in uses:
        assert (line.startswith(f"{lt} *=") or line.startswith("+")
                or line.startswith(f"diffuseColor.rgb += uLeafTint * {lt};")
                ), line
    # A crown is not a lamp: what reaches the eye is dappled from the WORLD
    # position, which also decorrelates neighbouring trees.
    assert re.search(rf"{lt} \*= [^;]*astraFbm2\(vAstraFol\.xz", fs)


def test_leaf_options_arrive_as_uniforms_and_the_sun_is_normalised():
    """A patch whose knobs are uniforms is one program for every tree in the
    scene; an un-normalised sunDir would scale the exponent's base and quietly
    change the lobe, so it is normalised on a COPY — the caller's light
    direction is not the patch's to mutate."""
    out = _probe("""
const a = std();
patchLeafSSS(a);
const sun = new THREE.Vector3(0, 4, 0);
const b = std();
patchLeafSSS(b, { strength: 0.2, power: 6, sunDir: sun,
                  tint: new THREE.Color(0x112233) });
const u = (m) => m.userData.uniforms;
console.log(JSON.stringify({
  amt: u(a).uLeafAmt.value, pow: u(a).uLeafPow.value,
  sunLen: u(a).uLeafSun.value.length(), sunY: u(a).uLeafSun.value.y,
  tint: u(a).uLeafTint.value.getHex(),
  bAmt: u(b).uLeafAmt.value, bPow: u(b).uLeafPow.value,
  bSunY: u(b).uLeafSun.value.y, callerSunY: sun.y,
  bTint: u(b).uLeafTint.value.getHex(),
}));
""")
    assert out["amt"] == 0.55 and out["pow"] == 3
    assert abs(out["sunLen"] - 1) < 1e-6 and out["sunY"] > 0.5
    assert out["tint"] == 0xccd966, "a warm yellow-green, not white"
    assert out["bAmt"] == 0.2 and out["bPow"] == 6
    assert abs(out["bSunY"] - 1) < 1e-6 and out["callerSunY"] == 4
    assert out["bTint"] == 0x112233


def test_the_transmitted_colour_takes_the_key_lights_own_colour():
    """New here.  A leaf transmits the light that reached it: under a golden
    rig, or a moon, a hardcoded noon yellow-green is the wrong colour on the
    one surface in the shot the eye reads as light.  `light` multiplies the
    pigment tint, on a COPY of the caller's colour, and defaults to no
    change at all."""
    out = _probe("""
const a = std();
patchLeafSSS(a);
const moon = new THREE.Color(0.5, 0.6, 1.0);
const b = std();
patchLeafSSS(b, { light: moon });
const t = (m) => m.userData.uniforms.uLeafTint.value;
console.log(JSON.stringify({
  plain: [t(a).r, t(a).g, t(a).b], lit: [t(b).r, t(b).g, t(b).b],
  callerB: moon.b,
}));
""")
    p, li = out["plain"], out["lit"]
    assert li[0] == p[0] * 0.5 and li[2] == p[2]
    assert li[2] / max(li[0], 1e-9) > p[2] / p[0], "moonlight must go cooler"
    assert out["callerB"] == 1.0, "the caller's colour is not ours to scale"


def test_wind_pins_the_base_and_puts_the_travel_in_the_tip():
    """A plant that slides sideways as a whole has come loose from the ground.
    The bend is the plant's own height fraction SQUARED, so it is exactly zero
    at the base and carries the full `strength` only at the tip; normalising
    by the asset's own height (not by a world y) is what lets a scaled
    instance and a tree on a hill sway alike."""
    out = _probe("""
const m = std();
patchWind(m, { strength: 0.4, speed: 2, height: 6 });
const u = m.userData.uniforms;
console.log(JSON.stringify({
  vs: compile(m).vertexShader,
  amp: u.uWindAmp.value, speed: u.uWindSpeed.value, h: u.uWindH.value,
  dirLen: u.uWindDir.value.length(),
}));
""")
    vs = out["vs"]
    bend = _find(r"float (\w+) = clamp\(transformed\.y"
                 r" / max\(uWindH, 1e-3\), 0\.0, 1\.0\);", vs)
    b = bend.group(1)
    _find(rf"transformed \+= (\w+) \* \(uWindAmp \* {b} \* {b} \* (\w+)\);",
          vs)
    assert out["amp"] == 0.4 and out["speed"] == 2 and out["h"] == 6
    # An un-normalised dir would multiply the metres the caller asked for.
    assert abs(out["dirLen"] - 1) < 1e-6


def test_wind_takes_its_phase_from_each_plants_world_origin():
    """Two rules at once.  The phase comes from the plant's ORIGIN, never from
    the vertex — a per-vertex phase shears one plant apart — and it is offset
    by astraStagger, whose several-turn spread is what stops a scattered row
    from nodding in step.  No PRNG anywhere: the world position is the seed,
    so a re-render places the same sway."""
    out = _probe("""
const m = std();
patchWind(m);
console.log(JSON.stringify({ vs: compile(m).vertexShader }));
""")
    vs = out["vs"]
    origin = _find(r"vec3 (\w+) = astraFolWorld\(vec3\(0\.0\)\);", vs)
    phase = _find(r"float (\w+) = uTime \* uWindSpeed"
                  r" \+ astraStagger\(([^)]*)\);", vs)
    seed = phase.group(2)
    assert origin.group(1) in seed, seed
    assert "transformed" not in seed, "per-vertex phase tears the plant"
    _find(rf"sin\({phase.group(1)}\)", vs)
    assert "Math.random" not in _LIB_SRC


def test_wind_reaches_an_instanced_mesh_and_a_plain_mesh_alike():
    """`instanceMatrix` is applied in <project_vertex>, AFTER the body patched
    in at <begin_vertex>, so world space here is hand-built behind a
    USE_INSTANCING guard — and three declares that attribute itself, so a
    second declaration would fail every instanced plant.  The wind direction
    is carried into the plant's own frame too: a scatter yaws every instance,
    and an object-space wind would blow a different way for each one."""
    out = _probe("""
const m = std();
patchWind(m);
console.log(JSON.stringify({ vs: compile(m).vertexShader }));
""")
    vs = out["vs"]
    world = vs[vs.index("vec3 astraFolWorld("):]
    world = world[:world.index("\n}")]
    assert "#ifdef USE_INSTANCING" in world
    assert "modelMatrix * instanceMatrix * vec4(p, 1.0)" in world
    assert "#else" in world and "modelMatrix * vec4(p, 1.0)" in world
    assert "attribute mat4 instanceMatrix" not in vs
    _find(r"vec3 (\w+) = astraFolLocalDir\(vec3\(uWindDir\.x, 0\.0,"
          r" uWindDir\.y\)\);", vs)
    # fwidth is fragment-only and the util block ships in both stages:
    # astraStroke may be DEFINED in the vertex shader (behind its guard),
    # never called from a vertex body.
    assert vs.count("astraStroke(") == 1
    assert "#define ASTRA_FRAG" not in vs


def test_wind_declares_the_time_uniform_its_body_reads():
    """patchStandard's withTime pass runs over the head and the original
    source BEFORE the body is injected, so a vertexBody that reads uTime gets
    no declaration from it — the patch declares its own, exactly once, or
    every plant fails to compile on an undeclared identifier."""
    out = _probe("""
const m = std();
patchWind(m);
const s = compile(m);
console.log(JSON.stringify({
  vsDecls: (s.vertexShader.match(/uniform float uTime;/g) || []).length,
  reads: s.vertexShader.includes('uTime * uWindSpeed'),
  driven: !!m.userData.uniforms.uTime,
}));
""")
    assert out["reads"] and out["vsDecls"] == 1
    # tickShaders() finds it through userData, so one call in tick() advances
    # the sway.
    assert out["driven"]


def test_the_root_band_is_measured_from_the_plants_own_base():
    """`band` is metres above the CONTACT, so the height it shades is the
    vertex's world y minus the object origin's world y.  Measuring from world
    zero instead would put the litter ring underground on any tree standing on
    a hill, which is where trees stand."""
    out = _probe("""
const m = std();
patchRootContact(m, { band: 0.6, darken: 0.3 });
const s = compile(m);
const u = m.userData.uniforms;
console.log(JSON.stringify({
  vs: s.vertexShader, fs: s.fragmentShader,
  band: u.uRootBand.value, dark: u.uRootDark.value,
  litter: u.uRootLitter.value.getHex(),
}));
""")
    _find(r"vAstraRootH = astraFolWorld\(transformed\)\.y"
          r" - astraFolWorld\(vec3\(0\.0\)\)\.y;", out["vs"])
    fs = out["fs"]
    band = _find(r"float (\w+) = astraContact\((\w+), uRootBand\);", fs)
    k = band.group(1)
    # Darkening scales the albedo down and the litter is a tint toward the
    # fallen-leaf colour; both fade out with the same band.
    _find(rf"diffuseColor\.rgb \*= 1\.0 - uRootDark \* {k};", fs)
    _find(rf"diffuseColor\.rgb = mix\(diffuseColor\.rgb, \w+,"
          rf" {k} \* {k} [^;]*\);", fs)
    assert out["band"] == 0.6 and out["dark"] == 0.3
    assert out["litter"] == 0x4a3a26


def test_the_litter_edge_is_broken_across_the_band_not_along_it():
    """Structure runs ACROSS the flow.  The band's flow is vertical, so its
    break-up has to be horizontal: the noise moves the HEIGHT the litter
    reaches and is sampled from world XZ, which makes the edge ragged around
    the trunk.  Noise ranked up the band instead would stripe the trunk in
    horizontal bands.  The port raised its frequency: a trunk is about half a
    metre across, and the reference's 3-per-metre field crosses it in one and
    a half cycles — a tilted line, not a ragged one (fx/crop_fol_root_*.png).
    And the litter itself is hue-broken, because fallen leaves are never one
    brown."""
    out = _probe("""
const m = std();
patchRootContact(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    height = _find(r"float (\w+) = vAstraRootH - uRootBand \* [^;]+;", fs)
    scale = _find(r"astraFbm2\(vAstraFol\.xz \* ([0-9.]+),", height.group(0))
    assert float(scale.group(1)) >= 6, "one cycle across a trunk is a line"
    _find(rf"astraContact\({height.group(1)}, uRootBand\)", fs)
    assert "astraFbm2(vAstraFol.y" not in fs
    litter = _find(r"vec3 (\w+) = astraHueBreak\(uRootLitter, vAstraFol\.xz,"
                   r" [^;]+\);", fs)
    _find(rf"mix\(diffuseColor\.rgb, {litter.group(1)},", fs)


def test_the_pigment_is_broken_up_before_the_glow_goes_on():
    """The port's own fix, and the one that decides whether a crown reads as
    a plant or as green plastic: one material over a whole tree paints one
    flat green, and the transmission then washes that single tone paler.  The
    break runs FIRST, at two coarse world scales (a third, finer octave was
    tried and cut — smooth noise under a metre reads as camouflage), with the
    value swing riding the COARSER field so warm clumps are also the light
    ones.  Measured on fx/out/foliage_shade: crown hue spread 27 -> 35 deg
    close in, 35 -> 45 deg at 20 m, one-value-bin share 0.49 -> 0.30."""
    out = _probe("""
const m = std();
patchLeafSSS(m);
console.log(JSON.stringify({ fs: compile(m).fragmentShader }));
""")
    fs = out["fs"]
    body = fs[fs.index("void main"):]
    breaks = re.findall(r"astraHueBreak\(diffuseColor\.rgb, (\w+),\s*"
                        r"([0-9.]+) \* uLeafVarS", body)
    assert len(breaks) == 2, body
    assert breaks[0][0] == breaks[1][0], "both scales sample one field"
    coarse, fine = float(breaks[0][1]), float(breaks[1][1])
    assert coarse < fine, "the clump break is the coarser of the two"
    # The field folds height in: an xz-only sample paints one tone down a
    # whole column of leaves.
    _find(rf"vec2 {breaks[0][0]} = vAstraFol\.xz \+ vAstraFol\.y", body)
    # Value rides the coarse field, so warm reads as sunlit, not as olive.
    val = _find(rf"float (\w+) = clamp\(\(astraFbm2\({breaks[0][0]} \*"
                rf" {coarse} \* uLeafVarS, 2\)\s*- [0-9.]+\) \*"
                r" [0-9.]+, -1\.0, 1\.0\);", body)
    _find(rf"diffuseColor\.rgb \*= 1\.0 \+ uLeafVar \* [0-9.]+ \*"
          rf" {val.group(1)};", body)
    # Every line of it scales by uLeafVar, so variance: 0 is exactly the
    # unbroken albedo — an agent that wants a flat colour still gets one.
    assert body.index("astraHueBreak") < body.index("uLeafTint")


def test_the_pigment_break_is_a_uniform_and_retunes_in_place():
    """Scale and swing are uniforms, so a meadow of grass and a canopy read
    from 40 m share one program, and a second call retunes rather than
    injecting a second copy of the break."""
    out = _probe("""
const a = std();
patchLeafSSS(a);
const b = std();
patchLeafSSS(b, { variance: 0, varyScale: 4 });
patchLeafSSS(b, { variance: 0.9, varyScale: 3 });
const u = (m) => m.userData.uniforms;
const fs = compile(b).fragmentShader;
console.log(JSON.stringify({
  var0: u(a).uLeafVar.value, scale0: u(a).uLeafVarS.value,
  varB: u(b).uLeafVar.value, scaleB: u(b).uLeafVarS.value,
  breaks: (fs.match(/astraHueBreak\\(diffuseColor/g) || []).length,
}));
""")
    assert out["var0"] == 0.6 and out["scale0"] == 1
    assert out["varB"] == 0.9 and out["scaleB"] == 3
    assert out["breaks"] == 2, "a re-applied patch must not stack its code"


def test_the_three_patches_stack_on_one_material_exactly_once():
    """Leaves that both glow and sway are two calls on one leaf material, so
    stacking is the ordinary case.  patchStandard chains them, but it repeats
    whatever it is handed: the shared world-space helpers must be emitted ONCE
    (a second function body is a compile error), and a re-applied patch has to
    retune its uniforms rather than inject its declarations a second time."""
    out = _probe("""
const all = std();
patchLeafSSS(all);
patchWind(all, { strength: 0.2 });
patchRootContact(all);
patchWind(all, { strength: 0.9 });
const s = compile(all);
const windOnly = std();
patchWind(windOnly);
const count = (src, needle) => src.split(needle).length - 1;
console.log(JSON.stringify({
  key: all.customProgramCacheKey(),
  windKey: windOnly.customProgramCacheKey(),
  leaf: s.fragmentShader.includes('uLeafTint * lT'),
  wind: s.vertexShader.includes('uWindAmp * wB * wB'),
  root: s.fragmentShader.includes('uRootDark'),
  amp: all.userData.uniforms.uWindAmp.value,
  worldFn: count(s.vertexShader, 'vec3 astraFolWorld(vec3 p) {'),
  bend: count(s.vertexShader, 'float wB ='),
  varyVs: count(s.vertexShader, 'varying vec3 vAstraFol;'),
  varyFs: count(s.fragmentShader, 'varying vec3 vAstraFol;'),
}));
""")
    assert out["leaf"] and out["wind"] and out["root"]
    assert out["worldFn"] == 1 and out["varyVs"] == 1 and out["varyFs"] == 1
    # Every option is a uniform, so re-applying a patch retunes it in place
    # instead of stacking a second copy of its code.
    assert out["bend"] == 1 and out["amp"] == 0.9
    for part in ("leaf", "wind", "root"):
        assert part in out["key"], out["key"]
    # three caches programs by key: a material carrying more patches must not
    # collide with one carrying fewer.
    assert out["key"] != out["windKey"]


def test_no_vertex_body_leans_on_another_patchs_varying():
    """patchStandard injects each body straight after <begin_vertex>, and the
    bodies are joined in call order, so a vertex body that read a varying
    another patch writes would depend on which order the caller happened to
    patch in.  Every body here stands alone; the shared world position is
    written for the FRAGMENT stage only."""
    out = _probe("""
const m = std();
patchLeafSSS(m);
patchWind(m);
patchRootContact(m);
console.log(JSON.stringify({ vs: compile(m).vertexShader }));
""")
    body = out["vs"][out["vs"].index("void main"):]
    reads = [ln.strip() for ln in body.splitlines()
             if "vAstraFol" in ln and "vAstraRootH" not in ln]
    assert reads == ["vAstraFol = astraFolWorld(transformed);"], reads


def test_all_three_patches_compile_on_the_gpu():
    """The only witness that the USE_INSTANCING branch exists at all: it is
    preprocessor-guarded, so nothing but a real InstancedMesh program ever
    compiles it.  The fixture puts one leaf material on both an InstancedMesh
    and a plain Mesh, and flat shading on it — the patch must not lean on
    vNormal, which three omits when FLAT_SHADED, and the fragment hook runs
    before <normal_fragment_begin> in any case."""
    code, out = compile_scene(_SCENE, _LIBS, extra={"fixture.js": _FIXTURE})
    assert code == 0, out
    report = json.loads(out.strip().splitlines()[-1])
    assert report["ok"] and report["errors"] == [], out
    # Two patched built-ins, and MORE programs than materials: the leaf
    # material compiled twice, once per instancing state.
    assert report["compile"]["programs"] >= 3, report["compile"]
