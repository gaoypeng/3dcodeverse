/** Two scales of rising density: warm tea and a cooling charcoal brazier. */
import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { makeSmoke, makeSteam } from '../lib/smoke.js';
import { patchStandard } from '../lib/shader.js';
import { mulberry32 } from '../lib/noise.js';

export function createScene({renderer}) {
  const scene = new THREE.Scene();scene.background = new THREE.Color(0x15202b);
  const room = new RoomEnvironment(), generator = new THREE.PMREMGenerator(renderer);
  const environment = generator.fromScene(room,.03);generator.dispose();room.dispose();
  scene.environment = environment.texture;scene.environmentIntensity = .36;
  scene.add(new THREE.HemisphereLight(0xb5d4e8,0x554936,.6));
  const sun = new THREE.DirectionalLight(0xffecd2,5.6);
  sun.position.set(-1.4,2.7,-2.0);sun.castShadow = true;
  sun.shadow.mapSize.set(2048,2048);sun.shadow.camera.left = -6;sun.shadow.camera.right = 6;
  sun.shadow.camera.top = 6;sun.shadow.camera.bottom = -6;sun.shadow.normalBias = .002;
  scene.add(sun);
  const stone = new THREE.MeshStandardMaterial({color:0x262b2d,roughness:.87});
  patchStandard(stone,{name:'PlumeSlate',vertexHead:'varying vec3 vSlate;',vertexBody:'vSlate=(modelMatrix*vec4(position,1.0)).xyz;',
    fragmentHead:'varying vec3 vSlate;',fragmentBody:`
      float mottling=astraFbm2(vSlate.xz*46.0+vSlate.y*4.0,4);
      diffuseColor.rgb*=.91+mottling*.22;`,roughnessBody:'roughnessFactor=clamp(roughnessFactor+astraNoise2(vSlate.xz*110.0)*.06,0.0,1.0);'});
  const table = new THREE.Mesh(new THREE.BoxGeometry(2.4,.06,2),stone);
  table.name = 'SlateCounter';table.position.y = .72;table.receiveShadow = true;scene.add(table);
  const ceramic = new THREE.MeshPhysicalMaterial({color:0x879994,roughness:.32,clearcoat:.38,clearcoatRoughness:.23});
  patchStandard(ceramic,{name:'PlumeGlaze',vertexHead:'varying vec3 vGlaze;',vertexBody:'vGlaze=position;',
    fragmentHead:'varying vec3 vGlaze;',fragmentBody:`
      float grain=astraNoise2(vGlaze.xy*240.0+vGlaze.z*91.0);
      diffuseColor.rgb*=.96+grain*.065;`});
  const cupProfile = [[.033,0],[.039,.005],[.042,.012],[.045,.033],[.049,.071],[.051,.095],
    [.050,.10],[.047,.10],[.046,.094],[.044,.074],[.039,.03],[.035,.009],[0,.009]];
  const cup = new THREE.Mesh(new THREE.LatheGeometry(cupProfile.map(p=>new THREE.Vector2(...p)),96),ceramic);
  cup.name = 'CeladonTeaCup';cup.position.y = .754;cup.castShadow = true;cup.receiveShadow = true;scene.add(cup);
  const handle = new THREE.Mesh(new THREE.TorusGeometry(.032,.006,14,48),ceramic);
  handle.name = 'CupHandle';handle.position.set(.064,.806,0);handle.scale.x = .8;scene.add(handle);
  const tea = new THREE.Mesh(new THREE.CircleGeometry(.046,96),new THREE.MeshPhysicalMaterial({
    color:0x422518,roughness:.11,metalness:0,clearcoat:1,ior:1.333}));
  tea.name = 'TeaSurface';tea.rotation.x = -Math.PI/2;tea.position.y = .845;scene.add(tea);
  const saucer = new THREE.Mesh(new THREE.LatheGeometry([[0,0],[.058,0],[.074,.004],[.084,.011],
    [.084,.014],[.074,.009],[.047,.005],[0,.005]].map(p=>new THREE.Vector2(...p)),96),ceramic);
  saucer.name = 'TeaSaucer';saucer.position.y = .750;scene.add(saucer);
  const steam = makeSteam({height:.29,radius:.03,spread:.12,riseSpeed:.12,
    wind:[.009,-.002],quality:'high',density:5.5,dissipation:2.8,turbulence:.9,seed:18});
  steam.position.y = .849;scene.add(steam);
  const steamLight=new THREE.PointLight(0xffead0,1.8,1.6,2);
  steamLight.name='SteamBacklight';steamLight.position.set(-.12,.98,-.40);scene.add(steamLight);
  const teaBackdrop = new THREE.Mesh(new THREE.PlaneGeometry(2.2,2.3),
    new THREE.MeshStandardMaterial({color:0x182026,roughness:1}));
  teaBackdrop.name = 'TeaAlcove';teaBackdrop.position.set(-.12,1.6,-.90);scene.add(teaBackdrop);

  const copper = new THREE.MeshStandardMaterial({color:0xb66b43,metalness:.95,roughness:.3});
  patchStandard(copper,{name:'PlumeHammeredCopper',vertexHead:'varying vec3 vCopper;',vertexBody:'vCopper=position;',
    fragmentHead:'varying vec3 vCopper;',fragmentBody:'diffuseColor.rgb*=.8+.2*astraFbm2(vCopper.xy*18.0,3);',
    roughnessBody:'roughnessFactor+=astraNoise2(vCopper.xy*170.0)*.1;'});
  const kettle = new THREE.Group();kettle.name = 'CopperKettle';kettle.position.set(-.36,.754,.03);
  kettle.rotation.y=2.2;
  const kettleProfile = new THREE.SplineCurve([[0,0],[.073,0],[.106,.034],[.119,.080],
    [.112,.139],[.075,.174],[.061,.178]].map(p=>new THREE.Vector2(...p))).getPoints(64);
  const body = new THREE.Mesh(new THREE.LatheGeometry(kettleProfile,96),copper);body.castShadow=true;kettle.add(body);
  const lid = new THREE.Mesh(new THREE.SphereGeometry(.064,64,24,0,Math.PI*2,0,Math.PI/2),copper);
  lid.scale.y = .28;lid.position.y = .176;kettle.add(lid);
  const dark = new THREE.MeshStandardMaterial({color:0x27211e,roughness:.62});
  const knob = new THREE.Mesh(new THREE.SphereGeometry(.014,24,16),dark);knob.position.y=.208;kettle.add(knob);
  const arch = new THREE.Mesh(new THREE.TorusGeometry(.11,.009,12,64,Math.PI),dark);
  arch.position.y=.133;kettle.add(arch);
  const spoutPath = new THREE.CatmullRomCurve3([new THREE.Vector3(.085,.052,0),
    new THREE.Vector3(.143,.076,0),new THREE.Vector3(.17,.13,0),new THREE.Vector3(.18,.171,0)]);
  const spout = new THREE.Mesh(new THREE.TubeGeometry(spoutPath,40,.014,18,false),copper);kettle.add(spout);scene.add(kettle);
  const silver = new THREE.MeshStandardMaterial({color:0xb7b5aa,metalness:.95,roughness:.22});
  const spoon = new THREE.Mesh(new THREE.SphereGeometry(.018,32,20),silver);
  spoon.scale.set(.7,.10,1.3);spoon.position.set(.14,.757,.02);scene.add(spoon);
  const stem = new THREE.Mesh(new THREE.CapsuleGeometry(.0024,.096,6,12),silver);
  stem.rotation.x=Math.PI/2;stem.position.set(.14,.757,.086);scene.add(stem);

  const floor = new THREE.Mesh(new THREE.PlaneGeometry(30,30),stone);floor.rotation.x=-Math.PI/2;
  floor.name='WorkshopFloor';floor.receiveShadow=true;scene.add(floor);
  const wall = new THREE.Mesh(new THREE.PlaneGeometry(40,14),new THREE.MeshStandardMaterial({color:0x152026,roughness:1}));wall.name='WorkshopWall';wall.position.set(0,5,-2.7);scene.add(wall);
  const brazier = new THREE.Group();brazier.name='CoolingBrazier';brazier.position.set(8,0,0);scene.add(brazier);
  const iron = new THREE.MeshStandardMaterial({color:0x313739,metalness:.7,roughness:.7});
  const bowl = new THREE.Mesh(new THREE.LatheGeometry([[0,0],[.16,0],[.22,.04],[.29,.17],
    [.31,.24],[.305,.248],[.297,.24],[.277,.175],[.21,.052],[.16,.014],[0,.014]].map(p=>new THREE.Vector2(...p)),64),iron);
  bowl.position.y=.44;brazier.add(bowl);
  for(const angle of [0,2.094,4.189]) {
    const leg=new THREE.Mesh(new THREE.CylinderGeometry(.013,.017,.47,12),iron);
    leg.position.set(Math.cos(angle)*.18,.235,Math.sin(angle)*.18);leg.castShadow=true;brazier.add(leg);
  }
  const char = new THREE.MeshStandardMaterial({color:0x181615,roughness:1});
  const random=mulberry32(8);
  for(let i=0;i<28;i++) {
    const coal=new THREE.Mesh(new THREE.DodecahedronGeometry(.034+random()*.025),char);
    const angle=random()*Math.PI*2,radial=Math.sqrt(random())*.21;
    coal.position.set(Math.cos(angle)*radial,.54+random()*.03,Math.sin(angle)*radial);
    coal.rotation.set(random()*3,random()*3,random()*3);coal.castShadow=true;brazier.add(coal);
  }
  const smoke=makeSmoke({height:1.9,radius:.075,spread:.19,riseSpeed:.43,wind:[.075,.012],
    color:0xc8d0d4,density:4.2,quality:'high',seed:33});
  smoke.position.set(8,.691,0);scene.add(smoke);
  return {scene,cameras:[
    {name:'tea_steam',position:[.40,1.14,.72],lookAt:[-.055,.975,-.005],fov:38},
    {name:'charcoal_smoke',position:[10.7,1.95,4.2],lookAt:[8.16,1.38,0],fov:39},
  ],update(t){steam.userData.update(t);smoke.userData.update(t);}};
}
