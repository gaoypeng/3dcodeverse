/**
 * Close-view meadow: curved leaf ribbons with a shaded fold and botanical variation.
 * Metres, local XZ footprint, seeded placement; wind is in the field's LOCAL XZ.
 * Blades use one instanced draw; sparse seed culms and soil are optional draws.
 * Real instance matrices keep the blades present in the host's AO pass. Moving sun shadows
 * use the same deformation as the colour pass. AO sees the rest pose (no TAA).
 * Moving directional, spot and point-light shadows share that deformation.
 *
 * makeMeadow({size:[10,10], density:700, height:.38, seed:12, heightAt,
 *   mask, wind:{dir:[1,.3],strength:1,speed:1}, dry:.12,
 *   shadows:true, ground:true, diversity:.85, seedHeads:.003}) -> Group; userData.update(t), dispose().
 * wind is grass.js's `windOf` over shader.js `readWind`: a number (strength), [x,z] or
 * {dir|direction, strength, speed}, so one wind moves the meadow, grass and trees alike (strength 1 = breeze).
 * density = blades/m² before the mask; maxBlades bounds memory and triangles.
 * mask(x,z) -> 0..1 and heightAt(x,z) are LOCAL coordinates. A mask of 0
 * leaves soil, a mask of 1 grows all blades. diversity (0..1) blends between
 * one simple growth habit and mixed fine/arched/basal/senescent foliage.
 * seedHeads is culms per blade (default .003, 0 disables; a large field thins them). No textures or network needed.
 */
import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { windOf } from './grass.js';
import { fbm2, mulberry32 } from './noise.js';
import { boundedSampler, option, patchStandard, shadowLike, tickShaders } from './shader.js';

// The rest mesh has an actual silhouette: GTAO's override material draws the
// real instance matrices instead of the origin-degenerate helper lattices.
function ribbon(rows) {
  const pos=[],uv=[],idx=[];
  // Spend the same triangle budget on longitudinal curvature. The midrib fold
  // is shaded by the two edge normals; a third column was invisible at grass
  // scale but left only four segments along a conspicuously angular leaf.
  const segments=rows*2;
  for(let j=0;j<=segments;j++) {
    const t=j/segments,w=Math.pow(1-t,.38);
    for(let k=0;k<2;k++) {
      pos.push((k-.5)*w,Math.sin(t*.42)/.42,(1-Math.cos(t*.42))/.42);
      uv.push(k,t);
    }
  }
  for(let j=0;j<segments;j++) {
    const a=j*2,b=a+1,c=a+2,d=c+1;idx.push(a,b,c,b,d,c);
  }
  const g=new THREE.BufferGeometry();
  g.setAttribute('position',new THREE.Float32BufferAttribute(pos,3));
  g.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));
  g.setIndex(idx);g.computeVertexNormals();return g;
}

const HEAD = /* glsl */`
  attribute vec4 meadowVar;
  attribute vec4 meadowShape;
  attribute vec4 meadowBotany;
  uniform vec2 uMeadowWind;
  uniform float uMeadowStrength, uMeadowSpeed, uMeadowCulm;
  varying vec4 vMeadow;
  varying vec4 vMeadowBotany;
  varying vec2 vMeadowUv;
`;

