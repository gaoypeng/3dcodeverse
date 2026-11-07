"""What each bundle CLAIMS to move — one deterministic quantity per skill."""

from __future__ import annotations

import importlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse3d.skills.model import Skill

DOWN = "down"
UP = "up"

SRC_GATE = "gate_kinds"
SRC_BUILD = "build"
SRC_GLB = "glb"
SRC_FRAMES = "frames"
SRC_ARTIFACT = "artifact"


@dataclass(frozen=True)
class Target:

    skill: str
    metric: str
    direction: str
    unit: str
    source: str
    why: str
    kinds: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()
    languages: tuple[str, ...] = field(default_factory=tuple)
    measurable: bool = True
    caveat: str = ""


OBJECT_LANGS = ("blender", "urdf_blender", "cadquery", "threejs")

TARGETS: tuple[Target, ...] = (
    Target(
        skill="c3d-part-contact",
        metric="interpenetrating_pairs",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("connectivity/penetration",),
        languages=OBJECT_LANGS,
        why="the penetrating-pair count is the strongest single predictor of assembly_fit (r = -0.258, eval/docs/COMPLEXITY.md §6)",
    ),
    Target(
        skill="c3d-bbox-contract",
        metric="contract_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("contract/part_bbox", "contract/overall_bbox", "contract/footprint_offset",
               "contract/ground_gap"),
        languages=OBJECT_LANGS,
        why="the bundle is the contract gate's own tolerance arithmetic: a part that is there at the wrong size or place",
        caveat="contract/instance_* is c3d-repeats-and-mirrors; contract/missing_part is a language-API failure, not a tolerance one.",
    ),
    Target(
        skill="c3d-repeats-and-mirrors",
        metric="contract_instance_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("contract/instance_bbox", "contract/instance_count"),
        languages=OBJECT_LANGS,
        why="metadata.owns already names these two kinds; they are the whole claim",
    ),
    Target(
        skill="c3d-blender-forms",
        metric="blender_lint_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("lint/*",),
        languages=("blender", "urdf_blender"),
        why="the bundle is written around the five bpy traps the static lint already counts",
    ),
    Target(
        skill="c3d-urdf-joints",
        metric="joint_sweep_errors",
        direction=DOWN,
        unit="ERROR findings per run",
        source=SRC_GATE,
        kinds=("joint_sweep/penetration", "joint_sweep/unattached"),
        severities=("error",),
        languages=("urdf_blender",),
        why="653 of 691 joint_sweep findings in the corpus are moved-pose ERRORs, which the clearance rule targets",
    ),
    Target(
        skill="c3d-cadquery-forms",
        metric="build_failure_rate",
        direction=DOWN,
        unit="fraction of rounds whose build failed",
        source=SRC_BUILD,
        languages=("cadquery",),
        why="an OCC kernel refusal is a build failure, not a gate finding",
        caveat="no baseline exists: eval/bench/out holds zero graded cadquery runs.",
    ),
    Target(
        skill="c3d-threejs-forms",
        metric="missing_parts",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("contract/missing_part",),
        languages=("threejs",),
        why="the traps that make a threejs part silently ship nothing count as a missing part",
        caveat="no baseline exists: eval/bench/out holds zero graded threejs runs.",
    ),
    Target(
        skill="c3d-scene-composition",
        metric="camera_placement_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("scene_frames/camera_*",),
        languages=("scene_threejs",),
        why="cameras below ground or inside geometry are arithmetic failures the bundle derives from measured bounds",
        caveat="frame coverage (content_small, hero_*) also routes the bundle (R15) but is not in this count.",
    ),
    Target(
        skill="c3d-scene-lighting",
        metric="dark_or_flat_frames",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("scene_frames/dark_frame", "scene_frames/flat_frame", "scene_frames/blown_frame"),
        languages=("scene_threejs",),
        why="the bundle is the exposure thresholds of frame_metrics.py with a recipe",
    ),
    Target(
        skill="c3d-scene-motion",
        metric="min_authored_changed_frac",
        direction=UP,
        unit="fraction of pixels changed on the WEAKEST authored camera, t=0 → t=1.5 s",
        source=SRC_FRAMES,
        languages=("scene_threejs",),
        measurable=False,
        why="reported as a regression guard only",
        caveat="NOT a target: no deterministic instrument attributes motion to a named plan item; a drop below MOVING_FRAC is still a regression.",
    ),
    Target(
        skill="c3d-threejs-shader-traps",
        metric="shader_preflight_findings",
        direction=DOWN,
        unit="WARN+ERROR findings per run",
        source=SRC_ARTIFACT,
        kinds=("shader_preflight/*",),
        languages=("scene_threejs", "threejs"),
        why="every trap the bundle teaches lands in the one shader_preflight report",
        caveat="zero headroom in today's corpus (all 4 recorded scene runs are clean): detects a regression only.",
    ),
    Target(
        skill="c3d-glsl-craft",
        metric="mean_edge_density",
        direction=UP,
        unit="mean edge density over the 5 sampled frames (frame_stats)",
        source=SRC_FRAMES,
        languages=("glsl_shader",),
        why="the gl_frames gate fired zero findings on 9 graded glsl runs; edge density is the continuous quantity it thresholds",
    ),
    Target(
        skill="c3d-opengl-pipeline",
        metric="gl_frame_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("gl_frames/*",),
        languages=("opengl_python",),
        why="every gl_frames finding in the graphics corpus came from opengl_python",
    ),
    # ------------------------------------------------------------- recipe port
    Target(
        skill="c3d-scene-atmosphere",
        metric="env_bake_orientation_flags",
        direction=DOWN,
        unit="inverted bakes per run (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_threejs",),
        measurable=False,
        why="the reference audit found the sky bake inverted in 4 of 9 delivered scenes",
        caveat="ported claim: reference-harness evidence, our own A/B pending.",
    ),
    Target(
        skill="c3d-scene-water",
        metric="metallic_water_planes",
        direction=DOWN,
        unit="water materials with metalness above 0.3 (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_threejs",),
        measurable=False,
        why="dielectric water against a live environment bake is the reference's most-shipped material fix",
        caveat="NOT yet measurable here: judged quality only; a fresnel/metalness census is the candidate instrument.",
    ),
    Target(
        skill="c3d-scene-night",
        metric="overbright_shaft_findings",
        direction=DOWN,
        unit="solid-reading light shafts per run (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_threejs",),
        measurable=False,
        why="night briefs are the dark_or_flat gate's richest source",
        caveat="ported claim: reference-harness evidence, our own A/B pending.",
    ),
    Target(
        skill="c3d-scene-materials",
        metric="flat_albedo_surfaces",
        direction=DOWN,
        unit="large surfaces with zero value variance (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_threejs",),
        measurable=False,
        why="flat albedo and identical twins are the reference audit's most-cited surface defects",
        caveat="NOT yet measurable here: judged quality only; a per-material value-spread census is the candidate instrument.",
    ),
    # ------------------------------------------------------------- scene_blender
    Target(
        skill="c3d-bpy-scene-placement",
        metric="bpy_placement_errors",
        direction=DOWN,
        unit="ERROR findings per run",
        source=SRC_GATE,
        kinds=("scene_placement/floating", "scene_placement/sunken", "scene_placement/unsupported",
               "scene_placement/interpenetration"),
        severities=("error",),
        languages=("scene_blender",),
        why="the bundle seats every placed row on height_at or its support; the floating+sunken+interpenetration ERROR count is the number to move",
        caveat="unsupported, cantilevered and free_exempt route the bundle (R30) but are WARN-only or per-report, so they add nothing to the count.",
    ),
    Target(
        skill="c3d-bpy-scene-lighting",
        metric="bpy_exposure_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("scene_frames/dark_frame", "scene_frames/flat_frame", "scene_frames/blown_frame"),
        languages=("scene_blender",),
        why="the bundle is the exposure thresholds of frame_metrics.py with the Cycles/AgX bands",
        caveat="the same three kinds as c3d-scene-lighting over a disjoint language; the uniqueness test keys on (kind, language).",
    ),
    Target(
        skill="c3d-bpy-scene-materials",
        metric="flat_principled_materials",
        direction=DOWN,
        unit="materials whose Base Color, Roughness and Metallic are all unlinked constants (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_blender",),
        measurable=False,
        why="placeholder_material, flat_ground and monotonous_vegetation are judge defects with no deterministic twin",
        caveat="NOT yet measurable here: judged quality only; a census of linked Principled sockets is the candidate instrument.",
    ),
)

BY_SKILL: dict[str, Target] = {t.skill: t for t in TARGETS}


def target_for(skill: str) -> Target | None:
    return BY_SKILL.get(skill)


# ===================================================================== claims
# A claim pins a number some text states to the code constant that produces it (``_claims/<name>.toml``,
# ``prompts/_claims.toml``); the text must stand as its own token and, with ``context``, share a line with that phrase.
CLAIMS_DIR = "_claims"
PROMPT_CLAIMS = "_claims.toml"


def claims_path(skill: Skill | str, root: Path | None = None) -> Path:
    from codeverse3d.skills import skills_dir

    name = skill.name if isinstance(skill, Skill) else skill
    base = Path(root) if root is not None else skills_dir()
    if isinstance(skill, Skill):
        own = skill.dir.parent / CLAIMS_DIR / f"{name}.toml"
        if own.is_file():
            return own
    return base / CLAIMS_DIR / f"{name}.toml"


def _load_rows(p: Path) -> list[dict[str, Any]]:
    if not p.is_file():
        return []
    rows = tomllib.loads(p.read_text()).get("claim") or []
    if not isinstance(rows, list):
        raise ValueError(f"{p}: [[claim]] must be an array of tables")
    return [dict(r) for r in rows]


def load_claims(skill: Skill | str, root: Path | None = None) -> list[dict[str, Any]]:
    return _load_rows(claims_path(skill, root))


def resolve(dotted: str) -> Any:
    mod, _, attr = dotted.partition(":") if ":" in dotted else dotted.rpartition(".")
    if not mod or not attr:
        raise ValueError(f"claim python target {dotted!r} must be 'module:NAME'")
    return getattr(importlib.import_module(mod), attr)


def resolve_js(target: str) -> float:
    rel, _, name = target.rpartition(":")
    if not rel or not name:
        raise ValueError(f"claim js target {target!r} must be '<path>:NAME'")
    head, _, tail = rel.partition("/")
    if head == "runtime_js":
        from codeverse3d.spatial.node import runtime_js_dir

        path = runtime_js_dir() / tail
    else:
        path = Path(__file__).resolve().parents[1] / rel
    m = re.search(rf"^\s*(?:export\s+)?const\s+{re.escape(name)}\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*[;,]",
                  path.read_text(), re.M)
    if m is None:
        raise ValueError(f"{path} states no numeric `const {name} = …`")
    return float(m.group(1))


def _target(row: dict[str, Any]) -> tuple[str, Any]:
    if row.get("js"):
        return f"js:{row['js']}", resolve_js(str(row["js"]))
    return str(row["python"]), resolve(str(row["python"]))


def render_claim(row: dict[str, Any]) -> str:
    value = _target(row)[1]
    scale = row.get("scale")
    fmt = row.get("format")
    if row.get("sep"):
        items = [str(fmt or "{:g}").format(v * scale if scale is not None else v) for v in value]
        head, last = items[:-1], items[-1:]
        return str(row["sep"]).join(head) + (str(row.get("last_sep") or row["sep"]) if head else "") + "".join(last)
    if scale is not None:
        value = value * scale
    return format(value, "") if not fmt else str(fmt).format(value)


def stated(text: str, doc: str, context: str = "") -> bool:
    pat = re.compile(rf"(?<![\w.]){re.escape(text)}(?!\w|\.\d)")
    return any(pat.search(line) and context in line for line in doc.splitlines())


def check_rows(rows: list[dict[str, Any]], read: Any) -> list[str]:
    issues: list[str] = []
    for i, row in enumerate(rows):
        where = f"claim[{i}] {row.get('key', '?')}"
        missing = [k for k in ("key", "text") if not row.get(k)]
        if not row.get("python") and not row.get("js"):
            missing.append("python` or `js")
        if missing:
            issues += [f"{where}: missing `{k}`" for k in missing]
            continue
        try:
            live = render_claim(row)
        except Exception as e:  # noqa: BLE001
            issues.append(f"{where}: cannot resolve {row.get('js') or row.get('python')!r}: {e}")
            continue
        try:
            doc = read(row.get("file"))
        except OSError as e:
            issues.append(f"{where}: cannot read {row.get('file')!r}: {e}")
            continue
        if live != row["text"]:
            issues.append(f"{where}: the constant now renders {live!r}, the text says {row['text']!r}")
        elif not stated(row["text"], doc, str(row.get("context", ""))):
            ctx = f" on a line with {row['context']!r}" if row.get("context") else ""
            issues.append(f"{where}: {row['text']!r} no longer appears{ctx} in {row.get('file') or 'the body'}")
    return issues


def check_claims(skill: Skill, root: Path | None = None) -> list[str]:
    try:
        rows = load_claims(skill, root)
    except (ValueError, OSError) as e:
        return [f"claims file unreadable: {e}"]
    return check_rows(rows, lambda f: skill.body if not f else (skill.dir / f).read_text())


def check_prompt_claims(root: Path | None = None) -> list[str]:
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1] / "prompts"
    try:
        rows = _load_rows(base / PROMPT_CLAIMS)
    except (ValueError, OSError) as e:
        return [f"prompt claims file unreadable: {e}"]
    return check_rows(rows, lambda f: (base / f).read_text() if f else "")
