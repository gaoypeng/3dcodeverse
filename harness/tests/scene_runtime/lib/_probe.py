"""Node probes for the shipped effect library (``starter/src/lib``).

Ported 2026-09-01 from the scene_multifile_graphics reference's tests/node_probe.py.
A probe is an ES module run under OUR resolve hook (``runtime_js/lib/resolve_three.mjs``
maps ``three`` / ``three/addons/*`` to the harness's own install), in a private temp
dir holding a copy of the library — never a live workspace.  ``measure`` returns the
LAST stdout line of the probe as JSON; ``compile_scene`` compiles every program a
fixture scene builds through ``runtime_js/check_shaders.mjs`` (headless GPU).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[3]
LIB_DIR = HARNESS / "codeverse3d" / "languages" / "scene_threejs" / "starter" / "src" / "lib"
RESOLVE_HOOK = HARNESS / "runtime_js" / "lib" / "resolve_three.mjs"
NODE_MODULES = HARNESS / "runtime_js" / "node_modules"

pytestmark = pytest.mark.node


def _three_ready() -> None:
    if not (NODE_MODULES / "three" / "package.json").is_file():
        pytest.skip("runtime_js/node_modules/three missing (npm ci in runtime_js)")


def _stage(root: Path, libs: tuple[str, ...]) -> None:
    (root / "lib").mkdir()
    # the hub modules every effect imports ride along whatever the test names
    for name in sorted(set(libs) | {"shader.js", "noise.js", "materials.js"}):
        shutil.copy(LIB_DIR / name, root / "lib" / name)


def measure(script: str, libs: tuple[str, ...] = (), *, timeout_s: float = 120.0) -> dict:
    """Run ``script`` (an ES module; ``import ... from './lib/x.js'``) and parse its
    last stdout line as JSON.  The script is responsible for ``console.log(JSON...)``."""
    _three_ready()
    with tempfile.TemporaryDirectory(prefix="c3d-libprobe-") as tmp:
        root = Path(tmp)
        _stage(root, libs)
        (root / "probe.mjs").write_text(script, encoding="utf-8")
        out = subprocess.run(
            ["node", "--import", str(RESOLVE_HOOK), str(root / "probe.mjs")],
            capture_output=True, text=True, timeout=timeout_s, cwd=str(root),
            env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        )
        assert out.returncode == 0, out.stderr[-2000:]
        lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
        assert lines, "probe printed nothing"
        return json.loads(lines[-1])


def compile_scene(scene_src: str, libs: tuple[str, ...] = (), *,
                  extra: dict[str, str] | None = None,
                  audit_module: str | None = None,
                  timeout_s: float = 180.0) -> tuple[int, str]:
    """Compile every program a fixture SCENE builds, on the headless GPU.

    ``scene_src`` is a workspace ``src/scene.js``: it must export
    ``createScene({THREE, renderer, loaders})`` returning
    ``{scene, cameras, update}`` with at least one camera, because
    ``check_shaders.mjs`` boots the workspace through ``openHost`` before it
    force-compiles.  ``extra`` writes further files under ``src/`` (a fixture
    the scene imports).  Returns (exit_code, combined output); 0 == every
    program compiled, and the LAST line of the output is the JSON report.

    ``audit_module`` scopes the STATIC audit to one staged file (the compile
    stage still covers everything).  Use it when a fixture of your own calls
    ``patchStandard``: the audit's per-file rule cannot see that shader.js
    declares and binds ``uTime`` at assembly time, so it reports an
    undeclared/unbound pair on GLSL that is correct.  ``src/lib/`` is already
    exempt for that reason (``shader_report.mjs``); a fixture is not.

    This replaced a ``shader_check(fixture_src, ...)`` that staged only
    ``src/fixture.js`` and could therefore never get past
    ``compile preflight failed: missing src/scene.js`` — every one of the 32
    module tests that wanted a GPU compile had hand-rolled the same 17-line
    workaround around it (consolidation, 2026-09-01).
    """
    _three_ready()
    with tempfile.TemporaryDirectory(prefix="c3d-compile-scene-") as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        _stage(root / "src", libs)
        (root / "src" / "scene.js").write_text(scene_src, encoding="utf-8")
        for rel, text in (extra or {}).items():
            (root / "src" / rel).write_text(text, encoding="utf-8")
        out = subprocess.run(
            ["node", "--import", str(RESOLVE_HOOK), str(HARNESS / "runtime_js" / "check_shaders.mjs"),
             "--ws", str(root), "--timeout-ms", str(int(timeout_s * 1000))]
            + (["--module", audit_module] if audit_module else []),
            capture_output=True, text=True, timeout=timeout_s + 30, cwd=str(HARNESS / "runtime_js"),
            env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        )
        return out.returncode, (out.stdout + "\n" + out.stderr)[-4000:]
