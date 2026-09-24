// Cloud layers: ONE InstancedMesh of billboard puffs (fbm alpha, flat
// base, sun-tinted) + optional thin cirrus streaks. three r184's Sky
// cloud branch grid-artifacts at any high contrast — this is the layer.
import * as THREE from 'three';
import { attachDisposal, snapshotResources } from './lifecycle.js';
import { mulberry32, fbm2 } from './noise.js';
import { makeShaderMaterial, keepOutOfDepthPasses, sunVector } from './shader.js';

// Cumulus/cirrus mood presets, graded for THIS renderer: ACES filmic at
// exposure 1.0, sRGB out, and nothing after that rescues a dim top or
// pulls a tint back (the post chain's bloom takes only EMISSION, which a
// cloud has none of, and its grade is identity unless the scene asks).
// A lit cumulus top is the brightest diffuse thing in a daylight frame,
// so `sunColor` is a near-WHITE with a warm bias — the old 0xffc890 sun
// tint was graded against a bloom+grade chain and lands as tan smoke
// here (measured: cloud region (224,233,236) against a (232,239,242)
// sky, 3% contrast). `shadeColor` is the sky-lit underside, never
// neutral grey: it is where the blue comes from.
const PRESETS = {
  day: { sunColor: 0xfff4e6, shadeColor: 0x7e94b8, litGain: 1.55 },
  golden: { sunColor: 0xffd0a0, shadeColor: 0x7c82ac, litGain: 1.70 },
  // A sheet, not a field of dark clumps: wide overlapping puffs at one
  // altitude, and the flattest lighting of the four.
  overcast: { sunColor: 0xbcc3cc, shadeColor: 0x717b8a, litGain: 1.05,
              count: 40, altitude: 230, spread: 45, alpha: 0.97,
              stretch: 1.9 },
  // Moonlit: dark but LIT — a cool silver top over a deep blue base,
  // never the black cut-out a plain brightness scale gives.
  night: { sunColor: 0xa8bfe0, shadeColor: 0x2c3652, litGain: 0.60,
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
  if (!v || !v.isVector3) return new THREE.Vector3(0, 1, 0);
  const d = v.clone().normalize();
  if (d.y >= -0.02) return d;
  const el = Math.asin(-d.y);                    // how deep the sun is
  const az = Math.atan2(-d.z, -d.x) * 180 / Math.PI;   // opposite azimuth
  return sunVector(az, Math.max(30, Math.min(55, 25 + el * 180 / Math.PI)));
}

/** Seeded billowy puff: RGB stores a shading normal, alpha optical coverage.
 * The base is flat. This is a distant impostor, not a participating volume.
 */
export function cloudTexture(seed = 7, size = 256) {
  const texture = puffTexture(seed, size, false);
  // Public alpha texture remains white for callers using it as a color map.
  const data = texture.image.data;
  for (let i = 0; i < data.length; i += 4) data[i] = data[i + 1] = data[i + 2] = 255;
  return texture;
}

function puffTexture(seed, size, cirrus) {
  if (!Number.isSafeInteger(seed) || !Number.isInteger(size) || size < 16 || size > 1024) {
    throw new RangeError('cloudTexture: use an integer seed and size in 16..1024');
  }
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
    if (cirrus) {
      // Wind-sheared ice filaments with curved, tapered ends. Unlike stretching
      // a round cumulus stamp, these never make parallel cylindrical ribbons.
      const taper = Math.pow(Math.max(0, 1 - Math.pow(u / .48, 2)), 1.8);
      const bend = .20 * Math.sin((u + .35) * 3.8) - .06;
      let threads = 0;
      for (let k = 0; k < 7; k++) {
        const center = bend + (k - 3) * .045 + .035 * Math.sin(u * 9 + k * 1.7);
        const width = .011 + .024 * taper;
        threads += Math.exp(-Math.pow((v - center) / width, 2)) * (.07 + .07 * Math.sin(k * 2.13 + 1));
      }
      field[y * size + x] = threads * taper * border * (.72 + .28 * broad);
    }
  }
  const data = new Uint8Array(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const h = field[y * size + x];
    const at = (dx,dy) => field[Math.max(0,Math.min(size-1,y+dy))*size + Math.max(0,Math.min(size-1,x+dx))];
    const nx = -(at(1,0)-at(-1,0)) * size * .045;
    const ny = -(at(0,1)-at(0,-1)) * size * .045;
    const len = Math.hypot(nx,ny,1),k=(y*size+x)*4;
    data[k] = (nx/len*.5+.5)*255;
    data[k+1] = (ny/len*.5+.5)*255;
    data[k+2] = (1/len*.5+.5)*255;
    data[k+3] = (1-Math.exp(-h*2.0))*255;
  }
  const tex = new THREE.DataTexture(data, size, size);
  tex.needsUpdate = true;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}

