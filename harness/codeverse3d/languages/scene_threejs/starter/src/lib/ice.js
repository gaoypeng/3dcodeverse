/** Fractured freshwater ice in metres.
 *
 * Each seeded floe is a closed, bevelled solid. Open fissures are geometry,
 * not dark lines on a transparent plane. Three's physical transmission reads
 * the opaque scene behind the volume; attenuation follows its local thickness.
 * Frost changes transmission and roughness together. Trapped air is instanced
 * inside the volume and therefore shifts relative to its surface with the view.
 */
import * as THREE from 'three';
import { fbm2, mulberry32 } from './noise.js';
import { patchStandard } from './shader.js';
import { attachDisposal, snapshotResources } from './lifecycle.js';

const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const smooth = (a, b, value) => {
  const t = clamp((value - a) / (b - a), 0, 1);
  return t * t * (3 - 2 * t);
};

// Clip a convex XZ polygon by nx*x + nz*z <= limit.
function clip(polygon, nx, nz, limit) {
  const result = [];
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i], b = polygon[(i + 1) % polygon.length];
    const da = nx * a[0] + nz * a[1] - limit;
    const db = nx * b[0] + nz * b[1] - limit;
    if (da <= 1e-10) result.push(a);
    if ((da < 0 && db > 0) || (da > 0 && db < 0)) {
      const t = da / (da - db);
      result.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
    }
  }
  return result.filter((p, i) => {
    const q = result[(i + result.length - 1) % result.length];
    return !q || Math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-8;
  });
}

function inset(polygon, distance) {
  const xs=polygon.map(p=>p[0]),zs=polygon.map(p=>p[1]);
  const x0=Math.min(...xs),x1=Math.max(...xs),z0=Math.min(...zs),z1=Math.max(...zs);
  let out = [[x0,z0],[x1,z0],[x1,z1],[x0,z1]];
  for (let i = 0; i < polygon.length && out.length > 2; i++) {
    const a = polygon[i], b = polygon[(i + 1) % polygon.length];
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const nx = (b[1] - a[1]) / length, nz = -(b[0] - a[0]) / length;
    out = clip(out, nx, nz, nx * a[0] + nz * a[1] - distance);
  }
  return out;
}

function distanceToEdge(polygon, point) {
  let distance = Infinity;
  for (let i = 0; i < polygon.length; i++) {
    const a = polygon[i], b = polygon[(i + 1) % polygon.length];
    const dx = b[0] - a[0], dz = b[1] - a[1];
    distance = Math.min(distance,
      (dx * (point[1] - a[1]) - dz * (point[0] - a[0])) / Math.hypot(dx, dz));
  }
  return distance;
}

