"""What an A/B rig may assume about a ``C3D_*`` switch — ``eval/bench/ab_plan.py`` asks.

Eval-only: no runtime module imports this.  Every switch the harness reads is a ``Settings``
field (``config.py``), so which names are live is derived from ``Settings.model_fields``
instead of a hand-kept list: an arm that differs only by a name no field reads is
byte-identical to its control (one A/B printed "keep, mean delta +0.344" for two such arms,
CQ-5), and ``ab_plan`` refuses it.  ``pin_plan_blockers`` keeps ``--pin-plan`` from sharing
one plan across arms whose switch acts at or before planning.
"""

from __future__ import annotations

from codeverse3d.config import Settings

#: switches that act AFTER planning — ``--pin-plan`` may share one plan across arms that
#: differ only by these.  Anything else is treated as plan-side: refusing to pin costs one
#: noisy A/B, pinning wrongly costs a confident wrong answer (eval/docs/EVAL.md §8.1
#: measured it — the A/A's worst pair differed 1 part vs 10).
GENERATION_SIDE_ENV: frozenset[str] = frozenset({"C3D_SKILLS", "C3D_SKILLS_MAX", "C3D_SKILLS_UNVERIFIED",
                                                 "C3D_SKILLS_ONLY", "C3D_REFERENCE_DIFF", "C3D_SEED_RECIPES"})
#: the two the node side reads itself (runtime_js: gpu_launch.cjs, browser_daemon.cjs)
NODE_SIDE_ENV: frozenset[str] = frozenset({"C3D_BROWSER_REUSE", "C3D_PAGE_TTL_MS"})


def _read(key: str) -> bool:
    """Does any ``Settings`` field — or the node side — read the environment variable ``key``?"""
    if key in Settings.FLAT or key in NODE_SIDE_ENV:
        return True
    section, _, name = key.removeprefix("C3D_").lower().partition("__")
    field = Settings.model_fields.get(section)
    if not name:
        return field is not None
    return field is not None and name in getattr(field.annotation, "model_fields", {})


def dead_env_keys(env: dict[str, str]) -> list[str]:
    """The ``C3D_*`` keys of ``env`` that no Settings field reads — empty when the arm really
    differs.  A key outside the ``C3D_`` family is not judged (it may matter to a CLI)."""
    return sorted(k for k in env if k.startswith("C3D_") and not _read(k))


def pin_plan_blockers(variant_env: dict[str, str]) -> list[str]:
    """Which of ``variant_env``'s switches forbid sharing one plan between the arms.

    Empty list = every switch this A/B changes acts after planning, so both arms can be
    seeded with the same ``plan.json`` and the paired difference stops carrying the
    planner's spread (the dominant variance term, eval/docs/EVAL.md §8.1).  A dead key
    blocks nothing: no code reads it, so it cannot change a plan.
    """
    dead = set(dead_env_keys(variant_env))
    return [f"{key} is not known to act after planning" for key in sorted(variant_env)
            if key not in GENERATION_SIDE_ENV and key not in dead]


__all__ = ["GENERATION_SIDE_ENV", "NODE_SIDE_ENV", "dead_env_keys", "pin_plan_blockers"]
