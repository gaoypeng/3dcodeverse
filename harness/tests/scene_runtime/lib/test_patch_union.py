"""The seven surface-patch libraries on ONE material."""
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

# The seven, and the patches each ships.
LIBRARIES: dict[str, tuple[str, ...]] = {
    "terrain_shade.js": ("patchTriplanar", "patchSlopeSplat"),
    "waterside.js": ("patchShoreWet", "patchShoreFoam", "patchShallowWater"),
    "surface_wear.js": ("patchMicroBreakup", "patchEdgeWear"),
    "aging.js": ("patchDripStains", "patchRust", "patchDust"),
    "strata.js": ("patchRockStrata", "patchErosionStreaks"),
    "damp.js": ("patchMoss", "patchMoisture", "patchCrackedMud"),
    "roadway.js": ("patchRoadSurface", "patchSeamBand", "patchTracks"),
}
ALL = [p for patches in LIBRARIES.values() for p in patches]

# Shared ON PURPOSE: the clock tickShaders drives, and the one world
# position / normal pair every library's base writes (shader.js
# WORLD_VARYINGS), so a material wearing several declares ONE.
SHARED = {"uniform": {"uTime"}, "varying": {"vAstraWorld", "vAstraWorldN"}}

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
    """Drop ``#ifndef X ..."""
    return re.sub(r"#ifndef\b.*?#endif", "", src, flags=re.S)


def test_no_library_declares_a_name_another_owns():
    """All 45 pairs, both stages, four kinds of name — each library compiled ALONE, so a clash is caught however the two ever meet."""
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

    for order in ("forward", "reversed"):
        for stage in ("vs", "fs"):
            src = out[order][stage]
            for kind, found in (("declaration", _DECL.findall(src)),
                                ("helper", _HELPER.findall(_unguarded(src))),
                                ("main() local", _LOCAL.findall(_main_body(src)))):
                twice = sorted(n for n, c in Counter(found).items() if c > 1)
                assert not twice, f"{order} chain, {stage}: {kind}(s) {twice} twice"
    # each library's vertex stage is its world base and no more: instancing folds in `instanceMatrix`
    # (else every copy of a scattered rock shades from the mesh origin), no stage-split varying,
    # nothing displaces `transformed` (a hard-edged bed would tear open at its rim)
    for lib, got in out["per"].items():
        vs, fs = got["vs"], got["fs"]
        body = _main_body(vs)
        p = _find(r"vAstraWorld = \(modelMatrix \* (\w+)\)\.xyz;", body).group(1)
        assert f"{p} = instanceMatrix * {p};" in body, (lib, p)
        assert set(_VARYING.findall(vs)) == set(_VARYING.findall(fs)), lib
        assert not re.search(r"\btransformed\s*[-+*/]?=", body), lib


# Every chain the scene compiles: (name, patches, options, instances).  Each patch ALONE (catches a body
# reading what a sibling declares), all of them forward and reversed, and the variants that change the
# program: flat shading, instancing (the only compile of USE_INSTANCING), transparency, maps, `fog: false`.
MATERIALS: list[tuple[str, list[str], dict, int]] = [
    *((f"{name} alone", [name], {}, 0) for name in ALL),
    ("every patch", ALL, {}, 0),
    ("every patch, reversed, flat-shaded, instanced", ALL[::-1],
     {"flatShading": True}, 4),
    ("every patch, transparent, textured, unfogged", ALL,
     {"transparent": True, "opacity": 0.6, "textured": True, "fog": False}, 0),
]

# A workspace SCENE for check_shaders.mjs: fogged (USE_FOG is a define, never compiled otherwise), a
# CASTING sun (else no shadow-map chunks), a camera (else the host reports no boot), plus terrain.js's
# ground and cliff wearing the splat and triplanar.
_SCENE = """
import * as THREE from 'three';
import { tickShaders } from './lib/shader.js';
import { ground, cliff } from './lib/terrain.js';
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
  scene.add(land.mesh, wall.mesh);
  return {
    scene,
    cameras: [{ name: 'hero', position: [0, 16, 26], lookAt: [0, 1, 0], fov: 60 }],
    update(t) { tickShaders(scene, t); },
  };
}
"""


def _library_line(glsl: str | None) -> str:
    """Where a quoted GLSL line lives in the library."""
    needle = (glsl or "").strip()
    hits = [f"{lib}:{n}" for lib in (*LIBRARIES, "shader.js")
            for n, text in enumerate((LIB_DIR / lib).read_text(encoding="utf-8").splitlines(), 1)
            if len(needle) > 2 and needle in text]
    return " ".join(hits[:3]) or "(not a library line)"


def test_every_chain_compiles_on_the_real_renderer(tmp_path):
    """The only witness that counts."""
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
    # Every chain above is its own program (plus ground and cliff):
    # a green exit on a compile that skipped them would prove nothing.
    assert compiled["custom_materials"] >= len(MATERIALS) + 2, compiled
    assert compiled["programs"] >= len(MATERIALS) + 2, compiled
