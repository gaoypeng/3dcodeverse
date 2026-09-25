"""Numerical regressions for world wind and normals of deformed cloth."""
from __future__ import annotations

import re

import pytest

from tests.scene_runtime.lib._probe import LIB_DIR, compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("grass.js", "flowers.js", "smalllife.js", "canopy.js", "cloth.js",
         "foliage_shade.js", "finish.js")

# Run the production material's actual vertex patch, with constant per-plant
# attributes across a screen quad. Float readback measures positions/normals
# independently of rasterized blade size, lighting, or camera-facing widths.
_VERTEX_PROBE = """
function vertexProbe(source, renderer, scene, camera, target) {
 const compiled = {uniforms:{}, fragmentShader:'void main() {}', vertexShader:`
   uniform vec3 uProbePoint, uProbeRoot;
   uniform float uProbeNormal;
   varying vec3 vNormal, vProbe;
   void main() {
     vNormal=vec3(0.,0.,1.);
     #include <begin_vertex>
     vProbe=uProbeNormal>.5 ? vNormal : (modelMatrix*vec4(transformed,1.)).xyz-uProbeRoot;
     gl_Position=vec4(position.xy,0.,1.);
   }`};
 source.material.onBeforeCompile(compiled);
 const uniforms={...compiled.uniforms,uProbePoint:{value:new THREE.Vector3()},
   uProbeRoot:{value:new THREE.Vector3()},uProbeNormal:{value:0}};
 const material=new THREE.ShaderMaterial({uniforms,
   vertexShader:compiled.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\\n transformed=uProbePoint;'),
   fragmentShader:'varying vec3 vProbe; void main(){gl_FragColor=vec4(vProbe,1.);}',
   toneMapped:false,depthTest:false,depthWrite:false});
 const geometry=new THREE.PlaneGeometry(2,2);
 for(const [name,attribute] of Object.entries(source.geometry.attributes)) {
   if(['position','normal','uv'].includes(name)) continue;
   const value=Array.from(attribute.array.slice(0,attribute.itemSize));
   geometry.setAttribute(name,new THREE.Float32BufferAttribute(Array(4).fill(value).flat(),attribute.itemSize));
 }
 const mesh=new THREE.Mesh(geometry,material);mesh.frustumCulled=false;mesh.matrixAutoUpdate=false;
 const pixel=new Float32Array(4);
 return {
  sample({matrix=new THREE.Matrix4(),root=new THREE.Vector3(),point=new THREE.Vector3(),attrs={},normal=false,time=0}={}) {
   mesh.matrix.copy(matrix);uniforms.uProbePoint.value.copy(point);uniforms.uProbeRoot.value.copy(root);
   uniforms.uProbeNormal.value=normal?1:0;uniforms.uTime.value=time;
   for(const [name,value] of Object.entries(attrs)) {
    const a=geometry.attributes[name];for(let i=0;i<a.count;i++)a.array.set(value,i*a.itemSize);a.needsUpdate=true;
   }
   scene.add(mesh);renderer.setRenderTarget(target);renderer.render(scene,camera);
   renderer.readRenderTargetPixels(target,1,1,1,1,pixel);scene.remove(mesh);
   if(!Array.from(pixel).every(Number.isFinite)||pixel[3]!==1)throw new Error('Nonfinite/empty vertex readback: '+pixel);
   return new THREE.Vector3().fromArray(pixel);
  },
  dispose(){geometry.dispose();material.dispose();},uniforms,
 };
}
"""


