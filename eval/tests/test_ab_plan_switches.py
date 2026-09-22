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


def test_ab_plan_still_accepts_a_live_switch(monkeypatch, capsys):
    """The guard must not block a real A/B: it fires only when EVERY key is dead.

    Offline — --no-preflight and --allow-siblings keep the provider health check and the
    docs/COST.md §23 admission check out of it, and run_ab itself is stubbed."""
    import bench.ab_plan as A

    seen: list[dict[str, str]] = []
    class _V:
        decision, reason, caution = "keep", "stubbed", ""

    monkeypatch.setattr(A, "run_ab", lambda battery, out, opts, **kw: (seen.append(opts.variant_env), _V())[1])
    for argv in (["--variant-env", "C3D_PLAN_BRIEF=off"],
                 ["--variant-env", "C3D_PLAN_FEATURES=all", "--variant-env", "C3D_PLAN_BRIEF=off"]):
        A.main(["--prompts", "p.yaml", "--out", "o", "--no-preflight", "--allow-siblings", *argv])

    assert len(seen) == 2, capsys.readouterr()
    assert "C3D_PLAN_BRIEF" in seen[0]
