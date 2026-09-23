"""windows.js — regression: the cell id hashed a perspective-interpolated key, and every facade rendered as static."""

from __future__ import annotations

from tests.scene_runtime.lib._probe import SHADER_JS, measure

_LIBS = ("shader.js", "windows.js")

# A fresh window-interior patch, run through the two hooks of SHADER_JS.
_PATCHED = """
const patched = (opts) => {
  const sh = fake();
  patchWindowInteriors(new THREE.MeshStandardMaterial(), opts || {})
      .onBeforeCompile(sh);
  return sh;
};
"""

_PRELUDE = """
import * as THREE from 'three';
import { patchWindowInteriors } from './lib/windows.js';
""" + SHADER_JS + _PATCHED


def _measure(script: str, libs: tuple[str, ...] = _LIBS) -> dict:
    return measure(_PRELUDE + script, libs)


def test_the_cell_id_is_a_whole_number_lattice_or_the_facade_is_static():
    """A perspective-correct varying comes back a few ULPs apart per pixel, and two
    chained astraHash21 calls amplify that into a per-pixel coin flip.  Every term
    of the id is rounded; drop one floor() and the facade fizzes again."""
    out = _measure("""
const sh = patched();
console.log(JSON.stringify({ fs: sh.fragmentShader }));
""")
    fs = out["fs"]
    assert "vec3 winKq = floor(vWinKey * 16.0 + 0.5);" in fs
    assert ("float winFace = dot(floor(winNn * 4.0 + 0.5),"
            " vec3(11.0, 29.0, 53.0));") in fs
    assert ("float winB = floor(astraHash21(winKq.xz + winKq.y) * 29.0)"
            " + winFace;") in fs
    assert "vec2 winId = floor(winG)" in fs
    assert "vec2(uWinSeed + winB, floor(uWinSeed * 1.7) - winB);" in fs
    # The furniture noise enters its lattice through the cell HASHES, not
    # through the id, so its coordinate stays small enough to interpolate.
    assert "vec2 winNz = winHit.xy * 3.0 + vec2(winHt, winHl) * 37.0;" in fs
    assert "winShade *= mix(0.55, 1.0, astraNoise2(winNz));" in fs
