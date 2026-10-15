"""Battery selection for the loop's non-scene tracks (private: ``bench.loop generate``).

The scene track writes its own briefs (``_loop_prompts``: a planner model, a rung ladder, cached
ids).  The object and graphics tracks already have hand-written batteries under
``bench/prompts/`` whose ids every recorded battery run is keyed by, so the loop SELECTS from
them instead of generating: it pools the source batteries (:func:`pool_sources`), routes each
prompt to the languages it may run in (:func:`route`, :data:`INHERIT_BATTERY_LANGUAGE`), and picks ``n`` per language by the rung's
tier mix (:data:`SOURCE_RUNGS`, :func:`select`).  Ids are kept VERBATIM — an iteration's cell is
then the same cell as the recorded battery's, and ``compare`` pairs by id across iterations.

Determinism: the order within a tier is :func:`stable_rank` — sha256 of (loop name, prompt id) —
never file order and never a random draw, so the same rung yields the same ids every iteration
(the pairs) and a prompt added to a source battery later slots in without reshuffling the rest.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from bench.run_bench import Battery, BenchPrompt
from codeverse3d.contracts.common import Language, Track

log = logging.getLogger(__name__)

Order = Literal["hash", "hardest"]


@dataclass(frozen=True)
class TierStep:
    """Fill the battery from ``tier``, in ``order``, until it holds ``n`` prompts."""

    tier: str
    order: Order = "hash"


#: THE rung table of the non-scene tracks — the tiers a rung draws from, filled in this order
#: until the battery holds ``n``.  "hardest" ranks a tier by :func:`demand` (most acceptance items
#: first; :func:`stable_rank` breaks ties); "hash" is :func:`stable_rank` alone.  A rung above the
#: table's top uses the top (rung 3+ = hard only).  ``easy`` prompts are never drawn: a loop
#: iteration that scores 0.9 everywhere has nothing to learn.
#:
#: ====  ==================================================
#: rung  tiers (in fill order)
#: ====  ==================================================
#: 1     medium (hash order), then hard (hash order)
#: 2     hard (hash order), then the hardest medium
#: 3+    hard only (hash order)
#: ====  ==================================================
SOURCE_RUNGS: dict[int, tuple[TierStep, ...]] = {
    1: (TierStep("medium"), TierStep("hard")),
    2: (TierStep("hard"), TierStep("medium", "hardest")),
    3: (TierStep("hard"),),
}


def rung_steps(rung: int) -> tuple[TierStep, ...]:
    if rung < 1:
        raise ValueError(f"rung must be >= 1, got {rung}")
    return SOURCE_RUNGS[min(rung, max(SOURCE_RUNGS))]


def stable_rank(loop_name: str, prompt_id: str) -> str:
    """The within-tier order key: sha256 of (loop name, prompt id) — stable across iterations and
    machines, different between loops (two loops over one source do not run the same prefix)."""
    return hashlib.sha256(f"{loop_name}\0{prompt_id}".encode()).hexdigest()


def demand(p: BenchPrompt) -> int:
    """How much a prompt asks for, read off typed fields: its acceptance items (``must_have``, each a
    planner acceptance item the judge verifies) plus its stated dimensions (each a contract check)."""
    return len(p.must_have) + len(p.dimensions_m or {})


#: the tracks whose source batteries are written for ONE language: a prompt that names no language
#: takes its battery's ``language`` (graphics_v1/v2: "per-prompt `language: opengl_python` marks the
#: raw-OpenGL prompts (battery default is glsl_shader)" — a glsl raymarch brief is not an OpenGL
#: prompt).  On the other tracks an unmarked prompt runs in every language of the loop: compare_v4 is
#: a `language: blender` battery whose prompts are blender, three.js and CadQuery prompts alike.
INHERIT_BATTERY_LANGUAGE: frozenset[Track] = frozenset({Track.GRAPHICS})


def normalised_text(text: str) -> str:
    return " ".join(text.lower().split())


@dataclass
class Pool:
    """Every distinct prompt of the source batteries, in source order (first occurrence wins)."""

    prompts: list[BenchPrompt] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)      # ids dropped: same text as an earlier prompt
    id_collisions: list[str] = field(default_factory=list)   # ids dropped: an earlier prompt holds the id


def pool_sources(paths: Sequence[Path], track: Track) -> Pool:
    """Load every source battery, check it is ``track``'s, and pool its prompts: a prompt whose
    normalised text was seen already is a duplicate (the first id wins); a prompt whose id was seen
    with OTHER text is a collision (dropped — two cells under one id would never pair).  Relative
    ``references`` are made absolute: the iteration's battery lives elsewhere.  On a track of
    :data:`INHERIT_BATTERY_LANGUAGE` an unmarked prompt is stamped with its battery's language."""
    pool = Pool()
    seen_text: set[str] = set()
    seen_id: set[str] = set()
    inherit = track in INHERIT_BATTERY_LANGUAGE
    for path in paths:
        battery = Battery.load(path)
        if battery.track != track:
            raise ValueError(f"{path}: a {battery.track.value} battery, the loop's track is {track.value}")
        base = battery.source_dir or Path(path).resolve().parent
        for p in battery.prompts:
            key = normalised_text(p.prompt)
            if key in seen_text:
                pool.duplicates.append(p.id)
                continue
            if p.id in seen_id:
                pool.id_collisions.append(p.id)
                log.warning("%s: id %s already names another prompt of the pool: dropped", path, p.id)
                continue
            seen_text.add(key)
            seen_id.add(p.id)
            refs = [r if Path(r).is_absolute() else str((base / r).resolve()) for r in p.references]
            language = battery.language if inherit and p.language is None else p.language
            pool.prompts.append(p.model_copy(update={"references": refs, "language": language}, deep=True))
    return pool


