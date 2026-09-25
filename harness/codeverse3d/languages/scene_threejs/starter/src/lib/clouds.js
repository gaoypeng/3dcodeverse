// A connected, ray-marched cloud deck with optical self-shadowing.
// cloudTexture remains a lightweight public alpha-map helper; the sky
// itself no longer stacks transparent camera-facing puff sprites.
import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { mulberry32, fbm2, bakeFbm3, dataTexture3D } from './noise.js';
import { intOption, makeShaderMaterial, keepOutOfDepthPasses, option, positive, readWind, sunVector, makeLightProbe, vector } from './shader.js';

// Scattering tints, multiplied by scene illumination. These are not baked
// cloud brightness: night belongs to the light rig, not black cloud albedo.
const PRESETS = {
  day: { sunColor: 0xfff4e6, shadeColor: 0x7e94b8, litGain: 1.55 },
  golden: { sunColor: 0xffd0a0, shadeColor: 0x7c82ac, litGain: 1.70 },
  // Broad overlapping density at a common altitude.
  overcast: { sunColor: 0xbcc3cc, shadeColor: 0x717b8a, litGain: 1.05,
              count: 40, altitude: 230, spread: 45, alpha: 0.97,
              stretch: 1.9 },
  night: { sunColor: 0xe0e8f0, shadeColor: 0x71839c, litGain: 0.85,
           alpha: 0.88 },
};

/**
 * The vector that actually LIGHTS the deck, from the authored sun.
 *
 * `sunRig().sunDir` keeps reporting the authored sun after it sets (the
 * sky needs it to paint twilight), and a light 20 deg under the ground
 * lights nothing: passed straight in, every night cloud is lit from
 * below through the earth. Same substitution `environment.js` makes for
 * its own key light — the moon rises opposite the sun, 30-55 deg up,
 * higher the deeper the sun. No argument keeps the old top-lit look.
 */
function lightVector(v) {
  const d = new THREE.Vector3(...vector(v,[0,1,0],3,'makeClouds: sunDir'));
  if(d.lengthSq()<1e-12) throw new RangeError('makeClouds: sunDir must be nonzero');
  d.normalize();
  if (d.y >= -0.02) return d;
  const el = Math.asin(-d.y);                    // how deep the sun is
  const az = Math.atan2(-d.z, -d.x) * 180 / Math.PI;   // opposite azimuth
  return sunVector(az, Math.max(30, Math.min(55, 25 + el * 180 / Math.PI)));
}

/** Seeded white-RGB alpha map for lightweight custom cloud sprites. */
export function cloudTexture(seed = 7, size = 256) {
  seed=option(seed,7,'cloudTexture: seed');
  intOption(size,null,'cloudTexture: size',16,1024);
  const field = new Float32Array(size * size);
  const rand = mulberry32(seed);
  const lobes = Array.from({length: 7}, (_,i) => ({
    x: (i / 6 - .5) * .58,
    y: -.03 + rand() * .17 - Math.abs(i / 6 - .5) * .18,
    radius: .15 + rand() * .12,
  }));
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const u = (x + .5) / size - .5, v = (y + .5) / size - .5;
    const broad = fbm2(u * 8 + 7, v * 8 + 3, {seed, octaves: 3});
    const fine = fbm2(u * 29 - 3, v * 29 + 9, {seed: seed + 17, octaves: 2});
    let density = -Infinity;
    for (const l of lobes) {
      const r = Math.hypot((u - l.x) * .96, (v - l.y) * 1.16) / l.radius;
      density = Math.max(density, 1 - r * r);
    }
    density = Math.max(0, density + broad * .18 + fine * .035);
    const base = THREE.MathUtils.smoothstep(v, -.36, -.26);
    const edge = THREE.MathUtils.smoothstep(density, 0, .12);
    const border = 1 - THREE.MathUtils.smoothstep(Math.max(Math.abs(u), Math.abs(v)), .43, .49);
    field[y * size + x] = Math.sqrt(density) * base * edge * border;

  }
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const h = field[y * size + x];
    const k=(y*size+x)*4;
    data[k]=data[k+1]=data[k+2]=255;
    data[k+3] = (1-Math.exp(-h*2.0))*255;
  }
  const tex = new THREE.DataTexture(data, size, size);
  tex.needsUpdate = true;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}

