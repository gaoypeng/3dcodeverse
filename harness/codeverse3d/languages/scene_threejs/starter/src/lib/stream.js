/**
 * Clear, flowing stream along a graded Catmull-Rom path (metres, local Y up).
 * Curved tessellated surface, geometric travelling ripples, obstacle wakes,
 * advected foam, bed gravel and submerged stones. Uses standard PBR lighting,
 * environment reflection, scene shadows and fog. Deterministic at absolute time.
 * This is a kinematic shallow stream, not a Navier-Stokes solver: stones generate
 * authored wake fields; water does not collide with arbitrary scene geometry.
 * Refracts the opaque scene through a depth-varying water column using Three's
 * physical transmission pass. Transparent objects are not in that pass; this
 * remains a screen-space approximation. Reflections use the environment map,
 * or an optional mean-grade planar capture of the scene's banks.
 */
import * as THREE from 'three';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { mulberry32 } from './noise.js';
import { GLSL_UTIL, planarCapture } from './shader.js';

// IcosahedronGeometry duplicates triangle vertices. Preserve rounded cobble
// shading after deformation by averaging normals at coincident positions.
function smoothCobbleNormals(geometry) {
  geometry.computeVertexNormals();
  const p = geometry.attributes.position;
  const n = geometry.attributes.normal;
  const sums = new Map();
  const keyAt = (i) =>
    [p.getX(i), p.getY(i), p.getZ(i)].map((v) => Math.round(v * 1e5)).join(',');
  for (let i = 0; i < p.count; i++) {
    const key = keyAt(i);
    const sum = sums.get(key) ?? new THREE.Vector3();
    sum.add(new THREE.Vector3().fromBufferAttribute(n, i));
    sums.set(key, sum);
  }
  for (let i = 0; i < p.count; i++) {
    const sum = sums.get(keyAt(i)).clone().normalize();
    n.setXYZ(i, sum.x, sum.y, sum.z);
  }
}

function option(v, d, name, min, max = Infinity) {
  v = v === undefined ? d : v;
  if (!Number.isFinite(v) || v < min || v > max)
    throw new RangeError(`makeStream: ${name} must be ${min}..${max}`);
  return v;
}
// the library's one GLSL value noise (astraHash21 / astraNoise2)
const noiseGLSL = GLSL_UTIL;

/**
 * @param {object} [opts]
 * points [[x,y,z],...] upstream to downstream (non-increasing control heights);
 * width=3, widthVariation=0 [0,.3] (irregular bank widths), depth=.45, speed=1.1 m/s, roughness=.5 [0,1] (riffle activity);
 * segments=180, widthSegments=20, stoneCount=220, seed=42;
 * obstacles [{u:0..1,lateral:-1..1,radius:.3}] generate stone + trailing wake;
 * bed=true, waterColor=0x80b6a6, attenuationDistance=3 metres, bedColor=0x554a35;
 * caustics=.45 [0,1] modulates direct bed light from the ripple curvature.
 * endFade=0 metres; positive values smoothly flatten ripples at both ends
 * over that distance, allowing an exact join to a still surface or weir lip.
 * reflectionSize=0 disables optional bank reflection; use 256..2048 to enable
 * one mean-grade planar capture. Do not combine it with another scene reflector.
 * waterColor controls absorption over attenuationDistance, not surface paint.
 * Result Group .userData.update(t,dt), sample(u,lateral=0,t), dispose().
 * sample returns {position, tangent, normal, depth, speed} in LOCAL coordinates.
 * speed/depth are constant along the path; no tributaries/overturning falls.
 */
