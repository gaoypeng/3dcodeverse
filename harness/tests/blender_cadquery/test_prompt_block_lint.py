"""Every Python block in the Blender/CadQuery prompts passes its own lint."""

from __future__ import annotations

import re
from pathlib import Path

from codeverse3d.languages.blender import lint_blender_source
from codeverse3d.languages.cadquery import lint_cadquery_source

PROMPTS = Path(__file__).resolve().parents[2] / "codeverse3d" / "prompts"
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


def test_prompt_blocks_lint_clean() -> None:
    for rel in ("blender/cookbook.md", "blender/contract.md"):
        for line, code in blocks(rel):
            src = code if "import bpy" in code else BLENDER_HEADER + code
            r = lint_blender_source(src, expect_names=False)
            errs = [(f.data.get("line"), f.message) for f in r.findings if f.severity.value == "error"]
            assert not errs, f"{rel} block at line {line}: {errs}"
    for rel in ("cadquery/cookbook.md", "cadquery/contract.md"):
        for line, code in blocks(rel):
            src = code if "import cadquery" in code else CADQUERY_HEADER + code
            if not re.search(r"^result\s*=", src, re.M):
                src += "\nresult = cq.Assembly()\n"
            r = lint_cadquery_source(src)
            errs = [(f.data.get("line"), f.message) for f in r.findings if f.severity.value == "error"]
            assert not errs, f"{rel} block at line {line}: {errs}"