function opticalTexture(width, depth, thicknessAt, maximumThickness, frost, seed, cracks) {
  const resolution = 512, data = new Uint8Array(resolution * resolution * 4);
  for (let z = 0; z < resolution; z++) for (let x = 0; x < resolution; x++) {
    const px = ((x + 0.5) / resolution - 0.5) * width;
    const pz = ((z + 0.5) / resolution - 0.5) * depth;
    const field = fbm2(px * 1.8, pz * 1.8, { seed: seed + 101, octaves: 3 });
    const grain = fbm2(px * 24, pz * 24, { seed: seed + 303, octaves: 2 });
    const cover = smooth(0.32 - frost * 0.62, 0.53 - frost * 0.62,
      field + grain * 0.28) * Math.min(1, frost * 3);
    const i = (z * resolution + x) * 4;
    data[i] = Math.round((1 - cover * 0.96) * 255);
    data[i + 1] = Math.round(clamp(thicknessAt(px, pz) / maximumThickness, 0, 1) * 255);
    data[i + 2] = Math.round(clamp(0.5 + grain, 0, 1) * 255);
    data[i + 3] = 0;
  }
  // Rasterize a sparse branching stress network in the same metric domain
  // as the volume. Coverage stores subtexel line width, then mipmaps filter
  // it further. These arrested hairlines terminate inside a solid floe.
  for (const [a, b] of cracks) {
    const ax=(a[0]/width+.5)*resolution, ay=(a[1]/depth+.5)*resolution;
    const bx=(b[0]/width+.5)*resolution, by=(b[1]/depth+.5)*resolution;
    const dx=bx-ax,dy=by-ay,length2=dx*dx+dy*dy;
    const steps=Math.max(1,Math.ceil(Math.sqrt(length2)*1.5));
    const coverage=Math.min(1,0.0025*resolution/Math.sqrt(width*depth));
    for(let i=0;i<=steps;i++) {
      const x=ax+dx*i/steps,y=ay+dy*i/steps;
      for(let oy=-1;oy<=1;oy++)for(let ox=-1;ox<=1;ox++) {
        const px=Math.floor(x)+ox,py=Math.floor(y)+oy;
        if(px<0||py<0||px>=resolution||py>=resolution)continue;
        const t=clamp(((px+.5-ax)*dx+(py+.5-ay)*dy)/Math.max(length2,1e-9),0,1);
        const distance=Math.hypot(px+.5-ax-t*dx,py+.5-ay-t*dy);
        const index=(py*resolution+px)*4+3;
        data[index]=Math.max(data[index],Math.round(Math.max(0,1-distance)*coverage*255));
      }
    }
  }
  const texture = new THREE.DataTexture(data, resolution, resolution);
  texture.colorSpace = THREE.NoColorSpace;
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.anisotropy = 8;
  texture.needsUpdate = true;
  return texture;
}

function iceMaterial(opts, optical, maximumThickness) {
  const material = new THREE.MeshPhysicalMaterial({
    color: opts.color ?? 0xc9e7ed,
    roughness: clamp(opts.roughness ?? 0.075, 0.015, 1),
    metalness: 0, ior: 1.31,
    transmission: clamp(opts.transmission ?? 0.98, 0, 1),
    transmissionMap: optical,
    thickness: maximumThickness,
    attenuationColor: new THREE.Color(opts.attenuationColor ?? 0x80c8d5),
    attenuationDistance: Math.max(0.02, opts.attenuationDistance ?? 1.8),
    envMapIntensity: opts.envMapIntensity ?? 1,
  });
  material.name = 'FracturedFreshwaterIce';
  patchStandard(material, {
    name: 'ice:volume',
    uniforms: {
      uIceOptical: { value: optical },
      uIceSeed: { value: (opts.seed ?? 47) % 997 },
      uIceFrost: { value: clamp(opts.frost ?? 0.35, 0, 1) },
    },
    vertexHead: 'attribute float aIceEdge, aIcePath, aIceChip;\nvarying vec3 vIceLocal;\nvarying vec3 vIceFace;\nvarying vec2 vIceUV;\nvarying float vIceEdge, vIcePath, vIceChip;',
    vertexBody: 'vIceLocal = position; vIceFace = normal; vIceUV = uv; vIceEdge = aIceEdge; vIcePath = aIcePath; vIceChip = aIceChip;',
    fragmentHead: `
      uniform sampler2D uIceOptical;
      uniform float uIceSeed, uIceFrost;
      varying vec3 vIceLocal, vIceFace;
      varying vec2 vIceUV;
      varying float vIceEdge, vIcePath, vIceChip;
      vec3 iceSurfaceBump(vec3 eye, vec3 n, float height) {
        vec3 dx = dFdx(eye), dy = dFdy(eye);
        vec3 a = cross(dy, n), b = cross(n, dx);
        float determinant = dot(dx, a);
        if (abs(determinant) < 1e-12) return n;
        return normalize(abs(determinant) * n - sign(determinant)
          * (dFdx(height) * a + dFdy(height) * b));
      }
    `,
    fragmentBody: `
      vec4 iceOptical = texture2D(uIceOptical, vIceUV);
      float iceTop = smoothstep(0.3, 0.9, normalize(vIceFace).y);
      float iceFrost = clamp((1.0 - iceOptical.r) / 0.96, 0.0, 1.0) * iceTop;
      float iceEdge = 1.0 - smoothstep(0.006, 0.04, vIceEdge);
      float iceRime = iceEdge * iceTop * uIceFrost * 0.28;
      float iceCover = max(max(iceFrost, iceRime), vIceChip * 0.72);
      float iceFaceFade = 1.0 - smoothstep(0.35, 1.2, length(fwidth(vIceLocal)) * 110.0);
      float iceFaceGrain = mix(0.5, astraNoise2(vec2(vIceLocal.x + vIceLocal.z,
        vIceLocal.y * 0.85) * 110.0), iceFaceFade);
      float iceFracture = (1.0 - abs(normalize(vIceFace).y)) * 0.24;
      float iceHairline = iceOptical.a * iceTop;
      iceCover = max(iceCover, iceHairline * 0.85);
      diffuseColor.rgb = mix(diffuseColor.rgb, vec3(0.77, 0.84, 0.85), iceCover);
      // Fine etched surface relief vanishes before it becomes a pixel grid.
      vec2 iceP = mix(vec2(vIceLocal.x + vIceLocal.z, vIceLocal.y * 0.85),
        vIceLocal.xz, iceTop) * 65.0 + uIceSeed;
      float iceFineFade = 1.0 - smoothstep(0.25, 1.0, length(fwidth(iceP)));
      float iceFine = astraNoise2(iceP) * iceFineFade;
      float iceHeight = iceCover * (0.0005 + iceFine * 0.0008)
        + iceFine * 0.000025 + iceFracture * iceFaceGrain * 0.000015;
    `,
    roughnessBody: 'roughnessFactor = mix(roughnessFactor + iceFracture, 0.86, iceCover);',
    normalBody: 'normal = iceSurfaceBump(-vViewPosition, normal, iceHeight);',
    transmissionBody: `
      material.thickness = max(0.001, vIcePath);
      material.transmission = transmission * (1.0 - iceCover * 0.96) * (1.0 - iceFracture);
    `,
  });
  return material;
}

