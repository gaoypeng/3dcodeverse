/** A weathered workshop courtyard: masonry, worn metal, cloth and road detail. */
import * as THREE from 'three';
import * as MAT from '../lib/materials.js';
import { clonePatchedMaterial } from '../lib/shader.js';
import { patchDripStains, patchRust, patchDust } from '../lib/aging.js';
import { patchMicroBreakup } from '../lib/surface_wear.js';
import { patchRoadSurface, patchSeamBand, patchTracks } from '../lib/roadway.js';
import { makeText, loadHelvetiker } from '../lib/signage.js';
import { sunRig } from '../lib/environment.js';
import { makeSky } from '../lib/sky.js';
import { snapshotResources, attachDisposal } from '../lib/lifecycle.js';
import { mulberry32 } from '../lib/noise.js';
import { makePaving } from '../lib/paving.js';

export async function createScene() {
  const scene=new THREE.Scene(),rand=mulberry32(62);
  const rig=sunRig({mood:'day',elevation:34,azimuth:138,bounds:22,disc:false});
  rig.sun.shadow.mapSize.set(4096,4096);rig.sun.shadow.normalBias=.012;
  scene.add(rig.sun,rig.fill);scene.environment=rig.envTex;
  makeSky(scene,{rig,turbidity:2.8});scene.fog=new THREE.FogExp2(0xc5bba5,.012);
  const own=material=>clonePatchedMaterial(material);
  const brick=own(MAT.brick({color:0x795343,bump:.012,roughness:.88}));
  patchDripStains(brick,{from:3.4,strength:.45,scale:.7,seed:19});
  patchDust(brick,{strength:.16,seed:9});
  const stone=own(MAT.travertine({color:0x929082,roughness:.94}));
  patchMicroBreakup(stone,{scale:.16,strength:.15,seed:13});
  const wood=own(MAT.weatheredWood({color:0x655341,roughness:.9}));
  patchDripStains(wood,{strength:.22,scale:.4,seed:23});
  const iron=new THREE.MeshStandardMaterial({color:0x777b78,roughness:.38,metalness:1});
  patchRust(iron,{strength:1,scale:.34,seed:8});
  patchDust(iron,{strength:.12,seed:48});
  const dark=new THREE.MeshStandardMaterial({color:0x181b1c,roughness:.94});

  // Planar world-metre UVs register courses across separately modelled piers.
  function box(name,size,position,material,rotation=0) {
    const g=new THREE.BoxGeometry(...size),p=g.attributes.position,n=g.attributes.normal,uv=g.attributes.uv;
    for(let i=0;i<p.count;i++){
      const x=p.getX(i)+position[0],y=p.getY(i)+position[1],z=p.getZ(i)+position[2];
      if(Math.abs(n.getY(i))>.5)uv.setXY(i,x,z);
      else if(Math.abs(n.getX(i))>.5)uv.setXY(i,z,y);
      else uv.setXY(i,x,y);
    }
    if(material===wood){
      const offset=rand(),along=rand();
      for(let i=0;i<uv.count;i++){
        const u=uv.getX(i),v=uv.getY(i);
        uv.setXY(i,(size[0]>size[1]?v:u)+offset,(size[0]>size[1]?u:v)+along);
      }
    }
    const mesh=new THREE.Mesh(g,material);mesh.name=name;mesh.position.set(...position);mesh.rotation.y=rotation;
    mesh.castShadow=true;mesh.receiveShadow=true;mesh.userData.placement='free';scene.add(mesh);return mesh;
  }
  const earth=own(MAT.soil({color:0x665e4c,roughness:.98,repeat:2}));
  box('CourtyardFoundation',[50,.25,50],[0,-.17,0],earth);
  box('LeftBrickPier',[3.35,3.65,.42],[-3.325,1.825,-3.25],brick);
  box('RightBrickPier',[3.35,3.65,.42],[3.325,1.825,-3.25],brick);
  box('DoorLintelBrick',[3.3,1.05,.42],[0,3.125,-3.25],brick);
  box('WorkshopSideWall',[.4,3.65,5.8],[-5.0,1.825,-6],brick);
  box('WorkshopOtherSide',[.4,3.65,5.8],[5.0,1.825,-6],brick);
  box('ShadowedBackWall',[10,3.5,.3],[0,1.7,-8.8],dark);
  box('WorkshopRoof',[10.65,.18,6.4],[0,3.8,-6.0],dark);
  box('RoofStoneCoping',[10.35,.13,.65],[0,3.7,-3.22],stone);
  box('StoneDoorLintel',[3.65,.25,.62],[0,2.72,-3.14],stone);
  box('DoorStep',[3.7,.16,1.0],[0,.07,-2.85],stone);
  for(const x of [-1.75,1.75])box('StoneDoorJamb',[.22,2.65,.6],[x,1.4,-3.13],stone);
  // A leaf left ajar reveals real interior depth and lets its hinges catch light.
  const leaf=new THREE.Group();leaf.position.set(-1.61,.16,-3.0);leaf.rotation.y=-.48;
  for(let i=0;i<7;i++){
    const board=box('DoorBoard',[.215,2.42,.065],[0,0,0],wood);
    scene.remove(board);leaf.add(board);board.position.set(.117+i*.23,1.21,0);
  }
  for(const y of [.35,1.95]){
    const strap=box('ForgedDoorStrap',[1.43,.085,.025],[0,0,0],iron);
    scene.remove(strap);leaf.add(strap);strap.position.set(.74,y,.055);
  }
  scene.add(leaf);
  const shutter=own(MAT.paintedWood({color:0x495a4a,roughness:.83}));
  patchDripStains(shutter,{strength:.4,scale:.24,seed:12});
  box('ShutterRecess',[2.2,1.45,.06],[-3.25,1.74,-2.994],dark);
  for(let i=0;i<10;i++)box('WindowShutterBoard',[.204,1.37,.045],[-4.25+i*.22,1.74,-2.941],shutter);
  for(const y of [1.22,2.26])box('ShutterBrace',[2.17,.055,.03],[-3.25,y,-2.903],iron);
  const pipe=new THREE.Mesh(new THREE.CylinderGeometry(.047,.047,3.5,16),iron);
  pipe.name='Downpipe';pipe.position.set(4.67,1.8,-2.94);pipe.castShadow=true;scene.add(pipe);
  for(let i=0;i<3;i++)box('InteriorTimberStack',[2.2,.22,.35],[.5,.24+i*.25,-4.6],wood,.08);

  // Real stones give the foreground a walkable profile and joint shadows.
  const yard=makePaving({size:[6.9,11],stoneSize:.24,joint:.009,thickness:.105,
    relief:.014,color:0x64625b,jointColor:0x514b3e,roughness:.89,seed:62});
  yard.position.set(-1.55,.035,2.4);scene.add(yard);
  const roadMat=new THREE.MeshStandardMaterial({color:0x4c4b46,roughness:.9});
  patchRoadSurface(roadMat,{aggregate:.62,wear:.68,patches:.28,gutter:.4,halfWidth:1.7,lane:3.4,seed:36});
  patchSeamBand(roadMat,{width:.26,weeds:.16});
  const road=box('ServiceLane',[3.4,.075,30],[3.65,-.025,3],roadMat);
  // Rotate the geometry's frame with the mesh: the road patch sees this axis.
  road.rotation.y=0;
  for(let i=0;i<22;i++)box('KerbStone',[.22,.18,.49],[1.86,.035,-2.9+i*.51],stone);
  const mud=new THREE.MeshStandardMaterial({color:0x5d5140,roughness:.95});
  patchMicroBreakup(mud,{scale:.13,strength:.22,seed:32});
  patchTracks(mud,{depth:.8,kind:'foot',count:1,offset:.1,seed:42});
  box('SoftVerge',[1.65,.052,12],[-5.5,-.015,2.3],mud);

  // Woven canvas has a sagged silhouette, hanging hem and a fine weave finish.
  const cloth=own(MAT.fabric({color:0x666b4d,repeat:5,bump:.0008,roughness:.94}));
  const clothGeo=new THREE.PlaneGeometry(3.2,1.25,48,18),cp=clothGeo.attributes.position;
  for(let i=0;i<cp.count;i++){
    const x=cp.getX(i),depth=cp.getY(i)+.625;
    cp.setXYZ(i,x,2.76-depth*.23-.13*Math.cos(x/3.2*Math.PI),-3+depth);
  }
  clothGeo.computeVertexNormals();const canopy=new THREE.Mesh(clothGeo,cloth);canopy.name='CanvasAwning';
  canopy.position.x=-3.25;canopy.castShadow=true;canopy.receiveShadow=true;cloth.side=THREE.DoubleSide;scene.add(canopy);
  for(const x of [-4.78,-1.72])box('AwningSupport',[.045,.045,1.45],[x,2.59,-2.43],iron);
  const hemGeometry=new THREE.PlaneGeometry(3.2,.12,48,1),hp=hemGeometry.attributes.position;
  for(let i=0;i<hp.count;i++){
    const x=hp.getX(i),drop=hp.getY(i)-.06;
    hp.setXYZ(i,x,2.4725-.13*Math.cos(x/3.2*Math.PI)+drop,-1.745);
  }
  hemGeometry.computeVertexNormals();const hem=new THREE.Mesh(hemGeometry,cloth);hem.position.x=-3.25;
  hem.name='AwningHem';hem.castShadow=true;scene.add(hem);
  // A small crate gives the fabric and iron real contact and human scale.
  for(let level=0;level<3;level++){
    for(const z of [-.14,.72])box('CrateSideBoard',[1.2,.18,.05],[-2.85,.18+level*.205,z],wood);
    for(const x of [-3.45,-2.25])box('CrateEndBoard',[.05,.18,.88],[x,.18+level*.205,.29],wood);
  }
  for(const x of [-3.4,-2.3])for(const z of [-.1,.68])box('CrateCorner',[.07,.66,.07],[x,.36,z],wood);
  const drum=new THREE.Mesh(new THREE.CylinderGeometry(.38,.37,.92,64,12),iron);
  drum.name='WeatheredSteelDrum';drum.position.set(-4.18,.47,-.23);drum.castShadow=true;drum.receiveShadow=true;scene.add(drum);
  for(const y of [.13,.44,.79]){
    const hoop=new THREE.Mesh(new THREE.TorusGeometry(.382,.015,8,64),iron);hoop.rotation.x=Math.PI/2;
    hoop.position.set(-4.18,y,-.23);hoop.castShadow=true;scene.add(hoop);
  }
  // Irregular chips settle against the kerb and masonry instead of floating.
  for(let i=0;i<36;i++){
    const x=i<20?1.56+rand()*.18:-4.75+rand()*.4,z=-2.5+rand()*9;
    box('LooseStoneChip',[.025+rand()*.065,.018+rand()*.025,.025+rand()*.06],
      [x,.055,z],stone,rand()*Math.PI);
  }
  const signBacking=box('SignBacking',[2.6,.42,.07],[-3.3,3.17,-2.98],dark);
  const font=await loadHelvetiker();
  const sign=await makeText('RIVER WORKSHOP',{font,size:.18,depth:.006,color:0xc3ba97});
  sign.position.set(-3.3,3.10,signBacking.position.z+.045);scene.add(sign);
  scene.userData.grade={exposure:1.0,contrast:1.035,saturation:.96};
  const resources=snapshotResources(scene);resources.add(rig.envTex);
  // Delegate nested effect ownership so their individual disposal guards
  // remain correct when a caller removes one before closing the scene.
  for(const effect of [yard,sign]){
    for(const resource of snapshotResources(effect))resources.delete(resource);
    resources.add({dispose:()=>effect.userData.dispose()});
  }
  attachDisposal(scene,resources);
  return {scene,cameras:[
    {name:'courtyard',position:[7.6,3.6,9.5],lookAt:[-1.0,1.05,-1.2],fov:48},
    {name:'masonry_and_cloth',position:[-5.15,2.02,.9],lookAt:[-3.12,1.91,-2.65],fov:49},
    {name:'stone_and_tracks',position:[-.8,.65,6.5],lookAt:[1.2,.15,1.5],fov:51},
    {name:'rust_and_grain',position:[-3.35,1.05,1.35],lookAt:[-3.7,.75,-.12],fov:45},
  ],update(){},dispose(){scene.userData.dispose();}};
}
