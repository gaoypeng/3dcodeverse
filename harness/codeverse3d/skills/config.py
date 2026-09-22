"""The three switches that gate the whole skill system — read from the environment NOW.

WHY not ``Settings``: ``codeverse3d.config.get_settings`` is ``lru_cache``d, so a value
read through it is frozen at first touch and an A/B arm that sets the variable after
import gets the control's behaviour.  ``bench/ab_plan.py`` differs its arms only by
environment, and a wave once printed "keep, mean delta +0.344" for two byte-identical
arms because the switch it flipped was read by nothing at all
(``codeverse3d/tracks/plan_features.py``).  So: one module, read at call time, registered
in ``plan_features.LIVE_SWITCHES``, and a test that greps the tree to prove it is read.

* ``C3D_SKILLS``            — off by default.  On = route, materialise, measure.
* ``C3D_SKILLS_MAX``        — attached bundles per session (default 5, design §5.2 law 1).
* ``C3D_SKILLS_UNVERIFIED`` — also route bundles labelled ``inherited-unverified``
  (cadquery / threejs today: zero graded runs, so their claims are carried, not measured).
* ``C3D_SKILLS_ONLY``       — comma list; the router may consider ONLY these bundles.

WHY ``C3D_SKILLS_ONLY`` exists.  An effect A/B has to attribute its delta to ONE bundle,
and ``C3D_SKILLS=1`` routes up to five.  Restricting the LIBRARY (rather than filtering
the selection afterwards) is the semantics that keeps the arms honest: the named bundle
is routed exactly where its own table rows fire, and the cap never silently drops it in
favour of a higher-priority sheet that is not under test.  An unknown name yields an
empty library — every session then attaches nothing, which shows up immediately as a
variant arm identical to its control, rather than quietly measuring the full set.
"""

from __future__ import annotations

import logging
import os

from codeverse3d.config import env_flag

log = logging.getLogger(__name__)

SKILLS_ENV = "C3D_SKILLS"
SKILLS_MAX_ENV = "C3D_SKILLS_MAX"
SKILLS_UNVERIFIED_ENV = "C3D_SKILLS_UNVERIFIED"
SKILLS_ONLY_ENV = "C3D_SKILLS_ONLY"

DEFAULT_SKILLS_MAX = 5
def skills_enabled() -> bool:
    """Is the skill system on for this process?  Default OFF until the A/B says otherwise."""
    return env_flag(SKILLS_ENV, False)


def skills_unverified() -> bool:
    """Route bundles whose evidence label is ``inherited-unverified``?  Default no."""
    return env_flag(SKILLS_UNVERIFIED_ENV, False)


def skills_only() -> frozenset[str]:
    """The bundles the router may consider, or empty for "all of them".

    Read at call time like the other three, for the same reason: an A/B arm sets it in
    the child's environment and a value frozen at import would hand the variant the
    control's library.
    """
    raw = os.environ.get(SKILLS_ONLY_ENV, "")
    return frozenset(n.strip() for n in raw.split(",") if n.strip())


def skills_max(default: int = DEFAULT_SKILLS_MAX) -> int:
    """Cap on bundles attached to one session.  A bad value falls back, never crashes a run."""
    raw = os.environ.get(SKILLS_MAX_ENV, "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        log.warning("%s=%r is not an integer; using %d", SKILLS_MAX_ENV, raw, default)
        return default
    if n < 0:
        log.warning("%s=%d is negative; using 0", SKILLS_MAX_ENV, n)
        return 0
    return n


__all__ = ["DEFAULT_SKILLS_MAX", "SKILLS_ENV", "SKILLS_MAX_ENV", "SKILLS_ONLY_ENV",
           "SKILLS_UNVERIFIED_ENV", "skills_enabled", "skills_max", "skills_only", "skills_unverified"]
