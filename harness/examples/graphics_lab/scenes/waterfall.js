/** An old stone weir in a wooded stream: a falling sheet and a shallow plunge pool. */
import * as THREE from 'three';
import { ConvexGeometry } from 'three/addons/geometries/ConvexGeometry.js';
import { makeWaterfall } from '../lib/waterfall.js';
import { makeStream } from '../lib/stream.js';
import { makeRock } from '../lib/rock.js';
import { makeMeadow } from '../lib/meadow.js';
import { makeTree, makeShrub } from '../lib/tree.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { patchTriplanar } from '../lib/terrain_shade.js';
import { patchStandard } from '../lib/shader.js';
import { mulberry32, fbm2 } from '../lib/noise.js';

export function createScene() {
  const scene = new THREE.Scene(), height = 1.85;
  const rig = sunRig({mood:'day',azimuth:138,elevation:44,bounds:14,
    intensity:4.1,fill:.82,disc:false});
  scene.add(rig.sun,rig.fill); scene.environment=rig.envTex;
  rig.sun.shadow.mapSize.set(4096,4096); rig.sun.shadow.normalBias=.009;
  makeSky(scene,{rig,turbidity:3.2,scale:1800});
  scene.fog=new THREE.FogExp2(0x9ca99b,.018);
  const waterfall=makeWaterfall({height,width:2.2,speed:.9,thickness:.055,
    breakup:.68,foam:.56,quality:'high',seed:17,sprayRate:280});
  // Surface froth has a little height above the receiving stream's wave crests.
  waterfall.getObjectByName('WaterfallImpactFoam').position.y=.037;
  scene.add(waterfall);
  const upstream=makeStream({points:[[.6,height,-13],[.2,height,-6],[0,height,-2],[0,height,0]],
    width:2.2,widthVariation:0,endFade:.65,depth:.22,speed:.9,segments:120,widthSegments:20,
    stoneCount:140,obstacles:[],seed:88,roughness:.20,reflectionSize:0,
    bedColor:0x4b4833,waterColor:0x92af88,attenuationDistance:4.2});
  const downstream=makeStream({points:[[0,0,-.02],[0,0,2.1],[.32,-.035,5],[.9,-.10,13]],
    width:3.8,widthVariation:.11,depth:.43,speed:.72,segments:180,widthSegments:28,
    stoneCount:420,seed:81,roughness:.26,reflectionSize:512,
    bedColor:0x5a503c,waterColor:0x647f59,attenuationDistance:4.5,obstacles:[]});
  scene.add(upstream,downstream);
  const smooth=(a,b,v)=>THREE.MathUtils.smoothstep(v,a,b);
  const lowerSamples=Array.from({length:261},(_,i)=>downstream.userData.sample(i/260));
  const upperSamples=Array.from({length:161},(_,i)=>upstream.userData.sample(i/160));
  const closest=(x,z,samples)=>{
    let best=samples[0],distance=Infinity;
    for(const sample of samples) {
      const d=(x-sample.position.x)**2+(z-sample.position.z)**2;
      if(d<distance){distance=d;best=sample;}
    }
    return {sample:best,offset:Math.sqrt(distance)-best.width*.5};
  };
  const heightAt=(x,z)=>{
    // Only the short built weir retains a sharp level change. The earth at its
    // ends grades continuously between upstream and downstream elevations.
    const upper=closest(x,z,upperSamples),lower=closest(x,z,lowerSamples);
    const span=.01+2.0*smooth(1.7,3.0,Math.abs(x));
    const blend=smooth(-.28-span,-.28+span,z);
    const waterY=THREE.MathUtils.lerp(upper.sample.position.y,lower.sample.position.y,blend);
    const offset=THREE.MathUtils.lerp(upper.offset,lower.offset,blend);
    const outside=Math.max(0,offset),noise=fbm2(x*.32,z*.32,{seed:37});
    const grade=height*(1-smooth(-2.3-Math.abs(x)*.13,2.0+Math.abs(x)*.12,z));
    const hill=.55*Math.sin(x*.24+z*.13)+.30*Math.cos(z*.28-x*.16)+noise*.35;
    const upland=grade+.25+hill*smooth(.3,3.2,outside);
    const bank=waterY-.025+outside*.16+noise*.07;
    if(Math.abs(x)<1.23 && z>-.28 && z<.9) return -.63;
    const channel=waterY-.66;
    const shore=THREE.MathUtils.lerp(channel,bank,smooth(-.17,.045,offset));
    return THREE.MathUtils.lerp(shore,upland,smooth(.30,2.8,outside));
  };
  const groundMaterial=new THREE.MeshStandardMaterial({color:0x4c4c32,roughness:.98});
  patchTriplanar(groundMaterial,{scale:.24,relief:.006,roughness:.98,colorA:0x20291a,colorB:0x4c5032});
  const groundGeometry=new THREE.PlaneGeometry(46,54,240,280);groundGeometry.rotateX(-Math.PI/2);
  const p=groundGeometry.attributes.position;
  for(let i=0;i<p.count;i++) p.setY(i,heightAt(p.getX(i),p.getZ(i)));
  groundGeometry.computeVertexNormals();
  const ground=new THREE.Mesh(groundGeometry,groundMaterial);
  ground.name='WoodlandBanks';ground.receiveShadow=true;scene.add(ground);

  // Weathered masonry explains the straight spillway. The banks roll around
  // its abutments instead of continuing as a sheer, uninterrupted earth wall.
  const random=mulberry32(47),masonry=new THREE.Group();masonry.name='OldStoneWeir';
  const materials=[],wetMaterials=[];
  for(let i=0;i<8;i++) {
    for(const wet of [false,true]) {
      const material=new THREE.MeshStandardMaterial({roughness:wet?.50:.93});
      const tint=(random()-.5)*.11;
      patchTriplanar(material,{scale:.053,relief:.004,roughness:wet?.50:.93,
        colorA:new THREE.Color(wet?0x343d30:0x575a4c).offsetHSL(0,0,tint*.6),
        colorB:new THREE.Color(wet?0x59634f:0x777463).offsetHSL(0,0,tint)});
      patchStandard(material,{name:'WeirStoneAge',fragmentBody:`
        float weirColony=smoothstep(.36,.66,astraFbm2(vAstraWorld.xz*8.0+vAstraWorld.y*2.6,3));
        float weirMoss=(pow(max(0.0,vAstraWorldN.y),3.0)*.36
          +exp(-max(0.0,vAstraWorld.y)*3.0)*.57)*weirColony;
        diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.058,.075,.025),weirMoss);
        float weirWet=(1.0-smoothstep(.03,.43,vAstraWorld.y))*.38;
        diffuseColor.rgb*=1.0-weirWet;`});
      (wet?wetMaterials:materials).push(material);
    }
  }
  let blockId=0;
  const block=(name,x,y,z,w,h,d,wet=false)=>{
    // Broken corners give each stone a broad face and a rough, chipped outline.
    // Face planes remain connected; this is dressed rubble, not a pile of spheres.
    const vertices=[],shear=(random()-.5)*.065,tilt=(random()-.5)*.035;
    for(const sx of [-1,1])for(const sy of [-1,1])for(const sz of [-1,1]) {
      const px=sx*w*.50+sy*shear,py=sy*h*.50, pz=sz*d*.50+sx*tilt;
      const cx=w*(.025+random()*.11),cy=h*(.045+random()*.19),cz=d*(.015+random()*.045);
      vertices.push(new THREE.Vector3(px-sx*cx,py,pz),new THREE.Vector3(px,py-sy*cy,pz),
        new THREE.Vector3(px,py,pz-sz*cz));
    }
    const material=(wet?wetMaterials:materials)[blockId++%8];
    const mesh=new THREE.Mesh(new ConvexGeometry(vertices),material);
    mesh.name=name;mesh.position.set(x,y,z);mesh.rotation.z=(random()-.5)*.025;
    mesh.castShadow=true;mesh.receiveShadow=true;masonry.add(mesh);return mesh;
  };
  // A recessed backing fills small mortar joints without a bright earth leak.
  const backing=new THREE.Mesh(new THREE.BoxGeometry(3.10,2.05,.45),
    new THREE.MeshStandardMaterial({color:0x242820,roughness:.95}));
  backing.position.set(0,.8,-.30);backing.receiveShadow=true;masonry.add(backing);
  for(let row=0;row<7;row++) {
    let left=-1.65;
    while(left<1.65) {
      const w=Math.min(1.65-left,.36+random()*.32);if(w<.07)break;
      block('WetWeirCourse',left+w*.5,-.15+row*.295,-.23-random()*.025,w-.008,.28,.44,true);
      left+=w;
    }
  }
  // Coping sits below the six-centimetre water layer at the crest.
  for(let i=0;i<6;i++)block('SubmergedCoping',-1.10+(i+.5)*2.2/6,height-.145,-.34,
    2.2/6-.005,.16,.76,true);
  for(const side of [-1,1]) {
    for(let row=0;row<8;row++) {
      let edge=1.15;
      while(edge<2.50) {
        const w=Math.min(2.50-edge,.30+random()*.36);if(w<.06)break;
        block('WeirAbutment',side*(edge+w*.5),-.10+row*.30,-.22-random()*.04,w,.282,.90+random()*.12);
        edge+=w;
      }
    }
    for(let j=0;j<3;j++)block('WornAbutmentCap',side*(1.36+j*.45),2.16,-.24,.46,.17,1.02);
    for(let i=0;i<4;i++)block('UpstreamWing',side*(1.43+i*.05),height+.15,-1.0-i*.54,.47,.52,.53);
  }
  scene.add(masonry);

  const stones=[];
  for(let i=0;i<59;i++) {
    const side=i%2?1:-1,z=-5+random()*14;
    const reference=closest(0,z,z<-.3?upperSamples:lowerSamples).sample;
    const x=reference.position.x+side*(reference.width*.5+.06+random()*.60);
    if(Math.abs(z)<.95)continue;
    const size=[.19+random()*.48,.13+random()*.30,.20+random()*.52];
    const stone=makeRock({type:i%5===0?'sandstone':'basalt',size,seed:310+i,detail:4,
      color:i%5===0?0x71674d:0x565b4a,moisture:.52,moss:.18,weathering:1.1});
    stone.position.set(x,heightAt(x,z)-size[1]*.24,z);stone.rotation.y=random()*6.28;
    scene.add(stone);stones.push({x,z,r:Math.max(size[0],size[2])*.48});
  }
  for(const [x,z,s] of [[-2.25,1.0,1.0],[2.62,.60,.8],[-2.55,-.55,.76],[2.9,-1.4,1.10]]) {
    const rock=makeRock({type:'sandstone',size:[s,s*.62,s*.8],seed:700+Math.round(s*100),
      detail:5,color:0x65624c,moisture:.32,moss:.35,weathering:1.3});
    rock.position.set(x,heightAt(x,z)-s*.20,z);rock.rotation.y=random()*6.28;scene.add(rock);
    stones.push({x,z,r:s*.48});
  }
  const meadow=makeMeadow({size:[17,23],height:.26,density:1400,maxBlades:230000,segments:4,seed:83,
    color:0x4c6428,bladeWidth:.014,dry:.12,ground:false,seedHeads:.002,heightAt,
    mask:(x,z)=>{
      const {offset}=closest(x,z,z<-.3?upperSamples:lowerSamples);
      let mask=smooth(.03,.46,offset)*(.70+.30*smooth(.15,.8,.5+2*fbm2(x*.8,z*.8,{seed:16})));
      mask*=1-smooth(.88,1,Math.max(Math.abs(x)/8.5,Math.abs(z)/11.5));
      if(Math.abs(x)<2.52&&z>-.76&&z<.34)return 0;
      if(Math.abs(Math.abs(x)-1.52)<.42&&z<-1&&z>-3.1)return 0;
      for(const rock of stones)mask*=smooth(rock.r*.45,rock.r,Math.hypot(x-rock.x,z-rock.z));
      return mask;
    },wind:{direction:[1,.4],strength:.28,speed:.7}});
  scene.add(meadow);
  const distantGrass=makeMeadow({size:[42,47],height:.22,density:100,maxBlades:50000,segments:3,seed:21,
    color:0x526332,bladeWidth:.018,dry:.16,ground:false,seedHeads:0,heightAt,
    mask:(x,z)=>smooth(.83,1,Math.max(Math.abs(x)/8.5,Math.abs(z)/11.5))*smooth(.10,.9,
      closest(x,z,z<-.3?upperSamples:lowerSamples).offset),
    wind:{direction:[1,.4],strength:.24,speed:.7}});
  scene.add(distantGrass);
  const plants=[];
  for(const [species,x,z,h,r,seed] of [
    ['willow',-4.4,-3.8,5.8,2.65,37],['birch',4.8,-6.4,6.7,2.0,83],
    ['oak',-7.5,-8.2,7.0,3.3,52],['birch',1.5,-11.0,5.8,1.9,73],
  ]) {
    const tree=makeTree({species,height:h,crownRadius:r,seed,leafDensity:.58,maxLeaves:species==='willow'?5200:3000,
      wind:{dir:[1,.4],strength:.24,speed:.7},shadows:true});
    tree.position.set(x,heightAt(x,z)-.06,z);scene.add(tree);plants.push(tree);
  }
  for(const [x,z,h,seed] of [[-3.0,-.5,1.25,10],[3.1,-2.2,1.5,18],[-3.1,3.4,.82,14],
    [4.4,1.5,1.15,48],[-6,-4.8,2.25,63],[3.8,-8.3,2.7,82]]) {
    const shrub=makeShrub({height:h,crownRadius:h*.67,seed,leafDensity:.42,maxLeaves:1600,
      wind:{dir:[1,.4],strength:.20,speed:.7}});
    shrub.position.set(x,heightAt(x,z)-.055,z);scene.add(shrub);plants.push(shrub);
  }
  return {scene,cameras:[
    {name:'basalt_falls',position:[3.3,2.0,4.9],lookAt:[0,.95,.35],fov:43},
    {name:'falling_film',position:[1.28,1.05,2.42],lookAt:[.02,.95,.48],fov:38},
    {name:'spillway_lip',position:[2.8,3.2,1.9],lookAt:[0,1.52,-.12],fov:42},
    {name:'woodland_weir',position:[4.6,2.6,7.0],lookAt:[-.2,1.30,-.4],fov:43},
  ],update(t,dt){
    waterfall.userData.update(t,dt);upstream.userData.update(t,dt);downstream.userData.update(t,dt);
    meadow.userData.update(t,dt);distantGrass.userData.update(t,dt);
    for(const plant of plants)plant.userData.update(t,dt);
  }};
}
