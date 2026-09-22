"""The A/B switch registry: which env switches any code path ACTUALLY reads.

Born as the switchboard for the plan-loop-engineering wave (CQ-5): six features were
declared under ``C3D_PLAN_FEATURES``, none was ever implemented, and one A/B run is on
record printing "keep, mean delta +0.344" for two byte-identical arms.  The feature
runtime (``parse_features`` / ``plan_feature_on``) was deleted 2026-08-28 per its own
instruction, and the six names with it (2026-09-22); what remains is the guard rail —
:data:`LIVE_SWITCHES` / :data:`DEAD_SWITCHES` (kept honest by
``tests/orchestrator_tracks/test_plan_features.py``, which greps the tree) and the
``--pin-plan`` blocker rule ``eval/bench/ab_plan.py`` consults so a paired A/B cannot
silently pin away the very thing it is testing.
"""

from __future__ import annotations

PLAN_FEATURES_ENV = "C3D_PLAN_FEATURES"

#: env switches that act AFTER planning — ``--pin-plan`` may share one plan across arms
#: that differ only by these.  Anything not listed is treated as plan-side: refusing to
#: pin costs one noisy A/B, pinning wrongly costs a confident wrong answer
#: (eval/docs/EVAL.md §8.1 measured it — the A/A's worst pair differed 1 part vs 10).
GENERATION_SIDE_ENV: frozenset[str] = frozenset({"C3D_SKILLS", "C3D_SKILLS_MAX", "C3D_SKILLS_UNVERIFIED",
                                                 "C3D_SKILLS_ONLY", "C3D_REFERENCE_DIFF", "C3D_SEED_RECIPES"})


def pin_plan_blockers(variant_env: dict[str, str]) -> list[str]:
    """Which of ``variant_env``'s switches forbid sharing one plan between the arms.

    Empty list = every switch this A/B changes acts after planning, so both arms can be
    seeded with the same ``plan.json`` and the paired difference stops carrying the
    planner's spread (the dominant variance term, eval/docs/EVAL.md §8.1).  A
    :data:`DEAD_SWITCHES` key blocks nothing: no code reads it, so it cannot change a plan.
    """
    return [f"{key} is not known to act after planning" for key in sorted(variant_env)
            if key not in GENERATION_SIDE_ENV and key not in DEAD_SWITCHES]

#: Plan-loop switches the tree ACTUALLY reads, name → the module that reads it.  Kept
#: honest by tests/orchestrator_tracks/test_plan_features.py, which greps the tree.
LIVE_SWITCHES: dict[str, str] = {
    "C3D_PLAN_BRIEF": "codeverse3d/tracks/planner.py",  # brief.py merged in, 2026-08-28
    # re-sample a degenerate plan from the original prompt instead of editing it in context
    "C3D_PLAN_RESTART": "codeverse3d/tracks/planner.py",
    "C3D_SCOPED_PARTS": "codeverse3d/tracks/depth.py",
    "C3D_REFERENCE_DIFF": "codeverse3d/judges/vlm_judge.py",  # reference.py merged in, 2026-08-28
    # the skill system (design: scratchpad/skills/design/DESIGN.md §6.5).  All three are
    # read at call time by one module, so an A/B arm that sets them really differs.
    "C3D_SKILLS": "codeverse3d/skills/config.py",
    "C3D_SKILLS_MAX": "codeverse3d/skills/config.py",
    "C3D_SKILLS_UNVERIFIED": "codeverse3d/skills/config.py",
    "C3D_SKILLS_ONLY": "codeverse3d/skills/config.py",
    "C3D_SEED_RECIPES": "codeverse3d/config.py",
    # wire the scene texture pack into the scene loop: a stage before env/zones, and the
    # pack description (texturing.plan.texture_pack_prompt) in both prompts.  OFF by
    # default — it costs an image-model call per run and nobody has measured what it buys.
    "C3D_SCENE_TEXTURES": "codeverse3d/config.py",
    # model-transport switches (2026-08-28, the hung-read waves): streaming with
    # inter-chunk stall detection, and the IPv4-only transport.  Read at call time
    # by every gemini request, so a control arm can set either to 0.
    "C3D_STREAM": "codeverse3d/models/gemini.py",
    "C3D_IPV4": "codeverse3d/models/gemini.py",
}

#: Switches that are DECLARED but read by no code path, with the reason.  An A/B arm that
#: differs only by one of these is byte-identical to its control, so ``ab_plan`` refuses
#: it: a rig that cannot tell a live switch from a dead one produces confident verdicts
#: about nothing.  A name leaves this dict in the same commit as the code that reads it.
DEAD_SWITCHES: dict[str, str] = {
    PLAN_FEATURES_ENV: "none of its six features was ever implemented; nothing reads this variable",
    "C3D_DETAIL_ROUNDS": "the surface-detail round went with the judgement stops that offered it (2026-09-22)",
    "C3D_FEWER_TURNS": "the fewer-turns bundle was deleted after its A/B read out flat (2026-09-22, COST §29)",
}


def dead_env_keys(env: dict[str, str]) -> list[str]:
    """The keys of ``env`` that no code path reads — empty when the arm really differs."""
    return sorted(k for k in env if k in DEAD_SWITCHES)


__all__ = ["DEAD_SWITCHES", "GENERATION_SIDE_ENV", "LIVE_SWITCHES", "PLAN_FEATURES_ENV", "dead_env_keys",
           "pin_plan_blockers"]
