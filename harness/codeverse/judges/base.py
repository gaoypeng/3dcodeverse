"""``JudgeInput`` + the pure round-replay helpers ``3dcv judge`` and calibration share."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import GateReport, Judgment, Measurement, RenderSet
from codeverse.contracts.plan import AcceptanceItem
from codeverse.contracts.spec import Spec
from codeverse.workspace import Workspace


class JudgeInput(BaseModel):
    spec: Spec
    renders: RenderSet
    measurement: Measurement | None = None
    gates: list[GateReport] = Field(default_factory=list)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)
    plan_summary: str = Field(default="", description="short plan digest (parts/zones/joints) for grounding")
    round_index: int = 0
    previous: Judgment | None = Field(default=None, description="last verdict (for delta framing)")
    extra_context: str = ""
    geometry_views: RenderSet | None = Field(
        default=None, description="clay/normals renders for the geometry-only montage (holes, intersections)"
    )


# ===================================================================== round replay
def plan_digest(plan: dict[str, Any]) -> str:
    """A track-agnostic plan summary (parts / joints / zones / assets / cameras) from ``plan.json``."""
    bits: list[str] = []
    title = plan.get("object_name") or plan.get("title") or ""
    if title:
        bits.append(f"{title}: {plan.get('summary', '')}".strip())
    bbox = plan.get("overall_bbox") or {}
    if isinstance(bbox, dict) and bbox.get("extents"):
        e = bbox["extents"]
        bits.append(f"Overall {e[0]:.2f}×{e[1]:.2f}×{e[2]:.2f} m.")
    parts = plan.get("parts") or []
    if parts:
        bits.append("Parts: " + ", ".join(f"{p['name']}×{p['instances']}" if p.get("instances", 1) > 1 else p["name"] for p in parts) + ".")
    if plan.get("root_link"):
        bits.append(f"Root link {plan['root_link']}.")
    joints = plan.get("joints") or []
    if joints:
        bits.append("Joints: " + "; ".join(
            f"{j['name']} ({j.get('type', '?')} {j.get('parent', '?')}→{j.get('child', '?')}, [{j.get('lower', 0):.2f},{j.get('upper', 0):.2f}])"
            for j in joints) + ".")
    for key, label in (("zones", "Zones"), ("assets", "Assets"), ("cameras", "Cameras")):
        items = plan.get(key) or []
        if items:
            bits.append(f"{label}: " + ", ".join(str(i.get("name", "?")) for i in items) + ".")
    if plan.get("setting"):
        bits.append(f"Setting: {plan['setting']}.")
    return " ".join(bits)


def resolve_paths(ws: Workspace, rs: RenderSet | None) -> RenderSet | None:
    """Render paths out of a round record, resolved against THIS workspace.

    Delegates to ``Workspace.rebase``: this used to rebase only paths for which
    ``is_absolute()`` was False, which made it a no-op against every record the harness
    itself writes (they are all absolute) — so a moved or archived run kept pointing at
    the host it was produced on.
    """
    if rs is None:
        return None
    fixed = [v.model_copy(update={"path": str(ws.rebase(v.path))}) for v in rs.views]
    sheet = str(ws.rebase(rs.contact_sheet)) if rs.contact_sheet else rs.contact_sheet
    out_dir = str(ws.rebase(rs.out_dir)) if rs.out_dir else rs.out_dir
    return rs.model_copy(update={"views": fixed, "contact_sheet": sheet, "out_dir": out_dir})


def judged_subset(rs: RenderSet | None) -> RenderSet | None:
    """The views the in-run judge actually saw: the per-view ``judge`` flags
    stamped at render time; legacy rounds (no flags) keep every stored view."""
    if rs is None or not any(v.judge is not None for v in rs.views):
        return rs
    return rs.model_copy(update={"views": [v for v in rs.views if v.judge]})
