/**
 * The physical atmosphere dome — a graded Preetham sky in one call.
 * Contract: the dome only GLOWS — pair it with a key light on the
 * SAME sun vector (`sunRig()` from ./environment.js) — and it owns
 * the backdrop: `scene.background` stays null, never a second dome.
 *
 * Graded for THIS renderer (ACES filmic, exposure 1.0, no post chain,
 * measured 2026-09-01): three's Sky shader gamma-encodes its radiance
 * (`pow(x, 1/2.4)`) BEFORE the tone map, so on an exposure-1.0 canvas
 * the whole dome landed at (240,244,246) — a white card, and the one
 * defect every day frame carried.  The patch below keeps the Preetham
 * chroma and re-grades its transfer: zenith ~(105,152,195), horizon
 * ~(218,222,224), sun-side haze brighter, nothing blown.  Below the
 * horizon the un-encoded Preetham is black, so a night gradient (deep
 * blue zenith, fog-family horizon, an afterglow band toward the set
 * sun) fades in with solar depression — one dome serves day, golden,
 * dusk AND night.  `skyRadiance()` is the same model on the CPU, so
 * `sunRig()` can bake an environment map that matches what the dome
 * shows: reflections and the sky the camera sees agree by construction.
 */

import * as THREE from 'three';
import { Sky } from 'three/addons/objects/Sky.js';

/**
 * The battery-graded atmosphere and its transfer grade, shared with
 * `sunRig()`'s environment bake (import, never restate).
 * `gain`/`gamma`: retColor = gain * pow(tex, gamma / 2.4).
 */
export const SKY_GRADE = Object.freeze({
  turbidity: 4.5, rayleigh: 1.6, mieCoefficient: 0.0035, mieDirectionalG: 0.8,
  gain: 0.36, gamma: 1.7,
  // Night gradient, LINEAR (pre-ACES); solved against three's ACES so
  // the canvas reads zenith (9,13,32) / horizon (38,52,86).
  nightZenith: [0.0109, 0.0137, 0.0312],
  nightHorizon: [0.0368, 0.0526, 0.1052],
  afterglow: [0.1741, 0.0742, 0.0335],
  // Low-sun warmth multiplier toward the sun (golden hour).
  warm: [1.0, 0.74, 0.5],
});

/** 0 with the sun up, 1 once it is ~9 deg below the horizon. */
export function nightAmount(sunY) {
  return THREE.MathUtils.smoothstep(-sunY, 0.0, 0.15);
}

/** Twilight afterglow weight: strongest just after sunset, gone by -20 deg. */
export function afterglowAmount(sunY) {
  if (sunY >= 0) return 0.35 * (1 - THREE.MathUtils.smoothstep(sunY, 0.0, 0.1));
  return 1 - THREE.MathUtils.smoothstep(-sunY, 0.03, 0.34);
}

/** Golden-hour warmth weight: 1 at the horizon, 0 by 20 deg elevation. */
export function lowSunAmount(sunY) {
  return 1 - THREE.MathUtils.smoothstep(sunY, 0.02, 0.35);
}

const _tmp = new THREE.Vector3();

/**
 * The graded dome's LINEAR radiance for a view direction — three's
 * Sky.js (r182) shader on the CPU, with the same grade the patched dome
 * applies (gain/gamma, night gradient, afterglow, low-sun warmth).  No
 * solar disc: the caller decides how the sun reads in its bake.
 *
 * @param {THREE.Vector3} dir Unit view direction.
 * @param {THREE.Vector3} sunDir Unit sun direction (may be below the horizon).
 * @param {object} [opts] `turbidity`, `rayleigh`, `mieCoefficient`,
 *   `mieDirectionalG`, `gain`, `gamma` (SKY_GRADE defaults).
 * @returns {[number, number, number]} Linear RGB radiance.
 */
