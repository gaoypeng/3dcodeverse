// Tiny loopback static server for the headless renderers.  Nothing is ever
// fetched from the internet: `three` and `three/addons/` are served from
// runtime_js/node_modules through the import map returned by importMapHtml().
//
//   const { serveDirs, importMapHtml, RUNTIME_MOUNT } = require('./serve.cjs');
//   const srv = await serveDirs({ root: '/path/to/workspace', routes: { '/__page.html': { body, type: 'text/html' } } });
//   srv.base            -> 'http://127.0.0.1:PORT'
//   srv.url('/x.glb')   -> absolute URL
//   srv.close()
//
// runtime_js/ itself is always mounted at RUNTIME_MOUNT ('/__runtime/').
'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');

const RUNTIME_DIR = __dirname;
const RUNTIME_MOUNT = '/__runtime/';

const MIME = {
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.cjs': 'text/javascript',
  '.json': 'application/json',
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.glb': 'model/gltf-binary',
  '.gltf': 'model/gltf+json',
  '.bin': 'application/octet-stream',
  '.hdr': 'application/octet-stream',
  '.exr': 'application/octet-stream',
  '.ktx2': 'image/ktx2',
  '.wasm': 'application/wasm',
  '.txt': 'text/plain',
  '.glsl': 'text/plain',
  '.vert': 'text/plain',
  '.frag': 'text/plain',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.mp3': 'audio/mpeg',
  '.ogg': 'audio/ogg',
  '.wav': 'audio/wav',
  '.ttf': 'font/ttf',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

function mimeFor(file) {
  return MIME[path.extname(file).toLowerCase()] || 'application/octet-stream';
}

// realpath of each served root, cached (computed once per root per process).
const REAL_ROOTS = new Map();
function realRoot(rootAbs) {
  let real = REAL_ROOTS.get(rootAbs);
  if (!real) {
    real = fs.realpathSync(rootAbs);
    REAL_ROOTS.set(rootAbs, real);
  }
  return real;
}

/** Resolve a URL path inside `rootAbs`; null when it escapes the root or is not a file.
 * Lexical containment first, then realpath containment: statSync follows symlinks, so a
 * symlink inside the root pointing outside would otherwise be served (workspace content
 * is model-authored).  In-root symlinks (workspace aliases, node_modules/.bin) still
 * resolve inside the root and keep working. */
function resolveInside(rootAbs, relUrlPath) {
  const abs = path.resolve(rootAbs, '.' + path.posix.normalize('/' + relUrlPath));
  if (abs !== rootAbs && !abs.startsWith(rootAbs + path.sep)) return null;
  let st;
  try {
    st = fs.statSync(abs);
  } catch (_e) {
    return null;
  }
  if (!st.isFile()) return null;
  try {
    const real = fs.realpathSync(abs);
    const rootReal = realRoot(rootAbs);
    if (real !== rootReal && !real.startsWith(rootReal + path.sep)) return null;
  } catch (_e) {
    return null;
  }
  return abs;
}

/**
 * Serve one or more directories on 127.0.0.1:<random port>.
 * @param {{root?: string, mounts?: Record<string,string>, routes?: Record<string,{body:string|Buffer,type?:string}>, log?: boolean}} opts
 *   root   - directory served at '/'
 *   mounts - url prefix ('/name/') -> directory; checked before root.  runtime_js is always mounted at RUNTIME_MOUNT.
 *   routes - exact url path -> in-memory response (e.g. a generated HTML page).
 */
async function serveDirs(opts = {}) {
  const root = opts.root ? path.resolve(opts.root) : null;
  const mounts = { [RUNTIME_MOUNT]: RUNTIME_DIR, ...(opts.mounts || {}) };
  for (const [prefix, dir] of Object.entries(mounts)) {
    if (!prefix.startsWith('/') || !prefix.endsWith('/')) throw new Error(`mount prefix must look like '/name/': ${prefix}`);
    mounts[prefix] = path.resolve(dir);
  }
  const routes = opts.routes || {};
  const requests = [];

  const server = http.createServer((req, res) => {
    const urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
    if (opts.log) requests.push(urlPath);
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Cache-Control', 'no-store');

    if (urlPath === '/favicon.ico' && !routes[urlPath]) {
      res.statusCode = 204;
      res.end();
      return;
    }
    const route = routes[urlPath];
    if (route) {
      res.setHeader('Content-Type', route.type || 'text/html; charset=utf-8');
      res.end(route.body);
      return;
    }
    let file = null;
    for (const [prefix, dir] of Object.entries(mounts)) {
      if (urlPath.startsWith(prefix)) {
        file = resolveInside(dir, urlPath.slice(prefix.length));
        if (file) break;
      }
    }
    if (!file && root) file = resolveInside(root, urlPath);
    if (!file) {
      res.statusCode = 404;
      res.end(`not found: ${urlPath}`);
      return;
    }
    res.setHeader('Content-Type', mimeFor(file));
    fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  return {
    server,
    base,
    requests,
    url: (p) => base + (p.startsWith('/') ? p : '/' + p),
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

/**
 * `<script type="importmap">` that maps 'three' and 'three/addons/' onto the
 * locally served runtime (no CDN).  `extra` adds more bare specifiers.
 */
function importMapHtml(extra = {}) {
  const imports = {
    three: `${RUNTIME_MOUNT}node_modules/three/build/three.module.js`,
    'three/addons/': `${RUNTIME_MOUNT}node_modules/three/examples/jsm/`,
    'three/examples/jsm/': `${RUNTIME_MOUNT}node_modules/three/examples/jsm/`,
    'three-mesh-bvh': `${RUNTIME_MOUNT}node_modules/three-mesh-bvh/build/index.module.js`,
    ...extra,
  };
  return `<script type="importmap">${JSON.stringify({ imports })}</script>`;
}

module.exports = { MIME, RUNTIME_DIR, RUNTIME_MOUNT, mimeFor, serveDirs, importMapHtml };
