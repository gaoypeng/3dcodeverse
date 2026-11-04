"""The naming rule: one PascalCase rule that accepts all-caps acronyms (owner, 2026-09-22) and the wrapper copies pinned to it."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from codeverse3d.conventions import PASCAL_RE, split_instance, to_snake

WRAPPERS = Path(__file__).resolve().parents[2] / "codeverse3d" / "languages" / "wrappers"

VALID = ["LED", "TV", "A", "TVStand", "LEDStrip", "USBPort", "HDMIPort1", "CPU_2", "SeatCushion", "Leg_0"]
INVALID = ["led", "tvStand", "seat_cushion", "TV Stand", "Seat Cushion", "TV-Stand", "Seat-Cushion", "", "_Leg", "Leg_", "3D"]

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


def test_one_instance_rule_for_every_reader():
    """split_instance is the one rule all readers share (refine grouping once read DonkeyCart_800006 as instance 800006)."""
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
