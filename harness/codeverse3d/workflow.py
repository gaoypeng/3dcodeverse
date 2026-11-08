"""The workflow tools: ``init`` · ``validate_plan`` · ``check`` · ``judge`` · ``judge_pack`` · ``score``.

They tie the instruments to the workspace the host agent works in: ``init`` writes ``spec.json`` and what the
language runtime owns, ``validate_plan`` validates and normalises ``plan.json``, ``check`` builds and runs every
gate of the track, ``judge_pack`` renders the judged views and writes ``judge/rNN/brief.md`` + images for a
fresh-context judge, ``judge`` is the one step from code to that judge (check if stale → commit + tag ``rNN`` →
judge-pack → the instruction to hand the judge), ``score`` turns the judge's ``judgment.json`` into the rubric
arithmetic, mending trivial deviations first.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from codeverse3d.config import get_settings
from codeverse3d.contracts.artifacts import GateReport, Judgment, RenderSet, Severity
from codeverse3d.contracts.common import ENTRY_FILE, TRACK_LANGUAGES, TRACK_RUBRIC, Language, Track
from codeverse3d.contracts.plan import ScenePlan
from codeverse3d.contracts.spec import Constraints, ReferenceImage, Spec
from codeverse3d.conventions import LANGUAGE_FRAME, frame_doc, to_snake
from codeverse3d.gates import PLAN_MODELS, CheckResult, render_for_judge, run_check
from codeverse3d.jsonparse import JsonParseError, parse_json_lenient
from codeverse3d.judges import prompt_builder as pb
from codeverse3d.judges.rubrics import (
    JudgeParseError,
    aggregate_samples,
    load_rubric,
    parse_judge_output,
    repair_judgment,
)
from codeverse3d.languages import get_runtime
from codeverse3d.plan_checks import (
    asset_kind_errors,
    fixed_part_clash,
    missing_link_names,
    normalise_plan,
    plan_budget,
    plan_quality_complaints,
    schema_echo,
    validation_errors,
)
from codeverse3d.proc import read_json_or_none, run_subprocess
from codeverse3d.spatial.observe import rel_path, text_observation
from codeverse3d.spatial.registry import NoArgs, Observation, ToolContext, ToolUsageError, tool
from codeverse3d.spatial.silhouette import compare_silhouette, dimension_conflict, measure_reference
from codeverse3d.spatial.tool_common import spec_dict
from codeverse3d.workspace import Workspace

log = logging.getLogger(__name__)


def _spec(ctx: ToolContext) -> Spec:
    raw = spec_dict(ctx)
    if not raw:
        raise ToolUsageError("no spec.json in the workspace — run `3dcode init` first",
                             "3dcode init --track static_object --language blender --prompt '...'")
    try:
        return Spec.model_validate(raw)
    except ValidationError as e:
        raise ToolUsageError(f"spec.json is not a valid spec: {e}") from e


def _plan_raw(ws: Workspace) -> dict[str, Any]:
    if not ws.plan_path.is_file():
        raise ToolUsageError("plan.json not found in the workspace — write it first (see the 3dcodeverse skill)")
    try:
        data = json.loads(ws.plan_path.read_text())
    except ValueError as e:
        raise ToolUsageError(f"plan.json is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ToolUsageError(f"plan.json must hold a JSON object, got {type(data).__name__}")
    return data


def _plan_or_none(ws: Workspace, track: Track) -> BaseModel | None:
    """The validated plan, or ``None`` when there is none / it does not validate (the tools say which)."""
    if not ws.plan_path.is_file():
        return None
    try:
        return PLAN_MODELS[track].model_validate(json.loads(ws.plan_path.read_text()))
    except (ValueError, ValidationError):
        return None


# ===================================================================== init
class InitArgs(BaseModel):
    track: str = Field(description=f"one of {[t.value for t in Track]}")
    language: str = Field(description="one of " + ", ".join(f"{t.value}: {[la.value for la in ls]}" for t, ls in TRACK_LANGUAGES.items()))
    prompt: str = Field(description="what to build, the user's words")
    must_have: list[str] = Field(default=[], description="hard requirements (repeat the flag per item)")
    must_not: list[str] = Field(default=[], description="things that must not appear (repeat per item)")
    dims: dict[str, float] = Field(default={}, description="stated dimensions in metres, e.g. height=0.45 (repeat per key)")
    references: list[str] = Field(default=[], description="reference image paths (the first is the silhouette target)")
    style: str = Field(default="", description="style words the plan and the judge read")
    force: bool = Field(default=False, description="overwrite an existing spec.json")


@tool("init", InitArgs, "Create the project: write spec.json and the workspace layout, plus what the language runtime "
      "owns (e.g. scene_threejs src/lib/). Prints the files the language expects and the skill to read.")
def init(ctx: ToolContext, args: InitArgs) -> Observation:
    ws = ctx.workspace
    if ws.spec_path.is_file() and not args.force:
        raise ToolUsageError(f"{ws.spec_path} exists — pass --force to overwrite it", "3dcode init ... --force")
    try:
        track, language = Track(args.track), Language(args.language)
    except ValueError as e:
        raise ToolUsageError(str(e), "3dcode init --track static_object --language blender --prompt '...'") from e
    refs = []
    for i, r in enumerate(args.references):
        p = Path(r).expanduser()
        if not p.is_file():
            raise ToolUsageError(f"reference image not found: {r}")
        refs.append(ReferenceImage(path=str(p.resolve()), role="target" if i == 0 else "detail"))
    try:
        spec = Spec(id=ws.root.name, track=track, language=language, prompt=args.prompt.strip(), references=refs,
                    constraints=Constraints(dimensions_m=args.dims or None, style=args.style, must_have=args.must_have,
                                            must_not=args.must_not))
    except ValidationError as e:
        raise ToolUsageError(f"invalid spec: {e}") from e
    ws.ensure_layout()
    ws.write_json(ws.spec_path, spec)
    owned = _language_owned_files(ws, language)
    rt = get_runtime(language)
    lines = [f"INIT OK: {ws.root} — track {track.value}, language {language.value}",
             f"spec.json written: {len(args.must_have)} must-have, {len(args.must_not)} must-not, "
             f"{len(args.dims)} dimension(s), {len(refs)} reference image(s)",
             f"entry file: {ENTRY_FILE[language]}; the agent's files: {', '.join(rt.entry_globs)}",
             f"frame: {frame_doc(LANGUAGE_FRAME[language.value])}"]
    if owned:
        lines.append("runtime-owned files written (import them; never edit, never read whole): " + ", ".join(rel_path(p, ws.root) for p in owned[:12])
                     + (f" … +{len(owned) - 12}" if len(owned) > 12 else ""))
    lib = getattr(rt, "lib_interface", None)
    if callable(lib) and (surface := lib(ws)):
        lines.append("library surface (src/lib) — every export with its parameters; this index replaces reading the files:\n" + surface)
    lines.append(f"next: read the skill 3d-lang-{language.value.replace("_", "-")}, write plan.json, run `3dcode validate-plan`")
    return text_observation(lines, numbers={"track": track.value, "language": language.value, "workspace": str(ws.root),
                                            "entry": ENTRY_FILE[language], "owned_files": [rel_path(p, ws.root) for p in owned]},
                            limit=6000)


def _language_owned_files(ws: Workspace, language: Language) -> list[Path]:
    """What the runtime, not the agent, puts in the workspace."""
    written: list[Path] = []
    if language is Language.SCENE_THREEJS:
        from codeverse3d.languages.scene_threejs import write_lib

        written += write_lib(ws)
        (ws.public / "assets").mkdir(parents=True, exist_ok=True)
    elif language is Language.SCENE_BLENDER:
        (ws.public / "assets").mkdir(parents=True, exist_ok=True)
    elif language is Language.THREEJS:
        from codeverse3d.languages.threejs import PACKAGE_JSON

        pj = ws.src / "package.json"
        if not pj.is_file():
            pj.write_text(PACKAGE_JSON)
            written.append(pj)
    return written


# ===================================================================== validate_plan
@tool("validate_plan", NoArgs, "Validate plan.json against the track's plan model and the deterministic plan checks "
      "(placeholder echo, size/detail budget from the request, fixed-part clash); writes the normalised plan back. "
      "FAIL lists each error with its JSON path.")
def validate_plan(ctx: ToolContext, args: NoArgs) -> Observation:
    ws = ctx.workspace
    spec = _spec(ctx)
    raw = _plan_raw(ws)
    model = PLAN_MODELS[spec.track]
    errors: list[str] = []
    echoed = schema_echo(raw)
    if echoed:
        errors.append(f"the plan echoes schema placeholders instead of describing the object: {echoed} — "
                      "write real names, real numbers")
    errors += asset_kind_errors(raw, spec.language)
    plan: BaseModel | None = None
    try:
        plan = model.model_validate(raw)
    except ValidationError as e:
        errors += validation_errors(e)
        missing = missing_link_names(raw)
        if missing:
            errors.append(f"joints reference links that `parts` does not define: {', '.join(missing[:10])} — every link a "
                          "joint names must be a top-level part with its own bbox")
    if plan is not None:
        clash = fixed_part_clash(plan, spec)
        if clash:
            errors.append(clash)
    if errors:
        text = [f"PLAN INVALID ({model.__name__}): {len(errors)} error(s)"] + [f"- {e}" for e in errors]
        return text_observation(text, ok=False, numbers={"errors": len(errors), "model": model.__name__}, limit=6000)
    assert plan is not None
    budget = plan_budget(spec)
    unit = "passes" if spec.track is Track.GRAPHICS else "zones" if spec.track is Track.SCENE else "parts"
    hard, soft = plan_quality_complaints(plan, budget, unit=unit) if unit != "zones" else ([], [])
    notes = normalise_plan(plan, spec)
    before = ws.plan_path.read_bytes() if ws.plan_path.is_file() else b""
    ws.write_json(ws.plan_path, plan)
    rewritten = ws.plan_path.read_bytes() != before
    if spec.language is Language.SCENE_BLENDER and isinstance(plan, ScenePlan):
        # src/scene.py is data the build reads (ZONES / ASSETS / HEROES / CAMERAS), so it follows the plan
        from codeverse3d.languages.scene_blender import render_scene_py

        (ws.src / "scene.py").write_text(render_scene_py(
            [to_snake(z.name) for z in plan.zones],
            [to_snake(a.name) for a in plan.assets if a.kind != "blender_glb"],
            [to_snake(a.name) for a in plan.assets if a.kind == "blender_glb"],
            list(plan.cameras[:6]), title=plan.title, summary=plan.summary))
        notes.append("src/scene.py written from the plan")
    rt = get_runtime(spec.language)
    files = rt.expected_files(plan)
    units = list(getattr(plan, unit, None) or [])
    head = "PLAN FAIL (valid, but not worth building yet)" if hard else "PLAN OK"
    lines = [f"{head}: {model.__name__}, {len(units)} {unit}, {len(plan.acceptance)} acceptance items"]
    lines += [f"- ERROR: {h}" for h in hard]
    lines += [f"- WARN: {s}" for s in soft]
    if notes:
        lines.append("normalised and written back: " + "; ".join(notes))
    if rewritten:
        lines.append("plan.json rewritten (normalised) — re-read before editing it")
    lines.append(f"files to write ({spec.language.value}): " + ", ".join(files[:16]) + (" …" if len(files) > 16 else ""))
    lines.append("acceptance: " + "; ".join(f"{a.id} [{a.priority}/{a.how}] {a.text}"[:160] for a in plan.acceptance[:12]))
    return text_observation(lines, ok=not hard,
                            numbers={"model": model.__name__, "n_units": len(units), "files": files,
                                     "acceptance_ids": [a.id for a in plan.acceptance], "hard": len(hard), "soft": len(soft),
                                     "rewritten": rewritten, **budget.as_dict()}, limit=6000)


# ===================================================================== check
def _gate_lines(gates: list[GateReport], *, max_errors: int = 20, max_warns: int = 10) -> list[str]:
    lines = []
    status = ", ".join(f"{g.gate}={'pass' if g.passed else 'FAIL'}" for g in gates)
    lines.append(f"gates: {status}")
    errs = [(g, f) for g in gates for f in g.findings if f.severity == Severity.ERROR]
    warns = [(g, f) for g in gates for f in g.findings if f.severity == Severity.WARN]
    for g, f in errs[:max_errors]:
        lines.append(f"- ERROR {g.gate}" + (f" [{f.target}]" if f.target else "") + f": {f.message}")
        if f.fix_hint:
            lines.append(f"    fix: {f.fix_hint}")
    if len(errs) > max_errors:
        lines.append(f"- … {len(errs) - max_errors} more error(s) (artifacts/gates.json)")
    for g, f in warns[:max_warns]:
        lines.append(f"- WARN {g.gate}" + (f" [{f.target}]" if f.target else "") + f": {f.message}"
                     + (f"  fix: {f.fix_hint}" if f.fix_hint else ""))
    if len(warns) > max_warns:
        lines.append(f"- … {len(warns) - max_warns} more warning(s) (artifacts/gates.json)")
    return lines


@tool("check", NoArgs, "Lint + build + measure + EVERY gate of the track (connectivity, contract, detail budget, fixed "
      "parts, project checks; joint sweep + motion direction; scene placement + asset modules; frame gate), written to "
      "artifacts/gates.json. PASS/FAIL with each ERROR and its fix. Run it after every edit; judge-pack needs it.",
      cost_hint="slow")
def check(ctx: ToolContext, args: NoArgs) -> Observation:
    from codeverse3d.spatial.measure import measure_summary_table
    from codeverse3d.spatial.observe import build_failure_lines, sanitize_text

    ws = ctx.workspace
    spec = _spec(ctx)
    plan = _plan_or_none(ws, spec.track)
    res = run_check(ws, spec, plan, round_index=ctx.round_index)
    numbers: dict[str, Any] = {"ok": res.ok, "build_ok": res.build.ok, "duration_ms": res.build.duration_ms,
                               "errors": sum(len(g.errors) for g in res.gates),
                               "warnings": sum(1 for g in res.gates for f in g.findings if f.severity == Severity.WARN),
                               "gates": {g.gate: g.passed for g in res.gates}, "src_hash": res.src_hash}
    if not res.build.ok:
        lint = next((g for g in res.gates if g.gate.startswith("lint")), None)
        if res.build.error_type == "LintError" and lint is not None:
            lines = ["CHECK FAIL — lint errors (nothing was built):"]
            lines += [f"- {f.as_line(with_target=True)}" for f in lint.errors]
        else:
            lines = ["CHECK FAIL — " + build_failure_lines(res.build, ws.root, [], tail_n=25)[0]]
            lines += build_failure_lines(res.build, ws.root, [], tail_n=25)[1:]
            lines += _gate_lines([g for g in res.gates if not g.gate.startswith("lint")])
        return text_observation(lines, ok=False, numbers=numbers, limit=6000)
    lines = [f"CHECK {'PASS' if res.ok else 'FAIL'} — build ok ({res.build.duration_ms} ms)"
             + (f" → {rel_path(res.build.glb_path, ws.root)}" if res.build.glb_path else "")]
    if plan is None and ws.plan_path.is_file():
        lines.append("WARN: plan.json does not validate — the contract / budget gates did not run (3dcode validate-plan)")
    elif plan is None:
        lines.append("WARN: no plan.json — the contract / budget gates did not run")
    lines += _gate_lines(res.gates)
    if res.measurement is not None:
        lines.append(measure_summary_table(res.measurement))
        numbers.update({"extents_m": list(res.measurement.extents), "tri_count": res.measurement.tri_count,
                        "n_parts": len(res.measurement.parts), "ground_gap_m": res.measurement.ground_gap_m})
    census_warn = res.build.census.get("warnings") if isinstance(res.build.census, dict) else None
    if census_warn:
        lines.append("build warnings:\n" + "\n".join(f"- {sanitize_text(str(w), ws.root)}" for w in list(census_warn)[:10]))
    lines.append(f"written: {rel_path(ws.gates_path, ws.root)}")
    return text_observation(lines, ok=res.ok, numbers=numbers, limit=8000)


# ===================================================================== judge_pack
#: per-track montage ceilings under the ``Settings.judge.montages`` dial
TRACK_MONTAGE_CAP: dict[str, int] = {"scene": 4, "graphics": 3}
SLICE_LABELS = {
    "front_back": "cross-section front-back · interior view",
    "left_right": "cross-section left-right · interior view",
}
#: IoU → measured silhouette score: ≤ 0.25 → 0, ≥ 0.85 → 1, linear between
IOU_LOW, IOU_HIGH = 0.25, 0.85
#: score the measured criterion gets when the measurement cannot be trusted
NEUTRAL_SCORE = 0.5
LIKENESS_NOTE = (
    "REAL-WORLD REFERENCE PHOTOS are attached (labelled REAL-WORLD REFERENCE n/N).  They are not a "
    "composition to copy; they are what the thing the brief names ACTUALLY looks like.  Score "
    "brief fidelity, colour/light and richness against them: dominant colour and where the "
    "secondary colours sit, how the structure folds / layers / thins out, where the brightness "
    "concentrates and how much of the frame stays dark, what the real texture is at fine scale.  A "
    "frame that ticks every noun in the brief but would never be mistaken for the photographed "
    "thing (a row of evenly spaced bars standing in for curtains, a flat band standing in for a "
    "gradient of light, cartoon-saturated colour where the photo is subtle) is a 0.4-0.5 on brief "
    "fidelity, not a 1.0.  Foreground, framing and landscape in the photos are incidental unless "
    "the brief asks for them."
)
PACK_NAME = "pack.json"
JUDGMENT_NAME = "judgment.json"
SCORE_NAME = "score.json"


def iou_to_score(iou: float) -> float:
    return max(0.0, min(1.0, (float(iou) - IOU_LOW) / (IOU_HIGH - IOU_LOW)))


def rubric_for(spec: Spec) -> str:
    """The rubric a project is judged on: ``reference`` for a static object with reference images, else the track's."""
    if spec.references and spec.track is Track.STATIC_OBJECT:
        return pb.REFERENCE_RUBRIC
    return TRACK_RUBRIC[spec.track]


