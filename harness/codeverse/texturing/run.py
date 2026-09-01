"""``texture_pass``: the orchestrating entry for object texturing.

    renders (latest sheet or a quick render) → normalise_materials (free, deterministic)
    → material_plan (1 vision call)
    → generate_textures (image model, cached, parallel) → seam_gate
    → apply_textures (+ derived roughness/normal maps) → artifacts/object_textured.glb
    → judge_gate (before/after, n=1) → TextureReport (+ artifacts/texturing.json,
      record.json extra["texturing"] when a record exists)

Code stays the truth: ``artifacts/object.glb`` is never overwritten; the textured
GLB and ``artifacts/textures/`` are a derived asset pack recorded with the run.
The textured GLB is built in an :class:`~codeverse.workspace.ArtifactStage` and
promoted to ``artifacts/object_textured.glb`` only when the pass ships — a
rejected pass leaves no canonical file for the deliverable/gallery to pick up.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import Measurement, RenderSet
from codeverse.contracts.common import TRACK_INFO, Track, Usage
from codeverse.contracts.plan import StaticPlan
from codeverse.contracts.spec import Spec
from codeverse.conventions import OBJECT_VIEWS_QUICK, ViewPreset
from codeverse.proc import EventLog
from codeverse.texturing.apply import (
    ApplyReport,
    NormaliseReport,
    apply_textures,
    normalise_materials,
)
from codeverse.texturing.generate import (
    GateResult,
    SeamGateResult,
    TextureSet,
    generate_textures,
    judge_gate,
    seam_gate,
)
from codeverse.texturing.plan import TexturePlan, material_plan
from codeverse.workspace import Workspace

log = logging.getLogger(__name__)

TEXTURED_GLB = "object_textured.glb"
TEXTURES_DIR = "textures"
REPORT_NAME = "texturing.json"


@dataclass
class TextureServices:
    """The injectable dependencies of :func:`texture_pass` in one bundle (tests and
    the spatial tool pass fakes through a single ``ctx.extra['texture_services']``
    key).  Every field defaults to None = "build/use the real thing"."""

    image_model: Any | None = None
    plan_model: Any | None = None
    render: Any | None = None
    cache_dir: Path | None = None
    judge_obj: Any | None = None


class TextureReport(BaseModel):
    plan: TexturePlan
    textures: TextureSet
    seam: SeamGateResult
    normalise: NormaliseReport | None = None
    apply: ApplyReport | None = None
    gate: GateResult | None = None
    glb_in: str
    glb_out: str = ""
    shipped: bool = False
    delta: float | None = None
    usage: Usage = Field(default_factory=Usage)
    duration_s: float = 0.0
    notes: list[str] = Field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "shipped": self.shipped, "delta": self.delta, "glb_out": self.glb_out,
            "n_textures": len(self.textures.paths()), "seam_failed": sorted(self.seam.failed),
            "parts_textured": len(self.apply.parts_textured) if self.apply else 0,
            "materials_normalised": len(self.normalise.changes) if self.normalise else 0,
            "derived_maps": self.apply.n_derived_maps if self.apply else 0,
            "materials_delta": self.gate.materials_delta if self.gate else None,
            "cost_usd": round(self.usage.cost_usd, 4), "duration_s": self.duration_s,
            "plan_source": self.plan.source, "reason": self.gate.reason if self.gate else "; ".join(self.notes),
        }


def latest_sheet(ws: Workspace) -> Path | None:
    """The most recent round's contact sheet (``artifacts/renders/rNN/sheet.png``)."""
    rdir = ws.artifacts / "renders"
    if not rdir.is_dir():
        return None
    rounds = sorted((p for p in rdir.glob("r[0-9][0-9]") if (p / "sheet.png").is_file()), key=lambda p: p.name)
    return rounds[-1] / "sheet.png" if rounds else None