const VERTEX = /* glsl */`
  float mdT = uv.y;
  vec3 mdScale = vec3(length(instanceMatrix[0].xyz),
                      length(instanceMatrix[1].xyz), length(instanceMatrix[2].xyz));
  mat3 mdRotation = mat3(instanceMatrix[0].xyz / mdScale.x,
                        instanceMatrix[1].xyz / mdScale.y,
                        instanceMatrix[2].xyz / mdScale.z);
  vec2 mdRoot = instanceMatrix[3].xz;
  float mdPhase = meadowVar.x * 6.2831853;
  float mdTime = uTime * uMeadowSpeed;
  float mdGust = .34 + .30 * sin(dot(mdRoot, uMeadowWind) * 1.15 - mdTime * 1.8)
                + .18 * sin(dot(mdRoot, vec2(-uMeadowWind.y,uMeadowWind.x)) * .7
                            - mdTime * .81 + 1.7);
  float mdFlutter = sin(mdTime * (4.5 + meadowVar.w * 2.) + mdPhase + mdRoot.x * 2.1) * .065;
  vec2 mdWindLocal = vec2(dot(uMeadowWind, mdRotation[0].xz),
                          dot(uMeadowWind, mdRotation[2].xz));
  float mdFlex = mix(.28, .95, smoothstep(.15,.9,meadowBotany.x));
  vec2 mdBend = vec2(.12 * sin(mdPhase), meadowShape.x)
             + mdWindLocal * uMeadowStrength * (mdGust + mdFlutter) * mdFlex;
  float mdAngle = clamp(length(mdBend), .015, 2.80);
  vec2 mdDirection = normalize(mdBend + vec2(.00001));
  // Integrate a unit-length tangent: stiff young leaves rise before their tips
  // roll; mature broad leaves arch from the crown; senescent leaves curl down.
  // Shape varies by botanical form, not by stretching the same circular arc.
  vec3 mdSpine = vec3(0.);
  float mdStep = mdT / 5.;
  for(int mdI=0; mdI<5; mdI++) {
    float mdS=(float(mdI)+.5)*mdStep;
    float mdA=meadowBotany.w + mdAngle*pow(mdS,meadowShape.y);
    mdSpine += vec3(mdDirection.x*sin(mdA),cos(mdA),mdDirection.y*sin(mdA))*mdStep;
  }
  float mdTipA=meadowBotany.w + mdAngle*pow(mdT,meadowShape.y);
  vec3 mdTangent = vec3(mdDirection.x*sin(mdTipA),cos(mdTipA),mdDirection.y*sin(mdTipA));
  vec3 mdSide0=normalize(vec3(1.,0.,0.)-mdTangent*mdTangent.x);
  vec3 mdNormal0=normalize(cross(mdSide0,mdTangent));
  float mdTwist=meadowShape.z*mdT*mdT + .12*sin(mdPhase+mdT*5.)*mdT;
  vec3 mdSide=mdSide0*cos(mdTwist)+mdNormal0*sin(mdTwist);
  vec3 mdN=normalize(cross(mdSide,mdTangent));
  float mdBroad=smoothstep(.25,.65,meadowBotany.x);
  float mdWidth=pow(max(0.,1.-mdT),mix(.38,.32,mdBroad))
    * mix(1.,.52+.48*smoothstep(0.,.13,mdT),mdBroad);
  float mdAcross=uv.x-.5;
  float mdRatio=mdScale.x/mdScale.y;
  vec3 mdP=mdSpine+mdSide*(mdAcross*mdWidth*mdRatio)
    +mdN*(abs(mdAcross)*mdWidth*mdRatio*meadowShape.w);
  transformed=vec3(mdP.x/mdRatio,mdP.y,mdP.z);
  if(uMeadowCulm>.5) {
    transformed=mdSpine+mdSide*position.x+mdN*position.z;
  }
  vMeadow=vec4(mdT,meadowVar.z,meadowVar.w,meadowVar.x);
  vMeadowBotany=meadowBotany;
  vMeadowUv=uv;
  #ifndef FLAT_SHADED
    if(uMeadowCulm>.5) mdN=normalize(mdSide*normal.x+mdTangent*normal.y+mdN*normal.z);
    else mdN=normalize(mdN-mdSide*sign(mdAcross)*meadowShape.w);
    transformedNormal=normalMatrix*mdRotation*mdN;
    vNormal=normalize(transformedNormal);
  #endif
`;

