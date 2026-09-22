// Minimal browser-API shims so three's GLTFExporter can write a binary GLB in
// plain node (no DOM).  Node 18+ already has Blob/TextEncoder/URL; the exporter
// additionally reads Blobs back through FileReader.  We do NOT shim canvases:
// texture images are stripped by the exporter wrapper (with a warning) so the
// exporter never reaches the canvas path.
//
//   import { installExporterPolyfills } from './node_polyfills.mjs';

class FileReaderShim {
  constructor() {
    this.result = null;
    this.onload = null;
    this.onloadend = null;
    this.onerror = null;
  }

  _finish(result) {
    this.result = result;
    if (typeof this.onload === 'function') this.onload({ target: this });
    if (typeof this.onloadend === 'function') this.onloadend({ target: this });
  }

  _fail(err) {
    if (typeof this.onerror === 'function') this.onerror(err);
    else throw err;
  }

  readAsArrayBuffer(blob) {
    blob.arrayBuffer().then((buf) => this._finish(buf), (e) => this._fail(e));
  }

  readAsDataURL(blob) {
    blob.arrayBuffer().then((buf) => {
      const b64 = Buffer.from(buf).toString('base64');
      this._finish(`data:${blob.type || 'application/octet-stream'};base64,${b64}`);
    }, (e) => this._fail(e));
  }

  readAsText(blob) {
    blob.text().then((t) => this._finish(t), (e) => this._fail(e));
  }
}

/** Install the shims once (idempotent). */
export function installExporterPolyfills() {
  if (typeof globalThis.self === 'undefined') globalThis.self = globalThis;
  if (typeof globalThis.FileReader === 'undefined') globalThis.FileReader = FileReaderShim;
}
