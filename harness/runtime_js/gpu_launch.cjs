// Headless-Chrome launcher for the WebGL renderers (render_glb / render_scene / probes).
//
// On this WSL2 box hardware WebGL needs BOTH the ANGLE gl-egl flags AND the
// Mesa d3d12 env trio; either alone silently lands on llvmpipe.  So every GPU
// attempt is verified through UNMASKED_RENDERER_WEBGL before it is trusted,
// and a negative verdict is cached (with a TTL) so the slow-to-fail GPU launch
// is not repeated on every render.
//
//   const { launchBrowser, rendererInfo } = require('./gpu_launch.cjs');
//   const handle = await launchBrowser({ gpu: 'auto' });
//   ... handle.browser / handle.gpu / handle.renderer ...
//   await handle.release();   // NOT browser.close(): the browser may be shared
//
// gpu: 'auto' (probe, cached) | 'on' (require hardware; throw otherwise) | 'off' (CPU).
//
// Browser reuse (connect-first): a launch costs ~0.55 s, a puppeteer.connect
// ~5 ms.  A detached `browser_daemon.cjs` owns one long-lived browser per
// backend ('gpu'|'cpu') and advertises its ws endpoint in a file under
// CACHE_DIR; launchBrowser connects to it when present, spawns the daemon when
// absent, and falls back to a plain owned launch when the daemon cannot help.
// `release()` disconnects from a shared browser (the daemon reaps it after
// ~90 s idle) and closes an owned one.  `CV3D_BROWSER_REUSE=off` disables
// sharing entirely.
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const CACHE_DIR = process.env.CV3D_CACHE_DIR || path.join(os.homedir(), '.cache', 'codeverse');
const CACHE_PATH = path.join(CACHE_DIR, 'gpu_probe.json');
const NEGATIVE_TTL_MS = 20 * 60 * 1000; // how long a "no GPU" verdict is trusted
const PROTOCOL_TIMEOUT_MS = 15 * 60 * 1000; // big frame batches exceed puppeteer's 180 s default
const DAEMON_WAIT_MS = 25000;   // max wait for a spawned daemon to advertise its endpoint
const CANARY_TIMEOUT_MS = 6000; // a healthy browser answers pages() in ~5 ms
const MAX_SHARED_PAGES = 12;    // more open pages than any sane moment = leaked pages piling up   // max wait for a spawned daemon to advertise its endpoint
const SPAWN_LOCK_STALE_MS = 30000;

const BASE_ARGS = ['--no-sandbox', '--disable-setuid-sandbox', '--enable-webgl', '--ignore-gpu-blocklist'];
const GPU_ARGS = [...BASE_ARGS, '--enable-gpu-rasterization', '--use-angle=gl-egl'];
const CPU_ARGS = [...BASE_ARGS, '--enable-unsafe-swiftshader', '--use-angle=swiftshader'];
const GPU_ENV = {
  GALLIUM_DRIVER: 'd3d12',
  MESA_LOADER_DRIVER_OVERRIDE: 'd3d12',
  MESA_D3D12_DEFAULT_ADAPTER_NAME: 'NVIDIA',
};
const SOFTWARE_RE = /swiftshader|llvmpipe|softpipe|software|basic render/i;

function readCache() {
  try {
    return JSON.parse(fs.readFileSync(CACHE_PATH, 'utf8'));
  } catch (_e) {
    return null;
  }
}

function writeCache(decision) {
  try {
    fs.mkdirSync(CACHE_DIR, { recursive: true });
    fs.writeFileSync(CACHE_PATH, JSON.stringify({ ...decision, at: Date.now() }, null, 2));
  } catch (_e) {
    /* cache is an optimisation only */
  }
}

/**
 * Probe the browser's WebGL backend. Returns the UNMASKED_RENDERER string or null
 * when no WebGL context can be created.
 */
async function rendererInfo(browser) {
  const page = await browser.newPage();
  try {
    return await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
      if (!gl) return null;
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      const r = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
      return String(r);
    });
  } finally {
    await page.close().catch(() => {});
  }
}

function loadPuppeteer(puppeteer) {
  return puppeteer || require('puppeteer');
}

function owned(browser, gpu, renderer) {
  return { browser, gpu, renderer, shared: false, release: () => browser.close().catch(() => {}) };
}

async function launchCpu(puppeteer) {
  const env = { ...process.env };
  for (const k of Object.keys(GPU_ENV)) delete env[k];
  const browser = await puppeteer.launch({
    headless: true,
    args: CPU_ARGS,
    env,
    protocolTimeout: PROTOCOL_TIMEOUT_MS,
  });
  let renderer = 'SwiftShader (CPU)';
  try {
    renderer = (await rendererInfo(browser)) || renderer;
  } catch (_e) {
    /* keep the default label */
  }
  return owned(browser, false, renderer);
}