const QUALITY = {
  low: { view: 40, light: 3 }, balanced: { view: 64, light: 5 },
  high: { view: 96, light: 8 },
};

// One weather footprint for the entire deck. Overlapping masses form a
// continuous medium instead of independently blended/sorted sprite layers.
function weatherTexture(seed, count, area, stretch, cirrus) {
  const size = 192, data = new Uint8Array(size * size * 4), random = mulberry32(seed);
  const weights=new Float32Array(size*size), tops=new Float32Array(size*size), bases=new Float32Array(size*size);
  const banks = Array.from({length: count}, () => ({
    x: .12 + random() * .76, z: .12 + random() * .76,
    rx: (140 + random() * 220) / area * Math.sqrt(stretch),
    rz: (100 + random() * 160) / area / Math.sqrt(stretch),
    angle: (random() - .5) * (cirrus ? .35 : 2.4),
    top: .40 + random() * .58, base: random(),
  }));
  if(!cirrus) {
    // Unequal connected convection towers break the one-dome-per-bank
    // silhouette. A separate random stream preserves authored bank centres.
    const lobes=mulberry32(seed+1973),parents=[...banks];
    for(const bank of parents) {
      for(let i=0;i<5;i++) {
        const angle=lobes()*Math.PI*2, reach=.45+lobes()*.4;
        banks.push({...bank,x:bank.x+Math.cos(angle)*bank.rx*reach,
          z:bank.z+Math.sin(angle)*bank.rz*reach,
          rx:bank.rx*(.35+lobes()*.3),rz:bank.rz*(.35+lobes()*.3),
          top:.45+lobes()*.55,base:Math.max(0,Math.min(1,bank.base+(lobes()-.5)*.35))});
      }
      bank.top*=.72;
    }
  }
  for (const b of banks) {
    const radius = Math.max(b.rx, b.rz);
    const loX = Math.max(0, Math.floor((b.x-radius) * size));
    const hiX = Math.min(size-1, Math.ceil((b.x+radius) * size));
    const loZ = Math.max(0, Math.floor((b.z-radius) * size));
    const hiZ = Math.min(size-1, Math.ceil((b.z+radius) * size));
    const c = Math.cos(b.angle), s = Math.sin(b.angle);
    for (let z=loZ; z<=hiZ; z++) for (let x=loX; x<=hiX; x++) {
      const dx=(x+.5)/size-b.x, dz=(z+.5)/size-b.z;
      const r=Math.hypot((c*dx+s*dz)/b.rx,(-s*dx+c*dz)/b.rz);
      const coverage=Math.max(0,1-r*r), at=z*size+x, i=at*4, weight=coverage*coverage;
      data[i]=Math.max(data[i],Math.round(coverage*255));
      weights[at]+=weight;tops[at]+=weight*b.top;bases[at]+=weight*b.base;
      data[i+3]=255;
    }
  }
  for(let i=0;i<weights.length;i++) if(weights[i]>0) {
    data[i*4+1]=Math.round(tops[i]/weights[i]*255);
    data[i*4+2]=Math.round(bases[i]/weights[i]*255);
  }
  const texture=new THREE.DataTexture(data,size,size);
  texture.minFilter=texture.magFilter=THREE.LinearFilter;
  texture.wrapS=texture.wrapT=THREE.RepeatWrapping; texture.needsUpdate=true;
  return texture;
}

