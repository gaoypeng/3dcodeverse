/**
 * A bounded shared fire/soot field for fuel beds, large fires and authored
 * structural-fire scenes. This is prescribed transport, not combustion or
 * structural simulation. Legacy makeFire/makeCandle remain in fire.js.
 *
 * makeFireField({emitters:[{position:[0,0,0],radius:.3,height:1,strength:1}],
 *   seed:7, wind:[.15,0], quality:'balanced', intensity:1,
 *   smoke:{height:3,density:.65,albedo:0x514942},
 *   embers:{count:48,size:[.0015,.006],lifetime:[2,4],riseSpeed:1.2},
 *   lighting:{count:2,intensity:12}, occluders:[], depthResolution:512})
 *
 * Positions/dimensions are local metres; wind is local metres/second. All
 * emitters share one ray integration. A baked source atlas makes runtime
 * density cost independent of emitter count. Source layout and wind are baked at
 * construction; rebuild the field to change them. Nonuniform object transforms
 * are supported; optical depth uses world metres. update(t) takes absolute seconds;
 * sampleField(x,y,z,t) mirrors the GPU's flame/soot/temperature field.
 *
 * Optional occluders are BORROWED Object3D roots. An isolated depth-proxy scene
 * terminates rays at those opaque meshes, including alpha cuts, instances and
 * customDepthMaterial deformation. Hidden ancestors are respected. Original
 * callbacks/parents/materials are never changed. Skinned meshes and morphed
 * InstancedMesh occluders are unsupported;
 * custom vertex deformation requires a matching customDepthMaterial. Glass and
 * other transparent volumes are not depth-resolved. One depth-aware fire field
 * per scene is recommended; each visible view adds a pass over its occluders.
 * Logarithmic depth is not supported by the optional depth-termination path.
 * No global renderer hooks or recursively captured reflective materials.
 */
import * as THREE from 'three';
import { bakeFbm3, dataTexture3D, mulberry32, sampleGrid3 } from './noise.js';
import { makeShaderMaterial, keepOutOfDepthPasses, withRendererState } from './shader.js';
import { snapshotResources, attachDisposal } from './lifecycle.js';

const TIERS = {
  low: { resolution: 48, steps: 48, lightSteps: 3 },
  balanced: { resolution: 64, steps: 80, lightSteps: 5 },
  high: { resolution: 80, steps: 128, lightSteps: 7 },
};
const NOISE_RESOLUTION = 48;
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };
const fract = x => x - Math.floor(x);
function number(value, fallback, min, max, label) {
  const v = value ?? fallback;
  if (!Number.isFinite(v) || v < min || v > max) throw new RangeError(`makeFireField: invalid ${label}`);
  return v;
}
function vector(value, fallback, length, label) {
  const v = value?.toArray ? value.toArray() : value ?? fallback;
  if (!Array.isArray(v) || v.length !== length || !v.every(Number.isFinite)) {
    throw new RangeError(`makeFireField: ${label} needs ${length} finite components`);
  }
  return v.slice();
}
function noiseTexture(seed) {
  const { values } = bakeFbm3(mulberry32(seed), NOISE_RESOLUTION, [4, 8, 16], 4);
  return dataTexture3D(Uint8Array.from(values, v => Math.round(clamp(v) * 255)), NOISE_RESOLUTION, true);
}
function sourceAtlas(emitters, box, resolution, smokeHeight, wind, rise, seed) {
  const extent = box.getSize(new THREE.Vector3()), data = new Uint8Array(resolution ** 3 * 4);
  const random = mulberry32(seed ^ 0x7139), phases = emitters.map(() => random()); let at = 0;
  for (let z = 0; z < resolution; z++) for (let y = 0; y < resolution; y++) for (let x = 0; x < resolution; x++) {
    const px = box.min.x + (x + .5) / resolution * extent.x;
    const py = box.min.y + (y + .5) / resolution * extent.y;
    const pz = box.min.z + (z + .5) / resolution * extent.z;
    let flame = 0, progress = 0, soot = 0, phase = 0, strongest = 0;
    for (let i = 0; i < emitters.length; i++) {
      const e = emitters[i], h = py - e.position[1];
      if (h <= 0 || e.strength === 0) continue;
      const age = h / rise, dx = px - e.position[0] - wind[0] * age;
      const dz = pz - e.position[2] - wind[1] * age;
      const distance = Math.hypot(dx, dz), f = h / e.height;
      if (f < 1.65) {
        const profile = Math.pow(Math.max(0, 1 - f / 1.05), .72) * .94 - Math.max(0,f-1.05)*2;
        const potential = clamp(.5 + (profile - distance / e.radius) * .57 + Math.log2(Math.max(.01,e.strength))*.075);
        if (potential > flame) { flame = potential; progress = f; phase = phases[i]; }
      }
      const s = h / smokeHeight;
      if (s < 1) {
        const radius = e.radius * .70 + h * .23;
        const plume = Math.pow(clamp(1 - distance / radius), .65)
          * smooth(.025, .16, s) * (1 - smooth(.70, 1, s)) * Math.exp(-s * 1.4) * e.strength;
        soot += plume * .55;
        if (plume > strongest && flame === 0) { strongest = plume; phase = phases[i]; }
      }
    }
    data[at++] = Math.round(flame * 255); data[at++] = Math.round(clamp(progress) * 255);
    data[at++] = Math.round(clamp(soot) * 255); data[at++] = Math.round(phase * 255);
  }
  return dataTexture3D(data, resolution, false);
}

