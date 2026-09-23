"""signage.js — the default font path loads under plain node with no node_modules."""

from __future__ import annotations

from tests.scene_runtime.lib._probe import measure


def test_default_font_path_yields_real_glyph_geometry():
    """In a bare temp dir with no node_modules and no NODE_PATH — the case that
    threw MODULE_NOT_FOUND and killed every census-side `makeText`."""
    m = measure("""
import { makeText } from './lib/signage.js';
const a = await makeText('OPEN', { size: 1 });
a.geometry.computeBoundingBox();
const bb = a.geometry.boundingBox;
console.log(JSON.stringify({ w: bb.max.x - bb.min.x, h: bb.max.y - bb.min.y, d: bb.max.z - bb.min.z,
                             verts: a.geometry.attributes.position.count }));
""", ("signage.js",))
    assert m["w"] > m["h"] > 0.5 and m["h"] <= 1.1, m
    assert 0.1 < m["d"] < 0.3 and m["verts"] > 500, m
