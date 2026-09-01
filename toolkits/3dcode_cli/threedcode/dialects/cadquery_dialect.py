"""CadQuery dialect runners. exec = run the script and verify it builds a non-empty
solid; render = export the solid to STL, then clay-render it in Blender (CadQuery has
no native image renderer). cadquery is an optional dep (`3dcode[cadquery]`)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import traceback
from pathlib import Path

MESH_WRAPPER = Path(__file__).resolve().parent.parent / "runners" / "blender_render_mesh.py"
NO_SOLID = "no solid with positive volume"


def _build(code_file: Path):
    """Run the CadQuery script; return (Workplane | None, error_str | None)."""
    import cadquery as cq
    captured: list = []
    g = {"__name__": "__main__", "show_object": lambda o, *a, **k: captured.append(o)}
    try:
        exec(compile(code_file.read_text(), str(code_file), "exec"), g)
    except Exception:
        tb = traceback.format_exc().strip().splitlines()
        return None, (tb[-1] if tb else "error")[:300]
    # candidates: show_object() args, a `result` global, then any Workplane
    objs = list(captured)
    if "result" in g:
        objs.insert(0, g["result"])
    objs += [v for v in g.values() if isinstance(v, cq.Workplane)]
    for o in objs:
        try:
            wp = o if isinstance(o, cq.Workplane) else (cq.Workplane(obj=o) if isinstance(o, cq.Shape) else None)
            if wp is not None and wp.val().Volume() > 1e-9:
                return wp, None
        except Exception:
            continue
    return None, NO_SOLID


def run(code_file: Path) -> dict:
    try:
        import cadquery  # noqa
    except ImportError:
        return {"status": "n/a", "error": "cadquery not installed"}
    wp, err = _build(code_file)
    if wp is None:
        return {"status": "no_geometry" if err == NO_SOLID else "error", "error": err or ""}
    try:
        return {"status": "ok", "volume": round(wp.val().Volume(), 3)}
    except Exception:
        return {"status": "ok"}


def render(code_file: Path, renders_dir: Path, blender_bin: str, mode: str) -> dict:
    try:
        import cadquery as cq
    except ImportError:
        return {"status": "n/a", "error": "cadquery not installed"}
    if not blender_bin or not Path(blender_bin).exists():
        return {"status": "n/a", "error": "no blender configured for mesh render"}
    wp, err = _build(code_file)
    if wp is None:
        return {"status": "no_geometry" if err == NO_SOLID else "error", "error": err or ""}
    renders_dir.mkdir(parents=True, exist_ok=True)
    stl = renders_dir.parent / "_cq_export.stl"
    try:
        diag = getattr(wp.val().BoundingBox(), "DiagonalLength", 0) or 10.0
        cq.exporters.export(wp, str(stl), tolerance=max(diag * 0.002, 1e-3), angularTolerance=0.2)
    except Exception as e:
        return {"status": "error", "error": f"stl export failed: {str(e)[:200]}"}
    tf = tempfile.NamedTemporaryFile("r", suffix=".json", delete=False)
    out = tf.name
    tf.close()
    try:
        subprocess.run([blender_bin, "--background", "--factory-startup", "--python", str(MESH_WRAPPER),
                        "--", str(stl), str(renders_dir), mode, out], capture_output=True, timeout=600)
        with open(out) as fh:
            return json.load(fh)
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}
    finally:
        Path(out).unlink(missing_ok=True)
        stl.unlink(missing_ok=True)