def _load_check(ws: Workspace) -> CheckResult:
    raw = read_json_or_none(ws.gates_path)
    if raw is None:
        raise ToolUsageError("no artifacts/gates.json — run `3dcode check` first", "3dcode check")
    res = CheckResult.model_validate(raw)
    if res.src_hash != ws.src_hash():
        raise ToolUsageError("src/ changed since the last `3dcode check` — run it again so the judge sees the code that built",
                             "3dcode check")
    if not res.build.ok:
        raise ToolUsageError("the last check did not build — nothing to judge; fix the build first", "3dcode check")
    return res


def _reference_material(spec: Spec, rubric, renders: RenderSet) -> tuple[list[tuple[str, Path]], str, dict[str, float]]:
    """Reference photos, the measured-silhouette block and the measured scores (reference rubric), or the likeness
    photos + note (scene / graphics with references)."""
    refs = [r for r in spec.references if Path(r.path).is_file()]
    if not refs:
        return [], "", {}
    measured_ids = [c.id for c in rubric.measured_criteria()]
    if spec.track in (Track.SCENE, Track.GRAPHICS):
        images = [(f"REAL-WORLD REFERENCE {i}/{min(len(refs), 3)} ({r.role})" + (f" — {r.note}" if r.note else ""), Path(r.path))
                  for i, r in enumerate(refs[:3], 1)]
        return images, LIKENESS_NOTE, {}
    images = [(f"REFERENCE {i}/{min(len(refs), 3)} ({r.role})" + (f" — {r.note}" if r.note else ""), Path(r.path))
              for i, r in enumerate(refs[:3], 1)]
    blocks: list[str] = []
    targets = [r for r in refs if r.role == "target"] or refs
    conflict = dimension_conflict(spec, targets[0].path)
    if conflict.get("conflict"):
        lo, hi = conflict.get("expected_range") or (0.0, 0.0)
        blocks.append(f"REFERENCE PROPORTIONS DISAGREE WITH THE BRIEF: the picture's outline is {conflict['image_aspect']:.2f} "
                      f"wide/high, outside the {lo:.2f}-{hi:.2f} band the stated dimensions allow. The BRIEF's dimensions are "
                      "correct; use the reference for the part inventory and the shape of each part, NOT for the overall "
                      "proportions. The measured silhouette score is therefore neutral.")
    scores: dict[str, float] = {}
    if measured_ids:
        match = measure_reference(renders.views, spec, compare=compare_silhouette, conflict=conflict)
        if match is None:
            score, why = NEUTRAL_SCORE, " (NEUTRAL: no render view could be compared)"
            blocks.append(f"MEASURED SILHOUETTE: unavailable → silhouette_match score {score:.2f}{why}.")
        else:
            if match.conflict:
                score, why = NEUTRAL_SCORE, " (NEUTRAL: the reference's proportions contradict the brief's dimensions)"
            elif not match.reliable:
                score, why = NEUTRAL_SCORE, " (NEUTRAL: the background mask was unreliable)"
            else:
                score, why = iou_to_score(match.iou), ""
            extra = match.model_dump(exclude={"iou", "reference", "conflict", "per_view"}, exclude_none=True)
            blocks.append(f"MEASURED SILHOUETTE: {match.view} render vs reference {Path(match.reference).name}: "
                          f"IoU {match.iou:.3f} → silhouette_match score {score:.2f}{why}. extra: {extra}")
        scores = {cid: score for cid in measured_ids}
    return images, "\n\n".join(blocks), scores


