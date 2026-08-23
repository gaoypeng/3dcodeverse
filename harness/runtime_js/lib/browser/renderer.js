// THE WebGLRenderer factory for every page-side rig (object renders through
// studio.js/render_rig.js, scenes through scene_host.mjs): one set of fixed
// harness settings — antialias, pixel ratio 1, sRGB output, ACES tone mapping,
// PCF soft shadows, preserveDrawingBuffer (canvas readback) — so an object
// render and a scene render expose the same colour pipeline.
//
//   import { makeRenderer, rendererString } from '/__runtime/lib/browser/renderer.js';

import * as THREE from 'three';

/**
 * WebGLRenderer on `canvas` sized width x height (device pixel ratio 1).
 * @param {{transparent?: boolean, logDepth?: boolean}} opts
 *   transparent - alpha buffer (transparent background renders)
 *   logDepth    - logarithmic depth buffer (huge scenes)
 */
export function makeRenderer(canvas, width, height, { transparent = false, logDepth = false } = {}) {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: transparent,
    preserveDrawingBuffer: true,
    powerPreference: 'high-performance',
    logarithmicDepthBuffer: !!logDepth,
  });
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  return renderer;
}

/** UNMASKED_RENDERER_WEBGL string (or the masked one) of a renderer's context. */
export function rendererString(renderer) {
  const gl = renderer.getContext();
  const ext = gl.getExtension('WEBGL_debug_renderer_info');
  return String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
}
