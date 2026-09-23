"""Static checks on fenced snippets the run tests cannot see: URDF frame recipe, GLSL-in-JS, the single-shot format."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from tests.prompts.conftest import blocks, read_prompt

XML_FILES = ["urdf/contract.md", "urdf/cookbook.md"]


def _is_template(body: str) -> bool:
    """Fill-in skeletons carry symbolic origins (``PX PY PZ``) — not FK-checkable."""
    return any(not _NUM.match(tok) for el in ET.fromstring(body).iter("origin") for tok in el.get("xyz", "0 0 0").split())


_NUM = re.compile(r"^-?\d+(\.\d+)?([eE]-?\d+)?$")


@pytest.mark.parametrize("rel", XML_FILES)
def test_xml_examples_follow_the_enforced_frame_recipe(rel: str, tmp_path) -> None:
    """Every worked URDF passes the lint and the build's FK rule: visual origin = −(link frame world)."""
    from codeverse3d.languages.urdf import lint_urdf_text
    from codeverse3d.spatial.joints_model import fk, load_urdf

    checked = 0
    for i, body in enumerate(blocks(rel, "xml")):
        if _is_template(body):
            continue
        findings, _ = lint_urdf_text(body)
        assert findings == [], f"{rel} block {i}: lint findings {[f.message for f in findings]}"
        path = tmp_path / f"block{i}.urdf"
        path.write_text(body)
        robot = load_urdf(path, load_meshes=False)
        T = fk(robot, {})
        for name, link in robot.links.items():
            assert np.allclose(T[name] @ link.visual_origin, np.eye(4), atol=1e-6), \
                f"{rel} block {i}: link {name}: visual origin must be -(link frame world) = {(-T[name][:3, 3]).round(4).tolist()}"
        checked += 1
    assert checked > 0, f"{rel}: expected at least one FK-checkable xml block"


def test_glsl_strings_are_sane() -> None:
    """GLSL lives inside JS template strings; audit the obvious silent killers
    INSIDE the js code blocks (prose may mention them as pitfalls)."""
    for rel in ("scene_threejs/cookbook.md",):
        for b, body in enumerate(blocks(rel, "js")):
            assert not re.search(r"^\s*#version", body, re.MULTILINE), \
                f"{rel} js block {b}: never write #version in ShaderMaterial GLSL"
            for m in re.finditer(r"^(.*#include <\w+>.*)$", body, re.MULTILINE):
                line = m.group(1).strip()
                if "'" in line:
                    # JS source line: inside every quoted chunk, each (escaped-\n separated)
                    # GLSL line holding an include must be ONLY the include
                    for chunk in re.findall(r"'([^']*#include[^']*)'", line):
                        for glsl_line in re.split(r"\\+n", chunk):
                            if "#include" not in glsl_line:
                                continue
                            assert re.fullmatch(r"\s*#include <\w+>\s*", glsl_line), \
                                f"{rel} js block {b}: include shares a line with code: {line!r}"
                else:
                    # raw GLSL line inside a template literal: must stand alone
                    assert re.fullmatch(r"#include <\w+>", line), \
                        f"{rel} js block {b}: #include must be alone on its line: {line!r}"


def test_singleshot_example_parses_with_harness_parser() -> None:
    from codeverse3d.tracks import generation as gen

    text = read_prompt("system/singleshot_format.md")
    example = blocks("system/singleshot_format.md", "text")[-1]
    files = gen.parse_multifile(example)
    assert set(files) == {"src/parts/seat.js", "src/object.js"}
    assert "buildSeat" in files["src/parts/seat.js"]
    assert "=== FILE" not in "".join(files.values())
    # the format doc and the parser must agree on the markers
    assert "=== FILE: " in text and "=== END FILE ===" in text
