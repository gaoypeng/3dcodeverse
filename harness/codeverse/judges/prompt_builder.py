"""Build the judge conversation: system (role + rubric + output rules) and ONE
user message (brief, plan digest, acceptance checklist, measurement table, gate
digest, previous-verdict framing, view-rig paragraph, then labelled images).

Text is budgeted to ≈6k tokens; images to ≤ ``max_images`` at ≤ ``max_px``.
The builder's code and reasoning are never included (blind judge).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING

from codeverse.contracts.artifacts import GateReport, Measurement, RenderSet, RenderView, Severity
from codeverse.contracts.chat import ChatMessage, ImagePart, TextPart
from codeverse.contracts.judgment import Judgment
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.spec import Spec
from codeverse.judges.images import image_part, prepare_image, view_label
from codeverse.judges.rubrics import Rubric

if TYPE_CHECKING:  # pragma: no cover
    from codeverse.judges.base import JudgeInput

TEXT_BUDGET_CHARS = 24_000  # ≈ 6k tokens
MAX_IMAGES = 10
MAX_PX = 768

_ROLE = """You are the BLIND JUDGE of a 3D-code harness: exacting but fair.
You see only the brief, a plan digest, measured numbers, deterministic gate findings and labelled renders of the result — never the builder's code or reasoning. Judge what is visible and measured; do not invent faults and do not credit what you cannot see.

