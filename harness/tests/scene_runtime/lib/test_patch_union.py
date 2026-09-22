"""The ten surface-patch libraries on ONE material: the names each owns, and
every chain of them on the real renderer.

terrain_shade, waterside, surface_wear, aging, accumulation, strata, damp,
roadway, caustics and submerged are written to land on the same materials —
a submerged, weathered, splatted bank wears half of them at once — and
`patchStandard` chains whatever it is handed.  Every way that goes wrong is
silent or far from its cause.  patchStandard DROPS a repeated uniform or
varying and keeps the first, so a name two libraries share leaves one patch
reading the other's value with nothing reported; a helper two libraries both
define is deduped while the bodies match and THROWS the day either is edited;
a main() local two bodies both declare is a redefinition that takes the whole
material down; and a chain that compiles in one order, one variant or next
to one neighbour only is a scene that renders black the day an author picks
another.

Each library's own suite pins its physics.  This file holds the two claims
about all ten at once.  Consolidated 2026-09-22 from eight per-file
"no patch declares a name its neighbours own" tests, which between them
checked the uniforms of 31 of the 45 library pairs, and from eleven per-file
GPU scenes that each compiled one library's own chain.
"""
from __future__ import annotations

import itertools
import json
import re
from collections import Counter

import pytest

from tests.scene_runtime.lib._probe import (
    LIB_DIR,
    SHADER_JS,
    _find,
    _main_body,
    compile_scene,
    measure,
)

pytestmark = pytest.mark.node

# The ten, and the patches each ships.
LIBRARIES: dict[str, tuple[str, ...]] = {
    "terrain_shade.js": ("patchTriplanar", "patchSlopeSplat"),
    "waterside.js": ("patchShoreWet", "patchShoreFoam", "patchShallowWater"),
    "surface_wear.js": ("patchMicroBreakup", "patchEdgeWear"),
    "aging.js": ("patchDripStains", "patchRust", "patchDust"),
    "accumulation.js": ("patchSnow", "patchSand"),
    "strata.js": ("patchRockStrata", "patchErosionStreaks"),
    "damp.js": ("patchMoss", "patchMoisture", "patchCrackedMud"),
    "roadway.js": ("patchRoadSurface", "patchSeamBand", "patchTracks"),
    "caustics.js": ("patchCaustics",),
    "submerged.js": ("patchUnderwater", "patchThinIce"),
}
ALL = [p for patches in LIBRARIES.values() for p in patches]

# Shared ON PURPOSE: the clock tickShaders drives, and the one world
# position / normal pair every library's base writes (shader.js
# WORLD_VARYINGS), so a material wearing several declares ONE.
SHARED = {"uniform": {"uTime"}, "varying": {"vAstraWorld", "vAstraWorldN"}}

# The spelling each library's own suite pinned, so a name added later stays
# inside its library rather than merely missing today's neighbours.
SPELLING = {
    "caustics.js": {"uniform": ("uCau",), "local": ("cau",)},
    "submerged.js": {"uniform": ("uSub", "uIce"), "local": ("sub", "ice"),
                     "helper": ("astraIce",)},
    "strata.js": {"helper": ("astraStrata",)},
    "roadway.js": {"helper": ("astraRoad",)},
}

# Every patch at its defaults, but the splat's snow line pulled into frame
# so its snow branch is live code.  Shared by the probe and the scene.
_APPLY_JS = "\n".join(
    f"import {{ {', '.join(patches)} }} from './lib/{lib}';"
    for lib, patches in LIBRARIES.items()) + """
const LIBS = """ + json.dumps({lib: list(p) for lib, p in LIBRARIES.items()}) + """;
const PATCH = { """ + ", ".join(ALL) + """ };
const OPTS = { patchSlopeSplat: { snowLine: 30 } };
const apply = (m, names) => {
  for (const n of names) PATCH[n](m, OPTS[n] || {});
  return m;
};
const ALL = Object.values(LIBS).flat();
"""

# What a head or a body declares, by kind (the return types are the set
# patchStandard's own duplicate-helper check reads).
_TYPE = r"(?:void|float|u?int|bool|[biu]?vec[234]|mat[234](?:x[234])?)"
_HELPER = re.compile(r"^\s*(?:(?:lowp|mediump|highp)\s+)?" + _TYPE + r"\s+(\w+)\s*\(", re.M)
_LOCAL = re.compile(r"^\s*(?:const\s+)?(?:(?:lowp|mediump|highp)\s+)?"
                    r"(?:float|u?int|bool|[biu]?vec[234]|mat[234])\s+(\w+)\s*[=;,\[]", re.M)
