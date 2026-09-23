/** Wind crossing a lush upland meadow, with close leaf geometry and far slopes. */
import * as THREE from 'three';
import { makeMeadow } from '../lib/meadow.js';
import { makeRock } from '../lib/rock.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { patchStandard } from '../lib/shader.js';

export function createScene() {
  const scene = new THREE.Scene();
  const rig = sunRig({mood:'day', azimuth:125, elevation:31, bounds:9,
    intensity:4.3, fill:1.3});
  scene.add(rig.sun, rig.sun.target, rig.fill);
  rig.sun.shadow.mapSize.set(4096,4096);
  rig.sun.shadow.normalBias=.0015;
  rig.sun.shadow.radius=2.2;
  scene.environment=rig.envTex;
  makeSky(scene,{rig});
  scene.fog=new THREE.FogExp2(0xc0c6b2,.019);
  scene.userData.grade={contrast:1.03,saturation:.93,warmth:.01};
  const heightAt=(x,z)=>.12*Math.sin(x*.45)+.09*Math.cos(z*.48)
    +.16*Math.sin(z*.19+x*.17) + 2.2*Math.exp(-Math.pow((z+14)/8,2));
  const wind={dir:[1,.4],strength:.92,speed:1.15/1.2};
  const field=makeMeadow({size:[14,14],density:1650,maxBlades:145000,segments:4,
    height:.32,bladeWidth:.012,seed:41,dry:.065,heightAt,
    color:0x537e2c,wind});
  scene.add(field);
  const distant=makeMeadow({size:[24,8],density:250,maxBlades:48000,segments:3,
    height:.42,bladeWidth:.037,seed:114,dry:.065,ground:true,
    heightAt:(x,z)=>heightAt(x,z-11),
    mask:(x,z)=>Math.abs(x)<7 && Math.abs(z-11)<7 ? 0:1,
    color:0x537e2c,wind});
  distant.position.z=-11;scene.add(distant);

  // Rooted stones show leaf-scale occlusion and anchor the field's depth.
  for(const [x,z,s,seed] of [[-1.15,-.4,.9,19],[1.7,-3.8,.72,91],[-3.4,-4.1,.54,44]]) {
    const rock=makeRock({size:[s,s*.62,s*.85],type:'granite',seed,moss:.4,detail:4});
    rock.position.set(x,heightAt(x,z)-.05,z);rock.rotation.y=seed*.47;scene.add(rock);
  }

  const ground=new THREE.PlaneGeometry(260,260,110,110);ground.rotateX(-Math.PI/2);
  const p=ground.attributes.position;
  for(let i=0;i<p.count;i++) {
    const x=p.getX(i),z=p.getZ(i);
    // The real near soil belongs to the meadow; broad hills stay beneath it.
    const far=THREE.MathUtils.smoothstep(Math.hypot(x,z),28,55);
    p.setY(i,heightAt(x,z)+far*(1.6+1.6*Math.sin(x*.065+z*.028)));
  }
  ground.computeVertexNormals();
  const soil=new THREE.MeshStandardMaterial({color:0x293e18,roughness:1});
  patchStandard(soil,{name:'LabMeadowHills',vertexHead:'varying vec3 vHill;',
    vertexBody:'vHill = transformed;',fragmentHead:'varying vec3 vHill;',
    fragmentBody:`
      if(abs(vHill.x)<6.95 && abs(vHill.z)<6.95) discard;
      if(abs(vHill.x)<11.95 && vHill.z< -7.05 && vHill.z> -14.95) discard;
      float field = astraFbm2(vHill.xz*.55,3);
      diffuseColor.rgb *= .79+field*.5;`});
  const earth=new THREE.Mesh(ground,soil);earth.name='DistantMeadow';
  earth.receiveShadow=true;scene.add(earth);
  const cameras=[
    {name:'meadow',position:[1.3,1.3,4.3],lookAt:[.8,.40,-1],fov:36},
    {name:'blades',position:[.60,.76,2.8],lookAt:[.4,.18,1.0],fov:36},
  ];
  return {scene,cameras,update(t,dt){field.userData.update(t,dt);distant.userData.update(t,dt);}};
}