export function skyRadiance(dir, sunDir, opts = {}) {
  const T = opts.turbidity === undefined ? SKY_GRADE.turbidity : opts.turbidity;
  const rayleigh = opts.rayleigh === undefined ? SKY_GRADE.rayleigh : opts.rayleigh;
  const mieC = opts.mieCoefficient === undefined ? SKY_GRADE.mieCoefficient : opts.mieCoefficient;
  const mieG = opts.mieDirectionalG === undefined ? SKY_GRADE.mieDirectionalG : opts.mieDirectionalG;
  const gain = opts.gain === undefined ? SKY_GRADE.gain : opts.gain;
  const gamma = opts.gamma === undefined ? SKY_GRADE.gamma : opts.gamma;

  // --- vertex stage
  const zc = Math.max(-1, Math.min(1, sunDir.y));
  const sunE = 1000 * Math.max(0, 1 - Math.exp(-((1.6110731556870734 - Math.acos(zc)) / 1.5)));
  const sunfade = 1 - Math.min(1, Math.max(0, 1 - Math.exp(sunDir.y / 450000)));
  const rC = rayleigh - (1 - sunfade);
  const betaR = [5.804542996261093e-6 * rC, 1.3562911419845635e-5 * rC, 3.0265902468824876e-5 * rC];
  const mc = 0.434 * (0.2 * T) * 10e-18 * mieC;
  const betaM = [mc * 1.8399918514433978e14, mc * 2.7798023919660528e14, mc * 4.0790479543861094e14];

  // --- fragment stage
  const za = Math.acos(Math.max(0, dir.y));
  const inv = 1 / (Math.cos(za) + 0.15 * Math.pow(93.885 - (za * 180) / Math.PI, -1.253));
  const sR = 8.4e3 * inv, sM = 1.25e3 * inv;
  const cosT = dir.dot(sunDir);
  const rPhase = 0.05968310365946075 * (1 + Math.pow(cosT * 0.5 + 0.5, 2));
  const g2 = mieG * mieG;
  const mPhase = 0.07957747154594767 * ((1 - g2) / Math.pow(1 - 2 * mieG * cosT + g2, 1.5));
  const dusk = Math.min(1, Math.max(0, Math.pow(1 - sunDir.y, 5)));
  const out = [0, 0, 0];
  const bias = [0, 0.0003, 0.00075];
  for (let k = 0; k < 3; k++) {
    const fex = Math.exp(-(betaR[k] * sR + betaM[k] * sM));
    const ratio = (betaR[k] * rPhase + betaM[k] * mPhase) / (betaR[k] + betaM[k]);
    let lin = Math.pow(Math.max(0, sunE * ratio * (1 - fex)), 1.5);
    lin *= (1 - dusk) + Math.sqrt(Math.max(0, sunE * ratio * fex)) * dusk;
    const tex = (lin + 0.1 * fex) * 0.04 + bias[k];
    out[k] = gain * Math.pow(Math.max(tex, 0), gamma / (1.2 + 1.2 * sunfade));
  }
  // Low-sun warmth toward the sun, then the night gradient + afterglow.
  const low = lowSunAmount(sunDir.y);
  if (low > 0) {
    const w = low * (0.85 * Math.pow(Math.max(cosT, 0), 3) + 0.25 * Math.exp(-Math.max(dir.y, 0) * 6));
    for (let k = 0; k < 3; k++) out[k] *= 1 + (SKY_GRADE.warm[k] - 1) * w;
  }
  const night = nightAmount(sunDir.y);
  if (night > 0) {
    const up = Math.max(0, Math.min(1, dir.y));
    const t = Math.pow(up, 0.45);
    _tmp.set(sunDir.x, 0, sunDir.z);
    const along = _tmp.lengthSq() > 0 ? Math.max(0, _tmp.normalize().dot(new THREE.Vector3(dir.x, 0, dir.z).normalize())) : 0;
    const glow = afterglowAmount(sunDir.y) * Math.pow(along, 6) * Math.exp(-up * 7);
    for (let k = 0; k < 3; k++) {
      const ns = SKY_GRADE.nightHorizon[k] + (SKY_GRADE.nightZenith[k] - SKY_GRADE.nightHorizon[k]) * t
          + SKY_GRADE.afterglow[k] * glow;
      out[k] = out[k] + (ns - out[k]) * night;
    }
  }
  return out;
}