_VARYING = re.compile(r"^\s*varying\s+(?:(?:lowp|mediump|highp)\s+)?\w+\s+(\w+)\s*;", re.M)
_DECL = re.compile(r"^\s*(?:uniform|varying)\s+(?:(?:lowp|mediump|highp)\s+)?\w+\s+(\w+)", re.M)


def _unguarded(src: str) -> str:
    """Drop ``#ifndef X ... #endif`` blocks, which may repeat verbatim.

    terrain_shade ships astraFbmUnit from two patches behind one guard, so the
    TEXT carries it twice and the preprocessor keeps one.  Only an unguarded
    repeat is a redefinition.
    """
    return re.sub(r"#ifndef\b.*?#endif", "", src, flags=re.S)


def test_no_library_declares_a_name_another_owns():
    """All 45 pairs, both stages, four kinds of name — each library compiled
    ALONE, so a clash is caught however the two ever meet.  Uniforms by the
    keys patchStandard merges (the map it dedupes silently), varyings and
    helpers by the heads, locals by what the bodies declare inside main().

    Then every chain at once, forward and reversed: no declaration, helper
    or main() local may appear twice in either stage.  And each library's
    vertex stage is its world base and no more: the point and normal it
    writes fold in `instanceMatrix` (the vertex hook runs before
    <project_vertex>, so without it every copy of a scattered rock shades
    from the mesh ORIGIN), three declares that attribute itself, nothing
    fragment-only is called there (the util block ships in both stages, so
    fwidth and astraStroke may be DEFINED in it, never called), and nothing
    displaces `transformed`, or a hard-edged bed would tear open at its rim."""
    out = measure(SHADER_JS + """
import * as THREE from 'three';
import { GLSL_UTIL } from './lib/shader.js';
""" + _APPLY_JS + """
const std = () => new THREE.MeshStandardMaterial({ color: 0x8b8478, roughness: 0.7 });
const per = {};
for (const [lib, names] of Object.entries(LIBS)) {
  const m = apply(std(), names);
  const s = compile(m);
  per[lib] = { uniforms: Object.keys(m.userData.uniforms),
               vs: s.vertexShader, fs: s.fragmentShader };
}
const union = (names) => {
  const s = compile(apply(std(), names));
  return { vs: s.vertexShader, fs: s.fragmentShader };
};
console.log(JSON.stringify({ per, util: GLSL_UTIL, forward: union(ALL),
                             reversed: union(ALL.slice().reverse()) }));
""", tuple(LIBRARIES))
    util = set(_HELPER.findall(out["util"]))
    owns = {}
    for lib, got in out["per"].items():
        vs, fs = got["vs"], got["fs"]
        heads = vs[:vs.index("void main")] + fs[:fs.index("void main")]
        owns[lib] = {
            "uniform": set(got["uniforms"]) - SHARED["uniform"],
            "varying": set(_VARYING.findall(vs + fs)) - SHARED["varying"],
            "helper": set(_HELPER.findall(heads)) - util,
            "local": set(_LOCAL.findall(_main_body(vs) + _main_body(fs))),
        }
        # An empty set would make every pair below pass for nothing.
        assert owns[lib]["uniform"] and owns[lib]["local"], (lib, owns[lib])
    clashes = [f"{a} and {b} both declare the {kind}(s) {sorted(both)}"
               for (a, mine), (b, theirs) in itertools.combinations(owns.items(), 2)
               for kind in mine if (both := mine[kind] & theirs[kind])]
    assert not clashes, "\n".join(clashes)
    for lib, rules in SPELLING.items():
        for kind, stems in rules.items():
            names = owns[lib][kind]
            assert names, (lib, kind)
            stray = sorted(n for n in names if not n.startswith(stems))
            assert not stray, f"{lib}: {kind}(s) outside {stems}: {stray}"

    for order in ("forward", "reversed"):
        for stage in ("vs", "fs"):
            src = out[order][stage]
            for kind, found in (("declaration", _DECL.findall(src)),
                                ("helper", _HELPER.findall(_unguarded(src))),
                                ("main() local", _LOCAL.findall(_main_body(src)))):
                twice = sorted(n for n, c in Counter(found).items() if c > 1)
                assert not twice, f"{order} chain, {stage}: {kind}(s) {twice} twice"

    for lib, got in out["per"].items():
        vs, fs = got["vs"], got["fs"]
        body = _main_body(vs)
        p = _find(r"vAstraWorld = \(modelMatrix \* (\w+)\)\.xyz;", body).group(1)
        n = _find(r"vAstraWorldN = normalize\(\(modelMatrix \* vec4\((\w+), 0\.0\)\)\.xyz\);",
                  body).group(1)
        assert "#ifdef USE_INSTANCING" in body, lib
        assert f"{p} = instanceMatrix * {p};" in body, (lib, p)
        assert f"{n} = mat3(instanceMatrix) * {n};" in body, (lib, n)
        assert "attribute mat4 instanceMatrix" not in vs, lib
        # A varying declared in one stage only is a link failure, and the
        # symptom is the patch simply not drawing.
        assert set(_VARYING.findall(vs)) == set(_VARYING.findall(fs)), lib
        assert SHARED["varying"] <= set(_VARYING.findall(vs)), lib
        called = [t for t in ("fwidth(", "dFdx(", "dFdy(", "astraStroke(") if t in body]
        assert not called and "gl_FragCoord" not in vs, (lib, called)
        assert not re.search(r"\btransformed\s*[-+*/]?=", body), lib


