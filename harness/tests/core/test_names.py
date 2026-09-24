"""The naming rule: one PascalCase rule (``conventions.PASCAL_RE``) that accepts all-caps
acronyms (owner, 2026-09-22), the stdlib-only wrapper copies pinned to it, and the
snake round trip every gate matches parts by."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from codeverse3d.contracts.common import Track
from codeverse3d.contracts.plan import StaticPlan
from codeverse3d.conventions import PASCAL_RE, part_key, split_instance, to_pascal, to_snake
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


def test_the_naming_rule_and_its_stdlib_wrapper_copies():
    """One rule; run_cq.py / run_bpy_links.py cannot import codeverse3d, so they keep copies — same verdicts."""
    assert [n for n in VALID if not PASCAL_RE.match(n)] == []
    assert [n for n in INVALID if PASCAL_RE.match(n)] == []
    wrap = _wrapper_pascal_re()
    assert wrap.pattern == PASCAL_RE.pattern
    assert [bool(wrap.match(n)) for n in VALID + INVALID] == [bool(PASCAL_RE.match(n)) for n in VALID + INVALID]
    snake = _wrapper_snake()
    for n in [*VALID, *INVALID[:-3], "LPCompressor", "Seat2Cushion"]:
        if n.strip():
            assert snake(n) == to_snake(n), n


def test_snake_round_trip_and_to_pascal():
    for name, (snake, rebuilt) in SNAKE.items():
        assert to_snake(name) == snake, name
        assert to_pascal(snake) == rebuilt, name        # rebuilding from the key loses the acronym
        if "_" not in name:                              # ... but for a plan name never the key itself
            assert to_snake(to_pascal(name)) == snake == to_snake(rebuilt), name
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


def test_one_instance_rule_for_every_reader():
    """``conventions.split_instance`` is the rule the measure table, the contract match and
    refine grouping read (four hand-written copies until 2026-09-23 — the refine grouping
    read ``DonkeyCart_800006``, a scene asset's all-digit hex id, as instance 800006)."""
    from codeverse3d.orchestrator import _canon_target, _instance_base
    from codeverse3d.spatial.measure import instance_groups

    rows = {"Leg_2": ("Leg", "2"), "Leg.001": ("Leg", "001"), "leg-3": ("leg", "3"), "Leg_1_2": ("Leg_1", "2"),
            "Leg": ("Leg", ""), "Shelf2": ("Shelf2", ""), "HDMIPort1": ("HDMIPort1", ""),
            "DonkeyCart_800006": ("DonkeyCart_800006", ""), "Leg_": ("Leg_", "")}
    assert {n: split_instance(n) for n in rows} == rows
    assert list(instance_groups(["Leg_1", "Leg.002", "Shelf", "Shelf2", "Cart_800006", "Cart_801"])) == \
        ["Leg", "Shelf", "Shelf2", "Cart_800006", "Cart_801"]
    assert _instance_base("DonkeyCart_800006") == "DonkeyCart_800006" and _instance_base("") == "overall"
    assert _canon_target("leg_1", {"leg": "Leg"}) == "Leg_1"


def test_one_part_rule_for_the_texture_pass_and_the_normaliser():
    """N4: the texture pass stripped an instance suffix only when 2+ copies existed and the
    normaliser never did, so a single ``Arm-2`` the contract gate accepts went untextured and
    instanced legs kept their default factors.  Both read ``conventions.part_key`` now."""
    from codeverse3d.texturing.apply import node_part_lookup

    plan = {"leg", "seat", "arm", "handle", "link", "cpu_2"}
    rows = {"Leg_0": "leg", "Arm-2": "arm", "Handle.001": "handle", "Link__2": "link", "Leg_0__1": "leg",
            "Seat": "seat", "CPU_2": "cpu_2", "Shelf": None}
    assert {n: part_key(n, plan) for n in rows} == rows
    parts = {k: k.upper() for k in plan}
    assert node_part_lookup(list(rows), parts)["Arm-2"] == "ARM"
