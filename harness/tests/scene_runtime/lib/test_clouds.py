"""Cloud deck transport, placement, and ownership regressions."""
from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure

pytestmark = pytest.mark.node


def test_the_layer_is_lit_by_the_rig_and_a_set_sun_becomes_the_moon():
    """`sunRig().sunDir` keeps reporting the authored sun after it sets; passed
    straight through, every night puff is lit from below.  A set sun becomes a
    moon above the horizon, opposite it."""
    m = measure("""
import * as THREE from 'three';
import { makeClouds } from './lib/clouds.js';
const dir = (o) => makeClouds(Object.assign({ seed: 3 }, o)).material.uniforms.uSunDir.value.toArray();
console.log(JSON.stringify({
  day: dir({ sunDir: new THREE.Vector3(-0.6, 0.55, -0.3) }),
  set: dir({ sunDir: new THREE.Vector3(0.62, -0.34, 0.21) }),
  none: dir({}),
}));
""", ("clouds.js",))
    assert m["day"][1] > 0.5, m
    assert m["set"][1] > 0.4 and m["set"][0] < 0, m
    assert m["none"][1] == 1.0, m


def test_cloud_deck_seed_options_and_disposal():
    out = measure("""
import {makeClouds} from './lib/clouds.js';
const hash=texture=>{
 let h=2166136261;
 for(const byte of texture.image.data)h=Math.imul(h^byte,16777619);
 return h>>>0;
};
const decks=[makeClouds({seed:7}),makeClouds({seed:7}),makeClouds({seed:8}),makeClouds({count:0})];
const weather=decks.slice(0,3).map(d=>hash(d.material.uniforms.uWeather.value));
const noise=decks.slice(0,3).map(d=>hash(d.material.uniforms.uNoise.value));
let rejected=false;
try {makeClouds({quality:'toString'});} catch(error) {rejected=error instanceof RangeError;}
const empty=decks[3].visible===false;
const counts=[];
for(const deck of decks) {
 const u=deck.material.uniforms;
 for(const resource of [deck.geometry,deck.material,u.uWeather.value,u.uNoise.value]) {
  const at=counts.length;counts.push(0);resource.addEventListener('dispose',()=>counts[at]++);
 }
 deck.userData.dispose();deck.userData.dispose();
}
console.log(JSON.stringify({weather,noise,rejected,empty,counts}));
""", ("clouds.js",))
    for field in (out["weather"], out["noise"]):
        assert field[0] == field[1] and field[0] != field[2], out
    assert out["rejected"] and out["empty"], out
    assert out["counts"] == [1] * 16, out


