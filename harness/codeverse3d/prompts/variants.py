"""Per-model prompt / skill variants: one overlay directory per name, chosen explicitly (D105).

WHY.  The owner's 2026-10-14 decision: gemini-4-argon on the scene track gets its own prompt
wording and skill routing, tuned by a loop (``eval/bench/loop.py``) that must be able to accept
or roll back a change without touching the base files every other model reads.  A variant is
therefore an OVERLAY, never a fork: ``codeverse3d/prompts/variants/<name>/<rel>`` shadows
``codeverse3d/prompts/<rel>`` for the callers of :func:`codeverse3d.prompts.load_text` /
``render`` (they do not change), and ``codeverse3d/skills/variants/<name>/`` shadows bundles by
name and adds ``routes.toml`` boosts (``codeverse3d.skills``).  Everything a variant does is a
file the loop can diff, snapshot and hash — :func:`variant_hash` goes into the run record beside
the prompt hashes, so two runs can be told apart by what their models were shown.

HOW it is chosen (Law 4: never a regex over the model id).  ``Settings.variant``
(``C3D_VARIANT``, or ``3dcode make --variant``, frozen on ``RunOptions.variant``) names one for
the whole process; otherwise ``Settings.variants`` maps an EXACT generator id to a name — by the
id as typed, then by ``<kind>:<served model>`` (``agy:gemini-4-argon-high`` runs the model
``gemini-4-argon``, so one mapping keyed on the served model covers every effort suffix).  An
unknown name is a ``ValueError`` at resolution time, before a run directory exists.

WHERE it is active.  A ``ContextVar`` set by :func:`activate` around the whole run
(``tracks/lifecycle.BaseTrack.run``) and inherited by every fan-out thread, because
``proc.fan_out`` copies the caller's context into its workers; outside any run,
``get_settings().variant`` — the process switch, like every other ``C3D_*`` knob.
"""

from __future__ import annotations

import hashlib
import logging
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse3d.config import Settings, get_settings

log = logging.getLogger(__name__)

#: the overlay directories: ``VARIANTS_DIR/<name>/`` mirrors ``codeverse3d/prompts/`` file for file
VARIANTS_DIR: Path = Path(__file__).resolve().parent / "variants"
#: the one file every variant must ship (its name, version, generators and knobs)
VARIANT_FILE = "variant.toml"
#: ``[knobs]`` may set ONLY these ``Settings`` fields — the skills dials a variant tunes.  Applied
#: like a profile (``Settings.apply_profile``): a field the user stated themselves wins.
KNOB_FIELDS: frozenset[str] = frozenset({"skills_max", "skills_only"})
_KNOB_TYPES: dict[str, type] = {"skills_max": int, "skills_only": str}
#: files under an overlay that are documentation, never material a model sees: left out of the hash
HASH_EXCLUDED: frozenset[str] = frozenset({"README.md"})

_ACTIVE: ContextVar[str | None] = ContextVar("c3d_variant", default=None)
_WARNED: set[str] = set()


class VariantInfo(BaseModel):
    """``variant.toml``, parsed and type-checked."""

    model_config = {"frozen": True}

    name: str = Field(description="equals the overlay directory name")
    version: int = Field(ge=1, description="the loop bumps it on accept (eval/bench/loop.py)")
    generators: tuple[str, ...] = Field(default=(), description="informative: the generator ids it was tuned "
                                        "for; selection is Settings.variants / --variant")
    description: str = ""
    knobs: dict[str, Any] = Field(default_factory=dict, description="Settings fields to apply (KNOB_FIELDS only)")


# --------------------------------------------------------------------------- the catalogue
def _plain_name(name: str) -> bool:
    """A variant name is a plain directory name: no separators, no dot-prefix."""
    return bool(name) and Path(name).name == name and not name.startswith(".")


def variant_root(name: str) -> Path:
    """``VARIANTS_DIR / name`` — the prompts half of the overlay (it need not exist yet)."""
    if not _plain_name(name):
        raise ValueError(f"variant name {name!r} must be a plain directory name")
    return VARIANTS_DIR / name


def list_variants() -> dict[str, VariantInfo]:
    """Every variant the tree ships, by name (a directory under ``VARIANTS_DIR`` with a ``variant.toml``).
    A file that does not parse raises: a shipped variant is never half-known."""
    if not VARIANTS_DIR.is_dir():
        return {}
    return {d.name: _parse(d / VARIANT_FILE, d.name)
            for d in sorted(VARIANTS_DIR.iterdir()) if d.is_dir() and (d / VARIANT_FILE).is_file()}


