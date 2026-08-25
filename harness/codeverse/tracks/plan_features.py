"""Feature switches for the plan-loop-engineering wave, one env variable for all of them.

Every change in that wave (the plan fit check, the rendered contact graph, the brief
attachment graph, …) ships OFF and is A/B'd one at a time with ``bench/ab_plan.py``,
whose arms differ only by environment.  So the switch has to be an env variable, it
has to be read at call time (never cached in ``Settings``), and every change has to
read the SAME variable — a second spelling per change is how a control arm quietly
turns into a variant.

``CV3D_PLAN_FEATURES="fit,contacts"`` turns two features on; ``all`` turns every known
one on and ``all,-graph`` all but one.  Unknown names are ignored (and logged once)
rather than fatal: a typo in a bench command must produce a control run, not a crash
half-way through a battery.

STATUS (CQ-5): none of the six features below is implemented, and nothing outside this
module reads ``CV3D_PLAN_FEATURES`` — the plan-loop changes that DID land each invented
their own spelling instead (see :data:`LIVE_SWITCHES`).  The switch is therefore DEAD,
and an A/B whose arms differ only by it is a no-op by construction: one such run is on
record printing "keep, mean delta +0.344" for two byte-identical arms.  :data:`DEAD_SWITCHES`
exists so ``bench/ab_plan.py`` refuses that A/B instead of producing a verdict.  Delete
this module, or implement a feature and move its variable into ``LIVE_SWITCHES``.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

PLAN_FEATURES_ENV = "CV3D_PLAN_FEATURES"

#: the switches the wave defines, in the order the design doc lists them
#: (``scratchpad/planloop/design/DESIGN.md``).  Add a name here in the same commit as
#: the code that reads it; a feature that is not listed cannot be turned on.
FIT = "fit"                    # plan-level fit check: undeclared deep overlaps, declared gaps → one repair re-ask
CONTACTS = "contacts"          # every declared contact rendered to the builder with its mating faces and overlap rule
GRAPH = "graph"                # brief sub-assemblies carry `mounts_on` edges; parts are grouped by node
GRAPH_BUDGET = "graph_budget"  # plan budget derived from the brief graph instead of the flat floor
GRAPH_EXAMPLE = "graph_example"  # the planner's worked example written as a graph (touches + assembly filled)
CONSISTENCY = "consistency"    # plan overall bbox checked against the brief's dimension rows
KNOWN_FEATURES: tuple[str, ...] = (FIT, CONTACTS, GRAPH, GRAPH_BUDGET, GRAPH_EXAMPLE, CONSISTENCY)

#: Which stage a switch changes.  This is not documentation — ``bench/ab_plan.py --pin-plan``
#: reads it, and pinning a PLAN-side switch would silently disable the very thing under test
#: (both arms would share one plan, so a change that only alters planning becomes a no-op the
#: rig would then report as "no effect").  A switch missing from here is treated as plan-side:
#: refusing to pin costs one noisy A/B, pinning wrongly costs a confident wrong answer.
#: docs/EVAL.md §8.1 measured why pinning matters — the A/A's worst pair differed 1 part vs 10.
PLAN_SIDE: frozenset[str] = frozenset({FIT, GRAPH, GRAPH_BUDGET, GRAPH_EXAMPLE, CONSISTENCY})
GENERATION_SIDE: frozenset[str] = frozenset({CONTACTS})
#: env switches outside CV3D_PLAN_FEATURES, same rule
PLAN_SIDE_ENV: frozenset[str] = frozenset({"CV3D_PLAN_BRIEF", "CV3D_SCOPED_PARTS"})
GENERATION_SIDE_ENV: frozenset[str] = frozenset({"CV3D_SKILLS", "CV3D_SKILLS_MAX", "CV3D_SKILLS_UNVERIFIED",
                                                 "CV3D_DETAIL_ROUNDS", "CV3D_REFERENCE_DIFF"})


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
    "CV3D_PLAN_BRIEF": "codeverse/tracks/brief.py",
    "CV3D_SCOPED_PARTS": "codeverse/tracks/depth.py",
    "CV3D_DETAIL_ROUNDS": "codeverse/tracks/lifecycle.py",
    "CV3D_REFERENCE_DIFF": "codeverse/judges/reference.py",
    # the skill system (design: scratchpad/skills/design/DESIGN.md §6.5).  All three are
    # read at call time by one module, so an A/B arm that sets them really differs.
    "CV3D_SKILLS": "codeverse/skills/config.py",
    "CV3D_SKILLS_MAX": "codeverse/skills/config.py",
    "CV3D_SKILLS_UNVERIFIED": "codeverse/skills/config.py",
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


_warned: set[str] = set()


def parse_features(raw: str | None) -> frozenset[str]:
    """``"fit, contacts"`` → ``{fit, contacts}``; ``"all,-graph"`` → everything but graph.

    Pure, so it is testable without touching the environment.  Unknown names are
    dropped and logged once per process (see the module docstring for why not fatal)."""
    if not raw or not raw.strip():
        return frozenset()
    on: set[str] = set()
    for tok in (t.strip().lower() for t in raw.split(",")):
        if not tok:
            continue
        negate = tok.startswith("-")
        name = tok[1:] if negate else tok
        names = set(KNOWN_FEATURES) if name == "all" else {name}
        unknown = names - set(KNOWN_FEATURES)
        for u in unknown - _warned:
            _warned.add(u)
            log.warning("%s: unknown plan feature %r ignored (known: %s)", PLAN_FEATURES_ENV, u, ", ".join(KNOWN_FEATURES))
        names -= unknown
        if negate:
            on -= names
        else:
            on |= names
    return frozenset(on)


def plan_features() -> frozenset[str]:
    """The features switched on for this process, read from the environment NOW."""
    return parse_features(os.environ.get(PLAN_FEATURES_ENV))


def plan_feature_on(name: str) -> bool:
    """Is feature ``name`` on?  Raises on a name the wave does not define, because a
    caller asking about a feature that cannot be switched on is a bug, not a config."""
    if name not in KNOWN_FEATURES:
        raise ValueError(f"unknown plan feature {name!r}; known: {', '.join(KNOWN_FEATURES)}")
    return name in plan_features()


__all__ = ["CONSISTENCY", "CONTACTS", "DEAD_SWITCHES", "FIT", "GENERATION_SIDE", "GENERATION_SIDE_ENV",
           "GRAPH", "GRAPH_BUDGET", "GRAPH_EXAMPLE", "PLAN_SIDE", "PLAN_SIDE_ENV", "pin_plan_blockers",
           "KNOWN_FEATURES", "LIVE_SWITCHES", "PLAN_FEATURES_ENV", "dead_env_keys", "parse_features",
           "plan_feature_on", "plan_features"]
