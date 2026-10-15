"""The 3dcode skill library: task-scoped rule sheets, routed automatically, read-measured.

A *skill* is not a recipe library — the two sides of the split in ``prompts/catalog.py``:
PROMPT material (``contract.md`` / ``system.md``: interface, sandbox, measured requirements)
is what the harness puts in a message; a skill is AGENT-READ material the harness only
materialises: <=350 lines of measured requirements and a few short optional practices for
one recurring failure class (library / API names, fences of ~12 lines at most, numbers only
where a gate or the judge measures them — D107), attached to a session only when the track /
language / kind / plan / **previous round's gate findings** say it applies.  (The cookbooks
that preceded the library were deleted track by track, D106 / D107; the ``read_cookbook`` MCP
tool that served them a chapter at a time was measured at 0 calls in 16 zone sessions.)

Why the library lives inside the package: every backend reads it from a materialised
workspace copy, but the source of truth ships in the wheel, so it must be package data
(``pyproject.toml`` ``[tool.setuptools.package-data]``) — a sibling wave already shipped
a wheel whose starter tree was silently empty for want of that glob.

Per-model variants (D105, 2026-10-14): ``<skills_dir>/variants/<name>/`` is an OVERLAY on
the library while that variant is active (``codeverse3d.prompts.variants.active_variant``):
its bundles shadow base bundles by name or add new ones (:func:`all_skills` merges the two
roots, :func:`library_roots`), its ``_claims/`` is consulted first
(``addons/skill_targets.claims_path``), and its ``routes.toml`` — boosts, disabled rule ids,
extra typed rows — reshapes the routing table (:func:`variants_table`, parsed with ``tomllib``
into typed records; ``registry.select`` applies it).  ``bundle_dirs`` never descends into it:
``variants/`` has no ``SKILL.md``, so the base listing is what it always was — ``3dcode skills
validate`` / ``list`` and ``doctor --skills`` walk every shipped root through :func:`shipped_roots`.

Public surface for the rest of the harness::

    from codeverse3d.skills import select, attach_skills, probe_reads

``select`` is pure and testable; ``attach_skills`` writes the routed bundles into a
workspace and returns what it wrote; ``probe_reads`` says afterwards which of them were
actually opened — from the CLI's own tool calls where its backend recorded them.  Everything
is behind ``C3D_SKILLS``: ON by default since 2026-09-22, ``C3D_SKILLS=0`` turns it off.
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

#: the per-model overlays live under the library: ``<skills_dir>/variants/<name>/``
VARIANTS_SUBDIR = "variants"
#: a variant's routing-table overlay, beside its bundles
ROUTES_FILE = "routes.toml"


def skills_dir() -> Path:
    """The bundle root: this package's directory, as for prompts and rubrics
    (``C3D_SKILLS_DIR`` points it elsewhere, read at call time)."""
    override = get_settings().skills_dir
    if override:
        return override.expanduser()
    return Path(__file__).resolve().parent


def variant_dir(name: str) -> Path:
    """``<skills_dir>/variants/<name>`` — the skills half of a variant overlay (it need not exist)."""
    return skills_dir() / VARIANTS_SUBDIR / name


def library_roots(variant: str | None = None) -> list[Path]:
    """The bundle roots in shadowing order: the base library, then the active variant's overlay
    when the variant ships one.  ``variant`` names one explicitly instead of :func:`active_variant`."""
    name = active_variant() if variant is None else variant
    roots = [skills_dir()]
    if name:
        overlay = variant_dir(name)
        if overlay.is_dir():
            roots.append(overlay)
    return roots


def bundle_dirs(root: Path | None = None) -> list[Path]:
    """Every directory under the library that looks like a bundle, sorted by name.

    ``_claims/`` and ``__pycache__/`` are deliberately invisible: the library shares a
    directory with this package's modules, and a pure tree is what ``skills-ref
    validate`` and the CLIs expect to walk.  ``variants/`` has no ``SKILL.md`` and is skipped
    the same way — an overlay root is listed by passing it as ``root``.
    """
    base = Path(root) if root is not None else skills_dir()
    if not base.is_dir():
        return []
    return sorted((p for p in base.iterdir()
                   if p.is_dir() and not p.name.startswith(("_", ".")) and (p / SKILL_FILE).is_file()),
                  key=lambda p: p.name)


def iter_skills(root: Path | None = None, *, strict: bool = False) -> Iterator[Skill]:
    """Every valid bundle.  A broken one is logged and skipped unless ``strict``.

    Skipping is the right default in a run: one malformed bundle must cost that skill,
    not the battery.  ``3dcode skills validate`` and the test suite pass ``strict=True``.
    """
    for d in bundle_dirs(root):
        try:
            yield parse_skill(d)
        except SkillError as e:
            if strict:
                raise
            log.warning("skill %s is invalid and will not be routed: %s", d.name, e)


def all_skills(root: Path | None = None, *, strict: bool = False) -> dict[str, Skill]:
    """name → Skill.  With no ``root``: the base library updated by the active variant's overlay
    (a same-name bundle shadows the base one, a new name is added); with ``root``: that directory
    alone.  Empty when no bundles ship yet."""
    if root is not None:
        return {s.name: s for s in iter_skills(root, strict=strict)}
    out: dict[str, Skill] = {}
    for r in library_roots():
        out.update({s.name: s for s in iter_skills(r, strict=strict)})
    return out


def load_skill(name: str, root: Path | None = None) -> Skill:
    """One bundle by name — the active variant's copy first; raises :class:`SkillError` when it is
    missing or invalid."""
    roots = [Path(root)] if root is not None else list(reversed(library_roots()))
    for base in roots:
        d = base / name
        if (d / SKILL_FILE).is_file():
            return parse_skill(d)
    raise SkillError(f"no skill {name!r} in {' or '.join(str(r) for r in roots)}")


@dataclass(frozen=True)
class ShippedRoot:
    """One root the library ships: the base (``variant == ""``) or one variant's skills overlay."""

    variant: str
    path: Path

    def label(self, bundle: str) -> str:
        """How the CLIs and the doctor name a bundle of this root: ``<variant>/<bundle>`` for an overlay."""
        return f"{self.variant}/{bundle}" if self.variant else bundle