def test_legacy_vegetation_shares_world_wind_after_parent_rotation_and_translation():
    code, out = compile_scene("""
import * as THREE from 'three';
import {makeGrass} from './lib/grass.js';
import {makeFlowers} from './lib/flowers.js';
import {makeReeds} from './lib/smalllife.js';
import {makeCanopy} from './lib/canopy.js';
import {makeWheatField} from './lib/cloth.js';
""" + _VERTEX_PROBE + """
export function createScene({renderer}) {
 const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,10);camera.position.z=2;
 const opts={extent:2,density:2,seed:5,wind:{dir:[1,.35],strength:1.4},shadows:true};
 const groups=[makeGrass(opts),makeFlowers(opts),makeReeds(opts),makeWheatField(opts),makeCanopy({density:4,...opts})];
 const names=['Blades','Plants','Stems','Stalks','Leaves'];
 const shape=[{iShape:[1,.05,0,0],iVar:[0,.37,.2,0]},
  {iShape:[1,.02,1,1],iBend:[.6,0,0,.4],iVar:[0,.4,.3,1],aPart:[0,0,0]},
  {iShape:[1,.02,.2,.03],iBend:[.5,.1,0,0],iVar:[.37,.2,.3,.4],aPart:[0]},
  {iCrop:[1,.01,.8,1],iVar:[.37,0,0],aPart:[0]},
  {iAxis:[0,1,0],iVar:[0,.2,.4,1]}];
 const target=new THREE.WebGLRenderTarget(4,4,{type:THREE.FloatType}),previous=renderer.getRenderTarget();
 let error=0,shadowError=0;
 try {
  for(let kind=0;kind<groups.length;kind++) {
   const source=groups[kind].getObjectByName(names[kind]);
   const probe=vertexProbe(source,renderer,scene,camera,target);
   const shadow=vertexProbe({geometry:source.geometry,material:source.customDepthMaterial},renderer,scene,camera,target);
   for(const root of [new THREE.Vector3(0,0,0),new THREE.Vector3(2.5,0,-4)]) for(const time of [0,.73,3.1]) {
    let reference=null;
    for(const yaw of [0,Math.PI/2,-Math.PI/3]) {
     const matrix=new THREE.Matrix4().makeRotationY(yaw);
     matrix.setPosition(yaw===0?0:3,0,yaw===0?0:-2);
     const local=root.clone().applyMatrix4(matrix.clone().invert());
     const attrs={...shape[kind],iPos:local.toArray(),aCorner:[0,1,0]};
     if(kind===4)attrs.iSpray=[...local.toArray(),.4];
     const sample={matrix,root,attrs,time},actual=probe.sample(sample),depth=shadow.sample(sample);
     if(reference)error=Math.max(error,actual.distanceTo(reference));else reference=actual;
     shadowError=Math.max(shadowError,actual.distanceTo(depth));
    }
   }
   probe.dispose();shadow.dispose();
  }
 } finally {renderer.setRenderTarget(previous);target.dispose();groups.forEach(g=>g.userData.dispose());}
 if(error>.002||shadowError>.00001)throw new Error('World-wind/depth mismatch: '+JSON.stringify({error,shadowError}));
 return {scene,cameras:[{name:'a',position:[0,0,2],lookAt:[0,0,0],fov:45}]};
}
""", _LIBS, audit_module="src/scene.js")
    assert code == 0, out


def test_flag_and_banner_normals_match_finite_differences_of_their_displacement():
    code, out = compile_scene("""
import * as THREE from 'three';
import {makeFlag,makeBanner} from './lib/cloth.js';
""" + _VERTEX_PROBE + """
export function createScene({renderer}) {
 const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,10);camera.position.z=2;
 const groups=[makeFlag({width:2,height:1,wind:3}),makeBanner({width:2,drop:2,wind:3})];
 const target=new THREE.WebGLRenderTarget(4,4,{type:THREE.FloatType}),previous=renderer.getRenderTarget();
 let error=0;
 try {
  for(let kind=0;kind<2;kind++) {
   const source=groups[kind].getObjectByName(kind?'BannerCloth':'FlagSheet');
   const probe=vertexProbe(source,renderer,scene,camera,target);
   const sample=(x,y,time,normal=false)=>{
    const u=x/2+.5,s=-y/2,k=5;
    // Analytic profiles isolate the displacement from tessellation density.
    const attrs=kind?{aBan:[s,Math.sin(k*x),k*Math.cos(k*x),k*k*x/2+k*Math.sin(2*k*x)/4]}:
      {aFlag:[u,u**1.6,1.6*u**.6,u**4.2/4.2]};
    return probe.sample({point:new THREE.Vector3(x,y,0),attrs,time,normal});
   };
   const epsilon=.0005;
   for(const x of [-.73,.13,.77])for(const y of [-.25,-.8])for(const time of [.4,1.6]) {
    const dx=sample(x+epsilon,y,time).sub(sample(x-epsilon,y,time));
    const dy=sample(x,y+epsilon,time).sub(sample(x,y-epsilon,time));
    const expected=dx.cross(dy).normalize(),actual=sample(x,y,time,true).normalize();
    error=Math.max(error,1-expected.dot(actual));
   }
   probe.dispose();
  }
 } finally {renderer.setRenderTarget(previous);target.dispose();groups.forEach(g=>g.userData.dispose());}
 if(error>.00005)throw new Error('Cloth normal does not follow displaced tangents: '+error);
 return {scene,cameras:[{name:'a',position:[0,0,2],lookAt:[0,0,0],fov:45}]};
}
""", ("cloth.js", "grass.js"), audit_module="src/scene.js")
    assert code == 0, out


