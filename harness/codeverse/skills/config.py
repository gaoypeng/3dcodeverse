"""The three switches that gate the whole skill system — read from the environment NOW.

WHY not ``Settings``: ``codeverse.config.get_settings`` is ``lru_cache``d, so a value
read through it is frozen at first touch and an A/B arm that sets the variable after
import gets the control's behaviour.  ``bench/ab_plan.py`` differs its arms only by
environment, and a wave once printed "keep, mean delta +0.344" for two byte-identical
arms because the switch it flipped was read by nothing at all
(``codeverse/tracks/plan_features.py``).  So: one module, read at call time, registered
in ``plan_features.LIVE_SWITCHES``, and a test that greps the tree to prove it is read.

* ``CV3D_SKILLS``            — off by default.  On = route, materialise, measure.
* ``CV3D_SKILLS_MAX``        — attached bundles per session (default 5, design §5.2 law 1).
* ``CV3D_SKILLS_UNVERIFIED`` — also route bundles labelled ``inherited-unverified``
  (cadquery / threejs today: zero graded runs, so their claims are carried, not measured).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

SKILLS_ENV = "CV3D_SKILLS"
SKILLS_MAX_ENV = "CV3D_SKILLS_MAX"
SKILLS_UNVERIFIED_ENV = "CV3D_SKILLS_UNVERIFIED"

DEFAULT_SKILLS_MAX = 5
_TRUE = frozenset({"1", "on", "true", "yes", "y"})
_FALSE = frozenset({"", "0", "off", "false", "no", "n"})


def _flag(env: str, default: bool = False) -> bool:
    raw = os.environ.get(env, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return default
    log.warning("%s=%r is not a boolean; treating it as %s", env, raw, "on" if default else "off")
    return default


def skills_enabled() -> bool:
    """Is the skill system on for this process?  Default OFF until the A/B says otherwise."""
    return _flag(SKILLS_ENV)


def skills_unverified() -> bool:
    """Route bundles whose evidence label is ``inherited-unverified``?  Default no."""
    return _flag(SKILLS_UNVERIFIED_ENV)


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


__all__ = ["DEFAULT_SKILLS_MAX", "SKILLS_ENV", "SKILLS_MAX_ENV", "SKILLS_UNVERIFIED_ENV",
           "skills_enabled", "skills_max", "skills_unverified"]
