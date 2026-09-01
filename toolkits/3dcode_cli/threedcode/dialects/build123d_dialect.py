"""build123d dialect runners. exec = run the script + verify a non-empty solid; render =
export STL → clay-render in Blender. build123d is an optional dep (`3dcode[build123d]`).
Same OCC kernel as CadQuery — mirrors cadquery_dialect.py."""

from __future__ import annotations

import json
import subprocess
import tempfile
import traceback
from pathlib import Path

MESH_WRAPPER = Path(__file__).resolve().parent.parent / "runners" / "blender_render_mesh.py"
NO_SOLID = "no solid with positive volume"


def _build(code_file: Path):
    """Run the build123d script; return (shape_with_volume | None, error | None)."""
    import build123d as bd
    captured: list = []
    g = {"__name__": "__main__",
         "show_object": lambda o, *a, **k: captured.append(o),
         "show": lambda o, *a, **k: captured.append(o)}
    try:
        exec(compile(code_file.read_text(), str(code_file), "exec"), g)
    except Exception:
        tb = traceback.format_exc().strip().splitlines()
        return None, (tb[-1] if tb else "error")[:300]
    # candidates: show()/show_object() args, common result globals, then any build123d shape
    objs = list(captured)
    for name in ("result", "part", "obj"):
        if name in g:
            objs.insert(0, g[name])
    shape_types = tuple(t for t in (getattr(bd, n, None) for n in
                        ("BuildPart", "Part", "Solid", "Compound", "Shape")) if isinstance(t, type))
    objs += [v for v in g.values() if isinstance(v, shape_types)]
    for o in objs:
        try:
            shape = o.part if hasattr(o, "part") else o   # BuildPart → its .part
            vol = getattr(shape, "volume", None)
            if vol and vol > 1e-9:
                return shape, None
        except Exception:
            continue
    return None, NO_SOLID


def run(code_file: Path) -> dict:
    try:
        import build123d  # noqa
    except ImportError:
        return {"status": "n/a", "error": "build123d not installed"}
    shape, err = _build(code_file)
    if shape is None:
        return {"status": "no_geometry" if err == NO_SOLID else "error", "error": err or ""}
    try:
        return {"status": "ok", "volume": round(shape.volume, 3)}
    except Exception:
        return {"status": "ok"}


def render(code_file: Path, renders_dir: Path, blender_bin: str, mode: str) -> dict:
    try:
        import build123d as bd
    except ImportError:
        return {"status": "n/a", "error": "build123d not installed"}
    if not blender_bin or not Path(blender_bin).exists():
        return {"status": "n/a", "error": "no blender configured for mesh render"}
    shape, err = _build(code_file)
    if shape is None:
        return {"status": "no_geometry" if err == NO_SOLID else "error", "error": err or ""}
    renders_dir.mkdir(parents=True, exist_ok=True)
    stl = renders_dir.parent / "_b123d_export.stl"
    try:
        diag = getattr(shape.bounding_box(), "diagonal", 0) or 10.0
        bd.export_stl(shape, str(stl), tolerance=max(diag * 0.002, 1e-3), angular_tolerance=0.2)
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