def variant_info(name: str) -> VariantInfo:
    """``variant.toml`` of ``name``; ``ValueError`` for an unknown name (naming the known ones)."""
    path = variant_root(name) / VARIANT_FILE if _plain_name(name) else None
    if path is None or not path.is_file():
        known = ", ".join(list_variants()) or "none"
        raise ValueError(f"unknown variant {name!r} (known: {known})")
    return _parse(path, name)


def _parse(path: Path, dir_name: str) -> VariantInfo:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: not valid TOML: {e}") from e
    head = data.get("variant")
    if not isinstance(head, dict):
        raise ValueError(f"{path}: missing the [variant] table")
    unknown_tables = sorted(set(data) - {"variant", "knobs"})
    if unknown_tables:
        raise ValueError(f"{path}: unknown table(s) {unknown_tables}; variant.toml holds [variant] and [knobs]")
    unknown = sorted(set(head) - {"name", "version", "generators", "description"})
    if unknown:
        raise ValueError(f"{path}: unknown [variant] key(s) {unknown}")
    name = head.get("name")
    if name != dir_name:
        raise ValueError(f"{path}: [variant] name {name!r} must equal the directory name {dir_name!r}")
    version = head.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"{path}: version must be an integer >= 1, got {version!r}")
    generators = head.get("generators", [])
    if not isinstance(generators, list) or not all(isinstance(g, str) for g in generators):
        raise ValueError(f"{path}: generators must be a list of generator ids")
    description = head.get("description", "")
    if not isinstance(description, str):
        raise ValueError(f"{path}: description must be a string")
    knobs = data.get("knobs", {})
    if not isinstance(knobs, dict):
        raise ValueError(f"{path}: [knobs] must be a table")
    for key, value in knobs.items():
        if key not in KNOB_FIELDS:
            raise ValueError(f"{path}: [knobs] {key!r} is not a variant knob (allowed: {sorted(KNOB_FIELDS)})")
        want = _KNOB_TYPES[key]
        if isinstance(value, bool) or not isinstance(value, want) or (want is int and value < 0):
            raise ValueError(f"{path}: [knobs] {key} must be a {want.__name__}"
                             f"{' >= 0' if want is int else ''}, got {value!r}")
    return VariantInfo(name=name, version=version, generators=tuple(generators), description=description,
                       knobs=dict(knobs))


# --------------------------------------------------------------------------- selection
def served_id(generator_id: str) -> str:
    """``agy:gemini-4-argon-high`` → ``agy:gemini-4-argon``: the id by the model the vendor serves, without
    the effort suffix; ``""`` for an id that is not ``<kind>:<model>`` (a single-shot id, a bare name)."""
    from codeverse3d.agents.backends import api_model_effort
    from codeverse3d.agents.registry import parse_agent_id

    try:
        kind, model = parse_agent_id(generator_id)
    except ValueError:
        return ""
    return f"{kind}:{api_model_effort(model)[0]}"


def resolve_variant(settings: Settings, generator_id: str) -> str:
    """The variant name for a run of ``generator_id`` under ``settings``: ``settings.variant`` (non-empty)
    > ``settings.variants[generator_id]`` > ``settings.variants["<kind>:<served model>"]`` > ``""`` (base).
    Plain dict lookups, never a pattern over the id.  An unknown name raises ``ValueError``."""
    name = settings.variant.strip()
    if not name and settings.variants:
        name = settings.variants.get(generator_id, "")
        if not name:
            served = served_id(generator_id)
            if served and served != generator_id:
                name = settings.variants.get(served, "")
    name = (name or "").strip()
    if name:
        variant_info(name)
    return name


# --------------------------------------------------------------------------- the active variant
def active_variant() -> str:
    """The variant everything resolves through right now: the ``ContextVar`` :func:`activate` set, else
    the process switch ``get_settings().variant``.  ``""`` is the base."""
    current = _ACTIVE.get()
    if current is not None:
        return current
    name = get_settings().variant.strip()
    if name and not _plain_name(name):
        if name not in _WARNED:
            _WARNED.add(name)
            log.warning("C3D_VARIANT=%r is not a plain variant name; using the base prompts", name)
        return ""
    return name


@contextmanager
def activate(name: str) -> Iterator[str]:
    """Set the active variant for the block (``""`` = base).  An unknown name raises before anything in
    the block renders from a directory that does not exist.  Nests: the outer value returns on exit."""
    name = (name or "").strip()
    if name:
        variant_info(name)
    token = _ACTIVE.set(name)
    try:
        yield name
    finally:
        _ACTIVE.reset(token)