def _slices(spec: Spec, check: CheckResult, gates: list[GateReport], out_dir: Path) -> list[tuple[str, Path]]:
    """The slice channel: up to two interior cross-sections when the connectivity gate carries an ERROR."""
    if get_settings().judge.slices != "on-error" or spec.track.value not in pb.SLICE_TRACKS or not check.build.glb_path:
        return []
    if not any(g.gate == "connectivity" and g.errors for g in gates):
        return []
    try:
        from codeverse3d.spatial.sections import judge_slices

        manifest = judge_slices(Path(check.build.glb_path), pb.connectivity_error_pairs(gates), out_dir)
    except Exception as e:  # noqa: BLE001 — the slice channel is optional (the 'mesh' extra)
        log.warning("judge slices unavailable: %s", e)
        return []
    return [(SLICE_LABELS.get(s.name, s.name), out_dir / s.png) for s in manifest.slices if s.rendered]


def _previous_verdict(ws: Workspace, round_index: int) -> Judgment | None:
    for r in range(round_index - 1, -1, -1):
        raw = read_json_or_none(ws.judge_dir(r) / SCORE_NAME)
        if raw is not None:
            try:
                return Judgment.model_validate(raw)
            except ValidationError:
                return None
    return None


def _slug(label: str) -> str:
    keep = "".join(c if c.isalnum() else "_" for c in label.split("—")[0].split("(")[0].strip().lower())
    return keep.strip("_")[:40] or "image"


