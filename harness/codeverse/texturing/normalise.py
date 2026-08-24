"""Post-build material normaliser: give a GLB's flat-default materials real PBR numbers.

The agent authors the object's *colours*; what it forgets are the two numbers that
decide whether a surface reads as the thing it is named after.  Two failure modes
show up in the recorded verdicts, and this pass fixes exactly those two — nothing
else, so it can never repaint a deliberate choice:

``untouched``
    metallic/roughness sit exactly on a framework default pair
    (glTF 1.0/1.0, three 0.0/1.0, Blender Principled 0.0/0.5).  Nobody types those:
    they are what you get when the material was never configured.  The glTF default
    in particular is *fully metallic, fully rough* — grey mud under any light.

``impossible``
    the numbers are outside the physical band of the family the material's own
    name claims: a node called ``PolishedChrome`` at ``metallic 0.2``, a
    ``VarnishedBeech`` at ``metallic 0.8``, a ``CastIron`` at ``roughness 0.05``.

Everything else is left alone.  Base colour is **never** changed — that is the
agent's design decision and the judge grades intent on it.

Like the texture pass this is a *derived asset*: ``artifacts/object.glb`` and
``src/`` are untouched; the result is ``artifacts/object_materials.glb``, shipped
only if the same do-no-harm judge gate the texture pass uses says it helped.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, NamedTuple

import trimesh
from pydantic import BaseModel, Field

from codeverse.contracts.plan import StaticPlan
from codeverse.conventions import to_snake
from codeverse.spatial.measure import load_scene
from codeverse.texturing.materials import (
    FamilyMatch,
    family_for,
    is_framework_default,
    looks_painted,
    out_of_band,
    pbr_for,
)

log = logging.getLogger(__name__)


class MaterialChange(BaseModel):
    node: str
    material: str = ""
    family: str
    keyword: str = ""
    reason: str  # untouched | impossible
    metallic: tuple[float, float]  # before, after
    roughness: tuple[float, float]


class NormaliseReport(BaseModel):
    glb_out: str = ""
    changes: list[MaterialChange] = Field(default_factory=list)
    n_materials: int = 0
    n_unchanged: int = 0
    n_unresolved: int = 0
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = 0

    def changed(self) -> bool:
        return bool(self.changes)

    def table(self) -> str:
        if not self.changes:
            return "no material needed normalising"
        rows = [f"{'node':22} {'family':15} {'why':11} metallic      roughness"]
        for c in self.changes:
            rows.append(f"{c.node[:22]:22} {c.family:15} {c.reason:11} "
                        f"{c.metallic[0]:.2f}→{c.metallic[1]:.2f}   {c.roughness[0]:.2f}→{c.roughness[1]:.2f}")
        return "\n".join(rows)


def _plan_materials(plan: StaticPlan | None) -> dict[str, str]:
    """snake(part name) → the plan's ``material`` string for that part."""
    if plan is None:
        return {}
    return {to_snake(p.name): (p.material or p.description or "") for p in plan.parts}


def _factors(mat: object) -> tuple[float | None, float | None]:
    m = getattr(mat, "metallicFactor", None)
    r = getattr(mat, "roughnessFactor", None)
    return (None if m is None else float(m)), (None if r is None else float(r))


def _base_rgb(mat: object) -> tuple[float, float, float] | None:
    """``baseColorFactor`` as 0..1 RGB (trimesh stores it as 0-255 RGBA)."""
    f = getattr(mat, "baseColorFactor", None)
    if f is None or len(f) < 3:
        return None
    vals = [float(c) for c in f[:3]]
    scale = 255.0 if max(vals) > 1.0 else 1.0
    return (vals[0] / scale, vals[1] / scale, vals[2] / scale)


def _has_texture(mat: object) -> bool:
    return any(getattr(mat, k, None) is not None
               for k in ("baseColorTexture", "metallicRoughnessTexture", "normalTexture"))


class Verdict(NamedTuple):
    family: str
    reason: str      # untouched | impossible
    keyword: str
    metallic: float
    roughness: float


def _clamp(v: float, lo: float, hi: float) -> float:
    return round(min(hi, max(lo, v)), 3)


def _evidence(material_name: str, node: str, plan_text: str) -> tuple[FamilyMatch | None, str]:
    """Best family match and WHERE it came from (``material`` > ``node`` > ``plan``)."""
    for tier, text in (("material", material_name), ("node", node), ("plan", plan_text)):
        hit = family_for(text)
        if hit is not None:
            return hit, tier
    return None, ""


