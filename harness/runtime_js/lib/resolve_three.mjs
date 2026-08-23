// Module-resolution hook: lets agent code that lives OUTSIDE runtime_js/
// `import ... from 'three'` / 'three/addons/*' without a node_modules of its own.
//
// NODE_PATH only affects CommonJS resolution, so ESM needs a loader hook.
// Usage:  node --import <runtime_js>/lib/resolve_three.mjs script.mjs
// Every bare specifier that is `three` or `three/...` is re-resolved as if it
// were imported from runtime_js/, i.e. from runtime_js/node_modules/three with
// its exports map (`three/addons/*` -> examples/jsm/*).  Nothing else is touched.

import { registerHooks } from 'node:module';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
// Must end with '/' so it acts as a directory parent for resolution.
const RUNTIME_PARENT = pathToFileURL(RUNTIME_DIR + path.sep).href;

const REDIRECTED = ['three', 'three-mesh-bvh'];

function isRedirected(spec) {
  return REDIRECTED.some((p) => spec === p || spec.startsWith(p + '/'));
}

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (isRedirected(specifier)) {
      return nextResolve(specifier, { ...context, parentURL: RUNTIME_PARENT });
    }
    return nextResolve(specifier, context);
  },
});