function cloudNoise(seed) {
  const size=40, random=mulberry32(seed+851), {values}=bakeFbm3(random,size,[4,8,16],2);
  // Periodic cellular billows supply rounded convection cells; fBm alone
  // erodes a smooth dome into fuzzy smoke with no internal cloud structure.
  const cells=4, features=Float32Array.from({length:cells**3*3},()=>random());
  const wrap=i=>((i%cells)+cells)%cells;
  const data=new Uint8Array(size**3*4);
  for(let i=0;i<size**3;i++) {
    const q=[(i%size+.5)/size*cells,(Math.floor(i/size)%size+.5)/size*cells,
      (Math.floor(i/(size*size))+.5)/size*cells];
    const base=q.map(Math.floor);let nearest=4;
    for(let z=-1;z<=1;z++) for(let y=-1;y<=1;y++) for(let x=-1;x<=1;x++) {
      const cell=[base[0]+x,base[1]+y,base[2]+z];
      const k=((wrap(cell[2])*cells+wrap(cell[1]))*cells+wrap(cell[0]))*3;
      const dx=cell[0]+features[k]-q[0],dy=cell[1]+features[k+1]-q[1],dz=cell[2]+features[k+2]-q[2];
      nearest=Math.min(nearest,dx*dx+dy*dy+dz*dz);
    }
    data[i*4]=Math.round(values[i*2]*255);
    data[i*4+1]=Math.round(values[i*2+1]*255);
    data[i*4+2]=Math.round(Math.max(0,1-Math.sqrt(nearest)/1.2)*255);data[i*4+3]=255;
  }
  return dataTexture3D(data,size);
}

const DECK_HEAD = /* glsl */`
precision highp sampler3D;
uniform sampler2D uWeather;
uniform sampler3D uNoise;
uniform vec3 uSize, uCenter, uCameraLocal, uSunLocal, uSunDir;
uniform vec3 uSunLight, uSkyLight, uFogColor, uViewLocal, uViewWorld;
uniform vec2 uDrift;
uniform mat3 uLocalToWorld;
uniform mat4 uLocalToClip;
uniform float uAlpha, uExtinction, uRim, uHaze, uSpread, uCirrus, uHue, uOrthographic;
varying vec3 vLocal;

float deckDensity(vec3 position) {
  vec3 uv=(position-uCenter)/uSize+.5;
  if(any(lessThan(uv,vec3(0)))||any(greaterThan(uv,vec3(1)))) return 0.0;
  vec3 p=position-uCenter-vec3(uDrift.x,0,uDrift.y)*uTime;
  vec2 broad=texture(uNoise,p/680.0).rg;
  vec2 weatherUV=uv.xz-uDrift*uTime/uSize.xz+(broad-.5)*.045;
  vec3 weather=texture2D(uWeather,weatherUV).rgb;
  float bottom=weather.b*uSpread+(broad.g-.5)*.045;
  float h=(uv.y-bottom)/max(.12,.30+weather.g*.64-bottom);
  if(h<=0.0||h>=1.0||weather.r<.015) return 0.0;
  vec3 q=p/mix(175.0,310.0,uCirrus);
  q.x*=mix(1.0,.28,uCirrus); q.z*=mix(1.0,2.7,uCirrus);
  vec3 noise=texture(uNoise,q).rgb;
  float fine=texture(uNoise,q*3.17+vec3(.17,.31,.73)).g;
  float body=weather.r-pow(max(h-.12,0.0),1.6)*.75;
  float erosion=(1.0-noise.r)*.28+(1.0-noise.b)*.36+(1.0-fine)*.14;
  float base=smoothstep(0.0,.09,h);
  float top=1.0-smoothstep(.80,1.0,h);
  float edge=smoothstep(0.0,.035,uv.x)*(1.0-smoothstep(.965,1.0,uv.x))
      *smoothstep(0.0,.035,uv.z)*(1.0-smoothstep(.965,1.0,uv.z));
  float billow=mix(.35,1.0,noise.b)*(.7+.3*noise.g);
  return smoothstep(.015,.24,body-erosion)*billow*base*top*edge;
}

float deckShadow(vec3 p) {
  float end=max(0.0,astraRayBox(p,uSunLocal,uCenter-uSize*.5,uCenter+uSize*.5).y);
  // Short near-surface intervals retain detailed silver edges; longer
  // intervals sample the rest of the cloud rather than repeating local shade.
  float opticalDepth=0.0;
  float metric=length(uLocalToWorld*uSunLocal);
  for(int i=0;i<DECK_LIGHT_STEPS;i++) {
    float a=float(i)/float(DECK_LIGHT_STEPS),b=float(i+1)/float(DECK_LIGHT_STEPS);
    float start=a*a*end,stop=b*b*end;
    opticalDepth+=deckDensity(p+uSunLocal*(start+stop)*.5)*(stop-start)*metric*uExtinction;
  }
  return opticalDepth;
}

float deckPhase(float cosine,float g) {
  return (1.0-g*g)/pow(max(.025,1.0+g*g-2.0*g*cosine),1.5);
}
`;

