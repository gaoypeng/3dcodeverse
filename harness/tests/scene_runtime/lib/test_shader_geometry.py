"""GPU evidence for transformed surface normals and shared shadow geometry."""
import json

import pytest

from tests.scene_runtime.lib._probe import compile_scene

pytestmark = pytest.mark.node


def test_cloned_and_merged_shader_sample_live_render_target_on_gpu():
    code, out = compile_scene("""
import * as THREE from 'three';
import {makeShaderMaterial,clonePatchedMaterial} from './lib/shader.js';
import {mergeStatic} from './lib/merge.js';
export function createScene({renderer}) {
 const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,10);
 camera.position.z=2;
 const textureTarget=new THREE.WebGLRenderTarget(8,8),readTarget=new THREE.WebGLRenderTarget(8,8);
 const sourceScene=new THREE.Scene();sourceScene.background=new THREE.Color(.2,.6,.9);
 const material=makeShaderMaterial({fog:false,toneMapped:false,
   uniforms:{uSample:{value:textureTarget.texture},uGain:{value:1}},
   fragmentHead:'uniform sampler2D uSample; uniform float uGain;',
   fragmentMain:'gl_FragColor=texture2D(uSample,vec2(.5))*vec4(vec3(uGain),1.0);'});
 const original=new THREE.Mesh(new THREE.PlaneGeometry(2,2),material);
 const clone=new THREE.Mesh(original.geometry,clonePatchedMaterial(material));
 const merged=mergeStatic([original],{variance:0});
 const previous=renderer.getRenderTarget(),expected=new Uint8Array(4),actual=new Uint8Array(4);
 try {
   renderer.setRenderTarget(textureTarget);renderer.render(sourceScene,camera);
   renderer.readRenderTargetPixels(textureTarget,4,4,1,1,expected);
   for(const mesh of [clone,merged]) {
     scene.add(mesh);renderer.setRenderTarget(readTarget);renderer.render(scene,camera);
     renderer.readRenderTargetPixels(readTarget,4,4,1,1,actual);scene.remove(mesh);
     if(actual.some((value,i)=>Math.abs(value-expected[i])>2))
       throw new Error('Lost cloned/merged render-target sampler: '+actual+' expected '+expected);
   }
   // The batch follows the source clock/control map after construction.
   material.uniforms.uGain.value=.5;scene.add(merged);renderer.render(scene,camera);
   renderer.readRenderTargetPixels(readTarget,4,4,1,1,actual);scene.remove(merged);
   if(actual.slice(0,3).some((value,i)=>Math.abs(value-expected[i]*.5)>2))
     throw new Error('Merged shader disconnected source controls: '+actual);
 } finally {
   renderer.setRenderTarget(previous);readTarget.dispose();textureTarget.dispose();
   merged.userData.dispose();clone.material.dispose();original.geometry.dispose();material.dispose();
 }
 return {scene,cameras:[{name:'a',position:[0,0,2],lookAt:[0,0,0],fov:45}]};
}
""", libs=("merge.js",))
    assert code == 0, out
    assert json.loads(out.strip().splitlines()[-1])["ok"]


def test_world_normals_match_inverse_transpose_on_gpu():
    code, out = compile_scene("""
import * as THREE from 'three';
import {patchStandard,worldBase,glslLocalDir} from './lib/shader.js';
export function createScene({renderer}) {
 const scene=new THREE.Scene();
 const material=new THREE.MeshStandardMaterial({side:THREE.DoubleSide,toneMapped:false});
 patchStandard(material,worldBase('normal-probe','probeP','probeN'));
 patchStandard(material,{name:'normal-output',uniforms:{uProbe:{value:0}},
   vertexHead:glslLocalDir('probeLocal',true)+'\\nvarying vec3 vProbeOffset;',
   vertexBody:'vProbeOffset=mat3(modelMatrix)*mat3(instanceMatrix)*probeLocal(vec3(.4,.1,-.3));',
   fragmentHead:'uniform float uProbe;\\nvarying vec3 vProbeOffset;',
   outputBody:'gl_FragColor=vec4(uProbe>0.5?abs(vProbeOffset-vec3(.4,.1,-.3))*100.0:normalize(vAstraWorldN)*0.5+0.5,1.0);'});
 const geometry=new THREE.PlaneGeometry(4,4);
 const mesh=new THREE.InstancedMesh(geometry,material,1);
 mesh.frustumCulled=false;mesh.matrixAutoUpdate=false;scene.add(mesh);
 const q=new THREE.Quaternion().setFromEuler(new THREE.Euler(.4,.7,-.25));
 const transforms=[new THREE.Matrix4(),
   new THREE.Matrix4().compose(new THREE.Vector3(),q,new THREE.Vector3(3,.5,1.2)),
   new THREE.Matrix4().makeShear(.4,-.2,.6,.1,-.3,.2),
   new THREE.Matrix4().compose(new THREE.Vector3(),q,new THREE.Vector3(-2,.7,1.5))];
 const target=new THREE.WebGLRenderTarget(16,16);
 const previous=renderer.getRenderTarget(),pixel=new Uint8Array(4);
 let maxError=0,maxOffsetError=0;
 try {
   for(const model of transforms) for(const instance of transforms) {
     mesh.matrix.copy(model);mesh.setMatrixAt(0,instance);mesh.instanceMatrix.needsUpdate=true;
     mesh.updateMatrixWorld(true);
     const matrix=new THREE.Matrix4().multiplyMatrices(model,instance);
     const expected=new THREE.Vector3(0,0,1).applyNormalMatrix(new THREE.Matrix3().getNormalMatrix(matrix));
     const camera=new THREE.PerspectiveCamera(35,1,.01,100);
     camera.position.copy(expected).multiplyScalar(12);camera.lookAt(0,0,0);
     material.userData.uniforms.uProbe.value=0;
     renderer.setRenderTarget(target);renderer.render(scene,camera);
     renderer.readRenderTargetPixels(target,8,8,1,1,pixel);
     for(let k=0;k<3;k++) maxError=Math.max(maxError,Math.abs(pixel[k]/255-(expected.getComponent(k)*.5+.5)));
     material.userData.uniforms.uProbe.value=1;renderer.render(scene,camera);
     renderer.readRenderTargetPixels(target,8,8,1,1,pixel);
     maxOffsetError=Math.max(maxOffsetError,pixel[0],pixel[1],pixel[2]);
   }
 } finally {renderer.setRenderTarget(previous);target.dispose();}
 if(maxError>.008) throw new Error('GPU inverse-transpose normal error: '+maxError);
 if(maxOffsetError>1) throw new Error('GPU world/local direction error: '+maxOffsetError);
 return {scene,cameras:[{name:'a',position:[6,3,8],lookAt:[0,0,0],fov:45}]};
}
""")
    assert code == 0, out
    assert json.loads(out.strip().splitlines()[-1])["ok"]
