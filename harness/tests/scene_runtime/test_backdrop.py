"""One backdrop classifier serves census and frame coverage."""

from __future__ import annotations

import json
import re

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
const out = cases.map(([name, size, instanced, span]) => classifyBackdrop(
  {{ name, isInstancedMesh: instanced }},
  {{ min: {{ x: -size[0] / 2, y: 0, z: -size[2] / 2 }}, max: {{ x: size[0] / 2, y: size[1], z: size[2] / 2 }} }},
  span,
));
console.log(JSON.stringify(out));
"""
    return run_node_json(body)


def test_classifier_rule_table():
    got = _classify([[c[0], c[1], c[2], c[3]] for c in CASES])
    assert got == [c[4] for c in CASES], list(zip([c[0] for c in CASES], got, strict=True))


def test_a_small_thing_kilometres_away_is_sky_whatever_its_name():
    """`sunRig` parks its 'SunDisc' (no word boundary for SKY_NAME_RE) at 3 600 m: content by
    name, and it blew the content bbox to 2.5 km — the overview rig then framed the sun."""
    body = f"""
import {{ classifyBackdrop }} from '{RUNTIME_JS}/lib/backdrop.mjs';
const disc = classifyBackdrop({{ name: 'SunDisc' }}, {{ min: {{ x: 2100, y: 2600, z: 1800 }}, max: {{ x: 2148, y: 2648, z: 1848 }} }}, 40);
const near = classifyBackdrop({{ name: 'SunDisc' }}, {{ min: {{ x: 10, y: 20, z: 10 }}, max: {{ x: 58, y: 68, z: 58 }} }}, 40);
console.log(JSON.stringify([disc, near]));
"""
    assert run_node_json(body) == ["sky", "content"]


def test_the_rules_live_in_exactly_one_file():
    hits = []
    for p in sorted((RUNTIME_JS / "lib").rglob("*.mjs")) + sorted((RUNTIME_JS / "lib").rglob("*.js")):
        text = p.read_text()
        if re.search(r"skydome\|skybox|\bGROUND_NAME_RE\s*=|\bSKY_NAME_RE\s*=", text):
            hits.append(p.name)
    assert hits == ["backdrop.mjs"], hits
