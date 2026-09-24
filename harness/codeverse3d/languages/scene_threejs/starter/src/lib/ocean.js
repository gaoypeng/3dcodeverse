/**
 * Displaced, reflective ocean. Metres, Y up, local XZ surface at mean y=0.
 * Thirty-two directional Gerstner bands use gravity-wave dispersion omega²=gk tanh(kd),
 * with analytic surface derivatives and compression-driven whitecaps. This is a
 * deterministic real-time approximation, NOT an FFT spectrum or fluid solver.
 * Planar reflections approximate the mean sea plane (one extra scene render).
 * One reflecting surface per scene; do not combine with Water/Reflector floors.
 * References: NVIDIA GPU Gems ch. 1 (directional Gerstner waves), and
 * Bruneton, Neyret & Holzschuch, Real-time Realistic Ocean Lighting (2010),
 * https://hal.science/inria-00443630 . The slope-variance
 * filtering here is a compact approximation, not their full anisotropic BRDF.
 * Equirectangular scene environments supply directional sky reflection; actual
 * nearby objects retain the mean-plane RTT reflection. Cube/PMREM environments
 * fall back to the reflection image. No screen-space refraction or fluid solve.
 */
import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { mulberry32 } from './noise.js';
import { GLSL_UTIL, makeLightProbe, planarCapture, readWind } from './shader.js';
import { Reflector } from 'three/addons/objects/Reflector.js';