export function makeStream(opts = {}) {
  const width = option(opts.width, 3, 'width', 0.1, 100),
    depth = option(opts.depth, 0.45, 'depth', 0.03, 10);
  const widthVariation = option(opts.widthVariation, 0, 'widthVariation', 0, 0.3);
  const speed = option(opts.speed, 1.1, 'speed', 0, 15),
    activity = option(opts.roughness, 0.5, 'roughness', 0, 1);
  const segments = Math.round(option(opts.segments, 180, 'segments', 16, 512));
  const acrossSegments = Math.round(option(opts.widthSegments, 20, 'widthSegments', 4, 96));
  const count = Math.round(option(opts.stoneCount, 220, 'stoneCount', 0, 12000));
  const attenuationDistance = option(opts.attenuationDistance, 3, 'attenuationDistance', 0.01, 10000);
  const caustics = option(opts.caustics, 0.45, 'caustics', 0, 1);
  const reflectionSize = option(opts.reflectionSize, 0, 'reflectionSize', 0, 2048);
  if (reflectionSize !== 0 && (!Number.isInteger(reflectionSize) || reflectionSize < 256))
    throw new RangeError('makeStream: reflectionSize must be 0 or an integer from 256 to 2048');
  const seed = option(opts.seed, 42, 'seed', 0, 4294967295),
    rng = mulberry32(seed);
  const points = opts.points ?? [
    [-2, 0.8, -12],
    [1, 0.55, -5],
    [-1, 0.2, 3],
    [2, 0, 12],
  ];
  if (
    !Array.isArray(points) ||
    points.length < 2 ||
    points.some((p) => !Array.isArray(p) || p.length !== 3 || !p.every(Number.isFinite))
  )
    throw new RangeError(
      'makeStream: points must contain at least two finite [x,y,z] coordinates'
    );
  for (let i = 1; i < points.length; i++) {
    if (points[i][1] > points[i - 1][1] + 1e-6)
      throw new RangeError(
        'makeStream: points must run upstream to downstream (non-increasing y)'
      );
    if (Math.hypot(points[i][0] - points[i - 1][0], points[i][2] - points[i - 1][2]) < 0.001)
      throw new RangeError('makeStream: adjacent points need distinct XZ positions');
  }
  const path = new THREE.CatmullRomCurve3(
    points.map((p) => new THREE.Vector3(p[0], 0, p[2])),
    false,
    'centripetal'
  );
  path.arcLengthDivisions = Math.max(400, segments * 3);
  path.updateArcLengths();
  const length = path.getLength();
  const endFade = option(opts.endFade, 0, 'endFade', 0, length * .5);
  // A spatial Catmull-Rom spline can overshoot perfectly valid flat-to-sloped
  // control heights and make water run uphill. Use it only for the XZ course;
  // harmonic Hermite slopes preserve each monotone height interval exactly.
  const heights = points.map((p) => p[1]);
  const deltas = heights.slice(1).map((y, i) => y - heights[i]);
  const slopes = heights.map((_, i) => {
    if (i === 0) return deltas[0];
    if (i === heights.length - 1) return deltas.at(-1);
    const a = deltas[i - 1],
      b = deltas[i];
    return a * b > 0 ? (2 * a * b) / (a + b) : 0;
  });
  function gradedPoint(u) {
    const t = path.getUtoTmapping(u);
    const segment = Math.min(points.length - 2, Math.floor(t * (points.length - 1)));
    const f = t * (points.length - 1) - segment;
    const f2 = f * f,
      f3 = f2 * f;
    const point = path.getPoint(t);
    point.y =
      (2 * f3 - 3 * f2 + 1) * heights[segment] +
      (f3 - 2 * f2 + f) * slopes[segment] +
      (-2 * f3 + 3 * f2) * heights[segment + 1] +
      (f3 - f2) * slopes[segment + 1];
    return point;
  }
  const obstacles = opts.obstacles ?? [
    { u: 0.25, lateral: -0.35, radius: width * 0.1 },
    { u: 0.52, lateral: 0.42, radius: width * 0.13 },
    { u: 0.78, lateral: -0.12, radius: width * 0.09 },
  ];
  if (!Array.isArray(obstacles) || obstacles.length > 24)
    throw new RangeError('makeStream: at most 24 obstacles');
  const stones = obstacles.map((o, i) => ({
    u: option(o.u, undefined, `obstacles[${i}].u`, 0, 1),
    lateral: option(o.lateral, 0, `obstacles[${i}].lateral`, -0.95, 0.95),
    radius: option(o.radius, width * 0.1, `obstacles[${i}].radius`, 0.002, width * 0.45),
  }));
  const phase = rng() * 6.28;
  const waveRng = mulberry32(seed ^ 0x57a4f319);
  const rippleBands = Array.from({ length: 11 }, (_, i) => {
    const wavelength = .28 * 9 ** (i / 10) * (.85 + waveRng() * .3);
    const angle = (waveRng() - .5) * 2.6;
    const k = 2 * Math.PI / wavelength;
    return { ks: k * Math.cos(angle), kl: k * Math.sin(angle),
      amplitude: .0064 * wavelength ** .9, phase: waveRng() * Math.PI * 2,
      drift: .92 + waveRng() * .16 };
  });
  const widthAt = (u) =>
    width *
    (1 +
      widthVariation *
        (Math.sin(u * 13.38 + phase) * 0.65 + Math.sin(u * 35.81 + phase * 0.7) * 0.35));
  const unconstrainedDisplacement =
    rippleBands.reduce((sum, wave) => sum + wave.amplitude, 0) * (0.3 + activity) +
    stones.reduce((sum, o) => sum + o.radius * 0.105 * activity, 0);
  // Keep supported very shallow channels above their bed, including the
  // narrower optical column at the bank. This also bounds grazing normals.
  const heightScale = Math.min(1, depth * .38 / Math.max(1e-6, unconstrainedDisplacement));
  const displacementBound = unconstrainedDisplacement * heightScale;
  const bedRelief = Math.min(.018, depth * .12);
  const group = new THREE.Group();
  group.name = opts.name ?? 'Stream';
  group.userData.placement = 'free';
  const uniforms = {
    streamTime: { value: 0 },
    streamSpeed: { value: speed },
    streamWidth: { value: width },
    streamActivity: { value: activity },
    streamPhase: { value: phase },
    streamLength: { value: length },
    streamWidthVariation: { value: widthVariation },
    streamHeightScale: { value: heightScale },
    streamEndFade: { value: endFade },
  };
  const wakeCode = stones
    .map(
      (o) => `{
    float ds=s-${(o.u * length).toFixed(7)}, dl=l-(${(o.lateral * widthAt(o.u) * 0.5).toFixed(7)});
    float radius=${o.radius.toFixed(7)};
    float gate=smoothstep(-radius*.6,radius*.7,ds)*exp(-max(ds,0.0)/(radius*6.0));
    float envelope=exp(-pow(dl/(radius*.8+max(ds,0.0)*.21),2.0));
    h+=gate*envelope*sin(ds*10.0-streamTime*streamSpeed*8.0+abs(dl)*4.0)*radius*.105*streamActivity;
  }`
    )
    .join('\n');
  const rippleCode = rippleBands.map((wave) =>
    `h+=${wave.amplitude.toFixed(8)}*sin((s-streamTime*streamSpeed*${wave.drift.toFixed(7)})*${wave.ks.toFixed(7)}+l*${wave.kl.toFixed(7)}+${wave.phase.toFixed(7)});`
  ).join('\n');
  const heightGLSL = /* glsl */ `
  uniform float streamTime;
  uniform float streamSpeed;
  uniform float streamWidth;
  uniform float streamActivity;
  uniform float streamPhase;
  uniform float streamLength;
  uniform float streamWidthVariation;
  uniform float streamHeightScale;
  uniform float streamEndFade;
  float streamLocalWidth(float s){float u=s/streamLength;return streamWidth*(1.0+streamWidthVariation*(sin(u*13.38+streamPhase)*.65+sin(u*35.81+streamPhase*.7)*.35));}
  float streamHeight(float s,float l){
    float bank=1.0-pow(clamp(abs(l)/(streamLocalWidth(s)*.5),0.0,1.0),6.0);
    float h=0.0;
    ${rippleCode}
    float riffle=.58+.42*pow(.5+.5*sin(s*.83+l*1.4+streamPhase),2.0);
    h*=riffle*(.3+streamActivity);
    ${wakeCode}
    float endEnvelope=streamEndFade>0.0
      ? smoothstep(0.0,streamEndFade,s)*smoothstep(0.0,streamEndFade,streamLength-s):1.0;
    return h*bank*streamHeightScale*endEnvelope;
  }
  `;
  // A bounded, vertical-light focusing approximation, evaluated on the same
  // moving height field. It modulates only shadowed DIRECT diffuse light, never
  // emits white lines in shade. This is not a photon/caustic transport solver.
  const causticGLSL = /* glsl */ `
    float streamCaustic(vec2 p,float waterDepth){
      float e=.07,h=streamHeight(p.x,p.y);
      float xx=(streamHeight(p.x+e,p.y)+streamHeight(p.x-e,p.y)-2.0*h)/(e*e);
      float yy=(streamHeight(p.x,p.y+e)+streamHeight(p.x,p.y-e)-2.0*h)/(e*e);
      float xy=(streamHeight(p.x+e,p.y+e)-streamHeight(p.x+e,p.y-e)-streamHeight(p.x-e,p.y+e)+streamHeight(p.x-e,p.y-e))/(4.0*e*e);
      float d=waterDepth*.2498;
      float determinant=(1.0+d*xx)*(1.0+d*yy)-d*d*xy*xy;
      float focused=clamp(.94/max(.38,abs(determinant)),.68,2.4);
      float footprint=max(length(dFdx(p)),length(dFdy(p)));
      return mix(1.0,focused,${caustics.toFixed(7)}*(1.0-smoothstep(.045,.18,footprint)));
    }
  `;
  function pathDerivative(u) {
    const before = Math.max(0, u - 0.00001),
      after = Math.min(1, u + 0.00001);
    // s is horizontal path arc length, not the graded 3D curve's arc length.
    // Preserve the derivative's magnitude: normalizing it changes ripple slopes.
    return gradedPoint(after).sub(gradedPoint(before)).divideScalar((after - before) * length);
  }
  function frame(u) {
    const position = gradedPoint(u),
      longitudinal = pathDerivative(u),
      tangent = longitudinal.clone().normalize(),
      across = new THREE.Vector3(tangent.z, 0, -tangent.x).normalize();
    const before = Math.max(0, u - 0.00001),
      after = Math.min(1, u + 0.00001),
      db = pathDerivative(before), da = pathDerivative(after),
      acrossDerivative = new THREE.Vector3(da.z, 0, -da.x).normalize()
        .sub(new THREE.Vector3(db.z, 0, -db.x).normalize())
        .divideScalar((after - before) * length);
    return { position, tangent, across, longitudinal, acrossDerivative };
  }
  // A bank further from the centreline than the bend's radius folds the
  // ribbon back over itself (downward-facing water and bed). Refuse it.
  const reach = opts.bed === false ? 0.5 : 0.625;
  for (let i = 0; i <= segments; i++) {
    const u = i / segments, radius = 1 / Math.max(1e-9, frame(u).acrossDerivative.length());
    if (radius <= widthAt(u) * reach)
      throw new RangeError(`makeStream: the path bends tighter (radius ${radius.toFixed(2)} m at u=${u.toFixed(3)}) `
        + `than the ${opts.bed === false ? 'water' : 'bed'} half-width ${(widthAt(u) * reach).toFixed(2)} m; `
        + 'narrow the stream or widen the bend');
  }
  function height(s, l, t) {
    const bank = 1 - Math.pow(Math.min(1, Math.abs(l) / (widthAt(s / length) * 0.5)), 6);
    let h = 0;
    for (const wave of rippleBands)
      h += wave.amplitude * Math.sin((s - t * speed * wave.drift) * wave.ks + l * wave.kl + wave.phase);
    const riffle = .58 + .42 * (.5 + .5 * Math.sin(s * .83 + l * 1.4 + phase)) ** 2;
    h *= riffle * (.3 + activity);
    for (const o of stones) {
      const ds = s - o.u * length,
        dl = l - o.lateral * widthAt(o.u) * 0.5,
        r = o.radius;
      let g = Math.max(0, Math.min(1, (ds + r * 0.6) / (r * 1.3)));
      g = g * g * (3 - 2 * g) * Math.exp(-Math.max(ds, 0) / (r * 6));
      const envelope = Math.exp(-Math.pow(dl / (r * 0.8 + Math.max(ds, 0) * 0.21), 2));
      h +=
        g *
        envelope *
        Math.sin(ds * 10 - t * speed * 8 + Math.abs(dl) * 4) *
        r *
        0.105 *
        activity;
    }
    const endEnvelope = endFade > 0
      ? THREE.MathUtils.smoothstep(s, 0, endFade) * THREE.MathUtils.smoothstep(length-s, 0, endFade) : 1;
    return h * bank * heightScale * endEnvelope;
  }
  function makeRibbon(bed = false) {
    const positions = [],
      uv = [],
      flows = [],
      longitudinals = [],
      acrosses = [],
      indices = [];
    for (let i = 0; i <= segments; i++) {
      const u = i / segments,
        f = frame(u);
      for (let j = 0; j <= acrossSegments; j++) {
        const lateral = (j / acrossSegments) * 2 - 1,
          l = lateral * widthAt(u) * 0.5 * (bed ? 1.25 : 1);
        const p = f.position.clone().addScaledVector(f.across, l);
        if (bed)
          p.y -= depth * (1 - 0.55 * (l / (widthAt(u) * .5)) ** 2) + bedRelief * Math.sin(i * 0.61 + j * 1.23);
        positions.push(p.x, p.y, p.z);
        uv.push(j / acrossSegments, u);
        flows.push(u * length, l);
        // Partial derivative of C(s) + l*A(s), holding lateral metres l fixed.
        // Width variation adds a multiple of the across derivative, which drops
        // out of their cross product, so this basis also covers tapered banks.
        const longitudinal = f.longitudinal.clone().addScaledVector(f.acrossDerivative, l);
        longitudinals.push(longitudinal.x, longitudinal.y, longitudinal.z);
        acrosses.push(f.across.x, f.across.y, f.across.z);
        if (i < segments && j < acrossSegments) {
          const a = i * (acrossSegments + 1) + j,
            b = a + acrossSegments + 1;
          indices.push(a, b, a + 1, b, b + 1, a + 1);
        }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setAttribute('streamFlow', new THREE.Float32BufferAttribute(flows, 2));
    g.setAttribute('streamLongitudinal', new THREE.Float32BufferAttribute(longitudinals, 3));
    g.setAttribute('streamAcross', new THREE.Float32BufferAttribute(acrosses, 3));
    g.setIndex(indices);
    g.computeVertexNormals();
    g.computeBoundingBox();
    g.computeBoundingSphere();
    g.boundingBox.expandByScalar(displacementBound);
    g.boundingSphere.radius += displacementBound;
    return g;
  }
  const material = new THREE.MeshPhysicalMaterial({
    name: 'ClearStreamWater',
    color: 0xffffff,
    roughness: 0.04,
    metalness: 0,
    ior: 1.333,
    clearcoat: 0,
    transmission: 1,
    thickness: depth,
    attenuationColor: opts.waterColor ?? 0x80b6a6,
    attenuationDistance,
    opacity: 1,
    depthWrite: true,
    side: THREE.FrontSide,
    envMapIntensity: 1,
  });
  const foamCode = stones
    .map(
      (o) => `{
    float ds=vStreamFlow.x-${(o.u * length).toFixed(7)},dl=vStreamFlow.y-(${(o.lateral * widthAt(o.u) * 0.5).toFixed(7)}), r=${o.radius.toFixed(7)};
    float downstream=smoothstep(-r*.4,r*.4,ds)*exp(-max(ds,0.0)/(r*3.8));
    float wakeWidth=r*.7+max(ds,0.0)*.22;
    float wakes=exp(-pow((abs(dl)-wakeWidth*.7)/(r*.3+max(ds,0.0)*.08),2.0));
    wakeFoam+=downstream*wakes;
  }`
    )
    .join('\n');
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        `#include <common>\nattribute vec2 streamFlow; attribute vec3 streamLongitudinal; attribute vec3 streamAcross; varying vec2 vStreamFlow; varying vec3 vStreamPosition; varying vec3 vStreamLongitudinal; varying vec3 vStreamAcross;\n${heightGLSL}`
      )
      .replace(
        '#include <beginnormal_vertex>',
        /* glsl */ `
       float hs=(streamHeight(streamFlow.x+.015,streamFlow.y)-streamHeight(streamFlow.x-.015,streamFlow.y))/.03;
       float hl=(streamHeight(streamFlow.x,streamFlow.y+.015)-streamHeight(streamFlow.x,streamFlow.y-.015))/.03;
       vec3 objectNormal=normalize(cross(streamLongitudinal+vec3(0,hs,0),streamAcross+vec3(0,hl,0)));
       #ifdef USE_TANGENT
         vec3 objectTangent=vec3(tangent.xyz);
       #endif
      `
      )
      .replace(
        '#include <begin_vertex>',
        `vec3 transformed=position; transformed.y+=streamHeight(streamFlow.x,streamFlow.y); vStreamFlow=streamFlow;vStreamPosition=transformed;vStreamLongitudinal=streamLongitudinal;vStreamAcross=streamAcross;`
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        `#include <common>\nuniform mat3 normalMatrix; varying vec2 vStreamFlow; varying vec3 vStreamPosition; varying vec3 vStreamLongitudinal; varying vec3 vStreamAcross;\n${heightGLSL}\n${noiseGLSL}`
      )
      .replace(
        '#include <color_fragment>',
        /* glsl */ `
      #include <color_fragment>
      vec2 advect=vec2(vStreamFlow.x-streamTime*streamSpeed,vStreamFlow.y);
      float cellular=astraNoise2(advect*vec2(4.5,15.0));
      float fine=astraNoise2(advect*vec2(12.0,29.0));
      float wakeFoam=0.0;
      ${foamCode}
      float foam=clamp(wakeFoam*smoothstep(.52,.80,cellular*.7+fine*.3)*.52*streamActivity,0.0,.72);
      float bank=pow(clamp(abs(vStreamFlow.y)/(streamLocalWidth(vStreamFlow.x)*.5),0.0,1.0),8.0);
      foam=max(foam,bank*smoothstep(.76,.94,cellular)*.055*streamActivity);
      diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.74,.78,.72),foam);
      // The refracted path reaches the graded bed rather than a flat sheet of
      // constant thickness. Absorption belongs to the volume, not its surface.
      float opticalDepth=${depth.toFixed(7)}*(1.0-.55*pow(vStreamFlow.y/(streamLocalWidth(vStreamFlow.x)*.5),2.0));
      `
      )
      .replace(
        '#include <normal_fragment_maps>',
        `#include <normal_fragment_maps>
      // Evaluate resolved slopes per fragment, not by interpolating sparse
      // vertex normals. Screen-footprint averaging removes distant sparkle.
      float flowFootprint=max(length(dFdx(vStreamFlow)),length(dFdy(vStreamFlow)));
      float slopeStep=max(.015,flowFootprint*.6);
      float streamHs=(streamHeight(vStreamFlow.x+slopeStep,vStreamFlow.y)-streamHeight(vStreamFlow.x-slopeStep,vStreamFlow.y))/(2.0*slopeStep);
      float streamHl=(streamHeight(vStreamFlow.x,vStreamFlow.y+slopeStep)-streamHeight(vStreamFlow.x,vStreamFlow.y-slopeStep))/(2.0*slopeStep);
      normal=normalize(normalMatrix*cross(vStreamLongitudinal+vec3(0,streamHs,0),vStreamAcross+vec3(0,streamHl,0)));
      float microFilter=1.0-smoothstep(.035,.14,flowFootprint);
      float streamMicro=(astraNoise2(advect*vec2(8.0,12.0))*.0012+astraNoise2(advect*vec2(19.0,24.0))*.0003)*microFilter;
      vec3 sx=dFdx(-vViewPosition),sy=dFdy(-vViewPosition);
      vec3 rx=cross(sy,normal),ry=cross(normal,sx);
      float det=dot(sx,rx);
      vec2 grad=vec2(dFdx(streamMicro),dFdy(streamMicro));
      normal=normalize(abs(det)*normal-sign(det)*(grad.x*rx+grad.y*ry));
      `
      )
      .replace(
        '#include <roughnessmap_fragment>',
        `#include <roughnessmap_fragment>\nroughnessFactor=mix(roughnessFactor,.67,foam);`
      )
      .replace(
        '#include <transmission_pars_fragment>',
        THREE.ShaderChunk.transmission_pars_fragment.replaceAll(
          'refractionCoords /= 2.0;', `refractionCoords /= 2.0;
            // Offscreen refraction has no valid source texel. Blend sampled
            // radiance at the border, never interpolate UV coordinates: a UV
            // blend folds the image and stretches pebbles into vertical bands.
            vec4 surfaceNdc=projMatrix*viewMatrix*vec4(position,1.0);
            vec2 streamSurfaceUv=surfaceNdc.xy/surfaceNdc.w*.5+.5;
            vec2 border=min(refractionCoords,1.0-refractionCoords);
            float streamInFrame=smoothstep(0.0,.06,min(border.x,border.y));
          `
        ).replace('transmittedLight = getTransmissionSample( refractionCoords, roughness, ior );',
          `transmittedLight = mix(getTransmissionSample(streamSurfaceUv,roughness,ior),
            getTransmissionSample(clamp(refractionCoords,vec2(0.0),vec2(1.0)),roughness,ior),streamInFrame);`)
         .replace('vec4 transmissionSample = getTransmissionSample( refractionCoords, roughness, iors[ i ] );',
          `vec4 transmissionSample = mix(getTransmissionSample(streamSurfaceUv,roughness,iors[i]),
            getTransmissionSample(clamp(refractionCoords,vec2(0.0),vec2(1.0)),roughness,iors[i]),streamInFrame);`)
         .replace('return normalize( refractionVector ) * thickness * modelScale;',
           // Our thickness below is the solved WORLD ray distance. Scaling a
           // world direction componentwise would bend it under rotated scale.
           'return normalize( refractionVector ) * thickness;')
      )
      .replace(
        '#include <transmission_fragment>',
        THREE.ShaderChunk.transmission_fragment
          .replace('material.transmission = transmission;', 'material.transmission = transmission * (1.0 - foam);')
          .replace('material.thickness = thickness;', `
            vec3 streamWorldNormal=inverseTransformDirection(normal,viewMatrix);
            vec3 streamRay=refract(-normalize(cameraPosition-vWorldPosition),streamWorldNormal,1.0/ior);
            vec3 streamLocalRay=inverse(mat3(modelMatrix))*streamRay;
            vec3 streamBedNormal=normalize(cross(vStreamLongitudinal,vStreamAcross));
            float streamBedDistance=(opticalDepth+streamHeight(vStreamFlow.x,vStreamFlow.y))*streamBedNormal.y;
            material.thickness=max(0.0,streamBedDistance)/max(1e-8,abs(dot(streamLocalRay,streamBedNormal)));
          `)
      );
    if (reflectionSize) {
      shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', '#include <common>\nuniform sampler2D streamReflection;uniform mat4 streamReflectionMatrix;')
        .replace('#include <lights_fragment_end>', `#include <lights_fragment_end>
          vec4 reflectionCoord=streamReflectionMatrix*vec4(vStreamPosition,1.0);
          vec2 reflectionUv=reflectionCoord.xy/reflectionCoord.w;
          vec3 viewFlat=normalize(normalMatrix*vec3(0.0,1.0,0.0));
          reflectionUv+=(normal.xy-viewFlat.xy)*.025;
          vec2 reflectionEdge=min(reflectionUv,1.0-reflectionUv);
          float reflectionWeight=smoothstep(0.0,.04,min(reflectionEdge.x,reflectionEdge.y))*(1.0-foam);
          float reflectionLod=clamp(log2(max(1.0,length(fwidth(reflectionUv))*${reflectionSize.toFixed(1)})),0.0,6.0);
          vec3 bankReflection=textureLod(streamReflection,clamp(reflectionUv,vec2(.001),vec2(.999)),reflectionLod).rgb;
          vec3 reflectionF=EnvironmentBRDF(normal,geometryViewDir,material.specularColorBlended,material.specularF90,material.roughness);
          reflectedLight.indirectSpecular=mix(reflectedLight.indirectSpecular,bankReflection*reflectionF,reflectionWeight);
        `);
    }
  };
  material.customProgramCacheKey = () =>
    `stream-v4-${seed}-${width}-${widthVariation}-${depth}-${length}-${reflectionSize}-${JSON.stringify(stones)}`;
  const surface = new THREE.Mesh(makeRibbon(), material);
  surface.name = 'StreamSurface';
  surface.receiveShadow = true;
  surface.renderOrder = 2;
  group.add(surface);
  let reflection = null;
  if (reflectionSize) {
    // The helper never enters the scene graph. Only its clipped virtual camera
    // and owned render target are used, so it cannot add a visible water slab.
    reflection = new Reflector(new THREE.PlaneGeometry(1, 1), {
      textureWidth: reflectionSize, textureHeight: reflectionSize, multisample: 0,
    });
    const first = frame(0).position, last = frame(1).position;
    const direction = last.clone().sub(first);
    const grade = direction.y / Math.max(.001, direction.x ** 2 + direction.z ** 2);
    const gradeNormal = new THREE.Vector3(-direction.x * grade, 1, -direction.z * grade).normalize();
    reflection.position.copy(frame(.5).position);
    reflection.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), gradeNormal);
    reflection.updateMatrix();
    const target = reflection.getRenderTarget();
    target.texture.generateMipmaps = true;
    target.texture.minFilter = THREE.LinearMipmapLinearFilter;
    uniforms.streamReflection = { value: target.texture };
    uniforms.streamReflectionMatrix = { value: new THREE.Matrix4() };
    // The helper Reflector is placed on the group's (possibly affine) mean
    // grade; its texture matrix is mapped back onto the surface's positions.
    planarCapture(surface, (renderer, scene, camera, toRigid) => {
      reflection.onBeforeRender(renderer, scene, camera);
      uniforms.streamReflectionMatrix.value
        .copy(reflection.material.uniforms.textureMatrix.value).multiply(toRigid);
    }, { mirror: reflection, frame: group, normal: gradeNormal, point: reflection.position });
  }
  if (opts.bed !== false) {
    const bedMaterial = new THREE.MeshStandardMaterial({
      color: opts.bedColor ?? 0x554a35,
      roughness: 0.9,
    });
    bedMaterial.onBeforeCompile = (shader) => {
      Object.assign(shader.uniforms, uniforms);
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', '#include <common>\nattribute vec2 streamFlow; varying vec2 vBedFlow; varying vec3 vGravelPosition;')
        .replace(
          '#include <begin_vertex>',
          '#include <begin_vertex>\nvGravelPosition=position;vBedFlow=streamFlow;'
        );
      shader.fragmentShader = shader.fragmentShader
        .replace(
          '#include <common>',
          `#include <common>\nvarying vec3 vGravelPosition;varying vec2 vBedFlow;\n${noiseGLSL}\n${heightGLSL}\n${causticGLSL}`
        )
        .replace(
          '#include <color_fragment>',
          /* glsl */ `
      #include <color_fragment>
      vec2 gp=vGravelPosition.xz*26.0;
      vec2 cell=floor(gp), frac=fract(gp);
      float d1=5.0,d2=5.0,stoneTone=.0;
      for(int x=-1;x<=1;x++)for(int y=-1;y<=1;y++){
        vec2 offset=vec2(float(x),float(y));
        vec2 jitter=vec2(astraHash21(cell+offset),astraHash21(cell+offset+53.2));
        float dist=length(offset+jitter-frac);
        if(dist<d1){d2=d1;d1=dist;stoneTone=astraHash21(cell+offset+19.1);}else if(dist<d2)d2=dist;
      }
      float filterWidth=max(length(dFdx(gp)),length(dFdy(gp)));
      float seam=smoothstep(.015,.10,d2-d1);
        float gravelDetail=mix(.68,.86+stoneTone*.4,seam);
      diffuseColor.rgb*=mix(gravelDetail,.91,smoothstep(.45,1.6,filterWidth));
      diffuseColor.rgb=mix(diffuseColor.rgb,diffuseColor.rgb*vec3(.66,.77,.75),.2);
      `
        )
        .replace('#include <lights_fragment_end>', `#include <lights_fragment_end>
          float waterDepth=${depth.toFixed(7)}*(1.0-.55*pow(vBedFlow.y/(streamLocalWidth(vBedFlow.x)*.5),2.0));
          reflectedLight.directDiffuse*=streamCaustic(vBedFlow,max(0.0,waterDepth));
        `);
    };
    bedMaterial.customProgramCacheKey = () => `stream-bed-v2-${material.customProgramCacheKey()}-${caustics}`;
    const bed = new THREE.Mesh(makeRibbon(true), bedMaterial);
    bed.name = 'StreamBed';
    bed.receiveShadow = true;
    group.add(bed);
    // One geometry/material/draw call for gravel, actual silhouettes under water.
    if (count > 0) {
      const stoneGeo = new THREE.IcosahedronGeometry(1, 2);
      const sp = stoneGeo.attributes.position;
      for (let i = 0; i < sp.count; i++) {
        const x = sp.getX(i),
          y = sp.getY(i),
          z = sp.getZ(i),
          r = 1 + 0.13 * Math.sin(x * 4.7 + y * 3.1) * Math.cos(z * 5.3 - x * 2.3);
        sp.setXYZ(i, x * r, y * r, z * r);
      }
      smoothCobbleNormals(stoneGeo);
      const stoneMaterial = new THREE.MeshStandardMaterial({
        color: 0xc5bfae,
        roughness: 0.72,
      });
      const pebbleFlows = new Float32Array(count * 3);
      const pebbleFrames = new Float32Array(count * 2);
      stoneMaterial.onBeforeCompile = (shader) => {
        Object.assign(shader.uniforms, uniforms);
        shader.vertexShader = shader.vertexShader
          .replace('#include <common>', '#include <common>\nattribute vec3 pebbleFlow;attribute vec2 pebbleFrame;varying vec3 vPebbleFlow;varying vec3 vPebblePoint;')
          .replace('#include <project_vertex>', `
            vec3 stoneOffset=(instanceMatrix*vec4(transformed,0.0)).xyz;
            vPebbleFlow=pebbleFlow+vec3(dot(stoneOffset.xz,pebbleFrame),dot(stoneOffset.xz,vec2(pebbleFrame.y,-pebbleFrame.x)), -stoneOffset.y);
            vPebblePoint=(instanceMatrix*vec4(transformed,1.0)).xyz;
            #include <project_vertex>
          `);
        shader.fragmentShader = shader.fragmentShader
          .replace('#include <common>', `#include <common>\nvarying vec3 vPebbleFlow;varying vec3 vPebblePoint;\n${noiseGLSL}\n${heightGLSL}\n${causticGLSL}`)
          .replace('#include <color_fragment>', `#include <color_fragment>
            float mineral=astraNoise2(vPebblePoint.xz*43.0+vPebblePoint.y*29.0);
            float grain=astraNoise2(vPebblePoint.xz*167.0+vPebblePoint.y*97.0);
            float filterWidth=max(length(dFdx(vPebblePoint)),length(dFdy(vPebblePoint)));
            diffuseColor.rgb*=mix(.70+mineral*.55+grain*.25,1.1,smoothstep(.008,.04,filterWidth));
          `)
          .replace('#include <lights_fragment_end>', `#include <lights_fragment_end>\nreflectedLight.directDiffuse*=streamCaustic(vPebbleFlow.xy,max(0.0,vPebbleFlow.z));`);
      };
      stoneMaterial.customProgramCacheKey = () => `stream-cobble-v2-${material.customProgramCacheKey()}-${caustics}`;
      const pebbles = new THREE.InstancedMesh(stoneGeo, stoneMaterial, count);
      pebbles.name = 'SubmergedPebbles';
      pebbles.receiveShadow = true;
      const temp = new THREE.Object3D(),
        color = new THREE.Color();
      for (let i = 0; i < count; i++) {
        const u = 0.01 + rng() * 0.98,
          lateral = (rng() * 2 - 1) * 0.91,
          f = frame(u),
          r = 0.035 + rng() ** 1.6 * 0.13;
        temp.position.copy(f.position).addScaledVector(f.across, lateral * widthAt(u) * 0.5);
        temp.position.y -= depth * (1 - 0.55 * lateral * lateral) - r * 0.2;
        temp.scale.set(
          r * (0.8 + rng() * 0.6),
          r * (0.45 + rng() * 0.4),
          r * (1 + rng() * 0.65)
        );
        temp.rotation.set((rng() - .5) * .32, rng() * 6.28, (rng() - .5) * .32);
        temp.updateMatrix();
        pebbles.setMatrixAt(i, temp.matrix);
        pebbleFlows.set([u * length, lateral * widthAt(u) * .5, depth * (1 - .55 * lateral * lateral)], i * 3);
        pebbleFrames.set([f.tangent.x, f.tangent.z], i * 2);
        color.setHSL(0.065 + rng() * 0.07, 0.08 + rng() * 0.22, 0.07 + rng() * 0.16);
        pebbles.setColorAt(i, color);
      }
      pebbles.instanceMatrix.needsUpdate = true;
      pebbles.instanceColor.needsUpdate = true;
      stoneGeo.setAttribute('pebbleFlow', new THREE.InstancedBufferAttribute(pebbleFlows, 3));
      stoneGeo.setAttribute('pebbleFrame', new THREE.InstancedBufferAttribute(pebbleFrames, 2));
      group.add(pebbles);
    }
  }
  // Wake-generating cobbles are actual obstacles, not white decals in empty water.
  for (let i = 0; i < stones.length; i++) {
    const o = stones[i],
      f = frame(o.u),
      geo = new THREE.IcosahedronGeometry(o.radius, 2),
      attr = geo.attributes.position;
    for (let j = 0; j < attr.count; j++) {
      const v = new THREE.Vector3().fromBufferAttribute(attr, j);
      const k = 1 + 0.075 * Math.sin(v.x * 19 + v.z * 13) * Math.cos(v.y * 23);
      v.multiplyScalar(k);
      attr.setXYZ(j, v.x, v.y, v.z);
    }
    smoothCobbleNormals(geo);
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color().setHSL(0.105, 0.1, 0.065 + rng() * 0.045),
      roughness: 0.47,
    });
    const rock = new THREE.Mesh(geo, mat);
    rock.name = `StreamObstacle_${i}`;
    rock.position.copy(f.position).addScaledVector(f.across, o.lateral * widthAt(o.u) * 0.5);
    rock.position.y -= o.radius * 0.32;
    rock.scale.set(1, 0.72, 1.15);
    rock.rotation.y = rng() * 6.28;
    rock.castShadow = rock.receiveShadow = true;
    group.add(rock);
  }
  let time = 0,
    disposed = false;
  group.userData.update = (t) => {
    if (!Number.isFinite(t)) throw new RangeError('makeStream: time must be finite');
    time = t;
    uniforms.streamTime.value = t;
  };
  group.userData.sample = (u, lateral = 0, t = time) => {
    if (
      !Number.isFinite(u) ||
      u < 0 ||
      u > 1 ||
      !Number.isFinite(lateral) ||
      Math.abs(lateral) > 1 ||
      !Number.isFinite(t)
    )
      throw new RangeError(
        'makeStream: sample requires u in [0,1], lateral in [-1,1], finite time'
      );
    const f = frame(u),
      s = u * length,
      l = lateral * widthAt(u) * 0.5;
    f.position.addScaledVector(f.across, l);
    f.position.y += height(s, l, t);
    const hs = (height(s + 0.015, l, t) - height(s - 0.015, l, t)) / 0.03,
      hl = (height(s, l + 0.015, t) - height(s, l - 0.015, t)) / 0.03;
    const normal = f.longitudinal
      .clone()
      .addScaledVector(f.acrossDerivative, l)
      .add(new THREE.Vector3(0, hs, 0))
      .cross(f.across.clone().add(new THREE.Vector3(0, hl, 0)))
      .normalize();
    return {
      position: f.position,
      tangent: f.tangent,
      normal,
      depth: depth * (1 - 0.55 * lateral * lateral),
      speed,
      width: widthAt(u),
    };
  };
  // Snapshot owned resources now. Callers may add children or remove meshes
  // later; neither action changes which allocations this constructor owns.
  const ownedResources = new Set();
  const ownedInstances = [];
  group.traverse((object) => {
    if (object.isInstancedMesh) ownedInstances.push(object);
    for (const resource of [
      object.geometry,
      ...(Array.isArray(object.material) ? object.material : [object.material]),
    ]) {
      if (resource) ownedResources.add(resource);
    }
  });
  group.userData.dispose = () => {
    if (disposed) return;
    disposed = true;
    for (const resource of ownedResources) resource.dispose();
    for (const object of ownedInstances) object.dispose();
    if (reflection) {
      reflection.geometry.dispose();
      reflection.dispose();
    }
  };
  group.userData.surface = surface;
  group.userData.length = length;
  group.userData.description =
    'Graded kinematic stream with geometric ripples, cobble wakes and depth-dependent opaque-scene refraction; no arbitrary fluid collisions';
  return group;
}
