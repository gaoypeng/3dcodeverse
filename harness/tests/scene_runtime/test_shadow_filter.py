"""The shared renderer must compile filtered shadows on the installed Three.

Checking the enum alone missed r182's removal of PCFSoftShadowMap from the
WebGLProgram mapping: that old enum compiled BASIC without a runtime error.
Inspect the actual linked fragment shader of a shadow-receiving material.
"""
import json
import subprocess

import pytest

from tests.scene_runtime.conftest import RUNTIME_JS, needs_browser


@pytest.mark.node
@needs_browser
def test_linked_gpu_program_uses_filtered_shadows(tmp_path):
    root = tmp_path / "scene"
    (root / "src").mkdir(parents=True)
    (root / "src/scene.js").write_text("""
import * as THREE from 'three';
export function createScene({renderer}) {
  const scene=new THREE.Scene();
  const sun=new THREE.DirectionalLight(0xffffff,3);
  sun.position.set(2,4,1);sun.castShadow=true;scene.add(sun);
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(),new THREE.MeshStandardMaterial());
  mesh.castShadow=true;mesh.receiveShadow=true;scene.add(mesh);
  window.shadowProbe=()=>renderer.info.programs.map(p=>
    renderer.getContext().getShaderSource(p.fragmentShader));
  return {scene,cameras:[{name:'a',position:[3,2,4],lookAt:[0,0,0],fov:45}],update(){}};
}
""")
    script = tmp_path / "probe.mjs"
    script.write_text(f"""
import {{openHost}} from {json.dumps((RUNTIME_JS / 'lib/host_page.mjs').as_uri())};
const host=await openHost({json.dumps(str(root))},{{width:128,height:128,post:false}});
try {{
 if(!host.boot.ok)throw new Error(host.boot.error);
 await host.page.evaluate(()=>window.__c3v.renderAt(window.__c3v.cameras()[0],0));
 const fragments=await host.page.evaluate(()=>window.shadowProbe());
 const receiving=fragments.filter(s=>s.includes('#define USE_SHADOWMAP'));
 console.log(JSON.stringify({{n:receiving.length,
   filtered:receiving.every(s=>s.includes('#define SHADOWMAP_TYPE_PCF')),
   basic:receiving.some(s=>s.includes('#define SHADOWMAP_TYPE_BASIC'))}}));
}} finally {{await host.close();}}
""")
    proc = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["n"] > 0 and result["filtered"] and not result["basic"], result