const FIELD = /* glsl */`
precision highp sampler3D;
uniform float uTime;
uniform sampler3D uSourceAtlas, uFieldNoise;
uniform sampler2D uOpaqueDepth;
uniform vec3 uBoxMin, uBoxSize, uFlameBoxMin, uFlameBoxSize, uSunLocal, uSunColor, uAmbient, uSootAlbedo;
uniform vec4 uViewport, uFireLights[4];
uniform vec3 uFireLightColor;
uniform mat4 uViewToLocal, uInverseProjection, uLocalToClip;
uniform mat3 uLocalToWorld;
uniform float uCoarseScale, uWarp, uRise, uTurbulence, uIntensity;
uniform float uFlameExtinction, uSootExtinction, uUseOpaqueDepth;
uniform int uFireLightCount;
varying vec3 vFieldLocal;
vec4 fireAtlas(vec3 p) {
  vec3 uv=(p-uBoxMin)/uBoxSize;
  if(any(lessThan(uv,vec3(0.0)))||any(greaterThan(uv,vec3(1.0))))return vec4(0.0);
  return texture(uSourceAtlas,uv);
}
vec3 fireFieldSample(vec3 p) {
  vec4 initial=fireAtlas(p);
  vec3 flow=p*vec3(.32,.13,.32)/uCoarseScale-vec3(0.0,uTime*uRise*.13/uCoarseScale,0.0);
  vec3 coarse=texture(uFieldNoise,flow+initial.a*.31).rgb;
  float anchor=smoothstep(0.0,.22,initial.g);
  vec3 warped=p+(coarse-.5)*uWarp*vec3(1.0,.38,1.0)*anchor*uTurbulence;
  vec4 field=fireAtlas(warped);
  float fine=texture(uFieldNoise,p*vec3(2.6,.75,2.6)-vec3(0.0,uTime*uRise*.75,0.0)+.197).g;
  float surface=field.r-.5+((coarse.g-.5)*(.35+field.g*.8)+(fine-.5)*.30)*uTurbulence;
  float flame=smoothstep(-.025,.075,surface)*(1.0-smoothstep(.035,.22,surface)*.97)
    *smoothstep(0.0,.055,field.g)*(1.0-smoothstep(.90,1.0,field.g));
  flame*=smoothstep(.21+field.g*.20,.42+field.g*.23,coarse.r*.65+fine*.35);
  float smoke=field.b*pow(clamp(.68+(coarse.r-.5)*2.4+(fine-.5)*.55,0.0,1.6),1.3);
  float temperature=clamp(.18+flame*.57+coarse.b*.15-field.g*.18,0.0,1.0);
  return vec3(flame,smoke,temperature);
}
// Light samples use only the precomputed soot scaffold, not every emitter.
float fireSootCoarse(vec3 p) {
  vec4 a=fireAtlas(p);
  float n=texture(uFieldNoise,p*vec3(.32,.13,.32)/uCoarseScale-vec3(0.0,uTime*uRise*.13/uCoarseScale,0.0)+a.a*.31).r;
  return a.b*pow(clamp(.68+(n-.5)*2.4,0.0,1.6),1.3);
}
vec2 fireRange(vec3 origin,vec3 direction,vec3 boxMin,vec3 boxSize) {
  vec3 safe=mix(vec3(-1.0),vec3(1.0),step(vec3(0.0),direction))*max(abs(direction),vec3(1e-7));
  vec3 a=(boxMin-origin)/safe,b=(boxMin+boxSize-origin)/safe;
  vec3 near=min(a,b),far=max(a,b);
  return vec2(max(near.x,max(near.y,near.z)),min(far.x,min(far.y,far.z)));
}
vec2 fireBox(vec3 origin,vec3 direction){return fireRange(origin,direction,uBoxMin,uBoxSize);}
vec3 fireRadiance(float heat) {
  vec3 c=mix(vec3(1.8,.06,.002),vec3(4.0,.75,.04),smoothstep(.15,.58,heat));
  return mix(c,vec3(6.0,2.8,.65),smoothstep(.55,.96,heat))*.72;
}
float fireSunTransmission(vec3 p,float jitter) {
  float end=max(0.0,fireBox(p,uSunLocal).y),sum=0.0;
  for(int j=0;j<FIELD_LIGHT_STEPS;j++) {
    float a=float(j)/float(FIELD_LIGHT_STEPS),b=float(j+1)/float(FIELD_LIGHT_STEPS);
    float start=a*a*end,stop=b*b*end;
    sum+=fireSootCoarse(p+uSunLocal*mix(start,stop,jitter))*(stop-start);
  }
  return exp(-sum*uSootExtinction*length(uLocalToWorld*uSunLocal));
}
`;

