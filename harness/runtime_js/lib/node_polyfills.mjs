// FileReader shim so three's GLTFExporter can write a binary GLB in plain node (no DOM):
// the exporter reads its Blobs back through readAsArrayBuffer + onloadend, nothing else.
// Canvases are not shimmed: export_glb.mjs strips texture images before exporting.
//
//   import { installExporterPolyfills } from './node_polyfills.mjs';

class FileReaderShim {
  readAsArrayBuffer(blob) {
    blob.arrayBuffer().then((buf) => { this.result = buf; this.onloadend?.({ target: this }); });
  }
}

/** Install the shim once (idempotent). */
export function installExporterPolyfills() {
  globalThis.FileReader ??= FileReaderShim;
}
