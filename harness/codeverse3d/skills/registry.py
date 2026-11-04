"""The skill routing table and the one place a gate finding becomes a routing kind.

``finding_kind()`` maps a finding to ``"<gate>/<data.kind>"`` (all ``lint:<language>`` gates are one
``lint`` family; an untyped finding is ``"<gate>/untyped"``); no message text is read and INFO
lines have no kind.  ``ROUTES`` are typed rows evaluated by ``select()``: a row fires when all its
conditions hold, ``priority`` decides who survives the cap, and gate-fired rows sit at >= 90.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from codeverse3d.contracts.artifacts import LintKind
from codeverse3d.skills.model import EVIDENCE_INHERITED, Selection, Skill

# --------------------------------------------------------------------------- finding kinds
UNTYPED = "untyped"
#: the kinds a route or a bundle target names on its own — the gates' own words
CONNECTIVITY_PENETRATION = "connectivity/penetration"
CONTRACT_PART_BBOX = "contract/part_bbox"
CONTRACT_INSTANCE_BBOX = "contract/instance_bbox"
CONTRACT_OVERALL_BBOX = "contract/overall_bbox"
CONTRACT_FOOTPRINT = "contract/footprint_offset"
CONTRACT_GROUND_GAP = "contract/ground_gap"
CONTRACT_MISSING_PART = "contract/missing_part"
CONTRACT_INSTANCE_COUNT = "contract/instance_count"
JOINT_SWEEP_PENETRATION = "joint_sweep/penetration"
JOINT_SWEEP_UNATTACHED = "joint_sweep/unattached"
SCENE_CAMERAS = "scene_frames/camera_*"
SCENE_DARK = "scene_frames/dark_frame"
SCENE_FLAT = "scene_frames/flat_frame"
SCENE_BLOWN = "scene_frames/blown_frame"
SCENE_NO_MOTION = "scene_frames/no_motion"
GL_FRAMES = "gl_frames/*"
SHADER_PREFLIGHT = "shader_preflight/*"
MOTION_WRONG_DIRECTION = "motion_direction/wrong_direction"
LINT = "lint/*"
LINT_PART_NOT_IMPORTED = f"lint/{LintKind.PART_NOT_IMPORTED.value}"
#: the scene_placement seating kinds; its PLAN kinds (no_fog, world_edge, ...) are not seating
SCENE_PLACEMENT_FLOATING = "scene_placement/floating"
SCENE_PLACEMENT_SUNKEN = "scene_placement/sunken"
SCENE_PLACEMENT_UNSUPPORTED = "scene_placement/unsupported"
SCENE_PLACEMENT_INTERPENETRATION = "scene_placement/interpenetration"
SCENE_PLACEMENT_FREE_EXEMPT = "scene_placement/free_exempt"
SCENE_PLACEMENT_CANTILEVERED = "scene_placement/cantilevered"
#: the seating kinds a placement row answers (a ``scene_placement/*`` pattern would over-attach)
SCENE_PLACEMENT_SEATING: tuple[str, ...] = (
    SCENE_PLACEMENT_FLOATING, SCENE_PLACEMENT_SUNKEN, SCENE_PLACEMENT_UNSUPPORTED,
    SCENE_PLACEMENT_INTERPENETRATION, SCENE_PLACEMENT_FREE_EXEMPT, SCENE_PLACEMENT_CANTILEVERED,
)


def gate_family(gate: str) -> str:
    gate = gate.strip().lower()
    return "lint" if gate.startswith("lint") else gate


def finding_kind(finding: Any) -> str | None:
    """A ``GateFinding`` → ``"<gate>/<data.kind>"``, or ``None`` for census/INFO lines.

    Severity is honoured, not guessed at: an INFO finding is the gate reporting what it saw,
    and routing a skill off it would attach the penetration sheet to a run with none."""
    if str(getattr(finding, "severity", "warn")).lower() == "info":
        return None
    family = gate_family(str(getattr(finding, "gate", "") or ""))
    data = getattr(finding, "data", None)
    kind = data.get("kind") if isinstance(data, dict) else None
    return f"{family}/{kind if isinstance(kind, str) and kind else UNTYPED}"


def kind_matches(kind: str, patterns: tuple[str, ...]) -> bool:
    return any(kind.startswith(p[:-1]) if p.endswith("*") else kind == p for p in patterns)


def finding_kinds(findings: object) -> list[str]:
    out: list[str] = []
    for f in _iter_findings(findings):
        k = finding_kind(f)
        if k and k not in out:
            out.append(k)
    return out


def _iter_findings(obj: object):
    for item in obj or ():  # type: ignore[union-attr]
        inner = getattr(item, "findings", None)
        if inner is not None:
            yield from inner
        else:
            yield item


# --------------------------------------------------------------------------- routes
OBJECT_TRACKS = ("static_object", "articulated_object")
BLENDER_LANGS = ("blender", "urdf_blender")
THREEJS_LANGS = ("scene_threejs", "threejs")
BPY_SCENE_LANGS = ("scene_blender",)
#: the scene track's session kinds that attach skills
BPY_SCENE_KINDS = ("env", "zone", "refine", "rebuild")
BUILD_KINDS = ("baseline", "part", "refine", "rebuild", "repair")
#: kinds that attach nothing on their own; a gate-fired row still reaches them
QUIET_KINDS = ("asset", "asset_fix", "reference")


@dataclass(frozen=True)
class Route:

    rule: str
    skill: str
    priority: int
    tracks: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    requires_all: tuple[str, ...] = ()
    requires_any: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    why: str = ""

    @property
    def gate_fired(self) -> bool:
        return bool(self.findings)

    def matches_finding(self, kinds: list[str]) -> str | None:
        return next((k for pat in self.findings for k in kinds if kind_matches(k, (pat,))), None)


ROUTES: tuple[Route, ...] = (
    Route("R1", "c3d-part-contact", 70, tracks=OBJECT_TRACKS, requires_all=("multi_part",),
          why="a multi-part object: contact, penetration and stray islands are the top defect class (50.5% of runs)"),
    Route("R2", "c3d-part-contact", 95, kinds=("repair", "refine", "rebuild"),
          findings=("connectivity/*", "joint_sweep/*"),
          why="the previous round's connectivity/joint gate fired"),
    # R3 and R4 are object tracks only: a scene session has no measure / check_contract / isolate tools
    Route("R3", "c3d-bbox-contract", 65, tracks=OBJECT_TRACKS,
          kinds=("baseline", "part", "refine", "rebuild"),
          why="this session owns dimensions, and the plan's numbers are the contract"),
    Route("R4", "c3d-bbox-contract", 95, tracks=OBJECT_TRACKS, kinds=("repair", "refine", "rebuild"),
          findings=("contract/*",),
          why="the previous round's contract gate fired"),
    # R5 is retired; a route id is how a ledger row and a run record name a routing decision, so ids are never reused
    Route("R6", "c3d-repeats-and-mirrors", 55, tracks=OBJECT_TRACKS,
          kinds=("baseline", "part", "refine", "rebuild"),
          requires_any=("has_instances", "has_symmetry"),
          why="the plan repeats or mirrors a part, and the contract gate measures each instance"),
    Route("R7", "c3d-repeats-and-mirrors", 90, kinds=("repair", "refine"), findings=(CONTRACT_INSTANCE_BBOX,),
          why="an instanced part's bbox drifted"),
    Route("R8", "c3d-blender-forms", 75, languages=BLENDER_LANGS, kinds=BUILD_KINDS,
          why="blender authoring: the language of 47 of our graded runs"),
    Route("R9", "c3d-blender-forms", 95, languages=BLENDER_LANGS, kinds=("repair",),
          findings=(LINT_PART_NOT_IMPORTED,),
          why="a part file was written but never imported, so its geometry does not exist"),
    Route("R10", "c3d-cadquery-forms", 75, languages=("cadquery",), kinds=BUILD_KINDS,
          why="cadquery authoring (evidence: inherited-unverified — unranked by our corpus; routed by default "
              "since 2026-09-22, C3D_SKILLS_UNVERIFIED=0 drops it)"),
    Route("R11", "c3d-threejs-forms", 75, tracks=("static_object",), languages=("threejs",), kinds=BUILD_KINDS,
          why="three.js object authoring (evidence: inherited-unverified — unranked by our corpus; routed by "
              "default since 2026-09-22, C3D_SKILLS_UNVERIFIED=0 drops it)"),
    Route("R12", "c3d-urdf-joints", 80, tracks=("articulated_object",), languages=("urdf_blender",),
          kinds=("baseline", "refine", "rebuild", "repair"),
          why="joints, limits and axes: joint_sweep fires on 38% and motion_direction on 35% of urdf runs"),
    Route("R13", "c3d-urdf-joints", 95, tracks=("articulated_object",), languages=("urdf_blender",),
          findings=("joint_sweep/*", MOTION_WRONG_DIRECTION),
          why="a joint_sweep finding, or a joint that moves against the plan, on the previous round"),
    Route("R14", "c3d-scene-composition", 75, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "env", "zone", "refine"),
          why="scene layout and camera framing"),
    Route("R15", "c3d-scene-composition", 95, tracks=("scene",), languages=("scene_threejs",),
          findings=(SCENE_CAMERAS, "scene_frames/content_small", "scene_frames/hero_*"),
          why="a camera was badly placed, or a shot shows too little of the scene or its hero"),
    Route("R16", "c3d-scene-lighting", 65, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "env", "refine"),
          why="lighting and exposure decide whether the frame reads at all"),
    Route("R17", "c3d-scene-lighting", 95, tracks=("scene",), languages=("scene_threejs",),
          findings=(SCENE_DARK, SCENE_FLAT, SCENE_BLOWN),
          why="the frame gate called the render dark, flat or blown out"),
    Route("R18", "c3d-scene-motion", 70, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "zone", "refine"),
          why="animation_life 0.431 is the lowest criterion in the whole corpus"),
    Route("R19", "c3d-scene-motion", 90, tracks=("scene",), languages=("scene_threejs",),
          findings=(SCENE_NO_MOTION,),
          why="the frame-difference gate saw nothing move on an authored camera"),
    Route("R20", "c3d-threejs-shader-traps", 60, tracks=("scene",), languages=("scene_threejs",),
          requires_all=("has_custom_shader",),
          why="the plan names a custom shader/material effect"),
    Route("R21", "c3d-threejs-shader-traps", 95, languages=THREEJS_LANGS, findings=(SHADER_PREFLIGHT,),
          why="the shader preflight flagged a material (a compile error, an unbound uniform, no fog)"),
    Route("R22", "c3d-glsl-craft", 80, tracks=("graphics",), languages=("glsl_shader",),
          kinds=("baseline", "refine", "repair", "rebuild"),
          why="fragment-shader craft: technical_cleanliness 0.597 is the lowest glsl criterion"),
    Route("R23", "c3d-opengl-pipeline", 80, tracks=("graphics",), languages=("opengl_python",),
          kinds=("baseline", "refine", "repair", "rebuild"),
          why="pipeline setup and pass structure: originality 0.330 is the lowest criterion anywhere"),
    # one row per language: a Route names exactly one skill so telemetry can say which one a finding routed
    Route("R24-glsl", "c3d-glsl-craft", 90, tracks=("graphics",), languages=("glsl_shader",),
          findings=(GL_FRAMES,), why="the frame gate saw no motion or no detail"),
    Route("R24-opengl", "c3d-opengl-pipeline", 90, tracks=("graphics",), languages=("opengl_python",),
          findings=(GL_FRAMES,), why="the frame gate saw no motion or no detail"),
    Route("R25", "c3d-scene-atmosphere", 70, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "env", "refine"),
          why="the sky bake is the light source every PBR surface reflects; 4/9 reference scenes shipped it inverted"),
    Route("R26", "c3d-scene-water", 85, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "env", "zone", "refine"), requires_any=("wants_water",),
          why="the plan's own words ask for water; metallic water is the reference's most-shipped material mistake"),
    Route("R27", "c3d-scene-night", 85, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "env", "zone", "refine"), requires_any=("wants_night",),
          why="the plan's own words ask for night; shafts/emissive discipline is measured in the reference ledger"),
    Route("R28", "c3d-scene-materials", 65, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "zone", "refine"),
          why="flat albedo and identical twins are the reference audit's most-cited surface defects"),
    Route("R29", "c3d-bpy-scene-placement", 80, tracks=("scene",), languages=BPY_SCENE_LANGS, kinds=BPY_SCENE_KINDS,
          why="every placed thing is a measured placement row, and the strict 2026-10-14 thresholds make a "
              "hand's breadth of hover an ERROR with a score ceiling"),
    Route("R30", "c3d-bpy-scene-placement", 95, tracks=("scene",), languages=BPY_SCENE_LANGS,
          findings=SCENE_PLACEMENT_SEATING,
          why="the previous round's placement gate found a floating, sunken, unsupported, interpenetrating or "
              "cantilevered asset, or too many free-tagged ones"),
    Route("R31", "c3d-bpy-scene-lighting", 65, tracks=("scene",), languages=BPY_SCENE_LANGS, kinds=BPY_SCENE_KINDS,
          why="Cycles under AgX at exposure 0 with no exposure knob: lamp energy, world strength and albedo are "
              "the only dials, and the frame gate measures both ends"),
    Route("R32", "c3d-bpy-scene-lighting", 95, tracks=("scene",), languages=BPY_SCENE_LANGS,
          findings=(SCENE_DARK, SCENE_FLAT, SCENE_BLOWN),
          why="the frame gate called a Blender render dark, flat or blown out"),
    Route("R33", "c3d-bpy-scene-materials", 65, tracks=("scene",), languages=BPY_SCENE_LANGS, kinds=BPY_SCENE_KINDS,
          why="the census reads three Principled sockets and no gate rewards material work, so placeholder and "
              "flat-ground defects are the judge's alone to penalise"),
    Route("R34", "c3d-scene-composition", 95, tracks=("scene",), languages=("scene_threejs",),
          findings=SCENE_PLACEMENT_SEATING,
          why="the previous round's placement gate fired on a three.js scene: one height function everything "
              "samples is the bundle's first rule"),
)

#: every skill the table can attach
ROUTED_SKILLS: tuple[str, ...] = tuple(dict.fromkeys(r.skill for r in ROUTES))


# ===================================================================== router
log = logging.getLogger(__name__)

_SHADER_WORDS = ("shader", "glsl", "onbeforecompile", "shadermaterial", "custom material",
                 "raymarch", "postprocess", "post-process")


_WATER_WORDS = ("water", "pond", "lake", "river", "ocean", "sea", "harbor", "harbour",
                "canal", "pool", "waterfall", "fountain", "shore", "beach", "koi")
_NIGHT_WORDS = ("night", "neon", "dusk", "evening", "moonlit", "moonlight", "lamplit",
                "midnight", "nocturnal", "starlit")


def plan_signals(plan: Any | None) -> dict[str, Any]:
    """Route-relevant facts about a plan; a missing or partial plan yields all-false signals."""
    parts = list(getattr(plan, "parts", None) or [])
    joints = list(getattr(plan, "joints", None) or [])
    effects = list(getattr(plan, "effects", None) or [])
    passes = list(getattr(plan, "passes", None) or [])
    text = " ".join(str(x) for x in (
        getattr(plan, "summary", ""), getattr(plan, "style_notes", ""), getattr(plan, "environment", ""),
        getattr(plan, "style", ""), getattr(plan, "motion", ""),
        *(f"{getattr(e, 'kind', '')} {getattr(e, 'description', '')}" for e in effects),
        *(f"{getattr(p, 'kind', '')} {getattr(p, 'description', '')}" for p in passes),
    )).lower()
    n_parts = len(parts)
    return {
        "n_parts": n_parts,
        "multi_part": n_parts >= 2,
        "has_instances": any(int(getattr(p, "instances", 1) or 1) > 1 for p in parts),
        "has_symmetry": any(str(getattr(p, "symmetry", "none") or "none") != "none" for p in parts),
        "has_assemblies": any(getattr(p, "children", None) for p in parts),
        "has_joints": bool(joints),
        "joint_types": sorted({str(getattr(j, "type", "")) for j in joints if getattr(j, "type", "")}),
        "has_custom_shader": bool(effects) or any(w in text for w in _SHADER_WORDS),
        "wants_water": any(w in text for w in _WATER_WORDS),
        "wants_night": any(w in text for w in _NIGHT_WORDS),
    }


def _row_applies(row: Route, track: str, language: str, kind: str, signals: dict[str, Any]) -> bool:
    if row.tracks and track not in row.tracks:
        return False
    if row.languages and language not in row.languages:
        return False
    if row.kinds and kind not in row.kinds:
        return False
    if any(not signals.get(k) for k in row.requires_all):
        return False
    return not (row.requires_any and not any(signals.get(k) for k in row.requires_any))


#: the floor of a gate-fired row: ``Selection.gate_fired`` reads priority >= 90 as "a gate routed this",
#: so a variant boost may not move a row across it
GATE_FIRED_PRIORITY = 90


def effective_priority(row: Route, boosts: Mapping[str, int]) -> int:
    """``row.priority`` plus the variant's boost, clamped so standing rows stay below and gate-fired rows at or above the floor."""
    delta = int(boosts.get(row.skill, 0))
    if not delta:
        return row.priority
    boosted = row.priority + delta
    if row.gate_fired:
        return max(GATE_FIRED_PRIORITY, boosted)
    if boosted >= GATE_FIRED_PRIORITY:
        log.warning("variant boost %+d lifts standing route %s (%s, priority %d) to %d: clamped to %d — a "
                    "standing row never outranks a gate-fired one", delta, row.rule, row.skill, row.priority,
                    boosted, GATE_FIRED_PRIORITY - 1)
        return GATE_FIRED_PRIORITY - 1
    return max(0, boosted)