# --------------------------------------------------------------------------- provenance
def _skills_root(name: str) -> Path:
    from codeverse3d.skills import variant_dir

    return variant_dir(name)


def variant_hash(name: str) -> str:
    """``sha256[:12]`` over the sorted ``(relpath, bytes)`` of the prompt AND skills overlay files of
    ``name`` (``README.md`` left out: documentation, not prompt material); ``""`` for the base.  The two
    trees are keyed ``prompts/<rel>`` and ``skills/<rel>`` so a same-named file in both still counts twice.
    For an empty overlay this is the hash of ``variant.toml`` + ``routes.toml``, so a version bump moves it."""
    if not name:
        return ""
    variant_info(name)
    h = hashlib.sha256()
    for prefix, root in (("prompts", variant_root(name)), ("skills", _skills_root(name))):
        if not root.is_dir():
            continue
        for p in sorted(x for x in root.rglob("*") if x.is_file()):
            if p.name in HASH_EXCLUDED or p.name.startswith(".") or "__pycache__" in p.parts:
                continue
            h.update(f"{prefix}/{p.relative_to(root).as_posix()}\0".encode())
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()[:12]


def variant_block(name: str) -> dict[str, Any]:
    """``{"name", "version", "hash"}`` for a record (``RunRecord.extra["variant"]``, the ``variant.active``
    event).  Tolerant: a variant that vanished mid-run keeps its name with version 0 / hash ``""``."""
    if not name:
        return {"name": "", "version": 0, "hash": ""}
    try:
        return {"name": name, "version": variant_info(name).version, "hash": variant_hash(name)}
    except (ValueError, OSError) as e:
        log.warning("variant %r cannot be read for the record: %s", name, e)
        return {"name": name, "version": 0, "hash": ""}


# --------------------------------------------------------------------------- knobs
def apply_variant_knobs(settings: Settings, info: VariantInfo) -> dict[str, Any]:
    """Set ``info.knobs`` on ``settings`` as DEFAULTS, the way a profile is applied: a field the user
    stated (config file / ``C3D_*`` / an explicit constructor kwarg — ``model_fields_set``) is never
    touched, and the field-set is restored afterwards so a knob is not later mistaken for a statement
    (``Settings.apply_profile.put_section`` keeps the same invariant).  Returns what was set."""
    stated = set(settings.model_fields_set)
    applied: dict[str, Any] = {}
    for field_name, value in info.knobs.items():
        if field_name not in KNOB_FIELDS or field_name in stated:
            continue
        setattr(settings, field_name, value)
        applied[field_name] = value
    settings.__pydantic_fields_set__ = stated
    return applied


@contextmanager
def knobs_applied(settings: Settings, info: VariantInfo | None) -> Iterator[dict[str, Any]]:
    """:func:`apply_variant_knobs` for the block ONLY (``info`` ``None``: nothing applied).

    WHY a block.  The prompt and skill overlays are scoped by :func:`activate`'s ``ContextVar``, but
    the knobs are written onto a ``Settings`` object that outlives the run — the process singleton
    ``attach_skills`` reads, a rig's injected Settings — so a variant's ``skills_max`` stayed on it for
    every later run of the process (a manifest rig, a bench cell after a variant cell, any script
    calling ``track.run`` twice), and since ``apply_variant_knobs`` keeps the field-set, the leaked
    value was indistinguishable from the default.  On exit — whatever the block raised — every knob
    field goes back to its prior value and its prior field-set membership; a statement the block made
    to any OTHER field is kept.  Yields what was set, for the ``variant.active`` event."""
    prior = {field_name: getattr(settings, field_name) for field_name in KNOB_FIELDS}
    stated = set(settings.model_fields_set) & KNOB_FIELDS
    applied = apply_variant_knobs(settings, info) if info is not None else {}
    try:
        yield applied
    finally:
        for field_name, value in prior.items():
            setattr(settings, field_name, value)      # pydantic marks each as set: undone next line
        settings.__pydantic_fields_set__ = (set(settings.model_fields_set) - KNOB_FIELDS) | stated


__all__ = ["HASH_EXCLUDED", "KNOB_FIELDS", "VARIANTS_DIR", "VARIANT_FILE", "VariantInfo", "activate",
           "active_variant", "apply_variant_knobs", "knobs_applied", "list_variants", "resolve_variant", "served_id",
           "variant_block", "variant_hash", "variant_info", "variant_root"]