@tool("judge_pack", NoArgs, "Render the track's judged views (14-view rig + clay + poses / authored cameras + rig / "
      "judged frames), compose the montages and crops, and write judge/rNN/: brief.md (the case, the rubric, the image "
      "list, the JSON shape to write), the images and pack.json. A fresh-context judge reads ONLY that folder. "
      "Requires a `check` of the current src/; `3dcode judge` runs it for you.", cost_hint="slow")
def judge_pack(ctx: ToolContext, args: NoArgs) -> Observation:
    ws = ctx.workspace
    spec = _spec(ctx)
    plan = _plan_or_none(ws, spec.track)
    check = _load_check(ws)
    settings = get_settings()
    rubric_name = rubric_for(spec)
    rubric = load_rubric(rubric_name)
    out = ws.judge_dir(ctx.round_index)
    if out.exists():
        for p in out.iterdir():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.name != JUDGMENT_NAME:
                p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    jr = render_for_judge(ws, spec, plan, check, round_index=ctx.round_index)
    if not jr.renders.views:
        errors = jr.renders.console_errors[:5]
        return text_observation(["JUDGE PACK FAILED: the render produced no views"] + [f"  ! {e[:300]}" for e in errors],
                                ok=False, failed=not errors, numbers={"console_errors": len(errors)})
    gates = list(check.gates) + list(jr.post_gates or [])
    is_scene = spec.track is Track.SCENE
    cap = TRACK_MONTAGE_CAP.get(spec.track.value, settings.judge.montages)
    images, montages = pb.montage_images(
        jr.renders, geometry_views=jr.geometry, scene=is_scene, max_montages=min(settings.judge.montages, cap),
        detail_crops=settings.judge.detail_crops, max_px=settings.judge.max_px, gates=gates if is_scene else ())
    ref_images, ref_text, measured = _reference_material(spec, rubric, jr.renders)
    slices = _slices(spec, check, gates, out / "slices")
    # the pack is self-contained: every image the brief names lives in judge/rNN/
    def _copy(items: list[tuple[str, Path]], prefix: str) -> list[tuple[str, Path]]:
        copied = []
        for i, (lbl, src) in enumerate(items, 1):
            dst = out / f"{prefix}_{i:02d}_{_slug(lbl)}.png"
            shutil.copyfile(src, dst)
            copied.append((lbl, dst))
        return copied

    ref_images, images, slices = _copy(ref_images, "ref"), _copy(images, "img"), _copy(slices, "slice")
    judgment_path = out / JUDGMENT_NAME
    brief = pb.build_brief(spec=spec, plan=plan, rubric=rubric, renders=jr.renders, measurement=check.measurement,
                           gates=gates, round_index=ctx.round_index, previous=_previous_verdict(ws, ctx.round_index),
                           extra_context=jr.extra_context, extra_text=ref_text, geometry_views=jr.geometry,
                           images=images, montages=montages, extra_images=ref_images, slice_images=slices,
                           judgment_path=judgment_path)
    (out / "brief.md").write_text(brief)
    acceptance_ids = [a.id for a in (getattr(plan, "acceptance", None) or [])]
    pack = {"round": ctx.round_index, "rubric": rubric_name, "src_hash": check.src_hash,
            "renders": jr.renders.model_dump(mode="json"), "gates": [g.model_dump(mode="json") for g in gates],
            "acceptance_ids": acceptance_ids, "measured_scores": measured,
            "images": [{"label": lbl, "path": str(p)} for lbl, p in [*ref_images, *images, *slices]],
            "judgment_path": str(judgment_path)}
    ws.write_json(out / PACK_NAME, pack)
    all_images = [str(p) for _, p in [*ref_images, *images, *slices]]
    lines = [f"JUDGE PACK READY: {out}",
             f"rubric {rubric_name}; {len(jr.renders.views)} judged view(s); {len(images)} montage/crop image(s)"
             + (f", {len(ref_images)} reference photo(s)" if ref_images else "") + (f", {len(slices)} slice(s)" if slices else ""),
             f"a fresh-context judge reads ONLY {out / 'brief.md'} and opens the images it lists, then writes {judgment_path}",
             f"then run: 3dcode score --round {ctx.round_index}"]
    n_err = sum(len(g.errors) for g in gates)
    if n_err:
        lines.append(f"note: {n_err} gate ERROR(s) go into the brief as facts and will cap the score")
    return text_observation(lines, numbers={"dir": str(out), "brief": str(out / "brief.md"), "judgment_path": str(judgment_path),
                                            "n_views": len(jr.renders.views), "n_images": len(all_images), "rubric": rubric_name},
                            images=all_images, limit=4000)


