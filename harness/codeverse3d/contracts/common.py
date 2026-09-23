"""Enums and small value types used everywhere."""

from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Vec3 = Annotated[tuple[float, float, float], Field(description="x, y, z")]


class Track(StrEnum):
    """What kind of 3D thing we are producing."""

    STATIC_OBJECT = "static_object"
    ARTICULATED_OBJECT = "articulated_object"
    SCENE = "scene"
    GRAPHICS = "graphics"  # shader / OpenGL effects (2D/2.5D/3D procedural graphics)


class Language(StrEnum):
    """Raw authoring language of the deliverable (no SDKs on top)."""

    BLENDER = "blender"  # pure bpy script → GLB/STL
    CADQUERY = "cadquery"  # pure cadquery script → STEP/STL/GLB
    THREEJS = "threejs"  # procedural three.js (ESM) → GLB via GLTFExporter
    URDF_BLENDER = "urdf_blender"  # bpy link meshes + hand-written URDF
    SCENE_THREEJS = "scene_threejs"  # multi-file three.js scene (+GLSL, +GLB assets)
    GLSL_SHADER = "glsl_shader"  # Shadertoy-style fragment shader (GLSL 330/ES 3.0), rendered by moderngl
    OPENGL_PYTHON = "opengl_python"  # raw OpenGL via moderngl + GLSL (multi-pass, geometry, FBOs)


#: languages allowed per track
TRACK_LANGUAGES: dict[Track, tuple[Language, ...]] = {
    Track.STATIC_OBJECT: (Language.BLENDER, Language.CADQUERY, Language.THREEJS),
    Track.ARTICULATED_OBJECT: (Language.URDF_BLENDER,),
    Track.SCENE: (Language.SCENE_THREEJS,),
    Track.GRAPHICS: (Language.GLSL_SHADER, Language.OPENGL_PYTHON),
}


class TrackInfo(BaseModel):
    """Per-track registry row: default judge rubric + human label.

    THE single source for what used to be restated as ``TRACK_RUBRIC`` /
    ``RUBRIC_BY_TRACK`` / ``TYPE_LABEL`` in judges, texturing, flywheel and cli.
    """

    model_config = ConfigDict(frozen=True)

    rubric: str = Field(description="rubric name under judges/rubrics/, e.g. 'static_object_v1'")
    label: str = Field(description="human label for galleries / captions / reports")
    best_of_n: bool = Field(default=True, description="the baseline is one session that --candidates N can run N "
                            "times side by side (a scene writes its baseline in the pre-round stages: no)")
    likeness_refs: bool = Field(default=False, description="reference images are a look to match (LikenessJudge, "
                                "the likeness prompt note), not a silhouette to measure")


#: one registry row per track
TRACK_INFO: dict[Track, TrackInfo] = {
    Track.STATIC_OBJECT: TrackInfo(rubric="static_object_v1", label="3D Objects"),
    Track.ARTICULATED_OBJECT: TrackInfo(rubric="articulated_v1", label="Articulated Objects"),
    Track.SCENE: TrackInfo(rubric="scene_v1", label="3D Scenes", best_of_n=False, likeness_refs=True),
    Track.GRAPHICS: TrackInfo(rubric="shader_v2", label="Procedural Graphics", likeness_refs=True),
}

#: entry file inside the workspace per language — the file a build starts from.
#: THE single source for entry paths (runtimes' first entry_glob, flywheel export).
ENTRY_FILE: dict[Language, str] = {
    Language.BLENDER: "src/model.py",
    Language.CADQUERY: "src/model.py",
    Language.URDF_BLENDER: "src/model.py",
    Language.THREEJS: "src/object.js",
    Language.SCENE_THREEJS: "src/scene.js",
    Language.GLSL_SHADER: "src/shader.frag",
    Language.OPENGL_PYTHON: "src/program.py",
}

#: source files the HARNESS writes under ``src/`` and owns: the agent reads them and calls what
#: they define, never writes them (``AgentJob.read_only`` — the agent's write scope excludes them).
#: Measured 2026-08-26 (eval/bench/out/seed_v1, aurora brief, gemini-3.7-flash api-agent): recipes
#: seeded into the agent's own ``src/common.glsl`` were gone by the end of the run — it rewrote
#: the file with its own helpers — so a seeded file has to be one the agent cannot rewrite.
#: An entry ending in ``/`` is a DIRECTORY prefix: every file under it is owned.  That is
#: how the 52-module effect library is named without listing 52 paths — and the list is
#: the library's contents, so an entry per file would go stale the day one is added.
HARNESS_OWNED_SRC: dict[Language, tuple[str, ...]] = {
    Language.GLSL_SHADER: ("src/recipes.glsl",),
    # the effect library (D51): the agent imports and calls it, never rewrites it.
    Language.SCENE_THREEJS: ("src/lib/",),
}