def test_legacy_transition_masks_have_defined_edges():
    """Equal snow slopes and six reversed silhouette ramps were undefined GLSL."""
    out = measure("""
import * as THREE from 'three';
import {patchSlopeSplat} from './lib/terrain_shade.js';
const values=[];
for(const pair of [[.6,.6],[.6,.6+1e-10],[.45,.75],[.75,.45]]) {
 const m=patchSlopeSplat(new THREE.MeshStandardMaterial(),{slopeLow:pair[0],slopeHigh:pair[1]});
 const u=m.userData.uniforms,lo=Math.fround(u.uSplatHigh.value),hi=Math.fround(u.uSplatLow.value);
 const masks=[lo-.01,(lo+hi)/2,hi+.01].map(x=>THREE.MathUtils.smoothstep(x,lo,hi));
 values.push({lo,hi,masks});
}
console.log(JSON.stringify({values}));
""", ("terrain_shade.js",))
    for item in out["values"]:
        assert item["lo"] < item["hi"]
        assert item["masks"] == pytest.approx([0, .5, 1], abs=1e-8)
    for module in ("flock.js", "smalllife.js", "woodland.js"):
        # GLSL leaves edge0 >= edge1 undefined even when one GPU accepts it.
        for match in re.finditer(r"smoothstep\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,",
                                 (LIB_DIR / module).read_text()):
            assert float(match[1]) < float(match[2]), (module, match[0])


def test_falling_sample_matches_gpu_after_parent_and_child_transforms():
    """World wind must have the same local velocity in the public CPU sampler."""
    code, out = compile_scene("""
import * as THREE from 'three';
import {makeFalling} from './lib/flowers.js';
""" + _VERTEX_PROBE + """
export function createScene({renderer}) {
 const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,10);camera.position.z=2;
 const parent=new THREE.Group(),group=makeFalling({count:7,seed:13,extent:8,height:5,
   wind:{dir:[1,.35],strength:1.4}}),source=group.getObjectByName('Drift');
 parent.add(group);parent.matrixAutoUpdate=group.matrixAutoUpdate=source.matrixAutoUpdate=false;
 const matrix=(position,rotation,scale)=>new THREE.Matrix4().compose(new THREE.Vector3(...position),
   new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation)),new THREE.Vector3(...scale));
 const identity=new THREE.Matrix4(),outer=matrix([3,.7,-4],[.2,.6,-.1],[1.7,.65,2.3]);
 const local=matrix([-.5,.2,.8],[0,.47,0],[.8,1.2,1.4]);
 const child=matrix([.3,.4,-.6],[.2,.9,.4],[.8,1.2,2.1]);
 const nearSingular=new THREE.Matrix4().set(1,0,1,0, 0,1,0,0, 0,0,1e-9,0, 0,0,0,1);
 const transforms=[[identity,identity,identity],
  [matrix([2,0,-1],[0,Math.PI/2,0],[1,1,1]),identity,identity],
  [outer,local,identity],[outer,local,child],
  [identity,identity,new THREE.Matrix4().makeScale(0,1,1)],
  [identity,identity,nearSingular]];
 const target=new THREE.WebGLRenderTarget(4,4,{type:THREE.FloatType}),previous=renderer.getRenderTarget();
 const probe=vertexProbe(source,renderer,scene,camera,target);
 let error=0;
 try {
  for(const [parentMatrix,groupMatrix,childMatrix] of transforms) {
   parent.matrix.copy(parentMatrix);group.matrix.copy(groupMatrix);source.matrix.copy(childMatrix);
   // No scene update/render precedes sample(): it must refresh its ancestors.
   for(const time of [.73,0,3.1,-2.4,31.7])for(const index of [-4,3,99]) {
    const expected=group.userData.sample(index,time);
    // Rendering refreshes matrices independently of the public CPU sampler.
    source.updateWorldMatrix(true,false);expected.applyMatrix4(group.matrixWorld);
    const k=Math.min(6,Math.max(0,index)),attrs={aCorner:[0,0,0]};
    for(const name of ['aPos','aFall','aAxis','aWob','aVar']) {
     const a=source.geometry.attributes[name];attrs[name]=Array.from(a.array.slice(k*a.itemSize,(k+1)*a.itemSize));
    }
    const actual=probe.sample({matrix:source.matrixWorld,attrs,time});
    error=Math.max(error,actual.distanceTo(expected));
   }
  }
 } finally {renderer.setRenderTarget(previous);probe.dispose();target.dispose();group.userData.dispose();}
 if(error>.00015)throw new Error('Falling CPU/GPU transformed sample mismatch: '+error);
 return {scene,cameras:[{name:'a',position:[0,0,2],lookAt:[0,0,0],fov:45}]};
}
""", _LIBS, audit_module="src/scene.js")
    assert code == 0, out
