"""The 3dcode skill library: task-scoped rule sheets, routed automatically, read-measured.

A skill is AGENT-READ material (<=350 lines of requirements and short optional practices for one
failure class) that the harness only materialises, when track / language / kind / plan / the
previous round's gate findings say it applies.  The library ships as package data.
``<skills_dir>/variants/<name>/`` overlays it while that variant is active: its bundles shadow
base ones by name, its ``routes.toml`` reshapes the routing table.

Public surface: ``select`` (pure routing), ``attach_skills`` (write the routed bundles),
``probe_reads`` (which were opened).  Controlled by ``C3D_SKILLS`` (default on).
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeverse3d.config import get_settings
from codeverse3d.prompts.variants import active_variant
from codeverse3d.skills.model import (
    SKILL_FILE,
    Skill,
    SkillError,
    parse_skill,
    validate_bundle,
)

if TYPE_CHECKING:
    from codeverse3d.skills.registry import Route

log = logging.getLogger(__name__)

#: per-model overlays: ``<skills_dir>/variants/<name>/``
VARIANTS_SUBDIR = "variants"
#: a variant's routing-table overlay
ROUTES_FILE = "routes.toml"


def skills_dir() -> Path:
    override = get_settings().skills_dir
    if override:
        return override.expanduser()
    return Path(__file__).resolve().parent


def variant_dir(name: str) -> Path:
    return skills_dir() / VARIANTS_SUBDIR / name


def library_roots(variant: str | None = None) -> list[Path]:
    name = active_variant() if variant is None else variant
    roots = [skills_dir()]
    if name:
        overlay = variant_dir(name)
        if overlay.is_dir():
            roots.append(overlay)
    return roots


def bundle_dirs(root: Path | None = None) -> list[Path]:
    """Every directory under the library that looks like a bundle, sorted by name.

    ``_claims/``, ``__pycache__/`` and ``variants/`` are invisible; pass an overlay as ``root``.
    """
    base = Path(root) if root is not None else skills_dir()
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir()
                   if p.is_dir() and not p.name.startswith(("_", ".")) and (p / SKILL_FILE).is_file()),
                  key=lambda p: p.name)


def iter_skills(root: Path | None = None, *, strict: bool = False) -> Iterator[Skill]:
    for d in bundle_dirs(root):
        try:
            yield parse_skill(d)
        except SkillError as e:
            if strict:
                raise
            log.warning("skill %s is invalid and will not be routed: %s", d.name, e)


def all_skills(root: Path | None = None, *, strict: bool = False) -> dict[str, Skill]:
    if root is not None:
        return {s.name: s for s in iter_skills(root, strict=strict)}
    out: dict[str, Skill] = {}
    for r in library_roots():
        out.update({s.name: s for s in iter_skills(r, strict=strict)})
    return out


def load_skill(name: str, root: Path | None = None) -> Skill:
    roots = [Path(root)] if root is not None else list(reversed(library_roots()))
    for base in roots:
        d = base / name
        if (d / SKILL_FILE).is_file():
            return parse_skill(d)
    raise SkillError(f"no skill {name!r} in {' or '.join(str(r) for r in roots)}")


@dataclass(frozen=True)
class ShippedRoot:

    variant: str
    path: Path

    def label(self, bundle: str) -> str:
        return f"{self.variant}/{bundle}" if self.variant else bundle


def shipped_roots(variants: Iterable[str] | None = None) -> list[ShippedRoot]:
    """The base root, then the skills overlay of every shipped variant that has one.

    ``3dcode skills validate`` / ``list`` walk this so a malformed overlay bundle fails validation
    instead of silently not routing.  ``variants`` defaults to every variant ``list_variants`` knows.
    """
    from codeverse3d.prompts.variants import list_variants

    names = list(list_variants()) if variants is None else list(variants)
    roots = [ShippedRoot("", skills_dir())]
    roots += [ShippedRoot(name, variant_dir(name)) for name in names if variant_dir(name).is_dir()]
    return roots


# ===================================================================== the variant routing table
@dataclass(frozen=True)
class RouteBoost:

    skill: str
    delta: int


@dataclass(frozen=True)
class VariantRoutes:
    """A variant's ``routes.toml``: boosts, disabled base rule ids and extra typed rows (appended after the base table)."""

    variant: str = ""
    routes: tuple[Route, ...] = ()
    boosts: tuple[RouteBoost, ...] = ()
    disabled: frozenset[str] = frozenset()

    @property
    def boost_map(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in self.boosts:
            out[b.skill] = out.get(b.skill, 0) + b.delta
        return out

    @property
    def empty(self) -> bool:
        return not (self.routes or self.boosts or self.disabled)


def variants_table(name: str) -> VariantRoutes:
    """The variant's parsed ``routes.toml``, or an empty table when it ships none; raises ``ValueError`` naming the bad row."""
    if not name:
        return VariantRoutes()
    path = variant_dir(name) / ROUTES_FILE
    if not path.is_file():
        return VariantRoutes(variant=name)
    return parse_routes(path, variant=name)


_ROUTE_KEYS = frozenset({"rule", "skill", "priority", "tracks", "languages", "kinds",
                         "requires_all", "requires_any", "findings", "why"})
_ROUTE_TUPLES = ("tracks", "languages", "kinds", "requires_all", "requires_any", "findings")


def parse_routes(path: Path, *, variant: str = "") -> VariantRoutes:
    """Parse one ``routes.toml`` into typed records; every typing error is a ``ValueError`` naming the row."""
    from codeverse3d.skills.registry import GATE_FIRED_PRIORITY, ROUTES, Route

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: not valid TOML: {e}") from e
    unknown = sorted(set(data) - {"boost", "disable", "route"})
    if unknown:
        raise ValueError(f"{path}: unknown table(s) {unknown}; routes.toml holds [[boost]], [[disable]] "
                         f"and [[route]] rows")
    boosts = tuple(RouteBoost(skill=_str(row, "skill", where), delta=_int(row, "delta", where))
                   for where, row in _rows(data, "boost", path, {"skill", "delta"}))
    disabled = frozenset(_str(row, "rule", where) for where, row in _rows(data, "disable", path, {"rule"}))
    routes: list[Route] = []
    taken = {r.rule for r in ROUTES}
    for where, row in _rows(data, "route", path, _ROUTE_KEYS):
        findings = _tuple(row, "findings", where)
        priority = _int(row, "priority", where)
        # a priority >= 90 means "a gate routed this" (Selection.gate_fired): only rows that name findings may sit there
        if findings and priority < GATE_FIRED_PRIORITY:
            raise ValueError(f"{where}: a gate-fired row (it names findings) needs priority >= "
                             f"{GATE_FIRED_PRIORITY}, got {priority}")
        if not findings and priority >= GATE_FIRED_PRIORITY:
            raise ValueError(f"{where}: a standing row (no findings) must stay below "
                             f"{GATE_FIRED_PRIORITY}, got {priority}")
        route = Route(rule=_str(row, "rule", where), skill=_str(row, "skill", where), priority=priority,
                      tracks=_tuple(row, "tracks", where), languages=_tuple(row, "languages", where),
                      kinds=_tuple(row, "kinds", where), requires_all=_tuple(row, "requires_all", where),
                      requires_any=_tuple(row, "requires_any", where), findings=findings,
                      why=_str(row, "why", where, required=False))
        if route.rule in taken:
            raise ValueError(f"{where}: rule id {route.rule!r} is already taken — a rule id names ONE "
                             f"routing decision in every record and ledger row")
        taken.add(route.rule)
        routes.append(route)
    return VariantRoutes(variant=variant, routes=tuple(routes), boosts=boosts, disabled=disabled)


def _rows(data: dict[str, Any], key: str, path: Path, allowed: frozenset[str] | set[str]) -> list[tuple[str, dict[str, Any]]]:
    rows = data.get(key, [])
    if not isinstance(rows, list):
        raise ValueError(f"{path}: [[{key}]] must be an array of tables")
    out: list[tuple[str, dict[str, Any]]] = []
    for i, row in enumerate(rows):
        where = f"{path}: [[{key}]] #{i}"
        if not isinstance(row, dict):
            raise ValueError(f"{where}: must be a table")
        extra = sorted(set(row) - set(allowed))
        if extra:
            raise ValueError(f"{where}: unknown key(s) {extra} (allowed: {sorted(allowed)})")
        out.append((where, row))
    return out


def _str(row: dict[str, Any], key: str, where: str, *, required: bool = True) -> str:
    value = row.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{where}: {key} must be a string, got {value!r}")
    if required and not value.strip():
        raise ValueError(f"{where}: {key} is required")
    return value.strip()


def _int(row: dict[str, Any], key: str, where: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where}: {key} must be an integer, got {value!r}")
    return value


def _tuple(row: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = row.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{where}: {key} must be a list of non-empty strings, got {value!r}")
    return tuple(v.strip() for v in value)


from codeverse3d.skills.materialize import attach_skills, materialize_skills  # noqa: E402
from codeverse3d.skills.registry import plan_signals, select  # noqa: E402
from codeverse3d.skills.telemetry import probe_reads  # noqa: E402

__all__ = ["ROUTES_FILE", "VARIANTS_SUBDIR", "RouteBoost", "ShippedRoot", "Skill", "SkillError",
           "VariantRoutes", "all_skills", "attach_skills", "bundle_dirs", "iter_skills", "library_roots",
           "load_skill", "materialize_skills", "parse_routes", "plan_signals", "probe_reads", "select",
           "shipped_roots", "skills_dir", "validate_bundle", "variant_dir", "variants_table"]