def is_harness_owned(rel: str, owned: Collection[str]) -> bool:
    """Is ``rel`` a harness-owned source file, given a language's ``HARNESS_OWNED_SRC``?

    Exact match, or under a directory entry (one ending in ``/``).
    """
    return any(rel == o or (o.endswith("/") and rel.startswith(o)) for o in owned)

#: human label per language (galleries, captions, reports)
LANGUAGE_LABEL: dict[Language, str] = {
    Language.BLENDER: "Blender Python",
    Language.CADQUERY: "CadQuery (Python)",
    Language.URDF_BLENDER: "URDF + Blender Python",
    Language.THREEJS: "Three.js (ESM)",
    Language.SCENE_THREEJS: "Three.js scene (multi-file ESM + GLSL)",
    Language.GLSL_SHADER: "GLSL fragment shader",
    Language.OPENGL_PYTHON: "OpenGL (moderngl Python + GLSL)",
}


def code_file(language: Language) -> str:
    """Top-level flywheel copy of the entry file: ``code`` + the entry suffix
    (``code.py`` / ``code.js`` / ``code.frag``)."""
    entry = ENTRY_FILE[language]
    return "code" + entry[entry.rfind(".") :]


class Usage(BaseModel):
    """Normalised token/cost accounting for ONE model or agent call."""

    backend: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            backend=self.backend or other.backend,
            model=self.model if self.model == other.model else (self.model or other.model),
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            thoughts_tokens=self.thoughts_tokens + other.thoughts_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_ms=self.latency_ms + other.latency_ms,
        )


class Budget(BaseModel):
    """Hard ceilings for a run.  The orchestrator stops (cleanly) when any is hit.

    The ceilings are non-negative by construction.  A negative one is not a small
    budget, it is an unrunnable one: ``BudgetGuard.ok()`` is False before a single
    token is spent and ``grant_grace`` cannot lift a hard ceiling back above zero —
    so the workspace and the git-committed spec are created for a run that can only die.

    A run is bounded by ``max_rounds`` and ``max_minutes``.  Cost is accumulated per call
    for the ledger and the run record, and never gates anything.

    ``extra="forbid"``: a caller still passing a retired ceiling (``max_usd``) or a typo
    must raise, not be silently swallowed.  Old on-disk specs are migrated in ONE place:
    ``Spec._strip_retired_budget_keys``.
    """

    model_config = ConfigDict(extra="forbid")

    max_rounds: int = Field(default=4, ge=0)
    max_minutes: float = Field(default=60.0, ge=0)
    max_repair_attempts: int = Field(default=3, ge=0)  # per build failure before escalating


class Backends(BaseModel):
    """Which model/agent does which job.  Ids are ``<kind>:<model>``:

    * API chat models:  ``gemini:gemini-3.7-flash`` · ``anthropic:claude-sonnet-5``
      · ``openai:gpt-5.6-sol``
    * Coding agents:    ``gemini-cli:gemini-3.7-flash`` · ``claude-code:sonnet``
      · ``codex:gpt-5.6-sol`` · ``agy:gemini-3.7-flash-high``

    The coding agent is always a VENDOR agent: the harness supplies the workspace, the
    prompt and its 3D tools (over MCP) and reads the result.  The in-process
    ``api-agent`` tool loop was deleted 2026-08-28 — reimplementing agent plumbing the
    vendors already ship was never this project's job (owner's call).
    """

    planner: str = "gemini:gemini-3.7-flash"
    generator: str = "gemini-cli:gemini-3.7-flash"
    # The judge drives the refine loop: the pro tier has ~3x lower sample noise than
    # flash (calibration 2026-08-23: std 0.03 vs 0.08-0.12) for ~$0.07 per verdict.
    judge: str = "gemini:gemini-3.1-pro-preview"
    captioner: str = "gemini:gemini-3.7-flash"


