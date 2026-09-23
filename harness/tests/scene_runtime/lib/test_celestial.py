"""celestial.js: the sky opts out of fog with the marker the static GLSL audit reads."""

from __future__ import annotations

import pytest

from tests.scene_runtime.lib._probe import measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "materials.js", "celestial.js")


def test_the_sky_declares_its_own_fog_opt_out():
    """The option the audit's own warning tells you to pass has to write the
    marker it reads (`3dcode:`; `astra3d:` still accepted on read)."""
    out = measure("""
import { makeStars, makeAurora } from './lib/celestial.js';
const marks = [];
for (const g of [makeStars({ count: 50, seed: 1 }),
                 makeAurora({ seed: 1 })]) {
  g.traverse((o) => {
    for (const m of [].concat(o.material || [])) {
      if (m && m.fragmentShader) {
        marks.push([m.name, m.fog === false,
                    /(3dcode|astra3d):\\s*no-fog/.test(m.fragmentShader)]);
      }
    }
  });
}
console.log(JSON.stringify({ marks }));
""", _LIBS)
    assert out["marks"], "no sky materials found"
    for name, flag, marker in out["marks"]:
        assert flag, f"{name} does not opt out of fog"
        assert marker, f"{name} opts out but writes no marker for the checker"
