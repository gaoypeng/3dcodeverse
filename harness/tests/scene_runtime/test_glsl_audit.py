"""Static GLSL audits + source-line mapping (node-side pure functions)."""

from __future__ import annotations

import json

import pytest

from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]


def audit(source: str, **opts) -> list[dict]:
    body = f"""
import {{ auditFile }} from './lib/glsl_audit.mjs';
const src = {json.dumps(source)};
console.log(JSON.stringify(auditFile('src/shaders/x.js', src, {json.dumps(opts)})));
"""
    return run_node_json(body)


def kinds(findings: list[dict]) -> set[str]:
    return {f["kind"] for f in findings}


def test_clean_shader_has_no_findings():
    src = """
const frag = `
uniform float uTime;
varying vec2 vUv;
void main() {
  gl_FragColor = vec4(vUv, sin(uTime), 1.0);
}`;
export const mat = { uniforms: { uTime: { value: 0 } }, fragmentShader: frag };
"""
    assert audit(src) == []


def test_include_not_alone_and_version_and_precision():
    src = """const vs = `
#version 300 es
precision highp float;
#include <common> uniform float uTime;
void main() { gl_Position = vec4(position, 1.0); }`;
const u = { uTime: { value: 0 } };"""
    f = audit(src)
    k = kinds(f)
    assert {"include_not_alone", "version_directive", "precision_directive"} <= k
    inc = next(x for x in f if x["kind"] == "include_not_alone")
    assert inc["line"] == 4  # line 1 is `const vs = \``, the include is on file line 4
    assert inc["severity"] == "error"
    assert next(x for x in f if x["kind"] == "version_directive")["severity"] == "error"


def test_fragment_out_plus_gl_fragcolor_and_glsl3():
    src = """import * as THREE from 'three';
const frag = `
out vec4 fragColor;
void main() { gl_FragColor = vec4(1.0); }`;
new THREE.ShaderMaterial({ glslVersion: THREE.GLSL3, fragmentShader: frag });"""
    k = kinds(audit(src))
    assert "fragment_out_and_gl_fragcolor" in k
    assert "glsl3_gl_fragcolor" in k


def test_utime_undeclared_and_unbound():
    src = """const frag = `
void main() { gl_FragColor = vec4(sin(uTime)); }`;"""
    f = audit(src)
    assert {"undeclared_uniform", "unbound_uniform"} <= kinds(f)
    # declared + bound → clean
    src2 = """const frag = `
uniform float uTime;
void main() { gl_FragColor = vec4(sin(uTime)); }`;
mat.uniforms.uTime = { value: 0 };"""
    assert kinds(audit(src2)) == set()


def test_chunk_dropped_warning_only_when_include_missing():
    good = """m.onBeforeCompile = (s) => { s.fragmentShader = s.fragmentShader.replace('#include <output_fragment>', `#include <output_fragment>
gl_FragColor.rgb += vec3(0.1);`); };"""
    bad = """m.onBeforeCompile = (s) => { s.vertexShader = s.vertexShader.replace('#include <begin_vertex>', `vec3 transformed = position * 2.0;`); };"""
    assert kinds(audit(good)) == set()
    f = audit(bad)
    assert kinds(f) == {"chunk_dropped"}
    assert f[0]["severity"] == "warn"


def test_no_fog_warning_respects_scene_fog_and_opt_out():
    src = """import * as THREE from 'three';
const frag = `
void main() { gl_FragColor = vec4(1.0); }`;
export const m = new THREE.ShaderMaterial({ fragmentShader: frag });"""
    assert "no_fog" not in kinds(audit(src, sceneUsesFog=False))
    assert "no_fog" in kinds(audit(src, sceneUsesFog=True))
    assert "no_fog" not in kinds(audit(src + "\n// c3v: no-fog", sceneUsesFog=True))


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