# ------------------------------------------------------------------ joint coupling
#: |multiplier| below this is a coupling that transmits no motion.  ONE value: the plan
#: validator used 1e-9 while the URDF loader and lint used 1e-12, so a coupling the
#: planner was forbidden to write was one the loader accepted.
MIMIC_MIN_MULTIPLIER = 1e-9


class MimicSpec(BaseModel):
    """One joint as the coupling rules see it, in whatever vocabulary the caller has.

    ``key`` is the caller's canonical name (the plan folds case and underscores, URDF
    does not); ``name`` is what its messages should print.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    movable: bool
    target: str | None = None  # key of the joint this one follows; None = drives itself
    multiplier: float = 1.0
    offset: float = 0.0
    lower: float | None = None  # this joint's own limits, when it declares them
    upper: float | None = None


class MimicIssue(BaseModel):
    """One broken coupling rule.  ``kind`` is what is wrong, ``joint``/``target`` are
    names as written, ``detail`` carries the joint a cycle closes through."""

    model_config = ConfigDict(frozen=True)

    joint: str
    kind: Literal["immobile", "zero_multiplier", "self", "unknown_target", "immobile_target",
                  "cycle", "out_of_range"]
    target: str
    detail: str = ""


def mimic_issues(specs: Collection[MimicSpec]) -> list[MimicIssue]:
    """Every rule a ``<mimic>`` has to satisfy, checked once over the whole joint set.

    Three layers reject the same bad couplings — ``ArticulatedPlan._check_mimics``,
    ``languages/urdf.lint`` and ``spatial.joints_model.load_urdf`` — and they had three
    copies of the walk that disagreed on the multiplier floor.  The rules live here; each
    caller renders the issues in its own vocabulary (``ValueError`` / gate finding /
    ``UrdfError``), picks the first one when it reports only one, and decides what is
    fatal: ``out_of_range`` is a warning in the lint and ignored by the loader, because
    the coupling still poses the mechanism — the follower's own limits are what disagree.
    """
    order = list(specs)
    by_key = {s.key: s for s in order}
    out: list[MimicIssue] = []
    for s in order:
        if s.target is None:
            continue
        if not s.movable:
            out.append(MimicIssue(joint=s.name, kind="immobile", target=s.target))
        if abs(s.multiplier) < MIMIC_MIN_MULTIPLIER:
            out.append(MimicIssue(joint=s.name, kind="zero_multiplier", target=s.target))
        if s.target == s.key:
            out.append(MimicIssue(joint=s.name, kind="self", target=s.name))
            continue
        src = by_key.get(s.target)
        if src is None:
            out.append(MimicIssue(joint=s.name, kind="unknown_target", target=s.target))
            continue
        if not src.movable:
            out.append(MimicIssue(joint=s.name, kind="immobile_target", target=src.name))
        reach = _driven_range(s, src)
        if reach is not None:
            out.append(MimicIssue(joint=s.name, kind="out_of_range", target=src.name,
                                  detail=f"[{reach[0]:.4g}, {reach[1]:.4g}]"))
        seen, cur = {s.key}, src
        while cur.target is not None:
            if cur.key in seen:
                out.append(MimicIssue(joint=s.name, kind="cycle", target=s.target, detail=cur.name))
                break
            seen.add(cur.key)
            nxt = by_key.get(cur.target)
            if nxt is None:
                break  # that joint reports its own unknown target on its own turn
            cur = nxt
    return out


def _driven_range(follower: MimicSpec, driver: MimicSpec) -> tuple[float, float] | None:
    """Where the coupling actually takes ``follower`` when both declare limits, if that
    is outside the follower's own — ``None`` when it fits or the limits are unknown.

    Slack is 1 % of the follower's span: the one case in 94 recorded couplings
    (wave2_lean, a folding brace) overshot 2.248 against 2.2, which is the multiplier and
    the limit rounded from different numbers rather than a mechanism that jams.
    """
    if None in (follower.lower, follower.upper, driver.lower, driver.upper):
        return None
    ends = (follower.multiplier * driver.lower + follower.offset,
            follower.multiplier * driver.upper + follower.offset)
    lo, hi = min(ends), max(ends)
    slack = max(1e-6, 0.01 * (follower.upper - follower.lower))
    if lo < follower.lower - slack or hi > follower.upper + slack:
        return lo, hi
    return None