function cloudAtlas(seed, cirrus = false) {
  const tile = 128, cols = 4, rows = 2;
  const data = new Uint8Array(tile * tile * cols * rows * 4);
  for (let i=0;i<cols*rows;i++) {
    const puff = puffTexture(seed + i*977, tile, cirrus);
    for (let y=0;y<tile;y++) {
      const start=((Math.floor(i/cols)*tile+y)*tile*cols+(i%cols)*tile)*4;
      data.set(puff.image.data.subarray(y*tile*4,(y+1)*tile*4),start);
    }
    puff.dispose();
  }
  const tex = new THREE.DataTexture(data,tile*cols,tile*rows);
  tex.minFilter=tex.magFilter=THREE.LinearFilter;
  tex.needsUpdate=true;
  return tex;
}

/**
 * Cumulus layer as ONE instanced billboard mesh (one draw call).
 * opts: seed, preset ('day'|'golden'|'overcast'|'night'), count, area,
 * altitude, spread, sunDir (THREE.Vector3 toward the sun — the lit side
 * follows it, and a sun already below the horizon becomes the moon),
 * sunColor, shadeColor, alpha, wind (m/s along +x), rim (silver-lining
 * strength, 0.55), haze (how far the deck may recede into scene.fog,
 * 0.22 — the fog COLOUR is always the scene's), hueVariance (0.13).
 * Animate cloud drift from tick(): `layer.userData.update(t)`.
 */
