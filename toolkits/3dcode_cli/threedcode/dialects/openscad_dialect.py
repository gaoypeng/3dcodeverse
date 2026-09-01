"""OpenSCAD dialect runners. exec = compile the .scad to STL (must be non-empty); render =
STL → clay-render in Blender. Needs the openscad binary/AppImage (config `openscad`)."""

from __future__ import annotations

import json
import struct
import subprocess
import tempfile
from pathlib import Path

MESH_WRAPPER = Path(__file__).resolve().parent.parent / "runners" / "blender_render_mesh.py"


def _wrap(openscad_bin: str) -> list:
    # AppImages run via extract-and-run on hosts without FUSE auto-mount
    b = str(openscad_bin)
    return [b, "--appimage-extract-and-run"] if b.lower().endswith(".appimage") else [b]


def _stl_tris(stl_path: Path) -> int:
    data = stl_path.read_bytes()
    if data[:5] == b"solid" and b"facet" in data[:2000]:      # ASCII STL
        return data.count(b"facet normal")
    if len(data) >= 84:                                       # binary STL: count at offset 80
        return struct.unpack("<I", data[80:84])[0]
    return 0


def _compile_stl(code_file: Path, openscad_bin: str, stl_path: Path):
    """openscad -o out.stl <scad>; return (triangle_count, error). Retries headless via xvfb."""
    base = _wrap(openscad_bin) + ["-o", str(stl_path), str(code_file)]
    for cmd in (base, ["xvfb-run", "-a"] + base):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            continue                                          # no xvfb-run → skip retry
        except subprocess.TimeoutExpired:
            return 0, "timeout"
        if p.returncode == 0 and stl_path.exists():
            return _stl_tris(stl_path), None
        err = (p.stderr or p.stdout or "openscad failed").strip().splitlines()
        last = (err[-1] if err else "openscad failed")[:300]
        if "DISPLAY" not in (p.stderr or "") and "display" not in (p.stderr or "").lower():
            return 0, last                                    # not a display issue → real error
    return 0, last


def run(code_file: Path, openscad_bin: str) -> dict:
    if not openscad_bin or not Path(str(openscad_bin)).exists():
        return {"status": "n/a", "error": "openscad not configured"}
    with tempfile.TemporaryDirectory() as td:
        stl = Path(td) / "out.stl"
        tris, err = _compile_stl(code_file, openscad_bin, stl)
        if err:
            return {"status": "error", "error": err}
        if tris == 0:
            return {"status": "no_geometry", "error": "empty STL (0 triangles)"}
        return {"status": "ok", "triangles": tris}


def render(code_file: Path, renders_dir: Path, blender_bin: str, mode: str, openscad_bin: str) -> dict:
    if not openscad_bin or not Path(str(openscad_bin)).exists():
        return {"status": "n/a", "error": "openscad not configured"}
    if not blender_bin or not Path(blender_bin).exists():
        return {"status": "n/a", "error": "no blender configured for mesh render"}
    renders_dir.mkdir(parents=True, exist_ok=True)
    stl = renders_dir.parent / "_scad_export.stl"
    tris, err = _compile_stl(code_file, openscad_bin, stl)
    if err or tris == 0:
        stl.unlink(missing_ok=True)
        return {"status": "error" if err else "no_geometry", "error": err or ""}
    tf = tempfile.NamedTemporaryFile("r", suffix=".json", delete=False)
    out = tf.name
    tf.close()
    try:
        subprocess.run([blender_bin, "--background", "--factory-startup", "--python", str(MESH_WRAPPER),
                        "--", str(stl), str(renders_dir), mode, out], capture_output=True, timeout=600)
        with open(out) as fh:
            return json.load(fh)
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}
    finally:
        Path(out).unlink(missing_ok=True)
        stl.unlink(missing_ok=True)
