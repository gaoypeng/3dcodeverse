"""FreeCAD dialect runners. exec/render run the user script inside freecadcmd (via
runners/freecad_exec.py): exec checks for a non-empty solid; render tessellates the
largest shape to STL → clay-render in Blender. Needs freecadcmd (config `freecadcmd`)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

FREECAD_WRAPPER = Path(__file__).resolve().parent.parent / "runners" / "freecad_exec.py"
MESH_WRAPPER = Path(__file__).resolve().parent.parent / "runners" / "blender_render_mesh.py"


def _run_freecad(code_file: Path, freecadcmd_bin: str, stl_out: str = ""):
    tf = tempfile.NamedTemporaryFile("r", suffix=".json", delete=False)
    out = tf.name
    tf.close()
    # args go via env: freecadcmd treats every positional arg as another script to run
    env = dict(os.environ, FC_CODE=str(code_file), FC_OUT=out, FC_STL=str(stl_out or ""))
    try:
        subprocess.run([str(freecadcmd_bin), str(FREECAD_WRAPPER)],
                       capture_output=True, timeout=600, env=env)
        with open(out) as fh:
            return json.load(fh), None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, str(e)[:200]
    finally:
        Path(out).unlink(missing_ok=True)


def run(code_file: Path, freecadcmd_bin: str) -> dict:
    if not freecadcmd_bin or not Path(str(freecadcmd_bin)).exists():
        return {"status": "n/a", "error": "freecadcmd not configured"}
    res, err = _run_freecad(code_file, freecadcmd_bin)
    if err:
        return {"status": "error", "error": err}
    out = {"status": res.get("status", "error")}
    if res.get("volume"):
        out["volume"] = res["volume"]
    if res.get("error"):
        out["error"] = res["error"]
    return out


def render(code_file: Path, renders_dir: Path, blender_bin: str, mode: str, freecadcmd_bin: str) -> dict:
    if not freecadcmd_bin or not Path(str(freecadcmd_bin)).exists():
        return {"status": "n/a", "error": "freecadcmd not configured"}
    if not blender_bin or not Path(blender_bin).exists():
        return {"status": "n/a", "error": "no blender configured for mesh render"}
    renders_dir.mkdir(parents=True, exist_ok=True)
    stl = renders_dir.parent / "_fc_export.stl"
    res, err = _run_freecad(code_file, freecadcmd_bin, stl)
    if err:
        return {"status": "error", "error": err}
    if res.get("status") != "ok" or not stl.exists():
        return {"status": res.get("status", "no_geometry"),
                "error": res.get("error", res.get("stl_error", ""))}
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
