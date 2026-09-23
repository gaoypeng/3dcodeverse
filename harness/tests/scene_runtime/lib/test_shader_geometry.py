"""GPU evidence for transformed surface normals and shared shadow geometry."""
import json

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure

pytestmark = pytest.mark.node


def test_override_guard_preserves_callbacks_material_arrays_and_draw_range():
    out = measure("""
import * as THREE from 'three';
import {keepOutOfDepthPasses} from './lib/shader.js';
const geometry=new THREE.BoxGeometry();geometry.setDrawRange(3,12);
const materials=[new THREE.MeshStandardMaterial(),new THREE.MeshStandardMaterial()];
const mesh=new THREE.Mesh(geometry,materials),foreign=new THREE.MeshNormalMaterial();
let before=0,after=0,correctThis=true,restoredAtCallback=true;
mesh.onBeforeRender=function(){before++;correctThis&&=this===mesh;};
mesh.onAfterRender=function(){after++;correctThis&&=this===mesh;restoredAtCallback&&=geometry.drawRange.count===12;};
keepOutOfDepthPasses(mesh);keepOutOfDepthPasses(mesh);
const args=[null,null,null,geometry,foreign];
mesh.onBeforeRender(...args);const excluded=geometry.drawRange.count===0;
mesh.onAfterRender(...args);
args[4]=materials[1];mesh.onBeforeRender(...args);const arrayVisible=geometry.drawRange.count===12;mesh.onAfterRender(...args);
mesh.material=new THREE.MeshBasicMaterial();args[4]=mesh.material;
mesh.onBeforeRender(...args);const replacementVisible=geometry.drawRange.count===12;mesh.onAfterRender(...args);
console.log(JSON.stringify({before,after,correctThis,restoredAtCallback,excluded,arrayVisible,replacementVisible,range:geometry.drawRange}));
""")
    assert out == {"before": 3, "after": 3, "correctThis": True,
                   "restoredAtCallback": True, "excluded": True,
                   "arrayVisible": True, "replacementVisible": True,
                   "range": {"start": 3, "count": 12}}


def test_patched_clone_keeps_typed_uniforms_and_borrows_textures():
    out = measure("""
import * as THREE from 'three';
import {patchStandard,clonePatchedMaterial} from './lib/shader.js';
const texture=new THREE.WebGLRenderTarget(4,4).texture;
const original=new THREE.MeshStandardMaterial({map:texture});
patchStandard(original,{name:'typed-patch',uniforms:{
 uOffset:{value:new THREE.Vector3(1,2,3)},uSample:{value:texture},
 uColors:{value:[new THREE.Color(.2,.3,.4)]},uWeights:{value:new Float32Array([1,2])}},
 fragmentHead:'uniform vec3 uOffset;',fragmentBody:'diffuseColor.rgb *= uOffset;'});
original.userData.shared=true;
const clone=clonePatchedMaterial(original),a=original.userData.uniforms,b=clone.userData.uniforms;
b.uOffset.value.x=8;b.uColors.value[0].r=.9;b.uWeights.value[0]=9;
patchStandard(clone,{name:'second',fragmentBody:'diffuseColor.rgb *= 0.5;'});
const shader={vertexShader:'void main(){\\n#include <begin_vertex>\\n}',
 fragmentShader:'void main(){\\n#include <color_fragment>\\n}',uniforms:{}};
clone.onBeforeCompile(shader);
console.log(JSON.stringify({typed:b.uOffset.value.isVector3&&b.uColors.value[0].isColor,
 old:[a.uOffset.value.x,a.uColors.value[0].r,a.uWeights.value[0]],
 texture:b.uSample.value===texture&&clone.map===texture,
 chain:original.userData.astraPatches.length,cloneChain:clone.userData.astraPatches.length,
 applied:shader.fragmentShader.includes('diffuseColor.rgb *= uOffset;')&&shader.fragmentShader.includes('diffuseColor.rgb *= 0.5;'),
 shared:!!clone.userData.shared,sourceShared:original.userData.shared}));
""")
    assert out == {"typed": True, "old": [1, 0.2, 1], "texture": True,
                   "chain": 1, "cloneChain": 2, "applied": True,
                   "shared": False, "sourceShared": True}