/**
 * One ray-marched 3D cloud deck, with connected density, optical extinction,
 * self-shadowing and forward scattering. Seed/count/area/altitude/spread keep
 * their authored placement roles; individual billboard puffs are no longer used.
 * Defaults: area 2600 m, altitude 260 m, spread 120 m, count 14. `alpha` scales
 * optical depth (zero is off); `quality` low/balanced/high changes integration
 * steps only. `wind` accepts the shared world-XZ wind forms. `sunDir` is a world
 * vector toward the light; a below-horizon sun selects the corresponding moon.
 * Preset and sunColor/shadeColor tint the actual scene key/fill illumination.
 * `litGain`, `rim`, `haze`, `stretch`, `hueVariance` remain optional controls.
 *
 * update(t) accepts absolute seconds. The returned Mesh owns its two textures
 * and supports idempotent disposal. Parent transforms affect the authored deck.
 * This is a distant atmospheric layer: it does not cast terrain shadows or
 * truncate integration at geometry embedded inside its density. Opaque geometry
 * entirely in front/behind is tested against the first participating sample.
 * Log-depth hosts retain proxy depth. Cost is higher than the former sprites;
 * use quality:'low' for previews, 'high' for large cloud close-ups.
 */
export function makeClouds(opts = {}) {
  const p=PRESETS[opts.preset||'day']||PRESETS.day, cirrus=opts._cirrus===true;
  const seed=option(opts.seed,7,'makeClouds: seed');
  const count=intOption(opts.count,p.count??14,'makeClouds: count',0,1000);
  const area=positive(opts.area,2600,'makeClouds: area');
  const altitude=option(opts.altitude,p.altitude??260,'makeClouds: altitude');
  const spread=option(opts.spread,p.spread??120,'makeClouds: spread',0);
  const stretch=positive(opts.stretch,p.stretch??1,'makeClouds: stretch');
  const alpha=option(opts.alpha,p.alpha??.92,'makeClouds: alpha',0,1);
  const gain=option(opts.litGain,p.litGain,'makeClouds: litGain',0);
  const rim=option(opts.rim,.55,'makeClouds: rim',0);
  const haze=option(opts.haze,.22,'makeClouds: haze',0,1);
  const hue=option(opts.hueVariance,.13,'makeClouds: hueVariance',0,1);
  const wind=readWind(opts.wind,3,'makeClouds wind');
  const quality=opts.quality??'balanced';
  if(!Object.hasOwn(QUALITY,quality)) throw new RangeError('makeClouds: quality must be low, balanced or high');
  const tier=QUALITY[quality];
  const thickness=cirrus?Math.max(45,spread*.55):Math.max(140,spread+170);
  const size=new THREE.Vector3(area,thickness,area);
  const center=new THREE.Vector3(0,altitude+thickness*.2,-area*.18);
  const sun=lightVector(opts.sunDir), sunTint=new THREE.Color(opts.sunColor??p.sunColor);
  const skyTint=new THREE.Color(opts.shadeColor??p.shadeColor);
  const weather=weatherTexture(seed,count,area,stretch,cirrus), noise=cloudNoise(seed);
  const material=makeShaderMaterial({
    name:opts.name||'CloudDeckMaterial',transparent:true,depthWrite:false,
    side:THREE.BackSide,fog:false,
    defines:{DECK_STEPS:tier.view,DECK_LIGHT_STEPS:tier.light},
    uniforms:{
      uWeather:{value:weather},uNoise:{value:noise},uSize:{value:size},uCenter:{value:center},
      uCameraLocal:{value:new THREE.Vector3()},uSunLocal:{value:sun.clone()},uSunDir:{value:sun},
      uSunLight:{value:new THREE.Color()},uSkyLight:{value:new THREE.Color()},
      uFogColor:{value:new THREE.Color()},uDrift:{value:new THREE.Vector2(wind.x,wind.z)},
      uViewLocal:{value:new THREE.Vector3()},uViewWorld:{value:new THREE.Vector3()},
      uOrthographic:{value:0},
      uWind:{value:wind.strength},uAlpha:{value:alpha},uRim:{value:rim},uHaze:{value:haze},
      uExtinction:{value:(cirrus?.006:.035)*alpha},uSpread:{value:Math.min(.28,spread/thickness*.4)},
      uCirrus:{value:cirrus?1:0},uHue:{value:hue},
      uLocalToWorld:{value:new THREE.Matrix3()},uLocalToClip:{value:new THREE.Matrix4()},
    },
    varyings:'varying vec3 vLocal;',vertexMain:'vLocal=position;',fragmentHead:DECK_HEAD,
    fragmentMain: /* glsl */`
      if(uAlpha<=0.0) discard;
      vec3 direction=uOrthographic>.5?uViewLocal:normalize(vLocal-uCameraLocal);
      float metric=length(uLocalToWorld*direction);
      vec3 origin=uCameraLocal;
      if(uOrthographic>.5) origin=vLocal-direction*
          (dot(uLocalToWorld*(vLocal-uCameraLocal),uViewWorld)/max(metric,1e-8));
      vec2 hit=astraRayBox(origin,direction,uCenter-uSize*.5,uCenter+uSize*.5);
      float start=max(0.0,hit.x),end=hit.y;
      if(end<=start) discard;
      float stepSize=(end-start)/float(DECK_STEPS);
      vec3 worldDirection=normalize(uLocalToWorld*direction);
      float cosine=dot(worldDirection,uSunDir);
      float phase=min(3.5,.85*deckPhase(cosine,.62)+.15*deckPhase(cosine,-.22));
      float jitter=.15+.7*astraHash21(gl_FragCoord.xy);
      float transmittance=1.0;
      vec3 radiance=vec3(0),first=origin+direction*end;
      bool found=false;
      for(int i=0;i<DECK_STEPS;i++) {
        vec3 point=origin+direction*(start+(float(i)+jitter)*stepSize);
        float density=deckDensity(point);
        if(density>.001) {
          if(!found){first=point;found=true;}
          float tau=deckShadow(point);
          float direct=exp(-tau);
          float multiple=.30*exp(-tau*.25)+.10*exp(-tau*.065);
          float height=clamp((point.y-uCenter.y)/uSize.y+.5,0.0,1.0);
          vec3 source=uSunLight*(direct*(.30+phase*uRim*.5)+multiple)
              +uSkyLight*(.45+.55*height);
          float tint=sin(dot(point,vec3(.006,.011,.009)))*uHue*.08;
          source*=vec3(1.0+tint,1.0,1.0-tint);
          float opacity=1.0-exp(-density*uExtinction*stepSize*metric);
          radiance+=transmittance*opacity*source;
          transmittance*=1.0-opacity;
          if(transmittance<.008) break;
        }
      }
      float opacity=1.0-transmittance;
      if(opacity<.003) discard;
      vec3 color=radiance/max(opacity,.001);
      float distance=length(uLocalToWorld*(first-uCameraLocal));
      color=mix(color,uFogColor,uHaze*(1.0-exp(-distance/3500.0)));
      #ifndef USE_LOGDEPTHBUF
        gl_FragDepth=astraClipDepth(uLocalToClip,first);
      #endif
      gl_FragColor=vec4(color,opacity);
    `,
  });
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(...size.toArray()).translate(...center.toArray()),material);
  mesh.name=opts.name||'CumulusLayer';
  mesh.visible=count>0;
  mesh.userData.sceneBackdrop='sky';
  keepOutOfDepthPasses(mesh);
  const before=mesh.onBeforeRender,readLights=makeLightProbe(),inverse=new THREE.Matrix4();
  const drift=new THREE.Vector3(wind.x,0,wind.z),localDrift=new THREE.Vector3();
  const inverseLinear=new THREE.Matrix3();
  mesh.onBeforeRender=(...args)=>{
    before.apply(mesh,args);
    const scene=args[1],camera=args[2],u=material.uniforms;
    const lights=readLights(scene);
    if(opts.sunDir==null && lights.sun) sun.copy(lights.sunDirection);
    u.uSunLight.value.setRGB(0,0,0);
    if(lights.sun) u.uSunLight.value.copy(sunTint).multiply(lights.sun.color)
      .multiplyScalar(gain*lights.sun.intensity/(opts.preset==='night'?2.2:5.4));
    const fill=c=>lights.ambient[c]+lights.sky[c]*.8+lights.ground[c]*.2+lights.environment*.12;
    u.uSkyLight.value.setRGB(fill('r'),fill('g'),fill('b')).multiply(skyTint);
    u.uFogColor.value.copy(scene.fog?.color||u.uSkyLight.value);
    inverse.copy(mesh.matrixWorld).invert();
    camera.getWorldPosition(u.uCameraLocal.value).applyMatrix4(inverse);
    camera.getWorldDirection(u.uViewWorld.value);
    u.uViewLocal.value.copy(u.uViewWorld.value).transformDirection(inverse);
    u.uOrthographic.value=camera.isOrthographicCamera?1:0;
    u.uSunLocal.value.copy(sun).transformDirection(inverse);
    u.uLocalToWorld.value.setFromMatrix4(mesh.matrixWorld);
    inverseLinear.setFromMatrix4(inverse);
    localDrift.copy(drift).applyMatrix3(inverseLinear);u.uDrift.value.set(localDrift.x,localDrift.z);
    u.uLocalToClip.value.copy(camera.projectionMatrix).multiply(camera.matrixWorldInverse).multiply(mesh.matrixWorld);
  };
  mesh.userData.update=t=>{
    if(!Number.isFinite(t)) throw new RangeError('CloudLayer.update: time must be finite');
    material.uniforms.uTime.value=t;
  };
  mesh.userData.quality=quality;
  mesh.userData.steps={...tier};
  const owned=snapshotResources(mesh);owned.add(weather);owned.add(noise);
  return attachDisposal(mesh,owned);
}

/** Thin, wind-stretched high-altitude ice-cloud deck; same options as makeClouds. */
export function makeCirrus(opts = {}) {
  return makeClouds({preset:'day',...opts,_cirrus:true,name:opts.name??'CirrusLayer',
    seed:opts.seed??23,count:opts.count??8,area:opts.area??3200,
    altitude:opts.altitude??520,spread:opts.spread??100,alpha:opts.alpha??.44,
    wind:opts.wind??6,stretch:opts.stretch??4.5,rim:opts.rim??.85});
}
