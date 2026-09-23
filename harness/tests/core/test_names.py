"""The naming rule: one PascalCase rule (``conventions.PASCAL_RE``) that accepts all-caps
acronyms (owner, 2026-09-22), the stdlib-only wrapper copies pinned to it, and the
snake round trip every gate matches parts by."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import StaticPlan
from codeverse3d.conventions import PASCAL_RE, to_pascal, to_snake
from codeverse3d.languages.blender import lint_blender_source
from codeverse3d.languages.cadquery import lint_cadquery_source
from codeverse3d.languages.urdf import _IDENT as URDF_LINK_RE
from codeverse3d.texturing.materials import family_for
from codeverse3d.tracks.planner import normalise_names, plan_example

WRAPPERS = Path(__file__).resolve().parents[2] / "codeverse3d" / "languages" / "wrappers"

VALID = ["LED", "TV", "A", "TVStand", "LEDStrip", "USBPort", "HDMIPort1", "CPU_2", "SeatCushion", "Leg_0"]
INVALID = ["led", "tvStand", "seat_cushion", "TV Stand", "Seat Cushion", "TV-Stand", "Seat-Cushion", "", "_Leg", "Leg_", "3D"]
#: acronym names: snake key, and what to_pascal rebuilds from that key (the acronym is lost)
SNAKE = {"LED": ("led", "Led"), "TV": ("tv", "Tv"), "A": ("a", "A"), "TVStand": ("tv_stand", "TvStand"),
         "LEDStrip": ("led_strip", "LedStrip"), "USBPort": ("usb_port", "UsbPort"),
         "HDMIPort1": ("hdmi_port1", "HdmiPort1"), "CPU_2": ("cpu_2", "Cpu2")}


def _wrapper_assign(file: str, name: str) -> ast.AST:
    tree = ast.parse((WRAPPERS / file).read_text())
    return next(n for n in tree.body if isinstance(n, (ast.Assign, ast.FunctionDef))
                and (getattr(n, "name", None) == name or any(getattr(t, "id", None) == name for t in getattr(n, "targets", ()))))


def _wrapper_pascal_re() -> re.Pattern[str]:
    node = _wrapper_assign("run_cq.py", "PASCAL_RE")
    return re.compile(ast.literal_eval(node.value.args[0]))  # type: ignore[attr-defined]


def _wrapper_snake():
    ns: dict = {"re": re}
    exec(compile(ast.Module(body=[_wrapper_assign("run_bpy_links.py", "_snake")], type_ignores=[]), "run_bpy_links", "exec"), ns)
    return ns["_snake"]


@pytest.mark.parametrize("name", VALID)
def test_acronym_names_are_valid_pascal(name):
    assert PASCAL_RE.match(name)


@pytest.mark.parametrize("name", INVALID)
def test_lowercase_first_spaces_hyphens_and_empty_are_rejected(name):
    assert not PASCAL_RE.match(name)


def test_the_cadquery_wrapper_copy_is_the_conventions_rule():
    """run_cq.py cannot import codeverse3d, so it keeps a copy — the same pattern, same verdicts."""
    wrap = _wrapper_pascal_re()
    assert wrap.pattern == PASCAL_RE.pattern
    assert [bool(wrap.match(n)) for n in VALID + INVALID] == [bool(PASCAL_RE.match(n)) for n in VALID + INVALID]


def test_the_links_wrapper_snake_copy_is_to_snake():
    snake = _wrapper_snake()
    for n in [*VALID, *INVALID[:-3], "LPCompressor", "Seat2Cushion"]:
        if n.strip():
            assert snake(n) == to_snake(n), n


@pytest.mark.parametrize("name", list(SNAKE))
def test_snake_round_trip_of_acronym_names(name):
    snake, rebuilt = SNAKE[name]
    assert to_snake(name) == snake
    assert to_pascal(snake) == rebuilt                 # rebuilding from the key loses the acronym
    if "_" not in name:                                # ... but for a plan name never the key itself
        assert to_snake(to_pascal(name)) == snake == to_snake(rebuilt)


def test_to_pascal_keeps_a_valid_name_and_still_normalises_the_rest():
    assert [to_pascal(n) for n in ("TVStand", "LED", "A", "HDMIPort1", "SeatCushion")] == ["TVStand", "LED", "A", "HDMIPort1", "SeatCushion"]
    # an instance suffix / underscores / spaces / hyphens / lowercase still go through to_snake
    # (an instance name is not a plan name: to_pascal folds its suffix, CPU_2 -> Cpu2 -> key cpu2,
    # exactly as Leg_0 -> Leg0 always has)
    assert [to_pascal(n) for n in ("CPU_2", "Leg_0", "tv stand", "LED strip", "seat-cushion", "tvStand", "")] == \
        ["Cpu2", "Leg0", "TvStand", "LedStrip", "SeatCushion", "TvStand", "Part"]


def test_planner_normalisation_keeps_acronym_names():
    d = plan_example(Track.STATIC_OBJECT)
    d["parts"][0]["name"], d["parts"][1]["name"] = "TVStand", "led strip"
    d["parts"][1]["attach_to"] = d["parts"][2]["attach_to"] = "TVStand"
    plan = normalise_names(StaticPlan.model_validate(d))
    assert [p.name for p in plan.parts[:2]] == ["TVStand", "LedStrip"] and plan.parts[1].attach_to == "TVStand"


def test_the_part_name_lints_accept_acronyms():
    src = "import cadquery as cq\nresult = cq.Assembly()\n" + "".join(
        f"result.add(cq.Workplane().box(1, 1, 1), name='{n}', color=cq.Color(1, 0, 0))\n" for n in ("TVStand", "LED", "CPU_2"))
    assert not any("not PascalCase" in f.message for f in lint_cadquery_source(src).findings)
    rep = lint_blender_source("import bpy\nobj = bpy.context.object\nobj.name = 'USBPort'\n")
    assert any("named objects" in f.message and "USBPort" in f.message for f in rep.findings)
    assert all(URDF_LINK_RE.match(n) for n in ("TVStand", "LED", "CPU_2", "A"))


def test_material_words_split_acronyms():
    hit = family_for("PVCPipe")
    assert hit is not None and hit == family_for("pvc pipe")
