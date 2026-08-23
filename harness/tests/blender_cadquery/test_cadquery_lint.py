"""Offline tests for the cadquery lint."""

from __future__ import annotations

from codeverse.contracts.artifacts import Severity
from codeverse.languages.cadquery.lint import lint_cadquery_source

GOOD = '''
import cadquery as cq
import math

top = cq.Workplane("XY").box(0.5, 0.5, 0.04).translate((0, 0, 0.58))
result = cq.Assembly(name="Table")
result.add(top, name="TableTop", color=cq.Color(0.5, 0.3, 0.2))
'''


def _msgs(r, sev=None):
    return [f.message for f in r.findings if sev is None or f.severity == sev]


def test_good_passes() -> None:
    r = lint_cadquery_source(GOOD)
    assert r.gate == "lint:cadquery" and r.passed, _msgs(r)


def test_missing_result_and_guarded_result() -> None:
    r = lint_cadquery_source("import cadquery as cq\nx = cq.Workplane().box(1, 1, 1)\n")
    assert not r.passed and any("module-level `result`" in m for m in _msgs(r, Severity.ERROR))
    r = lint_cadquery_source("import cadquery as cq\nif __name__ == '__main__':\n    result = cq.Workplane().box(1, 1, 1)\n")
    f = [x for x in r.findings if "result" in x.message][0]
    assert f.severity == Severity.ERROR and "NOT under" in f.fix_hint


def test_forbidden_imports_and_calls() -> None:
    src = ("import cadquery as cq\nimport os\nfrom OCP.BRepPrimAPI import BRepPrimAPI_MakeBox\nresult = cq.Workplane().box(1, 1, 1)\n"
           "show_object(result)\ncq.exporters.export(result, 'x.step')\nresult.save('x.step')\nopen('f', 'w')\n")
    r = lint_cadquery_source(src)
    e = _msgs(r, Severity.ERROR)
    assert any("`os`" in m for m in e) and any("`OCP`" in m for m in e)
    assert any("show_object" in m for m in e) and any("cq.exporters.export" in m for m in e)
    assert any("result.save" in m for m in e) and any("open()" in m for m in e)


def test_pitfalls() -> None:
    src = ("import cadquery as cq\nimport math\ns = cq.Solid.makeSphere(1)\nw = cq.Workplane().box(1, 1, 1).rotate((0, 0, 0), (0, 0, 1), math.pi / 2)\n"
           "result = cq.Assembly()\nresult.add(w)\nresult.add(s, name='my_part')\n")
    r = lint_cadquery_source(src)
    w, e = _msgs(r, Severity.WARN), _msgs(r, Severity.ERROR)
    assert any("makeSphere" in m for m in w)
    assert any("DEGREES" in m for m in e)
    assert any("without name=" in m for m in w)
    assert any("not PascalCase" in m and "my_part" in m for m in w)


def test_bare_workplane_warns() -> None:
    r = lint_cadquery_source("import cadquery as cq\nresult = cq.Workplane().box(1, 1, 1)\n")
    assert r.passed and any("ONE unnamed part" in m for m in _msgs(r, Severity.WARN))


def test_syntax_error() -> None:
    r = lint_cadquery_source("import cadquery as cq\nresult = (\n")
    assert not r.passed and "SyntaxError" in r.findings[0].message
