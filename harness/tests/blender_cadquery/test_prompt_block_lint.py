"""Every python block in the blender/cadquery prompts must pass its own lint.

Regression for two false positives that blocked the build on code copied verbatim from
the cookbook: the bmesh ensure_lookup_table rule firing on ``e.verts[0]`` (BMEdge), and
'Anisotropic' / 'Specular Tint' flagged as removed Principled BSDF inputs.  An agent that
pastes a cookbook snippet into ``src/model.py`` (with the standard imports) must never
get a lint ERROR for it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from codeverse.languages.blender import lint_blender_source
from codeverse.languages.cadquery import lint_cadquery_source

PROMPTS = Path(__file__).resolve().parents[2] / "codeverse" / "prompts"
FENCE = re.compile(r"^```(?:py|python)\s*$(.*?)^```\s*$", re.M | re.S)

BLENDER_HEADER = (
    "import bpy\nimport bmesh\nimport math\nimport random\nfrom mathutils import Vector, Matrix\n"
)
CADQUERY_HEADER = "import cadquery as cq\nimport math\nimport random\n"


def blocks(rel: str) -> list[tuple[int, str]]:
    text = (PROMPTS / rel).read_text()
    out = []
    for m in FENCE.finditer(text):
        out.append((text[: m.start()].count("\n") + 2, m.group(1)))
    assert out, f"{rel}: no python blocks found"
    return out


@pytest.mark.parametrize("rel", ["blender/cookbook.md", "blender/contract.md"])
def test_blender_prompt_blocks_lint_clean(rel: str) -> None:
    for line, code in blocks(rel):
        src = code if "import bpy" in code else BLENDER_HEADER + code
        r = lint_blender_source(src, expect_names=False)
        errs = [(f.data.get("line"), f.message) for f in r.findings if f.severity.value == "error"]
        assert not errs, f"{rel} block at line {line}: {errs}"


@pytest.mark.parametrize("rel", ["cadquery/cookbook.md", "cadquery/contract.md"])
def test_cadquery_prompt_blocks_lint_clean(rel: str) -> None:
    for line, code in blocks(rel):
        src = code if "import cadquery" in code else CADQUERY_HEADER + code
        if not re.search(r"^result\s*=", src, re.M):
            src += "\nresult = cq.Assembly()\n"  # fragments: satisfy the structural rule only
        r = lint_cadquery_source(src)
        errs = [(f.data.get("line"), f.message) for f in r.findings if f.severity.value == "error"]
        assert not errs, f"{rel} block at line {line}: {errs}"
