"""Standalone scene shader files load locally and report their own source lines."""
from __future__ import annotations

import json
import subprocess

import pytest

from tests.scene_runtime.conftest import RUNTIME_JS, needs_browser, needs_node, run_node_json

SCENE = """import * as THREE from 'three';
export async function createScene({loaders}) {
  const text = new THREE.FileLoader(loaders.manager).setResponseType('text');
  const [vertexShader, fragment, common] = await Promise.all(
    ['panel.vert', 'panel.frag', 'palette.glsl'].map(file =>
      text.loadAsync(new URL('./shaders/' + file, import.meta.url).href)));
  const material = new THREE.ShaderMaterial({name:'StandaloneFiles', vertexShader,
    fragmentShader:common + '\\n' + fragment, uniforms:{uTime:{value:0}}, fog:false});
  const scene = new THREE.Scene();
  const geometry = new THREE.BoxGeometry(2,2,2);
  scene.add(new THREE.Mesh(geometry, material));
  return {scene, cameras:[{name:'probe',position:[3,2,4],lookAt:[0,0,0]}],
    update(t){material.uniforms.uTime.value=t;},
    dispose(){geometry.dispose();material.dispose();}};
}
"""
VERTEX = "varying vec2 vUv;\nvoid main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}\n"
COMMON = "vec3 panelColor(vec2 uv, float t){return vec3(uv,0.5+0.2*sin(t));}\n"
FRAGMENT = "uniform float uTime;\nvarying vec2 vUv;\nvoid main(){\n gl_FragColor=vec4(panelColor(vUv,uTime),1.0);\n}\n"


def stage(tmp_path, *, broken=False):
    shader = tmp_path / "src/shaders"
    shader.mkdir(parents=True)
    (tmp_path / "src/scene.js").write_text(SCENE)
    (shader / "panel.vert").write_text(VERTEX)
    (shader / "palette.glsl").write_text(COMMON)
    fragment = FRAGMENT.replace("panelColor(vUv,uTime)", "panelColor(vUv,uTime)*missingExternalFactor") if broken else FRAGMENT
    (shader / "panel.frag").write_text(fragment)
    return tmp_path


@pytest.mark.node
@needs_node
def test_shader_files_are_audited_without_becoming_javascript(tmp_path):
    root = stage(tmp_path)
    result = run_node_json(f"""
import {{staticShaderReport}} from './lib/shader_report.mjs';
import {{findSyntaxError}} from './lib/syntax_check.mjs';
import {{auditFile,locateSourceLine}} from './lib/glsl_audit.mjs';
const ws={json.dumps(str(root))};
const {{report,files}}=staticShaderReport(ws);
console.log(JSON.stringify({{report,syntax:findSyntaxError(ws+'/src'),
 selected:staticShaderReport(ws,'src/shaders/panel.frag').report,
 helper:auditFile('src/shaders/helper.glsl','float clock(){{return uTime;}}'),
 malformed:auditFile('src/shaders/bad.frag','#include <common> void main(){{}}'),
 location:locateSourceLine(files,'gl_FragColor=vec4(panelColor(vUv,uTime),1.0);')}}));
""")
    assert result["report"]["static"] == {"files": 4, "glsl_files": 3}
    assert result["report"]["errors"] == []
    assert result["selected"]["static"] == {"files": 1, "glsl_files": 1}
    assert result["selected"]["errors"] == []
    assert result["syntax"] is None
    assert result["helper"] == []  # A helper's uniforms may be declared in a different file.
    assert result["malformed"][0]["kind"] == "include_not_alone"
    assert result["location"] == {"file": "src/shaders/panel.frag", "line": 4, "candidates": 1}


@pytest.mark.node
@needs_browser
@pytest.mark.parametrize("broken", [False, True])
def test_real_gpu_compiles_external_sources_and_maps_failures(tmp_path, broken):
    root = stage(tmp_path, broken=broken)
    proc = subprocess.run(["node", str(RUNTIME_JS / "check_shaders.mjs"), "--ws", str(root)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == (1 if broken else 0), proc.stdout + proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["static"] == {"files": 4, "glsl_files": 3}
    assert report["compile"]["programs"] > 0
    if broken:
        failures = [item for item in report["errors"] if item["kind"] == "compile"]
        assert failures
        assert any(item["file"] == "src/shaders/panel.frag" and item["line"] == 4
                   and "missingExternalFactor" in item["message"] for item in failures), failures
    else:
        assert report["ok"] and report["errors"] == []


@pytest.mark.node
@needs_browser
@pytest.mark.parametrize("declaration", [True, False])
def test_external_uniform_header_can_be_joined_with_inline_glsl(tmp_path, declaration):
    root = stage(tmp_path)
    (root / "src/shaders/panel.frag").write_text("uniform float uTime;\n" if declaration else "// No declaration\n")
    scene = SCENE.replace("fragmentShader:common + '\\n' + fragment,",
        "fragmentShader:common + '\\n' + fragment + `\\nvoid main(){gl_FragColor=vec4(vec3(uTime),1.0);}`, ")
    assert scene != SCENE
    (root / "src/scene.js").write_text(scene)
    proc = subprocess.run(["node", str(RUNTIME_JS / "check_shaders.mjs"), "--ws", str(root)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == (0 if declaration else 1), proc.stdout + proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    if declaration:
        assert report["ok"] and report["errors"] == []
        assert any(item["kind"] == "undeclared_uniform" and item.get("validation") == "runtime_compile_passed"
                   for item in report["warnings"])
    else:
        assert any(item["kind"] == "compile" for item in report["errors"])
        assert any(item["kind"] == "undeclared_uniform" for item in report["errors"])


@pytest.mark.node
@needs_browser
@pytest.mark.parametrize("unused", ["other_file", "same_file", "duplicate_file"])
def test_valid_external_header_does_not_clear_unused_declaration_errors(tmp_path, unused):
    """GPU success proves the evaluated body, not every literal in the workspace."""
    root = stage(tmp_path)
    (root / "src/shaders/panel.frag").write_text("uniform float uTime;\n")
    body = "void main(){gl_FragColor=vec4(vec3(uTime),1.0);}"
    scene = SCENE.replace("fragmentShader:common + '\\n' + fragment,",
                          f"fragmentShader:common + '\\n' + fragment + `\\n{body}`, ")
    inactive = body if unused == "duplicate_file" else body.replace("vec3(uTime)", "vec3(uTime*.37)")
    unused_source = f"export const unusedFragment = `{inactive}`;\n"
    if unused == "same_file":
        scene += unused_source
        expected_file = "src/scene.js"
    else:
        expected_file = "src/shaders/unused.js"
        (root / expected_file).write_text(unused_source)
    (root / "src/scene.js").write_text(scene)
    proc = subprocess.run(["node", str(RUNTIME_JS / "check_shaders.mjs"), "--ws", str(root)],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["compile"]["programs"] > 0
    assert not any(item["kind"] == "compile" for item in report["errors"])
    assert any(item["kind"] == "undeclared_uniform" and item["file"] == expected_file
               for item in report["errors"]), report
    if unused == "other_file":
        assert any(item["kind"] == "undeclared_uniform" and item["file"] == "src/scene.js"
                   and item.get("validation") == "runtime_compile_passed"
                   for item in report["warnings"]), report
