/** Rain on a woodland shelter: covered timber, wet gravel and reflected trees. */
import * as THREE from 'three';
import { makeRain, makeSplashes, wetten } from '../lib/rain.js';
import { makeRock } from '../lib/rock.js';
import { makeTree, makeShrub } from '../lib/tree.js';
import { makeMeadow } from '../lib/meadow.js';
import { sunRig } from '../lib/environment.js';
import { makeMirrorFloor } from '../lib/wetground.js';
import { patchTriplanar } from '../lib/terrain_shade.js';
import { weatheredWood } from '../lib/materials.js';
import { mergeStatic } from '../lib/merge.js';
import { mulberry32, fbm2 } from '../lib/noise.js';

const smooth=(a,b,value)=>THREE.MathUtils.smoothstep(value,a,b);
export const BOUNDS = { min: [-38, -.5, -38], max: [38, 15, 38] };
export function heightAt(x,z) {
  const clearing=Math.hypot(x/10.0,(z+1)/11.8);
  return -.018+smooth(.61,1.14,clearing)*(.16+Math.abs(fbm2(x*.18,z*.18,{seed:101}))*1.8)
    +smooth(1.1,2.8,clearing)*(.48+.25*Math.sin(x*.2+z*.11));
}

export function createScene() {
  const scene=new THREE.Scene(),owned=new Set(),effects=[],rand=mulberry32(35);
  const own=value=>{owned.add(value);return value;};
  const effect=value=>{effects.push(value);scene.add(value);return value;};
  const rig=sunRig({mood:'overcast',intensity:1.35,fill:1.55,bounds:23,disc:false,
    azimuth:135,elevation:46});
  rig.sun.shadow.mapSize.set(2048,2048);rig.sun.shadow.normalBias=.012;
  scene.add(rig.sun,rig.fill);scene.environment=rig.envTex;
  scene.background=new THREE.Color(0x88979d);scene.fog=new THREE.FogExp2(0x88979d,.018);

  const groundGeometry=own(new THREE.PlaneGeometry(76,76,100,100));groundGeometry.rotateX(-Math.PI/2);
  const p=groundGeometry.attributes.position;
  for(let i=0;i<p.count;i++)p.setY(i,heightAt(p.getX(i),p.getZ(i)));
  groundGeometry.computeVertexNormals();
  const soil=own(new THREE.MeshStandardMaterial({color:0x464b39,roughness:.95}));
  patchTriplanar(soil,{scale:.11,relief:.0015,roughness:.93,colorA:0x242923,colorB:0x58604a});
  const ground=new THREE.Mesh(groundGeometry,soil);ground.name='RaisedForestFloor';ground.receiveShadow=true;
  scene.add(ground);
  const wetFloor=effect(makeMirrorFloor(32,36,{seed:7,rttSize:1024,color:0x727979,
    overlayColor:0x554f42,overlayOpacity:.84,puddleMask:true,ripple:.10,rippleScale:10,detail:1}));
  wetFloor.name='RainFilledClearing';

  const timber=new THREE.Group(),roofParts=new THREE.Group();
  timber.name='ShelterCarpentry';roofParts.name='PitchedSheetRoof';
  const wood=weatheredWood({color:0x79654d,roughness:.91,bump:.0008,scale:5,contrast:.20,seed:24});
  const roofMaterial=own(new THREE.MeshStandardMaterial({color:0x4b5149,roughness:.69,metalness:.14}));
  wetten(roofMaterial,.65,{minRoughness:.25,darken:.22});
  let part=0;
  const box=(name,size,position,material=wood,rotation=[0,0,0],parent=timber)=>{
    const geometry=new THREE.BoxGeometry(...size),pos=geometry.attributes.position,uv=geometry.attributes.uv;
    if(material===wood){
      // Growth grain follows the long axis of each board in metric units.
      const along=size.indexOf(Math.max(...size)),across=(along+1)%3;
      for(let i=0;i<uv.count;i++)uv.setXY(i,pos.getComponent(i,across)*.45+part*.117,
        pos.getComponent(i,along)*.32+part*.173);
    }
    const mesh=new THREE.Mesh(geometry,material);mesh.name=name;mesh.position.set(...position);mesh.rotation.set(...rotation);
    mesh.castShadow=mesh.receiveShadow=true;parent.add(mesh);part++;return mesh;
  };
  const beam=(name,a,b,width,depth=width)=>{
    const from=new THREE.Vector3(...a),to=new THREE.Vector3(...b),direction=to.clone().sub(from);
    const mesh=box(name,[width,direction.length(),depth],from.add(to).multiplyScalar(.5).toArray());
    mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),direction.normalize());return mesh;
  };
  for(let plank=0;plank<25;plank++)box('DeckBoard',[.277,.065,6.8],[-3.456+plank*.288,.104,0]);
  for(const x of [-3.5,3.5])for(const z of [-3.4,3.4]){
    box('Upright',[.24,3.68,.24],[x,1.84,z]);
    for(const sign of [-1,1])if(Math.abs(x+sign*.90)<3.6)
      beam('KneeBrace',[x,2.77,z],[x+sign*.90,3.56,z],.13);
    beam('LongBrace',[x,2.91,z],[x,3.54,z-Math.sign(z)*.82],.13);
  }
  for(const z of [-3.4,3.4])box('CrossBeam',[7.3,.26,.25],[0,3.60,z]);
  for(const x of [-3.5,3.5])box('LongBeam',[.22,.22,7.1],[x,3.52,0]);
  box('RidgeBeam',[.17,.22,8.12],[0,4.39,0]);
  for(const z of [-3.85,-2.3,-.76,.76,2.3,3.85])for(const side of [-1,1])
    beam('RoofRafter',[0,4.36,z],[side*4.1,3.45,z],.10,.18);
  for(const side of [-1,1]){
    box('RoofPanel',[4.3,.055,8.4],[side*2.06,4.0,0],roofMaterial,[0,0,-side*.22],roofParts);
    for(let k=-5;k<=5;k++)box('StandingSeam',[4.3,.029,.035],[side*2.06,4.045,k*.78],
      roofMaterial,[0,0,-side*.22],roofParts);
    box('EaveTrim',[.075,.13,8.44],[side*4.15,3.55,0],roofMaterial,[0,0,0],roofParts);
  }
  // Bench and a small table make the roof's physical scale legible.
  for(const x of [-2.9,2.9]){
    for(let plank=0;plank<3;plank++)box('BenchSeat',[.145,.048,4.55],[x+(plank-1)*.157,.50,0]);
    for(const z of [-1.8,1.8]){
      box('BenchLeg',[.095,.40,.15],[x,.30,z]);
      box('BenchBearer',[.54,.085,.17],[x,.43,z]);
      box('BackrestUpright',[.06,.67,.08],[x+Math.sign(x)*.26,.73,z]);
    }
    for(let plank=0;plank<2;plank++)box('BenchBack',[.045,.15,4.50],[x+Math.sign(x)*.26,.81+plank*.17,0]);
  }
  for(let i=0;i<4;i++)box('TableTop',[1.5,.06,.17],[0,.86,-1.72+i*.182]);
  for(const x of [-.55,.55])for(const z of [-1.62,-1.12])box('TableLeg',[.09,.69,.09],[x,.49,z]);
  const collectParts=(parts,name)=>{
    const result=mergeStatic([parts],{variance:.05});result.name=name;
    parts.traverse(o=>o.geometry?.dispose());return effect(result);
  };
  collectParts(timber,'CoveredTimberStructure');
  const roof=collectParts(roofParts,'RainShelterRoof');
  // A soft, warm sheltered lamp contrasts with the cool wet clearing.
  const lampMaterial=own(new THREE.MeshStandardMaterial({color:0xffe4b5,emissive:0xffcb80,emissiveIntensity:2,roughness:.5}));
  const shadeMaterial=own(new THREE.MeshStandardMaterial({color:0x202826,roughness:.55,metalness:.4}));
  const bulb=new THREE.Mesh(own(new THREE.SphereGeometry(.058,16,12)),lampMaterial);bulb.position.set(0,3.47,-1.35);scene.add(bulb);
  const shade=new THREE.Mesh(own(new THREE.ConeGeometry(.24,.15,24,1,true)),shadeMaterial);
  shade.position.set(0,3.55,-1.35);shade.material.side=THREE.DoubleSide;scene.add(shade);
  const lamp=own(new THREE.PointLight(0xffd69d,13,8,2));lamp.position.copy(bulb.position);scene.add(lamp);

  for(let i=0;i<48;i++){
    const side=i%2?1:-1,x=side*(6.0+rand()*8),z=-13+rand()*27;
    const s=.17+Math.pow(rand(),2)*1.0;
    const rock=makeRock({seed:101+i,type:i%4?'granite':'sandstone',size:[s*1.3,s*.55,s],
      detail:4,color:0x697266,moisture:.82,moss:.30,weathering:1});
    rock.position.set(x,heightAt(x,z)-s*.12,z);rock.rotation.y=rand()*6.28;effect(rock);
  }
  effect(makeMeadow({size:[34,36],height:.27,density:720,maxBlades:240000,
    bladeWidth:.012,segments:5,seed:83,color:0x486032,dry:.14,ground:false,seedHeads:.001,
    heightAt,mask:(x,z)=>smooth(.78,1.18,Math.hypot(x/10,(z+1)/11.8))
      *(.40+.6*smooth(-.15,.16,fbm2(x*.32,z*.32,{seed:85}))),
    wind:{dir:[.65,.3],strength:.36,speed:.7/1.2}}));
  // A close strip has enough short blades to read as turf at eye level.
  // Sampling converts its local patch coordinates back to the terrain frame.
  const turf=makeMeadow({size:[16,16],height:.13,density:3000,maxBlades:300000,
    bladeWidth:.005,segments:4,seed:89,color:0x485b34,dry:.12,ground:false,seedHeads:0,
    heightAt:(x,z)=>heightAt(x+5,z+8),
    mask:(x,z)=>smooth(.83,1.08,Math.hypot((x+5)/10,(z+9)/11.8))
      *(.5+.5*smooth(-.16,.2,fbm2((x+5)*.32,(z+8)*.32,{seed:85}))),
    wind:{dir:[.65,.3],strength:.20,speed:.7/1.2}});
  turf.position.set(5,0,8);effect(turf);
  for(let i=0;i<18;i++){
    const angle=2.2+i*2.39996,r=14+rand()*16,x=Math.cos(angle)*r,z=Math.sin(angle)*r-4;
    if(z>0&&x>3)continue; // Keep the established exterior camera's sightline open.
    const species=i%4===0?'birch':i%3===0?'willow':'oak';
    const tree=makeTree({species,seed:800+i,height:6+rand()*2.5,crownRadius:2.3+rand()*1.1,
      maxLeaves:17000,leafDensity:1.1,leafSegments:4,shadows:false,wind:{dir:[.65,.3],strength:.16,speed:.7}});
    tree.position.set(x,heightAt(x,z)-.045,z);effect(tree);
  }
  for(const [x,z,h] of [[-7,-2,1.8],[7,-5,2.4],[-5,-9,1.4],[9,3,1.6],[-9,8,1.3],[4,-12,2.3]]){
    const shrub=makeShrub({seed:1010+Math.round(x*3+z),height:h,crownRadius:h*.7,
      maxLeaves:1100,leafDensity:.42,wind:{dir:[.65,.3],strength:.16,speed:.7}});
    shrub.position.set(x,heightAt(x,z)-.035,z);effect(shrub);
  }
  // Small physical gravel interrupts the wet clearing without hiding its pools.
  const gravelGeometry=own(new THREE.IcosahedronGeometry(1,1));
  const gravelMaterial=own(new THREE.MeshStandardMaterial({color:0x8d9380,roughness:.55}));
  const gravel=own(new THREE.InstancedMesh(gravelGeometry,gravelMaterial,1600));
  gravel.name='WetGravel';gravel.receiveShadow=true;
  const pebble=new THREE.Object3D(),tint=new THREE.Color();
  for(let i=0;i<gravel.count;i++){
    const angle=rand()*Math.PI*2,r=Math.sqrt(rand())*9.5;
    const x=Math.cos(angle)*r,z=Math.sin(angle)*r*1.12-1;
    const size=.007+Math.pow(rand(),2)*.028;
    const y=Math.max(.004,heightAt(x,z));
    pebble.position.set(x,y+size*.26,z);pebble.scale.set(size,size*.50,size*(.6+rand()*.5));
    pebble.rotation.set(rand(),rand()*6.28,rand());pebble.updateMatrix();gravel.setMatrixAt(i,pebble.matrix);
    tint.setHSL(.10+rand()*.08,.10+rand()*.10,.14+rand()*.11);gravel.setColorAt(i,tint);
  }
  gravel.instanceMatrix.needsUpdate=true;gravel.instanceColor.needsUpdate=true;scene.add(gravel);
  const surfaces=[roof,ground];
  // The authored terrain function avoids casting every rain-atlas ray through
  // the full terrain mesh whenever the camera moves. Roof rays remain exact.
  effect(makeRain({seed:5,count:11000,radius:24,height:22,length:.46,
    surfaces:[roof],heightAt,groundY:0,shelterResolution:48,shelterUpdateDistance:3}));
  effect(makeSplashes({seed:11,count:720,surfaces,surfaceBias:.35,
    area:{x:0,z:0,w:32,d:32},y:0,size:.13,opacity:.42}));
  // Library updates accept absolute time, including scrub and rewind.
  return {scene,cameras:[
    {name:'courtyard',position:[13,4.1,16],lookAt:[0,1.8,0],fov:51},
    {name:'sheltered',position:[.5,1.55,2.7],lookAt:[.5,1.55,-9],fov:62},
    {name:'rain_and_reflections',position:[7.5,.78,9.7],lookAt:[-.8,1.2,-1.4],fov:46},
  ],update(t,dt){for(const item of effects)item.userData.update?.(t,dt);},
  dispose(){for(const item of effects)item.userData.dispose?.();for(const item of owned)item.dispose();rig.dispose();}};
}