function fieldMaterial(uniforms, tier) {
  return makeShaderMaterial({name:'SharedFireSootVolume',util:false,fog:true,
    transparent:true,depthWrite:false,side:THREE.BackSide,uniforms,
    defines:{FIELD_STEPS:tier.steps,FIELD_LIGHT_STEPS:tier.lightSteps},
    varyings:'varying vec3 vFieldLocal;',vertexMain:'vFieldLocal=position;',fragmentHead:FIELD,
    fragmentMain:/* glsl */`
      vec2 uv=(gl_FragCoord.xy-uViewport.xy)/uViewport.zw;
      vec4 nearView=uInverseProjection*vec4(uv*2.0-1.0,-1.0,1.0);
      vec4 farView=uInverseProjection*vec4(uv*2.0-1.0,1.0,1.0);
      vec3 origin=(uViewToLocal*vec4(nearView.xyz/nearView.w,1.0)).xyz;
      vec3 farPoint=(uViewToLocal*vec4(farView.xyz/farView.w,1.0)).xyz;
      vec3 ray=normalize(farPoint-origin);
      vec2 range=fireBox(origin,ray);float enter=max(0.0,range.x),leave=range.y;
      if(uUseOpaqueDepth>.5) {
        float depth=texture2D(uOpaqueDepth,uv).r;
        if(depth<.999999) {
          vec4 viewPoint=uInverseProjection*vec4(uv*2.0-1.0,depth*2.0-1.0,1.0);
          vec3 localPoint=(uViewToLocal*vec4(viewPoint.xyz/viewPoint.w,1.0)).xyz;
          leave=min(leave,dot(localPoint-origin,ray));
        }
      }
      if(leave<=enter||uIntensity<=0.0)discard;
      float metric=length(uLocalToWorld*ray);
      vec2 hot=fireRange(origin,ray,uFlameBoxMin,uFlameBoxSize);
      hot=vec2(max(enter,hot.x),min(leave,hot.y));
      int pre=0,post=0;
      if(hot.y>hot.x){
        if(hot.x>enter)pre=int(float(FIELD_STEPS)*.12);
        if(hot.y<leave)post=int(float(FIELD_STEPS)*.12);
      }else hot=vec2(enter,leave);
      int mainCount=FIELD_STEPS-pre-post;
      float jitter=fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453);
      vec3 radiance=vec3(0.0),first=origin+ray*leave;float transmittance=1.0;bool hit=false;
      for(int i=0;i<FIELD_STEPS;i++) {
        float start=hot.x,stop=hot.y,offset=float(i-pre),count=float(mainCount);
        if(i<pre){start=enter;stop=hot.x;offset=float(i);count=float(pre);}
        else if(i>=pre+mainCount){start=hot.y;stop=leave;offset=float(i-pre-mainCount);count=float(post);}
        float ds=(stop-start)/max(count,1.0);
        vec3 p=origin+ray*(start+(offset+jitter)*ds);
        vec3 field=fireFieldSample(p);
        float flame=field.x*uFlameExtinction,soot=field.y*uSootExtinction,total=flame+soot;
        if(total<.0001)continue;
        float alpha=1.0-exp(-total*ds*metric);
        if(!hit&&alpha>.0005){first=p;hit=true;}
        vec3 incoming=uAmbient;
        if(soot>.001) {
          incoming+=uSunColor*fireSunTransmission(p,jitter);
          for(int j=0;j<4;j++) {
            if(j>=uFireLightCount)break;
            vec3 delta=uLocalToWorld*(p-uFireLights[j].xyz);
            incoming+=uFireLightColor*uFireLights[j].w/(dot(delta,delta)+.09);
          }
        }
        vec3 source=(fireRadiance(field.z)*flame*uIntensity+incoming*uSootAlbedo*soot)/max(total,.0001);
        radiance+=transmittance*alpha*source;transmittance*=1.0-alpha;
        if(transmittance<.008)break;
      }
      float alpha=1.0-transmittance;if(alpha<.005)discard;
      #ifndef USE_LOGDEPTHBUF
      vec4 clip=uLocalToClip*vec4(first,1.0);
      gl_FragDepth=clamp(clip.z/clip.w*.5+.5,0.0,1.0);
      #endif
      gl_FragColor=vec4(radiance/max(alpha,.001),alpha);
    `});
}