const FRAGMENT = /* glsl */`
  float mdSpecies=vMeadowBotany.x, mdAge=vMeadowBotany.y;
  float mdHabitat=vMeadowBotany.z;
  // Moist, shaded crowns skew blue-green; vigorous new growth is lime; old
  // blades keep their straw tips, russet sheath and wax loss along the leaf.
  vec3 mdGreen=uMeadowGreen*mix(vec3(.67,.89,1.30),vec3(1.35,1.10,.69),mdHabitat);
  mdGreen*=mix(vec3(.80,.94,1.12),vec3(1.13,1.04,.81),mdSpecies);
  vec3 mdLeaf=mix(mdGreen,uMeadowStraw,vMeadow.y);
  mdLeaf*=.77+.47*vMeadow.z;
  float mdDryTip=smoothstep(1.-mdAge*.57,1.,vMeadow.x)*smoothstep(.30,.9,mdAge);
  float mdScar=astraNoise2(vec2(vMeadowUv.x*5.,vMeadowUv.y*18.)+vMeadow.w*37.);
  mdLeaf=mix(mdLeaf,uMeadowStraw*vec3(.83,.65,.43),mdDryTip*.85);
  mdLeaf*=1.-smoothstep(.62,.82,mdScar)*mdAge*.24;
  // MSAA edge fragments can extrapolate slightly below the root. Fractional
  // powers need a bounded base, especially in untone-mapped transmission RTTs.
  float mdHeight=clamp(vMeadow.x,0.,1.);
  float mdDepth=mix(.32,1.,pow(mdHeight,.60));
  float mdVeins=.98+.02*sin(vMeadowUv.x*51.+vMeadow.w*3.);
  float mdMidrib=1.-smoothstep(.016,.075,abs(vMeadowUv.x-.5));
  diffuseColor.rgb=mdLeaf*mdDepth*mdVeins*(1.-.07*mdMidrib);
  float mdSheath=(1.-smoothstep(.01,.14,vMeadow.x))*smoothstep(.45,.95,mdAge);
  diffuseColor.rgb=mix(diffuseColor.rgb,uMeadowStraw*vec3(.30,.17,.10),mdSheath*.55);
  #if NUM_DIR_LIGHTS > 0
  #ifndef FLAT_SHADED
    vec3 mdEye=normalize(vViewPosition),mdNormal=normalize(vNormal);
    vec3 mdLight=directionalLights[0].direction;
    float mdBack=clamp(-dot(mdNormal,mdLight)*sign(dot(mdNormal,mdEye)),0.,1.);
    float mdForward=pow(max(0.,dot(mdEye,-mdLight)),3.);
    vec3 mdSap=mdLeaf*vec3(1.35,1.08,.60);
    float mdVisibility=1.;
    #if defined(USE_SHADOWMAP) && NUM_DIR_LIGHT_SHADOWS > 0
      mdVisibility=getShadow(directionalShadowMap[0],directionalLightShadows[0].shadowMapSize,
        directionalLightShadows[0].shadowIntensity,directionalLightShadows[0].shadowBias,
        directionalLightShadows[0].shadowRadius,vDirectionalShadowCoord[0]);
    #endif
    totalEmissiveRadiance+=mdSap*directionalLights[0].color
      *(.20*mdBack+.22*mdForward)*pow(mdHeight,1.4)*(1.-mdAge*.35)*mdVisibility;
  #endif
  #endif
`;

function seedHeadGeometry() {
  const positions=[],normals=[];
  const stem=new THREE.CylinderGeometry(.0012,.0017,1,4,6,false);
  stem.translate(0,.5,0);
  const flat=stem.toNonIndexed();positions.push(...flat.attributes.position.array);
  normals.push(...flat.attributes.normal.array);
  stem.dispose();flat.dispose();
  // Alternating closed spikelets, not a camera-facing flower photograph.
  for(let i=0;i<8;i++) {
    const sphere=new THREE.SphereGeometry(1,6,4),grain=sphere.toNonIndexed();
    sphere.dispose();
    const p=grain.attributes.position,n=grain.attributes.normal,a=i*2.399963,r=.0038;
    const normal=new THREE.Vector3();
    for(let j=0;j<p.count;j++) {
      positions.push(Math.cos(a)*r+p.getX(j)*.0042,
        .838+i*.021+p.getY(j)*.012,Math.sin(a)*r+p.getZ(j)*.0042);
      normal.set(n.getX(j)/.0042,n.getY(j)/.012,n.getZ(j)/.0042).normalize();
      normals.push(normal.x,normal.y,normal.z);
    }
    grain.dispose();
  }
  const g=new THREE.BufferGeometry();
  g.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  const uv=[];for(let i=0;i<positions.length;i+=3) uv.push(.5,positions[i+1]);
  g.setAttribute('uv',new THREE.Float32BufferAttribute(uv,2));
  g.setAttribute('normal',new THREE.Float32BufferAttribute(normals,3));return g;
}

