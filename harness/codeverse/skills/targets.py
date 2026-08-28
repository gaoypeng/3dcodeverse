"""What each bundle CLAIMS to move — one deterministic quantity per skill.

WHY this file exists.  A library you cannot measure per-skill is one you can only
maintain by taste.  ``metadata.evidence`` says where a bundle's *prose* came from;
nothing said what the bundle is supposed to *do* to a run, in a number a machine can
recompute.  This is that: one row per bundle, naming the quantity, the direction, and
the gate kinds (``skills.registry``) or artefact it is read out of.

Three rules the rows obey:

* **Deterministic only.**  Every row is read from a gate report, a build result or a
  measured artefact — never from the judge.  An 8-prompt A/A of the judged score gave
  paired sd 0.202 and needs ~408 paired prompts to resolve +0.02 (``docs/EVAL.md`` §8);
  a skills A/B read out on ``score`` at n=8 is a coin toss.  These do not carry that
  variance.
* **One owner per quantity.**  ``contract/instance_bbox`` belongs to
  ``cv3d-repeats-and-mirrors``, so ``cv3d-bbox-contract``'s row excludes it.  Two
  bundles sharing a metric means an A/B cannot say which one moved it.
* **``measurable=False`` is a finding, not a gap.**  A bundle whose claim no
  deterministic instrument can see says so here rather than borrowing a judged
  criterion and calling it evidence.

``bench/skill_targets.py`` is the readout; ``tests/skills/test_targets.py`` pins the
rows against the live gate vocabulary and against each bundle's own frontmatter.
"""

from __future__ import annotations

import importlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse.skills.model import Skill

#: the direction that counts as an improvement
DOWN = "down"
UP = "up"
DIRECTIONS = (DOWN, UP)

#: where the readout gets the number
SRC_GATE = "gate_kinds"     #: count findings of `kinds` in the last gated round
SRC_BUILD = "build"         #: the round's BuildResult
SRC_GLB = "glb"             #: recomputed from the exported object.glb
SRC_FRAMES = "frames"       #: recomputed from the round's sampled frames
SRC_ARTIFACT = "artifact"   #: a gate report written beside the run, not into the record
SOURCES = (SRC_GATE, SRC_BUILD, SRC_GLB, SRC_FRAMES, SRC_ARTIFACT)


@dataclass(frozen=True)
class Target:
    """One bundle's falsifiable claim."""

    skill: str
    metric: str
    direction: str
    unit: str
    source: str
    why: str
    #: gate kinds (skills.registry slugs) counted when ``source == SRC_GATE``
    kinds: tuple[str, ...] = ()
    #: severities that count; empty == every actionable (non-INFO) finding
    severities: tuple[str, ...] = ()
    #: languages the metric is defined over; empty == every language
    languages: tuple[str, ...] = field(default_factory=tuple)
    #: False when no deterministic instrument can see the bundle's claim
    measurable: bool = True
    caveat: str = ""

    @property
    def frontmatter(self) -> dict[str, str]:
        """The rows a bundle must carry under ``metadata`` (spec-legal string values)."""
        return {"target_metric": self.metric,
                "target_direction": self.direction,
                "target_unit": self.unit,
                "target_measurable": "true" if self.measurable else "false"}


OBJECT_LANGS = ("blender", "urdf_blender", "cadquery", "threejs")