def test_cloud_transport_matches_beer_lambert_and_camera_projection():
    """Constant data isolates optical integration from authored noise and color."""
    code, out = compile_scene("""
import * as THREE from 'three';
import {makeClouds} from './lib/clouds.js';
export function createScene({renderer}) {
 const scene=new THREE.Scene();
 // An odd target has an exact center pixel in both projection models.
 const target=new THREE.WebGLRenderTarget(17,17,{type:THREE.FloatType});
 const pixel=new Float32Array(4),previous=renderer.getRenderTarget();
 const clear=new THREE.Color();renderer.getClearColor(clear);const clearAlpha=renderer.getClearAlpha();
 const results=[];
 try {
  renderer.setClearColor(0,0);renderer.setRenderTarget(target);
  for(const transformed of [false,true])for(const quality of ['low','balanced','high']) {
   const parent=new THREE.Group(),placement=new THREE.Group();parent.add(placement);scene.add(parent);
   if(transformed) {
    parent.position.set(37,-8,23);parent.rotation.y=.41;parent.scale.set(1.7,.8,1.4);
    placement.position.set(-9,11,14);placement.rotation.y=-.55;placement.scale.set(.9,1.2,1.3);
   }
   parent.updateMatrixWorld(true);
   const linear=new THREE.Matrix3().setFromMatrix4(placement.matrixWorld);
   const worldAxis=new THREE.Vector3(0,0,1).applyMatrix3(linear),metric=worldAxis.length();
   const deck=makeClouds({quality,area:100,altitude:0,spread:0,alpha:.12,
     sunDir:worldAxis.clone().normalize(),wind:0});
   placement.add(deck);
   const material=deck.material,u=material.uniforms;
   for(const name of ['uWeather','uNoise']) {
    u[name].value.image.data.fill(255);u[name].value.needsUpdate=true;
   }
   const center=u.uCenter.value,size=u.uSize.value;
   const point=center.clone();point.y+=(-.5+.22)*size.y;
   const sunStart=point.clone();sunStart.z-=.4*size.z;
   u.uProbePoint={value:sunStart};
   // Read actual production transport, bypassing artistic source color and
   // blending. The material's ray setup and density/shadow loops still run.
   material.blending=THREE.NoBlending;material.toneMapped=false;material.depthTest=false;
   material.onBeforeCompile=shader=>{
    shader.fragmentShader='uniform vec3 uProbePoint;\\n'+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <tonemapping_fragment>',
      'gl_FragColor=vec4(opacity,deckShadow(uProbePoint),deckDensity(uProbePoint),1.0);\\n#include <tonemapping_fragment>');
   };
   const localEye=point.clone();localEye.z+=size.z*1.2;
   const eye=localEye.applyMatrix4(placement.matrixWorld),aim=point.clone().applyMatrix4(placement.matrixWorld);
   const cameras=[new THREE.PerspectiveCamera(45,1,.1,5000),new THREE.OrthographicCamera(-80,80,80,-80,.1,5000)];
   const samples=[];
   for(const camera of cameras) {
    camera.position.copy(eye);camera.lookAt(aim);camera.updateMatrixWorld(true);
    renderer.render(scene,camera);renderer.readRenderTargetPixels(target,8,8,1,1,pixel);
    if(!Array.from(pixel).every(Number.isFinite)||pixel[3]!==1)throw Error('Empty/nonfinite cloud probe: '+pixel);
    samples.push(Array.from(pixel));
   }
   // The integral of each 3.5%-wide smoothstep boundary is half its width.
   // View crosses two fades; light from z=.1 crosses only the exit fade.
   const extinction=u.uExtinction.value;
   const expectedOpacity=1-Math.exp(-size.z*.965*metric*extinction);
   const expectedShadow=size.z*(.9-.035/2)*metric*extinction;
   for(const sample of samples) {
    if(Math.abs(sample[2]-1)>.00001)throw Error('Probe density is not constant: '+sample);
    if(Math.abs(sample[0]-expectedOpacity)>.005)throw Error('Cloud view optical depth lost world distance: '+[quality,transformed,sample[0],expectedOpacity]);
    if(Math.abs(sample[1]/expectedShadow-1)>.03)throw Error('Cloud light march did not integrate the full path: '+[quality,transformed,sample[1],expectedShadow]);
   }
   if(Math.abs(samples[0][0]-samples[1][0])>.0002)throw Error('Orthographic/perspective center rays disagree: '+samples);
   results.push({transformed,quality,opacity:samples[0][0],shadow:samples[0][1]});
   u.uAlpha.value=0;
   renderer.render(scene,cameras[0]);renderer.readRenderTargetPixels(target,8,8,1,1,pixel);
   if(pixel.some(v=>v!==0))throw Error('Alpha zero left cloud transport: '+pixel);
   scene.remove(parent);deck.userData.dispose();
  }
  for(const transformed of [false,true]) {
   const group=results.filter(r=>r.transformed===transformed),reference=group[2];
   for(const row of group)if(Math.abs(row.opacity-reference.opacity)>.005||Math.abs(row.shadow/reference.shadow-1)>.03)
    throw Error('Cloud quality changed integrated density: '+JSON.stringify(group));
  }
 } finally {renderer.setRenderTarget(previous);renderer.setClearColor(clear,clearAlpha);target.dispose();}
 return {scene:new THREE.Scene(),cameras:[{name:'probe',position:[0,0,1],lookAt:[0,0,0]}]};
}
""", ("clouds.js",))
    assert code == 0, out
