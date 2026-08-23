"""Shared helpers for the prompt-pack tests.

The cookbooks promise that their fenced snippets RUN.  These helpers extract the
fenced blocks per language and execute them the way the cookbook headers say they
are executed (concatenated in order, with the documented prelude in scope).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from codeverse.config import get_settings

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "codeverse" / "prompts"
RUNTIME_JS = Path(__file__).resolve().parents[2] / "runtime_js"
HELPERS = Path(__file__).resolve().parent / "helpers"

#: every file this package owns (relative to codeverse/prompts/)
PROMPT_FILES = [
    "system/harness_contract.md",
    "system/singleshot_format.md",
    "system/tools_usage.md",
    "blender/contract.md",
    "blender/cookbook.md",
    "cadquery/contract.md",
    "cadquery/cookbook.md",
    "threejs/contract.md",
    "threejs/cookbook.md",
    "urdf/contract.md",
    "urdf/cookbook.md",
    "scene_threejs/contract.md",
    "scene_threejs/cookbook.md",
    "scene_threejs/glsl_cookbook.md",
]

_FENCE = re.compile(r"```(\w[\w-]*)\n(.*?)```", re.DOTALL)


def read_prompt(rel: str) -> str:
    return (PROMPTS_DIR / rel).read_text()


def blocks(rel: str, lang: str) -> list[str]:
    """All fenced blocks tagged ``lang`` in prompt file ``rel``, in order."""
    return [body for tag, body in _FENCE.findall(read_prompt(rel)) if tag == lang]


def strip_imports_exports(js: str) -> str:
    js = re.sub(r"^\s*import .*?;\s*$", "", js, flags=re.MULTILINE)
    return re.sub(r"^export (default )?", "", js, flags=re.MULTILINE)


def labelled_files(rel: str, tag: str = "js") -> dict[str, str]:
    """Blocks preceded by a `src/...` label line → {path: content} (contract examples)."""
    out: dict[str, str] = {}
    pattern = re.compile(r"`((?:src|public)/[^`]+)`\n```" + tag + r"\n(.*?)```", re.DOTALL)
    for path, body in pattern.findall(read_prompt(rel)):
        out[path] = body
    return out


# ----------------------------------------------------------------------- runners
def run_node_module(tmp_path: Path, code: str, timeout: int = 120) -> str:
    """Write ``code`` as an ES module next to a node_modules symlink and run it."""
    node = shutil.which(get_settings().binaries.node) or shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    link = tmp_path / "node_modules"
    if not link.exists():
        link.symlink_to(RUNTIME_JS / "node_modules")
    mod = tmp_path / "run.mjs"
    mod.write_text(code)
    proc = subprocess.run(
        [node, "run.mjs"], cwd=tmp_path, capture_output=True, text=True, timeout=timeout
    )
    assert proc.returncode == 0, f"node failed:\nSTDOUT:{proc.stdout[-3000:]}\nSTDERR:{proc.stderr[-3000:]}"
    return proc.stdout


def run_blender_script(tmp_path: Path, code: str, timeout: int = 240) -> str:
    """Run python ``code`` inside Blender headless with an emptied factory scene."""
    blender = get_settings().resolve_blender()
    if not blender:
        pytest.skip("no Blender binary configured")
    script = tmp_path / "run_bpy.py"
    script.write_text(
        "import bpy\n"
        "for _o in list(bpy.data.objects): bpy.data.objects.remove(_o, do_unlink=True)\n"
        + code
        + "\nprint('BPY_SNIPPETS_OK')\n"
    )
    proc = subprocess.run(
        [str(blender), "-b", "--factory-startup", "--python", str(script)],
        capture_output=True, text=True, timeout=timeout,
    )
    out = proc.stdout + proc.stderr
    assert "BPY_SNIPPETS_OK" in out, f"blender snippets failed (rc={proc.returncode}):\n{out[-4000:]}"
    return out


def js_prelude(extra: str = "") -> str:
    return (
        "import * as THREE from 'three';\n"
        "import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';\n"
        "import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';\n"
        "import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';\n"
        "import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';\n"
        "import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';\n"
        "import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';\n" + extra
    )