/**
 * Build the atmosphere dome, add it to the scene, null the
 * background, and return the normalized sun vector for the key light.
 * Sun precedence: `rig` (slaves to rig.sunDir; hides the rig's flat
 * disc — the Preetham shader draws its own; a NIGHT rig's disc is the
 * moon and stays) > `sunDir` (normalized copy, input untouched) >
 * `elevationDeg`/`azimuthDeg` (same convention as `sunRig()`).
 *
 * @param {THREE.Scene} scene The dome is added to it and its
 *   `background` is set to null (the dome IS the backdrop).
 * @param {object} [opts]
 *   `rig` a `sunRig()` result to slave to; `sunDir` explicit sun
 *   vector; `elevationDeg` (default 15) / `azimuthDeg` (default 135)
 *   sun angles; `turbidity` (default 4.5), `rayleigh` (default 1.6),
 *   `mieCoefficient` (default 0.0035), `mieDirectionalG` (default
 *   0.8) — the battery-graded atmosphere; `exposure` dome gain
 *   (default 0.36, graded for exposure 1.0 — the old ungraded dome is
 *   `exposure: 1, contrast: 1`); `contrast` transfer gamma (default
 *   1.7, more = deeper zenith against the horizon); `scale` dome
 *   half-extent driver (default 4500).
 * @returns {{sky: THREE.Mesh, sunDir: THREE.Vector3}} The dome
 *   (already in the scene) and the normalized sun direction — place
 *   the key light at `sunDir * distance`, aimed at the origin.
 */