# Every chain the scene compiles: (material name, patches, options, instances).
# Each patch ALONE: only that catches a body reading a uniform, helper or
# local that a sibling patch or another library declares, because every
# bigger chain supplies it.  Then all of them on one material, forward and
# reversed (a body that reads what a LATER one declares fails one of the
# two) — the case terrain_shade's own two patches once failed together
# ('astraWp' redefinition, 'astraFbmUnit' already has a body: a dead
# material, found only when a sibling library landed on the same bank) —
# and across the variants that change the program: flat shading
# (three omits vNormal), instancing (the only compile of each base's
# USE_INSTANCING branch), transparency (three defines OPAQUE otherwise),
# maps with a bump map and vertex colours (three's own uv and colour
# varyings arrive), and `fog: false` — the fogless branch the waterline
# sheen falls back to, which in a fogged scene only a fogless material
# compiles.  The variants share programs because each whole-chain program
# costs about a second and a half of ANGLE's time on this GPU.
MATERIALS: list[tuple[str, list[str], dict, int]] = [
    *((f"{name} alone", [name], {}, 0) for name in ALL),
    ("every patch", ALL, {}, 0),
    ("every patch, reversed, flat-shaded, instanced", ALL[::-1],
     {"flatShading": True}, 4),
    ("every patch, transparent, textured, unfogged", ALL,
     {"transparent": True, "opacity": 0.6, "textured": True, "fog": False}, 0),
]