def _render_quick(glb: Path, out_dir: Path, views: Sequence[ViewPreset], render: Any | None) -> RenderSet:
    if render is None:
        from codeverse.spatial.render import render_glb

        render = render_glb
    return render(glb, out_dir, views=list(views), width=512, height=512)


def _measure(glb: Path) -> Measurement | None:
    try:
        from codeverse.spatial.measure import measure_glb

        return measure_glb(glb)
    except Exception as e:  # noqa: BLE001 — measurement is judge context only
        log.warning("measure_glb failed for texture gate: %s", e)
        return None


#: the ONE owner of "does this run texture?".  Three call sites used to answer it
#: independently — ``tracks.lifecycle.finalise`` (spec.options.texture / a "texture" tag /
#: ctx.extra), and the ``texture_pass`` spatial tool, which the coding agent could call in
#: any run because the tool is registered for every object track.  That is how a
#: ``texture: false`` run still paid for a texture pass (docs/COST.md §15: the quality run's
#: ledger was +9.5% over record.total_usage because the pass ran twice, once from inside a
#: round-2 agent session).  Both now ask this function.
#: the object tracks — the only ones with an ``artifacts/object.glb`` to texture.  A scene
#: has no GLB deliverable (languages/scene_threejs/runtime.py: "BuildResult.glb_path stays
#: None") and neither graphics language produces one at all, so ``texture_pass`` on those
#: tracks can only ever raise FileNotFoundError.  Scenes have their own command,
#: ``3dcv texture scene-pack``.
TEXTURE_TRACKS = (Track.STATIC_OBJECT, Track.ARTICULATED_OBJECT)


def texture_supported(track: Track) -> bool:
    """Whether ``texture_pass`` can run on this track at all (is there a GLB?)."""
    return track in TEXTURE_TRACKS


def texture_requested(spec: Spec) -> bool:
    """True when the run asked for the derived texture pass AND the track can run one.

    ``Spec.options.texture`` is the switch; the legacy ``texture`` tag is still
    honoured because recorded specs carry it.  Nothing else may turn texturing
    on — an agent calling the ``texture_pass`` tool in a run that did not ask for
    it is refused, and ``3dcv texture pass <slug>`` is an explicit user
    instruction that does not go through here at all.

    The track scope belongs here too, for the same "one owner" reason: without it
    ``--profile quality`` (which forces texture=True) made every scene and graphics run
    call a pass that could only raise FileNotFoundError, swallowed at log.warning into a
    spurious ``texture.failed`` event — quality's advertised "+texture" was a guaranteed
    no-op on half the tracks, and scene runs never reached the command that would work.
    """
    return bool(spec.options.texture or "texture" in (spec.tags or [])) and texture_supported(spec.track)