export function makeSky(scene, opts = {}) {
  const sky = new Sky();
  sky.name = 'PhysicalSky';
  sky.scale.setScalar(opts.scale || 4500);
  // Layer between worldShell's gradient dome (-2) and its ridge (-1)
  // / sun disc (-1.5): all depthWrite:false backdrops, paint order rules.
  sky.renderOrder = -1.8;

  const u = sky.material.uniforms;
  u.turbidity.value = opts.turbidity === undefined ? SKY_GRADE.turbidity : opts.turbidity;
  u.rayleigh.value = opts.rayleigh === undefined ? SKY_GRADE.rayleigh : opts.rayleigh;
  u.mieCoefficient.value =
      opts.mieCoefficient === undefined ? SKY_GRADE.mieCoefficient : opts.mieCoefficient;
  u.mieDirectionalG.value =
      opts.mieDirectionalG === undefined ? SKY_GRADE.mieDirectionalG : opts.mieDirectionalG;

  let sunDir;
  if (opts.rig && opts.rig.sunDir) {
    sunDir = opts.rig.sunDir.clone().normalize();
    // The Preetham shader draws its own solar disc; the rig's flat
    // CircleGeometry disc on the same vector would double the sun.  A
    // night rig's disc is the MOON on another vector: it stays.
    if (opts.rig.sunDisc && !opts.rig.night) opts.rig.sunDisc.visible = false;
  } else if (opts.sunDir) {
    sunDir = opts.sunDir.clone().normalize();
  } else {
    const el = (opts.elevationDeg === undefined ? 15
                                                : opts.elevationDeg) *
        Math.PI / 180;
    const az = (opts.azimuthDeg === undefined ? 135 : opts.azimuthDeg) *
        Math.PI / 180;
    sunDir = new THREE.Vector3(
        Math.cos(el) * Math.cos(az), Math.sin(el),
        Math.cos(el) * Math.sin(az));
  }
  u.sunPosition.value.copy(sunDir);
  // r184 Sky ships value-noise clouds ON by default — grid artifacts
  // at readable contrast. Clouds come from ./clouds.js instead.
  if (u.cloudCoverage) u.cloudCoverage.value = 0;

  // THE GRADE.  Extra uniforms on the addon's own material; the
  // fragment's transfer line is rewritten in onBeforeCompile.
  _tmp.set(sunDir.x, 0, sunDir.z);
  if (_tmp.lengthSq() > 0) _tmp.normalize(); else _tmp.set(1, 0, 0);
  Object.assign(u, {
    uSkyGain: { value: opts.exposure === undefined ? SKY_GRADE.gain : opts.exposure },
    uSkyGamma: { value: opts.contrast === undefined ? SKY_GRADE.gamma : opts.contrast },
    uNight: { value: nightAmount(sunDir.y) },
    uAfterglow: { value: afterglowAmount(sunDir.y) },
    uLowSun: { value: lowSunAmount(sunDir.y) },
    uSunAzimuth: { value: _tmp.clone() },
    uNightZenith: { value: new THREE.Vector3().fromArray(SKY_GRADE.nightZenith) },
    uNightHorizon: { value: new THREE.Vector3().fromArray(SKY_GRADE.nightHorizon) },
    uAfterglowColor: { value: new THREE.Vector3().fromArray(SKY_GRADE.afterglow) },
    uWarm: { value: new THREE.Vector3().fromArray(SKY_GRADE.warm) },
  });
  sky.material.customProgramCacheKey = () => 'astra:sky-grade';
  sky.material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, u);
    const line = /vec3 retColor = pow\( texColor, vec3\( 1\.0 \/ \( 1\.2 \+ \( 1\.2 \* vSunfade \) \) \) \);/;
    if (!line.test(shader.fragmentShader)) {
      throw new Error('sky.js: three Sky.js transfer line moved — regrade makeSky');
    }
    shader.fragmentShader = shader.fragmentShader
        .replace('uniform float mieDirectionalG;', [
          'uniform float mieDirectionalG;',
          'uniform float uSkyGain;', 'uniform float uSkyGamma;',
          'uniform float uNight;', 'uniform float uAfterglow;', 'uniform float uLowSun;',
          'uniform vec3 uSunAzimuth;', 'uniform vec3 uNightZenith;', 'uniform vec3 uNightHorizon;',
          'uniform vec3 uAfterglowColor;', 'uniform vec3 uWarm;',
        ].join('\n'))
        .replace(line, [
          'vec3 retColor = uSkyGain * pow( texColor, vec3( uSkyGamma / ( 1.2 + ( 1.2 * vSunfade ) ) ) );',
          // Golden hour: warm toward the sun and along the horizon band.
          'float astraUp = clamp( direction.y, 0.0, 1.0 );',
          'float astraWarmW = uLowSun * ( 0.85 * pow( max( cosTheta, 0.0 ), 3.0 ) + 0.25 * exp( -astraUp * 6.0 ) );',
          'retColor *= mix( vec3( 1.0 ), uWarm, astraWarmW );',
          // Night: the un-encoded Preetham is black below the horizon;
          // blend to a graded gradient with an afterglow toward the set sun.
          'vec3 astraFlat = normalize( vec3( direction.x, 1e-4, direction.z ) );',
          'float astraAlong = max( dot( astraFlat, uSunAzimuth ), 0.0 );',
          'vec3 astraNight = mix( uNightHorizon, uNightZenith, pow( astraUp, 0.45 ) )',
          '    + uAfterglowColor * uAfterglow * pow( astraAlong, 6.0 ) * exp( -astraUp * 7.0 );',
          'retColor = mix( retColor, astraNight, uNight );',
          // Dither one code value against banding on the smooth gradient.
          'float astraDither = fract( sin( dot( gl_FragCoord.xy, vec2( 12.9898, 78.233 ) ) ) * 43758.5453 );',
          'retColor += ( astraDither - 0.5 ) * 0.0015;',
        ].join('\n\t\t\t'));
  };
  sky.material.needsUpdate = true;

  scene.add(sky);
  scene.background = null;   // the dome IS the backdrop (measured req)
  return { sky, sunDir };
}