export function makeClouds(opts = {}) {
  const p = PRESETS[opts.preset || 'day'] || PRESETS.day;
  const seed = opts.seed ?? 7;
  const count = opts.count ?? p.count ?? 14;
  const area = opts.area ?? 2600;
  const altitude = opts.altitude ?? p.altitude ?? 260;
  const spread = opts.spread ?? p.spread ?? 120;
  const alpha = opts.alpha ?? p.alpha ?? 0.92;
  const wind = opts.wind ?? 3.0;
  const stretch = opts.stretch ?? p.stretch ?? 1.0;
  if (!Number.isSafeInteger(seed) || !Number.isInteger(count) || count < 0 || count > 1000 ||
      ![area, altitude, spread, alpha, wind, stretch].every(Number.isFinite) ||
      area <= 0 || spread < 0 || alpha < 0 || alpha > 1 || stretch <= 0) {
    throw new RangeError('makeClouds: invalid seed, count, area, altitude, spread, alpha, wind or stretch');
  }
  const rand = mulberry32(seed);
  const rnd2 = mulberry32(seed + 977);
  const puffs = [];
  for (let c = 0; c < count; c++) {
    const cx = (rand() - 0.5) * area;
    const cz = (rand() - 0.5) * area - area * 0.18;
    const cy = altitude + (rand() - 0.5) * spread;
    const w = 130 + rand() * 240;
    const n = 5 + Math.floor(rand() * 5);
    const cluster = [];
    for (let q = 0; q < n; q++) {
      const t = (q + 0.5) / n - 0.5;
      cluster.push({
        x: cx + t * w * (1.5 + rand() * 0.5),
        y: cy + (rand() - 0.35) * w * 0.22 - Math.abs(t) * w * 0.3,
        z: cz + (rand() - 0.5) * w * 0.35,
        s: w * (0.55 + rand() * 0.5) * (1 - Math.abs(t) * 0.75),
        b: 0.75 + rand() * 0.25,
        ph: rand() * Math.PI * 2,
        h: 0,
      });
    }
    // A CROWN over the middle of the row. A single line of puffs is a
    // caterpillar, not a cumulus: the silhouette a cumulus is recognised
    // by is a flat base under a stack of smaller lobes.
    // Its own stream, so adding the crown did not reshuffle the deck a
    // caller already framed a shot around.
    const crown = 1 + Math.floor(rnd2() * 3);
    for (let q = 0; q < crown; q++) {
      const t = (rnd2() - 0.5) * 0.55;
      const up = 0.30 + rnd2() * 0.34;
      cluster.push({
        x: cx + t * w * 1.6,
        y: cy + w * up * 0.55,
        z: cz + (rnd2() - 0.5) * w * 0.3,
        s: w * (0.36 + rnd2() * 0.30),
        b: 0.85 + rnd2() * 0.15,
        ph: rnd2() * Math.PI * 2,
        h: 0,
      });
    }
    // Where each puff sits inside ITS OWN cluster, 0 at the base and 1
    // at the crown. Without it every billboard carries the same top-lit
    // gradient and a cluster reads as a bag of identical blobs instead
    // of one mass with a shaded underside — the single biggest tell of
    // billboard clouds.
    const lo = Math.min(...cluster.map((q) => q.y));
    const hi = Math.max(...cluster.map((q) => q.y));
    for (const q of cluster) {
      q.h = (q.y - lo) / Math.max(1e-3, hi - lo);
      puffs.push(q);
    }
  }
  const geo = new THREE.PlaneGeometry(1, 1);
  const inst = new THREE.InstancedBufferGeometry();
  inst.index = geo.index;
  // `position` stays at the ORIGIN and the corner offsets ride a custom
  // attribute: GTAOPass redraws the scene with an override material that
  // does NOT run this vertex shader, so a real 1x1 quad here burns a
  // hard-edged black rectangle into AO at world origin (measured).
  inst.attributes.position = new THREE.BufferAttribute(
      new Float32Array(geo.attributes.position.count * 3), 3);
  inst.setAttribute('aCorner', geo.attributes.position);
  inst.attributes.uv = geo.attributes.uv;
  inst.instanceCount = puffs.length;
  const off = new Float32Array(puffs.length * 3);
  const sc = new Float32Array(puffs.length * 2);
  const ex = new Float32Array(puffs.length * 3);
  puffs.forEach((q, i) => {
    off[i * 3] = q.x; off[i * 3 + 1] = q.y; off[i * 3 + 2] = q.z;
    sc[i * 2] = q.s * stretch; sc[i * 2 + 1] = q.s * 0.62 / Math.max(1, stretch * 0.45);
    ex[i * 3] = q.b; ex[i * 3 + 1] = q.ph; ex[i * 3 + 2] = q.h;
  });
  inst.setAttribute('iOff', new THREE.InstancedBufferAttribute(off, 3));
  inst.setAttribute('iScale', new THREE.InstancedBufferAttribute(sc, 2));
  inst.setAttribute('iExtra', new THREE.InstancedBufferAttribute(ex, 3));
  // Toward the sun. `sunDir` was documented and then ignored: every puff
  // was lit from straight up, so a low sun and a high sun painted the
  // same cloud. Default +Y keeps that old top-lit behaviour when a
  // caller has no rig to hand.
  const sunDir = lightVector(opts.sunDir);
  const mat = makeShaderMaterial({
    name: opts.name || 'CumulusLayer',
    transparent: true,
    depthWrite: false,
    // NOT three's fog. A cloud deck sits 300-1500 m out, and a scene's
    // FogExp2 is authored for scene-scale geometry: at the density a
    // 120 m courtyard uses (0.0035) the whole deck resolves to 100% fog
    // colour — measured, the layer above was invisible on this host
    // (darkest cloud pixel = the fog colour exactly). The layer opts out
    // of the chunk and mixes its OWN bounded aerial perspective toward
    // `fogColor` below, so it still recedes into the scene's haze and in
    // the scene's hue, but never disappears into it.
    fog: false,
    uniforms: {
      // three refreshes these itself once `mat.fog = true` (below) —
      // the shader keeps the scene's fog COLOUR and density without
      // taking three's fog blend.
      ...THREE.UniformsUtils.clone(THREE.UniformsLib.fog),
      map: { value: cloudAtlas(seed + 101, opts._cirrus === true) },
      uCirrus: { value: opts._cirrus ? 1 : 0 },
      uWind: { value: wind },
      uCameraRight: { value: new THREE.Vector3(1, 0, 0) },
      uCameraUp: { value: new THREE.Vector3(0, 1, 0) },
      uCameraForward: { value: new THREE.Vector3(0, 0, 1) },
      uSunLocal: { value: sunDir.clone() },
      uAlpha: { value: alpha },
      uLitGain: { value: opts.litGain ?? p.litGain },
      uSunDir: { value: sunDir },
      uRim: { value: opts.rim ?? 0.55 },
      uHazeMax: { value: opts.haze ?? 0.22 },
      uHazeScale: { value: 0.35 },
      uHue: { value: opts.hueVariance ?? 0.13 },
      uSunColor: {
        value: new THREE.Color(opts.sunColor ?? p.sunColor),
      },
      uShade: {
        value: new THREE.Color(opts.shadeColor ?? p.shadeColor),
      },
    },
    varyings: [
      'varying vec2 vUv; varying vec3 vExtra;',
      'varying vec2 vSun; varying float vDepth; varying float vSunZ;',
    ].join('\n'),
    vertexHead: [
      'attribute vec3 aCorner;',
      'attribute vec3 iOff; attribute vec2 iScale; attribute vec3 iExtra;',
      'uniform float uWind; uniform vec3 uSunDir; uniform float uCirrus;',
      'uniform vec3 uCameraRight; uniform vec3 uCameraUp;',
      'uniform vec3 uCameraForward; uniform vec3 uSunLocal;',
    ].join('\n'),
    vertexMain: [
      '  vExtra = iExtra;',
      // Half the puffs wear the puff texture mirrored: one alpha map
      // repeated at N scales is a recognisable stamp, and a free u-flip
      // doubles the silhouettes. The base cut runs in v, so mirroring u
      // cannot lift a cloud off its own flat bottom.
      '  float flip = step(0.5, fract(iExtra.y * 0.1591));',
      '  vUv = vec2(mix(uv.x, 1.0 - uv.x, flip), uv.y);',
      '  vec3 right = uCameraRight;',
      '  vec3 up = uCameraUp;',
      '  vec3 p = iOff + vec3(uTime * uWind',
      '      + sin(iExtra.y + uTime * 0.05) * 6.0, 0.0, 0.0);',
      '  float lean = uCirrus * sin(iExtra.y * 4.1) * .14;',
      '  vec2 corner = mat2(cos(lean),sin(lean),-sin(lean),cos(lean))',
      '      * (aCorner.xy * iScale);',
      '  transformed = p + right * corner.x + up * corner.y;',
      // The sun in the billboard's own 2D frame: the lit side then
      // follows the rig instead of always being the top edge.
      '  vec2 sp = vec2(dot(uSunLocal, right), dot(uSunLocal, up));',
      '  vSun = length(sp) > 1e-3 ? normalize(sp) : vec2(0.0, 1.0);',
      // the lit side is a world direction: mirror it with the uv or the
      // light lands on the wrong half of every flipped puff
      '  vSun.x *= 1.0 - 2.0 * flip;',
      // ...and whether it is in front of or behind the deck. A cloud
      // seen against the sun is a DARK body with a burning rim; the
      // same cloud with the sun over the shoulder is a white one. One
      // dot product decides which, and it is the difference between a
      // sky that looks photographed and one that looks painted.
      '  vSunZ = dot(uSunLocal, uCameraForward);',
      '  vDepth = -(modelViewMatrix * vec4(transformed, 1.0)).z;',
    ].join('\n'),
    fragmentHead: [
      'uniform sampler2D map; uniform vec3 uSunColor; uniform vec3 uShade;',
      'uniform float uAlpha; uniform float uLitGain; uniform float uRim;',
      'uniform float uHazeMax; uniform float uHazeScale; uniform float uHue;',
      'uniform vec3 fogColor;',
      'uniform float fogDensity; uniform float fogNear; uniform float fogFar;',
    ].join('\n'),
    fragmentMain: [
            '  float cell = floor(fract(vExtra.y * .7919) * 8.0);',
      '  vec2 atlasUv = (clamp(vUv,vec2(.004),vec2(.996)) + vec2(mod(cell,4.0),floor(cell/4.0))) / vec2(4,2);',
      '  vec4 puff = texture2D(map, atlasUv);',
      '  float a = puff.a;',
      '  if (a < 0.004) discard;',
      // Two lighting terms: the sun's own direction across the puff,
      // and a vertical term for the flat shaded base every cumulus has.
      '  vec3 normal = normalize(puff.rgb * 2.0 - 1.0);',
      '  vec3 light = normalize(vec3(vSun * sqrt(max(0.0,1.0-vSunZ*vSunZ)),vSunZ));',
      '  float incidence = max(dot(normal,light),0.0);',
      '  float front = smoothstep(-.4,.6,vSunZ);',
      '  float crown = mix(.68,1.0,vExtra.z);',
      '  float thickness = -log(max(1.0-a,.02));',
      '  float skyLit = smoothstep(-.45,.65,normal.y + .30*vExtra.z);',
      '  float direct = (.20 + .40*incidence + .36*skyLit) * crown * vExtra.x;',
      '  vec3 sunLit = uSunColor * uLitGain;',
      '  vec3 ambient = mix(uShade,fogColor,.20) * mix(.92,1.18,vExtra.z);',
      '  vec3 col = ambient * (.65 + .35*front) + sunLit * direct;',
      // A silver lining needs backlighting AND an optically thin path. The old
      // alpha-band highlight outlined every puff, producing gray soap bubbles.
      '  float silver = pow(max(-vSunZ,0.0),3.0) * thickness * exp(-thickness*1.8);',
      '  col += sunLit * (uRim * silver * 1.7);',
      // Hue variance: warm where the sun reaches, cool where it does
      // not, decorrelated per puff by its phase. A cloud deck of one
      // flat white is the giveaway of a painted sky.
      '  col = astraHueBreak(col, vUv * 2.2 + vExtra.y * 6.3, 1.0, uHue);',
      // Bounded aerial perspective (see the `fog: false` note above).
      '  float hz = 0.0;',
      '#ifdef USE_FOG',
      '#ifdef FOG_EXP2',
      '  float fd = fogDensity * uHazeScale * vDepth;',
      '  hz = 1.0 - exp(-fd * fd);',
      '#else',
      '  hz = clamp((vDepth - fogNear) / max(fogFar - fogNear, 1e-4),',
      '      0.0, 1.0) * uHazeScale * 2.0;',
      '#endif',
      '#endif',
      '  col = mix(col, fogColor, clamp(hz, 0.0, 1.0) * uHazeMax);',
      // A cloud is one long smooth ramp across hundreds of pixels: at 8
      // bits that bands. One LSB of hash noise costs nothing and kills
      // the rings.
      '  col += (astraHash21(gl_FragCoord.xy) - 0.5) * 0.004;',
      '  gl_FragColor = vec4(col, a * uAlpha);',
    ].join('\n'),
  });
  // Opted out of three's fog BLEND, opted in to its fog UNIFORMS: this
  // is what makes the renderer keep fogColor/fogDensity current.
  mat.fog = true;
  const mesh = new THREE.Mesh(inst, mat);
  mesh.name = opts.name || 'CumulusLayer';
  mesh.frustumCulled = false;
  const inverse = new THREE.Matrix4();
  mesh.onBeforeRender = (_renderer, _scene, camera) => {
    inverse.copy(mesh.matrixWorld).invert();
    mat.uniforms.uCameraRight.value.setFromMatrixColumn(camera.matrixWorld, 0).transformDirection(inverse);
    mat.uniforms.uCameraUp.value.setFromMatrixColumn(camera.matrixWorld, 1).transformDirection(inverse);
    mat.uniforms.uCameraForward.value.setFromMatrixColumn(camera.matrixWorld, 2).transformDirection(inverse);
    mat.uniforms.uSunLocal.value.copy(mat.uniforms.uSunDir.value).transformDirection(inverse);
  };
  mesh.userData.update = (t) => {
    if (!Number.isFinite(t)) throw new RangeError('CloudLayer.update: time must be finite');
    mat.uniforms.uTime.value = t;
  };
  const owned = snapshotResources(mesh);
  owned.add(mat.uniforms.map.value);
  return attachDisposal(keepOutOfDepthPasses(mesh), owned);
}