function makeSeedHeads({count,rand,roots,botany,height,green,straw,dir,strength,speed,shadows}) {
  const geometry=seedHeadGeometry(),vars=[],forms=[],traits=[],matrices=[];
  const dummy=new THREE.Object3D();
  for(let i=0;i<count;i++) {
    const at=Math.floor(rand()*roots.length/3),phase=rand();
    const h=height*(1.40+.55*rand());
    dummy.position.set(roots[at*3],roots[at*3+1]-.003,roots[at*3+2]);
    dummy.rotation.set(0,rand()*Math.PI*2,0);dummy.scale.setScalar(h);dummy.updateMatrix();
    matrices.push(dummy.matrix.clone());
    vars.push(phase,rand(),.3,rand());forms.push(.18+rand()*.44,1.45,0,.05);
    traits.push(.10,.62,botany[at*4+2],0);
  }
  geometry.setAttribute('meadowVar',new THREE.InstancedBufferAttribute(new Float32Array(vars),4));
  geometry.setAttribute('meadowShape',new THREE.InstancedBufferAttribute(new Float32Array(forms),4));
  geometry.setAttribute('meadowBotany',new THREE.InstancedBufferAttribute(new Float32Array(traits),4));
  const material=new THREE.MeshStandardMaterial({color:0xffffff,roughness:.69,name:'MeadowSeedHeads'});
  material.userData.bloom=false;
  patchStandard(material,{name:'meadow:seed',vertexHead:HEAD,vertexBody:VERTEX,
    fragmentHead:'varying vec4 vMeadow; uniform vec3 uMeadowGreen,uMeadowStraw;',
    fragmentBody:`diffuseColor.rgb=mix(uMeadowGreen*.72,uMeadowStraw*.87,
      smoothstep(.80,.86,vMeadow.x))*(.82+.32*vMeadow.z);`,
    uniforms:{uMeadowGreen:{value:green},uMeadowStraw:{value:straw},uMeadowWind:{value:dir},
      uMeadowStrength:{value:strength},uMeadowSpeed:{value:speed},uMeadowCulm:{value:1}},
  });
  const mesh=new THREE.InstancedMesh(geometry,material,count);mesh.name='MeadowSeedHeads';
  matrices.forEach((m,i)=>mesh.setMatrixAt(i,m));mesh.instanceMatrix.needsUpdate=true;
  mesh.receiveShadow=true;mesh.frustumCulled=false;
  if(shadows) shadowLike(mesh,'meadow:seed-depth',HEAD,VERTEX);
  return mesh;
}

