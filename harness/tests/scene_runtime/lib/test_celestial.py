"""celestial.js: the sky opts out of fog with the marker the static GLSL audit reads."""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import compile_scene, measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "materials.js", "celestial.js")


def test_the_sky_declares_its_own_fog_opt_out():
    """The option the audit's own warning tells you to pass has to write the
    marker it reads (`3dcode:`; `astra3d:` still accepted on read)."""
    out = measure("""
import { makeStars, makeAurora } from './lib/celestial.js';
const marks = [];
for (const g of [makeStars({ count: 50, seed: 1 }),
                 makeAurora({ seed: 1 })]) {
  g.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (m && m.fragmentShader) {
        marks.push([m.name, m.fog === false,
                    /(3dcode|astra3d):\\s*no-fog/.test(m.fragmentShader)]);
      }
    }
  });
}
console.log(JSON.stringify({ marks }));
""", _LIBS)
    assert out["marks"], "no sky materials found"
    for name, flag, marker in out["marks"]:
        assert flag, f"{name} does not opt out of fog"
        assert marker, f"{name} opts out but writes no marker for the checker"


def test_milky_way_is_faint_and_magnitude_controls_all_starlight():
    """Diffuse galactic light must not become a bright cloud or ignore magnitude."""
    code, out = compile_scene("""
import * as THREE from 'three';
import {makeStars} from './lib/celestial.js';
export function createScene({renderer}) {
 const scene=new THREE.Scene(), camera=new THREE.PerspectiveCamera(100,1,.1,5000);
 const target=new THREE.WebGLRenderTarget(96,96,{type:THREE.FloatType});
 const pixels=new Float32Array(96*96*4), previous=renderer.getRenderTarget();
 const clear=new THREE.Color();renderer.getClearColor(clear);const clearAlpha=renderer.getClearAlpha();
 const groups=[1,.5,0].map(magnitude=>makeStars({count:200,seed:18,magnitude}));
 const pole=groups[0].getObjectByName('MilkyWayStars').material.uniforms.uPole.value;
 const core=groups[0].getObjectByName('MilkyWayStars').material.uniforms.uCore.value;
 const directions=[core,core.clone().negate(),new THREE.Vector3(0,1,0),
   new THREE.Vector3(-.6,.2,-1).normalize()];
 try {
  renderer.setClearColor(0,0);renderer.setRenderTarget(target);
  for(const direction of directions) {
   camera.lookAt(direction);camera.updateMatrixWorld(true);
   const energies=[];
   for(const group of groups) {
    group.getObjectByName('StarField').visible=false;scene.add(group);
    renderer.render(scene,camera);renderer.readRenderTargetPixels(target,0,0,96,96,pixels);
    let energy=0,peak=0;
    for(let i=0;i<pixels.length;i+=4) {
     const l=.2126*pixels[i]+.7152*pixels[i+1]+.0722*pixels[i+2];
     if(!Number.isFinite(l))throw Error('Nonfinite galactic light');
     energy+=l;peak=Math.max(peak,l);
    }
    if(peak>.035||energy/(96*96)>.008)throw Error('Milky Way became luminous fog: '+[peak,energy]);
    energies.push(energy);scene.remove(group);
   }
   if(energies[0]<=.001)throw Error('Unresolved starlight disappeared');
   if(Math.abs(energies[1]/energies[0]-.5)>.0001||energies[2]!==0)
    throw Error('Magnitude does not control diffuse starlight: '+energies);
  }
  const off=groups[2];off.getObjectByName('StarField').visible=true;scene.add(off);
  renderer.render(scene,camera);renderer.readRenderTargetPixels(target,0,0,96,96,pixels);
  for(let i=0;i<pixels.length;i+=4)if(pixels[i]||pixels[i+1]||pixels[i+2])throw Error('Magnitude zero left visible stars');
 } finally {
  renderer.setRenderTarget(previous);renderer.setClearColor(clear,clearAlpha);
  target.dispose();groups.forEach(g=>g.userData.dispose());
 }
 return {scene:new THREE.Scene(),cameras:[{name:'probe',position:[0,0,1],lookAt:[0,0,0]}]};
}
""", _LIBS)
    assert code == 0, out