async function tryGpu(puppeteer) {
  let browser = null;
  try {
    browser = await puppeteer.launch({
      headless: true,
      args: GPU_ARGS,
      env: { ...process.env, ...GPU_ENV },
      protocolTimeout: PROTOCOL_TIMEOUT_MS,
    });
    const renderer = await rendererInfo(browser);
    if (!renderer || SOFTWARE_RE.test(renderer)) {
      await browser.close().catch(() => {});
      return { browser: null, renderer: renderer || 'no WebGL context' };
    }
    return { browser, renderer };
  } catch (e) {
    if (browser) await browser.close().catch(() => {});
    return { browser: null, renderer: `launch failed: ${e.message}` };
  }
}

// --------------------------------------------------------------------- sharing
function endpointPath(backend) {
  return path.join(CACHE_DIR, `browser_${backend}.json`);
}

function daemonFailPath(backend) {
  return path.join(CACHE_DIR, `browser_${backend}.failed.json`);
}

function reuseEnabled() {
  return !/^(off|0|false)$/i.test(process.env.CV3D_BROWSER_REUSE || '');
}

function readJson(p) {
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8'));
  } catch (_e) {
    return null;
  }
}

/** Touch the endpoint file: its mtime is the daemon's idle-reap heartbeat. */
function heartbeat(backend) {
  try {
    const now = new Date();
    fs.utimesSync(endpointPath(backend), now, now);
  } catch (_e) { /* endpoint may be gone; connect error handling covers it */ }
}

// The ws this process last REJECTED (canary timeout / page overflow).  The endpoint
// file stays for everyone else (only the daemon retires the browser), but re-testing
// the same wedged ws in sharedBrowser's 50 ms wait loop costs a 6 s canary per
// iteration — up to ~24 s of pure wait.  Skip it until a NEW ws is advertised.
let rejectedWs = null;

/** Connect to the shared browser advertised for `backend`; null when absent/poisoned. */
async function connectShared(puppeteer, backend) {
  const info = readJson(endpointPath(backend));
  if (!info || !info.ws || info.ws === rejectedWs) return null;
  try {
    const browser = await puppeteer.connect({ browserWSEndpoint: info.ws, protocolTimeout: PROTOCOL_TIMEOUT_MS });
    // Health canary: a long-lived shared browser can wedge (WSL GPU decay plus pages
    // leaked by SIGKILLed clients) and then every CDP roundtrip stalls ~100 s.  One
    // bounded pages() decides whether THIS caller uses it — nothing more.  It must not
    // retire the browser: deleting the endpoint makes the daemon close it, and a
    // busy box (four runs, blender pegging the CPU) answers pages() slowly, so a
    // false verdict killed a live scene probe's page mid-call with
    // 'Protocol error: Target closed' (measured 2026-08-27).  Only the daemon, which
    // knows whether anyone is working, may retire the browser; a caller that does not
    // like what it sees simply launches its own.
    let pages = null;
    try {
      let timer = null;
      pages = await Promise.race([
        browser.pages(),
        new Promise((_res, rej) => { timer = setTimeout(() => rej(new Error('canary timeout')), CANARY_TIMEOUT_MS); if (timer.unref) timer.unref(); }),
      ]);
      if (timer) clearTimeout(timer);
    } catch (_e) { pages = null; }
    if (!pages || pages.length > MAX_SHARED_PAGES) {
      rejectedWs = info.ws;
      try { await browser.disconnect(); } catch (_e) { /* ignore */ }
      return null;   // own launch for this caller; the endpoint stays for everyone else
    }
    heartbeat(backend);
    // Keep the heartbeat fresh WHILE the client works: it used to tick only on
    // connect and release, so a scene probe holding one page for 16 minutes looked
    // idle to the daemon, which closed its page (page TTL) or the whole browser
    // (HARD_REAP_MS) out from under it — 'Protocol error: Target closed'.
    const beat = setInterval(() => heartbeat(backend), 30_000);
    if (beat.unref) beat.unref();
    return {
      browser,
      gpu: !!info.gpu,
      renderer: String(info.renderer || ''),
      shared: true,
      release: async () => {
        clearInterval(beat);
        heartbeat(backend);
        try { await browser.disconnect(); } catch (_e) { /* already gone */ }
      },
    };
  } catch (_e) {
    // connect itself REFUSED: nothing listens at that ws, so the daemon (the browser's
    // parent) is gone too — clearing a DEAD advertisement does not violate "only the
    // daemon retires the browser"; a merely-slow browser is the canary's case above,
    // and the canary never touches the file.
    try { fs.rmSync(endpointPath(backend), { force: true }); } catch (_e2) { /* ignore */ }
    return null;
  }
}