/**
 * Thin high-altitude streaks — the same instanced billboard layer,
 * stretched flat and thinned out.
 * opts: seed, preset, count, area, altitude, sunDir, sunColor.
 */
export function makeCirrus(opts = {}) {
  // Reuses the proven cumulus billboard shader as stretched streaks —
  // Sprites rendered as black slabs under the post chain (measured on
  // the manhattan run), and tilted planes went edge-on from low cams.
  const seed = opts.seed ?? 23;
  // Ice, not water: whiter and cooler than the cumulus of the same hour,
  // and thin enough that the light comes THROUGH — hence the wide rim.
  // Derived from the PRESET rather than fixed, or a night deck gets a
  // daylight-white streak across it (measured on the night render: the
  // cirrus band read as a light leak over a deep blue sky).
  const p = PRESETS[opts.preset || 'day'] || PRESETS.day;
  const white = new THREE.Color(0xffffff);
  const ice = new THREE.Color(p.sunColor).lerp(white, 0.35);
  const veil = new THREE.Color(p.shadeColor).lerp(white, 0.55);
  return makeClouds({
    // Its own name: two layers called CumulusLayer collide in the
    // census and in the viewer's find-by-name.
    name: opts.name ?? 'CirrusLayer',
    _cirrus: true,
    seed,
    count: opts.count ?? 8,
    area: opts.area ?? 3200,
    altitude: opts.altitude ?? 520,
    spread: 120,
    alpha: opts.alpha ?? 0.44,
    wind: opts.wind ?? 6.0,
    litGain: (opts.litGain ?? p.litGain) * 0.85,
    sunColor: opts.sunColor ?? ice.getHex(),
    shadeColor: opts.shadeColor ?? veil.getHex(),
    sunDir: opts.sunDir,
    rim: opts.rim ?? 0.85,
    hueVariance: opts.hueVariance ?? 0.08,
    stretch: opts.stretch ?? 4.5,
  });
}
