"""The routing table (design §5.2) and the one place a gate finding becomes a routing kind.

Two things live here and nowhere else:

**finding_kind()** — a finding's kind is ``"<gate>/<data.kind>"``, the kind the gate itself
wrote (``connectivity/penetration``, ``scene_frames/no_motion``); the ``lint:<language>``
gates are one ``lint`` family.  A finding whose gate writes no kind (the lints, motion_direction,
render_console) is ``"<gate>/untyped"``, which only a family row (``lint/*``) answers.  No
message text is read: the regex this replaced left seven scene_frames kinds unrouted and R19
unreachable (audit 2026-09-24, N52), and every gate reword could unroute a skill.  INFO lines
("all 7 parts are connected") are census, not defects, and have no kind.

**ROUTES** — typed rows, evaluated by ``select()`` below.  A row fires when ALL of its stated
conditions hold; ``priority`` decides who survives the cap, and every gate-fired row sits
at >= 90 so a repair round spends its budget on what actually broke.  Routing a kind is a
claim that a skill helps, and that claim needs evidence; most kinds have no row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from codeverse3d.skills.model import EVIDENCE_INHERITED, Selection, Skill

# --------------------------------------------------------------------------- finding kinds
#: the kind of a finding whose gate writes no ``data.kind``
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
LINT = f"lint/{UNTYPED}"


def finding_kind(finding: Any) -> str | None:
    """A ``GateFinding`` → ``"<gate>/<data.kind>"``, or ``None`` for census/INFO lines.

    Severity is honoured, not guessed at: an INFO finding is the gate reporting what it saw,
    and routing a skill off it would attach the penetration sheet to a run with none."""
    if str(getattr(finding, "severity", "warn")).lower() == "info":
        return None
    gate = str(getattr(finding, "gate", "") or "").strip().lower()
    family = "lint" if gate.startswith("lint") else gate
    data = getattr(finding, "data", None)
    kind = data.get("kind") if isinstance(data, dict) else None
    return f"{family}/{kind if isinstance(kind, str) and kind else UNTYPED}"


def finding_kinds(findings: object) -> list[str]:
    """Distinct kinds of an iterable of ``GateFinding`` (or of ``GateReport``s), in order."""
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
BUILD_KINDS = ("baseline", "part", "refine", "rebuild", "repair")
#: kinds that attach nothing on their own (design §5.2 law 4): short, narrow sessions the
#: corpus attaches no defect class to.  A gate-fired row still reaches them.
QUIET_KINDS = ("asset", "asset_fix", "reference")


@dataclass(frozen=True)
class Route:
    """One row of the table.  Empty tuple == "any"; ``findings`` == "a gate fired"."""

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
        """The first routed finding kind this row answers, or None."""
        for pat in self.findings:
            family = pat[:-1] if pat.endswith("*") else ""
            for k in kinds:
                if (family and k.startswith(family)) or k == pat:
                    return k
        return None


ROUTES: tuple[Route, ...] = (
    Route("R1", "c3d-part-contact", 70, tracks=OBJECT_TRACKS, requires_all=("multi_part",),
          why="a multi-part object: contact, penetration and stray islands are the top defect class (50.5% of runs)"),
    Route("R2", "c3d-part-contact", 95, kinds=("repair", "refine", "rebuild"),
          findings=("connectivity/*", "joint_sweep/*"),
          why="the previous round's connectivity/joint gate fired"),
    # R3 and R4 are object tracks only: the sheet's proof loop is measure / check_contract /
    # isolate, and a scene session has none of those tools (both reached scene sessions until 2026-09-22)
    Route("R3", "c3d-bbox-contract", 65, tracks=OBJECT_TRACKS,
          kinds=("baseline", "part", "refine", "rebuild"),
          why="this session owns dimensions, and the plan's numbers are the contract"),
    Route("R4", "c3d-bbox-contract", 95, tracks=OBJECT_TRACKS, kinds=("repair", "refine", "rebuild"),
          findings=("contract/*",),
          why="the previous round's contract gate fired"),
    # R5 was c3d-form-manifest, retired 2026-08-25: read 2/19 (11%, CI [3%,31%]) before and
    # after its description was revised.  See docs/SKILLS_LEDGER.md (the bundle's text is in git history).  The id is
    # not reused — a route id is how a ledger row and a run record refer to a routing decision.
    Route("R6", "c3d-repeats-and-mirrors", 55, tracks=OBJECT_TRACKS,
          kinds=("baseline", "part", "refine", "rebuild"),
          requires_any=("has_instances", "has_symmetry"),
          why="the plan repeats or mirrors a part, and the contract gate measures each instance"),
    Route("R7", "c3d-repeats-and-mirrors", 90, kinds=("repair", "refine"), findings=(CONTRACT_INSTANCE_BBOX,),
          why="an instanced part's bbox drifted"),
    Route("R8", "c3d-blender-forms", 75, languages=BLENDER_LANGS, kinds=BUILD_KINDS,
          why="blender authoring: the language of 47 of our graded runs"),
    Route("R9", "c3d-blender-forms", 95, languages=BLENDER_LANGS, kinds=("repair",),
          findings=(LINT,),
          why="the lint flagged the build script (a part file never imported, a bpy trap)"),
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
          findings=("joint_sweep/*", "motion_direction/*"),
          why="a joint_sweep or motion_direction gate fired on the previous round"),
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
    Route("R20", "c3d-threejs-shader-traps", 60, tracks=("scene", "static_object"), languages=THREEJS_LANGS,
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
    # R24 is one row per language: the design writes it as a single line, but a Route names
    # exactly one skill so telemetry can say which one the finding routed.
    Route("R24-glsl", "c3d-glsl-craft", 90, tracks=("graphics",), languages=("glsl_shader",),
          findings=(GL_FRAMES,), why="the frame gate saw no motion or no detail"),
    Route("R24-opengl", "c3d-opengl-pipeline", 90, tracks=("graphics",), languages=("opengl_python",),
          findings=(GL_FRAMES,), why="the frame gate saw no motion or no detail"),
    # R25-R28 (2026-09-01): the graphics-recipe port from the scene_multifile_graphics
    # reference (eval/docs/EVAL.md sceneloop entry).  Atmosphere and materials ride every
    # env/baseline build; water and night only when the plan's own words ask for them.
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
)

#: every skill the table can attach — the Author phase's contract for which bundles must exist
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
    """Route-relevant facts about a plan.  Tolerant by design: a missing/partial plan
    yields all-false signals rather than raising, because a planner failure must not
    also take out the round's skills.  Kept here, not on the Plan contracts: adding a
    routing signal must not migrate every stored plan."""
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
        # scene-content signals (2026-09-01, the graphics-recipe port): matched against the
        # plan's own words so the water/night recipes ride only when the brief wants them
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
) -> list[Selection]:
    """The routed bundles for one session, ranked, capped, each with its reason.

    ``findings`` may be ``GateFinding``s, ``GateReport``s, or already-classified kind
    strings — the caller usually has the previous ``RoundRecord.gates`` on hand.
    """
    from codeverse3d.skills import all_skills

    lib = library if library is not None else all_skills()
    sig = dict(signals or {})
    kinds = [f for f in findings if isinstance(f, str)] if findings else []
    if findings and not kinds:
        kinds = finding_kinds(findings)

    best: dict[str, Selection] = {}
    first_seen: dict[str, int] = {}
    for order, row in enumerate(ROUTES):
        hit = row.matches_finding(kinds) if row.gate_fired else None
        if row.gate_fired and hit is None:
            continue
        if not row.gate_fired and kind in QUIET_KINDS:
            # design §5.2 law 4: short, narrow sessions attach nothing on their own
            continue
        if not _row_applies(row, track, language, kind, sig):
            continue
        skill = lib.get(row.skill)
        if skill is None:
            # The Author phase ships bodies in its own commits; a table row without a
            # bundle must not crash a run — it is a missing skill, not a broken harness.
            log.debug("route %s names skill %r, which is not in the library", row.rule, row.skill)
            continue
        if skill.evidence == EVIDENCE_INHERITED and not allow_unverified:
            log.debug("skill %s is %s and C3D_SKILLS_UNVERIFIED is off; not routed", skill.name, EVIDENCE_INHERITED)
            continue
        reason = f"{row.rule}: {row.why}" + (f" [{hit}]" if hit else "")
        first_seen.setdefault(row.skill, order)
        prev = best.get(row.skill)
        if prev is None:
            best[row.skill] = Selection(skill=skill, priority=row.priority, rules=(row.rule,), reason=reason)
        elif row.priority > prev.priority:
            # a gate-fired row overrides the standing one, and says so in the reason
            best[row.skill] = Selection(skill=skill, priority=row.priority, rules=(*prev.rules, row.rule), reason=reason)
        else:
            best[row.skill] = prev.model_copy(update={"rules": (*prev.rules, row.rule)})

    # priority first (law 2: gate-fired rows outrank standing ones), then table order —
    # a stable, testable ranking, so the cap always cuts the same tail.
    ranked = sorted(best.values(), key=lambda s: (-s.priority, first_seen[s.name]))
    return ranked[:max(0, max_skills)]