/** @returns {THREE.Group} Rooted meadow, animated by userData.update(seconds). */
export function makeMeadow(opts = {}) {
  const dimensions = Array.isArray(opts.size) ? opts.size : [opts.size ?? 10, opts.size ?? 10];
  const sx = option(dimensions[0], 10, 'makeMeadow: size[0]', .05, 1000);
  const sz = option(dimensions[1], 10, 'makeMeadow: size[1]', .05, 1000);
  const density = option(opts.density, 700, 'makeMeadow: density', 0, 20000);
  const height = option(opts.height, .38, 'makeMeadow: height', .005, 4);
  const width = option(opts.bladeWidth, .011, 'makeMeadow: bladeWidth', .0005, .15);
  const seed = option(opts.seed, 12, 'makeMeadow: seed');
  const dry = option(opts.dry, .12, 'makeMeadow: dry', 0, 1);
  const diversity = option(opts.diversity, .85, 'makeMeadow: diversity', 0, 1);
  const seedHeads = option(opts.seedHeads, .003, 'makeMeadow: seedHeads', 0, .05);
  const maxBlades = Math.floor(option(opts.maxBlades, 100000, 'makeMeadow: maxBlades', 1, 300000));
  const rows = Math.floor(option(opts.segments, 5, 'makeMeadow: segments', 3, 12));
  const wind = windOf(opts.wind);
  const strength = option(wind.amp, .5, 'makeMeadow: wind.strength', 0, 3);
  // The meadow's own gust clock runs 1.2x the shared wind's.
  const speed = option(wind.speed * 1.2, 1.2, 'makeMeadow: wind.speed', 0, 10);
  const dir = wind.dir;
  if (!Number.isFinite(dir.x) || !Number.isFinite(dir.y)) {
    throw new TypeError('makeMeadow: wind direction must be [x,z] finite numbers');
  }
  const heightAt = opts.heightAt ?? (() => 0), mask = opts.mask ?? (() => 1);
  if (typeof heightAt !== 'function' || typeof mask !== 'function') {
    throw new TypeError('makeMeadow: heightAt and mask must be functions');
  }
  const sampleHeight = (x, z) => {
    const y = heightAt(x, z);
    if (!Number.isFinite(y)) throw new RangeError('makeMeadow: heightAt returned a non-finite value');
    return y;
  };
  const rand = mulberry32(seed);
  const roots = [], vars = [], shapes = [], botany = [], matrices = [];
  const target = Math.min(maxBlades, Math.round(sx * sz * density));
  const dummy = new THREE.Object3D();
  // Every handful of blades shares a crown, but each leaf has its own angle.
  // Centres cover the ENTIRE footprint even when maxBlades limits the density.
  const tuftSize = 9;
  for (let i = 0; i < target;) {
    const cx=(rand()-.5)*sx,cz=(rand()-.5)*sz;
    const wet=fbm2(cx*.43,cz*.43,{seed,octaves:3});
    const habitat=THREE.MathUtils.clamp(.5+wet*2.4,0,1);
    const formPatch=fbm2(cx*.81+47,cz*.81-19,{seed:seed+113,octaves:2});
    const patchDry=THREE.MathUtils.clamp(dry+wet*.55,0,1);
    const tuftH=height*(.66+.57*rand())*(1+wet*.9);
    const crownYaw=rand()*Math.PI*2;
    for(let j=0;j<tuftSize && i<target;j++,i++) {
      const phi=crownYaw+j*2.399963+(rand()-.5)*.65;
      const r=Math.sqrt(rand())*height*.075;
      const x=THREE.MathUtils.clamp(cx+Math.cos(phi)*r,-sx/2,sx/2);
      const z=THREE.MathUtils.clamp(cz+Math.sin(phi)*r,-sz/2,sz/2);
      const coverage=mask(x,z);
      if(!Number.isFinite(coverage)) throw new RangeError('makeMeadow: mask returned a non-finite value');
      if(rand()>=THREE.MathUtils.clamp(coverage,0,1)) continue;
      // An individual crown keeps a family resemblance, with old outer leaves
      // and a few fine upright shoots. Habitat changes the mix between crowns.
      const choose=rand()+formPatch*.55;
      let form=choose<.10?0:choose<.72?1:2;
      const old=rand()<(.10+dry*.25)*diversity;
      if(old) form=3;
      const age=old?.72+rand()*.28:Math.min(.86,rand()*.67+dry*.12);
      const phase=rand(),bendRandom=rand(),tone=rand();
      const preset=[
        [.91,.48,.28,.68,1.55,.18,.10],
        [1.19,1.08,1.15,1.30,1.02,.59,.19],
        [.71,1.50,1.80,.85,.78,.95,.25],
        [1.04,.77,2.36,.31,.85,1.35,.10],
      ][form];
      const h=tuftH*THREE.MathUtils.lerp(.90,preset[0],diversity)*(.79+.42*rand());
      const w=width*THREE.MathUtils.lerp(1,preset[1],diversity)*(.73+.52*rand());
      const curvature=THREE.MathUtils.lerp(.8,preset[2]+bendRandom*preset[3],diversity);
      // Keep even a fully wind-loaded old leaf above its crown plane.
      const curl=Math.max(1,THREE.MathUtils.lerp(1,preset[4]+rand()*.25,diversity));
      const twist=(rand()-.5)*2*preset[5]*diversity;
      const fold=preset[6]*(.7+rand()*.6);
      const y=sampleHeight(x,z);
      dummy.position.set(x,y-.003,z);dummy.rotation.set(0,phi,0);dummy.scale.set(w,h,h);
      dummy.updateMatrix();matrices.push(dummy.matrix.clone());roots.push(x,y,z);
      const leafDry=old?.58+rand()*.40:THREE.MathUtils.clamp(patchDry+(rand()-.5)*.10,0,1);
      vars.push(phase,bendRandom,leafDry,tone);
      shapes.push(curvature,curl,twist,fold);
      botany.push(form/3,age,habitat,form===3?.08:form===2?.12:0);
    }
  }
  const group = new THREE.Group(); group.name = opts.name ?? 'Meadow';
  const green = new THREE.Color(opts.color ?? 0x527829);
  const straw = new THREE.Color(opts.dryColor ?? 0xaaa166);
  const geometry = ribbon(rows);
  geometry.setAttribute('meadowVar', new THREE.InstancedBufferAttribute(new Float32Array(vars), 4));
  geometry.setAttribute('meadowShape', new THREE.InstancedBufferAttribute(new Float32Array(shapes), 4));
  geometry.setAttribute('meadowBotany', new THREE.InstancedBufferAttribute(new Float32Array(botany), 4));
  const material = new THREE.MeshPhysicalMaterial({
    color: 0xffffff, side: THREE.DoubleSide, roughness: .68, metalness: 0,
    specularIntensity: .45, envMapIntensity: .55, name: 'MeadowLeaf',
  });
  material.userData.bloom = false;
  patchStandard(material, {name: 'meadow:leaf', vertexHead: HEAD, vertexBody: VERTEX,
    fragmentHead: `varying vec4 vMeadow; varying vec4 vMeadowBotany; varying vec2 vMeadowUv;
      uniform vec3 uMeadowGreen, uMeadowStraw;`, fragmentBody: FRAGMENT,
    roughnessBody: 'roughnessFactor = clamp(.39 + vMeadowBotany.y*.35 + vMeadow.y*.24, .36, .95);',
    uniforms: {uMeadowGreen: {value: green}, uMeadowStraw: {value: straw},
      uMeadowWind: {value: dir}, uMeadowStrength: {value: strength}, uMeadowSpeed: {value: speed},
      uMeadowCulm: {value: 0}},
  });
  const blades = new THREE.InstancedMesh(geometry, material, matrices.length);
  matrices.forEach((m, i) => blades.setMatrixAt(i, m));
  blades.instanceMatrix.needsUpdate = true;
  blades.name = 'MeadowBlades'; blades.receiveShadow = true;
  // Actual wind bounds extend beyond rest geometry; avoid edge popping.
  blades.frustumCulled = false;
  blades.computeBoundingBox(); blades.computeBoundingSphere();
  if (opts.shadows !== false) shadowLike(blades, 'meadow:depth', HEAD, VERTEX);
  group.add(blades);
  const headCount=Math.min(2000,Math.round(matrices.length*seedHeads));
  if(headCount) {
    const heads=makeSeedHeads({count:headCount,rand,roots,botany,height,green,straw,
      dir,strength,speed,shadows:opts.shadows!==false});
    group.add(heads);
  }
  group.userData.seedHeadCount=headCount;

  if (opts.ground !== false) {
    const nx = Math.min(160, Math.max(8, Math.ceil(sx * 4)));
    const nz = Math.min(160, Math.max(8, Math.ceil(sz * 4)));
    const geo = new THREE.PlaneGeometry(sx, sz, nx, nz); geo.rotateX(-Math.PI / 2);
    const p = geo.attributes.position;
    for (let i = 0; i < p.count; i++) p.setY(i, sampleHeight(p.getX(i), p.getZ(i)) - .006);
    geo.computeVertexNormals();
    const soil = new THREE.MeshStandardMaterial({color: 0x34321e, roughness: .98,
      envMapIntensity: .6, name: 'MeadowSoil'});
    patchStandard(soil, {name: 'meadow:soil',
      vertexHead: 'varying vec3 vMeadowSoil;', vertexBody: 'vMeadowSoil = transformed;',
      fragmentHead: 'varying vec3 vMeadowSoil;',
      fragmentBody: `float mdSoil = astraFbm2(vMeadowSoil.xz * 14., 3);
        float mdLitter = astraFbm2(vMeadowSoil.xz * 2.1, 2);
        diffuseColor.rgb *= .66 + .62 * mdSoil;
        diffuseColor.rgb = mix(diffuseColor.rgb, vec3(.08,.063,.027), smoothstep(.48,.7,mdLitter)*.4);`,
    });
    const ground = new THREE.Mesh(geo, soil); ground.name = 'MeadowSoil';
    ground.receiveShadow = true; group.add(ground);
  }
  group.userData.bladeCount = matrices.length;
  group.userData.roots = new Float32Array(roots);
  group.userData.sampleHeight = boundedSampler(sx, sz, heightAt);
  group.userData.update = (t) => {
    if (!Number.isFinite(t)) throw new RangeError('makeMeadow.update: time must be finite');
    tickShaders(group, t);
  };
  group.userData.tick = group.userData.update;
  // Includes instance buffers and both custom shadow materials.
  attachDisposal(group, snapshotResources(group));
  return group;
}
