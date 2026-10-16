"""D112: ``SceneThreeJsRuntime.check_env`` / ``lib_interface`` — the env module alone, checked before a
recovered env counts as written (argon loop iteration 5: 5 of 21 recovered modules failed only at
assembly — ``materials.js does not export 'gravel'``, ``pavingPatch is not defined`` — and one more was
unlit)."""

from __future__ import annotations

import pytest

from codeverse3d.languages.scene_threejs import LIB_DIR, check_env, lib_interface
from codeverse3d.workspace import Workspace
from tests.scene_runtime.conftest import needs_browser

GOOD = """import * as THREE from 'three';
export function heightAt(x, z) { return 0; }
export function buildEnv(ctx) {
  ctx.scene.background = new THREE.Color(0x88aacc);
  ctx.scene.add(new THREE.DirectionalLight(0xffffff, 2));
  return {};
}
"""
VARIANTS = {
    "bad_import": ("import { gravel } from './lib/materials.js';\n" + GOOD, "gravel"),
    "throws": (GOOD.replace("return {};", "pavingPatch(ctx);\n  return {};"), "pavingPatch"),
    "unlit": (GOOD.replace("ctx.scene.background = new THREE.Color(0x88aacc);", "")
                  .replace("ctx.scene.add(new THREE.DirectionalLight(0xffffff, 2));", ""), "renders black"),
}


def _ws(tmp_path, env: str) -> Workspace:
    ws = Workspace(tmp_path / "run").create()
    (ws.src / "lib").mkdir(parents=True, exist_ok=True)
    for p in LIB_DIR.glob("*.js"):
        (ws.src / "lib" / p.name).write_text(p.read_text())
    (ws.src / "env.js").write_text(env)
    return ws


def test_the_lib_interface_is_the_exported_names_with_their_parameters(tmp_path):
    text = lib_interface(_ws(tmp_path, GOOD))
    row = next(r for r in text.splitlines() if r.startswith("lib/sky.js: "))
    assert "makeSky(scene, opts = {})" in row and "SKY_GRADE" in row
    assert "gravel" not in text.split("lib/materials.js: ")[1].split("\n")[0].split(", ")


@needs_browser
@pytest.mark.node
def test_a_good_env_passes_and_each_broken_one_says_why(tmp_path):
    assert check_env(_ws(tmp_path / "good", GOOD)) == ""
    for name, (env, says) in VARIANTS.items():
        error = check_env(_ws(tmp_path / name, env))
        assert says in error, (name, error)
    assert not list((tmp_path / "good" / "run" / "src").glob("_c3v_env_probe*")), "the probe entry is deleted"


def test_an_arrow_function_export_is_listed_with_its_parameters(tmp_path):
    """D112 review: the material factories are ``export const asphalt = (o = {}) => …`` — listed as bare
    names, a single-shot writer could not tell them from constants.  A parenthesised value is not a call."""
    from codeverse3d.languages._js_lint import export_signatures

    row = next(r for r in lib_interface(_ws(tmp_path, GOOD)).splitlines() if r.startswith("lib/materials.js: "))
    assert "asphalt(o = {})" in row.split(": ", 1)[1].split(", ")
    src = ("export const a = (x, {y = 1} = {}) => x;\nexport const b = async (p) => p;\nexport const c = q => q;\n"
           "export let d = function named(m, n) { return m; };\nexport const K = (1 + 2) * 3;\nexport const ARR = [1, 2];\n")
    assert export_signatures(src) == ["ARR", "K", "a(x, {y = 1} = {})", "b(p)", "c(q)", "d(m, n)"]
