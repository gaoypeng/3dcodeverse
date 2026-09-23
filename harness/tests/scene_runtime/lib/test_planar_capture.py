"""GPU behavior of addon-based planar captures and their shared state scope."""
from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene

_LIBS = ("water.js", "wetground.js", "ocean.js")


# ocean: its reflection once ran in override passes and clobbered the viewport.
@pytest.mark.parametrize("factory", ["wetground", "water", "ocean"])
def test_addon_capture_preserves_affine_projection_and_renderer_state(factory: str) -> None:
    script = r"""
import * as THREE from 'three';
import { makeMirrorFloor } from './lib/wetground.js';
import { makeOcean } from './lib/water.js';
import { makeOceanSurface } from './lib/ocean.js';
import { withRendererState } from './lib/shader.js';
export function createScene({renderer}) {
 const kind='FACTORY';
 const scene=new THREE.Scene();scene.background=new THREE.Color(0x667788);
 const object=kind==='wetground'?makeMirrorFloor(8,8,{rttSize:64,detail:0,ripple:0})
  :kind==='ocean'?makeOceanSurface({width:8,depth:8,segments:16,reflectionSize:64}):makeOcean(8,8,{rttSize:64,distortionScale:0});
 const surface=kind==='wetground'?object.getObjectByName('MirrorSurface'):kind==='ocean'?object.userData.surface:object;
 // Caller attachments include a mesh, a camera, a bone hierarchy, and a
 // manually managed world transform; no capture may take ownership of them.
 const attached=new THREE.Mesh(new THREE.BoxGeometry(.2,.3,.4),new THREE.MeshBasicMaterial());
 attached.position.set(.3,.4,.5);surface.add(attached);
 const attachedCamera=new THREE.PerspectiveCamera();attachedCamera.position.set(.2,.7,.1);attached.add(attachedCamera);
 const bone=new THREE.Bone(),tip=new THREE.Bone();bone.position.set(.1,.2,.3);tip.position.y=.8;
 attached.add(bone);bone.add(tip);
 const manual=new THREE.Object3D();manual.matrixAutoUpdate=false;manual.matrixWorldAutoUpdate=false;
 manual.matrixWorld.makeTranslation(9,8,7);attached.add(manual);
 const attachments=[attached,attachedCamera,bone,tip,manual];
 const attachmentMatrices=()=>JSON.stringify(attachments.map(o=>({world:o.matrixWorld.toArray(),
  inverse:o.isCamera?o.matrixWorldInverse.toArray():null})));
 const attachmentFlags=()=>attachments.map(o=>[o.matrixAutoUpdate,o.matrixWorldAutoUpdate,o.matrixWorldNeedsUpdate]);
 const child=new THREE.Group(),parent=new THREE.Group();child.add(object);parent.add(child);scene.add(parent);
 const marker=new THREE.Mesh(new THREE.BoxGeometry(.8,.8,.8),new THREE.MeshBasicMaterial({color:0xeeaa22}));scene.add(marker);
 const camera=new THREE.PerspectiveCamera(45,1,.1,100),expectedCamera=new THREE.PerspectiveCamera();
 const originalRender=renderer.render,originalSetTarget=renderer.setRenderTarget;
 const originalRatio=renderer.getPixelRatio();
 const cube=new THREE.WebGLCubeRenderTarget(32,{generateMipmaps:true,minFilter:THREE.LinearMipmapLinearFilter});
 cube.viewport.set(2,3,12,11);cube.scissor.set(3,4,9,8);cube.scissorTest=true;
 const state=()=>{
  const gl=renderer.getContext();return {
   target:renderer.getRenderTarget()===cube?'cube':renderer.getRenderTarget()?'other':'canvas',
   face:renderer.getActiveCubeFace(),mip:renderer.getActiveMipmapLevel(),
   viewport:renderer.getCurrentViewport(new THREE.Vector4()).toArray(),glViewport:Array.from(gl.getParameter(gl.VIEWPORT)),
   logicalViewport:renderer.getViewport(new THREE.Vector4()).toArray(),logicalScissor:renderer.getScissor(new THREE.Vector4()).toArray(),
   scissor:Array.from(gl.getParameter(gl.SCISSOR_BOX)),scissorTest:gl.isEnabled(gl.SCISSOR_TEST),logicalTest:renderer.getScissorTest(),
   xr:renderer.xr.enabled,shadow:renderer.shadowMap.autoUpdate,tone:renderer.toneMapping,
   clear:renderer.getClearColor(new THREE.Color()).toArray(),alpha:renderer.getClearAlpha(),visible:surface.visible,
   attachments:attachmentMatrices(),attachmentFlags:attachmentFlags(),
   matrix:surface.matrixWorld.toArray(),autoUpdate:surface.matrixWorldAutoUpdate,needsUpdate:surface.matrixWorldNeedsUpdate,
   targetViewport:cube.viewport.toArray(),targetScissor:cube.scissor.toArray(),targetTest:cube.scissorTest,
   setTargetIdentity:renderer.setRenderTarget===originalSetTarget,
  };
 };
 try {
  withRendererState(renderer,()=>{
   for(const transform of [0,1,2]){
    parent.scale.set(transform===2?-2:2,transform?.4:2,transform?1.6:2);
    parent.rotation.set(transform?.1:0,transform?.5:0,transform?.2:0);parent.position.set(2,1,3);
    child.rotation.set(transform?.35:0,transform?.2:0,transform?-.25:0);scene.updateMatrixWorld(true);
    const world=surface.matrixWorld.clone(),normal=new THREE.Vector3(0,0,1).applyMatrix3(new THREE.Matrix3().getNormalMatrix(world)).normalize();
    const center=new THREE.Vector3().setFromMatrixPosition(world),tangent=new THREE.Vector3(1,0,0).transformDirection(world);
    camera.position.copy(center).addScaledVector(normal,4).addScaledVector(tangent,2);camera.lookAt(center);camera.updateMatrixWorld(true);
    marker.position.copy(center).addScaledVector(normal,1.2);scene.updateMatrixWorld(true);
    const reflect=p=>p.clone().addScaledVector(normal,-2*p.clone().sub(center).dot(normal));
    expectedCamera.position.copy(reflect(camera.position));
    expectedCamera.up.copy(camera.up).transformDirection(camera.matrixWorld).reflect(normal);
    expectedCamera.lookAt(reflect(camera.position.clone().add(camera.getWorldDirection(new THREE.Vector3()))));expectedCamera.updateMatrixWorld(true);
    attached.matrixWorldNeedsUpdate=true;
    const expectedAttachments=attachmentMatrices();
    let actualCamera=null,captures=0;
    renderer.render=function(s,c){
     captures++;actualCamera=c.position.clone();
     if(captures>1)throw Error('Planar callback recursed');
     surface.onBeforeRender(this,s,c); // The explicit re-entry must be inert.
     const result=originalRender.call(this,s,c); // Actual GPU render updates scene matrices.
     if(attachmentMatrices()!==expectedAttachments)throw Error('Nested capture changed caller attachment matrices');
     return result;
    };
    const before=state();surface.onBeforeRender(renderer,scene,camera);
    if(captures!==1||actualCamera.distanceTo(expectedCamera.position)>1e-10)throw Error('Wrong transformed mirror camera');
    if(JSON.stringify(before)!==JSON.stringify(state()))throw Error('Capture changed affine matrix or renderer state');
    const bias=new THREE.Matrix4().set(.5,0,0,.5,0,.5,0,.5,0,0,.5,.5,0,0,0,1);
    const expected=bias.multiply(camera.projectionMatrix).multiply(expectedCamera.matrixWorldInverse);
    const actual=surface.material.uniforms.textureMatrix.value;
    for(const point of [[0,0,0],[1,0,0],[0,1,0],[-2,.7,0],[.4,-1.3,0]]){
     const local=new THREE.Vector4(...point,1),wp=local.clone().applyMatrix4(world);
     const a=(kind==='water'?wp:local).clone().applyMatrix4(actual),b=wp.clone().applyMatrix4(expected);
     if(Math.abs(a.x/a.w-b.x/b.w)>1e-10||Math.abs(a.y/a.w-b.y/b.w)>1e-10)throw Error('Wrong local/world reflection projection');
    }
    renderer.render=originalRender;
   }
   parent.scale.set(1,1,1);parent.rotation.set(0,0,0);parent.position.set(0,0,0);child.rotation.set(0,0,0);scene.updateMatrixWorld(true);
   camera.position.set(0,3,5);camera.lookAt(0,0,0);camera.updateMatrixWorld(true);
   for(const ratio of [1,2])for(const override of [false,true])for(const throws of [false,true]){
    renderer.setPixelRatio(ratio);renderer.setRenderTarget(cube,3,1);
    if(override){renderer.setViewport(4,5,10,9);renderer.setScissor(5,6,7,6);renderer.setScissorTest(false);}
    renderer.xr.enabled=true;renderer.shadowMap.autoUpdate=true;surface.visible=true;
    renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.setClearColor(0x123456,.37);
    attached.matrixWorldNeedsUpdate=true;
    const before=state();let caught=false,captures=0;
    renderer.render=function(...args){
     captures++;const result=originalRender.apply(this,args);
     if(attachmentMatrices()!==before.attachments)throw Error('Nested capture changed attachments before success/failure');
     if(throws){this.toneMapping=THREE.NoToneMapping;this.setClearColor(0xffffff,0);throw Error('injected capture failure');}
     return result;
    };
    try{surface.onBeforeRender(renderer,scene,camera);}catch(e){if(e.message!=='injected capture failure')throw e;caught=true;}
    if(captures!==1||caught!==throws||JSON.stringify(before)!==JSON.stringify(state()))throw Error('Planar capture leaked state '+JSON.stringify({ratio,override,throws,before,after:state()}));
    renderer.render=originalRender;
   }
   let captures=0;renderer.render=()=>{captures++;};
   scene.overrideMaterial=new THREE.MeshNormalMaterial();surface.onBeforeRender(renderer,scene,camera);
   scene.overrideMaterial.dispose();scene.overrideMaterial=null;
   if(captures!==0)throw Error('Override pass performed colour capture');
   const originalMaterial=surface.material,foreignMaterial=new THREE.MeshNormalMaterial();
   surface.material=foreignMaterial;surface.onBeforeRender(renderer,scene,camera);
   surface.material=originalMaterial;surface.onBeforeRender(renderer,scene,camera,surface.geometry,foreignMaterial);
   foreignMaterial.dispose();
   if(captures!==0)throw Error('Material replacement pass performed colour capture');
   const projection=surface.material.uniforms.textureMatrix.value.clone();
   camera.position.set(0,-3,5);camera.lookAt(0,0,0);camera.updateMatrixWorld(true);
   surface.onBeforeRender(renderer,scene,camera);surface.onBeforeRender(renderer,scene,camera);
   if(captures!==0||!projection.equals(surface.material.uniforms.textureMatrix.value))throw Error('Back-face skip changed cached projection');
  });
 } finally {
  renderer.render=originalRender;renderer.setPixelRatio(originalRatio);cube.dispose();
  let callerDisposals=0;
  attached.geometry.addEventListener('dispose',()=>callerDisposals++);
  attached.material.addEventListener('dispose',()=>callerDisposals++);
  object.userData.dispose();
  if(callerDisposals)throw Error('Factory disposed caller attachments');
  attached.geometry.dispose();attached.material.dispose();
  marker.geometry.dispose();marker.material.dispose();
 }
 return {scene:new THREE.Scene(),cameras:[{name:'probe',position:[0,3,5],lookAt:[0,0,0]}]};
}
""".replace("FACTORY", factory)
    code, output = compile_scene(script, _LIBS, audit_module="src/scene.js")
    assert code == 0, output


