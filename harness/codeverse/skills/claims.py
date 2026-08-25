"""Pin every number a skill quotes to the live constant it came from.

WHY generically, and not one hand-written test per drift: the reference library shipped a
skill that told agents water is metallic for months after the code stopped agreeing, and
caught it once, by hand, for one pair of files.  A skill's whole value is that its numbers
are OUR numbers; the moment ``PENETRATION_ERROR_M`` moves, the sentence quoting "10 mm" is
worse than no sentence, because it is confidently wrong.

So each bundle may carry ``skills/_claims/<name>.toml`` — deliberately OUTSIDE the bundle
directory, so ``skills-ref validate`` and every CLI's discovery walk see a pure tree::

    [[claim]]
    key    = "penetration_error_m"                              # shared name across skills
    text   = "10 mm"                                            # must appear in the body
    python = "codeverse.spatial.connectivity:PENETRATION_ERROR_M"
    scale  = 1000                                               # optional, applied first
    format = "{:.0f} mm"                                        # optional, default str()

``check_claims`` asserts the constant still formats to ``text`` and that ``text`` still
appears in the body.  ``claim_values`` feeds the agreement test: two skills that can be
attached to the same session may not carry two different values for one ``key``.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

from codeverse.skills.model import Skill

CLAIMS_DIR = "_claims"


def claims_path(name: str, root: Path | None = None) -> Path:
    from codeverse.skills import skills_dir

    base = Path(root) if root is not None else skills_dir()
    return base / CLAIMS_DIR / f"{name}.toml"


def load_claims(name: str, root: Path | None = None) -> list[dict[str, Any]]:
    """The claim rows for one skill ([] when the bundle pins nothing)."""
    p = claims_path(name, root)
    if not p.is_file():
        return []
    data = tomllib.loads(p.read_text())
    rows = data.get("claim") or []
    if not isinstance(rows, list):
        raise ValueError(f"{p}: [[claim]] must be an array of tables")
    return [dict(r) for r in rows]


def resolve(dotted: str) -> Any:
    """``module:NAME`` or ``module.NAME`` → the live object."""
    mod, _, attr = dotted.partition(":") if ":" in dotted else dotted.rpartition(".")
    if not mod or not attr:
        raise ValueError(f"claim python target {dotted!r} must be 'module:NAME'")
    return getattr(importlib.import_module(mod), attr)


def render_claim(row: dict[str, Any]) -> str:
    """The string the live constant produces, per this row's scale/format."""
    value = resolve(str(row["python"]))
    scale = row.get("scale")
    if scale is not None:
        value = value * scale
    fmt = row.get("format")
    return format(value, "") if not fmt else str(fmt).format(value)


def check_claims(skill: Skill, root: Path | None = None) -> list[str]:
    """Every stale or missing claim in one bundle, as human lines (empty == clean)."""
    issues: list[str] = []
    try:
        rows = load_claims(skill.name, root)
    except (ValueError, OSError) as e:
        return [f"claims file unreadable: {e}"]
    for i, row in enumerate(rows):
        where = f"claim[{i}] {row.get('key', '?')}"
        for field in ("key", "text", "python"):
            if not row.get(field):
                issues.append(f"{where}: missing `{field}`")
        if issues and issues[-1].startswith(where):
            continue
        try:
            live = render_claim(row)
        except Exception as e:  # noqa: BLE001 — a bad dotted path is a claim problem
            issues.append(f"{where}: cannot resolve {row['python']!r}: {e}")
            continue
        if live != row["text"]:
            issues.append(f"{where}: the constant now renders {live!r}, the body says {row['text']!r}")
        elif row["text"] not in skill.body:
            issues.append(f"{where}: {row['text']!r} no longer appears in the body")
    return issues


def claim_values(name: str, root: Path | None = None) -> dict[str, str]:
    """``key -> text`` for one bundle — the rendered string, in that bundle's own units."""
    return {str(r["key"]): str(r.get("text", "")) for r in load_claims(name, root) if r.get("key")}


def claim_bases(name: str, root: Path | None = None) -> dict[str, tuple[str, Any]]:
    """``key -> (dotted target, live value)`` — the agreement test's real comparison.

    WHY not the rendered text: two skills may honestly quote one constant in two units.
    ``cv3d-bbox-contract`` says "1 cm" (scale 100) and ``cv3d-repeats-and-mirrors`` says
    "0.01" metres; both pin ``conventions:BBOX_TOLERANCE_M`` and both are right.  A
    contradiction is two skills pointing a shared key at DIFFERENT numbers, so that is what
    is compared — the pre-scale value, and the target it came from.
    """
    out: dict[str, tuple[str, Any]] = {}
    for row in load_claims(name, root):
        key = row.get("key")
        if not key or not row.get("python"):
            continue
        out[str(key)] = (str(row["python"]), resolve(str(row["python"])))
    return out


__all__ = ["CLAIMS_DIR", "check_claims", "claim_bases", "claim_values", "claims_path", "load_claims",
           "render_claim", "resolve"]