function visibleInTree(object) {
  for(let o=object;o;o=o.parent)if(!o.visible)return false;
  return true;
}

/** Isolated borrowed-geometry proxies: no original render callbacks can recurse. */
function depthCapture(roots, resolution) {
  const scene=new THREE.Scene(),proxies=new Map(),materials=new Map(),ownedInstances=new Set();
  let target=null,captures=0,disposed=false;
  const invisible=new THREE.MeshDepthMaterial();invisible.visible=false;
  function depthMaterial(material, source) {
    if(!material?.visible||material.transparent||material.transmission>0)return invisible;
    if(source.customDepthMaterial)return source.customDepthMaterial;
    let depth=materials.get(material);
    if(!depth){depth=new THREE.MeshDepthMaterial();materials.set(material,depth);}
    const changed=depth.map!==material.map||depth.alphaMap!==material.alphaMap
      ||depth.alphaTest!==material.alphaTest||depth.side!==material.side
      ||depth.displacementMap!==material.displacementMap||depth.userData.sourceVersion!==material.version;
    depth.map=material.map;depth.alphaMap=material.alphaMap;depth.alphaTest=material.alphaTest;depth.opacity=material.opacity;
    depth.side=material.side;depth.displacementMap=material.displacementMap;
    depth.displacementScale=material.displacementScale;depth.displacementBias=material.displacementBias;
    depth.wireframe=material.wireframe;depth.vertexColors=material.vertexColors;depth.alphaHash=material.alphaHash;
    depth.clippingPlanes=material.clippingPlanes;depth.clipIntersection=material.clipIntersection;
    depth.userData.sourceVersion=material.version;if(changed)depth.needsUpdate=true;
    return depth;
  }
  function sync() {
    const seen=new Set();
    for(const root of roots)root.traverse(source=>{
      if(!source.isMesh||seen.has(source)||!visibleInTree(source))return;
      seen.add(source);
      if(source.isSkinnedMesh||source.isInstancedMesh&&source.morphTexture)throw new TypeError('makeFireField: skinned or morphed-instance depth occluders are unsupported');
      if(source.userData?.astraNoOverride)return;
      const material=Array.isArray(source.material)?source.material.map(m=>depthMaterial(m,source))
        :depthMaterial(source.material,source);
      let proxy=proxies.get(source);
      if(!proxy){
        proxy=source.isInstancedMesh?new THREE.InstancedMesh(source.geometry,material,source.instanceMatrix.count)
          :new THREE.Mesh(source.geometry,material);
        if(proxy.isInstancedMesh)ownedInstances.add(proxy);
        proxy.matrixAutoUpdate=false;proxy.frustumCulled=false;proxy.name='FireDepthProxy';
        proxies.set(source,proxy);scene.add(proxy);
      }
      source.updateWorldMatrix(true,false);proxy.geometry=source.geometry;proxy.material=material;
      proxy.layers.mask=source.layers.mask;
      proxy.matrix.copy(source.matrixWorld);proxy.matrixWorld.copy(source.matrixWorld);
      if(source.morphTargetInfluences)proxy.morphTargetInfluences=source.morphTargetInfluences.slice();
      if(proxy.isInstancedMesh){
        if(proxy.instanceMatrix.count<source.instanceMatrix.count){
          proxy.dispose();proxy.instanceMatrix=new THREE.InstancedBufferAttribute(new Float32Array(source.instanceMatrix.array.length),16);
        }
        proxy.instanceMatrix.array.set(source.instanceMatrix.array);proxy.instanceMatrix.needsUpdate=true;
        proxy.count=Math.min(source.count,source.instanceMatrix.count);
      }
    });
    // A source removed, hidden or turned into a skip since the last capture
    // loses its proxy, so the proxy scene never outgrows the live occluders.
    proxies.forEach((proxy,source)=>{
      if(seen.has(source)&&!source.userData?.astraNoOverride)return;
      scene.remove(proxy);proxies.delete(source);
      if(ownedInstances.delete(proxy))proxy.dispose();
    });
  }
  return {
    capture(renderer,camera,viewport){
      if(disposed)throw new Error('Disposed fire depth capture');
      sync();
      const scale=resolution/Math.max(viewport.z,viewport.w);
      const width=Math.max(1,Math.round(viewport.z*scale)),height=Math.max(1,Math.round(viewport.w*scale));
      if(!target){
        target=new THREE.WebGLRenderTarget(width,height,{minFilter:THREE.NearestFilter,magFilter:THREE.NearestFilter});
        target.depthTexture=new THREE.DepthTexture(width,height,THREE.UnsignedIntType);
        target.depthTexture.minFilter=target.depthTexture.magFilter=THREE.NearestFilter;
      }else if(target.width!==width||target.height!==height)target.setSize(width,height);
      withRendererState(renderer,()=>{
        renderer.xr.enabled=false;renderer.shadowMap.autoUpdate=false;renderer.toneMapping=THREE.NoToneMapping;
        renderer.setRenderTarget(target);renderer.setScissorTest(false);renderer.setClearColor(0xffffff,1);
        renderer.clear(true,true,true);renderer.render(scene,camera);
      });
      captures++;return target.depthTexture;
    },
    get captures(){return captures;},
    get target(){return target;},
    get proxies(){return proxies.size;},
    dispose(){if(disposed)return;disposed=true;target?.dispose();
      materials.forEach(m=>m.dispose());invisible.dispose();ownedInstances.forEach(m=>m.dispose());
      proxies.clear();materials.clear();ownedInstances.clear();scene.clear();},
  };
}