def test_renderer_state_scope_is_nested_and_keeps_callback_result_and_exception() -> None:
    code, output = compile_scene(r"""
import * as THREE from 'three';
import {withRendererState} from './lib/shader.js';
export function createScene({renderer}) {
 const old=renderer.getRenderTarget();
 const a=new THREE.WebGLRenderTarget(32,32),b=new THREE.WebGLRenderTarget(16,16);
 const read=()=>JSON.stringify({target:renderer.getRenderTarget()?.uuid||null,viewport:renderer.getCurrentViewport(new THREE.Vector4()).toArray(),scissor:renderer.getScissor(new THREE.Vector4()).toArray(),alpha:renderer.getClearAlpha(),tone:renderer.toneMapping});
 try{
  renderer.setRenderTarget(a);renderer.setViewport(1,2,23,24);renderer.setScissor(3,4,17,18);
  const initial=read();
  const result=withRendererState(renderer,()=>{
   renderer.setRenderTarget(b);renderer.setViewport(2,3,8,9);renderer.setScissor(1,2,7,8);renderer.setClearAlpha(.41);
   const middle=read(),error=new Error('nested failure');let caught;
   try{withRendererState(renderer,()=>{renderer.setRenderTarget(null);renderer.setClearAlpha(.17);renderer.toneMapping=THREE.NoToneMapping;throw error;});}catch(e){caught=e;}
   if(caught!==error||read()!==middle)throw Error('Nested scope did not restore its own entry state');
   return 37;
  });
  if(result!==37||read()!==initial)throw Error('Outer scope result or state changed');
 }finally{renderer.setRenderTarget(old);a.dispose();b.dispose();}
 return {scene:new THREE.Scene(),cameras:[{name:'probe',position:[0,3,5],lookAt:[0,0,0]}]};
}
""", audit_module="src/scene.js")
    assert code == 0, output
