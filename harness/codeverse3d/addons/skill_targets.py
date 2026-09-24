"""What each bundle CLAIMS to move — one deterministic quantity per skill.

WHY this file exists.  A library you cannot measure per-skill is one you can only
maintain by taste.  ``metadata.evidence`` says where a bundle's *prose* came from;
nothing said what the bundle is supposed to *do* to a run, in a number a machine can
recompute.  This is that: one row per bundle, naming the quantity, the direction, and
the gate kinds (``skills.registry``) or artefact it is read out of.

Three rules the rows obey:

* **Deterministic only.**  Every row is read from a gate report, a build result or a
  measured artefact — never from the judge.  An 8-prompt A/A of the judged score gave
  paired sd 0.202 and needs ~408 paired prompts to resolve +0.02 (``eval/docs/EVAL.md`` §8);
  a skills A/B read out on ``score`` at n=8 is a coin toss.  These do not carry that
  variance.
* **One owner per quantity.**  ``contract/instance_bbox`` belongs to
  ``c3d-repeats-and-mirrors``, so ``c3d-bbox-contract``'s row excludes it.  Two
  bundles sharing a metric means an A/B cannot say which one moved it.
* **``measurable=False`` is a finding, not a gap.**  A bundle whose claim no
  deterministic instrument can see says so here rather than borrowing a judged
  criterion and calling it evidence.

``eval/bench/skill_targets.py`` is the readout; ``tests/skills/test_targets.py`` pins the
rows against the live gate vocabulary and against each bundle's own frontmatter.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeverse3d.skills.model import Skill

#: the direction that counts as an improvement
DOWN = "down"
UP = "up"
DIRECTIONS = (DOWN, UP)

#: where the readout gets the number
SRC_GATE = "gate_kinds"     #: count findings of `kinds` in the last gated round
SRC_BUILD = "build"         #: the round's BuildResult
SRC_GLB = "glb"             #: recomputed from the measured round's own object.glb (artifacts/rNN/)
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
    #: ``gate/kind`` (or a ``gate/*`` family, see skills.registry) counted when ``source == SRC_GATE``
    kinds: tuple[str, ...] = ()
    #: severities that count; empty == every actionable (non-INFO) finding
    severities: tuple[str, ...] = ()
    #: languages the metric is defined over; empty == every language
    languages: tuple[str, ...] = field(default_factory=tuple)
    #: False when no deterministic instrument can see the bundle's claim
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
        why="the plain count of penetrating pairs is the strongest single predictor of "
            "assembly_fit (r = -0.258, eval/docs/COMPLEXITY.md §6) and the bundle exists to lower it",
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
        why="the bundle is the contract gate's own tolerance arithmetic: a part that IS there "
            "and is the wrong size or in the wrong place",
        caveat="two contract kinds are deliberately somebody else's. contract/instance_* is "
               "c3d-repeats-and-mirrors; contract/missing_part is a part that never reached the "
               "GLB at all, which is a language-API failure (c3d-threejs-forms owns it for "
               "threejs; for blender its upstream signal is lint/part_not_imported, counted by "
               "c3d-blender-forms) rather than a tolerance failure.",
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
        why="metadata.owns names joint_sweep; 653 of 691 sweep findings in the corpus are "
            "moved-pose ERRORs, which is exactly what the clearance rule targets",
    ),
    Target(
        skill="c3d-cadquery-forms",
        metric="build_failure_rate",
        direction=DOWN,
        unit="fraction of rounds whose build failed",
        source=SRC_BUILD,
        languages=("cadquery",),
        why="the bundle's own framing is 'the kernel rules that decide whether the script "
            "builds at all'; an OCC kernel refusal is a build failure, not a gate finding",
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
        why="the bundle's distinct claim is the traps that make a threejs part silently ship "
            "nothing (bevel_depth 0, no computeVertexNormals); the contract gate calls that a missing part",
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
        why="cameras below ground or inside geometry are arithmetic failures, and the bundle's "
            "whole method is deriving them from measured bounds",
        caveat="frame coverage (content_small, hero_*) is the bundle's other half and routes it "
               "too (R15), but is not in this count.",
    ),
    Target(
        skill="c3d-scene-lighting",
        metric="dark_or_flat_frames",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("scene_frames/dark_frame", "scene_frames/flat_frame", "scene_frames/blown_frame"),
        languages=("scene_threejs",),
        why="the bundle is the exposure thresholds of frame_metrics.py restated with a recipe; "
            "the gate counts exactly the frames it failed to light",
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
        caveat="NOT a target. The bundle says so itself: re-measuring all 8 recorded rounds, every "
               "authored camera cleared the 0.4 % bar by 5-12x while animation_life averaged 0.431. "
               "The claim is per-plan-item ('the water and the leaves are static'), and no "
               "deterministic instrument attributes motion to a named plan item. Raising this "
               "number is not evidence the bundle worked; a drop below MOVING_FRAC still is a "
               "regression worth catching.",
    ),
    Target(
        skill="c3d-threejs-shader-traps",
        metric="shader_preflight_findings",
        direction=DOWN,
        unit="WARN+ERROR findings per run",
        source=SRC_ARTIFACT,
        kinds=("shader_preflight/*",),
        languages=("scene_threejs", "threejs"),
        why="every trap the bundle teaches lands "
            "in the one shader_preflight report",
        caveat="zero headroom in today's corpus: all 4 recorded scene runs are clean, so this can "
               "only detect a regression until a battery makes it fire. Runs recorded before "
               "2026-09-22 have shader_preflight only beside the run (artifacts/), their final state; "
               "since then every round's gates carry it (BuildResult.gates).",
    ),
    Target(
        skill="c3d-glsl-craft",
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
        skill="c3d-opengl-pipeline",
        metric="gl_frame_findings",
        direction=DOWN,
        unit="findings per run",
        source=SRC_GATE,
        kinds=("gl_frames/*",),
        languages=("opengl_python",),
        why="every gl_frames finding in the whole graphics corpus (3 flicker, 1 low detail) came "
            "from opengl_python, and flicker is trap 1 (a sampled solver) showing up in pixels",
    ),
    # ------------------------------------------------------------- 2026-09-01 recipe port
    # The four bundles ported from the scene_multifile_graphics reference carry that
    # harness's measured findings; OUR baselines attach when the sceneloop A/B lands.
    # Until then the honest target is the same frame-gate family the recipes serve.
    Target(
        skill="c3d-scene-atmosphere",
        metric="env_bake_orientation_flags",
        direction=DOWN,
        unit="inverted bakes per run (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_threejs",),
        measurable=False,
        why="the sky bake IS the scene's light source; the reference audit found the bake "
            "inverted in 4 of 9 delivered scenes and every reflective surface went dark",
        caveat="ported claim: reference-harness evidence, our own A/B pending (sceneloop).",
    ),
    Target(
        skill="c3d-scene-water",
        metric="metallic_water_planes",
        direction=DOWN,
        unit="water materials with metalness above 0.3 (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_threejs",),
        measurable=False,
        why="dielectric water against a live environment bake is the reference's most-shipped "
            "material fix; no deterministic gate measures water quality yet",
        caveat="NOT yet measurable here: judged quality only. The sceneloop A/B attaches numbers; "
               "a water-specific instrument (fresnel/metalness census) is a candidate follow-up.",
    ),
    Target(
        skill="c3d-scene-night",
        metric="overbright_shaft_findings",
        direction=DOWN,
        unit="solid-reading light shafts per run (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_threejs",),
        measurable=False,
        why="night briefs are the dark_or_flat gate's richest source; the bundle is the "
            "reference's measured shaft/emissive discipline plus our linear-luminance table",
        caveat="ported claim: reference-harness evidence, our own A/B pending (sceneloop).",
    ),
    Target(
        skill="c3d-scene-materials",
        metric="flat_albedo_surfaces",
        direction=DOWN,
        unit="large surfaces with zero value variance (prospective census)",
        source=SRC_ARTIFACT,
        languages=("scene_threejs",),
        measurable=False,
        why="flat albedo and identical twins are the reference audit's most-cited surface "
            "defects; no deterministic gate measures material variance yet",
        caveat="NOT yet measurable here: judged quality only. A variance census (per-material "
               "value spread over sampled texels) is the candidate instrument.",
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
# A claim pins a number some text states to the code constant that produces it: a skill's
# ``_claims/<name>.toml`` (its SKILL.md body, or a bundle file such as ``references/x.md`` via
# ``file``) and the prompt corpus's ``prompts/_claims.toml`` (``file`` relative to prompts/).
# The constant is ``python = "module:NAME"`` or ``js = "<path>:NAME"`` (an ``export const`` numeric
# literal; ``runtime_js/...`` under the configured runtime, any other path under the package).
# The text must stand as its own token (``0.5 m`` is not inside ``10.5 m`` or ``0.5 mm``) and, with
# ``context``, on a line that also carries that phrase — a short "2 mm" cannot pass by occurring
# anywhere else in the file.
CLAIMS_DIR = "_claims"
PROMPT_CLAIMS = "_claims.toml"


def claims_path(name: str, root: Path | None = None) -> Path:
    from codeverse3d.skills import skills_dir

    base = Path(root) if root is not None else skills_dir()
    return base / CLAIMS_DIR / f"{name}.toml"


def _load_rows(p: Path) -> list[dict[str, Any]]:
    if not p.is_file():
        return []
    rows = tomllib.loads(p.read_text()).get("claim") or []
    if not isinstance(rows, list):
        raise ValueError(f"{p}: [[claim]] must be an array of tables")
    return [dict(r) for r in rows]


def load_claims(name: str, root: Path | None = None) -> list[dict[str, Any]]:
    """The claim rows for one skill ([] when the bundle pins nothing)."""
    return _load_rows(claims_path(name, root))


def resolve(dotted: str) -> Any:
    """``module:NAME`` or ``module.NAME`` → the live object."""
    mod, _, attr = dotted.partition(":") if ":" in dotted else dotted.rpartition(".")
    if not mod or not attr:
        raise ValueError(f"claim python target {dotted!r} must be 'module:NAME'")
    return getattr(importlib.import_module(mod), attr)


def resolve_js(target: str) -> float:
    """``<path>:NAME`` → the number ``[export] const NAME = <literal>`` states in that JS file."""
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
    """``(target, live value)`` of a row — its ``python`` or ``js`` constant."""
    if row.get("js"):
        return f"js:{row['js']}", resolve_js(str(row["js"]))
    return str(row["python"]), resolve(str(row["python"]))


def render_claim(row: dict[str, Any]) -> str:
    """The string the live constant produces, per this row's scale/format.  With ``sep`` the
    constant is a sequence: each item is formatted (default ``{:g}``) and joined by ``sep``, the
    last pair by ``last_sep`` when given — so ``(0, 1, 2.5)`` renders ``0, 1 and 2.5`` whole,
    and a fourth item changes the text."""
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
    """Does ``doc`` state ``text`` as its own token (on a line that also says ``context``)?"""
    pat = re.compile(rf"(?<![\w.]){re.escape(text)}(?!\w|\.\d)")
    return any(pat.search(line) and context in line for line in doc.splitlines())


def check_rows(rows: list[dict[str, Any]], read: Any) -> list[str]:
    """Every stale or missing claim among ``rows``; ``read(file)`` returns the text a row's
    ``file`` names (``file`` absent → ``read(None)``)."""
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
        except Exception as e:  # noqa: BLE001 — a bad target is a claim problem
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
    """Every stale or missing claim in one bundle, as human lines (empty == clean)."""
    try:
        rows = load_claims(skill.name, root)
    except (ValueError, OSError) as e:
        return [f"claims file unreadable: {e}"]
    return check_rows(rows, lambda f: skill.body if not f else (skill.dir / f).read_text())


def check_prompt_claims(root: Path | None = None) -> list[str]:
    """The same check over the prompt corpus: ``prompts/_claims.toml``, ``file`` relative to prompts/."""
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1] / "prompts"
    try:
        rows = _load_rows(base / PROMPT_CLAIMS)
    except (ValueError, OSError) as e:
        return [f"prompt claims file unreadable: {e}"]
    return check_rows(rows, lambda f: (base / f).read_text() if f else "")


def claim_bases(name: str, root: Path | None = None) -> dict[str, tuple[str, Any]]:
    """``key -> (dotted target, live value)`` — the agreement test's real comparison.

    WHY not the rendered text: two skills may honestly quote one constant in two units.
    ``c3d-bbox-contract`` says "1 cm" (scale 100) and ``c3d-repeats-and-mirrors`` says
    "0.01" metres; both pin ``conventions:BBOX_TOLERANCE_M`` and both are right.  A
    contradiction is two skills pointing a shared key at DIFFERENT numbers, so that is what
    is compared — the pre-scale value, and the target it came from.
    """
    out: dict[str, tuple[str, Any]] = {}
    for row in load_claims(name, root):
        key = row.get("key")
        if not key or not (row.get("python") or row.get("js")):
            continue
        out[str(key)] = _target(row)
    return out
