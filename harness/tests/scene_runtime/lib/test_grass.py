"""grass.js (12 recorded scenes import it): the field stands on the caller's ground, and
the blade normal is wound the way the strip is."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "grass.js")

# Run a material's patch chain the way three does, and hand back the GLSL.
_COMPILE = """
const compile = (mat) => {
  const shader = {
    uniforms: {},
    vertexShader: '#include <begin_vertex>\\n',
    fragmentShader: 'void main() {\\n#include <color_fragment>\\n}\\n',
  };
  mat.onBeforeCompile(shader, {});
  return shader;
};
"""


def test_blades_and_mat_stand_on_the_callers_ground():
    """Grass that ignores `heightAt` floats over a slope or buries
    itself in it — the one defect a still frame shows immediately."""
    out = measure("""
import { makeGrass } from './lib/grass.js';
const h = (x, z) => 0.8 * Math.sin(x * 0.3) * Math.cos(z * 0.21) + 0.9;
const g = makeGrass({ extent: 10, density: 300, seed: 8, heightAt: h });
const iPos = g.getObjectByName('Blades').geometry.attributes.iPos.array;
let bladeErr = 0;
for (let i = 0; i < iPos.length; i += 3) {
  bladeErr = Math.max(bladeErr,
      Math.abs(iPos[i + 1] - h(iPos[i], iPos[i + 2])));
}
const p = g.getObjectByName('Sward').geometry.attributes.position;
let matErr = 0, lift = 0;
for (let v = 0; v < p.count; v++) {
  const d = p.getY(v) - h(p.getX(v), p.getZ(v));
  matErr = Math.max(matErr, Math.abs(d - 0.012));
  lift = Math.max(lift, d);
}
const flat = makeGrass({ extent: 6, density: 300, seed: 8 });
const fPos = flat.getObjectByName('Blades').geometry.attributes.iPos.array;
let flatMax = 0;
for (let i = 1; i < fPos.length; i += 3) flatMax = Math.max(flatMax, fPos[i]);
console.log(JSON.stringify({
  bladeErr, matErr, lift, flatMax,
  boxLow: g.getObjectByName('Blades').geometry.boundingBox.min.y,
}));
""", _LIBS)
    assert out["bladeErr"] < 1e-4, out
    assert out["matErr"] < 1e-4, out
    # Just clear of the ground: level with it the mat z-fights, higher
    # and the blades look planted in a tray.
    assert 0.005 < out["lift"] < 0.05, out
    # No heightAt: the field rests exactly on y = 0.
    assert out["flatMax"] == 0, out
    assert out["boxLow"] <= 0.11, out


def test_the_shading_normal_is_wound_the_way_the_strip_is():
    """The reference wrote `cross(grTan, grSide)`, the BACK face of the swept
    strip; DOUBLE_SIDED cannot rescue it (vNormal and gl_FrontFacing flip
    together), and a front-lit meadow rendered black."""
    out = measure(_COMPILE + """
import { makeGrass } from './lib/grass.js';
const vs = compile(makeGrass({ extent: 4, density: 100 })
    .getObjectByName('Blades').material).vertexShader;
console.log(JSON.stringify({
  right: vs.includes('cross(grSide, grTan) - grSide * aCorner.x'),
  wrong: vs.includes('cross(grTan, grSide)'),
  guarded: vs.includes('#ifndef FLAT_SHADED'),
}));
""", _LIBS)
    assert out["right"], out
    assert not out["wrong"], out
    # The depth pass defines FLAT_SHADED, and a depth shader has no vNormal.
    assert out["guarded"], out
