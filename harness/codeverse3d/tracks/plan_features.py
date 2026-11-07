"""Plan-feature switches that change generation-side behaviour."""


from __future__ import annotations

from codeverse3d.config import Settings

GENERATION_SIDE_ENV: frozenset[str] = frozenset({"C3D_SKILLS", "C3D_SKILLS_ONLY", "C3D_REFERENCE_DIFF"})
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
    """The ``C3D_*`` keys of ``env`` that no Settings field reads — empty when the arm really"""
    return sorted(k for k in env if k.startswith("C3D_") and not _read(k))


def pin_plan_blockers(variant_env: dict[str, str]) -> list[str]:
    """Which of ``variant_env``'s switches forbid sharing one plan between the arms."""
    dead = set(dead_env_keys(variant_env))
    return [f"{key} is not known to act after planning" for key in sorted(variant_env)
            if key not in GENERATION_SIDE_ENV and key not in dead]


__all__ = ["GENERATION_SIDE_ENV", "NODE_SIDE_ENV", "dead_env_keys", "pin_plan_blockers"]
