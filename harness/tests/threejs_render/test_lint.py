"""Static lint of three.js object code."""

from __future__ import annotations

from pathlib import Path

import pytest

import codeverse3d.languages.threejs as lint_mod
from codeverse3d.contracts.artifacts import Severity
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.languages.threejs import lint_workspace
from codeverse3d.workspace import Workspace


@pytest.fixture
def no_node_syntax(monkeypatch):
    """Run lint offline: pretend every file parses."""
    monkeypatch.setattr(lint_mod, "check_syntax", lambda paths: {})


def _msgs(rep, sev=None):
    return [f.message for f in rep.findings if sev is None or f.severity == sev]


def test_clean_workspace_passes(stool_ws: Workspace, no_node_syntax):
    rep = lint_workspace(stool_ws)
    assert rep.gate == "lint:threejs"
    assert rep.passed, rep.findings
    assert rep.findings == []


def test_missing_object_js(tmp_path: Path, no_node_syntax):
    ws = Workspace(tmp_path)
    (ws.src / "parts").mkdir(parents=True)
    rep = lint_workspace(ws)
    assert not rep.passed
    assert any("object.js is missing" in m for m in _msgs(rep, Severity.ERROR))


def test_forbidden_apis_and_imports(stool_ws: Workspace, no_node_syntax):
    (stool_ws.src / "parts" / "legs.js").write_text(
        "import * as THREE from 'three';\nimport x from 'https://cdn.example.com/x.js';\nimport y from 'lodash';\n"
        "import { q } from './nope.js';\n// document in a comment is fine\nconst s = 'window in a string is fine';\n"
        "export function buildLegs(THREE_) { fetch('http://x'); const c = document.createElement('canvas');\n"
        "  const r = new THREE.WebGLRenderer(); const cam = new THREE.PerspectiveCamera(); return new THREE.Group(); }\n"
    )
    rep = lint_workspace(stool_ws)
    errs = _msgs(rep, Severity.ERROR)
    assert not rep.passed
    assert any("URL 'https://cdn.example.com/x.js'" in m for m in errs)
    assert any("package 'lodash'" in m for m in errs)
    assert any("does not exist: './nope.js'" in m for m in errs)
    assert any("fetch" in m and "legs.js:7" in m for m in errs)
    assert any("document" in m and "legs.js:7" in m for m in errs)
    assert any("renderers" in m and "legs.js:8" in m for m in errs)
    assert any("cameras" in m for m in _msgs(rep, Severity.WARN))
    # comment/string mentions must not trigger
    assert not any("legs.js:5" in m or "legs.js:6" in m for m in errs)


def test_part_export_name_and_dead_file(stool_ws: Workspace, no_node_syntax):
    (stool_ws.src / "parts" / "extra.js").write_text("import * as THREE from 'three';\nexport function buildWrong(THREE_) { return new THREE.Group(); }\n")
    rep = lint_workspace(stool_ws)
    warns = _msgs(rep, Severity.WARN)
    assert any("expected `export function buildExtra(THREE)`" in m for m in warns)
    assert any("not imported by any module" in m for m in warns)
    (stool_ws.src / "parts" / "extra.js").write_text("export const nothing = 1;\n")
    rep = lint_workspace(stool_ws)
    assert any("buildExtra" in m for m in _msgs(rep, Severity.ERROR))


def test_object_without_build_export(stool_ws: Workspace, no_node_syntax):
    (stool_ws.src / "object.js").write_text("import * as THREE from 'three';\nexport function make() {}\n")
    rep = lint_workspace(stool_ws)
    assert any("must `export function build(THREE)`" in m for m in _msgs(rep, Severity.ERROR))


def test_plan_parts_missing_are_warned(stool_ws: Workspace, no_node_syntax):
    plan = StaticPlan(object_name="Stool", summary="s", overall_bbox=BBox(center=(0, 0.2, 0), extents=(0.4, 0.45, 0.4)),
                      parts=[PartPlan(name="Seat", role="r", description="d", bbox=BBox(center=(0, 0.43, 0), extents=(0.34, 0.04, 0.34))),
                             PartPlan(name="FootRing", role="r", description="d", bbox=BBox(center=(0, 0.1, 0), extents=(0.3, 0.02, 0.3)))])
    stool_ws.write_json(stool_ws.plan_path, plan)
    rep = lint_workspace(stool_ws)
    assert rep.passed
    assert any("FootRing has no file src/parts/foot_ring.js" in m for m in _msgs(rep, Severity.WARN))


@pytest.mark.node
def test_syntax_error_reported_with_line(stool_ws: Workspace):
    p = stool_ws.src / "parts" / "seat.js"
    p.write_text(p.read_text() + "\nconst = 5;\n")
    rep = lint_workspace(stool_ws)
    assert not rep.passed
    assert any("seat.js:13" in m and "SyntaxError" in m for m in _msgs(rep, Severity.ERROR))


@pytest.mark.node
def test_real_syntax_check_on_clean_stool(stool_ws: Workspace):
    assert lint_workspace(stool_ws).passed


@pytest.mark.node
def test_one_node_checks_every_file_and_a_parsed_file_is_not_sent_again(tmp_path: Path, monkeypatch):
    import codeverse3d.languages._js_lint as js_lint

    good = [tmp_path / f"ok_{i}.js" for i in range(3)]
    for i, p in enumerate(good):
        p.write_text(f"import * as THREE from 'three';\nexport const n{i} = await Promise.resolve({i});\n")
    bad = tmp_path / "bad.js"
    bad.write_text("export function f() {\n  return 1;\n}\nconst = 2;\n")
    calls = []
    real = js_lint.run_node
    monkeypatch.setattr(js_lint, "run_node", lambda *a, **k: calls.append(a) or real(*a, **k))
    problems = js_lint.node_check_syntax([*good, bad])
    assert list(problems) == [bad] and problems[bad].line == 4 and "SyntaxError" in problems[bad].message
    assert len(calls) == 1  # one process for four files
    assert js_lint.node_check_syntax(good) == {} and len(calls) == 1  # all three parsed already: no node at all
    assert list(js_lint.node_check_syntax([bad])) == [bad] and len(calls) == 2  # a failure is never cached
