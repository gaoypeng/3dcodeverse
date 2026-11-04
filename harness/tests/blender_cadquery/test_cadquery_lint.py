"""Offline tests for the cadquery lint."""

from __future__ import annotations

from codeverse3d.contracts.artifacts import Severity
from codeverse3d.languages.cadquery import lint_cadquery_source


def _msgs(r, sev=None):
    return [f.message for f in r.findings if sev is None or f.severity == sev]


def test_missing_result_guarded_result_and_syntax_error() -> None:
    r = lint_cadquery_source("import cadquery as cq\nx = cq.Workplane().box(1, 1, 1)\n")
    assert not r.passed and any("module-level `result`" in m for m in _msgs(r, Severity.ERROR))
    r = lint_cadquery_source("import cadquery as cq\nif __name__ == '__main__':\n    result = cq.Workplane().box(1, 1, 1)\n")
    f = [x for x in r.findings if "result" in x.message][0]
    assert f.severity == Severity.ERROR and "NOT under" in f.fix_hint
    r = lint_cadquery_source("import cadquery as cq\nresult = (\n")
    assert not r.passed and "SyntaxError" in r.findings[0].message


def test_pitfalls() -> None:
    src = ("import cadquery as cq\nimport math\ns = cq.Solid.makeSphere(1)\nw = cq.Workplane().box(1, 1, 1).rotate((0, 0, 0), (0, 0, 1), math.pi / 2)\n"
           "result = cq.Assembly()\nresult.add(w)\nresult.add(s, name='my_part')\n")
    r = lint_cadquery_source(src)
    w, e = _msgs(r, Severity.WARN), _msgs(r, Severity.ERROR)
    assert any("makeSphere" in m for m in w)
    assert any("DEGREES" in m for m in e)
    assert any("without name=" in m for m in w)
    assert any("not PascalCase" in m and "my_part" in m for m in w)


def _rotate_src(expr: str, prelude: str = "") -> str:
    return (f"import cadquery as cq\nimport math\nimport numpy as np\n{prelude}n_pins, pitch_deg, spindles, i, a = 6, 30.0, 5, 2, 0.4\n"
            f"w = cq.Workplane().box(1, 1, 1).rotate((0, 0, 0), (0, 0, 1), {expr})\nresult = cq.Assembly()\nresult.add(w, name='Part')\n")


def test_rotate_angle_expression_table() -> None:
    degrees = ("i * 360 / n_pins", "math.degrees(a) * 2", "a * 180 / math.pi", "90")
    radians = ("math.pi / 2", "i * 2 * math.pi / n_pins", "math.radians(30) * 2", "math.tau / 4", "np.deg2rad(30)")
    for expr in degrees:
        r = lint_cadquery_source(_rotate_src(expr))
        assert r.passed and not any("DEGREES" in m for m in _msgs(r)), (expr, _msgs(r))
    for expr in radians:
        r = lint_cadquery_source(_rotate_src(expr))
        assert not r.passed and any("DEGREES" in m for m in _msgs(r, Severity.ERROR)), (expr, _msgs(r))