const TAU = Math.PI * 2;
const COUNT = 32;
function finite(value, fallback, name, min, max = Infinity) {
  const v = value === undefined ? fallback : value;
  if (!Number.isFinite(v) || v < min || v > max)
    throw new RangeError(`makeOceanSurface: ${name} must be ${min}..${max}`);
  return v;
}
const waveGLSL = /* glsl */ `
uniform float seaTime;
uniform vec4 seaWaves[32];
uniform vec2 seaPhases[32];
uniform float seaChop;
uniform vec4 seaShore;
uniform float seaShoreWidth;
float shoreFactor(vec2 q) {
  float d = dot(q, seaShore.xy) - seaShore.z;
  return mix(1.0, smoothstep(-0.5, seaShoreWidth * 1.7, d), seaShore.w);
}
void seaSurface(vec2 q, float footprint, out vec3 p, out vec3 n,
                out float compression, out float unresolvedVariance) {
  p=vec3(q.x,0.0,q.y);
  vec3 dx=vec3(1.0,0.0,0.0), dz=vec3(0.0,0.0,1.0);
  float shoal=shoreFactor(q);
  vec2 shoalD=vec2(shoreFactor(q+vec2(.005,0.0))-shoreFactor(q-vec2(.005,0.0)),
    shoreFactor(q+vec2(0.0,.005))-shoreFactor(q-vec2(0.0,.005)))/.01;
  unresolvedVariance=0.0;
  for(int i=0;i<32;i++) {
    vec4 w=seaWaves[i];
    float phase=w.w*dot(w.xy,q)-seaPhases[i].x*seaTime+seaPhases[i].y;
    float sn=sin(phase),cs=cos(phase);
    // The normal field is integrated over the pixel footprint. Missing slopes
    // become BRDF roughness rather than vanishing or aliasing into bright lines.
    float resolved=exp(-.5*pow(w.w*footprint*.55,2.0));
    float amplitude=w.z*resolved;
    float a=amplitude*shoal,h=seaChop*amplitude;
    unresolvedVariance+=.5*pow(w.z*w.w*shoal,2.0)*(1.0-resolved*resolved);
    p+=vec3(h*shoal*w.x*cs,a*sn,h*shoal*w.y*cs);
    float cx=shoalD.x*cs-shoal*w.w*w.x*sn;
    float cz=shoalD.y*cs-shoal*w.w*w.y*sn;
    dx+=vec3(h*w.x*cx,amplitude*(shoalD.x*sn+shoal*w.w*w.x*cs),h*w.y*cx);
    dz+=vec3(h*w.x*cz,amplitude*(shoalD.y*sn+shoal*w.w*w.y*cs),h*w.y*cz);
  }
  n=normalize(cross(dz,dx));
  compression=1.0-(dx.x*dz.z-dx.z*dz.x);
}
`;
const vertex = /* glsl */ `
uniform mat4 textureMatrix;
varying vec4 seaMirror;
varying vec3 seaWorld;
varying vec2 seaRest;
#include <common>
#include <fog_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
${waveGLSL}
void main() {
  seaRest=vec2(position.x,-position.y);
  vec3 p,n; float compression,variance;
  seaSurface(seaRest,0.0,p,n,compression,variance);
  vec3 transformed=vec3(p.x,-p.z,p.y);
  vec3 objectNormal=vec3(n.x,-n.z,n.y);
  #include <defaultnormal_vertex>
  vec4 worldPosition=modelMatrix*vec4(transformed,1.0);
  seaWorld=worldPosition.xyz;
  seaMirror=textureMatrix*vec4(transformed,1.0);
  vec4 mvPosition=modelViewMatrix*vec4(transformed,1.0);
  gl_Position=projectionMatrix*mvPosition;
  #include <logdepthbuf_vertex>
  #include <fog_vertex>
  #include <shadowmap_vertex>
}
`;
const fragment = /* glsl */ `
uniform sampler2D tDiffuse;
uniform sampler2D seaReflectionDepth;
uniform sampler2D seaEnvironment;
uniform float seaEnvironmentEnabled;
uniform float seaEnvironmentIntensity;
uniform mat3 seaEnvironmentRotation;
uniform float seaCompressionRms;
uniform vec3 color;
uniform vec3 seaDeep;
uniform vec3 seaShallow;
uniform vec3 seaSun;
uniform vec3 seaSunColor;
uniform vec3 seaAmbient;
uniform vec3 seaKey;
uniform float seaFoam;
uniform float seaHeightScale;
uniform float seaReflectionSize;
uniform vec2 seaWind;
uniform mat3 seaRotation;
varying vec4 seaMirror;
varying vec3 seaWorld;
varying vec2 seaRest;
#include <common>
#include <packing>
#include <bsdfs>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <lights_pars_begin>
#include <shadowmap_pars_fragment>
#include <shadowmask_pars_fragment>
${waveGLSL}
${GLSL_UTIL}
vec3 seaNoiseGradient(vec2 p) {
  vec2 i=floor(p),f=fract(p),u=f*f*(3.0-2.0*f),du=6.0*f*(1.0-f);
  float a=astraHash21(i),b=astraHash21(i+vec2(1,0)),c=astraHash21(i+vec2(0,1)),d=astraHash21(i+vec2(1,1));
  return vec3(mix(mix(a,b,u.x),mix(c,d,u.x),u.y),
    mix(b-a,d-c,u.y)*du.x,mix(c-a,d-b,u.x)*du.y);
}
float seaFbm(vec2 q) {
  return astraNoise2(q)*.57+astraNoise2(q*2.13+7.2)*.28+astraNoise2(q*4.31)*.15;
}
// Dielectric water Fresnel uses air/water indices, not a tinted metal lobe.
float seaFresnel(float c) {
  c=clamp(c,0.0,1.0);
  float g=sqrt(1.333*1.333-1.0+c*c);
  float a=(g-c)/(g+c), b=(c*(g+c)-1.0)/(c*(g-c)+1.0);
  return .5*a*a*(1.0+b*b);
}
float seaGGX(float alpha,float nh,float nv,float nl) {
  float a2=alpha*alpha;
  float denominator=nh*nh*(a2-1.0)+1.0;
  float distribution=a2/(PI*denominator*denominator);
  float visibility=.5/max(nl*sqrt(nv*nv*(1.0-a2)+a2)
    +nv*sqrt(nl*nl*(1.0-a2)+a2),.0001);
  return distribution*visibility;
}
void main() {
  #include <logdepthbuf_fragment>
  vec2 q=seaRest;
  float footprint=max(length(dFdx(q)),length(dFdy(q)));
  vec3 p,localNormal; float compression,variance;
  seaSurface(q,footprint,p,localNormal,compression,variance);
  // Advected broadband wind-ripple slopes fill the sub-metre range without
  // the visible directional comb made by a handful of capillary sine waves.
  // Analytic noise gradients stay continuous and feed lost variance into GGX.
  vec2 slope=vec2(0.0);
  vec2 crossWind=vec2(-seaWind.y,seaWind.x);
  mat2 frame=mat2(seaWind,crossWind);
  vec2 aligned=vec2(dot(q,seaWind),dot(q,crossWind));
  for(int i=0;i<5;i++) {
    float fi=float(i),frequency=3.7*pow(2.07,fi);
    float slopeAmplitude=.095/(1.0+fi*.13);
    float resolved=exp(-.5*pow(frequency*footprint*.8,2.0));
    vec2 flow=vec2(seaTime*(.11+.031*fi),sin(fi*3.4)*seaTime*.026);
    vec3 field=seaNoiseGradient(aligned*vec2(1.0,1.35)*frequency-flow+seaPhases[i].y*7.3);
    slope+=frame*(field.yz*vec2(1.0,1.35))*slopeAmplitude*resolved;
    variance+=.15*slopeAmplitude*slopeAmplitude*(1.0-resolved*resolved);
  }
  localNormal=normalize(localNormal+vec3(-slope.x,0.0,-slope.y));
  vec3 n=normalize(seaRotation*localNormal);
  vec3 eyeDir=normalize(cameraPosition-seaWorld);
  float nv=max(dot(n,eyeDir),.001);
  float alpha=clamp(sqrt(.0025+variance),.05,.42);
  // Average Fresnel over the unresolved normal cone. This is a compact
  // quadrature, not the exact anisotropic ocean BRDF from Bruneton et al.
  float fresnel=seaFresnel(nv);
  fresnel=(fresnel*2.0+seaFresnel(clamp(nv+alpha,0.0,1.0))
    +seaFresnel(clamp(nv-alpha,0.0,1.0)))*.25;
  // Mip-filter the reflection by unresolved slope variance. A perfectly sharp
  // planar sample beside a rough sun lobe was the old polished-plastic cue.
  vec3 normalView=mat3(viewMatrix)*n;
  vec3 meanView=mat3(viewMatrix)*normalize(seaRotation*vec3(0,1,0));
  vec2 distortion=(normalView.xy-meanView.xy)*.075;
  vec2 reflectedUV=seaMirror.xy/seaMirror.w+distortion;
  float lod=clamp(log2(max(1.0,alpha*seaReflectionSize*.15)),0.0,9.0);
  vec3 reflected=texture2DLodEXT(tDiffuse,clamp(reflectedUV,vec2(.002),vec2(.998)),lod).rgb;
  vec3 diffuseSky=seaAmbient;
  if(seaEnvironmentEnabled>.5) {
    vec3 reflectionDirection=normalize(reflect(-eyeDir,n));
    reflectionDirection=seaEnvironmentRotation*reflectionDirection;
    float skyVisibility=smoothstep(-.16,.09,reflectionDirection.y);
    reflectionDirection.y=max(.008,reflectionDirection.y);
    reflectionDirection=normalize(reflectionDirection);
    vec3 tangent=normalize(cross(reflectionDirection,abs(reflectionDirection.y)<.99?vec3(0,1,0):vec3(1,0,0)));
    vec3 bitangent=cross(reflectionDirection,tangent);
    vec3 env=texture2D(seaEnvironment,equirectUv(reflectionDirection)).rgb*.4;
    env+=texture2D(seaEnvironment,equirectUv(normalize(reflectionDirection+tangent*alpha*1.8))).rgb*.15;
    env+=texture2D(seaEnvironment,equirectUv(normalize(reflectionDirection-tangent*alpha*1.8))).rgb*.15;
    env+=texture2D(seaEnvironment,equirectUv(normalize(reflectionDirection+bitangent*alpha*1.8))).rgb*.15;
    env+=texture2D(seaEnvironment,equirectUv(normalize(reflectionDirection-bitangent*alpha*1.8))).rgb*.15;
    // A downward reflected ray on a continuous sea meets another wave, not
    // the terrestrial ground hemisphere of the scene's environment map.
    // The second wave still reflects the bright horizon at a grazing angle.
    // Replacing it with dark body colour produces implausible black hatch lines.
    env=mix(env*.72+seaDeep*(seaAmbient+seaKey*.24)*.28,env,skyVisibility);
    // Five cosine-weighted upper-hemisphere samples estimate the broad sky
    // irradiance on diffuse whitecaps. A stylized fill light can be purple even
    // when the visible/environment sky is blue; foam should reflect that sky.
    diffuseSky=texture2D(seaEnvironment,equirectUv(seaEnvironmentRotation*vec3(0,1,0))).rgb*.25;
    diffuseSky+=texture2D(seaEnvironment,equirectUv(seaEnvironmentRotation*vec3(.8165,.57735,0))).rgb*.1875;
    diffuseSky+=texture2D(seaEnvironment,equirectUv(seaEnvironmentRotation*vec3(-.8165,.57735,0))).rgb*.1875;
    diffuseSky+=texture2D(seaEnvironment,equirectUv(seaEnvironmentRotation*vec3(0,.57735,.8165))).rgb*.1875;
    diffuseSky+=texture2D(seaEnvironment,equirectUv(seaEnvironmentRotation*vec3(0,.57735,-.8165))).rgb*.1875;
    diffuseSky*=seaEnvironmentIntensity;
    float reflectionDepth=texture2D(seaReflectionDepth,clamp(reflectedUV,vec2(.002),vec2(.998))).r;
    // Keep actual reflected coast/rocks. Only sky pixels use the directional
    // environment, so their reflection follows the full wave normal instead
    // of stretching a screen-space sky pixel into long glossy streaks.
    reflected=mix(reflected,env*seaEnvironmentIntensity,step(.99999,reflectionDepth));
  }
  float shade=getShadowMask();
  float nl=max(dot(n,seaSun),0.0);
  float coastDistance=dot(q,seaShore.xy)-seaShore.z;
  float shallowness=seaShore.w*(1.0-smoothstep(0.0,seaShoreWidth*3.0,coastDistance));
  vec3 body=mix(seaDeep,seaShallow,shallowness*.75);
  // Scattering under the interface is broad; it must not print the surface
  // normal's Lambert stripes onto every wave face like opaque painted plastic.
  vec3 ambient=seaAmbient;
  vec3 scatter=body*(ambient*1.2+seaKey*.80*shade);
  float back=pow(max(dot(eyeDir,-seaSun),0.0),5.0);
  float thinCrest=smoothstep(.10,.65,p.y/seaHeightScale);
  scatter+=seaShallow*seaKey*back*thinCrest*.28*shade;
  vec3 halfDir=normalize(eyeDir+seaSun);
  float nh=max(dot(n,halfDir),0.0),vh=max(dot(eyeDir,halfDir),0.0);
  float sunAlpha=sqrt(alpha*alpha+.00465*.00465);
  vec3 specular=seaKey*PI*seaFresnel(vh)*seaGGX(sunAlpha,nh,nv,nl)*nl*shade;
  vec2 foamUV=q-seaWind*seaTime*.23;
  float patches=seaFbm(foamUV*.61+vec2(astraNoise2(q*.22),astraNoise2(q*.22+8.1))*1.5);
  float breakingThreshold=max(.15,seaCompressionRms*1.5);
  float crest=smoothstep(breakingThreshold,breakingThreshold+.11,compression)
    *smoothstep(.06,.35,p.y/seaHeightScale);
  crest*=smoothstep(.38,.62,patches);
  float incoming=sin(coastDistance*1.4-seaTime*1.15+astraNoise2(q*.3)*2.1);
  float coast=seaShore.w*(1.0-smoothstep(.1,seaShoreWidth,abs(coastDistance)))
    *smoothstep(.45,.90,incoming+patches*.25)*smoothstep(.33,.66,patches);
  float bubble=seaFbm(foamUV*9.0);
  float detailFade=exp(-footprint*24.0);
  float coverage=mix(.8,smoothstep(.26,.68,bubble),detailFade);
  float foam=clamp((crest*1.7+coast)*seaFoam*coverage,0.0,.98);
  vec3 foamLight=diffuseSky+seaKey*nl*shade;
  vec3 foamColor=vec3(.76,.79,.76)*foamLight;
  vec3 outgoing=mix(scatter*(1.0-fresnel)+reflected*fresnel+specular,foamColor,foam);
  gl_FragColor=vec4(outgoing,1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  #include <fog_fragment>
}
`;

