"""The scene post chain (runtime_js/lib/browser/post.js): GTAO + selective bloom + grade.

The three properties that must not regress, each pinned against a raw render of
the SAME scene:

  * the chain is colour-neutral by itself — with AO and bloom off, a post render
    matches `--no-post` (only MSAA separates them).  This is what lets the grade
    default to the identity and lets us attribute any shift to AO or bloom.
  * bloom is SELECTIVE: it fires on an emissive material and does nothing at all
    on a scene whose brightest thing is merely bright.  A global luminance
    bright-pass (what the reference chain used) cannot tell those apart on our
    renderer — the sky dome outshines every authored emissive — and fogged a
    daylit frame, so the mask is the whole design and this is its guard.
  * `scene.userData.grade` reaches the shader and is clamped.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from codeverse3d.spatial.render_scene import post_chain_args, read_metrics, run_scene_script
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
    """One authored-camera frame at t=0, straight through the driver so the test
    can pass --post-options / --no-post (render_scene() owns neither)."""
    out.mkdir(parents=True, exist_ok=True)
    run_scene_script("render_scene.mjs", [
        "--ws", str(ws.root), "--out", str(out), "--cameras", "authored",
        "--orbit-views", "none", "--times", "0", "--width", "256", "--height", "144",
        "--fps-seconds", "0", "--no-settle", "--timeout-ms", "120000", *extra_args,
    ], timeout_s=150)
    return out / "cam_t0.png"


def test_post_chain_args_follow_the_env_switch(monkeypatch):
    monkeypatch.delenv("C3D_POST", raising=False)
    assert post_chain_args() == []          # ON by default for scene pictures
    monkeypatch.setenv("C3D_POST", "0")
    assert post_chain_args() == ["--no-post"]
    monkeypatch.setenv("C3D_POST", "on")
    assert post_chain_args() == []


@needs_node
@needs_browser
def test_chain_alone_is_colour_neutral(tmp_path):
    """AO off, bloom off, grade at its defaults → the same picture as no chain.

    Without this the grade could drift and nobody would notice: every later
    measurement of AO or bloom is a delta against a raw render.
    """
    ws = make_ws(tmp_path, "neutral", BRIGHT)
    raw = render(ws, tmp_path / "raw", "--no-post")
    chain = render(ws, tmp_path / "chain", "--post-options", '{"ao":0,"bloom":0}')
    assert abs(mean_lum(chain) - mean_lum(raw)) < 0.005, (mean_lum(raw), mean_lum(chain))


@needs_node
@needs_browser
def test_bloom_is_selective_not_a_brightness_threshold(tmp_path):
    """An EMISSIVE box blooms; an equally bright NON-emissive box does not.

    Both scenes are lit the same and the plain box is the brighter of the two in
    the raw render — so anything that keys off frame luminance fails this test,
    which is exactly the reference chain's failure mode.
    """
    emissive = make_ws(tmp_path, "emissive", EMISSIVE)
    plain = make_ws(tmp_path, "plain", BRIGHT)

    e_raw, e_post = render(emissive, tmp_path / "e_raw", "--no-post"), render(emissive, tmp_path / "e_post")
    p_raw, p_post = render(plain, tmp_path / "p_raw", "--no-post"), render(plain, tmp_path / "p_post")

    assert read_metrics(tmp_path / "e_post")["census"]["post"]["bloom_sources"] >= 1
    assert read_metrics(tmp_path / "p_post")["census"]["post"]["bloom_sources"] == 0

    assert mean_lum(e_post) - mean_lum(e_raw) > 0.004, "emissive box did not bloom"
    assert abs(mean_lum(p_post) - mean_lum(p_raw)) < 0.005, "a merely bright box must not bloom"


@needs_node
@needs_browser
def test_scene_grade_hint_reaches_the_shader_and_is_clamped(tmp_path):
    """`scene.userData.grade` is the scene's only lever on the grade; a value far
    outside the sane range is clamped rather than obeyed or ignored."""
    ws = make_ws(tmp_path, "graded", BRIGHT, extra="scene.userData.grade = { exposure: 99, warmth: 0.4 };")
    out = tmp_path / "graded_out"
    render(ws, out, "--post-options", '{"ao":0,"bloom":0}')
    grade = read_metrics(out)["census"]["post"]["grade"]
    assert grade["exposure"] == 8.0        # clamped from 99
    assert grade["warmth"] == pytest.approx(0.4)
    assert read_metrics(out)["census"]["post"]["grade_neutral"] is False


@needs_node
@needs_browser
def test_no_post_leaves_the_chain_off(tmp_path):
    ws = make_ws(tmp_path, "off", BRIGHT)
    out = tmp_path / "off_out"
    render(ws, out, "--no-post")
    assert read_metrics(out)["census"]["post"] == {"enabled": False}


@needs_node
@needs_browser
def test_ungraded_scene_reports_a_neutral_grade(tmp_path):
    ws = make_ws(tmp_path, "ungraded", BRIGHT)
    out = tmp_path / "ungraded_out"
    render(ws, out)
    post = read_metrics(out)["census"]["post"]
    assert post["enabled"] is True and post["grade_neutral"] is True
    assert post["warnings"] == [] and json.dumps(post)   # serialisable into metrics.json
