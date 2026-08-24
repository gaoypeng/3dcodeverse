"""``compare_reference`` — let the coding agent LOOK at the target and its own
render side by side.

``compare_silhouette`` (tools_render) answers *how much* the outline differs;
this tool answers *what* differs: it renders the object from the view that best
matches the reference's camera, builds one labelled side-by-side sheet
(REFERENCE | RENDER | outline diff) and hands the agent the numbers plus a
checklist of what to look for.  No model call — the agent IS the vision model.

Registered from ``spatial/tools.py`` (one appended import) so
``import codeverse.spatial.tools`` still registers every tool.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from codeverse.spatial.observe import fmt_numbers, image_budget
from codeverse.spatial.registry import Observation, ToolContext, ToolUsageError, tool
from codeverse.spatial.sheet import contact_sheet
from codeverse.spatial.silhouette import compare_silhouette as _compare_silhouette
from codeverse.spatial.tool_common import (
    VIEW_BY_NAME,
    cached_render_glb,
    glb_path,
    render_cache_dir,
    spec_dict,
)

#: what the agent should compare, in the order that decides whether the object
#: reads as the real thing (identity before polish)
CHECKLIST = (
    "1) part inventory — is every part visible in the reference present in your model, and nothing invented? "
    "2) counts — spokes / slats / rods / panes / flutes: count them in the reference and match the number. "
    "3) proportions — the ratio of each part to the whole, not just the overall box. "
    "4) profile — where the reference curves, tapers, bevels or cuts through, does your part still read as a raw box? "
    "5) materials — per-part colour and finish."
)

_SYNTH_WARNING = ("This reference was SYNTHESIZED from the brief by an image model, not photographed: it is a "
                  "shape and part-inventory target only. Where it disagrees with the brief or the stated "
                  "dimensions, follow the BRIEF.")


class CompareReferenceArgs(BaseModel):
    view: str = Field(default="front_right_34", description="view to render for the comparison")
    reference_index: int = Field(default=0, ge=0, description="index into spec.references")
    size: int = Field(default=512, ge=256, le=1024, description="render size in px (square)")


def _reference_path(ctx: ToolContext, index: int) -> tuple[Path, dict]:
    refs = spec_dict(ctx).get("references") or []
    if not refs:
        raise ToolUsageError("the spec has no reference images — nothing to compare against")
    if index >= len(refs):
        raise ToolUsageError(f"reference_index {index} out of range (have {len(refs)})",
                             "compare_reference(reference_index=0)")
    ref = refs[index]
    ref = ref if isinstance(ref, dict) else {"path": ref.path, "role": ref.role, "note": ref.note}
    p = Path(ref["path"])
    if not p.is_absolute():
        p = ctx.workspace.root / p
    return p, ref


@tool("compare_reference", CompareReferenceArgs,
      "Put the REFERENCE image and a render of your object side by side (+ outline diff and IoU). Use it to check "
      "you built the right thing: part inventory, counts, proportions, profiles.", cost_hint="slow")
def compare_reference(ctx: ToolContext, args: CompareReferenceArgs) -> Observation:
    glb = glb_path(ctx)
    ref_path, ref = _reference_path(ctx, args.reference_index)
    if not ref_path.is_file():
        return Observation.error(f"reference image {ref_path.name} not found")
    if args.view not in VIEW_BY_NAME:
        raise ToolUsageError(f"unknown view {args.view!r}; choose from {list(VIEW_BY_NAME)}",
                             "compare_reference(view='front_right_34')")
    preset = VIEW_BY_NAME[args.view]
    shaded = cached_render_glb(ctx, glb, views=[preset], mode="shaded", size=args.size, sheet=False)
    sil = cached_render_glb(ctx, glb, views=[preset], mode="silhouette", size=args.size, sheet=False)
    if not shaded.views or not sil.views:
        return Observation.error("compare_reference: renderer produced no view")
    out_dir = render_cache_dir(ctx, glb, compare_ref=args.view, ref=args.reference_index)
    diff_png = out_dir / f"outline_diff_{args.view}_ref{args.reference_index}.png"
    res = _compare_silhouette(sil.views[0].path, ref_path, diff_png=diff_png)
    sheet = contact_sheet(
        [("REFERENCE (target)", ref_path), (f"YOUR MODEL ({args.view})", shaded.views[0].path),
         ("outline: red=reference only, blue=yours", diff_png)],
        out_dir / f"compare_reference_{args.view}_ref{args.reference_index}.png", cols=3, tile=384)
    iou = float(res["iou"])
    verdict = "good match" if iou >= 0.8 else "rough match" if iou >= 0.6 else "POOR match"
    if not res["reliable"]:
        verdict += " (IoU UNRELIABLE: background mask failed — trust the pictures, not the number)"
    lines = [
        f"REFERENCE #{args.reference_index} ({ref_path.name}, role={ref.get('role', 'target')}) vs your {args.view} "
        f"render: outline IoU {iou:.2f} → {verdict}; aspect w/h yours {res['render_aspect']:.2f} vs reference "
        f"{res['ref_aspect']:.2f} (err {res['aspect_ratio_err']:.0%}).",
        f"Look at the sheet and check, in this order: {CHECKLIST}",
    ]
    if str(ref.get("note", "")).startswith("SYNTHESIZED"):
        lines.append(_SYNTH_WARNING)
    lines.append(fmt_numbers({k: v for k, v in res.items() if k != "diff_png_path"}))
    images = image_budget([str(sheet), str(diff_png), shaded.views[0].path])
    return Observation(ok=True, text="\n".join(lines),
                       numbers={"view": args.view, "reference_index": args.reference_index, **res}, images=images)


__all__ = ["CHECKLIST", "CompareReferenceArgs", "compare_reference"]
