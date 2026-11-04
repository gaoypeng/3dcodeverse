"""The scene post chain (runtime_js/lib/browser/post.js)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from codeverse3d.proc import read_json_or_none
from codeverse3d.spatial.render_scene import run_scene_script
from codeverse3d.workspace import Workspace
from tests.scene_runtime.conftest import needs_browser, needs_node

pytestmark = [pytest.mark.node]

CAMERA = "{ name: 'cam', position: [0, 0, 6], lookAt: [0, 0, 0], fov: 45 }"

SCENE = """
import * as THREE from 'three';

export function createScene() {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x000000);
  scene.add(new THREE.AmbientLight(0xffffff, 1.2));
  const mat = new THREE.MeshStandardMaterial(__MATERIAL__);
  scene.add(new THREE.Mesh(new THREE.BoxGeometry(1.2, 1.2, 1.2), mat));
  __EXTRA__
  return { scene, cameras: [__CAMERA__], update(t, dt) {} };
}
"""

# same albedo, same lighting: the ONLY difference is that one of them emits
EMISSIVE = "{ color: 0x202020, emissive: 0xffffff, emissiveIntensity: 3.0 }"
BRIGHT = "{ color: 0xffffff, emissive: 0x000000 }"


def make_ws(tmp_path: Path, name: str, material: str, extra: str = "") -> Workspace:
    w = Workspace(tmp_path / name).create()
    src = SCENE.replace("__MATERIAL__", material).replace("__CAMERA__", CAMERA).replace("__EXTRA__", extra)
    (w.root / "src").mkdir(parents=True, exist_ok=True)
    (w.root / "src" / "scene.js").write_text(src)
    return w


def mean_lum(path: Path) -> float:
    """Mean luminance of a render, on the host's own 96x54 sampling grid."""
    im = Image.open(path).convert("RGB").resize((96, 54), Image.BILINEAR)
    a = np.asarray(im, dtype=float)
    return float((0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]).mean()) / 255


def render(ws: Workspace, out: Path, *extra_args: str) -> Path:
    """One authored-camera frame at t=0, straight through the driver so the test can pass --post-options / --no-post (render_scene() owns neither)."""
    out.mkdir(parents=True, exist_ok=True)
    run_scene_script("render_scene.mjs", [
        "--ws", str(ws.root), "--out", str(out), "--cameras", "authored",
        "--orbit-views", "none", "--times", "0", "--width", "256", "--height", "144",
        "--fps-seconds", "0", "--no-settle", "--timeout-ms", "120000", *extra_args,
    ], timeout_s=150)
    return out / "cam_t0.png"


@needs_node
@needs_browser
def test_bloom_is_selective_and_the_chain_alone_is_colour_neutral(tmp_path):
    """An EMISSIVE box blooms."""
    emissive = make_ws(tmp_path, "emissive", EMISSIVE)
    plain = make_ws(tmp_path, "plain", BRIGHT)

    e_raw, e_post = render(emissive, tmp_path / "e_raw", "--no-post"), render(emissive, tmp_path / "e_post")
    p_raw, p_post = render(plain, tmp_path / "p_raw", "--no-post"), render(plain, tmp_path / "p_post")

    assert read_json_or_none(tmp_path / "e_post" / "metrics.json")["census"]["post"]["bloom_sources"] >= 1
    post = read_json_or_none(tmp_path / "p_post" / "metrics.json")["census"]["post"]
    assert post["bloom_sources"] == 0
    # an ungraded scene reports a neutral grade; --no-post leaves the chain off
    assert post["enabled"] is True and post["grade_neutral"] is True and post["warnings"] == []
    assert read_json_or_none(tmp_path / "p_raw" / "metrics.json")["census"]["post"] == {"enabled": False}

    assert mean_lum(e_post) - mean_lum(e_raw) > 0.004, "emissive box did not bloom"
    assert abs(mean_lum(p_post) - mean_lum(p_raw)) < 0.005, "a merely bright box must not bloom"
    chain = render(plain, tmp_path / "p_chain", "--post-options", '{"ao":0,"bloom":0}')
    assert abs(mean_lum(chain) - mean_lum(p_raw)) < 0.005, (mean_lum(p_raw), mean_lum(chain))
