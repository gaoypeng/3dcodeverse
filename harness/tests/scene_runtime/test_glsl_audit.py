"""Static GLSL audits + source-line mapping (node-side pure functions)."""

from __future__ import annotations

import json

import pytest

from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]


def audit_all(cases: dict[str, tuple[str, dict]]) -> dict[str, list[dict]]:
    """``auditFile`` over every named ``(source, opts)`` case, in one node process."""
    body = f"""
import {{ auditFile }} from './lib/glsl_audit.mjs';
const cases = {json.dumps(cases)};
const out = {{}};
for (const [k, [src, opts]] of Object.entries(cases)) out[k] = auditFile('src/shaders/x.js', src, opts);
console.log(JSON.stringify(out));
"""
    return run_node_json(body)


def kinds(findings: list[dict]) -> set[str]:
    return {f["kind"] for f in findings}


INCLUDE = """const vs = `
#version 300 es
precision highp float;
#include <common> uniform float uTime;
void main() { gl_Position = vec4(position, 1.0); }`;
const u = { uTime: { value: 0 } };"""
OUT_PLUS_FRAGCOLOR = """import * as THREE from 'three';
const frag = `
out vec4 fragColor;
void main() { gl_FragColor = vec4(1.0); }`;
new THREE.ShaderMaterial({ glslVersion: THREE.GLSL3, fragmentShader: frag });"""
UTIME_BARE = """const frag = `
void main() { gl_FragColor = vec4(sin(uTime)); }`;"""
UTIME_OK = """const frag = `
uniform float uTime;
void main() { gl_FragColor = vec4(sin(uTime)); }`;
mat.uniforms.uTime = { value: 0 };"""
CHUNK_KEPT = """m.onBeforeCompile = (s) => { s.fragmentShader = s.fragmentShader.replace('#include <output_fragment>', `#include <output_fragment>
gl_FragColor.rgb += vec3(0.1);`); };"""
CHUNK_DROPPED = """m.onBeforeCompile = (s) => { s.vertexShader = s.vertexShader.replace('#include <begin_vertex>', `vec3 transformed = position * 2.0;`); };"""
NO_FOG = """import * as THREE from 'three';
const frag = `
void main() { gl_FragColor = vec4(1.0); }`;
export const m = new THREE.ShaderMaterial({ fragmentShader: frag });"""


def test_the_static_audit_rules():
    """Every rule the shader preflight reports before (or without) a GPU compile, with its
    severity and its file line — the finding an agent is handed."""
    got = audit_all({
        "include": (INCLUDE, {}), "out_plus": (OUT_PLUS_FRAGCOLOR, {}),
        "utime_bare": (UTIME_BARE, {}), "utime_ok": (UTIME_OK, {}),
        "chunk_kept": (CHUNK_KEPT, {}), "chunk_dropped": (CHUNK_DROPPED, {}),
        "fog_unused": (NO_FOG, {"sceneUsesFog": False}), "fog_used": (NO_FOG, {"sceneUsesFog": True}),
        "fog_opt_out": (NO_FOG + "\n// 3dcode: no-fog", {"sceneUsesFog": True}),
    })
    f = got["include"]
    assert {"include_not_alone", "version_directive", "precision_directive"} <= kinds(f)
    inc = next(x for x in f if x["kind"] == "include_not_alone")
    assert inc["line"] == 4  # line 1 is `const vs = \``, the include is on file line 4
    assert inc["severity"] == "error"
    assert next(x for x in f if x["kind"] == "version_directive")["severity"] == "error"

    assert {"fragment_out_and_gl_fragcolor", "glsl3_gl_fragcolor"} <= kinds(got["out_plus"])

    assert {"undeclared_uniform", "unbound_uniform"} <= kinds(got["utime_bare"])
    assert kinds(got["utime_ok"]) == set()          # declared + bound → clean

    assert kinds(got["chunk_kept"]) == set()
    assert kinds(got["chunk_dropped"]) == {"chunk_dropped"}
    assert got["chunk_dropped"][0]["severity"] == "warn"

    assert "no_fog" not in kinds(got["fog_unused"])
    assert "no_fog" in kinds(got["fog_used"])
    assert "no_fog" not in kinds(got["fog_opt_out"])


def test_locate_source_line_maps_to_file_and_line():
    files = [
        {"file": "src/shaders/a.js", "text": "const f = `\nvoid main() {\n  float x = undefinedThing;\n}`;"},
        {"file": "src/shaders/b.js", "text": "const g = `\nvoid main() { gl_FragColor = vec4(1.0); }`;"},
    ]
    body = f"""
import {{ locateSourceLine }} from './lib/glsl_audit.mjs';
console.log(JSON.stringify(locateSourceLine({json.dumps(files)}, 'float x = undefinedThing;')));
"""
    loc = run_node_json(body)
    assert loc == {"file": "src/shaders/a.js", "line": 3, "candidates": 1}
