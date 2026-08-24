// Module-resolution hook: lets agent code that lives OUTSIDE runtime_js/
// `import ... from 'three'` / 'three/addons/*' without a node_modules of its own.
//
// NODE_PATH only affects CommonJS resolution, so ESM needs a loader hook.
// Usage:  node --import <runtime_js>/lib/resolve_three.mjs script.mjs
// Every bare specifier that is `three` or `three/...` is re-resolved as if it
// were imported from runtime_js/, i.e. from runtime_js/node_modules/three with
// its exports map (`three/addons/*` -> examples/jsm/*).  Nothing else is touched.
//
// Two node APIs do this, and the floor (node 20.6, see ../package.json
// "engines") only has the older one:
//   * `module.registerHooks` (node >= 22.15 / 23.5) — synchronous, in-thread,
//     no worker, no serialisation.  Preferred when present.
//   * `module.register` (node >= 20.6) — the original async hooks, run on a
//     separate loader thread (./resolve_three_async.mjs).
// Both produce the same resolutions; drop the fallback when the node floor
// reaches 22.15.

import module from 'node:module';
import { RUNTIME_PARENT, isRedirected } from './three_redirect.mjs';

if (typeof module.registerHooks === 'function') {
  module.registerHooks({
    resolve(specifier, context, nextResolve) {
      if (isRedirected(specifier)) {
        return nextResolve(specifier, { ...context, parentURL: RUNTIME_PARENT });
      }
      return nextResolve(specifier, context);
    },
  });
} else if (typeof module.register === 'function') {
  module.register('./resolve_three_async.mjs', import.meta.url);
} else {
  throw new Error(
    `3dcodeverse needs node >= 20.6.0 for module loader hooks; this is ${process.version}`,
  );
}
