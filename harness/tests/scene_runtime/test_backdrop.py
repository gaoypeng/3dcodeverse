"""One backdrop classifier for the census and the coverage instrument.

`lib/host_census.mjs` (what `content_bbox` covers) and `lib/host_coverage.mjs`
(what `content_frac` measures) used to carry two copies of the same sky/ground
rules; both now call `lib/backdrop.mjs`.  If a copy ever comes back, the census
and the per-frame coverage can disagree about what "content" is — which is
exactly what the frame gate and the orbit framing depend on.
"""

from __future__ import annotations

import json
import re

import pytest

from codeverse.config import get_settings
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


def test_content_span_rule_only_applies_when_a_content_bbox_is_known():
    """The census classifies before it has a content bbox and therefore passes
    span 0 (its historical rule set); coverage knows the content bbox and adds
    the "far larger than the content" rule.  One function, one extra argument —
    not two rule sets."""
    assert _classify([["Path", [30, 0.1, 30], False, 5]]) == ["ground"]
    assert _classify([["Path", [30, 0.1, 30], False, 0]]) == ["content"]
    # a normal object is content whether or not the content span is known
    assert _classify([["Cabin", [6, 4, 5], False, 3]]) == ["content"]
    assert _classify([["Cabin", [6, 4, 5], False, 0]]) == ["content"]


def test_the_rules_live_in_exactly_one_file():
    hits = []
    for p in sorted((RUNTIME_JS / "lib").rglob("*.mjs")) + sorted((RUNTIME_JS / "lib").rglob("*.js")):
        text = p.read_text()
        if re.search(r"skydome\|skybox|\bGROUND_NAME_RE\s*=|\bSKY_NAME_RE\s*=", text):
            hits.append(p.name)
    assert hits == ["backdrop.mjs"], hits
