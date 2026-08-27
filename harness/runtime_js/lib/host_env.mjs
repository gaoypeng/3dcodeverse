/**
 * Node-side environment for the scene host drivers: browser launch and
 * workspace serving.  Thin ESM adapters over the two CJS runtime modules —
 * `gpu_launch.cjs` (GPU-verified headless Chrome) and `serve.cjs` (loopback
 * static server + import map).  Consumers: `lib/host_page.mjs` (scenes) and
 * `render_glb.mjs` (objects) — no other module reaches for the CJS files.
 */

import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require_ = createRequire(import.meta.url);
export const RUNTIME_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const gpuLaunch = require_(path.join(RUNTIME_ROOT, 'gpu_launch.cjs'));
const serve = require_(path.join(RUNTIME_ROOT, 'serve.cjs'));

/**
 * Launch (or connect to) a headless browser; see gpu_launch.cjs.
 * @param {{gpu?: 'auto'|'on'|'off'}} opts
 * @returns {Promise<{browser: object, gpu: boolean, renderer: string, release: () => Promise<void>}>}
 */
export async function launchBrowser(opts = {}) {
  return gpuLaunch.launchBrowser(opts);
}

/**
 * Serve arbitrary directories / in-memory routes (see serve.cjs); runtime_js is
 * always mounted at `runtimeMount()`.  The object renderer serves the GLB's
 * directory this way; scenes go through `serveWorkspace` below.
 * @returns {Promise<{server, base: string, url: (p: string) => string, close: () => Promise<void>}>}
 */
export async function serveDirs(opts = {}) {
  return serve.serveDirs(opts);
}

/** URL prefix under which runtime_js is served ('/__runtime/'). */
export function runtimeMount() {
  return serve.RUNTIME_MOUNT;
}

/** Import map that resolves 'three' and 'three/addons/*' to the local runtime copy. */
export function importMapHtml() {
  return serve.importMapHtml();
}

/**
 * Serve a workspace on 127.0.0.1:0 with these mounts:
 *   /src/** /public/**      → <ws>/src, <ws>/public
 *   /assets/**              → <ws>/public/assets  (contract: '/assets/x.glb')
 *   <runtimeMount()>/**     → runtime_js (node_modules/three, lib/*.mjs)
 *   /__host.html            → generated host page (import map + scene_host.mjs)
 * @returns {Promise<{server, base: string, close: () => Promise<void>}>}
 */
export async function serveWorkspace(wsRoot, { hostHtml = '' } = {}) {
  wsRoot = path.resolve(wsRoot);
  const srv = await serve.serveDirs({
    // explicit mounts, no `root`: the workspace root also holds spec.json, run_state.json,
    // events.jsonl, .git/ and artifacts/ — generated scene code must not be able to GET them.
    mounts: {
      '/src/': path.join(wsRoot, 'src'),
      '/public/': path.join(wsRoot, 'public'),
      '/assets/': path.join(wsRoot, 'public', 'assets'),
    },
    routes: { '/__host.html': { body: hostHtml, type: 'text/html; charset=utf-8' } },
  });
  return { server: srv.server, base: srv.base, close: srv.close };
}
