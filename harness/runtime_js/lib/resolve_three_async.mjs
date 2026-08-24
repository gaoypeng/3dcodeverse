// Async loader hooks (`module.register`, node >= 20.6) — the fallback shape of
// the redirect in resolve_three.mjs, used on node < 22.15 where the cheaper
// in-thread `module.registerHooks` does not exist.  This module is loaded on
// node's loader thread; it must not import anything from the application.

import { RUNTIME_PARENT, isRedirected } from './three_redirect.mjs';

export async function resolve(specifier, context, nextResolve) {
  if (isRedirected(specifier)) {
    return nextResolve(specifier, { ...context, parentURL: RUNTIME_PARENT });
  }
  return nextResolve(specifier, context);
}