/**
 * @param {object} [opts]
 * width/depth (metres); waveHeight (significant wave height Hs); wavelength (m);
 * windDirection [x,z]; waterDepth (m); choppiness [0,1]; foam [0,2]; seed;
 * segments [16,384] along the longest edge; reflectionSize [64,2048];
 * waterColor/shallowColor; optional sunDirection THREE.Vector3 or [x,y,z].
 * shoreline: {direction:[x,z],position,width}; water is on the positive side
 * of dot(localXZ,direction)-position. It softens waves and adds swash foam.
 * Return Group: .userData.update(t,dt), sampleHeight(x,z,t), sampleSurface(x,z,t),
 * dispose(). Sampling uses LOCAL group coordinates, absolute seconds. Group may
 * be translated/rotated/scaled. Local heights are not world-space raycasts.
 */
export function makeOceanSurface(opts = {}) {
  let disposed = false;
  const width = finite(opts.width, 160, 'width', 1, 10000),
    depth = finite(opts.depth, 160, 'depth', 1, 10000);
  const height = finite(opts.waveHeight, 1.2, 'waveHeight', 0, 20);
  const length = finite(opts.wavelength, 18, 'wavelength', 1, 500);
  const waterDepth = finite(opts.waterDepth, 24, 'waterDepth', 0.1, 10000);
  const chop = finite(opts.choppiness, 0.85, 'choppiness', 0, 1);
  const foam = finite(opts.foam, 0.8, 'foam', 0, 2);
  const segments = Math.round(finite(opts.segments, 160, 'segments', 16, 384));
  const reflectionSize = Math.round(
    finite(opts.reflectionSize, 512, 'reflectionSize', 64, 2048)
  );
  const seed = finite(opts.seed, 42, 'seed', 0, 4294967295);
  const wind = readWind(opts.windDirection, [1, 0.2], 'makeOceanSurface windDirection');
  if (Math.hypot(wind.x, wind.z) < 1e-8)
    throw new RangeError('makeOceanSurface: windDirection must be a nonzero [x,z]');
  const windAngle = Math.atan2(wind.z, wind.x);
  const rng = mulberry32(seed),
    waves = [],
    phases = [];
  // Sample a gravity-wave energy envelope in logarithmic wavenumber bands.
  // For deep water E(k) is proportional to k^-3 exp[-1.25(kp/k)^2].
  // Multiplying by the logarithmic bin width gives each component's variance.
  // This finite directional quadrature is not an FFT/JONSWAP simulation.
  let varianceSum = 0;
  for (let i = 0; i < COUNT; i++) {
    const band = (i + 0.18 + rng() * 0.64) / COUNT;
    const waveLength = length * 2.6 * Math.pow(0.065 / 2.6, band);
    const k = TAU / waveLength;
    const relativeLength = waveLength / length;
    const spread = 0.32 + 0.95 * band;
    const angle = windAngle + (rng() - 0.5) * spread * 2;
    const amplitude =
      relativeLength *
      Math.exp(-0.625 * relativeLength * relativeLength) *
      (0.78 + rng() * 0.44);
    waves.push(new THREE.Vector4(Math.cos(angle), Math.sin(angle), amplitude, k));
    phases.push(
      new THREE.Vector2(Math.sqrt(9.81 * k * Math.tanh(k * waterDepth)), rng() * TAU)
    );
    varianceSum += amplitude * amplitude;
  }
  // Hs = 4 sigma for a random-phase sea; the public waveHeight is its sea-state
  // scale, not an impossible promise that no individual wave exceeds it.
  const amplitudeScale = height / (4 * Math.sqrt(0.5 * varianceSum));
  for (const wave of waves) wave.z *= amplitudeScale;
  const steepness = waves.reduce((s, w) => s + w.z * w.w, 0);
  let shore = new THREE.Vector4(0, 1, 0, 0),
    shoreWidth = 5;
  if (opts.shoreline) {
    const spec = opts.shoreline,
      direction = spec.direction ?? [0, 1];
    if (
      !Array.isArray(direction) ||
      direction.length !== 2 ||
      !direction.every(Number.isFinite) ||
      Math.hypot(...direction) < 1e-8
    )
      throw new RangeError('makeOceanSurface: invalid shoreline.direction');
    const norm = Math.hypot(...direction);
    shore = new THREE.Vector4(
      direction[0] / norm,
      direction[1] / norm,
      finite(spec.position, 0, 'shoreline.position', -10000, 10000),
      1
    );
    shoreWidth = finite(spec.width, 5, 'shoreline.width', 0.1, 500);
  }
  // Bound BOTH the wave derivative and the changing shoaling envelope. A
  // narrow beach under large waves otherwise folds horizontal coordinates,
  // making a queried height multi-valued and fixed-point inversion diverge.
  const shoreGradient = shore.w ? 1.5 / (shoreWidth * 1.7 + 0.5) : 0;
  const derivativeBound = steepness + waves.reduce((sum, w) => sum + w.z, 0) * shoreGradient;
  const chopScale = chop * Math.min(1.1, 0.88 / Math.max(derivativeBound, 1e-8));
  let sun = new THREE.Vector3(0.6, 0.7, 0.3).normalize();
  if (opts.sunDirection !== undefined) {
    if (
      !opts.sunDirection?.isVector3 &&
      (!Array.isArray(opts.sunDirection) || opts.sunDirection.length !== 3)
    ) {
      throw new RangeError('makeOceanSurface: sunDirection must be a Vector3 or [x,y,z]');
    }
    sun = opts.sunDirection.isVector3
      ? opts.sunDirection.clone()
      : new THREE.Vector3(...opts.sunDirection);
    if (![sun.x, sun.y, sun.z].every(Number.isFinite) || sun.length() < 1e-8)
      throw new RangeError('makeOceanSurface: invalid sunDirection');
    sun.normalize();
  }
  const uniforms = THREE.UniformsUtils.merge([
    THREE.UniformsLib.fog,
    THREE.UniformsLib.lights,
    {
      color: { value: new THREE.Color(0xffffff) },
      tDiffuse: { value: null },
      textureMatrix: { value: new THREE.Matrix4() },
      seaTime: { value: 0 },
      seaWaves: { value: waves },
      seaPhases: { value: phases },
      seaChop: { value: chopScale },
      seaShore: { value: shore },
      seaShoreWidth: { value: shoreWidth },
      seaFoam: { value: foam },
      seaHeightScale: { value: Math.max(height, 0.01) },
      seaReflectionSize: { value: reflectionSize },
      seaReflectionDepth: { value: null },
      seaEnvironment: { value: null },
      seaEnvironmentEnabled: { value: 0 },
      seaEnvironmentIntensity: { value: 1 },
      seaEnvironmentRotation: { value: new THREE.Matrix3() },
      seaCompressionRms: {
        value: Math.sqrt(waves.reduce((sum, w) => sum + 0.5 * (chopScale * w.z * w.w) ** 2, 0)),
      },
      seaDeep: { value: new THREE.Color(opts.waterColor ?? 0x083e4d) },
      seaShallow: { value: new THREE.Color(opts.shallowColor ?? 0x248d88) },
      seaSun: { value: sun },
      seaSunColor: { value: new THREE.Color(0xffffff) },
      seaAmbient: { value: new THREE.Color(0.28, 0.36, 0.43) },
      seaKey: { value: new THREE.Color(0.8, 0.8, 0.8) },
      seaWind: { value: wind.dir.clone() },
      seaRotation: { value: new THREE.Matrix3() },
    },
  ]);
  const nx = Math.max(8, Math.round((segments * width) / Math.max(width, depth)));
  const nz = Math.max(8, Math.round((segments * depth) / Math.max(width, depth)));
  const geometry = new THREE.PlaneGeometry(width, depth, nx, nz);
  // Concentrate samples around the local origin: near-camera crests remain
  // curved while distant water does not spend the same triangles per metre.
  // A sinh distribution preserves useful near-field density even at kilometre
  // extents; the old power law lost that density when the horizon moved out.
  const grid = geometry.attributes.position;
  const gridFocus = Math.max(3, Math.log(Math.max(width, depth) / 16) + 2.5);
  for (let i = 0; i < grid.count; i++) {
    const x = grid.getX(i) / (width * 0.5),
      y = grid.getY(i) / (depth * 0.5);
    grid.setXY(
      i,
      (Math.sinh(x * gridFocus) / Math.sinh(gridFocus)) * width * 0.5,
      (Math.sinh(y * gridFocus) / Math.sinh(gridFocus)) * depth * 0.5
    );
  }
  grid.needsUpdate = true;
  const sea = new Reflector(geometry, {
    textureWidth: reflectionSize,
    textureHeight: reflectionSize,
    multisample: 0,
    shader: {
      name: 'DisplacedOcean',
      uniforms,
      vertexShader: vertex,
      fragmentShader: fragment,
    },
  });
  sea.getRenderTarget().depthTexture = new THREE.DepthTexture(
    reflectionSize,
    reflectionSize,
    THREE.UnsignedIntType
  );
  sea.material.uniforms.seaReflectionDepth.value = sea.getRenderTarget().depthTexture;
  sea.getRenderTarget().texture.generateMipmaps = true;
  sea.getRenderTarget().texture.minFilter = THREE.LinearMipmapLinearFilter;
  sea.name = 'OceanSurface';
  sea.rotation.x = -Math.PI / 2;
  sea.material.lights = true;
  sea.material.fog = true;
  sea.receiveShadow = true;
  const group = new THREE.Group();
  group.name = opts.name ?? 'Ocean';
  group.add(sea);
  group.userData.placement = 'free';
  // Shader displacement needs a conservative bound for frustum/scene probes:
  // crests reach the amplitude sum (local z), chop moves them by chopScale times it.
  const reach = waves.reduce((sum, w) => sum + w.z, 0);
  geometry.computeBoundingBox();
  geometry.boundingBox.expandByVector(new THREE.Vector3(chopScale * reach, chopScale * reach, reach));
  geometry.boundingSphere = geometry.boundingBox.getBoundingSphere(new THREE.Sphere());
  const u = sea.material.uniforms,
    base = sea.onBeforeRender;
  const readLights = makeLightProbe(), environmentRotation = new THREE.Matrix4();
  // One guarded colour-pass capture: never in override (GTAO/depth) passes or
  // nested captures, renderer state restored, rigid mirror frame under scale.
  planarCapture(sea, (renderer, scene, camera, toRigid) => {
    const environment = scene.environment;
    const equirect =
      environment &&
      (environment.mapping === THREE.EquirectangularReflectionMapping ||
        environment.mapping === THREE.EquirectangularRefractionMapping);
    u.seaEnvironmentEnabled.value = equirect ? 1 : 0;
    u.seaEnvironment.value = equirect ? environment : null;
    u.seaEnvironmentIntensity.value = scene.environmentIntensity ?? 1;
    if (scene.environmentRotation) {
      u.seaEnvironmentRotation.value.setFromMatrix4(
        environmentRotation.makeRotationFromEuler(scene.environmentRotation).invert()
      );
    }
    const { sun: key, sunDirection, ambient, sky } = readLights(scene);
    u.seaKey.value.setRGB(0, 0, 0);
    u.seaSunColor.value.setRGB(0, 0, 0);
    u.seaAmbient.value.copy(ambient).add(sky).multiplyScalar(1 / Math.PI);
    if (key) {
      if (opts.sunDirection === undefined) u.seaSun.value.copy(sunDirection);
      u.seaSunColor.value.copy(key.color);
      u.seaKey.value.copy(key.color).multiplyScalar(key.intensity / Math.PI);
    }
    group.updateWorldMatrix(true, false);
    u.seaRotation.value.getNormalMatrix(group.matrixWorld);
    base.call(sea, renderer, scene, camera);
    u.textureMatrix.value.multiply(toRigid);
  }, { skip: () => disposed });
  let time = 0;
  const smooth = (x) => {
    x = Math.max(0, Math.min(1, x));
    return x * x * (3 - 2 * x);
  };
  const shoreAt = (x, z) =>
    shore.w
      ? smooth((x * shore.x + z * shore.y - shore.z + 0.5) / (shoreWidth * 1.7 + 0.5))
      : 1;
  function evaluate(x, z, t) {
    const s = shoreAt(x, z),
      p = new THREE.Vector3(x, 0, z);
    for (let i = 0; i < COUNT; i++) {
      const w = waves[i],
        phase = w.w * (w.x * x + w.y * z) - phases[i].x * t + phases[i].y;
      p.x += chopScale * w.z * s * w.x * Math.cos(phase);
      p.z += chopScale * w.z * s * w.y * Math.cos(phase);
      p.y += w.z * s * Math.sin(phase);
    }
    return p;
  }
  group.userData.update = (t) => {
    if (!Number.isFinite(t)) throw new RangeError('makeOceanSurface: time must be finite');
    time = t;
    u.seaTime.value = t;
  };
  group.userData.sampleSurface = (x, z, t = time) => {
    if (![x, z, t].every(Number.isFinite))
      throw new RangeError('makeOceanSurface: sampling coordinates/time must be finite');
    let qx = x,
      qz = z;
    // Fixed-point inversion: return height at the displaced XZ, not at its rest vertex.
    for (let i = 0; i < 128; i++) {
      const p = evaluate(qx, qz, t);
      const ex = x - p.x,
        ez = z - p.z;
      qx += ex;
      qz += ez;
      if (ex * ex + ez * ez < 1e-14) break;
    }
    const position = evaluate(qx, qz, t),
      dx = evaluate(qx + 0.005, qz, t).sub(evaluate(qx - 0.005, qz, t));
    const dz = evaluate(qx, qz + 0.005, t).sub(evaluate(qx, qz - 0.005, t));
    return { position, normal: dz.cross(dx).normalize() };
  };
  group.userData.sampleHeight = (x, z, t = time) =>
    group.userData.sampleSurface(x, z, t).position.y;
  const owned = snapshotResources(group).add(sea.getRenderTarget());
  owned.add({ dispose() { disposed = true; } });
  attachDisposal(group, owned);
  group.userData.surface = sea;
  group.userData.description =
    '32-band dispersive Gerstner ocean; mean-plane reflection; no fluid interaction or overturning breakers';
  return group;
}
