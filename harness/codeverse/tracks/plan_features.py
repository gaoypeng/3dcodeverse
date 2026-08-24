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


__all__ = ["CONSISTENCY", "CONTACTS", "FIT", "GRAPH", "GRAPH_BUDGET", "GRAPH_EXAMPLE", "KNOWN_FEATURES",
           "PLAN_FEATURES_ENV", "parse_features", "plan_feature_on", "plan_features"]