/** Spawn the detached daemon for `backend` (single flight via a lock file). */
function spawnDaemon(backend) {
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  const lock = path.join(CACHE_DIR, `browser_${backend}.lock`);
  try {
    const st = fs.statSync(lock);
    if (Date.now() - st.mtimeMs < SPAWN_LOCK_STALE_MS) return; // someone else is spawning
    fs.rmSync(lock, { force: true });
  } catch (_e) { /* no lock */ }
  try {
    fs.writeFileSync(lock, String(process.pid), { flag: 'wx' });
  } catch (_e) {
    return; // lost the race
  }
  try { fs.rmSync(daemonFailPath(backend), { force: true }); } catch (_e) { /* ignore */ }
  const child = spawn(process.execPath, [path.join(__dirname, 'browser_daemon.cjs'), '--backend', backend],
    { detached: true, stdio: 'ignore', env: process.env });
  child.unref();
}

/**
 * Get a shared browser for `backend` ('gpu'|'cpu'): connect to the advertised
 * endpoint, spawning the daemon first when there is none.
 * @returns handle, or {failed: string} when the daemon reported a launch
 *          failure (meaningful for 'gpu'), or null on timeout/no daemon.
 */
async function sharedBrowser(puppeteer, backend) {
  let handle = await connectShared(puppeteer, backend);
  if (handle) return handle;
  spawnDaemon(backend);
  const deadline = Date.now() + DAEMON_WAIT_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 50));
    handle = await connectShared(puppeteer, backend);
    if (handle) return handle;
    const failed = readJson(daemonFailPath(backend));
    if (failed) return { failed: String(failed.error || 'daemon launch failed') };
  }
  return null;
}

async function cpuBrowser(puppeteer) {
  if (reuseEnabled()) {
    const h = await sharedBrowser(puppeteer, 'cpu');
    if (h && h.browser) return h;
  }
  return launchCpu(puppeteer);
}

/** GPU browser or {browser:null, renderer:<why>} — shared first, owned fallback. */
async function gpuBrowser(puppeteer) {
  if (reuseEnabled()) {
    const h = await sharedBrowser(puppeteer, 'gpu');
    if (h && h.browser) return h;
    if (h && h.failed) return { browser: null, renderer: h.failed };
    // daemon never answered: fall through to an owned launch
  }
  const attempt = await tryGpu(puppeteer);
  if (!attempt.browser) return { browser: null, renderer: attempt.renderer };
  return owned(attempt.browser, true, attempt.renderer);
}

/**
 * Launch (or connect to) headless Chrome on the best available WebGL backend.
 * @param {{gpu?: 'auto'|'on'|'off', puppeteer?: object}} opts
 * @returns {Promise<{browser: object, gpu: boolean, renderer: string, shared: boolean, release: () => Promise<void>}>}
 */
async function launchBrowser(opts = {}) {
  const mode = String(opts.gpu || process.env.CV3D_RENDER_GPU || 'auto').toLowerCase();
  const puppeteer = loadPuppeteer(opts.puppeteer);
  if (!['auto', 'on', 'off'].includes(mode)) throw new Error(`gpu must be auto|on|off, got ${mode}`);

  if (mode === 'off') return cpuBrowser(puppeteer);

  if (mode === 'on') {
    const attempt = await gpuBrowser(puppeteer);
    if (!attempt.browser) throw new Error(`gpu=on but no hardware WebGL: ${attempt.renderer}`);
    return attempt;
  }

  // auto: a fresh negative verdict skips the slow GPU attempt; a positive one is
  // re-verified by the GPU attempt itself (shared endpoint or launch), since the
  // GPU can go away.
  const cached = readCache();
  const fresh = cached && typeof cached.at === 'number' && Date.now() - cached.at < NEGATIVE_TTL_MS;
  if (cached && cached.use_gpu === false && fresh) return cpuBrowser(puppeteer);

  const attempt = await gpuBrowser(puppeteer);
  if (attempt.browser) {
    writeCache({ use_gpu: true, renderer: attempt.renderer });
    return attempt;
  }
  writeCache({ use_gpu: false, renderer: attempt.renderer });
  return cpuBrowser(puppeteer);
}

module.exports = {
  launchBrowser, rendererInfo, GPU_ARGS, CPU_ARGS, GPU_ENV, CACHE_PATH,
  // internals shared with browser_daemon.cjs (not a public surface)
  _internal: { launchCpu, tryGpu, loadPuppeteer, endpointPath, daemonFailPath, CACHE_DIR, SOFTWARE_RE },
};
