"""The A/B switch registry: which env switches any code path ACTUALLY reads.

Born as the switchboard for the plan-loop-engineering wave (CQ-5): six features were
declared under ``CV3D_PLAN_FEATURES``, none was ever implemented, and one A/B run is on
record printing "keep, mean delta +0.344" for two byte-identical arms.  The feature
runtime (``parse_features`` / ``plan_feature_on``) was deleted 2026-08-28 per its own
instruction; the six names stay only so ``pin_plan_blockers`` can classify a pinned
A/B, and what else remains is the guard rail — :data:`LIVE_SWITCHES` / :data:`DEAD_SWITCHES` (kept honest by
``tests/orchestrator_tracks/test_plan_features.py``, which greps the tree) and the
``--pin-plan`` blocker rule ``bench/ab_plan.py`` consults so a paired A/B cannot
silently pin away the very thing it is testing.
"""

from __future__ import annotations

PLAN_FEATURES_ENV = "CV3D_PLAN_FEATURES"

#: the six switch names the wave defined.  Nothing reads them at runtime any more —
#: they remain because ``pin_plan_blockers`` still classifies a CV3D_PLAN_FEATURES
#: value inside a MIXED A/B env, and that classification must not silently widen.
FIT = "fit"
CONTACTS = "contacts"
GRAPH = "graph"
GRAPH_BUDGET = "graph_budget"
GRAPH_EXAMPLE = "graph_example"
CONSISTENCY = "consistency"
KNOWN_FEATURES: tuple[str, ...] = (FIT, CONTACTS, GRAPH, GRAPH_BUDGET, GRAPH_EXAMPLE, CONSISTENCY)
#: the one generation-side switch; ``pin_plan_blockers`` treats the rest as plan-side
GENERATION_SIDE: frozenset[str] = frozenset({CONTACTS})
#: env switches that act AFTER planning — ``--pin-plan`` may share one plan across arms
#: that differ only by these.  Anything not listed is treated as plan-side: refusing to
#: pin costs one noisy A/B, pinning wrongly costs a confident wrong answer
#: (docs/EVAL.md §8.1 measured it — the A/A's worst pair differed 1 part vs 10).
GENERATION_SIDE_ENV: frozenset[str] = frozenset({"CV3D_SKILLS", "CV3D_SKILLS_MAX", "CV3D_SKILLS_UNVERIFIED",
                                                 "CV3D_SKILLS_ONLY",
                                                 "CV3D_DETAIL_ROUNDS", "CV3D_REFERENCE_DIFF",
                                                 "CV3D_FEWER_TURNS", "CV3D_SEED_RECIPES"})


def pin_plan_blockers(variant_env: dict[str, str]) -> list[str]:
    """Which of ``variant_env``'s switches forbid sharing one plan between the arms.

    Empty list = every switch this A/B changes acts after planning, so both arms can be
    seeded with the same ``plan.json`` and the paired difference stops carrying the
    planner's spread (the dominant variance term, docs/EVAL.md §8.1).
    """
    blockers: list[str] = []
    for key, value in sorted(variant_env.items()):
        if key == PLAN_FEATURES_ENV:
            names = set(KNOWN_FEATURES) if value.strip() == "all" else {
                n.strip().lstrip("-") for n in value.split(",") if n.strip()}
            for name in sorted(names & (set(KNOWN_FEATURES) - GENERATION_SIDE)):
                blockers.append(f"{PLAN_FEATURES_ENV}={name} changes the plan itself")
        elif key in GENERATION_SIDE_ENV:
            continue
        else:
            blockers.append(f"{key} is not known to act after planning")
    return blockers

#: Plan-loop switches the tree ACTUALLY reads, name → the module that reads it.  Kept
#: honest by tests/orchestrator_tracks/test_plan_features.py, which greps the tree.
LIVE_SWITCHES: dict[str, str] = {
    "CV3D_PLAN_BRIEF": "codeverse/tracks/planner.py",  # brief.py merged in, 2026-08-28
    # plan-time geometry re-ask for articulated plans (tracks/plan_checks.py); plan-side,
    # so an A/B over it can never --pin-plan.  Off by default (A/B 2026-08-28: no gain).
    "CV3D_PLAN_GEOMETRY": "codeverse/tracks/planner.py",
    # re-sample a degenerate plan from the original prompt instead of editing it in context
    "CV3D_PLAN_RESTART": "codeverse/tracks/planner.py",
    "CV3D_SCOPED_PARTS": "codeverse/tracks/depth.py",
    "CV3D_DETAIL_ROUNDS": "codeverse/tracks/lifecycle.py",
    "CV3D_REFERENCE_DIFF": "codeverse/judges/vlm_judge.py",  # reference.py merged in, 2026-08-28
    # the skill system (design: scratchpad/skills/design/DESIGN.md §6.5).  All three are
    # read at call time by one module, so an A/B arm that sets them really differs.
    "CV3D_SKILLS": "codeverse/skills/config.py",
    "CV3D_SKILLS_MAX": "codeverse/skills/config.py",
    "CV3D_SKILLS_UNVERIFIED": "codeverse/skills/config.py",
    "CV3D_SKILLS_ONLY": "codeverse/skills/config.py",
    # fewer turns (docs/COST.md §29): build folds the gates in, write_file lints, the refine
    # prompt inlines its files, the baseline prompt asks for every file in turn 1.  One
    # switch, read at call time by config.fewer_turns_enabled; acts after planning.
    "CV3D_FEWER_TURNS": "codeverse/config.py",
    "CV3D_SEED_RECIPES": "codeverse/config.py",
    # wire the scene texture pack into the scene loop: a stage before env/zones, and the
    # pack description (texturing.plan.texture_pack_prompt) in both prompts.  OFF by
    # default — it costs an image-model call per run and nobody has measured what it buys.
    "CV3D_SCENE_TEXTURES": "codeverse/config.py",
    # model-transport switches (2026-08-28, the hung-read waves): streaming with
    # inter-chunk stall detection, and the IPv4-only transport.  Read at call time
    # by every gemini request, so a control arm can set either to 0.
    "CV3D_STREAM": "codeverse/models/gemini.py",
    "CV3D_IPV4": "codeverse/models/gemini.py",
}

#: Switches that are DECLARED but read by no code path, with the reason.  An A/B arm that
#: differs only by one of these is byte-identical to its control, so ``ab_plan`` refuses
#: it: a rig that cannot tell a live switch from a dead one produces confident verdicts
#: about nothing.  A name leaves this dict in the same commit as the code that reads it.
DEAD_SWITCHES: dict[str, str] = {
    PLAN_FEATURES_ENV: "no feature in KNOWN_FEATURES is implemented; nothing reads this variable",
}


def dead_env_keys(env: dict[str, str]) -> list[str]:
    """The keys of ``env`` that no code path reads — empty when the arm really differs."""
    return sorted(k for k in env if k in DEAD_SWITCHES)


__all__ = ["CONSISTENCY", "CONTACTS", "DEAD_SWITCHES", "FIT", "GENERATION_SIDE",
           "GENERATION_SIDE_ENV", "GRAPH", "GRAPH_BUDGET", "GRAPH_EXAMPLE", "KNOWN_FEATURES",
           "LIVE_SWITCHES", "PLAN_FEATURES_ENV", "dead_env_keys", "pin_plan_blockers"]
