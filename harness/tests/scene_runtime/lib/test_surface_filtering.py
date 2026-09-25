"""Unresolved material grain averages away, including its chroma variation."""
import json

import pytest

from tests.scene_runtime.lib._probe import compile_scene

pytestmark = pytest.mark.node


def test_micro_breakup_keeps_resolved_detail_and_filters_subpixel_color():
    code, out = compile_scene("""
import * as THREE from 'three';
import { patchStandard } from './lib/shader.js';
import { patchMicroBreakup } from './lib/surface_wear.js';
export function createScene({renderer}) {
  const scene = new THREE.Scene();
  const material = new THREE.MeshStandardMaterial({color:new THREE.Color(.4,.4,.4),toneMapped:false});
  patchMicroBreakup(material,{strength:.3,hue:.5,scale:.4,seed:7});
  patchStandard(material,{name:'read-albedo',outputBody:'gl_FragColor=vec4(diffuseColor.rgb,1.0);'});
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(2,2),material);scene.add(plane);
  const camera = new THREE.OrthographicCamera(-1,1,1,-1,.1,10);camera.position.z=2;
  const target = new THREE.WebGLRenderTarget(64,64),pixels = new Uint8Array(64*64*4);
  const previous = renderer.getRenderTarget();
  try {
    renderer.setRenderTarget(target);
    for (const scale of [.4, .0001]) {
      material.userData.uniforms.uMicroScale.value=scale;
      renderer.render(scene,camera);renderer.readRenderTargetPixels(target,0,0,64,64,pixels);
      let lo=255,hi=0,maxError=0;
      for(let i=0;i<pixels.length;i+=4) for(let c=0;c<3;c++) {
        lo=Math.min(lo,pixels[i+c]);hi=Math.max(hi,pixels[i+c]);
        maxError=Math.max(maxError,Math.abs(pixels[i+c]-102));
      }
      if(scale>.1 && hi-lo<12) throw new Error('Resolved material lost its grain: '+[lo,hi]);
      if(scale<.1 && maxError>1) throw new Error('Subpixel material grain/chroma did not filter to its mean: '+maxError);
    }
  } finally {
    renderer.setRenderTarget(previous);target.dispose();plane.geometry.dispose();material.dispose();
  }
  scene.remove(plane);
  return {scene,cameras:[{name:'probe',position:[0,0,2],lookAt:[0,0,0],fov:45}]};
}
""", libs=("surface_wear.js",))
    assert code == 0, out
    assert json.loads(out.strip().splitlines()[-1])["ok"]


def test_moss_casts_no_edge_shadow_on_a_dry_sun_facing_wall():
    code, out = compile_scene("""
import * as THREE from 'three';
import { patchStandard } from './lib/shader.js';
import { patchMoss } from './lib/damp.js';
export function createScene({renderer}) {
  const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(-1,1,1,-1,.1,10);
  camera.position.z=2;
  const material=new THREE.MeshStandardMaterial({color:new THREE.Color(.4,.4,.4),toneMapped:false});
  // The wall faces +Z; the shaded side is -Z. There is no local concavity.
  patchMoss(material,{amount:1,north:[0,0,-1]});
  patchStandard(material,{name:'dry-wall-albedo',outputBody:'gl_FragColor=vec4(diffuseColor.rgb,1.0);'});
  const plane=new THREE.Mesh(new THREE.PlaneGeometry(2,2),material);scene.add(plane);
  const target=new THREE.WebGLRenderTarget(32,32),pixels=new Uint8Array(32*32*4);
  const previous=renderer.getRenderTarget();
  try {
    renderer.setRenderTarget(target);renderer.render(scene,camera);
    renderer.readRenderTargetPixels(target,0,0,32,32,pixels);
    for(let i=0;i<pixels.length;i+=4) for(let c=0;c<3;c++)
      if(Math.abs(pixels[i+c]-102)>1) throw new Error('Absent moss darkened the dry wall: '+pixels[i+c]);
  } finally {
    renderer.setRenderTarget(previous);target.dispose();plane.geometry.dispose();material.dispose();
  }
  scene.remove(plane);
  return {scene,cameras:[{name:'probe',position:[0,0,2],lookAt:[0,0,0],fov:45}]};
}
""", libs=("damp.js",))
    assert code == 0, out
    assert json.loads(out.strip().splitlines()[-1])["ok"]
