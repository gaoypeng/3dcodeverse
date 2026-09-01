"""Blender-Python executability: run the project's code headless in each configured
Blender version and report {status, verts}. Pass = ran error-free AND produced a non-empty
mesh. Missing/unconfigured Blender binaries are reported as `n/a` (skipped, not failed).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent / "runners" / "blender_exec.py"
RENDER_WRAPPER = Path(__file__).resolve().parent.parent / "runners" / "blender_render.py"
TIMEOUT = 600  # seconds per run


def _run_one(blender_bin: str, code_file: Path) -> dict:
    if not blender_bin or not Path(blender_bin).exists():
        return {"status": "n/a"}
    tf = tempfile.NamedTemporaryFile("r", suffix=".json", delete=False)
    out = tf.name
    tf.close()
    try:
        proc = subprocess.run(
            [blender_bin, "--background", "--factory-startup", "--python", str(WRAPPER),
             "--", str(code_file), out],
            capture_output=True, timeout=TIMEOUT,
        )
        with open(out) as fh:
            return json.load(fh)
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception as e:  # wrapper didn't write output (e.g. blender crashed)
        tail = (proc.stderr.decode(errors="replace").strip().splitlines()[-1:] if "proc" in dir() else [])
        return {"status": "error", "error": (tail[0] if tail else str(e))[:300]}
    finally:
        Path(out).unlink(missing_ok=True)


def run(code_file: Path, blender_bins: dict[str, str]) -> dict:
    return {f"blender_{ver}": _run_one(bin_, code_file) for ver, bin_ in blender_bins.items()}


def render(code_file: Path, renders_dir: Path, blender_bin: str, mode: str) -> dict:
    """Run the code in Blender and render turntable views into renders_dir."""
    if not blender_bin or not Path(blender_bin).exists():
        return {"status": "n/a"}
    tf = tempfile.NamedTemporaryFile("r", suffix=".json", delete=False)
    out = tf.name
    tf.close()
    try:
        proc = subprocess.run(
            [blender_bin, "--background", "--factory-startup", "--python", str(RENDER_WRAPPER),
             "--", str(code_file), str(renders_dir), mode, out],
            capture_output=True, timeout=900,
        )
        with open(out) as fh:
            return json.load(fh)
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception as e:
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-1:] if "proc" in dir() else []
        return {"status": "error", "error": (tail[0] if tail else str(e))[:300]}
    finally:
        Path(out).unlink(missing_ok=True)
