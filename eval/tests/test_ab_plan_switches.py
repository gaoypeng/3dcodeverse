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


def test_a_dead_switch_does_not_block_pin_plan(monkeypatch, capsys):
    """C3D_PLAN_FEATURES is read by nothing, so it cannot change a plan: next to a
    generation-side switch it must not make --pin-plan refuse the A/B (until 2026-09-22
    its never-implemented feature names did).  The generation-side switch is the skills
    OFF arm: skills are on by default since 2026-09-22, so `C3D_SKILLS=1` would be no switch."""
    import bench.ab_plan as A

    seen: list[dict[str, str]] = []

    class _V:
        decision, reason, caution = "keep", "stubbed", ""

    monkeypatch.setattr(A, "run_ab", lambda battery, out, opts, **kw: (seen.append(opts.variant_env), _V())[1])
    A.main(["--prompts", "p.yaml", "--out", "o", "--no-preflight", "--allow-siblings", "--pin-plan",
            "--variant-env", "C3D_PLAN_FEATURES=all", "--variant-env", "C3D_SKILLS=0"])
    assert seen == [{"C3D_PLAN_FEATURES": "all", "C3D_SKILLS": "0"}], capsys.readouterr()


def test_a_skills_ab_spells_its_off_arm_explicitly(monkeypatch):
    """Skills are ON by default since 2026-09-22 (harness docs/SKILLS.md §6).  The control
    arm is the driver's env minus every variant key, so the only way to get an arm WITHOUT
    skills is to name `C3D_SKILLS=0` in the variant — and `C3D_SKILLS=1` is now two
    identical arms, the byte-identical-arms trap tracks/plan_features.py exists for."""
    from bench._ab_report import CONTROL, VARIANT
    from bench.ab_plan import AbOptions, child_env
    from codeverse3d.skills.config import SKILLS_ENV, skills_enabled

    def enabled(env: dict[str, str]) -> bool:
        monkeypatch.delenv(SKILLS_ENV, raising=False)
        if SKILLS_ENV in env:
            monkeypatch.setenv(SKILLS_ENV, env[SKILLS_ENV])
        return skills_enabled()

    shell = {"PATH": "/usr/bin", SKILLS_ENV: "0"}   # exported in the launching shell: never reaches the control
    off = AbOptions(variant_env={SKILLS_ENV: "0"})
    assert enabled(child_env(CONTROL, off, shell)) is True
    assert enabled(child_env(VARIANT, off, shell)) is False
    on = AbOptions(variant_env={SKILLS_ENV: "1"})
    assert enabled(child_env(CONTROL, on, shell)) is enabled(child_env(VARIANT, on, shell)) is True