def shipped_roots(variants: Iterable[str] | None = None) -> list[ShippedRoot]:
    """The base root, then the skills overlay of every shipped variant that has one — every directory
    a run can route a bundle from, whichever variant it activates.  ``3dcode skills validate`` /
    ``list`` and ``doctor --skills`` walk THIS rather than :func:`bundle_dirs` alone: an overlay bundle
    a loop iteration wrote is routed the moment its variant is active (:func:`all_skills`), so a
    malformed one must fail validation here, not vanish from a session with a logged warning while
    the row that named it routes nothing.  ``variants`` names the ones to walk; the default is every
    variant ``prompts.variants.list_variants`` knows, and a ``variant.toml`` that does not parse raises
    its ``ValueError`` (a shipped variant is never half-known)."""
    from codeverse3d.prompts.variants import list_variants

    names = list(list_variants()) if variants is None else list(variants)
    roots = [ShippedRoot("", skills_dir())]
    roots += [ShippedRoot(name, variant_dir(name)) for name in names if variant_dir(name).is_dir()]
    return roots


# ===================================================================== the variant routing table
@dataclass(frozen=True)
class RouteBoost:
    """``[[boost]]``: ``delta`` is added to the priority of every route naming ``skill``
    (``registry.effective_priority`` clamps: a standing row stays below 90, a gate-fired row never
    falls under it)."""

    skill: str
    delta: int


@dataclass(frozen=True)
class VariantRoutes:
    """A variant's ``routes.toml``: boosts, disabled base rule ids and extra typed rows, parsed with
    ``tomllib`` (no regex).  The extra rows are appended AFTER the base table (ties keep base order)."""

    variant: str = ""
    routes: tuple[Route, ...] = ()
    boosts: tuple[RouteBoost, ...] = ()
    disabled: frozenset[str] = frozenset()

    @property
    def boost_map(self) -> dict[str, int]:
        """skill → summed delta (what ``registry.select`` takes)."""
        out: dict[str, int] = {}
        for b in self.boosts:
            out[b.skill] = out.get(b.skill, 0) + b.delta
        return out

    @property
    def empty(self) -> bool:
        return not (self.routes or self.boosts or self.disabled)


def variants_table(name: str) -> VariantRoutes:
    """The variant's table: ``<skills_dir>/variants/<name>/routes.toml`` parsed, or an empty table when
    the variant ships none (or ``name`` is the base).  A table that does not type-check raises
    ``ValueError`` naming the row and the problem — ``attach_skills`` logs it and routes with the
    base table, so a typo costs the overlay, never the round."""
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
    """Parse one ``routes.toml`` into typed records; every typing error is a ``ValueError`` that names
    the row (``<path>: [[route]] #2: priority must be an integer``)."""
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
        # the table's law (design §5.2 law 2): a priority >= 90 MEANS "a gate routed this" to telemetry
        # and to Selection.gate_fired, so an extra row may only sit there when it answers a finding
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
