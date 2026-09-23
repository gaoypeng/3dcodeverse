/**
 * Rising smoke and condensation plumes: a bounded 3D density field advected
 * upward, widened by entrainment, bent by wind and illuminated through itself.
 * Absolute time and a seeded noise texture make scrubbing deterministic.
 *
 * This is an authored transport model, not a fluid simulation. It has no
 * collision response, scene-shadow reception or terrain shadow casting. Keep
 * dense opaque intersections out of the visible plume: its first contributing
 * density supplies fragment depth, so empty proxy space cannot erase visible
 * front wisps. This does not truncate integration at opaque scene depth; an
 * object cutting through actual density still needs a scene-depth pass.
 * The front-density depth correction requires standard, non-logarithmic depth.
 * Overlapping transparent objects use ordinary Three.js object sorting.
 */
import * as THREE from 'three';
import { makeShaderMaterial, keepOutOfDepthPasses } from './shader.js';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { bakeFbm3, dataTexture3D, mulberry32, sampleGrid3 } from './noise.js';

const RESOLUTION = 48;
const TIERS = { low: [36, 4], balanced: [64, 6], high: [100, 10] };
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };

function noiseTexture(seed) {
  const { values } = bakeFbm3(mulberry32(seed), RESOLUTION, [4, 8, 16, 32], 3);
  const data = new Uint8Array(RESOLUTION ** 3 * 4).fill(255);
  for (let i = 0; i < values.length; i++) data[i + Math.floor(i / 3)] = Math.round(values[i] * 255);
  return dataTexture3D(data, RESOLUTION);
}

const HEAD = /* glsl */`
precision highp sampler3D;
uniform sampler3D uPlumeNoise;
uniform vec3 uBoundsMin;
uniform vec3 uBoundsMax;
uniform vec3 uCameraLocal;
uniform mat3 uLocalToWorld;
uniform mat4 uLocalToClip;
uniform vec3 uSunLocal;
uniform vec3 uSunWorld;
uniform vec3 uSunColor;
uniform vec3 uAmbient;
uniform vec3 uPointLocal;
uniform vec3 uPointColor;
uniform vec2 uPointFalloff;
uniform vec3 uAlbedo;
uniform vec2 uWind;
uniform float uHeight;
uniform float uRadius;
uniform float uSpread;
uniform float uRise;
uniform float uTurbulence;
uniform float uDissipation;
uniform float uExtinction;
uniform float uAnisotropy;
uniform float uTime;
varying vec3 vPlumeLocal;

vec2 plumeBox(vec3 origin, vec3 direction) {
  vec3 safe = sign(direction) * max(abs(direction), vec3(1e-7));
  safe += vec3(equal(direction, vec3(0.0))) * 1e-7;
  vec3 a = (uBoundsMin - origin) / safe, b = (uBoundsMax - origin) / safe;
  vec3 lo = min(a,b), hi = max(a,b);
  return vec2(max(lo.x,max(lo.y,lo.z)), min(hi.x,min(hi.y,hi.z)));
}
float plumeDensity(vec3 p) {
  float y = p.y;
  if (y <= 0.0 || y >= uHeight) return 0.0;
  float h = y / uHeight, age = y / uRise;
  float phase = (y - uTime * uRise) / uHeight;
  vec2 drift = uWind * age + uTurbulence * y * .13 *
      vec2(sin(phase*8.1), cos(phase*6.7+1.4));
  vec3 q = vec3(p.x-drift.x, y-uTime*uRise, p.z-drift.y) / uRadius;
  vec3 warp = texture(uPlumeNoise,q*.083).rgb - .5;
  float coarse = texture(uPlumeNoise,q*.115+warp*.34).r;
  float fine = texture(uPlumeNoise,q*.49+warp*.21+vec3(.17,.39,.61)).g;
  float radius = uRadius + uSpread * y;
  float irregular = (coarse-.5)*3.8*uTurbulence*smoothstep(.02,.18,h);
  float radial = length(p.xz-drift) / radius;
  float body = max(0.0,1.0-radial+irregular);
  float density = pow(max(0.0,body*.85-(1.0-fine)*.30*uTurbulence),1.2);
  return min(density,1.0) * smoothstep(0.0,.018,h) *
      (1.0-smoothstep(.65,1.0,h)) * exp(-uDissipation*h);
}
float plumePhase(float cosine) {
  float g = uAnisotropy, gg = g*g;
  return (1.0-gg) / (12.5663706144 * pow(max(.001,1.0+gg-2.0*g*cosine),1.5));
}
float plumeTransmission(vec3 p, vec3 direction, float distance) {
  float end = min(max(0.0,plumeBox(p,direction).y),distance);
  float metric = length(uLocalToWorld*direction);
  float tau = 0.0;
  for(int j=0;j<PLUME_LIGHT_STEPS;j++) {
    float a=float(j)/float(PLUME_LIGHT_STEPS), b=float(j+1)/float(PLUME_LIGHT_STEPS);
    float start=a*a*end, stop=b*b*end;
    tau += plumeDensity(p+direction*(start+stop)*.5)*(stop-start)*metric*uExtinction;
  }
  return exp(-tau);
}
`;