# A workspace SCENE for check_shaders.mjs, which boots it through the host:
# fogged, because USE_FOG is a define and a define that is never set compiles
# nothing (rain.js shipped a fog branch that could not compile at all for
# months behind exactly that); a sun that CASTS, or no program carries the
# shadow-map chunks; a camera, or the host reports the scene as not booted
# and never reaches the compile stage.  Around the chains: terrain.js's
# ground and cliff wearing the splat and the triplanar as the terrain suite
# shipped them, and the rain rings, which assemble their OWN shader and so
# take their fog and depth chunks from makeShaderMaterial alone.
_SCENE = """
import * as THREE from 'three';
import { tickShaders } from './lib/shader.js';
import { ground, cliff } from './lib/terrain.js';
import { makeRainRings } from './lib/submerged.js';
""" + _APPLY_JS + """
const MATERIALS = """ + json.dumps(MATERIALS) + """;

export const BOUNDS = { min: [-40, 0, -40], max: [40, 30, 40] };

export function createScene({ renderer }) {
  // The report pins an error on the materials whose source holds the line
  // it quotes, and every chain here shares most of its lines.  So each
  // material's cache key carries its own index, and the host's error hook
  // is wrapped to name the chain whose program did not link.
  const labels = [];
  const tag = (mat, label) => {
    const key = mat.customProgramCacheKey;
    const i = labels.push(label) - 1;
    mat.name = label;
    mat.customProgramCacheKey = () => key() + '#' + i;
  };
  const hook = renderer.debug.onShaderError;
  renderer.debug.onShaderError = (gl, program, ...rest) => {
    const p = renderer.info.programs.find((x) => x.program === program);
    const i = p && /#(\\d+)$/.exec(p.cacheKey);
    console.error('this chain did not link: ' + (i ? labels[i[1]] : p ? p.name : '?'));
    if (hook) hook(gl, program, ...rest);
  };

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcfd8e6, 0.0035);
  scene.add(new THREE.HemisphereLight(0xbcd6ef, 0x4a4130, 0.9));
  const sun = new THREE.DirectionalLight(0xfff0d6, 2.5);
  sun.position.set(5, 8, 7);
  sun.castShadow = true;
  scene.add(sun);

  const tex = new THREE.DataTexture(new Uint8Array([200, 190, 170, 255]), 1, 1);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.needsUpdate = true;
  MATERIALS.forEach(([label, names, o, count], i) => {
    const { textured, ...params } = o;
    const mat = new THREE.MeshStandardMaterial(Object.assign(
        { color: 0x8b8478, roughness: 0.7 }, params,
        textured ? { map: tex, bumpMap: tex, roughnessMap: tex, vertexColors: true } : {}));
    tag(apply(mat, names), label);
    const geo = new THREE.IcosahedronGeometry(0.6, 2);
    if (textured) {
      geo.setAttribute('color', new THREE.Float32BufferAttribute(
          new Array(geo.attributes.position.count * 3).fill(0.9), 3));
    }
    const mesh = count ? new THREE.InstancedMesh(geo, mat, count) : new THREE.Mesh(geo, mat);
    for (let k = 0; k < count; k++) {
      mesh.setMatrixAt(k, new THREE.Matrix4().makeTranslation(0, k * 1.3, 0));
    }
    mesh.position.set((i % 10) * 2 - 9, 0.6, Math.floor(i / 10) * 2 - 6);
    mesh.castShadow = mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    scene.add(mesh);
  });

  let s = 7;
  const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
  const land = ground({ size: 80, segments: 24, rand, relief: 10 });
  tag(patchSlopeSplat(land.mesh.material, { snowLine: 9, snowBand: 2.5 }),
      'ground() with patchSlopeSplat');
  const wall = cliff({ length: 30, height: 12, rand });
  tag(patchTriplanar(wall.mesh.material, { scale: 3.5 }), 'cliff() with patchTriplanar');
  wall.mesh.position.set(0, 0, -20);
  scene.add(land.mesh, wall.mesh, makeRainRings({ count: 16 }));
  return {
    scene,
    cameras: [{ name: 'hero', position: [0, 16, 26], lookAt: [0, 1, 0], fov: 60 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def _library_line(glsl: str | None) -> str:
    """Where a quoted GLSL line lives in the library.  The report maps a line
    back through template strings, and these libraries write their GLSL as
    arrays of quoted lines, which it cannot see."""
    needle = (glsl or "").strip()
    hits = [f"{lib}:{n}" for lib in (*LIBRARIES, "shader.js")
            for n, text in enumerate((LIB_DIR / lib).read_text(encoding="utf-8").splitlines(), 1)
            if len(needle) > 2 and needle in text]
    return " ".join(hits[:3]) or "(not a library line)"


def test_every_chain_compiles_on_the_real_renderer(tmp_path):
    """The only witness that counts.  Everything above reads strings; whether
    the GPU takes every chain of the ten — two dozen bodies in one main, each
    library's instancing branch, the fog and no-fog branches, flat and
    textured variants, a Voronoi with nested loops, a refract into a parallax
    — cannot be asserted from source, and a patch that does not compile is
    worth nothing.  Runs the preflight the authoring agent runs, on our own
    renderer; a failure names the library line, the GLSL error and the chain
    whose program did not link."""
    report_path = tmp_path / "report.json"
    code, out = compile_scene(
        _SCENE, (*LIBRARIES, "terrain.js"), report=report_path)
    assert report_path.is_file(), out  # the preflight itself died: `out` says why
    report = json.loads(report_path.read_text(encoding="utf-8"))
    problems = [f"{_library_line(e.get('source_line'))}: {e.get('message')}"
                for e in report["errors"]]
    problems += [f"{w.get('file')}: {w.get('message')}" for w in report["warnings"]]
    assert code == 0 and report["ok"] and not problems, "\n".join(problems) or out
    compiled = report["compile"]
    assert compiled["gpu"], "this claim is only worth a real GPU"
    # Every chain above is its own program (plus ground, cliff and rings):
    # a green exit on a compile that skipped them would prove nothing.
    assert compiled["custom_materials"] >= len(MATERIALS) + 3, compiled
    assert compiled["programs"] >= len(MATERIALS) + 3, compiled
