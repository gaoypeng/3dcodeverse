"""The routing table (design §5.2) and the ONE place gate message text is pattern-matched.

Two things live here and nowhere else:

**finding_kind()** — ``GateFinding`` carries no ``kind`` field (``contracts/artifacts.py``),
so turning "'Lid' and 'Body' interpenetrate by ~7.4 mm" into the stable slug
``connectivity/interpenetration`` needs a classifier.  Keeping it in one function means a
gate reword breaks one golden test instead of silently unrouting a repair skill.  It
classifies **actionable** findings only: INFO lines ("named objects: [...]") are census,
not defects, and return ``None``.

**ROUTES** — typed rows, evaluated by ``router.py``.  A row fires when ALL of its stated
conditions hold; ``priority`` decides who survives the cap, and every gate-fired row sits
at >= 90 so a repair round spends its budget on what actually broke.

Classification is deliberately WIDER than routing: several kinds below have no rule at
all.  Naming a defect costs nothing and lets the golden test assert full coverage of the
corpus; routing one is a claim that a skill helps, and that claim needs evidence.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from codeverse.skills.model import EVIDENCE_INHERITED, Selection, Skill

# --------------------------------------------------------------------------- finding kinds
CONNECTIVITY_INTERPENETRATION = "connectivity/interpenetration"
CONNECTIVITY_STRAY_ISLANDS = "connectivity/stray_islands"
CONNECTIVITY_FLOATING_PART = "connectivity/floating_part"
CONNECTIVITY_NO_GROUND = "connectivity/no_ground_contact"
CONNECTIVITY_NO_GEOMETRY = "connectivity/no_geometry"
CONTRACT_PART_BBOX = "contract/part_bbox"
CONTRACT_INSTANCE_BBOX = "contract/instance_bbox"
CONTRACT_OVERALL_BBOX = "contract/overall_bbox"
CONTRACT_FOOTPRINT = "contract/footprint_offset"
CONTRACT_GROUND_GAP = "contract/ground_gap"
CONTRACT_MISSING_PART = "contract/missing_part"
CONTRACT_INSTANCE_COUNT = "contract/instance_count"
CONTRACT_SCENE_BOUNDS = "contract/scene_bounds"
JOINT_SWEEP_LINK_OVERLAP = "joint_sweep/link_overlap"
JOINT_SWEEP_DISCONNECTED = "joint_sweep/disconnected"
MOTION_WRONG_AXIS = "motion_direction/wrong_axis"
MOTION_SKIPPED = "motion_direction/skipped"
SCENE_DARK_OR_FLAT = "scene_frames/dark_or_flat"
SCENE_CAMERA_PLACEMENT = "scene_frames/camera_placement"
GL_MOTION_OR_DETAIL = "gl_frames/motion_or_detail"
LINT_PART_NOT_IMPORTED = "lint/part_not_imported"
LINT_LINK_NAME = "lint/link_name"
LINT_API_TRAP = "lint/api_trap"
SHADER_COMPILE_OR_BINDING = "shader/compile_or_binding"
DETAIL_DRIFT = "detail_drift/part_moved"
RUNTIME_SLOW = "render_console/slow_or_error"

#: (gate predicate, message regex, kind).  Order matters: first match wins.
_RULES: tuple[tuple[str, str, str], ...] = (
    ("connectivity", r"interpenetrate", CONNECTIVITY_INTERPENETRATION),
    ("connectivity", r"tiny disconnected island", CONNECTIVITY_STRAY_ISLANDS),
    ("connectivity", r"is floating", CONNECTIVITY_FLOATING_PART),
    ("connectivity", r"no part touches the ground", CONNECTIVITY_NO_GROUND),
    ("connectivity", r"no mesh parts", CONNECTIVITY_NO_GEOMETRY),
    ("contract", r"\(each instance\)|\(all instances\)", CONTRACT_INSTANCE_BBOX),
    ("contract", r"part '.*' bbox deviates", CONTRACT_PART_BBOX),
    ("contract", r"overall bbox deviates", CONTRACT_OVERALL_BBOX),
    ("contract", r"footprint centre", CONTRACT_FOOTPRINT),
    ("contract", r"floats .* the ground", CONTRACT_GROUND_GAP),
    ("contract", r"missing from the GLB|not found in the exported scene", CONTRACT_MISSING_PART),
    ("contract", r"instance\(s\), plan asks for", CONTRACT_INSTANCE_COUNT),
    ("contract", r"exceeds the planned bounds", CONTRACT_SCENE_BOUNDS),
    ("joint_sweep", r"overlap by", JOINT_SWEEP_LINK_OVERLAP),
    ("joint_sweep", r"apart at rest|nothing physically connects", JOINT_SWEEP_DISCONNECTED),
    ("motion_direction", r"check skipped|checks skipped", MOTION_SKIPPED),
    ("motion_direction", r"WRONG", MOTION_WRONG_AXIS),
    ("scene_frames", r"too dark|flat frame", SCENE_DARK_OR_FLAT),
    ("scene_frames", r"BELOW the ground|inside / touching geometry", SCENE_CAMERA_PLACEMENT),
    ("gl_frames", r"frame-to-frame|visual detail|do not change over time"
                 r"|essentially black|blown out|NaN/Inf|no frames were rendered", GL_MOTION_OR_DETAIL),
    ("detail_drift", r"moved|resized|removed|added", DETAIL_DRIFT),
    ("render_console", r"frame rate|error", RUNTIME_SLOW),
    ("shader_preflight", r".", SHADER_COMPILE_OR_BINDING),
    ("lint", r"never imported", LINT_PART_NOT_IMPORTED),
    ("lint", r"never appears as a string", LINT_LINK_NAME),
    ("lint", r"unbound|uniform|ignores scene\.fog|shader (?:fail|error|compile)", SHADER_COMPILE_OR_BINDING),
    ("lint", r".", LINT_API_TRAP),
)
_COMPILED = tuple((gate, re.compile(pat, re.I), kind) for gate, pat, kind in _RULES)


def finding_kind(gate: str, message: str, severity: str = "warn") -> str | None:
    """``(gate, message)`` → a stable defect slug, or ``None`` for census/INFO lines.

    ``severity`` is honoured, not guessed at: an INFO finding is the gate reporting what
    it saw ("all 7 parts are connected"), and routing a skill off it would attach the
    interpenetration sheet to a run with no interpenetration.
    """
    if str(severity).lower() == "info":
        return None
    g = (gate or "").strip().lower()
    text = message or ""
    for want, pattern, kind in _COMPILED:
        if (g == want or (want == "lint" and g.startswith("lint:"))) and pattern.search(text):
            return kind
    return None


def finding_kinds(findings: object) -> list[str]:
    """Distinct kinds of an iterable of ``GateFinding`` (or of ``GateReport``s), in order."""
    out: list[str] = []
    for f in _iter_findings(findings):
        k = finding_kind(getattr(f, "gate", ""), getattr(f, "message", ""),
                         str(getattr(f, "severity", "warn")))
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
BUILD_KINDS = ("baseline", "part", "detail", "refine", "rebuild", "repair")
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
    Route("R1", "cv3d-part-contact", 70, tracks=OBJECT_TRACKS, requires_all=("multi_part",),
          why="a multi-part object: contact, penetration and stray islands are the top defect class (50.5% of runs)"),
    Route("R2", "cv3d-part-contact", 95, kinds=("repair", "refine", "rebuild"),
          findings=("connectivity/*", "joint_sweep/*"),
          why="the previous round's connectivity/joint gate fired"),
    Route("R3", "cv3d-bbox-contract", 65, tracks=(*OBJECT_TRACKS, "scene"),
          kinds=("baseline", "part", "detail", "refine", "rebuild", "zone"),
          why="this session owns dimensions, and the plan's numbers are the contract"),
    Route("R4", "cv3d-bbox-contract", 95, kinds=("repair", "refine", "rebuild"), findings=("contract/*",),
          why="the previous round's contract gate fired"),
    # R5 was cv3d-form-manifest, retired 2026-08-25: read 2/19 (11%, CI [3%,31%]) before and
    # after its description was revised.  See docs/skills-attic/cv3d-form-manifest/.  The id is
    # not reused — a route id is how a ledger row and a run record refer to a routing decision.
    Route("R6", "cv3d-repeats-and-mirrors", 55, tracks=OBJECT_TRACKS,
          kinds=("baseline", "part", "detail", "refine", "rebuild"),
          requires_any=("has_instances", "has_symmetry"),
          why="the plan repeats or mirrors a part, and the contract gate measures each instance"),
    Route("R7", "cv3d-repeats-and-mirrors", 90, kinds=("repair", "refine"), findings=("contract/instance_bbox",),
          why="an instanced part's bbox drifted"),
    Route("R8", "cv3d-blender-forms", 75, languages=BLENDER_LANGS, kinds=BUILD_KINDS,
          why="blender authoring: the language of 47 of our graded runs"),
    Route("R9", "cv3d-blender-forms", 95, languages=BLENDER_LANGS, kinds=("repair",),
          findings=(LINT_PART_NOT_IMPORTED,),
          why="a part file was written but never imported, so its geometry does not exist"),
    Route("R10", "cv3d-cadquery-forms", 75, languages=("cadquery",), kinds=BUILD_KINDS,
          why="cadquery authoring (evidence: inherited-unverified — 0 graded runs, routed off by default)"),
    Route("R11", "cv3d-threejs-forms", 75, tracks=("static_object",), languages=("threejs",), kinds=BUILD_KINDS,
          why="three.js object authoring (evidence: inherited-unverified — 0 graded runs, routed off by default)"),
    Route("R12", "cv3d-urdf-joints", 80, tracks=("articulated_object",), languages=("urdf_blender",),
          kinds=("baseline", "refine", "rebuild", "repair"),
          why="joints, limits and axes: joint_sweep fires on 38% and motion_direction on 35% of urdf runs"),
    Route("R13", "cv3d-urdf-joints", 95, tracks=("articulated_object",), languages=("urdf_blender",),
          findings=("joint_sweep/*", "motion_direction/*"),
          why="a joint_sweep or motion_direction gate fired on the previous round"),
    Route("R14", "cv3d-scene-composition", 75, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "env", "zone", "compose", "refine"),
          why="scene layout and camera framing"),
    Route("R15", "cv3d-scene-composition", 95, tracks=("scene",), languages=("scene_threejs",),
          findings=(SCENE_CAMERA_PLACEMENT,),
          why="the camera was below ground or inside geometry"),
    Route("R16", "cv3d-scene-lighting", 65, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "env", "refine"),
          why="lighting and exposure decide whether the frame reads at all"),
    Route("R17", "cv3d-scene-lighting", 95, tracks=("scene",), languages=("scene_threejs",),
          findings=(SCENE_DARK_OR_FLAT,),
          why="the frame gate called the render dark or flat"),
    Route("R18", "cv3d-scene-motion", 70, tracks=("scene",), languages=("scene_threejs",),
          kinds=("baseline", "zone", "refine", "compose"),
          why="animation_life 0.431 is the lowest criterion in the whole corpus"),
    Route("R19", "cv3d-scene-motion", 90, tracks=("scene",), languages=("scene_threejs",),
          findings=(GL_MOTION_OR_DETAIL,),
          why="the frame-difference gate saw a flicker or a still image"),
    Route("R20", "cv3d-threejs-shader-traps", 60, tracks=("scene", "static_object"), languages=THREEJS_LANGS,
          requires_all=("has_custom_shader",),
          why="the plan names a custom shader/material effect"),
    Route("R21", "cv3d-threejs-shader-traps", 95, languages=THREEJS_LANGS, findings=(SHADER_COMPILE_OR_BINDING,),
          why="a shader failed to compile or a uniform was unbound"),
    Route("R22", "cv3d-glsl-craft", 80, tracks=("graphics",), languages=("glsl_shader",),
          kinds=("baseline", "refine", "repair", "rebuild"),
          why="fragment-shader craft: technical_cleanliness 0.597 is the lowest glsl criterion"),
    Route("R23", "cv3d-opengl-pipeline", 80, tracks=("graphics",), languages=("opengl_python",),
          kinds=("baseline", "refine", "repair", "rebuild"),
          why="pipeline setup and pass structure: originality 0.330 is the lowest criterion anywhere"),
    # R24 is one row per language: the design writes it as a single line, but a Route names
    # exactly one skill so telemetry can say which one the finding routed.
    Route("R24-glsl", "cv3d-glsl-craft", 90, tracks=("graphics",), languages=("glsl_shader",),
          findings=(GL_MOTION_OR_DETAIL,), why="the frame gate saw no motion or no detail"),
    Route("R24-opengl", "cv3d-opengl-pipeline", 90, tracks=("graphics",), languages=("opengl_python",),
          findings=(GL_MOTION_OR_DETAIL,), why="the frame gate saw no motion or no detail"),
)

#: every skill the table can attach — the Author phase's contract for which bundles must exist
ROUTED_SKILLS: tuple[str, ...] = tuple(dict.fromkeys(r.skill for r in ROUTES))


# ===================================================================== router
log = logging.getLogger(__name__)

#: plan-derived booleans/ints the table may test.  Kept here (not on the Plan contracts)
#: because they are a routing concern: adding one must not migrate every stored plan.
SIGNAL_KEYS = ("n_parts", "multi_part", "has_instances", "has_symmetry", "has_assemblies",
               "has_joints", "joint_types", "has_custom_shader")

_SHADER_WORDS = ("shader", "glsl", "onbeforecompile", "shadermaterial", "custom material",
                 "raymarch", "postprocess", "post-process")


def plan_signals(plan: Any | None) -> dict[str, Any]:
    """Route-relevant facts about a plan.  Tolerant by design: a missing/partial plan
    yields all-false signals rather than raising, because a planner failure must not
    also take out the round's skills."""
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
    from codeverse.skills import all_skills

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
            log.debug("skill %s is %s and CV3D_SKILLS_UNVERIFIED is off; not routed", skill.name, EVIDENCE_INHERITED)
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


def skills_for(
    track: str,
    language: str,
    kind: str,
    *,
    plan: Any | None = None,
    findings: Any = (),
    library: dict[str, Skill] | None = None,
    max_skills: int = 5,
    allow_unverified: bool = False,
) -> list[Skill]:
    """``select`` without the reasons — the shape most callers want."""
    sel = select(track, language, kind, signals=plan_signals(plan), findings=findings,
                 library=library, max_skills=max_skills, allow_unverified=allow_unverified)
    return [s.skill for s in sel]