def classify(
    node: str, material_name: str, plan_text: str, metallic: float | None, roughness: float | None,
    base_rgb: tuple[float, float, float] | None = None,
) -> Verdict | None:
    """What (if anything) to do with one material.

    ``untouched`` snaps to the family's canonical numbers; ``impossible`` only
    *clamps* the offending factor to the nearest edge of the family's plausible
    band, so an author's deliberate-but-extreme choice survives as far as physics
    allows.  ``None`` = leave it exactly as it is."""
    hit, tier = _evidence(material_name, node, plan_text)
    if hit is None:
        return None
    family = hit.family
    if looks_painted(family, base_rgb):
        # a saturated colour on a "cast iron" part means paint over the iron
        family = "painted_wood" if family == "cast_iron" and (base_rgb or (0, 0, 0))[1] > 0.5 else "painted_metal"
    target = pbr_for(family)
    if target is None:
        return None
    if is_framework_default(metallic, roughness):
        return Verdict(target.family, "untouched", hit.keyword, target.metallic, target.roughness)
    if tier == "plan":
        # the plan's prose covers a whole part, not one material: it is good enough to
        # spot a never-configured default, never good enough to overrule real numbers
        return None
    bad = out_of_band(target, metallic, roughness)
    if not bad:
        return None
    m = 1.0 if metallic is None else float(metallic)
    r = 1.0 if roughness is None else float(roughness)
    return Verdict(
        target.family, "impossible", hit.keyword,
        _clamp(m, *target.metallic_band) if "metallic" in bad else m,
        _clamp(r, *target.roughness_band) if "roughness" in bad else r,
    )


def normalise_materials(
    glb_in: Path | str, glb_out: Path | str, *, plan: StaticPlan | None = None, write: bool = True
) -> NormaliseReport:
    """Rewrite the flat-default / impossible materials of ``glb_in`` into ``glb_out``.

    Geometry, node names, UVs and base colours are untouched; only
    ``metallicFactor`` / ``roughnessFactor`` move.  When nothing needs changing the
    output is not written and ``report.glb_out`` stays empty.
    """
    t0 = time.time()
    glb_in, glb_out = Path(glb_in), Path(glb_out)
    scene = load_scene(glb_in)
    rep = NormaliseReport()
    plan_mats = _plan_materials(plan)
    seen: dict[int, tuple[float, float] | None] = {}
    for node in scene.graph.nodes_geometry:
        _, geom_name = scene.graph.get(node)
        geom = scene.geometry.get(geom_name) if geom_name else None
        mat = getattr(getattr(geom, "visual", None), "material", None)
        if mat is None:
            rep.n_unresolved += 1
            continue
        if id(mat) in seen:  # a material shared by several nodes is normalised once
            continue
        rep.n_materials += 1
        if _has_texture(mat):
            rep.n_unchanged += 1
            seen[id(mat)] = None
            continue
        metallic, roughness = _factors(mat)
        snake = to_snake(node)
        plan_text = plan_mats.get(snake) or plan_mats.get(to_snake(node.split("__", 1)[0])) or ""
        verdict = classify(node, str(getattr(mat, "name", "") or ""), plan_text, metallic, roughness,
                           _base_rgb(mat))
        if verdict is None:
            rep.n_unchanged += 1
            seen[id(mat)] = None
            continue
        before = (1.0 if metallic is None else metallic, 1.0 if roughness is None else roughness)
        mat.metallicFactor = float(verdict.metallic)
        mat.roughnessFactor = float(verdict.roughness)
        seen[id(mat)] = (verdict.metallic, verdict.roughness)
        rep.changes.append(MaterialChange(
            node=node, material=str(getattr(mat, "name", "") or ""), family=verdict.family,
            keyword=verdict.keyword, reason=verdict.reason,
            metallic=(round(before[0], 3), verdict.metallic), roughness=(round(before[1], 3), verdict.roughness),
        ))
    if rep.changes and write:
        glb_out.parent.mkdir(parents=True, exist_ok=True)
        scene.export(glb_out)
        rep.glb_out = str(glb_out)
        rep.warnings.extend(_verify(glb_in, glb_out))
    rep.duration_ms = int((time.time() - t0) * 1000)
    return rep


