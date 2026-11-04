"""Node probes for the shipped effect library (``starter/src/lib``)."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from codeverse3d.spatial.node import node_modules_dir, run_node
from codeverse3d.spatial.render_scene import SceneRenderError, run_scene_script

HARNESS = Path(__file__).resolve().parents[3]
LIB_DIR = HARNESS / "codeverse3d" / "languages" / "scene_threejs" / "starter" / "src" / "lib"

pytestmark = pytest.mark.node

def _three_ready() -> None:
    if not (node_modules_dir() / "three" / "package.json").is_file():
        pytest.skip("runtime_js/node_modules/three missing (npm ci in runtime_js)")


def _stage(root: Path, libs: tuple[str, ...]) -> None:
    (root / "lib").mkdir()
    # the hub modules every effect imports ride along whatever the test names
    for name in sorted(set(libs) | {"shader.js", "noise.js", "materials.js", "lifecycle.js"}):
        shutil.copy(LIB_DIR / name, root / "lib" / name)


def measure(script: str, libs: tuple[str, ...] = (), *, timeout_s: float = 120.0) -> dict:
    """Run ``script`` (an ES module."""
    _three_ready()
    with tempfile.TemporaryDirectory(prefix="c3d-libprobe-") as tmp:
        root = Path(tmp)
        _stage(root, libs)
        (root / "probe.mjs").write_text(script, encoding="utf-8")
        out = run_node(root / "probe.mjs", cwd=root, timeout_s=timeout_s, three_hook=True, check=False)
        assert out.rc == 0, out.stderr_tail
        lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
        assert lines, "probe printed nothing"
        return json.loads(lines[-1])


def compile_scene(scene_src: str, libs: tuple[str, ...] = (), *,
                  extra: dict[str, str] | None = None,
                  audit_module: str | None = None,
                  report: Path | None = None,
                  timeout_s: float = 180.0) -> tuple[int, str]:
    """Compile every program a fixture SCENE builds, on the headless GPU."""
    _three_ready()
    with tempfile.TemporaryDirectory(prefix="c3d-compile-scene-") as tmp:
        root = Path(tmp)
        (root / "src").mkdir()
        _stage(root / "src", libs)
        (root / "src" / "scene.js").write_text(scene_src, encoding="utf-8")
        for rel, text in (extra or {}).items():
            (root / "src" / rel).write_text(text, encoding="utf-8")
        # through the production driver runner, so a browser lost under load is retried
        # once on an owned browser exactly as a build's probe is (run_node alone had no retry)
        try:
            out = run_scene_script("check_shaders.mjs",
                                   ["--ws", str(root), "--timeout-ms", str(int(timeout_s * 1000))]
                                   + (["--module", audit_module] if audit_module else [])
                                   + (["--out", str(report)] if report else []),
                                   timeout_s=timeout_s + 30)
        except SceneRenderError as e:
            return 2, str(e)[-4000:]
        return out.rc, (out.stdout + "\n" + out.stderr)[-4000:]
