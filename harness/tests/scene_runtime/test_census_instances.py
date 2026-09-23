"""Scene budgets include shader-instanced geometry and respect hidden parents."""
import pytest

from codeverse3d.config import get_settings
from tests.scene_runtime.conftest import needs_node, run_node_json
from tests.scene_runtime.lib._probe import compile_scene

pytestmark = [pytest.mark.node, needs_node]
RUNTIME_JS = get_settings().runtime_js_dir()


def test_shader_instances_capacity_draw_range_and_hidden_ancestors():
    out = run_node_json("""
import * as THREE from 'three';
import {sceneCensus} from './lib/host_census.mjs';
import {geometryTriangles,geometryInstances} from './lib/census.mjs';
const scene=new THREE.Scene(),zone=new THREE.Group();scene.add(zone);
const geo=new THREE.InstancedBufferGeometry().copy(new THREE.PlaneGeometry());
geo.instanceCount=Infinity;
geo.setAttribute('aOffset',new THREE.InstancedBufferAttribute(new Float32Array(12),3,false,2));
const cards=new THREE.Mesh(geo,new THREE.MeshBasicMaterial());cards.name='Cards';zone.add(cards);
const full=sceneCensus(scene,THREE).totals;geo.instanceCount=3;
const limited=sceneCensus(scene,THREE).totals;geo.setDrawRange(3,99);
const range=sceneCensus(scene,THREE).totals;geo.setDrawRange(10,99);const empty=geometryTriangles(geo);
const points=new THREE.Points(new THREE.BoxGeometry(),new THREE.PointsMaterial());zone.add(points);
const lines=new THREE.LineSegments(new THREE.BoxGeometry(),new THREE.LineBasicMaterial());zone.add(lines);
const nonTriangles=sceneCensus(scene,THREE).totals.triangles;
zone.visible=false;const hidden=sceneCensus(scene,THREE).totals;
const instanced=new THREE.InstancedMesh(new THREE.BoxGeometry(),new THREE.MeshBasicMaterial(),7);
instanced.count=2;
console.log(JSON.stringify({full,limited,range,empty,nonTriangles,hidden,matrix:geometryInstances(instanced)}));
""".replace("'./lib/", f"'{RUNTIME_JS}/lib/"))
    assert out["full"]["instances"] == 8 and out["full"]["triangles"] == 16
    assert out["limited"]["instances"] == 3 and out["limited"]["triangles"] == 6
    assert out["range"]["triangles"] == 3 and out["empty"] == 0
    assert out["nonTriangles"] == 0 and out["matrix"] == 2
    assert out["hidden"]["meshes"] == out["hidden"]["instances"] == out["hidden"]["triangles"] == 0


def test_census_matches_actual_gpu_instanced_triangle_submission():
    code, output = compile_scene("""
import * as THREE from 'three';
import {sceneCensus} from '/__runtime/lib/host_census.mjs';
export function createScene({renderer}) {
 const scene=new THREE.Scene(),geo=new THREE.InstancedBufferGeometry().copy(new THREE.PlaneGeometry());
 geo.instanceCount=Infinity;
 geo.setAttribute('aOffset',new THREE.InstancedBufferAttribute(new Float32Array(12),3,false,2));
 const material=new THREE.ShaderMaterial({vertexShader:
 'attribute vec3 aOffset;void main(){gl_Position=projectionMatrix*modelViewMatrix*vec4(position+aOffset,1.0);}',
 fragmentShader:'void main(){gl_FragColor=vec4(.3,.6,.8,1.0);}'});
 scene.add(new THREE.Mesh(geo,material));
 const camera=new THREE.PerspectiveCamera(45,1,.01,100);camera.position.z=3;
 const target=new THREE.WebGLRenderTarget(32,32),previous=renderer.getRenderTarget();
 try {
  for(const start of [0,3]) {
   geo.setDrawRange(start,Infinity);
   const census=sceneCensus(scene,THREE).totals;
   renderer.setRenderTarget(target);renderer.render(scene,camera);
   if(census.triangles!==renderer.info.render.triangles || census.instances!==8)
    throw new Error(JSON.stringify({census,render:renderer.info.render}));
  }
 } finally {renderer.setRenderTarget(previous);target.dispose();}
 return {scene,cameras:[{name:'a',position:[0,0,3],lookAt:[0,0,0]}]};
}
""", audit_module="src/scene.js")
    assert code == 0, output


def test_bound_gpu_capacity_ignores_unused_short_instanced_attributes():
    """Only attributes used by the linked program constrain Three's draw."""
    code, output = compile_scene("""
import * as THREE from 'three';
import {sceneCensus} from '/__runtime/lib/host_census.mjs';
export function createScene({renderer}) {
 const scene=new THREE.Scene(),geo=new THREE.InstancedBufferGeometry().copy(new THREE.PlaneGeometry());
 geo.setAttribute('activeOffset',new THREE.InstancedBufferAttribute(new Float32Array(24),3));
 geo.setAttribute('unusedShort',new THREE.InstancedBufferAttribute(new Float32Array(3),3));
 const material=new THREE.ShaderMaterial({vertexShader:
 'attribute vec3 activeOffset;void main(){gl_Position=projectionMatrix*modelViewMatrix*vec4(position+activeOffset,1.0);}',
 fragmentShader:'void main(){gl_FragColor=vec4(.3,.6,.8,1.0);}'});
 const mesh=new THREE.Mesh(geo,material);mesh.frustumCulled=false;scene.add(mesh);
 const camera=new THREE.PerspectiveCamera(45,1,.01,100);camera.position.z=3;
 const target=new THREE.WebGLRenderTarget(32,32),previous=renderer.getRenderTarget();
 try {
  for(const requested of [8,3,Infinity]) {
   geo.instanceCount=requested;
   renderer.setRenderTarget(target);renderer.render(scene,camera);
   const census=sceneCensus(scene,THREE).totals,expected=Math.min(requested,8);
   if(geo._maxInstanceCount!==8 || census.instances!==expected ||
      census.triangles!==renderer.info.render.triangles || census.triangles!==expected*2)
    throw new Error(JSON.stringify({requested,census,capacity:geo._maxInstanceCount,render:renderer.info.render}));
  }
 } finally {renderer.setRenderTarget(previous);target.dispose();}
 return {scene,cameras:[{name:'a',position:[0,0,3],lookAt:[0,0,0]}]};
}
""", audit_module="src/scene.js")
    assert code == 0, output
