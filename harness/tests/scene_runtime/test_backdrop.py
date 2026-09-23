"""One backdrop classifier serves census and frame coverage."""

from __future__ import annotations

import json

import pytest

from codeverse3d.config import get_settings
from tests.scene_runtime.conftest import needs_node, run_node_json

pytestmark = [pytest.mark.node, needs_node]

RUNTIME_JS = get_settings().runtime_js_dir()

CASES = [
    # (name, box size [sx, sy, sz], instanced, content span, expected)
    ("SkyDome", [400, 200, 400], False, 0, "sky"),
    ("Anything", [3000, 10, 3000], False, 0, "sky"),
    ("GroundPlane", [60, 0.2, 60], False, 0, "ground"),
    ("Terrain", [30, 1.0, 30], False, 0, "ground"),          # named ground, flat enough
    ("Meadow", [100, 0.5, 100], False, 0, "ground"),         # unnamed but very wide + flat
    ("Grass", [80, 1.0, 80], True, 0, "content"),            # scatter: instanced wins
    ("Cabin", [6, 4, 5], False, 0, "content"),
    ("Cabin", [6, 4, 5], False, 3, "content"),
    ("Path", [30, 0.1, 30], False, 5, "ground"),             # far larger than the content bbox
    ("Path", [30, 0.1, 30], False, 0, "content"),            # same box, no content bbox known
    ("Rug", [3, 0.05, 3], False, 40, "content"),             # small relative to content: not backdrop
]


def _classify(cases):
    body = f"""
import {{ classifyBackdrop }} from '{RUNTIME_JS}/lib/backdrop.mjs';
const cases = {json.dumps(cases)};
const out = cases.map(([name, size, instanced, span, lo]) => {{
  const o = lo || [-size[0] / 2, 0, -size[2] / 2];   // the box's min corner: centred on the origin by default
  return classifyBackdrop(
    {{ name, isInstancedMesh: instanced }},
    {{ min: {{ x: o[0], y: o[1], z: o[2] }}, max: {{ x: o[0] + size[0], y: o[1] + size[1], z: o[2] + size[2] }} }},
    span,
  );
}});
console.log(JSON.stringify(out));
"""
    return run_node_json(body)


def test_classifier_rule_table():
    got = _classify([[c[0], c[1], c[2], c[3]] for c in CASES] + [
        # `sunRig` parks its 'SunDisc' (no word boundary for SKY_NAME_RE) at 3 600 m: content by
        # name, and it blew the content bbox to 2.5 km — the overview rig then framed the sun.
        # A small thing kilometres away is sky whatever its name; the same disc near is content.
        ["SunDisc", [48, 48, 48], False, 40, [2100, 2600, 1800]],
        ["SunDisc", [48, 48, 48], False, 40, [10, 20, 10]],
    ])
    assert got == [c[4] for c in CASES] + ["sky", "content"], list(zip([c[0] for c in CASES], got, strict=False))