/**
 * A Group with a closed volume mesh and optional instanced trapped air.
 * size number/[width,depth] (default [8,6]), thickness metres (.28),
 * outline optional convex [[x,z],...] polygon inside size, crackDensity
 * sites per square metre (.65; a very large sheet gets coarser floes), gap metres
 * (.018), frost/bubbles/chipping 0..1 (.35/.45/.4), heave metres (0), seed (47),
 * color/attenuationColor, attenuationDistance metres (1.8), roughness (.075).
 *
 * Nominal top is local y=0, bottom near -thickness; heave lifts and tilts
 * individual floes. sampleHeight(x,z) returns the actual LOCAL upper triangle
 * height, or null in an open fissure/outside the sheet. update(t,dt) is a
 * static no-op; dispose() releases only construction-owned resources.
 *
 * Optical path is estimated from local thickness on the top and bottom,
 * and floe width on fracture faces. It is not an exact volume exit trace.
 * Physical transmission sees opaque scene objects. Transparent water and
 * other transmissive volumes are not recursively ray traced by this renderer.
 * A lakebed or opaque water backing supplies that view. Ice does not cast
 * opaque shadows by default (castShadow:true explicitly opts in).
 */
export function makeFracturedIce(opts = {}) {
  for (const key of ['gap', 'frost', 'bubbles', 'chipping', 'heave', 'seed', 'roughness',
    'transmission', 'attenuationDistance', 'envMapIntensity'])
    if (opts[key] !== undefined && !Number.isFinite(opts[key]))
      throw new RangeError(`ice ${key} must be finite`);
  let outline = null;
  if (opts.outline !== undefined) {
    if (!Array.isArray(opts.outline) || opts.outline.length < 3 || opts.outline.length > 128
        || !opts.outline.every(p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite)))
      throw new RangeError('ice outline must contain 3..128 finite [x,z] points');
    outline = opts.outline.map(p => [...p]);
    const turns = outline.map((a, i) => {
      const b = outline[(i + 1) % outline.length], c = outline[(i + 2) % outline.length];
      return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
    });
    if (!turns.every(t => t > 1e-10) && !turns.every(t => t < -1e-10))
      throw new RangeError('ice outline must be strictly convex and ordered around its boundary');
    if (turns[0] < 0) outline.reverse();
    if (outline.some((a, i) => {
      const b = outline[(i + 1) % outline.length];
      return outline.some(p => (b[0] - a[0]) * (p[1] - a[1])
        - (b[1] - a[1]) * (p[0] - a[0]) < -1e-10);
    })) throw new RangeError('ice outline must not intersect itself');
  }
  const size = Array.isArray(opts.size) ? opts.size : opts.size !== undefined
    ? [opts.size, opts.size] : outline
      ? [2 * Math.max(...outline.map(p => Math.abs(p[0]))), 2 * Math.max(...outline.map(p => Math.abs(p[1])))]
      : [8, 6];
  if (size.length !== 2 || !size.every(value => Number.isFinite(value) && value > 0))
    throw new RangeError('ice size must contain positive finite metres');
  const [width, depth] = size;
  if (outline?.some(p => Math.abs(p[0]) > width / 2 || Math.abs(p[1]) > depth / 2))
    throw new RangeError('ice outline must lie inside the requested size');
  const thickness = opts.thickness ?? 0.28;
  if (!Number.isFinite(thickness) || thickness <= 0) throw new RangeError('ice thickness must be positive finite metres');
  const seed = opts.seed ?? 47, rand = mulberry32(seed);
  const density = opts.crackDensity ?? 0.65;
  if (!Number.isFinite(density) || density < 0) throw new RangeError('ice crackDensity must be finite and nonnegative');
  const count = clamp(Math.round(width * depth * density), 1, 180);
  const gap = clamp(opts.gap ?? 0.018, 0,
    Math.min(Math.min(width, depth) * 0.08, Math.sqrt(width * depth / count) * 0.2));
  const frost = clamp(opts.frost ?? 0.35, 0, 1), heave = Math.max(0, opts.heave ?? 0);
  const chipping = clamp(opts.chipping ?? 0.4, 0, 1);
  const thicknessAt = (x, z) => thickness * (0.9
    + fbm2(x * 0.72, z * 0.72, { seed: seed + 711, octaves: 3 }) * 0.5);
  const maximumThickness = thickness * 1.3;
  const sites = [];
  // Stratification keeps the cells distributed without regular crack lines.
  const columns = Math.max(1, Math.round(Math.sqrt(count * width / depth)));
  const rows = Math.ceil(count / columns);
  for (let i = 0; i < count; i++) sites.push([
    ((i % columns + 0.12 + rand() * 0.76) / columns - 0.5) * width,
    ((Math.floor(i / columns) + 0.12 + rand() * 0.76) / rows - 0.5) * depth,
  ]);
  // Some pressure fractures split an existing large plate. Keeping both
  // broad floes and small chips avoids a uniformly sized paving pattern.
  const cellScale = Math.sqrt(width * depth / count);
  for (let i = 3; i < sites.length; i += 4) {
    const anchor = sites[i - 2], angle = rand() * Math.PI * 2;
    sites[i] = [clamp(anchor[0] + Math.cos(angle) * cellScale * 0.25, -width * 0.49, width * 0.49),
      clamp(anchor[1] + Math.sin(angle) * cellScale * 0.25, -depth * 0.49, depth * 0.49)];
  }
  const boundaryCenter=outline ? outline.reduce((sum,p)=>[sum[0]+p[0]/outline.length,
    sum[1]+p[1]/outline.length],[0,0]) : [0,0];
  // One deformation for every shared point. Sampling an inset cell first,
  // or scaling this by its radius, lets neighboring boundaries cross.
  const deform=(x,z)=>{
    const margin=Math.max(0,outline?distanceToEdge(outline,[x,z])
      :Math.min(width/2-Math.abs(x),depth/2-Math.abs(z)));
    const strength=Math.min(1,margin/.08)*Math.min(1,cellScale/.25);
    const warpX=fbm2(x*2.2,z*2.2,{seed:seed+907,octaves:3})*.13
      +fbm2(x*17,z*17,{seed:seed+813,octaves:2})*.014;
    const warpZ=fbm2(x*2.2,z*2.2,{seed:seed+1009,octaves:3})*.13
      +fbm2(x*17,z*17,{seed:seed+1213,octaves:2})*.014;
    const chip=(1-Math.min(1,margin/.035))*(.002+Math.abs(warpX+warpZ)*.8)
      *Math.min(1,cellScale/.25);
    const length=Math.max(1e-9,Math.hypot(boundaryCenter[0]-x,boundaryCenter[1]-z));
    return [x+warpX*strength+(boundaryCenter[0]-x)/length*chip,
      z+warpZ*strength+(boundaryCenter[1]-z)/length*chip];
  };
  const position = [], uv = [], edge = [], path = [], chips = [], topTriangles = [], cells = [], cracks = [];
  let opticalWidth = thickness, triangleChip = 0;
  const vertex = (p, e, vertical) => {
    position.push(...p); uv.push(p[0] / width + 0.5, p[2] / depth + 0.5); edge.push(e);
    path.push(THREE.MathUtils.lerp(opticalWidth, thicknessAt(p[0], p[2]), vertical));
    chips.push(triangleChip);
  };
  const triangle = (a, b, c, ea, eb, ec, top = false) => {
    const normal = new THREE.Vector3().subVectors(new THREE.Vector3(...b), new THREE.Vector3(...a))
      .cross(new THREE.Vector3().subVectors(new THREE.Vector3(...c), new THREE.Vector3(...a))).normalize();
    const vertical = Math.abs(normal.y);
    vertex(a, ea, vertical); vertex(b, eb, vertical); vertex(c, ec, vertical);
    if (top) topTriangles.push([a, b, c]);
  };
  for (const [id, site] of sites.entries()) {
    let polygon = outline || [[-width / 2, -depth / 2], [width / 2, -depth / 2],
      [width / 2, depth / 2], [-width / 2, depth / 2]];
    for (const other of sites) if (other !== site) {
      const nx = other[0] - site[0], nz = other[1] - site[1];
      polygon = clip(polygon, nx, nz,
        (other[0] * other[0] + other[1] * other[1] - site[0] * site[0] - site[1] * site[1]) * 0.5);
    }
    const cellGap=gap*(0.38+rand()*.25);
    if (polygon.length < 3) continue;
    const center = polygon.reduce((sum, p) => [sum[0] + p[0] / polygon.length,
      sum[1] + p[1] / polygon.length], [0, 0]);
    let radius = distanceToEdge(polygon, center);
    // A shared domain warp makes neighboring fracture sides interlock.
    // Independent edge noise would overlap floes or widen cracks randomly.
    const corners = polygon.map(p => [...p]), jagged = [];
    for (let i = 0; i < polygon.length; i++) {
      const a = polygon[i], b = polygon[(i + 1) % polygon.length];
      const steps = clamp(Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / 0.065), 1, 48);
      for (let j = 0; j < steps; j++) {
        const x = a[0] + (b[0] - a[0]) * j / steps;
        const z = a[1] + (b[1] - a[1]) * j / steps;
        jagged.push(deform(x,z));
      }
    }
    polygon = jagged;
    // A point in every inward half-plane is in the polygon's visibility
    // kernel. Shrinking toward it therefore stays inside the shared cell,
    // even when a fracture boundary is concave. Tiny degenerate chips vanish.
    const kernel=inset(polygon,0);
    if(kernel.length<3)continue;
    center[0]=kernel.reduce((sum,p)=>sum+p[0]/kernel.length,0);
    center[1]=kernel.reduce((sum,p)=>sum+p[1]/kernel.length,0);
    radius=distanceToEdge(polygon,center);
    if(radius<1e-7)continue;
    const shrink=Math.min(.45,cellGap/radius);
    polygon=polygon.map(p=>[THREE.MathUtils.lerp(p[0],center[0],shrink),
      THREE.MathUtils.lerp(p[1],center[1],shrink)]);
    radius*=1-shrink;
    opticalWidth = Math.max(thickness, radius * 1.7);
    const bevel = Math.min(thickness * (0.015 + chipping * 0.10),
      0.003 + chipping * 0.025, radius * 0.055);
    const lift = (rand() - 0.3) * heave;
    const tiltX = (rand() - 0.5) * heave / Math.max(radius, 0.1) * 0.32;
    const tiltZ = (rand() - 0.5) * heave / Math.max(radius, 0.1) * 0.32;
    const topAt = p => lift + (p[0] - center[0]) * tiltX + (p[1] - center[1]) * tiltZ;
    // Radial bevel retains a one-to-one ring topology even at acute corners.
    const cuts=polygon.map(p=>.35+Math.abs(fbm2(p[0]*9,p[1]*9,{seed:seed+337,octaves:2}))*2.4);
    const inner = polygon.map((p,i) => [p[0] + (center[0] - p[0]) * bevel * cuts[i] / radius,
      p[1] + (center[1] - p[1]) * bevel * cuts[i] / radius]);
    const top = inner.map(p => [p[0], topAt(p), p[1]]);
    const rim = polygon.map((p,i) => [p[0], topAt(p) - bevel * cuts[i], p[1]]);
    // Fracture planes widen and undercut at different depths. Every ring
    // moves inward from the shared footprint, so neighboring solids fit.
    const insetPoint=(p,i,amount)=>{
      const move=Math.min(radius*.08,thickness*amount)*cuts[i];
      return [p[0]+(center[0]-p[0])*move/radius,p[1]+(center[1]-p[1])*move/radius];
    };
    const mid = polygon.map((p,i)=>{const q=insetPoint(p,i,.09);
      return [q[0],topAt(q)-thicknessAt(...q)*(.38+cuts[i]*.09),q[1]];});
    const bottom = polygon.map((p,i)=>{const q=insetPoint(p,i,.04);
      return [q[0],topAt(q)-thicknessAt(...q),q[1]];});
    const ct = [center[0], topAt(center), center[1]];
    const cb = [center[0], topAt(center) - thicknessAt(...center), center[1]];
    for (let i = 0; i < polygon.length; i++) {
      const j = (i + 1) % polygon.length;
      triangleChip=0;
      triangle(ct, top[j], top[i], radius, bevel, bevel, true);
      triangle(cb, bottom[i], bottom[j], radius, 0, 0);
      triangleChip=clamp(((cuts[i]+cuts[j])*.5-.65)*2.5,0,1)*chipping;
      triangle(top[i], top[j], rim[j], bevel, bevel, 0, true);
      triangle(top[i], rim[j], rim[i], bevel, 0, 0, true);
      triangleChip*=.25;
      triangle(rim[i], rim[j], mid[j], 0, 0, 0);
      triangle(rim[i], mid[j], mid[i], 0, 0, 0);
      triangleChip*=.3;
      triangle(mid[i], mid[j], bottom[j], 0, 0, 0);
      triangle(mid[i], bottom[j], bottom[i], 0, 0, 0);
    }
    cells.push({ id, polygon, center, topAt, radius });
    for(let branch=0;branch<2;branch++) {
      const start=corners[Math.floor(rand()*corners.length)];
      const bend=[THREE.MathUtils.lerp(start[0],center[0],.45+rand()*.2),
        THREE.MathUtils.lerp(start[1],center[1],.45+rand()*.2)];
      const end=[THREE.MathUtils.lerp(start[0],center[0],.78+rand()*.13),
        THREE.MathUtils.lerp(start[1],center[1],.78+rand()*.13)];
      cracks.push([start,bend],[bend,end]);
      const fork=corners[(corners.indexOf(start)+1)%corners.length];
      cracks.push([bend,[THREE.MathUtils.lerp(bend[0],fork[0],.25),
        THREE.MathUtils.lerp(bend[1],fork[1],.25)]]);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(position, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
  geometry.setAttribute('aIceEdge', new THREE.Float32BufferAttribute(edge, 1));
  geometry.setAttribute('aIcePath', new THREE.Float32BufferAttribute(path, 1));
  geometry.setAttribute('aIceChip', new THREE.Float32BufferAttribute(chips, 1));
  geometry.computeVertexNormals(); geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  const optical = opticalTexture(width, depth, thicknessAt, maximumThickness, frost, seed, cracks);
  const volume = new THREE.Mesh(geometry, iceMaterial(opts, optical, maximumThickness));
  volume.name = 'IceVolume'; volume.castShadow = opts.castShadow === true; volume.receiveShadow = true;
  const group = new THREE.Group(); group.name = opts.name ?? 'FracturedIce';
  group.userData.placement = volume.userData.placement = 'free';
  group.add(volume);

  // Flattened gas lenses occur in columns where bubbles rose before the
  // water froze. Their depth is real geometry, not dots on the top texture.
  const bubbles = clamp(opts.bubbles ?? 0.45, 0, 1);
  const bubbleCount = clamp(Math.round(width * depth * bubbles * 11), 0, 4000);
  if (bubbleCount && cells.length) {
    const bubbleGeometry = new THREE.SphereGeometry(1, 12, 6);
    const bubbleMaterial = new THREE.MeshStandardMaterial({ color: 0x758f93, roughness: 0.35 });
    const air = new THREE.InstancedMesh(bubbleGeometry, bubbleMaterial, bubbleCount);
    air.name = 'TrappedAir'; const dummy = new THREE.Object3D();
    let cluster = null, remaining = 0;
    for (let i = 0; i < bubbleCount; i++) {
      if (remaining === 0) {
        const cell = cells[Math.floor(rand() * cells.length)];
        const direction = rand() * Math.PI * 2, spread = Math.sqrt(rand()) * cell.radius * 0.55;
        cluster = { cell, x: cell.center[0] + Math.cos(direction) * spread,
          z: cell.center[1] + Math.sin(direction) * spread,
          size: 0.004 + Math.pow(rand(), 3) * 0.026 };
        remaining = 1 + Math.floor(rand() * 5);
      }
      const { cell } = cluster;
      const drift = Math.min(0.05, cell.radius * 0.12);
      const x = cluster.x + (rand() - 0.5) * drift, z = cluster.z + (rand() - 0.5) * drift;
      const radius = Math.min(thickness * 0.085, cell.radius * 0.06,
        cluster.size * (0.45 + rand() * 0.95));
      const localDepth = thicknessAt(x, z);
      dummy.position.set(x, cell.topAt([x, z]) - localDepth * (0.16 + rand() * 0.68), z);
      dummy.scale.set(radius, radius * (0.10 + rand() * 0.18), radius * (0.7 + rand() * 0.5));
      dummy.rotation.set((rand() - 0.5) * 0.18, rand() * Math.PI, (rand() - 0.5) * 0.18);
      dummy.updateMatrix(); air.setMatrixAt(i, dummy.matrix);
      remaining--;
    }
    air.instanceMatrix.needsUpdate = true; air.computeBoundingSphere();
    air.userData.placement = 'free'; group.add(air);
  }
  // Float32 coordinates are exactly what the GPU and raycaster see.
  for (const face of topTriangles) for (const point of face)
    for (let i = 0; i < 3; i++) point[i] = Math.fround(point[i]);
  group.userData.sampleHeight = (x, z) => {
    let result = null;
    for (const [a, b, c] of topTriangles) {
      const denominator = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2]);
      if (Math.abs(denominator) < 1e-12) continue;
      const wa = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / denominator;
      const wb = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / denominator;
      const wc = 1 - wa - wb;
      if (Math.min(wa, wb, wc) >= -1e-7) {
        const y = wa * a[1] + wb * b[1] + wc * c[1];
        result = result === null ? y : Math.max(result, y);
      }
    }
    return result;
  };
  group.userData.floeCount = cells.length;
  group.userData.floeOutlines = cells.map(cell => cell.polygon.map(point => [...point]));
  group.userData.seed = seed;
  group.userData.update = () => {};
  const owned = snapshotResources(group); owned.add(optical);
  return attachDisposal(group, owned);
}