TARGETS: tuple[Target, ...] = (
    Target(
        skill="cv3d-part-contact",
        metric="interpenetrating_pairs",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("connectivity/interpenetration",),
        languages=OBJECT_LANGS,
        why="the plain count of penetrating pairs is the strongest single predictor of "
            "assembly_fit (r = -0.258, docs/COMPLEXITY.md §6) and the bundle exists to lower it",
    ),
    Target(
        skill="cv3d-bbox-contract",
        metric="contract_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("contract/part_bbox", "contract/overall_bbox", "contract/footprint_offset",
               "contract/ground_gap", "contract/scene_bounds"),
        languages=(*OBJECT_LANGS, "scene_threejs"),
        why="the bundle is the contract gate's own tolerance arithmetic: a part that IS there "
            "and is the wrong size or in the wrong place",
        caveat="two contract kinds are deliberately somebody else's. contract/instance_* is "
               "cv3d-repeats-and-mirrors; contract/missing_part is a part that never reached the "
               "GLB at all, which is a language-API failure (cv3d-threejs-forms owns it for "
               "threejs; for blender its upstream signal is lint/part_not_imported, counted by "
               "cv3d-blender-forms) rather than a tolerance failure.",
    ),
    Target(
        skill="cv3d-repeats-and-mirrors",
        metric="contract_instance_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("contract/instance_bbox", "contract/instance_count"),
        languages=OBJECT_LANGS,
        why="metadata.owns already names these two kinds; they are the whole claim",
    ),
    Target(
        skill="cv3d-blender-forms",
        metric="blender_lint_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("lint/api_trap", "lint/part_not_imported"),
        languages=("blender", "urdf_blender"),
        why="the bundle is written around the five bpy traps the static lint already counts",
    ),
    Target(
        skill="cv3d-urdf-joints",
        metric="joint_sweep_errors",
        direction=DOWN,
        unit="ERROR findings per run",
        source=SRC_GATE,
        kinds=("joint_sweep/link_overlap", "joint_sweep/disconnected"),
        severities=("error",),
        languages=("urdf_blender",),
        why="metadata.owns names joint_sweep; 653 of 691 sweep findings in the corpus are "
            "moved-pose ERRORs, which is exactly what the clearance rule targets",
    ),
    Target(
        skill="cv3d-cadquery-forms",
        metric="build_failure_rate",
        direction=DOWN,
        unit="fraction of rounds whose build failed",
        source=SRC_BUILD,
        languages=("cadquery",),
        why="the bundle's own framing is 'the kernel rules that decide whether the script "
            "builds at all'; an OCC kernel refusal is a build failure, not a gate finding",
        caveat="no baseline exists: bench/out holds zero graded cadquery runs.",
    ),
    Target(
        skill="cv3d-threejs-forms",
        metric="missing_parts",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("contract/missing_part",),
        languages=("threejs",),
        why="the bundle's distinct claim is the traps that make a threejs part silently ship "
            "nothing (bevel_depth 0, no computeVertexNormals); the contract gate calls that a missing part",
        caveat="no baseline exists: bench/out holds zero graded threejs runs.",
    ),
    Target(
        skill="cv3d-scene-composition",
        metric="camera_placement_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("scene_frames/camera_placement",),
        languages=("scene_threejs",),
        why="cameras below ground or inside geometry are arithmetic failures, and the bundle's "
            "whole method is deriving them from measured bounds",
        caveat="frame coverage (content_frac) is the bundle's other half and registry.py "
               "classifies no kind for it, so it is not in this count.",
    ),
    Target(
        skill="cv3d-scene-lighting",
        metric="dark_or_flat_frames",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("scene_frames/dark_or_flat",),
        languages=("scene_threejs",),
        why="the bundle is the exposure thresholds of frame_metrics.py restated with a recipe; "
            "the gate counts exactly the frames it failed to light",
    ),
    Target(
        skill="cv3d-scene-motion",
        metric="min_authored_changed_frac",
        direction=UP,
        unit="fraction of pixels changed on the WEAKEST authored camera, t=0 → t=1.5 s",
        source=SRC_FRAMES,
        languages=("scene_threejs",),
        measurable=False,
        why="reported as a regression guard only",
        caveat="NOT a target. The bundle says so itself: re-measuring all 8 recorded rounds, every "
               "authored camera cleared the 0.4 % bar by 5-12x while animation_life averaged 0.431. "
               "The claim is per-plan-item ('the water and the leaves are static'), and no "
               "deterministic instrument attributes motion to a named plan item. Raising this "
               "number is not evidence the bundle worked; a drop below MOVING_FRAC still is a "
               "regression worth catching.",
    ),
    Target(
        skill="cv3d-threejs-shader-traps",
        metric="shader_preflight_findings",
        direction=DOWN,
        unit="WARN+ERROR findings per run",
        source=SRC_ARTIFACT,
        kinds=("shader/compile_or_binding",),
        languages=("scene_threejs", "threejs"),
        why="metadata.owns names shader/compile_or_binding; every trap the bundle teaches lands "
            "in the one shader_preflight report",
        caveat="zero headroom in today's corpus: all 4 recorded scene runs are clean, so this can "
               "only detect a regression until a battery makes it fire. shader_preflight is written "
               "beside the run (artifacts/shader_preflight.json) and is NOT merged per-round into "
               "the record, so only the run's final state is readable.",
    ),
    Target(
        skill="cv3d-glsl-craft",
        metric="mean_edge_density",
        direction=UP,
        unit="mean edge density over the 5 sampled frames (frame_stats)",
        source=SRC_FRAMES,
        languages=("glsl_shader",),
        why="the gl_frames gate fired zero findings on all 9 graded glsl runs, so its finding "
            "count has no headroom; edge density is the continuous quantity that gate thresholds "
            "and the quantity its own fix hint asks the agent to raise",
    ),
    Target(
        skill="cv3d-opengl-pipeline",
        metric="gl_frame_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("gl_frames/motion_or_detail",),
        languages=("opengl_python",),
        why="every gl_frames finding in the whole graphics corpus (3 flicker, 1 low detail) came "
            "from opengl_python, and flicker is trap 1 (a sampled solver) showing up in pixels",
    ),
)

BY_SKILL: dict[str, Target] = {t.skill: t for t in TARGETS}
METRICS: frozenset[str] = frozenset(t.metric for t in TARGETS)


def target_for(skill: str) -> Target | None:
    return BY_SKILL.get(skill)


def gate_kinds_claimed() -> frozenset[str]:
    """Every registry kind some row counts — what test_targets checks is still live."""
    return frozenset(k for t in TARGETS for k in t.kinds)


# ===================================================================== claims
# (merged from codeverse/skills/claims.py, 2026-08-28)
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
        for key_name in ("key", "text", "python"):
            if not row.get(key_name):
                issues.append(f"{where}: missing `{key_name}`")
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
