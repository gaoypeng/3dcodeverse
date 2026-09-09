"""A minimal binary glTF (one box mesh) for tests that need a real GLB on disk."""

from __future__ import annotations

import json
import struct
from pathlib import Path


def write_box_glb(path: Path, size: tuple[float, float, float] = (2.0, 2.0, 2.0), name: str = "HeroCube") -> Path:
    """Write a GLB holding one box mesh (8 vertices, 12 triangles, no normals — the loader's
    consumer computes them) centred on the origin.  Returns ``path``."""
    sx, sy, sz = (s / 2 for s in size)
    verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
             (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
             (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    pos = struct.pack(f"<{len(verts) * 3}f", *[c for v in verts for c in v])
    idx = struct.pack(f"<{len(faces) * 3}H", *[i for f in faces for i in f])
    idx += b"\x00" * ((4 - len(idx) % 4) % 4)
    blob = pos + idx
    gltf = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
                        {"buffer": 0, "byteOffset": len(pos), "byteLength": len(faces) * 6, "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": len(verts), "type": "VEC3",
                       "min": [-sx, -sy, -sz], "max": [sx, sy, sz]},
                      {"bufferView": 1, "componentType": 5123, "count": len(faces) * 3, "type": "SCALAR"}],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    out = (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(js), 0x4E4F534A) + js
           + struct.pack("<II", len(blob), 0x004E4942) + blob)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)
    return path
