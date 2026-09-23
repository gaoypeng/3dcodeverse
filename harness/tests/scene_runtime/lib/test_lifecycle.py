"""Construction-time resource ownership, including independent instance buffers."""
import pytest

from tests.scene_runtime.lib._probe import measure

pytestmark = pytest.mark.node


def test_owned_buffers_shadows_and_explicit_textures_release_once():
    out = measure("""
import * as THREE from 'three';
import {snapshotResources,attachDisposal} from './lib/lifecycle.js';
const root=new THREE.Group(), geometry=new THREE.BoxGeometry();
const borrowedMap=new THREE.Texture(), ownedMap=new THREE.DataTexture();
const material=new THREE.MeshStandardMaterial({map:borrowedMap});
const mesh=new THREE.InstancedMesh(geometry,material,2);
mesh.customDepthMaterial=new THREE.MeshDepthMaterial();
mesh.customDistanceMaterial=new THREE.MeshDistanceMaterial();
root.add(mesh,new THREE.Mesh(geometry,material));
const resources=snapshotResources(root);resources.add(ownedMap);
attachDisposal(root,resources);
const callerMesh=new THREE.InstancedMesh(new THREE.BoxGeometry(),material,1);
root.add(callerMesh);
const events={};
for(const [name,res] of Object.entries({geometry,material,mesh,ownedMap,borrowedMap,
 depth:mesh.customDepthMaterial,distance:mesh.customDistanceMaterial,
 callerMesh,callerGeometry:callerMesh.geometry})) {
 events[name]=0;res.addEventListener('dispose',()=>events[name]++);
}
resources.add(borrowedMap); // The attached ownership set is itself a snapshot.
const first=root.userData.dispose(),second=root.userData.dispose();
console.log(JSON.stringify({events,first,second}));
""")
    assert out == {"events": {"geometry": 1, "material": 1, "mesh": 1,
                              "ownedMap": 1, "borrowedMap": 0, "depth": 1,
                              "distance": 1, "callerMesh": 0, "callerGeometry": 0},
                   "first": True, "second": False}


def test_borrowed_categories_and_failed_disposer_do_not_skip_cleanup():
    out = measure("""
import * as THREE from 'three';
import {snapshotResources,attachDisposal} from './lib/lifecycle.js';
const mesh=new THREE.Mesh(new THREE.BoxGeometry(),new THREE.MeshStandardMaterial());
const resources=snapshotResources(mesh,{materials:false});
let geometry=0,material=0,extra=0,failures=0;
mesh.geometry.addEventListener('dispose',()=>geometry++);
mesh.material.addEventListener('dispose',()=>material++);
resources.add({dispose(){throw new Error('broken extension');}});
resources.add({dispose(){extra++;}});
attachDisposal(mesh,resources);
try {mesh.userData.dispose();} catch(error) {failures=error.errors.length;}
const repeated=mesh.userData.dispose();
console.log(JSON.stringify({geometry,material,extra,failures,repeated}));
""")
    assert out == {"geometry": 1, "material": 0, "extra": 1,
                   "failures": 1, "repeated": False}
