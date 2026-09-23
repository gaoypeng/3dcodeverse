"""flowers.js — regression: the flowers cast the shadow of their petal CARDS."""
from __future__ import annotations

import pytest
from _probe import measure

pytestmark = pytest.mark.node

_LIBS = ("shader.js", "noise.js", "grass.js", "foliage_shade.js", "finish.js", "flowers.js")


# Run a material's patch chain the way three does, and hand back the GLSL.
_COMPILE = """
const compile = (mat, vs, fs) => {
  const shader = { uniforms: {}, vertexShader: vs, fragmentShader: fs };
  mat.onBeforeCompile(shader, {});
  return shader;
};
const STD_V = '#include <begin_vertex>\\n';
const STD_F = 'void main() {\\n#include <color_fragment>\\n}\\n';
const DEPTH_F = 'void main() {\\n#include <alphatest_fragment>\\n}\\n';
"""


def test_the_shadow_is_cut_to_the_petal_and_not_to_its_card():
    """three's depth shader has no `<color_fragment>`, so the petal carving was
    dropped in silence and every daisy stamped a slab.  The cut is spliced above
    the alpha test and reads the SURFACE's profile uniform."""
    out = measure(_COMPILE + """
import { makeFlowers } from './lib/flowers.js';
const m = makeFlowers({ kind: 'daisy', extent: 2, density: 4,
                        shadows: true }).children[0];
const dep = m.customDepthMaterial;
const d = compile(dep, STD_V, DEPTH_F);
const surf = compile(m.material, STD_V, STD_F);
console.log(JSON.stringify({
  cuts: /discard/.test(d.fragmentShader),
  byProfile: d.fragmentShader.indexOf('flowProfile(vFlow.y, uFlowProf)') >= 0,
  aboveAlphaTest: d.fragmentShader.indexOf('discard')
      < d.fragmentShader.indexOf('#include <alphatest_fragment>'),
  // The one thing that makes the cut the SAME cut: the depth material is
  // handed the surface's own uniform map, profile and all.
  sameProfileObject: dep.userData.uniforms.uFlowProf
      === m.material.userData.uniforms.uFlowProf,
  uploaded: !!d.uniforms.uFlowProf,
  varying: /varying vec4 vFlow;/.test(d.fragmentShader),
  writesVarying: /vFlow = vec4/.test(d.vertexShader),
  // and the surface still discards by the same profile.
  surfaceProfile: surf.fragmentShader.indexOf('flowProfile(') >= 0,
  // one helper, not two: the vertex head carries it as well and a second
  // copy in one stage is a GLSL redefinition.
  vertexCopies: (d.vertexShader.match(/float flowProfile\\(/g) || []).length,
  fragCopies: (d.fragmentShader.match(/float flowProfile\\(/g) || []).length,
}));
""", _LIBS)
    assert out["cuts"] and out["byProfile"], out
    assert out["aboveAlphaTest"], out
    assert out["sameProfileObject"] and out["uploaded"], out
    assert out["varying"] and out["writesVarying"], out
    assert out["surfaceProfile"], out
    assert out["fragCopies"] == 1, out
    assert out["vertexCopies"] == 0, out