def texture_pass(
    ws: Workspace,
    spec: Spec,
    plan: StaticPlan,
    *,
    model_id: str,
    image_model: Any | None = None,
    judge: bool = True,
    judge_obj: Any | None = None,
    judge_model_id: str | None = None,
    rubric: str | None = None,
    glb_in: Path | None = None,
    views: Sequence[ViewPreset] = OBJECT_VIEWS_QUICK,
    size: int = 1024,
    plan_model: Any | None = None,
    render: Any | None = None,
    cache_dir: Path | None = None,
    services: TextureServices | None = None,
    events: EventLog | None = None,
    update_record: bool = True,
    normalise: bool = True,
) -> TextureReport:
    """Run the whole pass on ``ws``.

    ``judge=False`` skips the before/after VLM gate (ship iff something was
    textured and no seam failed); ``judge_obj`` replaces the constructed
    ``VlmJudge`` (tests).  ``services`` bundles the five injectable dependencies
    (image_model / plan_model / render / cache_dir / judge_obj); explicit
    keyword arguments win over the bundle.

    ``normalise=True`` first runs the deterministic material normaliser
    (:func:`codeverse.texturing.apply.normalise_materials`) over the input GLB,
    so the parts the texture pass *skips* — chrome, glass, tiny hardware — still get
    plausible metallic/roughness numbers.  It costs no model call, and the same
    before/after judge gate (BEFORE is always the untouched ``glb_in``) decides
    whether the combined result ships."""
    if services is not None:
        image_model = image_model if image_model is not None else services.image_model
        plan_model = plan_model if plan_model is not None else services.plan_model
        render = render if render is not None else services.render
        cache_dir = cache_dir if cache_dir is not None else services.cache_dir
        judge_obj = judge_obj if judge_obj is not None else services.judge_obj
    t0 = time.time()
    events = events or EventLog(ws.events_path)
    glb_in = Path(glb_in) if glb_in else ws.artifacts / "object.glb"
    if not glb_in.is_file():
        raise FileNotFoundError(f"no GLB to texture: {glb_in}")
    tex_dir = ws.artifacts / TEXTURES_DIR
    glb_out = ws.artifacts / TEXTURED_GLB
    notes: list[str] = []
    events.emit("texture.start", model=model_id, glb=glb_in.name)

    # 1. renders for the planner (reuse the latest sheet; else a quick render)
    sheet = latest_sheet(ws)
    if sheet is None:
        rs = _render_quick(glb_in, tex_dir / "planner_views", views, render)
        sheet = Path(rs.contact_sheet) if rs.contact_sheet else (Path(rs.views[0].path) if rs.views else None)

    # 1b. deterministic material normalisation (free; the texture base, never the judged BEFORE)
    texture_base = glb_in
    norm = None
    if normalise:
        norm = normalise_materials(glb_in, tex_dir / "object_normalised.glb", plan=plan)
        if norm.glb_out:
            texture_base = Path(norm.glb_out)
        events.emit("texture.normalised", changed=len(norm.changes), materials=norm.n_materials,
                    families=sorted({c.family for c in norm.changes}))

    # 2. material plan
    tplan = material_plan(spec, plan, sheet, model_id, model=plan_model, cache_dir=cache_dir)
    usage = tplan.usage
    events.emit("texture.plan", source=tplan.source, n_parts=len(tplan.parts), n_textured=len(tplan.textured()),
                n_textures=len(tplan.texture_ids()), cost_usd=round(tplan.usage.cost_usd, 4))
    ws.write_json(tex_dir / "texture_plan.json", tplan)

    # 3. images
    if image_model is None:
        from codeverse.models.gemini import GeminiImageModel

        image_model = GeminiImageModel()
    tset = generate_textures(tplan, tex_dir, image_model, size=size, cache_dir=cache_dir)
    usage = usage + tset.usage
    seam = seam_gate(tset.textures)
    events.emit("texture.generated", n=len(tset.textures), failed=sorted(tset.failed()), seam_failed=sorted(seam.failed),
                cost_usd=round(tset.usage.cost_usd, 4), duration_s=tset.duration_s)
    for tid, err in tset.failed().items():
        notes.append(f"texture {tid} failed: {err}")
    keep = {tid: Path(tset.textures[tid].path) for tid in seam.passed}
    report = TextureReport(plan=tplan, textures=tset, seam=seam, glb_in=str(glb_in), usage=usage,
                           notes=notes, normalise=norm)
    notes = report.notes  # pydantic copied the list; keep appending to the report's own
    if not keep:
        notes.append("no usable textures (all failed or seams too strong) — nothing applied")
        return _finish(ws, report, t0, events, update_record)

    # 4. apply — into staging: the canonical object_textured.glb exists on disk ONLY
    # when the pass ships (entering the stage also removes any earlier pass's file)
    with ws.stage_artifacts(TEXTURED_GLB) as stage:
        staged_glb = stage.path(TEXTURED_GLB)
        report.apply = apply_textures(texture_base, tplan, keep, staged_glb)
        report.glb_out = str(glb_out)  # the canonical home; a real file iff shipped
        notes.extend(report.apply.warnings)
        events.emit("texture.applied", parts=len(report.apply.parts_textured), skipped=len(report.apply.parts_skipped),
                    materials=report.apply.n_materials, warnings=len(report.apply.warnings))
        if not report.apply.parts_textured:
            notes.append("no part was textured")
            return _finish(ws, report, t0, events, update_record)

        # 5. gate
        if not judge:
            report.shipped = True
            notes.append("judge gate skipped (--no-judge): shipped on seam gate only")
            stage.promote()
            return _finish(ws, report, t0, events, update_record)
        gate_judge = judge_obj if judge_obj is not None else _make_judge(spec, rubric, judge_model_id)
        gate = judge_gate(spec, plan, glb_in, staged_glb, tex_dir / "gate", judge=gate_judge, measurement=_measure(glb_in),
                          views=views, render=render)
        report.gate, report.shipped, report.delta = gate, gate.shipped, gate.delta
        report.usage = report.usage + gate.usage
        events.emit("texture.gate", shipped=gate.shipped, delta=gate.delta, materials_delta=gate.materials_delta,
                    before=gate.overall_before, after=gate.overall_after, reason=gate.reason,
                    cost_usd=round(gate.usage.cost_usd, 4))
        if report.shipped:
            stage.promote()
        return _finish(ws, report, t0, events, update_record)


