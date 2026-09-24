/**
 * A gravity-driven sheet spilling over a lip, with advected surface detail,
 * thinning optical depth, aeration, impact foam and the existing ballistic spray.
 * Origin is the impact water level; the lip is [0,height,0], flow heads toward +Z.
 * This is an authored weir/free-fall model, not a fluid or collision solver.
 * The caller supplies the cliff, upstream water and receiving pool.
 */
import * as THREE from 'three';
import { patchStandard, keepOutOfDepthPasses } from './shader.js';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { mulberry32 } from './noise.js';
import { makeSpray } from './watermist.js';

const GRAVITY = 9.81;
const TIERS = { low: [32,48], balanced: [56,96], high: [80,160] };
const clamp = (x,a=0,b=1) => Math.max(a,Math.min(b,x));

const HEAD = /* glsl */`
uniform float uFallHeight;
uniform float uFallWidth;
uniform float uFallFlight;
uniform float uFallSpeed;
uniform float uFallThickness;
uniform float uFallBreakup;
uniform float uFallFoam;
uniform float uFallSeed;
uniform vec4 uFallWaves[4];
varying vec2 vFallUv;
varying vec3 vFallView;
varying float vFallThickness;
float fallWidth(float u, float t) {
  return 1.0-.10*u+.025*uFallBreakup*u*sin((t-u*uFallFlight)*3.1+uFallSeed);
}
vec3 fallPosition(vec2 uv,float t) {
  float u=uv.y, travel=u*uFallFlight, emitted=t-travel;
  float lateral=(uv.x-.5)*uFallWidth;
  vec3 p=vec3(lateral*fallWidth(u,t),uFallHeight-4.905*travel*travel,uFallSpeed*travel);
  vec3 n=normalize(vec3(0.0,uFallSpeed,9.81*travel));
  float ripple=0.0;
  for(int i=0;i<4;i++) {
    vec4 wave=uFallWaves[i];
    ripple+=wave.z*sin(lateral*wave.x-emitted*wave.y+wave.w);
  }
  return p+n*ripple*(16.0*u*u*(1.0-u)*(1.0-u));
}
`;

/**
 * makeWaterfall({width:2,height:3,speed:1,thickness:.045,breakup:.6,
 *   foam:.65,spray:true,seed:17,quality:'balanced'}) -> Group.
 * Metres and seconds. speed is horizontal lip velocity; gravity accelerates
 * the sheet downward. thickness is lip depth; downstream thickness follows
 * width * velocity * thickness conservation. Set spray:false or foam:0 for
 * a clear quiet sheet. No reflection capture is added. Physical transmission
 * reads opaque scene colour only, with an approximate thin-film optical path.
 * sample(u,lateral,t) uses u=0 at the lip, u=1 at impact, lateral=-.5..+.5.
 * Update once with absolute seconds; dispose releases construction-owned data.
 */