def test_shader_clone_borrows_samplers_and_preserves_uniform_sharing():
    out = measure("""
import * as THREE from 'three';
import {makeShaderMaterial,clonePatchedMaterial} from './lib/shader.js';
const target=new THREE.WebGLRenderTarget(4,4),texture=new THREE.Texture();
const results=[],warnings=[];console.warn=(...args)=>warnings.push(args.join(' '));
for(const factory of [opts=>new THREE.ShaderMaterial(opts),
    opts=>new THREE.RawShaderMaterial(opts),opts=>makeShaderMaterial(opts)]) {
 const uniforms={uTarget:{value:target.texture},uMap:{value:texture},
   uNested:{value:{samples:[target.texture,texture],offset:new THREE.Vector3(1,2,3)}},
   uWeights:{value:new Float32Array([1,2])}};
 const original=factory({uniforms});original.userData.uniforms=original.uniforms;
 const clone=clonePatchedMaterial(original),shared=clonePatchedMaterial(original,{shareUniforms:true});
 const source=original.uniforms,copy=clone.uniforms;
 copy.uNested.value.offset.x=7;copy.uWeights.value[0]=8;
 results.push({type:clone.type===original.type,
   borrowed:copy.uTarget.value===target.texture&&copy.uMap.value===texture&&
     copy.uNested.value.samples[0]===target.texture&&copy.uNested.value.samples[1]===texture,
   independent:copy!==source&&copy.uTarget!==source.uTarget&&
     copy.uNested.value.offset.isVector3&&source.uNested.value.offset.x===1&&source.uWeights.value[0]===1,
   alias:clone.userData.uniforms===copy,
   shared:shared.uniforms===source&&shared.userData.uniforms===source,
   intact:source.uTarget.value===target.texture&&source.uMap.value===texture});
 clone.dispose();shared.dispose();original.dispose();
}
target.dispose();texture.dispose();console.log(JSON.stringify({results,warnings}));
""")
    assert out == {"results": [dict.fromkeys(
        ["type", "borrowed", "independent", "alias", "shared", "intact"], True)] * 3,
        "warnings": []}


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


def test_depth_and_distance_share_clock_displacement_and_alpha_silhouette():
    out = measure("""
import * as THREE from 'three';
import {patchStandard,shadowLike,tickShaders} from './lib/shader.js';
const mesh=new THREE.Mesh(new THREE.PlaneGeometry(),new THREE.MeshStandardMaterial());
const head='varying vec2 vCut;';
const body='vCut=uv; transformed.x += sin(uTime);';
patchStandard(mesh.material,{name:'sway',vertexHead:head,vertexBody:body});
shadowLike(mesh,'sway-shadow',head,body,{fragmentHead:head,
 fragmentBody:'if(length(vCut-0.5)>0.4) discard;'});
tickShaders(mesh,2.5);
const compiled=[['depth',mesh.customDepthMaterial],['distance',mesh.customDistanceMaterial]]
 .map(([key,material])=>{
   const template=THREE.ShaderLib[key];
   const shader={vertexShader:template.vertexShader,fragmentShader:template.fragmentShader,uniforms:{}};
   material.onBeforeCompile(shader);
   return {time:material.userData.uniforms.uTime.value,
     shared:material.userData.uniforms===mesh.material.userData.uniforms,
     displaced:shader.vertexShader.includes('transformed.x += sin(uTime);'),
     cut:shader.fragmentShader.includes('if(length(vCut-0.5)>0.4) discard;'),
     beforeAlpha:shader.fragmentShader.indexOf('if(length(vCut-0.5)>0.4) discard;')<shader.fragmentShader.indexOf('#include <alphatest_fragment>')};
 });
console.log(JSON.stringify({compiled,depth:mesh.customDepthMaterial.isMeshDepthMaterial,
 distance:mesh.customDistanceMaterial.isMeshDistanceMaterial}));
""")
    assert out["depth"] and out["distance"]
    assert out["compiled"] == [{"time": 2.5, "shared": True, "displaced": True,
                                 "cut": True, "beforeAlpha": True}] * 2


