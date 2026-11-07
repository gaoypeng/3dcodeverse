"""Call attribution: explicit > a label naming its own job > ambient > the label."""

from __future__ import annotations

from codeverse3d.cost.context import (
    attribute,
    context_from_label,
)
from codeverse3d.cost.types import Role, Stage


def test_label_tells_stage_role_and_round():
    c = context_from_label("refine_drip_tray")
    assert c.stage is Stage.REFINE and c.role is None  # a generation label states no role...
    assert attribute(label="refine_drip_tray").role is Role.GENERATOR  # ...the stage derives it
    c = context_from_label("judge:static_object:r02:s1")
    assert c.stage is Stage.JUDGE and c.role is Role.JUDGE and c.round == 2
    assert context_from_label("planner-retry").stage is Stage.PLAN
    assert context_from_label("captioner").role is Role.CAPTIONER
    assert context_from_label("").stage is None
    assert context_from_label("baseline_repair1").stage is Stage.REPAIR  # repair wins
    zl = attribute(label="zone-layout")  # the ledger agrees with the guard's stage='plan'
    assert zl.stage is Stage.PLAN and zl.role is Role.PLANNER