export function makeWaterfall(opts = {}) {
  const width=opts.width??2, height=opts.height??3, speed=opts.speed??1;
  const thickness=opts.thickness??.045, breakup=opts.breakup??.6, foam=opts.foam??.65;
  const seed=opts.seed??17, quality=opts.quality??'balanced';
  const attenuationDistance=opts.attenuationDistance??8;
  const sprayRate=opts.sprayRate??Math.min(500,100*width);
  if (![width,height,speed,thickness,breakup,foam].every(Number.isFinite) ||
      width<.02 || width>500 || height<.02 || height>500 || speed<.02 || speed>50 ||
      thickness<=0 || thickness>Math.min(width,height)*.5 || breakup<0 || breakup>1 || foam<0 || foam>1 ||
      !Number.isSafeInteger(seed) || !Object.hasOwn(TIERS,quality) ||
      !(attenuationDistance>0) || (!Number.isFinite(attenuationDistance) && attenuationDistance!==Infinity) ||
      !Number.isFinite(sprayRate) || sprayRate<0 || sprayRate>100000) {
    throw new RangeError('makeWaterfall: invalid dimensions, flow, foam, seed or quality');
  }
  const flight=Math.sqrt(2*height/GRAVITY), impactZ=speed*flight;
  const random=mulberry32(seed), waves=[];
  for(let i=0;i<4;i++) waves.push(new THREE.Vector4(
    (17+i*11.1)/Math.max(width,.05),20.8+i*19.65,
    Math.min(width,height)*.004*breakup/(1+i*.8),random()*Math.PI*2));
  const phase=random()*6.28;
  const widthAt=(u,t) => width*(1-.10*u+.025*breakup*u*Math.sin((t-u*flight)*3.1+phase));
  const positionAt=(u,lateral,t) => {
    const travel=u*flight,emitted=t-travel;
    const n=new THREE.Vector3(0,speed,GRAVITY*travel).normalize();
    let ripple=0;
    for(const w of waves) ripple+=w.z*Math.sin(lateral*width*w.x-emitted*w.y+w.w);
    return new THREE.Vector3(lateral*widthAt(u,t),height-.5*GRAVITY*travel*travel,speed*travel)
      .addScaledVector(n,ripple*16*u*u*(1-u)*(1-u));
  };
  const material=new THREE.MeshPhysicalMaterial({color:0xffffff,roughness:.12,metalness:0,
    transmission:1,ior:1.333,thickness,attenuationDistance,
    attenuationColor:new THREE.Color(opts.waterColor??0xc9e5df),side:THREE.DoubleSide});
  const uniforms={
    uFallHeight:{value:height},uFallWidth:{value:width},uFallFlight:{value:flight},
    uFallSpeed:{value:speed},uFallThickness:{value:thickness},uFallBreakup:{value:breakup},
    uFallFoam:{value:foam},uFallSeed:{value:phase},uFallWaves:{value:waves},
  };
  patchStandard(material,{name:'WaterfallFilm',uniforms,vertexHead:HEAD,fragmentHead:HEAD,
    vertexBody:/* glsl */`
      vFallUv=uv;
      transformed=fallPosition(uv,uTime);
      vFallView=(modelViewMatrix*vec4(transformed,1.0)).xyz;
      float velocity=length(vec2(uFallSpeed,9.81*uv.y*uFallFlight));
      vFallThickness=uFallThickness*uFallSpeed/(velocity*fallWidth(uv.y,uTime));
    `,
    fragmentBody:/* glsl */`
      float fallEmitted=uTime-vFallUv.y*uFallFlight;
      vec2 fallCoordinate=vec2((vFallUv.x-.5)*uFallWidth,fallEmitted);
      vec2 fallCoarse=fallCoordinate*vec2(31.0,29.4)+uFallSeed;
      vec2 fallFine=fallCoordinate*vec2(137.0,97.0)+uFallSeed;
      float fallRibbon=astraNoise2(fallCoarse);
      float fallFineResolved=1.0-smoothstep(.30,1.0,max(length(dFdx(fallFine)),length(dFdy(fallFine))));
      float fallGrain=mix(.5,astraNoise2(fallFine),fallFineResolved);
      float fallStrand=astraNoise2(fallCoordinate*vec2(19.0,4.1)+uFallSeed);
      float fallEdge=pow(abs(vFallUv.x-.5)*2.0,10.0);
      float fallAir=uFallFoam*smoothstep(.55,1.0,vFallUv.y)
          *smoothstep(.36,.72,fallStrand*.35+fallRibbon*.65)*.16
          +uFallFoam*fallEdge*.035;
      float fallRelief=(fallRibbon*.0007+fallGrain*.00014)*(1.0-fallAir*.6);
      diffuseColor.rgb=mix(vec3(1.0),vec3(.91,.96,.97),fallAir);
    `,
    alphaBody:/* glsl */`
      // Aerated edge strands separate late in the fall. The core remains a
      // continuous film; discarded gaps reveal the actual background.
      float fallGap=smoothstep(.60,.98,vFallUv.y)*uFallBreakup;
      if(fallEdge*fallGap*(1.0-fallRibbon)>.33) discard;
    `,
    normalBody:/* glsl */`
      vec3 fallDx=dFdx(vFallView),fallDy=dFdy(vFallView);
      vec3 fallN=normalize(cross(fallDx,fallDy));
      vec3 fallR1=cross(fallDy,fallN),fallR2=cross(fallN,fallDx);
      float fallDet=dot(fallDx,fallR1);
      vec3 fallGradient=dFdx(fallRelief)*fallR1+dFdy(fallRelief)*fallR2;
      normal=normalize(abs(fallDet)*fallN-sign(fallDet)*fallGradient);
    `,
    roughnessBody:'roughnessFactor=mix(roughnessFactor,.56,fallAir);',
    transmissionBody:'material.transmission*=1.0-fallAir*.97; material.thickness=vFallThickness;',
  });
  const [nx,ny]=TIERS[quality], positions=[],uvs=[],normals=[],indices=[];
  for(let iy=0;iy<=ny;iy++) for(let ix=0;ix<=nx;ix++) {
    const u=iy/ny,lateral=ix/nx-.5,p=positionAt(u,lateral,0);
    positions.push(...p.toArray());uvs.push(ix/nx,u);
    const n=new THREE.Vector3(0,speed,GRAVITY*u*flight).normalize();normals.push(...n.toArray());
    if(ix<nx && iy<ny) {
      const a=iy*(nx+1)+ix,b=a+1,c=a+nx+1,d=c+1;
      indices.push(a,c,b,b,c,d);
    }
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('normal',new THREE.Float32BufferAttribute(normals,3));
  geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));geometry.setIndex(indices);
  const margin=waves.reduce((s,w)=>s+w.z,0)+width*.03;
  geometry.boundingBox=new THREE.Box3(new THREE.Vector3(-width*.54,-margin,-margin),
    new THREE.Vector3(width*.54,height+margin,impactZ+margin));
  geometry.boundingSphere=geometry.boundingBox.getBoundingSphere(new THREE.Sphere());
  const group=new THREE.Group();group.name=opts.name??'Waterfall';
  const sheet=new THREE.Mesh(geometry,material);sheet.name='FallingWaterFilm';
  keepOutOfDepthPasses(sheet);group.add(sheet);
  let foamMaterial=null;
  if(foam>0) {
    foamMaterial=new THREE.MeshStandardMaterial({color:0xdce7e7,roughness:.73,transparent:true,
      opacity:foam,depthWrite:false,side:THREE.DoubleSide});
    patchStandard(foamMaterial,{name:'WaterfallImpact',uniforms:{uImpactFoam:{value:foam},uImpactSeed:{value:phase}},
      vertexHead:'varying vec2 vImpactUv;',vertexBody:'vImpactUv=uv;',
      fragmentHead:'varying vec2 vImpactUv;\nuniform float uImpactFoam;\nuniform float uImpactSeed;',
      fragmentBody:/* glsl */`
        vec2 p=vec2(vImpactUv.x-.5,.5-vImpactUv.y)*2.0;
        float downstream=p.y+.32;
        float boundary=1.0-smoothstep(.63,1.0,length(vec2(p.x,downstream*.9)));
        float cells=astraFbm2(vec2(p.x*17.0,p.y*13.0-uTime*1.8)+uImpactSeed,3);
        float turbulence=smoothstep(.25,.58,cells);
        float core=exp(-p.x*p.x*2.8-downstream*downstream*30.0);
        diffuseColor.a*=boundary*clamp(core*.67+turbulence*.37,0.0,.78);
        if(diffuseColor.a<.015) discard;
      `});
    const foamMesh=new THREE.Mesh(new THREE.PlaneGeometry(width*1.6,Math.max(width*.9,height*.45)),foamMaterial);
    foamMesh.name='WaterfallImpactFoam';foamMesh.rotation.x=-Math.PI/2;
    foamMesh.position.set(0,.012,impactZ+Math.max(width*.9,height*.45)*.16);
    keepOutOfDepthPasses(foamMesh);group.add(foamMesh);
  }
  const owned=snapshotResources(group);
  let spray=null;
  if(opts.spray!==false && sprayRate>0) {
    spray=makeSpray({origin:[0,.015,impactZ],radius:width*.28,
      rate:sprayRate,speed:Math.min(3.4,Math.sqrt(GRAVITY*height)*.36),
      size:Math.max(.004,Math.min(.025,width*.009)),seed:seed+107});
    group.add(spray);owned.add({dispose:()=>spray.userData.dispose()});
  }
  group.userData.update=t=>{
    if(!Number.isFinite(t)) throw new RangeError('Waterfall.update: time must be finite');
    material.userData.uniforms.uTime.value=t;
    if(foamMaterial) foamMaterial.userData.uniforms.uTime.value=t;
    if(spray) spray.userData.update(t);
  };
  group.userData.sample=(u,lateral=0,t=material.userData.uniforms.uTime.value)=>{
    if(![u,lateral,t].every(Number.isFinite)) throw new RangeError('Waterfall.sample: inputs must be finite');
    u=clamp(u);lateral=clamp(lateral,-.5,.5);
    const position=positionAt(u,lateral,t),epsilon=1e-4;
    const along=positionAt(clamp(u+epsilon),lateral,t).sub(positionAt(clamp(u-epsilon),lateral,t));
    const across=positionAt(u,clamp(lateral+epsilon,-.5,.5),t).sub(positionAt(u,clamp(lateral-epsilon,-.5,.5),t));
    const velocity=new THREE.Vector3(0,-GRAVITY*u*flight,speed),actualWidth=widthAt(u,t);
    return {position,normal:along.cross(across).normalize(),velocity,width:actualWidth,
      thickness:thickness*speed*width/(velocity.length()*actualWidth),travelTime:u*flight};
  };
  group.userData.impact=new THREE.Vector3(0,0,impactZ);
  group.userData.flightTime=flight;
  group.userData.quality=quality;
  return attachDisposal(group,owned);
}
