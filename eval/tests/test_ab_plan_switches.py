"""`bench/ab_plan.py` refuses an A/B whose only switch nothing reads (the switch registry is
`codeverse3d/tracks/plan_features.py`, tested with the harness)."""

from __future__ import annotations

import pytest


def test_ab_plan_refuses_an_ab_whose_only_switch_is_dead(capsys):
    """`--variant-env C3D_PLAN_FEATURES=all` produced a full battery and the verdict
    'keep, mean delta +0.344' for two byte-identical arms.  It must not start."""
    import bench.ab_plan as A

    with pytest.raises(SystemExit):
        A.main(["--prompts", "p.yaml", "--out", "o", "--variant-env", "C3D_PLAN_FEATURES=all"])
    err = capsys.readouterr().err
    assert "nothing reads" in err and "C3D_PLAN_FEATURES" in err


def test_a_dead_switch_does_not_block_pin_plan(monkeypatch, capsys):
    """A dead switch beside a live one (the skills OFF arm) runs, and does not make --pin-plan refuse."""
    import bench.ab_plan as A

    seen: list[dict[str, str]] = []

    class _V:
        decision, reason, caution = "keep", "stubbed", ""

    monkeypatch.setattr(A, "run_ab", lambda battery, out, opts, **kw: (seen.append(opts.variant_env), _V())[1])
    A.main(["--prompts", "p.yaml", "--out", "o", "--no-preflight", "--pin-plan",
            "--variant-env", "C3D_PLAN_FEATURES=all", "--variant-env", "C3D_SKILLS=0"])
    assert seen == [{"C3D_PLAN_FEATURES": "all", "C3D_SKILLS": "0"}], capsys.readouterr()
