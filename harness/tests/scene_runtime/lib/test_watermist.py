"""watermist.js (8 recorded scenes import it) — regression: a set sun still lit the mist bank."""

from __future__ import annotations

from tests.scene_runtime.lib._probe import measure

_LIBS = ("shader.js", "watermist.js")


def test_the_sun_below_the_horizon_delivers_nothing():
    """`sunRig` keeps reporting the authored sun after it sets; a sun gain that
    ignored elevation put a lit bank into a night frame."""
    out = measure("""
import { makeWaterMist, makeSpray } from './lib/watermist.js';
const gain = (dir) => [
  makeWaterMist({ sunDir: dir }).getObjectByName('MistCards')
      .material.uniforms.uSunGain.value,
  makeSpray({ rate: 60, sunDir: dir }).getObjectByName('SprayDroplets')
      .material.uniforms.uSunGain.value,
];
const dirOf = (o) => o.getObjectByName('MistCards')
    .material.uniforms.uSunDir.value.toArray();
console.log(JSON.stringify({
  high: gain([-0.65, 0.62, -0.45]),
  grazing: gain([-0.8, 0.06, -0.6]),
  set: gain([-0.6, -0.34, -0.72]),
  none: gain(undefined),
  noneDir: dirOf(makeWaterMist({})),
  unit: dirOf(makeWaterMist({ sunDir: [0, 4, 3] })),
}));
""", _LIBS)
    assert out["set"] == [0, 0], out
    assert all(g > 0.5 for g in out["high"]), out
    # A grazing sun delivers a fraction, not all of it and not none.
    for a, b in zip(out["grazing"], out["high"], strict=True):
        assert 0 < a < b, out
    # No rig to hand: top lit at full strength, which is the old look.
    assert out["none"] == out["high"], out
    assert out["noneDir"] == [0, 1, 0], out
    assert abs(sum(v * v for v in out["unit"]) - 1.0) < 1e-6, out
