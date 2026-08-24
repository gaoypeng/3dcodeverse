// Which bare specifiers get re-resolved as if imported from runtime_js/, and
// from where.  Shared by the two loader-hook shapes in resolve_three.mjs so the
// redirect rule is stated once (the async hook runs on its own loader thread and
// cannot see the sync hook's module scope).

import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const RUNTIME_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** Parent URL to resolve from; must end with '/' so it acts as a directory. */
export const RUNTIME_PARENT = pathToFileURL(RUNTIME_DIR + path.sep).href;

const REDIRECTED = ['three', 'three-mesh-bvh'];

export function isRedirected(spec) {
  return REDIRECTED.some((p) => spec === p || spec.startsWith(p + '/'));
}