export function makeFireField(opts = {}) {
  const input=opts.emitters??[{position:[0,0,0],radius:.3,height:1,strength:1}];
  if(!Array.isArray(input)||input.length<1||input.length>16)throw new RangeError('makeFireField: needs1–16 emitters');
  const emitters=input.map((e,i)=>({position:vector(e.position,[0,0,0],3,`emitter${i} position`),
    radius:number(e.radius,.3,.025,8,'emitter radius'),height:number(e.height,1,.08,25,'emitter height'),
    strength:number(e.strength,1,0,4,'emitter strength')}));
  const activeEmitters=emitters.filter(e=>e.strength>0);
  const sources=activeEmitters.length?activeEmitters:[emitters[0]];
  const seed=number(opts.seed,7,-2147483648,2147483647,'seed');
  if(!Number.isInteger(seed))throw new RangeError('makeFireField: seed must be an integer');
  const quality=opts.quality??'balanced';if(!Object.hasOwn(TIERS,quality))throw new RangeError('makeFireField: invalid quality');
  const tier=TIERS[quality],wind=vector(opts.wind,[.12,0],2,'wind');
  if(wind.some(v=>Math.abs(v)>8))throw new RangeError('makeFireField: wind exceeds8m/s');
  const intensity=number(opts.intensity,1,0,8,'intensity'),turbulence=number(opts.turbulence,1,0,2,'turbulence');
  const radius=sources.reduce((s,e)=>s+e.radius,0)/sources.length;
  const height=Math.max(...sources.map(e=>e.height)),rise=number(opts.riseSpeed,.75+Math.sqrt(height)*.75,.1,12,'riseSpeed');
  const smoke=opts.smoke===false?{density:0}:opts.smoke??{};
  const smokeHeight=number(smoke.height,height*3,Math.max(.1,height),80,'smoke height');
  const smokeDensity=number(smoke.density,.65,0,5,'smoke density');
  const warp=Math.max(.025,Math.sqrt(radius)*.25),box=new THREE.Box3();
  for(const e of sources){
    const top=smokeDensity>0?Math.max(e.height*1.1,smokeHeight):e.height*1.1;
    const spread=smokeDensity>0?e.radius*.7+smokeHeight*.23:e.radius*1.4;
    const reach=Math.max(e.radius*(1.35+.85*turbulence),spread)+warp*1.5*turbulence;
    box.expandByPoint(new THREE.Vector3(e.position[0]-reach+Math.min(0,wind[0]*top/rise),e.position[1],e.position[2]-reach+Math.min(0,wind[1]*top/rise)));
    box.expandByPoint(new THREE.Vector3(e.position[0]+reach+Math.max(0,wind[0]*top/rise),e.position[1]+top+warp*.3,e.position[2]+reach+Math.max(0,wind[1]*top/rise)));
  }
  const flameBox=new THREE.Box3();
  for(const e of sources){const top=e.height*1.12,reach=e.radius*(1.35+.85*turbulence)+warp*1.5*turbulence;
    flameBox.expandByPoint(new THREE.Vector3(e.position[0]-reach+Math.min(0,wind[0]*top/rise),e.position[1],e.position[2]-reach+Math.min(0,wind[1]*top/rise)));
    flameBox.expandByPoint(new THREE.Vector3(e.position[0]+reach+Math.max(0,wind[0]*top/rise),e.position[1]+top+warp*.3,e.position[2]+reach+Math.max(0,wind[1]*top/rise)));
  }
  const size=box.getSize(new THREE.Vector3());
  if(Math.max(size.x,size.y,size.z)>120)throw new RangeError('makeFireField: emitter field exceeds120m');
  const noise=noiseTexture(seed),atlas=sourceAtlas(emitters,box,tier.resolution,smokeHeight,wind,rise,seed);
  const emptyDepth=new THREE.DataTexture(new Uint8Array([255,255,255,255]),1,1);emptyDepth.needsUpdate=true;
  const roots=opts.occluders??[];
  if(!Array.isArray(roots)||roots.some(o=>!o?.isObject3D))throw new TypeError('makeFireField: occluders must be Object3D roots');
  const depthResolution=number(opts.depthResolution,512,64,2048,'depthResolution');
  if(!Number.isInteger(depthResolution))throw new RangeError('makeFireField: depthResolution must be integer');
  const depth=roots.length?depthCapture(roots.slice(),depthResolution):null;
  const lighting=opts.lighting===false?{count:0}:opts.lighting??{};
  const requestedLights=number(lighting.count,Math.min(2,activeEmitters.length),0,4,'light count');
  const lightCount=Math.min(requestedLights,activeEmitters.length);
  if(!Number.isInteger(requestedLights))throw new RangeError('makeFireField: light count must be integer');
  const lightIntensity=number(lighting.intensity,12*radius/.3,0,20000,'light intensity');
  const uniforms={
    uTime:{value:0},uSourceAtlas:{value:atlas},uFieldNoise:{value:noise},uOpaqueDepth:{value:emptyDepth},
    uBoxMin:{value:box.min.clone()},uBoxSize:{value:size.clone()},
    uFlameBoxMin:{value:flameBox.min.clone()},uFlameBoxSize:{value:flameBox.getSize(new THREE.Vector3())},uViewport:{value:new THREE.Vector4(0,0,1,1)},
    uViewToLocal:{value:new THREE.Matrix4()},uInverseProjection:{value:new THREE.Matrix4()},
    uLocalToClip:{value:new THREE.Matrix4()},uLocalToWorld:{value:new THREE.Matrix3()},
    uSunLocal:{value:new THREE.Vector3(0,1,0)},uSunColor:{value:new THREE.Color(0)},uAmbient:{value:new THREE.Color(.03,.03,.04)},
    uSootAlbedo:{value:new THREE.Color(smoke.albedo??0x514942)},uCoarseScale:{value:Math.max(.3,Math.sqrt(radius)*.7)},
    uWarp:{value:warp},uRise:{value:rise},uTurbulence:{value:turbulence},uIntensity:{value:intensity},
    uFlameExtinction:{value:1.3/Math.sqrt(radius)},uSootExtinction:{value:smokeDensity},uUseOpaqueDepth:{value:0},
    uFireLightCount:{value:lightCount},uFireLights:{value:Array.from({length:4},()=>new THREE.Vector4())},
    uFireLightColor:{value:new THREE.Color(0xff973d)},
  };
  const group=new THREE.Group();group.name=opts.name??'FireField';
  const geometry=new THREE.BoxGeometry(size.x,size.y,size.z);geometry.translate(...box.getCenter(new THREE.Vector3()).toArray());
  const material=fieldMaterial(uniforms,tier);material.userData.bloom=true;
  const volume=new THREE.Mesh(geometry,material);volume.name='SharedFlameSootVolume';volume.renderOrder=2;volume.visible=activeEmitters.length>0;
  keepOutOfDepthPasses(volume);group.add(volume);
  const inverse=new THREE.Matrix4(),sunPosition=new THREE.Vector3(),sunTarget=new THREE.Vector3(),sunDirection=new THREE.Vector3(),ambientColor=new THREE.Color();
  const guard=volume.onBeforeRender;
  volume.onBeforeRender=function(renderer,scene,camera,geo,mat,...rest){
    guard.call(this,renderer,scene,camera,geo,mat,...rest);
    if(mat!==material||scene.overrideMaterial)return;
    inverse.copy(volume.matrixWorld).invert();
    uniforms.uInverseProjection.value.copy(camera.projectionMatrixInverse);
    uniforms.uViewToLocal.value.multiplyMatrices(inverse,camera.matrixWorld);
    uniforms.uLocalToClip.value.copy(camera.projectionMatrix).multiply(camera.matrixWorldInverse).multiply(volume.matrixWorld);
    uniforms.uLocalToWorld.value.setFromMatrix4(volume.matrixWorld);
    renderer.getCurrentViewport(uniforms.uViewport.value);
    uniforms.uSunColor.value.setRGB(0,0,0);uniforms.uAmbient.value.setRGB(0,0,0);let strongest=0;
    scene.traverseVisible(o=>{
      if(o.isDirectionalLight&&o.intensity>strongest){strongest=o.intensity;
        o.getWorldPosition(sunPosition);o.target.getWorldPosition(sunTarget);sunDirection.copy(sunPosition).sub(sunTarget).normalize();
        uniforms.uSunLocal.value.copy(sunDirection).transformDirection(inverse);
        uniforms.uSunColor.value.copy(o.color).multiplyScalar(o.intensity*.20);
      }else if(o.isAmbientLight||o.isHemisphereLight)uniforms.uAmbient.value.add(ambientColor.copy(o.color).multiplyScalar(o.intensity*.15));
    });
    if(scene.environment)uniforms.uAmbient.value.addScalar(.035);
    if(depth){
      if(renderer.capabilities.logarithmicDepthBuffer)throw new Error('makeFireField: occluder depth requires non-logarithmic depth');
      uniforms.uOpaqueDepth.value=depth.capture(renderer,camera,uniforms.uViewport.value);
      uniforms.uUseOpaqueDepth.value=1;
    }
  };
  const lights=[];
  const ranked=activeEmitters.map((e,i)=>({e,i,weight:e.radius*e.radius*e.strength})).sort((a,b)=>b.weight-a.weight);
  for(let i=0;i<lightCount;i++){
    const e=ranked[Math.floor(i*ranked.length/lightCount)].e;
    const light=new THREE.PointLight(0xff973d,lightIntensity,0,2);light.name=`FireFieldLight_${i}`;
    light.position.fromArray(e.position);light.position.y+=e.height*.24;light.userData.sourceStrength=e.strength;group.add(light);lights.push(light);
  }
  const emberOptions=opts.embers===false?{count:0}:opts.embers??{};
  const requestedEmbers=number(emberOptions.count,48,0,512,'ember count');
  const count=activeEmitters.length?requestedEmbers:0;
  if(!Number.isInteger(requestedEmbers))throw new RangeError('makeFireField: ember count must be integer');
  const emberSize=vector(emberOptions.size,[.0015,.006],2,'ember size');
  const lifetime=vector(emberOptions.lifetime,[2,4],2,'ember lifetime');
  if(emberSize[0]<=0||emberSize[1]<emberSize[0]||emberSize[1]>.12||lifetime[0]<=0||lifetime[1]<lifetime[0]||lifetime[1]>30)throw new RangeError('makeFireField: invalid ember size/lifetime');
  const emberRise=number(emberOptions.riseSpeed,rise,.1,20,'ember rise speed');
  const random=mulberry32(seed^0x1837),particles=Array.from({length:count},()=>{
    const total=activeEmitters.reduce((sum,e)=>sum+e.radius*e.radius*e.strength,0);
    let pick=random()*total,emitter=activeEmitters[activeEmitters.length-1];
    for(const candidate of activeEmitters){pick-=candidate.radius*candidate.radius*candidate.strength;if(pick<=0){emitter=candidate;break;}}
    return {emitter,phase:random(),life:THREE.MathUtils.lerp(...lifetime,random()),angle:random()*Math.PI*2,
      offset:random()*.7,size:THREE.MathUtils.lerp(...emberSize,random())};
  });
  const dummy=new THREE.Object3D(),emberColor=new THREE.Color();let embers=null;
  if(count){embers=new THREE.InstancedMesh(new THREE.SphereGeometry(1,5,3),new THREE.MeshBasicMaterial(),count);
    embers.name='FieldEmbers';embers.frustumCulled=false;embers.instanceMatrix.setUsage(THREE.DynamicDrawUsage);group.add(embers);}
  // `out` lets update() reuse one record instead of allocating per ember.
  function sampleEmber(index,t,out={position:new THREE.Vector3()}){
    if(!count)return null;const p=particles[clamp(index|0,0,count-1)],a=fract(t/p.life+p.phase),age=a*p.life;
    const e=p.emitter,swirl=Math.sin(age*2.1+p.angle)*.07*age;
    out.position.set(e.position[0]+Math.cos(p.angle)*e.radius*p.offset+wind[0]*age+swirl,
      e.position[1]+.08+emberRise*age,e.position[2]+Math.sin(p.angle)*e.radius*p.offset+wind[1]*age+Math.cos(age*1.7+p.angle)*.07*age);
    out.size=p.size*Math.pow(Math.sin(a*Math.PI),.6);out.age=a;return out;
  }
  const ember={position:new THREE.Vector3()};
  let disposed=false;
  group.userData.update=group.userData.tick=(t=0)=>{
    if(disposed)return;number(t,0,-1e9,1e9,'time');material.uniforms.uTime.value=t;
    lights.forEach((light,i)=>{const flicker=.83+.10*Math.sin(t*(3.1+i*.47)+seed+i*2)+.07*Math.sin(t*7.3+seed*.7+i);
      light.intensity=lightIntensity*flicker*intensity*light.userData.sourceStrength;
      uniforms.uFireLights.value[i].set(light.position.x,light.position.y,light.position.z,light.intensity*.10);});
    if(embers){embers.visible=intensity>0;for(let i=0;i<count;i++){
      const p=sampleEmber(i,t,ember);dummy.position.copy(p.position);dummy.scale.set(p.size,p.size*2.5,p.size);dummy.updateMatrix();
      embers.setMatrixAt(i,dummy.matrix);emberColor.setRGB(5*(1-p.age*.82),.6*(1-p.age),.01).multiplyScalar(intensity);embers.setColorAt(i,emberColor);
    }embers.instanceMatrix.needsUpdate=true;embers.instanceColor.needsUpdate=true;}
  };
  const atlasRead=p=>[0,1,2,3].map(c=>{
    const uv=p.map((v,i)=>(v-box.min.getComponent(i))/size.getComponent(i));
    return uv.some(v=>v<0||v>1)?0:sampleGrid3(atlas.image.data,tier.resolution,...uv.map(v=>v*tier.resolution-.5),4,c,false)/255;
  });
  const noiseRead=(p,c)=>sampleGrid3(noise.image.data,NOISE_RESOLUTION,...p.map(v=>v*NOISE_RESOLUTION-.5),4,c)/255;
  group.userData.sampleField=(x,y,z,t=material.uniforms.uTime.value)=>{
    if(![x,y,z,t].every(Number.isFinite))throw new RangeError('makeFireField: sample requires finite coordinates/time');
    const p=[x,y,z],initial=atlasRead(p),coarseScale=uniforms.uCoarseScale.value;
    const flow=p.map((v,i)=>v*(i===1?.13:.32)/coarseScale-(i===1?t*rise*.13/coarseScale:0)+initial[3]*.31);
    const coarse=[0,1,2].map(c=>noiseRead(flow,c)),anchor=smooth(0,.22,initial[1]);
    const warped=p.map((v,i)=>v+(coarse[i]-.5)*warp*(i===1?.38:1)*anchor*turbulence),field=atlasRead(warped);
    const fine=noiseRead(p.map((v,i)=>v*(i===1?.75:2.6)-(i===1?t*rise*.75:0)+.197),1);
    const surface=field[0]-.5+((coarse[1]-.5)*(.35+field[1]*.8)+(fine-.5)*.30)*turbulence;
    const flame=smooth(-.025,.075,surface)*(1-smooth(.035,.22,surface)*.97)*smooth(0,.055,field[1])*(1-smooth(.90,1,field[1]))
      *smooth(.21+field[1]*.20,.42+field[1]*.23,coarse[0]*.65+fine*.35);
    const soot=field[2]*Math.pow(clamp(.68+(coarse[0]-.5)*2.4+(fine-.5)*.55,0,1.6),1.3);
    return {flame,soot,temperature:clamp(.18+flame*.57+coarse[2]*.15-field[1]*.18)};
  };
  const bounds=box.clone();
  if(count)for(const e of sources){const age=lifetime[1],travel=.07*age+emberSize[1]*3;
    bounds.expandByPoint(new THREE.Vector3(e.position[0]-e.radius*.7+Math.min(0,wind[0]*age)-travel,e.position[1],e.position[2]-e.radius*.7+Math.min(0,wind[1]*age)-travel));
    bounds.expandByPoint(new THREE.Vector3(e.position[0]+e.radius*.7+Math.max(0,wind[0]*age)+travel,e.position[1]+.08+emberRise*age+travel,e.position[2]+e.radius*.7+Math.max(0,wind[1]*age)+travel));}
  group.userData.bounds=bounds;group.userData.sampleEmber=sampleEmber;
  group.userData.emitters=emitters.map(e=>({...e,position:e.position.slice()}));group.userData.quality=quality;
  group.userData.cost={emitters:emitters.length,steps:tier.steps,lightSteps:tier.lightSteps,
    textureBytes:atlas.image.data.byteLength+noise.image.data.byteLength,depthPass:!!depth};
  group.userData.depthCapture=depth;
  const owned=snapshotResources(group);[atlas,noise,emptyDepth,...lights].forEach(r=>owned.add(r));
  if(depth)owned.add(depth);owned.add({dispose(){disposed=true;}});
  attachDisposal(group,owned);group.userData.update(0);return group;
}