def route(prompts: Sequence[BenchPrompt], language: Language) -> list[BenchPrompt]:
    """The prompts ``language`` may run: a prompt that names a language goes to that language's
    battery only; one that names none goes to every language of the loop (a static prompt is a
    blender, three.js and CadQuery prompt alike).  Graphics prompts always name one by then
    (:func:`pool_sources` stamps the battery's: :data:`INHERIT_BATTERY_LANGUAGE`)."""
    return [p for p in prompts if p.language is None or p.language is language]


@dataclass
class Selection:
    """What :func:`select` picked: the prompts in battery order, how many per tier, and how many the
    rung's tiers held in all (``available`` < ``n`` is a short battery)."""

    prompts: list[BenchPrompt]
    tier_counts: dict[str, int]
    available: int


def select(prompts: Sequence[BenchPrompt], *, rung: int, n: int, loop_name: str) -> Selection:
    """``n`` prompts by the rung's tier mix (:data:`SOURCE_RUNGS`), deterministic.  Fewer than ``n``
    when the pool's tiers hold fewer — the caller logs it and writes it to generate.json."""
    picked: list[BenchPrompt] = []
    counts: dict[str, int] = {}
    taken: set[str] = set()
    steps = rung_steps(rung)
    for step in steps:
        if len(picked) >= n:
            break
        tier = [p for p in prompts if p.tier == step.tier and p.id not in taken]
        if step.order == "hardest":
            tier.sort(key=lambda p: (-demand(p), stable_rank(loop_name, p.id)))
        else:
            tier.sort(key=lambda p: stable_rank(loop_name, p.id))
        chosen = tier[: n - len(picked)]
        picked.extend(chosen)
        taken.update(p.id for p in chosen)
        if chosen:
            counts[step.tier] = counts.get(step.tier, 0) + len(chosen)
    available = sum(1 for p in prompts if p.tier in {s.tier for s in steps})
    return Selection(prompts=picked, tier_counts=counts, available=available)


def rung_table_text() -> str:
    """The rung table in one line (written to generate.json beside the rung used)."""
    return "; ".join(f"rung {r}{'+' if r == max(SOURCE_RUNGS) else ''}: "
                     + " then ".join(f"{s.tier}{' (hardest first)' if s.order == 'hardest' else ''}" for s in steps)
                     for r, steps in SOURCE_RUNGS.items())


__all__ = ["INHERIT_BATTERY_LANGUAGE", "SOURCE_RUNGS", "Pool", "Selection", "TierStep", "demand", "normalised_text", "pool_sources", "route",
           "rung_steps", "rung_table_text", "select", "stable_rank"]