# ===================================================================== judge
#: the plugin root (``agents/3d-judge.md`` lives beside the package)
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
GIT_IDENTITY = (("user.name", "3dcodeverse"), ("user.email", "3dcodeverse@localhost"))
#: what a workspace repo never tracks (written to .git/info/exclude, not to a tracked file)
GIT_EXCLUDE = ("artifacts/", "judge/", "node_modules/", "__pycache__/", ".venv/")
GIT_TIMEOUT_S = 120.0


def _git(ws: Workspace, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
    r = run_subprocess(["git", *args], cwd=ws.root, timeout_s=GIT_TIMEOUT_S)
    if r.returncode not in ok_codes:
        raise ToolUsageError(f"git {' '.join(args)} failed ({r.returncode}): {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def _check_stale(ws: Workspace) -> str:
    """Why the last ``check`` cannot stand for the current src/ (empty when it can)."""
    raw = read_json_or_none(ws.gates_path)
    if raw is None:
        return "no check yet"
    res = CheckResult.model_validate(raw)
    if res.src_hash != ws.src_hash():
        return "src/ changed since the last check"
    if not res.build.ok:
        return "the last check did not build"
    return ""


def commit_round(ws: Workspace, round_index: int, message: str) -> tuple[str, list[str]]:
    """Commit src/ + plan.json as ``rNN`` (re-tagged on repeat) in the workspace's own repo, creating the repo and
    a local identity when there is none.  Returns ``(short sha, what was set up)``."""
    notes: list[str] = []
    if not (ws.root / ".git").exists():
        _git(ws, "init", "-q")
        notes.append("git init")
    exclude = ws.root / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        have = exclude.read_text().splitlines() if exclude.is_file() else []
        add = [e for e in GIT_EXCLUDE if e not in have]
        if add:
            exclude.write_text("\n".join([*have, *add]) + "\n")
    for key, value in GIT_IDENTITY:
        if not _git(ws, "config", key, ok_codes=(0, 1)).strip():
            _git(ws, "config", key, value)
            notes.append(f"git config {key} {value}")
    paths = [p for p in ("src", "plan.json") if (ws.root / p).exists()]
    if paths:
        _git(ws, "add", "-A", "--", *paths)
    _git(ws, "commit", "-q", "--allow-empty", "-m", message)
    tag = f"r{round_index:02d}"
    _git(ws, "tag", "-f", tag)
    return _git(ws, "rev-parse", "--short", "HEAD").strip(), notes


def judge_instruction(out: Path, images: list[str]) -> str:
    """The exact text to hand a fresh-context judge subagent for the folder ``out``: two steps, then one line."""
    agent = PLUGIN_ROOT / "agents" / "3d-judge.md"
    return (f"Judge ONLY the folder {out}. Step 1, in ONE step: read {out / 'brief.md'} and all of these images "
            f"together: {' '.join(images)}. Step 2: write {out / JUDGMENT_NAME} (exactly the JSON the brief specifies). "
            f"Then send exactly one line back to whoever started you: wrote {out / JUDGMENT_NAME} "
            f"(where a subagent can only reach its caller by a message, send it as a message; nothing else wakes the caller). "
            f"If you were not started as the 3d-judge agent, read {agent} in step 1 as well.")


@tool("judge", NoArgs, "One step from code to the judge: make the workspace a git repo (local identity if none), run "
      "`check` when the gates are stale for the current src/, commit src/ + plan.json tagged rNN, run `judge-pack`, "
      "and print the folder plus the exact JUDGE INSTRUCTION for a fresh-context 3d-judge subagent. Then "
      "`3dcode score --round N`.", cost_hint="slow")
def judge(ctx: ToolContext, args: NoArgs) -> Observation:
    ws = ctx.workspace
    spec = _spec(ctx)
    lines: list[str] = []
    stale = _check_stale(ws)
    if stale:
        chk = check(ctx, NoArgs())
        if not chk.numbers.get("build_ok"):
            return Observation(ok=False, failed=chk.failed, text=f"JUDGE STOPPED — check ran ({stale}) and did not build:\n" + chk.text,
                               numbers=chk.numbers)
        lines.append(f"check ran ({stale}): {chk.text.splitlines()[0]}")
    else:
        lines.append("check is current for this src/")
    sha, notes = commit_round(ws, ctx.round_index, f"round {ctx.round_index}: {spec.prompt[:72]}")
    lines.append(f"committed {sha} tagged r{ctx.round_index:02d}" + (f" ({'; '.join(notes)})" if notes else ""))
    pack = judge_pack(ctx, NoArgs())
    if pack.failed or not pack.ok:
        return Observation(ok=False, failed=pack.failed, text="\n".join(lines) + "\n" + pack.text, numbers=pack.numbers,
                           images=pack.images)
    out = ws.judge_dir(ctx.round_index)
    lines += pack.text.splitlines()[:2]
    instruction = judge_instruction(out, list(pack.images))
    lines.append("JUDGE INSTRUCTION: " + instruction)
    lines.append(f"next: 3dcode score --round {ctx.round_index}")
    return text_observation(lines, numbers={**pack.numbers, "commit": sha, "tag": f"r{ctx.round_index:02d}",
                                            "instruction": instruction}, limit=6000)


# ===================================================================== score
@tool("score", NoArgs, "Read judge/rNN/judgment.json (the judge's JSON), mend trivial deviations (an enum spelt wrong, "
      "a number out of range, a bare value, an unknown key — each printed as `repaired:`), apply the rubric's weights, "
      "defect penalties, floors and gate caps, write judge/rNN/score.json. Prints overall, PASS/FAIL, the caps and the "
      "improvement list for the next round (gate ERRORs first). A judgment missing a criterion score, a defect answer "
      "or an acceptance verdict is a FAIL that names exactly what to add.")
def score(ctx: ToolContext, args: NoArgs) -> Observation:
    ws = ctx.workspace
    spec = _spec(ctx)
    plan = _plan_or_none(ws, spec.track)
    out = ws.judge_dir(ctx.round_index)
    pack = read_json_or_none(out / PACK_NAME)
    if pack is None:
        raise ToolUsageError(f"no {out / PACK_NAME} — run `3dcode judge-pack --round {ctx.round_index}` first")
    jpath = out / JUDGMENT_NAME
    if not jpath.is_file():
        raise ToolUsageError(f"{jpath} does not exist — the judge has to write it first (see brief.md)")
    rubric = load_rubric(str(pack["rubric"]))
    acceptance_ids = [str(a) for a in pack.get("acceptance_ids") or []]
    measured = dict(pack.get("measured_scores") or {})
    again = f"then run `3dcode score --round {ctx.round_index}` again"
    try:
        payload = parse_json_lenient(jpath.read_text(errors="replace"))
    except JsonParseError as e:
        return text_observation([f"SCORE FAIL — {rel_path(jpath, ws.root)} is not JSON: {e}",
                                 f"Write ONE JSON object of the shape at the end of {out / 'brief.md'} to {jpath}, {again}."],
                                ok=False, numbers={"missing": ["a JSON object"]})
    payload, repairs, missing = repair_judgment(payload, rubric, acceptance_ids, measured_scores=measured)
    repaired = [f"repaired: {r}" for r in repairs]
    if missing:
        return text_observation([f"SCORE FAIL — {rel_path(jpath, ws.root)} is incomplete; add to it:"] + [f"- {m}" for m in missing]
                                + repaired + [f"(the shape is at the end of {out / 'brief.md'}; {again})"],
                                ok=False, numbers={"missing": missing, "repairs": len(repairs)})
    try:
        parsed = parse_judge_output(payload, rubric, acceptance_ids, measured_scores=measured)
    except JudgeParseError as e:
        return text_observation([f"SCORE FAIL — {rel_path(jpath, ws.root)} does not validate: {e}"] + repaired
                                + [f"(the shape is at the end of {out / 'brief.md'}; {again})"], ok=False, numbers={"repairs": len(repairs)})
    gates = [GateReport.model_validate(g) for g in pack.get("gates") or []]
    renders = RenderSet.model_validate(pack.get("renders") or {})
    items = list(getattr(plan, "acceptance", None) or [])
    judgment = aggregate_samples(rubric, [parsed], gates=gates, acceptance_items=items,
                                 console_errors=renders.console_errors, views=renders.views, judge_backend="host")
    ws.write_json(out / SCORE_NAME, judgment)
    breakdown = json.loads(judgment.raw)
    lines = [f"SCORE {judgment.overall:.2f} → {'PASS' if judgment.passed else 'FAIL'} (threshold {rubric.pass_threshold:.2f}; "
             f"uncapped {breakdown.get('overall_uncapped', 0):.2f}, defects -{breakdown.get('defect_penalty', 0):.2f})"] + repaired
    lines.append("criteria: " + ", ".join(f"{k} {v:.2f}" for k, v in judgment.scores.items()))
    present = [d for d, on in (breakdown.get("defects") or {}).items() if on]
    if present:
        lines.append("defects present: " + ", ".join(present))
    if breakdown.get("overridden"):
        lines.append("checklist claims a passed gate contradicted (no penalty): " + ", ".join(breakdown["overridden"]))
    caps = (breakdown.get("caps") or {}).get("caps_applied") or []
    if caps:
        lines.append("caps: " + "; ".join(f"{c['rule']}≤{c['cap']:.2f} ({c['evidence'][:120]})" for c in caps))
    missing = [a.id for a in items if a.priority == "must" and not judgment.acceptance_results.get(a.id, False)]
    if missing:
        lines.append("must items unverified: " + ", ".join(missing))
    if judgment.summary:
        lines.append("summary: " + judgment.summary.split(" [verdict:")[0][:600])
    improve: list[str] = []
    for g in gates:
        for f in g.errors:
            improve.append(f"[{g.gate}" + (f"/{f.target}" if f.target else "") + f"] {f.message}" + (f" — fix: {f.fix_hint}" if f.fix_hint else ""))
    for it in judgment.improvement_plan:
        improve.append(f"[{it.target}/{it.kind}, p{it.priority}] {it.instruction}")
    for a in items:
        if a.id in missing:
            improve.append(f"[acceptance {a.id}] not verified: {a.text}")
    if improve:
        lines.append("IMPROVE NEXT (gate errors first):")
        lines += [f"{i}. {t}" for i, t in enumerate(improve[:12], 1)]
    paths = [str(i.get("path", "")) for i in pack.get("images") or [] if i.get("path")]
    if paths:
        lines.append("the round's images (open these to confirm an item; never list or guess names):")
        lines += [f"  {p}" for p in paths]
    lines.append(f"written: {rel_path(out / SCORE_NAME, ws.root)}")
    return text_observation(lines, ok=judgment.passed,
                            numbers={"overall": judgment.overall, "passed": judgment.passed, "scores": judgment.scores,
                                     "defects": present, "caps": [c["rule"] for c in caps], "must_missing": missing,
                                     "n_improvements": len(improve), "repairs": len(repairs)}, images=paths, limit=8000)