SCORING RULES
- Score each criterion 0..1 against its anchors (interpolate between anchors). Use the whole range: competent work sits at 0.8+, one clearly visible major defect pulls the affected criterion to ~0.4, broken work sits at 0.1-0.3. Do not compress everything into 0.5-0.7.
- Every score needs evidence that cites the VIEW label(s) and/or the measurement that shows it (e.g. "VIEW 4 (right): rear leg ends 3 cm above ground; measurement ground_gap 0.03").
- Do NOT compute an overall or decide pass/fail; the harness computes the weighted overall, applies floors and caps.
- Issues: observable defects, most severe first, with target = the part / zone / joint / asset name from the plan digest (or "overall"), a kind, a severity and evidence.
- Improvement plan: at most 6 concrete, imperative instructions for the builder ("taper the four legs from 45 mm at the seat to 30 mm at the foot and extend them to touch y=0"), priority 1 first, each with a target name from the plan digest and an expected_gain estimate. Give items even for passing work if a named change would raise the score; leave empty only when nothing would.
- Acceptance items: answer verified=true ONLY when the renders or measurements prove the item; otherwise false with what is missing.
- Reply with ONE JSON object matching the requested schema; no prose outside it."""


def _rubric_block(rubric: Rubric) -> str:
    lines = [f"RUBRIC {rubric.name} v{rubric.version} (pass threshold {rubric.pass_threshold:.2f}, for information only)"]
    for i, c in enumerate(rubric.criteria, 1):
        if c.kind == "measured":
            lines.append(f"{i}. {c.id} (weight {c.weight:.2f}) — MEASURED BY THE HARNESS, shown for calibration; do not score it.")
            continue
        floor = f", hard floor {c.floor:.2f}" if c.floor is not None else ""
        lines.append(f"{i}. {c.id} (weight {c.weight:.2f}{floor}) — {c.label}: {c.description.strip()}")
        for lvl in ("1.0", "0.7", "0.4", "0.1"):
            lines.append(f"     {lvl}: {c.anchors[lvl]}")
    if rubric.extra_instructions.strip():
        lines.append("")
        lines.append("RUBRIC NOTES: " + rubric.extra_instructions.strip())
    return "\n".join(lines)


def build_system_prompt(rubric: Rubric) -> str:
    return _ROLE + "\n\n" + _rubric_block(rubric)


# --------------------------------------------------------------------------- text sections
def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 12].rstrip() + " …[clipped]"


def brief_section(spec: Spec) -> str:
    lines = [f"BRIEF (track {spec.track.value}, language {spec.language.value}):", _clip(spec.prompt, 3000)]
    c = spec.constraints
    cons = []
    if c.dimensions_m:
        cons.append("dimensions_m: " + ", ".join(f"{k}={v:g}" for k, v in c.dimensions_m.items()))
    if c.max_triangles:
        cons.append(f"max_triangles: {c.max_triangles}")
    if c.style:
        cons.append(f"style: {c.style}")
    if c.must_have:
        cons.append("must_have: " + "; ".join(c.must_have))
    if c.must_not:
        cons.append("must_not: " + "; ".join(c.must_not))
    if cons:
        lines.append("CONSTRAINTS: " + " | ".join(cons))
    return "\n".join(lines)


def acceptance_section(items: list[AcceptanceItem]) -> str:
    if not items:
        return "ACCEPTANCE CHECKLIST: (none)"
    lines = ["ACCEPTANCE CHECKLIST (answer every id):"]
    for a in items[:40]:
        lines.append(f"- {a.id} [{a.priority}, via {a.how}]: {_clip(a.text, 240)}")
    if len(items) > 40:
        lines.append(f"- … {len(items) - 40} more items omitted")
    return "\n".join(lines)


def measurement_section(m: Measurement | None) -> str:
    if m is None:
        return "MEASUREMENTS: (none available)"
    try:
        from codeverse.spatial.measure import measure_summary_table  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        measure_summary_table = None
    if measure_summary_table is not None:
        return "MEASUREMENTS (harness, Y-up meters):\n" + _clip(str(measure_summary_table(m)), 6000)
    return "MEASUREMENTS (harness, Y-up meters):\n" + _local_measure_table(m)


def _fmt3(v: tuple[float, float, float]) -> str:
    return f"({v[0]:.3f}, {v[1]:.3f}, {v[2]:.3f})"


def _local_measure_table(m: Measurement) -> str:
    ex = m.extents
    lines = [
        f"overall extents x/y/z = {ex[0]:.3f} × {ex[1]:.3f} × {ex[2]:.3f} m; bbox min {_fmt3(m.bbox_min)} max {_fmt3(m.bbox_max)}; centre {_fmt3(m.center)}",
        f"triangles {m.tri_count}; meshes {m.n_meshes}; islands {m.n_islands}; materials {m.materials}; ground_gap {m.ground_gap_m:+.4f} m; footprint_offset {m.footprint_offset_m:.4f} m",
    ]
    if m.parts:
        lines.append("part | size x×y×z (m) | min y | tris | islands | watertight")
        for p in m.parts[:40]:
            sx, sy, sz = (p.bbox_max[i] - p.bbox_min[i] for i in range(3))
            wt = "-" if p.watertight is None else ("yes" if p.watertight else "no")
            lines.append(f"{p.name} | {sx:.3f}×{sy:.3f}×{sz:.3f} | {p.bbox_min[1]:.3f} | {p.tri_count} | {p.islands} | {wt}")
        if len(m.parts) > 40:
            lines.append(f"… {len(m.parts) - 40} more parts")
    for k, v in list(m.extra.items())[:8]:
        lines.append(f"{k}: {_clip(str(v), 200)}")
    return "\n".join(lines)


def gates_section(gates: list[GateReport], *, max_errors: int = 12, max_warns: int = 8) -> str:
    if not gates:
        return "GATE FINDINGS: (no gates run)"
    errs, warns = [], []
    for g in gates:
        for f in g.findings:
            tgt = f" [{f.target}]" if f.target else ""
            line = f"- {g.gate}{tgt}: {_clip(f.message, 220)}"
            (errs if f.severity == Severity.ERROR else warns if f.severity == Severity.WARN else []).append(line)
    status = ", ".join(f"{g.gate}={'pass' if g.passed else 'FAIL'}" for g in gates)
    lines = [f"GATE FINDINGS (deterministic; treat as facts): {status}"]
    if errs:
        lines.append(f"errors ({len(errs)}):")
        lines.extend(errs[:max_errors])
        if len(errs) > max_errors:
            lines.append(f"- … {len(errs) - max_errors} more errors")
    if warns:
        lines.append(f"warnings ({len(warns)}):")
        lines.extend(warns[:max_warns])
        if len(warns) > max_warns:
            lines.append(f"- … {len(warns) - max_warns} more warnings")
    if not errs and not warns:
        lines.append("no errors or warnings — parts are connected and contracts hold.")
    return "\n".join(lines)


def previous_section(prev: Judgment | None, round_index: int) -> str:
    if prev is None:
        return ""
    lines = [
        f"PREVIOUS VERDICT (round {max(round_index - 1, 0)}): overall {prev.overall:.2f}, "
        f"{'passed' if prev.passed else 'not passed'}. The builder was then asked to:"
    ]
    for it in prev.improvement_plan[:6]:
        lines.append(f"- [{it.target}/{it.kind}] {_clip(it.instruction, 200)}")
    if not prev.improvement_plan:
        lines.append("- (no plan items)")
    lines.append(
        "Judge THIS round on its own merits (do not anchor on the old numbers), but say in the summary which of "
        "those items are now fixed, which are STILL present, and whether anything regressed."
    )
    return "\n".join(lines)


def view_rig_section(renders: RenderSet, n_views: int, *, scene: bool) -> str:
    bits = [
        "VIEW RIG: the first image is a CONTACT SHEET of all views (tiles are labelled); it is followed by the individual views, each with a burnt-in label 'VIEW k/N — name · az/el'." if renders.contact_sheet else
        "VIEW RIG: each image carries a burnt-in label 'VIEW k/N — name · az/el'.",
        "Azimuth 0° = looking at the FRONT of the object, increasing counter-clockwise seen from above (90° = the object's right side, 180° = back); elevation is degrees above the horizon (negative = looking up from below the ground plane, which reveals undersides and ground contact).",
    ]
    if scene:
        bits.append("Views named overview_* are harness cameras fitted to the scene bounds (layout X-ray); views named cam_* are the scene's own authored cameras (grade composition/lighting on those); 't=' is the animation time.")
    else:
        bits.append("All views show the same object; cite the view label when you report a defect. Use top + low views for symmetry, footprint and ground contact.")
    if renders.console_errors:
        bits.append(f"PROBE: {len(renders.console_errors)} console error(s) during rendering, first: {_clip(renders.console_errors[0], 200)}")
    if renders.fps is not None:
        bits.append(f"PROBE: measured {renders.fps:.0f} fps.")
    bits.append(f"{n_views} view(s) follow.")
    return "\n".join(bits)


# --------------------------------------------------------------------------- images
def select_views(views: list[RenderView], max_views: int, *, shuffle_seed: int | None) -> list[RenderView]:
    """Evenly sub-sample to ``max_views`` (keeps rig diversity) then optionally shuffle the order."""
    chosen = list(views)
    if len(chosen) > max_views:
        step = len(chosen) / max_views
        chosen = [chosen[int(i * step)] for i in range(max_views)]
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(chosen)
    return chosen


def image_parts_for_renders(
    renders: RenderSet,
    *,
    max_images: int = MAX_IMAGES,
    max_px: int = MAX_PX,
    shuffle_seed: int | None = None,
    cache_dir: Path | None = None,
    reserved: int = 0,
) -> tuple[list[tuple[str, ImagePart]], list[RenderView]]:
    """(label, ImagePart) pairs: contact sheet first, then labelled views; plus the chosen views."""
    budget = max_images - reserved
    out: list[tuple[str, ImagePart]] = []
    if renders.contact_sheet and budget > 0:
        lbl = f"CONTACT SHEET — all {len(renders.views)} views"
        out.append((lbl, image_part(prepare_image(renders.contact_sheet, label=lbl, max_px=max(max_px, 1024), cache_dir=cache_dir), lbl)))
        budget -= 1
    chosen = select_views(renders.views, max(budget, 0), shuffle_seed=shuffle_seed)
    n = len(chosen)
    for i, v in enumerate(chosen, 1):
        lbl = view_label(v, i, n)
        out.append((lbl, image_part(prepare_image(v.path, label=lbl, max_px=max_px, cache_dir=cache_dir), lbl)))
    return out, chosen


# --------------------------------------------------------------------------- assembly
def build_judge_messages(
    inp: JudgeInput,
    rubric: Rubric,
    *,
    shuffle_seed: int | None = None,
    max_images: int = MAX_IMAGES,
    max_px: int = MAX_PX,
    cache_dir: Path | None = None,
    extra_images: list[tuple[str, str | Path]] | None = None,
    extra_text: str = "",
) -> tuple[str, list[ChatMessage]]:
    """Return ``(system, [user_message])`` for a rubric judge call.

    ``extra_images`` (label, path) are placed BEFORE the renders (e.g. reference
    images); ``extra_text`` is appended to the text block (e.g. measured silhouette).
    """
    system = build_system_prompt(rubric)
    is_scene = inp.spec.track.value == "scene"
    sections = [
        brief_section(inp.spec),
        ("PLAN DIGEST:\n" + _clip(inp.plan_summary, 5000)) if inp.plan_summary.strip() else "PLAN DIGEST: (none)",
        acceptance_section(inp.acceptance),
        measurement_section(inp.measurement),
        gates_section(inp.gates),
        previous_section(inp.previous, inp.round_index),
        _clip(inp.extra_context, 2500) if inp.extra_context.strip() else "",
        _clip(extra_text, 2500) if extra_text.strip() else "",
    ]
    extras = extra_images or []
    images, chosen = image_parts_for_renders(
        inp.renders, max_images=max_images, max_px=max_px, shuffle_seed=shuffle_seed, cache_dir=cache_dir,
        reserved=len(extras),
    )
    sections.append(view_rig_section(inp.renders, len(chosen), scene=is_scene))
    text = "\n\n".join(s for s in sections if s)
    if len(text) > TEXT_BUDGET_CHARS:
        text = _clip(text, TEXT_BUDGET_CHARS)
    parts: list[TextPart | ImagePart] = [TextPart(text=text)]
    for lbl, p in extras:
        parts.append(TextPart(text=lbl))
        parts.append(image_part(prepare_image(p, label=lbl, max_px=max_px, cache_dir=cache_dir), lbl))
    for lbl, ip in images:
        parts.append(TextPart(text=lbl))
        parts.append(ip)
    parts.append(TextPart(text="Now score every criterion with evidence, answer every acceptance item, and return the JSON object."))
    return system, [ChatMessage(role="user", parts=parts)]
