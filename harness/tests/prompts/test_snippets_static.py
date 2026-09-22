"""Static validity of every fenced snippet: python parses, XML/URDF parses, JS parses."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from tests.prompts.conftest import PROMPT_FILES, blocks, read_prompt

PY_FILES = [f for f in PROMPT_FILES if f.split("/")[0] in ("blender", "cadquery", "urdf")]
JS_FILES = [f for f in PROMPT_FILES if f.split("/")[0] in ("threejs", "scene_threejs")]
JS_FILES.append("system/singleshot_format.md")
XML_FILES = ["urdf/contract.md", "urdf/cookbook.md"]


@pytest.mark.parametrize("rel", PY_FILES)
def test_python_blocks_parse(rel: str) -> None:
    found = 0
    for i, body in enumerate(blocks(rel, "python")):
        try:
            ast.parse(body)
        except SyntaxError as e:  # pragma: no cover - failure path
            pytest.fail(f"{rel} python block {i} does not parse: {e}")
        found += 1
    assert found > 0, f"{rel}: expected at least one python block"


@pytest.mark.parametrize("rel", XML_FILES)
def test_xml_blocks_are_valid_urdf(rel: str) -> None:
    found = 0
    for i, body in enumerate(blocks(rel, "xml")):
        root = ET.fromstring(body)
        assert root.tag == "robot" and root.get("name"), f"{rel} xml block {i}: not a <robot>"
        links = {ln.get("name") for ln in root.findall("link")}
        children = set()
        for j in root.findall("joint"):
            jtype = j.get("type")
            parent = j.find("parent").get("link")
            child = j.find("child").get("link")
            assert parent in links and child in links, f"{rel} block {i}: joint {j.get('name')} references unknown link"
            assert child not in children, f"{rel} block {i}: link {child} has two parents"
            children.add(child)
            origin = j.find("origin")
            assert origin is not None and origin.get("rpy", "0 0 0").split() == ["0", "0", "0"], \
                f"{rel} block {i}: joint {j.get('name')} must have rpy='0 0 0' (frame recipe)"
            limit = j.find("limit")
            if jtype in ("revolute", "prismatic"):
                assert limit is not None and limit.get("lower") is not None and limit.get("upper") is not None, \
                    f"{rel} block {i}: {jtype} joint {j.get('name')} needs lower/upper"
                assert float(limit.get("lower")) < float(limit.get("upper"))
            if jtype == "continuous":
                assert limit is None or (limit.get("lower") is None and limit.get("upper") is None)
            axis = j.find("axis")
            if jtype != "fixed":
                v = [float(x) for x in axis.get("xyz").split()]
                assert abs(sum(a * a for a in v) - 1.0) < 1e-6, f"{rel} block {i}: non-unit axis on {j.get('name')}"
        roots = links - children
        assert len(roots) == 1, f"{rel} block {i}: expected a single root link, got {roots}"
        for ln in root.findall("link"):
            vis, col = ln.findall("visual"), ln.findall("collision")
            assert len(vis) == 1 and len(col) == 1, f"{rel} block {i}: link {ln.get('name')} needs one <visual> + one <collision>"
            vo, co = vis[0].find("origin"), col[0].find("origin")
            assert vo is not None and co is not None and vo.get("xyz") == co.get("xyz"), \
                f"{rel} block {i}: link {ln.get('name')}: collision origin must equal the visual origin"
            for tag in (vis[0], col[0]):
                mesh = tag.find("geometry/mesh")
                assert mesh is not None and mesh.get("filename") == f"meshes/{ln.get('name')}.glb", \
                    f"{rel} block {i}: link {ln.get('name')} must reference meshes/<link>.glb"
                assert mesh.get("scale") is None, f"{rel} block {i}: mesh scale is forbidden"
        found += 1
    assert found > 0, f"{rel}: expected at least one xml block"


def _is_template(body: str) -> bool:
    """Fill-in skeletons carry symbolic origins (``PX PY PZ``) — not FK-checkable."""
    return any(not _NUM.match(tok) for el in ET.fromstring(body).iter("origin") for tok in el.get("xyz", "0 0 0").split())


_NUM = re.compile(r"^-?\d+(\.\d+)?([eE]-?\d+)?$")


@pytest.mark.parametrize("rel", XML_FILES)
def test_xml_examples_follow_the_enforced_frame_recipe(rel: str, tmp_path) -> None:
    """Every worked URDF in the agent-facing docs must pass the harness lint AND the FK
    consistency rule the build enforces: meshes hold WORLD coordinates, so FK(q=0) of a
    link composed with its visual origin is the identity (visual origin = −link frame).
    This is the recipe the reviewer found the docs contradicting (visual origin 0 0 0)."""
    from codeverse3d.languages.urdf import lint_urdf_text
    from codeverse3d.spatial.joints import fk, load_urdf

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


@pytest.mark.node
@pytest.mark.parametrize("rel", [f for f in JS_FILES if f != "system/singleshot_format.md"])
def test_js_blocks_syntax(rel: str, tmp_path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    bs = blocks(rel, "js")
    assert bs, f"{rel}: expected at least one js block"
    for i, body in enumerate(bs):
        p = tmp_path / f"block_{i}.mjs"
        p.write_text(body)
        proc = subprocess.run([node, "--check", str(p)], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, f"{rel} js block {i} syntax error:\n{proc.stderr[-1500:]}"


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
    gen = pytest.importorskip("codeverse3d.tracks.generation")
    text = read_prompt("system/singleshot_format.md")
    example = blocks("system/singleshot_format.md", "text")[-1]
    files = gen.parse_multifile(example)
    assert set(files) == {"src/parts/seat.js", "src/object.js"}
    assert "buildSeat" in files["src/parts/seat.js"]
    assert "=== FILE" not in "".join(files.values())
    assert "SINGLE_SHOT_FORMAT" in dir(gen)
    # the format doc and the parser must agree on the markers
    assert "=== FILE: " in text and "=== END FILE ===" in text
