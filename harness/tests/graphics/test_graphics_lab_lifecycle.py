"""The live viewer respects effect ownership and releases failed scene contexts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.scene_runtime.conftest import needs_browser, run_node_json

pytestmark = [pytest.mark.node, needs_browser]
LAB = Path(__file__).resolve().parents[2] / "examples/graphics_lab"

FIXTURE = """
import * as THREE from 'three';
export async function makeScene(kind,{renderer}) {
 const stats=window.cleanupStats={};
 const count=key=>stats[key]=(stats[key]||0)+1;
 const track=(resource,key)=>{resource.addEventListener('dispose',()=>count(key));return resource;};
 const originalDispose=renderer.dispose.bind(renderer);
 renderer.dispose=()=>{count('renderer');originalDispose();if(kind==='throwing')throw Error('Renderer cleanup failure');};
 const originalLoss=renderer.forceContextLoss.bind(renderer);
 renderer.forceContextLoss=()=>{count('context');originalLoss();};
 const canvas=renderer.domElement,originalRemove=canvas.remove.bind(canvas);
 canvas.remove=()=>{count('canvas');originalRemove();};
 const scene=new THREE.Scene();
 const geometry=track(new THREE.BoxGeometry(),'geometry');
 const material=track(new THREE.MeshBasicMaterial({color:0x558899}),'material');
 const mesh=new THREE.Mesh(geometry,material);scene.add(mesh);
 const result={scene,cameras:[{name:'Study',position:[2,2,4],lookAt:[0,0,0]}],update(){}};
 if(kind==='authoritative') {
  mesh.userData.dispose=()=>count('childDisposer');
  const borrowed=track(new THREE.BoxGeometry(),'borrowedGeometry');
  mesh.add(new THREE.Mesh(borrowed,track(new THREE.MeshBasicMaterial(),'borrowedMaterial')));
  result.dispose=()=>{count('sceneDisposer');geometry.dispose();material.dispose();};
 } else if(kind.startsWith('fallback')) {
  const owner=new THREE.Group();scene.add(owner);owner.add(mesh);
  owner.userData.dispose=()=>{count('ownerDisposer');geometry.dispose();material.dispose();
   if(kind==='fallback_failure')throw Error('Effect cleanup failure');};
  mesh.userData.dispose=()=>count('nestedDisposer');
  mesh.add(new THREE.Mesh(track(new THREE.BoxGeometry(),'borrowedGeometry'),
   track(new THREE.MeshBasicMaterial(),'borrowedMaterial')));
  // A managed resource also used by an unmanaged sibling must not be freed twice.
  scene.add(new THREE.Mesh(geometry,material));
  const looseGeometry=track(new THREE.BoxGeometry(),'looseGeometry');
  const looseMaterial=track(new THREE.MeshBasicMaterial(),'looseMaterial');
  const texture=track(new THREE.DataTexture(new Uint8Array([255,255,255,255]),1,1),'texture');texture.needsUpdate=true;
  looseMaterial.map=texture;
  const loose=new THREE.InstancedMesh(looseGeometry,looseMaterial,1);track(loose,'instances');
  loose.customDepthMaterial=track(new THREE.MeshDepthMaterial(),'depthMaterial');
  loose.customDistanceMaterial=track(new THREE.MeshDistanceMaterial(),'distanceMaterial');scene.add(loose);
  scene.add(new THREE.Mesh(looseGeometry,looseMaterial));
  const cached=track(new THREE.MeshBasicMaterial(),'cachedMaterial');cached.userData.shared=true;
  scene.add(new THREE.Mesh(looseGeometry,cached));
 } else if(kind==='throwing'||kind==='invalid') {
  result.dispose=()=>{count('sceneDisposer');throw Error('Owned cleanup failure');};
  if(kind==='invalid')result.scene=null;
 } else if(kind==='cancelled') {
  result.dispose=()=>{count('sceneDisposer');geometry.dispose();material.dispose();};
  await new Promise(resolve=>{window.resolveScene=resolve;});
 }
 return result;
}
"""


def test_live_cleanup_ownership_failures_and_pending_cancellation(tmp_path: Path):
    for name in ("index.html", "viewer.js", "viewer.css"):
        (tmp_path / name).write_bytes((LAB / name).read_bytes())
    cases = []
    for kind in ("authoritative", "fallback", "fallback_failure", "throwing", "invalid", "cancelled"):
        (tmp_path / f"{kind}.js").write_text(
            "import {makeScene} from './fixture.js';\n"
            f"export const createScene=options=>makeScene('{kind}',options);\n"
        )
        cases.append({"id": kind, "title": kind, "subtitle": "Cleanup study",
                      "kind": "test", "number": "01", "module": f"./{kind}.js"})
    (tmp_path / "fixture.js").write_text(FIXTURE)
    (tmp_path / "manifest.json").write_text(json.dumps({"cases": cases}))
    result = run_node_json("""
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {launchBrowser,serveDirs} from './lib/host_env.mjs';
import {releaseBrowser} from './lib/host_page.mjs';
const root=ROOT;
const server=await serveDirs({root,routes:{'/':{body:fs.readFileSync(root+'/index.html','utf8')}}});
const launched=await launchBrowser({gpu:'auto'});
let page;
try {
 page=await launched.browser.newPage();
 const errors=[],warnings=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='warn'&&m.text().includes('cleanup failed'))warnings.push(m.text());});
 await page.goto(server.base,{waitUntil:'networkidle0'});
 await page.waitForFunction(()=>!!window.graphicsLab);
 const snapshots={};
 for(const kind of ['authoritative','fallback','fallback_failure','throwing','invalid','cancelled']) {
  await page.evaluate(async kind=>{
   const item=(await (await fetch('./manifest.json')).json()).cases.find(item=>item.id===kind);
   window.graphicsLab.open(item);
   window.pendingBoot=window.graphicsLab.explore();
  },kind);
  if(kind==='cancelled') {
   await page.waitForFunction(()=>typeof window.resolveScene==='function');
   await page.click('#close');
   await page.evaluate(async()=>{window.resolveScene();await window.pendingBoot;});
  } else {
   await page.evaluate(()=>window.pendingBoot);
   if(kind==='invalid') {
    await page.$eval('#video',e=>e.dispatchEvent(new Event('error')));
    assert.match(await page.$eval('#status',e=>e.textContent),/Scene does not implement the scene contract/);
   }
   else assert(await page.evaluate(()=>!!window.graphicsLab.active),kind+' did not boot');
   await page.click('#close');
  }
  snapshots[kind]=await page.evaluate(()=>({...window.cleanupStats,active:!!window.graphicsLab.active,canvasCount:document.querySelectorAll('#live canvas').length}));
 }
 assert.deepEqual(errors,[]);
 for(const stats of Object.values(snapshots)) {
  assert.equal(stats.renderer,1);assert.equal(stats.context,1);assert.equal(stats.canvas,1);
  assert.equal(stats.active,false);assert.equal(stats.canvasCount,0);
 }
 const a=snapshots.authoritative;
 assert.equal(a.sceneDisposer,1);assert.equal(a.geometry,1);assert.equal(a.material,1);
 assert.equal(a.childDisposer,undefined);assert.equal(a.borrowedGeometry,undefined);assert.equal(a.borrowedMaterial,undefined);
 for(const f of [snapshots.fallback,snapshots.fallback_failure]) {
  for(const key of ['ownerDisposer','geometry','material','looseGeometry','looseMaterial','instances','depthMaterial','distanceMaterial'])assert.equal(f[key],1,key);
  for(const key of ['nestedDisposer','borrowedGeometry','borrowedMaterial','cachedMaterial','texture'])assert.equal(f[key],undefined,key);
 }
 assert.equal(snapshots.throwing.sceneDisposer,1);assert.equal(snapshots.invalid.sceneDisposer,1);
 assert.equal(snapshots.cancelled.sceneDisposer,1);
 assert.equal(warnings.length,3);
 console.log(JSON.stringify({snapshots,warnings:warnings.length,errors}));
} finally {
 await page?.close();await releaseBrowser(launched);await server.close();
}
""".replace("ROOT", json.dumps(str(tmp_path))), timeout=120)
    assert len(result["snapshots"]) == 6
    assert result["errors"] == []