def _make_judge(spec: Spec, rubric: str | None, judge_model_id: str | None) -> Any:
    from codeverse.judges.vlm_judge import VlmJudge

    return VlmJudge(rubric=rubric or TRACK_INFO[spec.track].rubric, model_id=judge_model_id or spec.backends.judge,
                    n_samples=1, label="texture_gate")


def _finish(ws: Workspace, report: TextureReport, t0: float, events: EventLog, update_record: bool) -> TextureReport:
    report.duration_s = round(time.time() - t0, 2)
    if not report.shipped:
        # a pass that did not ship leaves NO canonical textured GLB — including one
        # left behind by an earlier shipped pass (the report keeps all its fields)
        ws.stage_artifacts(TEXTURED_GLB).invalidate()
    ws.write_json(ws.artifacts / TEXTURES_DIR / REPORT_NAME, report)
    if update_record:
        record_texturing(ws, report)
    events.emit("texture.done", **report.summary())
    return report


def report_path(ws: Workspace) -> Path:
    """THE location of the texture report — the finalise double-buy guard reads it too."""
    return ws.artifacts / TEXTURES_DIR / REPORT_NAME


def record_texturing(ws: Workspace, report: TextureReport) -> bool:
    """``record.json`` extra["texturing"] = summary (+ asset paths) when a record exists."""
    if not ws.record_path.is_file():
        return False
    try:
        data = json.loads(ws.record_path.read_text())
    except json.JSONDecodeError:
        return False
    extra = data.setdefault("extra", {})
    extra["texturing"] = texturing_extra(ws, report)
    ws.write_json(ws.record_path, data)
    return True


def texturing_extra(ws: Workspace, report: TextureReport) -> dict[str, Any]:
    """The ``extra["texturing"]`` payload — ONE builder for the record write here and the
    finalise-side collect (a tool-textured session no longer writes record.json itself)."""
    return {
        **report.summary(),
        "glb_textured": str(Path(report.glb_out).relative_to(ws.root)) if report.glb_out else "",
        "textures_dir": f"artifacts/{TEXTURES_DIR}",
        "textures": {tid: Path(a.path).name for tid, a in report.textures.textures.items() if a.ok},
        "texture_plan": {p.part: p.texture_id for p in report.plan.textured()},
    }


def load_report(ws: Workspace) -> TextureReport:
    p = report_path(ws)
    if not p.is_file():
        raise FileNotFoundError(f"no texturing report at {p}")
    return TextureReport.model_validate_json(p.read_text())