/**
 * makeSmoke({height:3,radius:.14,spread:.20,riseSpeed:.7,wind:[.06,0],
 *   density:5/height,turbulence:1,dissipation:.7,color:0xb0b5bc,
 *   anisotropy:.35,quality:'balanced',seed:21,name:'Smoke'}) -> Mesh.
 * Distances are local metres; wind/riseSpeed are local metres per second.
 * density is optical extinction per WORLD metre. The origin is the emitter
 * at local y=0. Geometry bounds include wind displacement and turbulence.
 *
 * Lighting follows the strongest visible directional and point light, plus
 * an average ambient/hemisphere/environment contribution. Local light shadow
 * marches only include this plume. color is scattering albedo (linear Color
 * or sRGB hex). update(t) uses absolute seconds; sampleDensity(x,y,z,t) queries
 * the same seeded field; dispose() releases only construction-owned resources.
 */
export function makeSmoke(opts = {}) {
  return makePlume(opts, false);
}

/** Condensation preset: lighter scattering, faster upward motion and dilution. */
export function makeSteam(opts = {}) {
  return makePlume(opts, true);
}

function makePlume(opts, steam) {
  const height = opts.height ?? (steam ? .7 : 3);
  const radius = opts.radius ?? height * (steam ? .09 : .047);
  const spread = opts.spread ?? (steam ? .16 : .20);
  const rise = opts.riseSpeed ?? height * (steam ? .38 : .2333333);
  const wind = opts.wind?.toArray ? opts.wind.toArray() : opts.wind ?? [height * .02, 0];
  const extinction = opts.density ?? (steam ? 2.1 : 5) / height;
  const turbulence = opts.turbulence ?? (steam ? .9 : 1);
  const dissipation = opts.dissipation ?? (steam ? 1.2 : .7);
  const anisotropy = opts.anisotropy ?? (steam ? .62 : .35);
  const quality = opts.quality ?? 'balanced', seed = opts.seed ?? 21;
  if (![height,radius,spread,rise,extinction,turbulence,dissipation,anisotropy].every(Number.isFinite) ||
      height < .001 || height > 10000 || radius <= 0 || radius > height * 4 || spread < 0 || spread > 2 ||
      rise <= 0 || extinction < 0 || turbulence < 0 || turbulence > 2 || dissipation < 0 ||
      Math.abs(anisotropy) > .9 || !Array.isArray(wind) || wind.length !== 2 || !wind.every(Number.isFinite) ||
      !Number.isSafeInteger(seed) || !Object.hasOwn(TIERS,quality) || Math.hypot(...wind) / rise > 10) {
    throw new RangeError('makeSmoke/makeSteam: invalid dimensions, flow, density, scattering, seed or quality');
  }
  const texture = noiseTexture(seed), [steps, lightSteps] = TIERS[quality];
  const maxRadius = (radius + spread * height) * (1 + turbulence * 1.9) + turbulence * height * .13;
  const drift = wind.map(v => v * height / rise);
  const boundsMin = new THREE.Vector3(Math.min(0,drift[0])-maxRadius,0,Math.min(0,drift[1])-maxRadius);
  const boundsMax = new THREE.Vector3(Math.max(0,drift[0])+maxRadius,height,Math.max(0,drift[1])+maxRadius);
  const material = makeShaderMaterial({
    name: steam ? 'SteamVolumeMaterial' : 'SmokeVolumeMaterial', transparent: true,
    depthWrite: false, side: THREE.BackSide, fog: false,
    defines: { PLUME_STEPS: steps, PLUME_LIGHT_STEPS: lightSteps },
    uniforms: {
      uPlumeNoise: { value: texture }, uBoundsMin: { value: boundsMin }, uBoundsMax: { value: boundsMax },
      uCameraLocal: { value: new THREE.Vector3() }, uLocalToWorld: { value: new THREE.Matrix3() },
      uLocalToClip: { value: new THREE.Matrix4() },
      uSunLocal: { value: new THREE.Vector3(0,1,0) }, uSunWorld: { value: new THREE.Vector3(0,1,0) },
      uSunColor: { value: new THREE.Color(0) }, uAmbient: { value: new THREE.Color(0) },
      uPointLocal: { value: new THREE.Vector3() }, uPointColor: { value: new THREE.Color(0) },
      uPointFalloff: { value: new THREE.Vector2() },
      uAlbedo: { value: new THREE.Color(opts.color ?? (steam ? 0xf8faff : 0xb0b5bc)) },
      uWind: { value: new THREE.Vector2(...wind) }, uHeight: { value: height }, uRadius: { value: radius },
      uSpread: { value: spread }, uRise: { value: rise }, uExtinction: { value: extinction },
      uTurbulence: { value: turbulence }, uDissipation: { value: dissipation }, uAnisotropy: { value: anisotropy },
      uTime: { value: 0 },
    },
    varyings: 'varying vec3 vPlumeLocal;', vertexMain: 'vPlumeLocal=position;', fragmentHead: HEAD,
    fragmentMain: /* glsl */`
      vec3 direction=normalize(vPlumeLocal-uCameraLocal);
      vec2 bounds=plumeBox(uCameraLocal,direction);
      float start=max(0.0,bounds.x), end=bounds.y;
      if(end<=start || uExtinction<=0.0) discard;
      float stepLength=(end-start)/float(PLUME_STEPS);
      float metric=length(uLocalToWorld*direction);
      vec3 worldDirection=normalize(uLocalToWorld*direction);
      float sunPhase=plumePhase(dot(worldDirection,uSunWorld));
      float jitter=.15+.7*astraHash21(gl_FragCoord.xy);
      float transmittance=1.0;
      vec3 radiance=vec3(0.0);
      vec3 firstDensity=uCameraLocal+direction*end;
      bool densityHit=false;
      for(int i=0;i<PLUME_STEPS;i++) {
        vec3 p=uCameraLocal+direction*(start+(float(i)+jitter)*stepLength);
        float density=plumeDensity(p);
        if(density>.001) {
          if(!densityHit){firstDensity=p;densityHit=true;}
          float sunT=plumeTransmission(p,uSunLocal,1e6);
          vec3 lighting=uAmbient*(.5+.5*exp(-density*uExtinction*uRadius)) + uSunColor*sunPhase*sunT;
          if(max(uPointColor.r,max(uPointColor.g,uPointColor.b))>0.0) {
            vec3 delta=uPointLocal-p;
            float localDistance=max(length(delta),1e-5);
            vec3 pointDirection=delta/localDistance;
            vec3 worldDelta=uLocalToWorld*delta;
            float distance=max(length(worldDelta),.01);
            float falloff=1.0/max(pow(distance,uPointFalloff.y),.01);
            if(uPointFalloff.x>0.0) falloff*=pow(clamp(1.0-pow(distance/uPointFalloff.x,4.0),0.0,1.0),2.0);
            lighting+=uPointColor*falloff*plumePhase(dot(worldDirection,normalize(worldDelta)))*
                plumeTransmission(p,pointDirection,localDistance);
          }
          float alpha=1.0-exp(-density*uExtinction*stepLength*metric);
          radiance+=transmittance*alpha*lighting*uAlbedo;
          transmittance*=1.0-alpha;
          if(transmittance<.01) break;
        }
      }
      float alpha=1.0-transmittance;
      if(alpha<.002) discard;
      // The exit face is only a ray proxy, often behind nearby opaque objects.
      // Depth-test the visible field instead of discarding its entire ray.
      #ifndef USE_LOGDEPTHBUF
        vec4 densityClip=uLocalToClip*vec4(firstDensity,1.0);
        gl_FragDepth=clamp(densityClip.z/densityClip.w*.5+.5,0.0,1.0);
      #endif
      gl_FragColor=vec4(radiance/max(alpha,.001),alpha);
    `,
  });
  const size = boundsMax.clone().sub(boundsMin), centre = boundsMin.clone().add(boundsMax).multiplyScalar(.5);
  const geometry = new THREE.BoxGeometry(size.x,size.y,size.z).translate(centre.x,centre.y,centre.z);
  const mesh = new THREE.Mesh(geometry,material);
  mesh.name = opts.name ?? (steam ? 'Steam' : 'Smoke');
  keepOutOfDepthPasses(mesh);
  const before = mesh.onBeforeRender, inverse = new THREE.Matrix4();
  const position = new THREE.Vector3(), target = new THREE.Vector3(), lightPosition = new THREE.Vector3();
  const u = material.uniforms;
  mesh.onBeforeRender = (...args) => {
    before.apply(mesh,args);
    const [,scene,camera] = args;
    inverse.copy(mesh.matrixWorld).invert();
    camera.getWorldPosition(u.uCameraLocal.value).applyMatrix4(inverse);
    u.uLocalToWorld.value.setFromMatrix4(mesh.matrixWorld);
    u.uLocalToClip.value.copy(camera.projectionMatrix).multiply(camera.matrixWorldInverse).multiply(mesh.matrixWorld);
    mesh.getWorldPosition(position);
    let sun = null, point = null, best = -1;
    u.uAmbient.value.setRGB(0,0,0);u.uSunColor.value.setRGB(0,0,0);u.uPointColor.value.setRGB(0,0,0);
    scene.traverseVisible(light => {
      if (!light.isLight || !(light.intensity > 0)) return;
      if (light.isDirectionalLight && (!sun || light.intensity > sun.intensity)) sun = light;
      if (light.isPointLight) {
        light.getWorldPosition(lightPosition);
        const score = light.intensity / Math.max(.01,lightPosition.distanceToSquared(position));
        if (score > best) { point = light;best = score; }
      }
      const amount = light.isAmbientLight ? light.intensity * .28 : light.isHemisphereLight ? light.intensity * .22 : 0;
      u.uAmbient.value.r += light.color.r * amount;
      u.uAmbient.value.g += light.color.g * amount;
      u.uAmbient.value.b += light.color.b * amount;
    });
    if (scene.environment) {
      const level = .12 * (scene.environmentIntensity ?? 1);
      u.uAmbient.value.r += level;u.uAmbient.value.g += level;u.uAmbient.value.b += level;
    }
    if (sun) {
      sun.getWorldPosition(lightPosition);sun.target.getWorldPosition(target);
      u.uSunWorld.value.copy(lightPosition).sub(target).normalize();
      u.uSunLocal.value.copy(u.uSunWorld.value).transformDirection(inverse);
      u.uSunColor.value.copy(sun.color).multiplyScalar(sun.intensity);
    }
    if (point) {
      point.getWorldPosition(u.uPointLocal.value).applyMatrix4(inverse);
      u.uPointColor.value.copy(point.color).multiplyScalar(point.intensity);
      u.uPointFalloff.value.set(point.distance,point.decay);
    }
  };
  mesh.userData.update = t => {
    if (!Number.isFinite(t)) throw new RangeError('Smoke.update: time must be finite');
    u.uTime.value = t;
  };
  const noise = (x,y,z,c) => sampleGrid3(texture.image.data,RESOLUTION,
    x*RESOLUTION-.5,y*RESOLUTION-.5,z*RESOLUTION-.5,4,c)/255;
  mesh.userData.sampleDensity = (x,y,z,t = u.uTime.value) => {
    if (![x,y,z,t].every(Number.isFinite)) throw new RangeError('Smoke.sampleDensity: coordinates/time must be finite');
    if (y <= 0 || y >= height) return 0;
    const h = y/height, phase = (y-t*rise)/height;
    const dx = wind[0]*y/rise+turbulence*y*.13*Math.sin(phase*8.1);
    const dz = wind[1]*y/rise+turbulence*y*.13*Math.cos(phase*6.7+1.4);
    const q = [(x-dx)/radius,(y-t*rise)/radius,(z-dz)/radius];
    const warp = [0,1,2].map(c => noise(...q.map(v => v*.083),c)-.5);
    const coarse = noise(...q.map((v,i) => v*.115+warp[i]*.34),0);
    const fine = noise(...q.map((v,i) => v*.49+warp[i]*.21+[.17,.39,.61][i]),1);
    const irregular = (coarse-.5)*3.8*turbulence*smooth(.02,.18,h);
    const body = Math.max(0,1-Math.hypot(x-dx,z-dz)/(radius+spread*y)+irregular);
    const value = Math.pow(Math.max(0,body*.85-(1-fine)*.30*turbulence),1.2);
    return Math.min(value,1)*smooth(0,.018,h)*(1-smooth(.65,1,h))*Math.exp(-dissipation*h);
  };
  mesh.userData.bounds = new THREE.Box3(boundsMin.clone(),boundsMax.clone());
  mesh.userData.quality = quality;
  const owned = snapshotResources(mesh);owned.add(texture);
  return attachDisposal(mesh,owned);
}