def _verify(glb_in: Path, glb_out: Path) -> list[str]:
    """Same nodes, same triangles — this pass may only move two floats per material."""
    warnings: list[str] = []
    a, b = load_scene(glb_in), load_scene(glb_out)
    na, nb = set(a.graph.nodes_geometry), set(b.graph.nodes_geometry)
    if na != nb:
        warnings.append(f"node set changed: missing={sorted(na - nb)[:5]} extra={sorted(nb - na)[:5]}")
    fa = sum(len(g.faces) for g in a.geometry.values() if isinstance(g, trimesh.Trimesh))
    fb = sum(len(g.faces) for g in b.geometry.values() if isinstance(g, trimesh.Trimesh))
    if fa != fb:
        warnings.append(f"face count changed {fa} → {fb}")
    return warnings


MATERIALS_GLB = "object_materials.glb"


class MaterialPassReport(BaseModel):
    """What :func:`material_pass` did, and whether it shipped."""

    normalise: NormaliseReport
    gate: Any | None = None
    glb_out: str = ""
    shipped: bool = False
    delta: float | None = None
    materials_delta: float | None = None
    reason: str = ""
    duration_s: float = 0.0

    def summary(self) -> dict[str, Any]:
        return {
            "shipped": self.shipped, "delta": self.delta, "materials_delta": self.materials_delta,
            "changed": len(self.normalise.changes), "materials": self.normalise.n_materials,
            "families": sorted({c.family for c in self.normalise.changes}),
            "glb_out": self.glb_out, "reason": self.reason, "duration_s": self.duration_s,
        }


def material_pass(
    ws: Any,
    spec: Any,
    plan: StaticPlan | None,
    *,
    glb_in: Path | None = None,
    judge: bool = True,
    judge_obj: Any | None = None,
    judge_model_id: str | None = None,
    rubric: str | None = None,
    render: Any | None = None,
    events: Any | None = None,
) -> MaterialPassReport:
    """Normalise ``artifacts/object.glb``'s materials into ``artifacts/object_materials.glb``
    and keep it only if the same do-no-harm gate the texture pass uses agrees.

    Standalone sibling of :func:`codeverse.texturing.run.texture_pass`: no model
    call except the two gate verdicts, and nothing is written when no material
    needed changing.
    """
    from codeverse.events import EventLog
    from codeverse.texturing.gate import judge_gate

    t0 = time.time()
    events = events or EventLog(ws.events_path)
    glb_in = Path(glb_in) if glb_in else ws.artifacts / "object.glb"
    if not glb_in.is_file():
        raise FileNotFoundError(f"no GLB to normalise: {glb_in}")
    out = ws.artifacts / MATERIALS_GLB
    norm = normalise_materials(glb_in, out, plan=plan)
    rep = MaterialPassReport(normalise=norm, glb_out=norm.glb_out)
    events.emit("material.normalised", changed=len(norm.changes), materials=norm.n_materials,
                families=sorted({c.family for c in norm.changes}))
    if not norm.changes:
        rep.reason = "no material needed normalising"
        rep.duration_s = round(time.time() - t0, 2)
        return rep
    if not judge:
        rep.shipped = True
        rep.reason = "judge gate skipped: shipped on the deterministic rules alone"
        rep.duration_s = round(time.time() - t0, 2)
        return rep
    gate = judge_gate(spec, plan, glb_in, out, ws.artifacts / "materials_gate",
                      judge=judge_obj if judge_obj is not None else _gate_judge(spec, rubric, judge_model_id),
                      render=render)
    rep.gate, rep.shipped, rep.delta = gate, gate.shipped, gate.delta
    rep.materials_delta, rep.reason = gate.materials_delta, gate.reason
    rep.duration_s = round(time.time() - t0, 2)
    events.emit("material.gate", **rep.summary())
    return rep


def _gate_judge(spec: Any, rubric: str | None, judge_model_id: str | None) -> Any:
    from codeverse.contracts.common import TRACK_INFO
    from codeverse.judges.vlm_judge import VlmJudge

    info = TRACK_INFO.get(spec.track)
    name = rubric or (info.rubric if info is not None else "static_object_v1")
    return VlmJudge(rubric=name, model_id=judge_model_id or spec.backends.judge, n_samples=1,
                    label="material_gate")


__all__ = ["MaterialChange", "MaterialPassReport", "NormaliseReport", "Verdict", "classify",
           "material_pass", "normalise_materials"]