def select(
    track: str,
    language: str,
    kind: str,
    *,
    signals: dict[str, Any] | None = None,
    findings: Any = (),
    library: dict[str, Skill] | None = None,
    max_skills: int = 5,
    allow_unverified: bool = False,
    routes: Sequence[Route] = ROUTES,
    boosts: Mapping[str, int] | None = None,
    disabled: frozenset[str] = frozenset(),
) -> list[Selection]:
    """The routed bundles for one session, ranked, capped, each with its reason.

    ``findings`` may be ``GateFinding``s, ``GateReport``s or kind strings; ``routes`` / ``boosts`` /
    ``disabled`` are a variant's reshaping of the table.
    """
    from codeverse3d.skills import all_skills

    lib = library if library is not None else all_skills()
    sig = dict(signals or {})
    boost: Mapping[str, int] = boosts or {}
    kinds = [f for f in findings if isinstance(f, str)] if findings else []
    if findings and not kinds:
        kinds = finding_kinds(findings)

    best: dict[str, Selection] = {}
    first_seen: dict[str, int] = {}
    for order, row in enumerate(routes):
        if row.rule in disabled:
            continue
        hit = row.matches_finding(kinds) if row.gate_fired else None
        if row.gate_fired and hit is None:
            continue
        if not row.gate_fired and kind in QUIET_KINDS:
            continue
        if not _row_applies(row, track, language, kind, sig):
            continue
        skill = lib.get(row.skill)
        if skill is None:
            # a table row without a bundle must not crash a run
            log.debug("route %s names skill %r, which is not in the library", row.rule, row.skill)
            continue
        if skill.evidence == EVIDENCE_INHERITED and not allow_unverified:
            log.debug("skill %s is %s and C3D_SKILLS_UNVERIFIED is off; not routed", skill.name, EVIDENCE_INHERITED)
            continue
        priority = effective_priority(row, boost)
        reason = (f"{row.rule}: {row.why}" + (f" [{hit}]" if hit else "")
                  + (f" (variant boost {priority - row.priority:+d})" if priority != row.priority else ""))
        first_seen.setdefault(row.skill, order)
        prev = best.get(row.skill)
        if prev is None:
            best[row.skill] = Selection(skill=skill, priority=priority, rules=(row.rule,), reason=reason)
        elif priority > prev.priority:
            best[row.skill] = Selection(skill=skill, priority=priority, rules=(*prev.rules, row.rule), reason=reason)
        else:
            best[row.skill] = prev.model_copy(update={"rules": (*prev.rules, row.rule)})

    # priority first (gate-fired outranks standing), then table order: the cap cuts a stable tail
    ranked = sorted(best.values(), key=lambda s: (-s.priority, first_seen[s.name]))
    return ranked[:max(0, max_skills)]

