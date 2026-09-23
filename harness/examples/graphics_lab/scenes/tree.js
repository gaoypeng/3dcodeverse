/** A sunlit woodland edge: connected oak limbs, birch bark and a low hazel-like understory. */
import * as THREE from 'three';
import {makeTree,makeShrub} from '../lib/tree.js';
import {makeMeadow} from '../lib/meadow.js';
import {makeRock} from '../lib/rock.js';
import {makeImposters} from '../lib/woodland.js';
import {sunRig} from '../lib/environment.js';
import {makeSky} from '../lib/sky.js';
import {patchStandard} from '../lib/shader.js';

export function createScene(){
  const scene=new THREE.Scene();
  const rig=sunRig({mood:'day',azimuth:125,elevation:31,bounds:12,intensity:4,fill:1.2});
  scene.add(rig.sun,rig.sun.target,rig.fill);scene.environment=rig.envTex;makeSky(scene,{rig});
  rig.sun.shadow.mapSize.set(4096,4096);rig.sun.shadow.normalBias=.004;rig.sun.shadow.radius=1.8;
  scene.fog=new THREE.FogExp2(0xb5c3b5,.015);
  scene.userData.grade={contrast:1.03,saturation:.94,warmth:.01};
  const heightAt=(x,z)=>.08*Math.sin(x*.5)+.055*Math.sin(z*.4+x*.25);
  const wind={dir:[1,.3],strength:.75,speed:.85};
  const oak=makeTree({species:'oak',height:6.8,crownRadius:2.8,seed:23,wind});
  oak.position.set(-.8,heightAt(-.8,0),0);scene.add(oak);
  const birch=makeTree({species:'birch',height:6.6,crownRadius:1.5,leafDensity:.30,seed:76,wind});
  birch.position.set(4,heightAt(4,-3),-3);scene.add(birch);
  const willow=makeTree({species:'willow',height:5.3,crownRadius:2.1,leafDensity:.28,seed:12,wind});
  willow.position.set(-5.2,heightAt(-5.2,-5),-5);scene.add(willow);
  const shrub=makeShrub({height:1.35,crownRadius:1.1,leafSize:.09,leafDensity:.22,seed:41,wind});
  shrub.position.set(3.3,heightAt(3.3,1),1);scene.add(shrub);
  const grass=makeMeadow({size:[19,17],height:.30,density:550,maxBlades:80000,segments:3,bladeWidth:.022,
    seed:39,color:0x526b2c,dry:.12,heightAt,wind,ground:false,seedHeads:.001,
    mask:(x,z)=>(.25+.75*THREE.MathUtils.smoothstep(Math.hypot(x+.8,z),.35,1.1))
      *THREE.MathUtils.smoothstep(Math.min(9.5-Math.abs(x),8.5-Math.abs(z)),0,2)});
  scene.add(grass);
  const terrain=new THREE.PlaneGeometry(600,600,220,220);terrain.rotateX(-Math.PI/2);
  const p=terrain.attributes.position;
  const earthHeight=(x,z)=>heightAt(x,z)-.008+1.7*Math.exp(-(((z+29)/14)**2))*THREE.MathUtils.smoothstep(-z,15,30);
  for(let i=0;i<p.count;i++){
    const x=Math.sign(p.getX(i))*300*Math.pow(Math.abs(p.getX(i))/300,1.8);
    const z=Math.sign(p.getZ(i))*300*Math.pow(Math.abs(p.getZ(i))/300,1.8);
    p.setXYZ(i,x,earthHeight(x,z),z);
  }
  terrain.computeVertexNormals();
  const material=new THREE.MeshStandardMaterial({color:0x34321e,roughness:1,envMapIntensity:.65});
  patchStandard(material,{name:'TreeGladeEarth',vertexHead:'varying vec3 vGlade;',vertexBody:'vGlade=transformed;',
    uniforms:{uGladeFar:{value:new THREE.Color(0x5d6544)}},
    fragmentHead:'varying vec3 vGlade; uniform vec3 uGladeFar;',
    fragmentBody:`float earthDistance=smoothstep(10.,26.,length(vGlade.xz));
      diffuseColor.rgb=mix(diffuseColor.rgb,uGladeFar,earthDistance);
      diffuseColor.rgb*=.66+.62*astraFbm2(vGlade.xz*14.,3);
      float litter=astraFbm2(vGlade.xz*2.1,2);
      diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.08,.063,.027),smoothstep(.48,.70,litter)*.4);`});
  const ground=new THREE.Mesh(terrain,material);ground.receiveShadow=true;scene.add(ground);
  const distant=makeImposters({source:'tree',count:230,extent:110,height:8,seed:51,
    color:0x49672b,rig,wind,heightAt:(x,z)=>earthHeight(x,z-142)});
  distant.position.z=-142;scene.add(distant);
  const rock=makeRock({size:[1.1,.55,.8],seed:97,type:'granite',detail:3,moss:.4});
  rock.position.set(-2.5,heightAt(-2.5,2.2)-.08,2.2);scene.add(rock);
  const animated=[oak,birch,willow,shrub,grass,distant];
  return {scene,cameras:[
    {name:'woodland',position:[9.5,4.3,13.8],lookAt:[-.4,3.15,-.7],fov:43},
    {name:'branches',position:[2.7,3.5,4.5],lookAt:[-.1,3.55,-.1],fov:42},
  ],update(t,dt){for(const object of animated)object.userData.update(t,dt);}};
}
