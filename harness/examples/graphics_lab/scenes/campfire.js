/** Two fuel-bed scales: a campfire and open bonfire in a wooded clearing. */
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { makeFireField } from '../lib/firefield.js';
import { makeRock } from '../lib/rock.js';
import { makeTree } from '../lib/tree.js';
import { makeMeadow } from '../lib/meadow.js';
import { patchStandard } from '../lib/shader.js';
import { patchMicroBreakup } from '../lib/surface_wear.js';
import { attachDisposal, snapshotResources } from '../lib/lifecycle.js';
import { fbm2, mulberry32 } from '../lib/noise.js';

const smooth = THREE.MathUtils.smoothstep;

export function createScene() {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x202d35);
  scene.fog = new THREE.FogExp2(0x202d35, .044);
  scene.add(new THREE.HemisphereLight(0x9cb3c3, 0x241a11, .65));
  const moon = new THREE.DirectionalLight(0xaabdc9, 1.15);
  moon.position.set(-7, 12, -5); moon.castShadow = true;
  moon.shadow.mapSize.set(2048, 2048); moon.shadow.normalBias = .009;
  Object.assign(moon.shadow.camera, {left:-14, right:17, top:14, bottom:-14, far:38});
  scene.add(moon);
  const actors = [], random = mulberry32(341);
  const ownEffect = object => { actors.push(object); return object; };
  const groundHeight = (x, z) => fbm2(x * .22, z * .22, {seed:43, octaves:3}) * .23
    * smooth(Math.hypot(x, z), 1.2, 3.0) * smooth(Math.hypot(x-8, z+1), 2.8, 4.5);
  const groundMat = new THREE.MeshStandardMaterial({color:0x51483b, roughness:.98});
  patchStandard(groundMat, {name:'CampSoil',
    vertexHead:'varying vec3 vCampEarth;',
    vertexBody:'vCampEarth=(modelMatrix*vec4(transformed,1.0)).xyz;',
    fragmentHead:'varying vec3 vCampEarth;',
    fragmentBody:`
      float campSoil=astraFbm2(vCampEarth.xz*13.0,4);
      float campBed=min(length(vCampEarth.xz),length(vCampEarth.xz-vec2(8.0,-1.0))/2.6);
      float campAsh=(1.0-smoothstep(.40,.73,campBed))
        *smoothstep(.24,.73,astraFbm2(vCampEarth.xz*31.0,3));
      diffuseColor.rgb*=.63+campSoil*.60;
      diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.14,.13,.115),campAsh*.68);
    `});
  patchMicroBreakup(groundMat, {scale:.055, strength:.14, seed:41});
  const groundGeometry = new THREE.PlaneGeometry(150, 150, 220, 220);
  groundGeometry.rotateX(-Math.PI/2);
  const gp = groundGeometry.attributes.position;
  for(let i=0; i<gp.count; i++) gp.setY(i,groundHeight(gp.getX(i),gp.getZ(i)));
  groundGeometry.computeVertexNormals();
  const ground = new THREE.Mesh(groundGeometry, groundMat);
  ground.name='ForestClearing'; ground.receiveShadow=true; scene.add(ground);
  const grass=ownEffect(makeMeadow({size:[34,28], ground:false, density:360,
    height:.12, bladeWidth:.006, maxBlades:135000, dry:.43, seed:31,
    heightAt:groundHeight, shadows:false,
    mask:(x,z)=>smooth(Math.hypot(x,z),1.04,2.0)*smooth(Math.hypot(x-8,z+1),2.65,3.7),
    wind:{strength:.12,speed:.8}}));scene.add(grass);
  const grove=[[-9,-6],[-9.6,-10],[-.7,-10],[3.7,-11],[9.4,-10],
    [13,-6],[-8.5,.5],[15,1.5],[-2.8,-17],[6.8,-19]];
  for(let i=0;i<grove.length;i++) {
    const tree=ownEffect(makeTree({species:i%4===0?'birch':'oak', height:7+random()*2.8,
      crownRadius:2.6+random()*.9, leafDensity:.8, maxLeaves:11000, leafSegments:4,
      seed:140+i, wind:{strength:.12,speed:.7}, shadows:false}));
    const [x,z]=grove[i];tree.position.set(x,groundHeight(x,z),z);scene.add(tree);
  }

  // Preserve local log coordinates when batching. Fine cracks emit between
  // dark charcoal blocks; powdery ash remains a rough reflective surface.
  const char = new THREE.MeshStandardMaterial({color:0x2c2722, roughness:.96});
  patchStandard(char,{name:'CampChar',
    vertexHead:'attribute vec3 fuelLocal; attribute float fuelEnd; varying vec3 vFuel; varying float vFuelEnd;',
    vertexBody:'vFuel=fuelLocal;vFuelEnd=fuelEnd;', fragmentHead:`
      varying vec3 vFuel; varying float vFuelEnd;
      vec3 campCharField(vec3 p) {
        float theta=atan(p.z+1e-7,p.x+1e-7);
        vec2 q=mix(vec2(theta*3.0,p.y*19.0),p.xz*43.0,step(.5,vFuelEnd));
        q+=vec2(astraNoise2(q*.44),astraNoise2(q*.39+19.0))*.85;
        vec2 cell=abs(fract(q)-.5);
        float crack=smoothstep(.445,.495,max(cell.x,cell.y));
        float ash=smoothstep(.40,.73,astraFbm2(q*.8+vec2(2.0,11.0),3));
        float heat=smoothstep(.54,.75,astraFbm2(q*.71+2.7,3));
        return vec3(crack,ash,heat);
      }
    `, fragmentBody:`
      vec3 charField=campCharField(vFuel);
      diffuseColor.rgb*=.52+.42*astraNoise2(vec2(atan(vFuel.z+1e-7,vFuel.x+1e-7)*42.0,vFuel.y*4.0));
      diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.19,.175,.16),charField.y*.67);
      diffuseColor.rgb*=1.0-charField.x*.70;
    `, normalBody:`
      float charRelief=(1.0-campCharField(vFuel).x)*.00075;
      vec3 charDx=dFdx(-vViewPosition),charDy=dFdy(-vViewPosition);
      vec3 charR1=cross(charDy,normal),charR2=cross(normal,charDx);
      float charDet=dot(charDx,charR1);
      normal=normalize(normal-sign(charDet)*(dFdx(charRelief)*charR1+dFdy(charRelief)*charR2)
        /max(abs(charDet),1e-9));
    `, outputBody:`
      vec3 charEmission=campCharField(vFuel);
      gl_FragColor.rgb+=vec3(.32,.017,.0005)*charEmission.x*charEmission.z*(1.0-charEmission.y*.7);
    `});
  const coalMaterial=new THREE.MeshStandardMaterial({color:0x201e1b, roughness:.98});
  patchStandard(coalMaterial,{name:'CampCoals',
    vertexHead:'attribute float coalHeat; varying float vCoalHeat; varying vec3 vCoalLocal;',
    vertexBody:'vCoalHeat=coalHeat;vCoalLocal=position;',
    fragmentHead:'varying float vCoalHeat; varying vec3 vCoalLocal;',
    fragmentBody:`
      float coalAsh=smoothstep(.34,.67,astraNoise2(vCoalLocal.xz*5.0+vCoalHeat*21.0));
      diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.12,.115,.10),coalAsh*.7);
    `, outputBody:`
      float coalLine=1.0-smoothstep(.025,.11,abs(astraFbm2(vCoalLocal.xy*7.0,3)-.49));
      gl_FragColor.rgb+=vec3(.95,.032,.0008)*coalLine*vCoalHeat*vCoalHeat;
    `});

  function fuelBed(x,z,scale,seed) {
    const group=new THREE.Group();group.name='CharredFuelBed';group.position.set(x,0,z);scene.add(group);
    const rand=mulberry32(seed), parts=[], transform=new THREE.Matrix4();
    const position=new THREE.Vector3(), rotation=new THREE.Quaternion(), euler=new THREE.Euler();
    for(let i=0;i<12;i++) {
      const angle=i/12*Math.PI*2+.09*rand();
      const rock=ownEffect(makeRock({type:'granite', seed:seed+i,
        size:[(.25+rand()*.10)*scale,(.18+rand()*.06)*scale,(.22+rand()*.10)*scale],
        detail:4, weathering:1.7, color:0x625f57, moisture:.2, burial:.045}));
      rock.position.set(Math.cos(angle)*.72*scale,0,Math.sin(angle)*.72*scale);
      rock.rotation.y=rand()*Math.PI*2;group.add(rock);
    }
    for(let i=0;i<8;i++) {
      const geometry=new THREE.CylinderGeometry((.045+rand()*.021)*scale,
        (.065+rand()*.021)*scale,(.70+rand()*.24)*scale,20,16);
      const p=geometry.attributes.position;
      for(let j=0;j<p.count;j++) {
        const irregular=1+.06*Math.sin(p.getY(j)*29/scale+i)+.035*Math.sin(p.getY(j)*61/scale+p.getX(j)*17/scale);
        const endWear=Math.sign(p.getY(j))*.010*scale
          *Math.sin(p.getX(j)*91/scale+i)*Math.cos(p.getZ(j)*76/scale-i);
        p.setXYZ(j,p.getX(j)*irregular,p.getY(j)+endWear,p.getZ(j)*irregular);
      }
      geometry.computeVertexNormals();
      const normals=geometry.attributes.normal;
      geometry.setAttribute('fuelEnd',new THREE.Float32BufferAttribute(
        Array.from({length:p.count},(_,j)=>Math.abs(normals.getY(j))),1));
      const local=p.clone();for(let j=0;j<local.count;j++)local.setXYZ(j,local.getX(j)/scale,local.getY(j)/scale,local.getZ(j)/scale);
      geometry.setAttribute('fuelLocal',local);
      euler.set(Math.PI*.5+(rand()-.5)*.31,0,i*1.93);
      rotation.setFromEuler(euler);
      position.set((rand()-.5)*.25*scale,(.10+i*.018)*scale,(rand()-.5)*.25*scale);
      geometry.applyMatrix4(transform.compose(position,rotation,new THREE.Vector3(1,1,1)));parts.push(geometry);
    }
    const logs=new THREE.Mesh(mergeGeometries(parts),char);parts.forEach(p=>p.dispose());
    logs.name='PartlyConsumedLogs';logs.castShadow=logs.receiveShadow=true;group.add(logs);
    const count=210,coalGeometry=new THREE.DodecahedronGeometry(1,1),heat=new Float32Array(count);
    const coals=new THREE.InstancedMesh(coalGeometry,coalMaterial,count);coals.name='AshAndCoal';
    for(let i=0;i<count;i++) {
      const angle=rand()*Math.PI*2,r=Math.sqrt(rand())*.46*scale,s=(.007+rand()*.021)*scale;
      position.set(Math.cos(angle)*r,.35*s+.003*scale,Math.sin(angle)*r);
      rotation.setFromEuler(euler.set(rand(),rand(),rand()));
      transform.compose(position,rotation,new THREE.Vector3(s,s*.34,s*.8));coals.setMatrixAt(i,transform);
      heat[i]=.1+Math.pow(rand(),1.8)*.9;
    }
    coalGeometry.setAttribute('coalHeat',new THREE.InstancedBufferAttribute(heat,1));
    coals.receiveShadow=true;coals.computeBoundingSphere();group.add(coals);
    return group;
  }
  const fuel=fuelBed(0,0,1,7),bigFuel=fuelBed(8,-1,2.6,29);
  const fire=ownEffect(makeFireField({name:'CampsiteFire',seed:51,quality:'high',wind:[.06,.035],intensity:.8,
    emitters:[{position:[-.23,.10,.08],radius:.18,height:.73},{position:[.20,.14,-.02],radius:.20,height:1.05},
      {position:[-.04,.16,-.22],radius:.18,height:.90},{position:[.09,.10,.23],radius:.17,height:.65}],
    smoke:{height:3.6,density:.6,albedo:0x685f54},embers:{count:42,size:[.001,.0045],lifetime:[1.5,3]},
    lighting:{count:2,intensity:2.6},occluders:[fuel,ground],depthResolution:768}));scene.add(fire);
  const bonfire=ownEffect(makeFireField({name:'OpenBonfire',seed:79,quality:'high',wind:[.23,.08],intensity:.8,
    emitters:[{position:[-.54,.22,.1],radius:.52,height:2.3},{position:[.42,.24,.12],radius:.48,height:2.8},
      {position:[.03,.30,-.53],radius:.44,height:2.5},{position:[.14,.18,.53],radius:.47,height:1.9}],
    smoke:{height:8,density:.48,albedo:0x504a43},embers:{count:120,size:[.002,.009],lifetime:[2,5]},
    lighting:{count:3,intensity:14},occluders:[bigFuel,ground],depthResolution:768}));
  bonfire.position.set(8,0,-1);scene.add(bonfire);

  const resources=snapshotResources(scene);
  for(const actor of actors) {
    for(const resource of snapshotResources(actor))resources.delete(resource);
    resources.add({dispose:()=>actor.userData.dispose()});
  }
  resources.add({dispose:()=>moon.shadow.dispose()});attachDisposal(scene,resources);
  return {scene,cameras:[
    {name:'campfire',position:[1.9,1.25,2.7],lookAt:[0,.69,0],fov:42},
    {name:'embers_and_char',position:[.85,.58,1.22],lookAt:[0,.34,0],fov:44},
    {name:'open_bonfire',position:[12.9,3.4,5.7],lookAt:[8,2,-1],fov:43},
  ],update(t,dt){actors.forEach(actor=>actor.userData.update(t,dt));},dispose(){scene.userData.dispose();}};
}
