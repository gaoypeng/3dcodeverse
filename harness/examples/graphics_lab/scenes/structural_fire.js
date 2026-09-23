/** A authored damaged cottage with one shared fire/soot field. No structural simulation. */
import * as THREE from 'three';
import {makeFireField} from '../lib/firefield.js';
import {makeMeadow} from '../lib/meadow.js';
import {makeTree} from '../lib/tree.js';
import {patchStandard} from '../lib/shader.js';
import {mulberry32} from '../lib/noise.js';
import {snapshotResources,attachDisposal} from '../lib/lifecycle.js';

export function createScene(){
 const scene=new THREE.Scene();scene.background=new THREE.Color(0x3b4652);scene.fog=new THREE.FogExp2(0x3b4652,.014);
 scene.add(new THREE.HemisphereLight(0xbdcbd7,0x28211c,.8));
 const sky=new THREE.DirectionalLight(0xc1d2e0,2);sky.position.set(-8,16,4);sky.castShadow=true;sky.shadow.mapSize.set(2048,2048);
 Object.assign(sky.shadow.camera,{left:-12,right:12,top:12,bottom:-12});sky.shadow.normalBias=.018;scene.add(sky);
 const random=mulberry32(251),actors=[],opaque=new THREE.Group();opaque.name='BorrowedHouseOccluders';scene.add(opaque);
 const earthMat=new THREE.MeshStandardMaterial({color:0x666252,roughness:1});
 patchStandard(earthMat,{name:'CottageYard',vertexHead:'varying vec3 vYard;',vertexBody:'vYard=position;',fragmentHead:'varying vec3 vYard;',fragmentBody:'diffuseColor.rgb*=.65+astraFbm2(vYard.xy*7.0,3)*.6;'});
 const groundGeometry=new THREE.PlaneGeometry(90,90,80,80),gp=groundGeometry.attributes.position;
 for(let i=0;i<gp.count;i++){const x=gp.getX(i),z=-gp.getY(i),edge=Math.max(0,Math.hypot(x,z)-14)/30;gp.setZ(i,edge*.8*(1+Math.sin(x*.21+z*.13)));}groundGeometry.computeVertexNormals();
 const ground=new THREE.Mesh(groundGeometry,earthMat);ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;ground.name='EarthYard';opaque.add(ground);
 const plaster=new THREE.MeshStandardMaterial({color:0xa39682,roughness:.96});
 patchStandard(plaster,{name:'SmokeStainedPlaster',vertexHead:'varying vec3 vWall;',vertexBody:'vWall=(modelMatrix*vec4(position,1.0)).xyz;',fragmentHead:'varying vec3 vWall;',fragmentBody:`
  float n=astraFbm2(vWall.xy*3.0+vWall.z*.2,4);
  float soot=smoothstep(1.2,2.8,vWall.y)*(.28+.7*astraNoise2(vWall.xz*1.4));
  diffuseColor.rgb*=mix(.74+n*.4,.17+n*.14,soot);
 `});
 const char=new THREE.MeshStandardMaterial({color:0x251f1c,roughness:.94});
 patchStandard(char,{name:'CharredStructuralTimber',vertexHead:'varying vec3 vBeam;',vertexBody:'vBeam=position;',fragmentHead:'varying vec3 vBeam;',fragmentBody:`
  float grain=astraFbm2(vBeam.xy*vec2(36.0,5.0)+vBeam.z*4.0,3);diffuseColor.rgb*=.5+grain;
 `,outputBody:`
  float split=pow(max(0.0,sin(vBeam.x*109.0+astraNoise2(vBeam.xy*8.0)*5.0)),35.0);
  gl_FragColor.rgb+=vec3(.035,.001,.00002)*split*smoothstep(.65,.90,astraNoise2(vBeam.xy*5.0+11.0));
 `});
 const tile=new THREE.MeshStandardMaterial({color:0x55443a,roughness:.9});
 const stone=new THREE.MeshStandardMaterial({color:0x575652,roughness:.96});
 function box(name,size,pos,mat=plaster){const m=new THREE.Mesh(new THREE.BoxGeometry(...size),mat);m.name=name;m.position.set(...pos);m.castShadow=m.receiveShadow=true;opaque.add(m);return m;}
 // Wall strips make real empty door/window apertures; depth capture can see through them.
 box('RubbleFoundation',[5.7,.35,4.4],[0,.16,0],stone);box('InteriorFloor',[5.2,.09,4],[0,.36,0],char);
 box('RearWall',[5.5,2.9,.22],[0,1.78,-2.0]);box('WestWall',[.22,2.9,4],[ -2.65,1.78,0]);
 box('EastWallLower',[.22,.8,4],[2.65,.72,0]);box('EastWallUpper',[.22,.8,4],[2.65,2.72,0]);
 for(const z of [-1.6,.0,1.6])box('EastWindowPier',[.22,1.2,.55],[2.65,1.72,z]);
 box('LeftWindowSill',[2.25,.75,.22],[-1.65,.71,2.0]);box('RightWindowSill',[1.55,.75,.22],[1.98,.71,2.0]);box('FacadeLintel',[5.5,.63,.22],[0,2.91,2.0]);
 for(const [x,w] of [[-2.47,.56],[-.7,.50],[1.45,.53],[2.51,.48]])box('FacadePier',[w,1.50,.22],[x,1.83,2.0]);
 // Charred framing and actual roof gaps expose the upper fire without a transparent wall.
 for(const x of [-2.75,-.88,.88,2.75])box('TimberUpright',[.15,2.9,.27],[x,1.78,2.06],char);
 for(const y of [2.59,3.18])box('FacadeCrossbeam',[5.65,.15,.28],[0,y,2.08],char);
 box('LeftWindowSillTimber',[2.30,.15,.28],[-1.65,1.11,2.08],char);box('RightWindowSillTimber',[1.58,.15,.28],[1.98,1.11,2.08],char);
 for(const x of [-2.75,-1.83,-.91,0,.91,1.83,2.75])for(const side of [-1,1]){
  const rafter=box('ExposedRoofRafter',[.105,.12,2.53],[x,3.80,side*1.06],char);rafter.rotation.x=side*.51;
 }
 box('CharredRidgeBeam',[5.8,.18,.18],[0,4.42,0],char);
 for(let ix=0;ix<19;ix++)for(let row=0;row<7;row++)for(const side of [-1,1]){
  const x=-2.8+ix*.31,z=side*(.18+row*.31),y=4.43-Math.abs(z)*.555;
  const damage=Math.exp(-((x-.55)**2+(z-.6)**2)*.9)+Math.exp(-((x+1.7)**2+(z+.5)**2)*1.2)*.7;
  if(random()<damage*.94)continue;
  const t=box('WeatheredRoofTile',[.304,.035,.36],[x,y,z],tile);t.rotation.x=side*.51;t.rotation.z=(random()-.5)*.035;
 }
 box('BrickChimney',[.56,1.62,.62],[-1.95,4.32,-.57],stone);
 for(let row=0;row<9;row++){const band=box('ChimneyMortar',[.57,.016,.63],[-1.95,3.56+row*.18,-.57],plaster);band.material=plaster;}
 // A few fallen beams and fragments connect the damaged roof to the yard.
 for(let i=0;i<18;i++){const debris=box('FallenTimber',[.10+random()*.05,.08,.35+random()*.7],[(random()-.5)*5.7,.08,2.4+random()*1.7],char);debris.rotation.y=random()*Math.PI;debris.rotation.z=(random()-.5)*.15;}
 const grass=makeMeadow({size:[30,26],density:170,height:.22,dry:.24,maxBlades:90000,seed:32,
  mask:(x,z)=>Math.min(1,Math.max(0,(Math.max(Math.abs(x)-3.8,Math.abs(z)-3.5))*.9)),wind:{dir:[1,.3],strength:.68,speed:1.1/1.2}});scene.add(grass);actors.push(grass);
 for(let i=0;i<5;i++){const tree=makeTree({height:8+i*.5,crownRadius:2.5,leafDensity:.40,maxLeaves:5500,leafSegments:4,seed:100+i,shadows:false});tree.position.set((i-2)*5.5,0,-9-random()*3);scene.add(tree);actors.push(tree);}
 const fire=makeFireField({name:'CottageSharedFire',seed:107,quality:'high',wind:[.32,.10],intensity:.88,
  emitters:[
   {position:[-1.62,.65,1.55],radius:.68,height:2.5},{position:[.33,.58,1.53],radius:.65,height:3.1},
   {position:[1.86,.50,.93],radius:.64,height:2.7},{position:[2.44,1.25,.75],radius:.42,height:2.2},
   {position:[-.85,1.5,.05],radius:.75,height:3.7},{position:[1.1,2.4,-.15],radius:.78,height:3.8},
   {position:[-1.60,2.92,-.50],radius:.54,height:2.7},{position:[.18,3.4,.74],radius:.60,height:3.1},
  ],smoke:{height:10,density:2.0,albedo:0x4f4a43},embers:{count:160,size:[.002,.010],lifetime:[2,5],riseSpeed:2.2},lighting:{count:4,intensity:38},occluders:[opaque],depthResolution:1024});scene.add(fire);actors.push(fire);
 for(const light of fire.children.filter(o=>o.isPointLight).slice(0,2)){light.castShadow=true;light.shadow.mapSize.set(512,512);light.shadow.camera.near=.1;light.shadow.camera.far=18;light.shadow.normalBias=.025;}
 const resources=snapshotResources(opaque);resources.add(sky);
 for(const actor of actors)resources.add({dispose:()=>actor.userData.dispose()});attachDisposal(scene,resources);
 return {scene,cameras:[
  {name:'cottage_fire',position:[10.5,6.3,14.7],lookAt:[0,3.8,0],fov:43},
  {name:'window_fire',position:[.9,2.45,7.2],lookAt:[.25,1.75,1.1],fov:47},
  {name:'roof_fire',position:[6.8,7.8,8.3],lookAt:[0,4.0,.0],fov:44},
 ],update(t,dt){actors.forEach(a=>a.userData.update(t,dt));},dispose(){scene.userData.dispose();}};
}