def test_point_light_shadow_displacement_and_cut_compile_on_gpu():
    code, out = compile_scene("""
import * as THREE from 'three';
import {patchStandard,shadowLike,tickShaders} from './lib/shader.js';
export function createScene() {
 const scene=new THREE.Scene();scene.background=new THREE.Color(0x203040);
 const light=new THREE.PointLight(0xffffff,60,20);light.position.set(2,4,3);light.castShadow=true;scene.add(light);
 const material=new THREE.MeshStandardMaterial({side:THREE.DoubleSide});
 const head='varying vec2 vCut;';
 const body='vCut=uv; transformed.x += .3*sin(uTime+position.y);';
 const cut='if(length(vCut-0.5)>0.4) discard;';
 patchStandard(material,{name:'point-cut',vertexHead:head,vertexBody:body,fragmentHead:head,alphaBody:cut});
 const mesh=new THREE.Mesh(new THREE.PlaneGeometry(2,2,8,8),material);mesh.position.y=1.5;
 shadowLike(mesh,'point-shadow',head,body,{fragmentHead:head,fragmentBody:cut});scene.add(mesh);
 const floor=new THREE.Mesh(new THREE.PlaneGeometry(12,12),new THREE.MeshStandardMaterial());
 floor.rotation.x=-Math.PI/2;floor.receiveShadow=true;scene.add(floor);
 return {scene,cameras:[{name:'a',position:[4,3,6],lookAt:[0,1,0],fov:45}],update(t){tickShaders(scene,t);}};
}
""", audit_module="src/scene.js")
    assert code == 0, out


def test_transmission_hook_runs_after_maps_and_survives_clone_on_gpu():
    code, out = compile_scene("""
import * as THREE from 'three';
import {patchStandard,clonePatchedMaterial} from './lib/shader.js';
export function createScene({renderer}) {
 const scene=new THREE.Scene();
 const map=new THREE.DataTexture(new Uint8Array([64,128,255,255]),1,1);map.needsUpdate=true;
 const original=new THREE.MeshPhysicalMaterial({transmission:.8,thickness:2,
   transmissionMap:map,thicknessMap:map,toneMapped:false});
 patchStandard(original,{name:'optical-add',transmissionBody:
   'material.thickness += .2; material.transmission += .1;'});
 patchStandard(original,{name:'optical-scale',transmissionBody:
   'material.thickness *= .5; material.transmission *= .5;',
   outputBody:'gl_FragColor=vec4(material.thickness*.5,material.transmission,0.,1.);'});
 const mesh=new THREE.Mesh(new THREE.PlaneGeometry(4,4),original);scene.add(mesh);
 const camera=new THREE.PerspectiveCamera(40,1,.01,100);camera.position.z=5;
 const target=new THREE.WebGLRenderTarget(16,16),previous=renderer.getRenderTarget();
 const clone=clonePatchedMaterial(original),pixel=new Uint8Array(4);
 try {
  for(const material of [original,clone]) {
   mesh.material=material;renderer.setRenderTarget(target);renderer.render(scene,camera);
   renderer.readRenderTargetPixels(target,8,8,1,1,pixel);
   const expected=[(2*128/255+.2)*.25,(.8*64/255+.1)*.5,0];
   if(expected.some((v,i)=>Math.abs(pixel[i]/255-v)>.006))
    throw new Error('Optical map/patch order or cloned chain failed: '+Array.from(pixel));
  }
 } finally {renderer.setRenderTarget(previous);target.dispose();}
 return {scene,cameras:[{name:'a',position:[0,0,5],lookAt:[0,0,0],fov:40}]};
}
""", audit_module="src/scene.js")
    assert code == 0, out
