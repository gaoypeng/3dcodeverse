"""Plans: the planner's structured decomposition.  Every plan carries an
*acceptance checklist* — concrete, verifiable items derived from the spec —
so acceptance can be mechanical (evidence), not an opinion.

Plans are written by an LLM under a JSON schema and then VALIDATED IN CODE
(unit axes, single-root joint trees, sane limits, unique names).  Invalid
plans are re-asked with the validation errors, before any generator runs.
"""

from __future__ import annotations

import contextlib
import math
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from codeverse3d.contracts.common import (
    MimicSpec,
    Vec3,
    mimic_issues,
)
from codeverse3d.conventions import to_snake


class BBox(BaseModel):
    """Axis-aligned box in the plan's frame: centre + full extents (meters)."""

    center: Vec3
    extents: Vec3

    @property
    def min(self) -> tuple[float, float, float]:
        return tuple(c - e / 2 for c, e in zip(self.center, self.extents, strict=True))  # type: ignore[return-value]

    @property
    def max(self) -> tuple[float, float, float]:
        return tuple(c + e / 2 for c, e in zip(self.center, self.extents, strict=True))  # type: ignore[return-value]


class AcceptanceItem(BaseModel):
    """One verifiable requirement.  ``how`` says which evidence proves it."""

    id: str
    text: str
    how: Literal["measure", "visual", "probe", "articulation"] = "visual"
    priority: Literal["must", "should"] = "must"


#: how far a sub-part may stick out of its parent's bbox before the plan is rejected:
#: ``max(SUBPART_SLACK_M, SUBPART_REL_SLACK × parent extent)`` per axis.  Planner boxes are
#: design intent, not measurements, so the check catches "this child is somewhere else
#: entirely", never a 3 mm rounding.
SUBPART_SLACK_M = 0.01
SUBPART_REL_SLACK = 0.15
#: A ``revolute`` range over 2π whose limits both sit inside ±``DEGREES_MAX_ABS`` and whose span is
#: at least ``DEGREES_MIN_SPAN`` was written in DEGREES (door ``0..90``, lid ``-180..0``): 30 rad is
#: nearly five turns, which no hinge is planned in.  A span between 2π and 30 (``0..4π``, ``0..6.5``,
#: ``0..10``) is read as radians — an implausible hinge either way — and becomes ``continuous``
#: (``ArticulatedPlan._normalise_raw``).
DEGREES_MAX_ABS = 360.0
DEGREES_MIN_SPAN = 30.0


class SubPartPlan(BaseModel):
    """One sub-part of a :class:`PartPlan` — depth 1 by construction (a sub-part has no
    children of its own, so a cycle cannot be expressed).

    Sub-parts are a PLANNING device, not export nodes: the parent part is still exactly ONE
    named object in the GLB and the contract gate still checks exactly the parent's bbox.
    They exist so a part a human would call an *assembly* ("burr mechanism", "pegbox",
    "belt housing") can be planned as the 3-6 shapes it really is — which is where the
    measured judge reward lives — without adding contact surfaces the assembly gates must
    police.  See ``docs/EVAL.md`` / the complexity baseline: built-parts ÷ planned-parts is
    the only complexity metric with a positive partial correlation to geometry_detail."""

    name: str = Field(description="PascalCase, unique within the parent, e.g. Burr")

    @field_validator("name")
    @classmethod
    def _no_model_leak(cls, v: str) -> str:
        return reject_model_leak(v)
    role: str = Field(default="", description="what this sub-part is, a few words")
    description: str = Field(description="shape and construction in numbers")
    bbox: BBox = Field(description="inside the parent's bbox, same frame")
    material: str = Field(default="", description="material / finish when it differs from the parent's")
    instances: int = Field(default=1, ge=1, description="identical copies inside the parent")


class PartPlan(BaseModel):
    name: str = Field(description="PascalCase unique part name, e.g. SeatCushion")

    @field_validator("name")
    @classmethod
    def _no_model_leak(cls, v: str) -> str:
        return reject_model_leak(v)
    role: str = Field(description="what this part is / does, one line")
    description: str = Field(description="shape, construction and visible detail the builder must realise")
    bbox: BBox
    material: str = Field(default="", description="material / finish in plain words")
    attach_to: str | None = Field(default=None, description="parent part name this part touches")
    symmetry: Literal["none", "mirror_x", "mirror_y", "radial"] = "none"
    instances: int = Field(default=1, ge=1, description="identical copies (e.g. 4 legs)")
    children: list[SubPartPlan] = Field(
        default_factory=list, max_length=8,
        description="sub-parts of an ASSEMBLY part (a mechanism, a housing, a head) — still ONE named "
                    "object in the export; leave empty for a simple part such as a leg")
    detail_hint: str = Field(
        default="", description="one line telling the builder what makes THIS part read as real "
                                "(e.g. 'carries the visible mechanism — model the burrs, the shaft and the nut')")

    @property
    def leaf_count(self) -> int:
        """Shapes this part stands for: its own copies, or the sum of its children's."""
        inner = sum(c.instances for c in self.children) or 1
        return self.instances * inner

    @model_validator(mode="after")
    def _children_ok(self) -> PartPlan:
        seen: set[str] = set()
        pmin, pmax = self.bbox.min, self.bbox.max
        for c in self.children:
            key = to_snake(c.name)
            if not key:
                raise ValueError(f"part {self.name}: a sub-part has an empty name")
            if key == to_snake(self.name):
                raise ValueError(f"part {self.name}: sub-part {c.name} repeats its parent's name")
            if key in seen:
                raise ValueError(f"part {self.name}: duplicate sub-part name (after normalisation): {c.name}")
            seen.add(key)
            cmin, cmax = c.bbox.min, c.bbox.max
            for axis, lo, hi, clo, chi, ext in zip("xyz", pmin, pmax, cmin, cmax, self.bbox.extents, strict=True):
                slack = max(SUBPART_SLACK_M, SUBPART_REL_SLACK * abs(ext))
                over = max(lo - clo, chi - hi)
                if over > slack:
                    raise ValueError(
                        f"part {self.name}: sub-part {c.name} sticks {over * 1000:.0f} mm out of the parent bbox on "
                        f"{axis} (parent {axis} in [{lo:.3f}, {hi:.3f}], sub-part [{clo:.3f}, {chi:.3f}], "
                        f"allowed slack {slack * 1000:.0f} mm) — either shrink/move the sub-part or grow the parent bbox")
        return self


class StaticPlan(BaseModel):
    object_name: str
    summary: str
    overall_bbox: BBox
    style_notes: str = ""
    parts: list[PartPlan] = Field(min_length=1)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> StaticPlan:
        seen: set[str] = set()
        for p in self.parts:
            key = to_snake(p.name)
            if key in seen:
                raise ValueError(f"duplicate part name (after normalisation): {p.name}")
            seen.add(key)
        names = {to_snake(p.name) for p in self.parts}
        for p in self.parts:
            if p.attach_to and to_snake(p.attach_to) not in names:
                raise ValueError(f"part {p.name} attaches to unknown part {p.attach_to}")
        dotted: set[str] = set()
        for p in self.parts:
            for c in p.children:
                key = f"{to_snake(p.name)}.{to_snake(c.name)}"
                if key in dotted:  # pragma: no cover — PartPlan._children_ok already rejects this
                    raise ValueError(f"duplicate sub-part name {p.name}.{c.name}")
                dotted.add(key)
                if to_snake(c.name) in names:
                    raise ValueError(f"sub-part {p.name}.{c.name} has the same name as top-level part {c.name}; "
                                     "either rename it or promote it to a top-level part")
        return self

    @property
    def leaf_count(self) -> int:
        """Total shapes the plan asks for (parts × instances, sub-parts counted individually)."""
        return sum(p.leaf_count for p in self.parts)


class MimicPlan(BaseModel):
    """This joint is driven by another: ``q = multiplier * q[joint] + offset``.

    A coupled mechanism (umbrella ribs on one runner, a pantograph, a tambour) has ONE
    input and many moving links.  Declaring the coupling lets the sweep pose it the way
    it really moves; without it every link is driven independently."""

    joint: str = Field(description="the joint this one follows, by name")
    multiplier: float = Field(default=1.0, description="q_this = multiplier * q_that + offset")
    offset: float = Field(default=0.0)

class JointPlan(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _no_model_leak(cls, v: str) -> str:
        return reject_model_leak(v)
    type: Literal["revolute", "prismatic", "continuous", "fixed"]
    parent: str = Field(description="parent link (part) name")
    child: str = Field(description="child link (part) name")
    axis: Vec3 = Field(description="unit axis in the plan frame")
    pivot: Vec3 = Field(description="a point on the joint axis, WORLD coords (rest pose)")
    lower: float = Field(default=0.0, description="rad for revolute, m for prismatic")
    upper: float = Field(default=0.0)
    rest: float = Field(default=0.0, description="joint value in the authored rest pose")
    motion: str = Field(default="", description="what moving this joint does, one line")
    mimic: MimicPlan | None = Field(default=None, description="set when this joint is driven by another")

    @model_validator(mode="after")
    def _sane(self) -> JointPlan:
        n = math.sqrt(sum(a * a for a in self.axis))
        if n < 1e-6:
            raise ValueError(f"joint {self.name}: zero axis")
        if abs(n - 1.0) > 1e-3:
            self.axis = tuple(a / n for a in self.axis)  # type: ignore[assignment]
        if self.type in ("revolute", "prismatic"):
            if self.upper < self.lower:
                raise ValueError(f"joint {self.name}: upper < lower")
            if not (self.lower - 1e-9 <= self.rest <= self.upper + 1e-9):
                raise ValueError(f"joint {self.name}: rest {self.rest} outside [{self.lower},{self.upper}]")
            if self.type == "revolute" and (self.upper - self.lower) > 2 * math.pi + 1e-6:
                raise ValueError(f"joint {self.name}: revolute range > 2π, use continuous")
            if self.type == "prismatic" and (self.upper - self.lower) > 5.0:
                raise ValueError(f"joint {self.name}: prismatic range > 5 m is implausible")
        if to_snake(self.parent) == to_snake(self.child):
            # name BOTH sides: pydantic truncates the offending value right after the
            # joint name, so "parent == child" was all the model ever saw, and it rewrote
            # the same joint through every re-ask (7 runs lost that way, 2026-09-03).
            same = "" if self.parent == self.child else (
                f" ('{self.parent}' and '{self.child}' are the same name once normalised)")
            raise ValueError(
                f"joint {self.name}: parent and child are both '{self.parent}'{same} — a joint "
                f"connects TWO different links; name the moving link as child and what it is "
                f"attached to as parent, or drop the joint if nothing moves")
        return self


def _looks_like_degrees(lower: float, upper: float) -> bool:
    """Revolute limits the planner wrote in degrees: inside ±360 with a span of at least 30 (see the constants)."""
    return abs(lower) <= DEGREES_MAX_ABS and abs(upper) <= DEGREES_MAX_ABS and upper - lower >= DEGREES_MIN_SPAN


class ArticulatedPlan(StaticPlan):
    root_link: str
    joints: list[JointPlan] = Field(min_length=1)
    normalisations: list[str] = Field(
        default_factory=list,
        description="filled by the harness, leave empty: automatic corrections applied to the planner's answer "
                    "before validation (a sub-part promoted to a link because a joint moves it, revolute limits "
                    "written in degrees converted to radians or a > 2π radian range made continuous, swapped "
                    "or out-of-range limits clamped, a parent bbox grown around a sub-part)")

    @model_validator(mode="before")
    @classmethod
    def _normalise_raw(cls, data: object) -> object:
        """Repair the three planner mistakes that failed validation twice on compare_art_v2
        (2026-08-25: 5 of 14 articulated prompts, a third of the harness's losses) — each is
        a schema-shaped slip a re-ask did not fix, not a design decision worth a run.

        1. a joint whose ``parent`` / ``child`` names a SUB-PART: the sub-part is promoted to a
           top-level part (``attach_to`` = its former parent) — anything a joint moves is a link;
        2. a ``revolute`` joint whose range exceeds 2π: with both limits inside ±360 and a span of
           at least 30 the planner wrote DEGREES (door ``0..90``, lid ``-180..0`` — the template says
           rad), so the limits and a ``rest`` inside them are converted with ``math.radians`` and the
           joint stays ``revolute``; any other span over 2π (``0..4π``, ``-4π..4π``) becomes
           ``continuous`` (limits dropped).  The band 2π < span < 30 (``0..6.5``, ``0..10``) is
           ambiguous and is read as radians: a wrong ``continuous`` there is what the run got before,
           while a wrong degrees reading would squeeze a full turn into a 6° hinge without a trace.
           A converted range that is still over 2π (``-360..360``) falls through to ``continuous``;
        2b. swapped or out-of-range limits: ``upper < lower`` is written as the swap it is
           (a hinge "from 90 to 0"), and a ``rest`` outside ``[lower, upper]`` is clamped to
           the nearer limit — both killed cs37_urdf_02 at the planner (2026-08-29, 3.7-flash)
           after two re-asks, and neither is a design decision worth a dead run;
        3. a sub-part sticking out of its parent's bbox by more than the slack: the parent bbox
           grows to enclose it (planner boxes are design intent, not measurements).
        4. a ``root_link`` that names no part but loosely matches exactly ONE (af_excavator
           2026-08-31, 3.7-flash: ``root_link: chassis`` over a part list that spelt it
           differently — two re-asks did not fix it and the run died at the planner): the
           root is rewritten to that part.  Zero or several candidates still raise.
        Every repair is recorded in ``normalisations`` so the record shows what the planner
        actually wrote.  Anything else still fails validation and is re-asked.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        parts = [dict(p) for p in (data.get("parts") or []) if isinstance(p, dict)]
        joints = [dict(j) for j in (data.get("joints") or []) if isinstance(j, dict)]
        # harness-only: the planner is told to leave this empty, and one did not (articulated_v2
        # scissor_mirror, 2026-08-26: two lines of design prose landed here) — a planner's text
        # would read as a harness repair in the record, so incoming values are dropped
        notes: list[str] = []
        if not parts or not joints:
            return data
        by_name = {to_snake(str(p.get("name", ""))): p for p in parts}

        # 1. joints that move a sub-part → promote it
        referenced = {to_snake(str(j.get(k, ""))) for j in joints for k in ("parent", "child")}
        for part in list(parts):
            kept = []
            for child in part.get("children") or []:
                if not isinstance(child, dict):
                    kept.append(child)
                    continue
                key = to_snake(str(child.get("name", "")))
                if key in referenced and key not in by_name:
                    promoted = {
                        "name": child.get("name"), "role": child.get("role") or f"moving part of {part.get('name')}",
                        "description": child.get("description", ""), "bbox": child.get("bbox"),
                        "material": child.get("material") or part.get("material", ""),
                        "attach_to": part.get("name"), "instances": child.get("instances", 1),
                    }
                    parts.append(promoted)
                    by_name[key] = promoted
                    notes.append(f"promoted sub-part {part.get('name')}.{child.get('name')} to a link: a joint moves it")
                else:
                    kept.append(child)
            part["children"] = kept

        # 1b. a joint that names a link by a UNIQUE fragment of a real part ("Lid" for
        # "ChestLid"): a naming slip, not a design decision.  Measured 2026-08-27
        # (art_med_tool_chest, 3.6-flash): 'joint LidHinge references unknown link(s)
        # Carcass/Lid' three times in a row, killing the run at the planner with 0 rounds.
        known = set(by_name)
        for j in joints:
            for side in ("parent", "child"):
                raw_name = str(j.get(side, ""))
                key = to_snake(raw_name)
                if not key or key in known:
                    continue
                hits = [n for n in known if key in n.split("_") or n.endswith(f"_{key}") or n.startswith(f"{key}_")]
                if len(hits) == 1:
                    j[side] = by_name[hits[0]].get("name")
                    notes.append(f"joint {j.get('name')}.{side} '{raw_name}' resolved to the one part it names: {j[side]}")

        # 1c. a joint that names a SUB-PART by a fragment (af_grandfather_clock 2026-08-31,
        # 3.7-flash: everything nested under clock_case, the joint said GlazedDoor, the child
        # spelt it longer — the exact-match promotion above missed it, 1b searches top-level
        # links only, and the re-ask complaint listed top-level parts only, so both re-asks
        # died the same way).  Sub-part names are longer and decorated, so the affix rule is
        # too weak here: the match is WORD-SUBSET (every word of the reference appears in the
        # candidate's words — {glazed,door} ⊆ {glazed,front,door}; "arm" still never matches
        # "alarm").  A unique hit promotes the child and rewrites the joint side.
        sub_index: dict[str, tuple[dict, dict]] = {}
        for part in list(parts):
            for child in part.get("children") or []:
                if isinstance(child, dict) and child.get("name"):
                    sub_index[to_snake(str(child["name"]))] = (part, child)
        for j in joints:
            for side in ("parent", "child"):
                raw_name = str(j.get(side, ""))
                key = to_snake(raw_name)
                if not key or key in by_name:
                    continue
                kw = set(key.split("_"))
                hits = [k for k in sub_index if kw <= set(k.split("_"))]
                if len(hits) != 1:
                    continue
                owner, child = sub_index[hits[0]]
                ckey = to_snake(str(child["name"]))
                if ckey not in by_name:
                    promoted = {
                        "name": child.get("name"), "role": child.get("role") or f"moving part of {owner.get('name')}",
                        "description": child.get("description", ""), "bbox": child.get("bbox"),
                        "material": child.get("material") or owner.get("material", ""),
                        "attach_to": owner.get("name"), "instances": child.get("instances", 1),
                    }
                    parts.append(promoted)
                    by_name[ckey] = promoted
                    with contextlib.suppress(ValueError, KeyError):
                        owner["children"].remove(child)
                    notes.append(f"promoted sub-part {owner.get('name')}.{child.get('name')} to a link: "
                                 f"joint {j.get('name')} names it")
                j[side] = by_name[ckey].get("name")
                notes.append(f"joint {j.get('name')}.{side} '{raw_name}' resolved to sub-part {j[side]}")

        # 2. revolute joints with a > 2π range: degrees written for radians → radians; a radian
        #    range over 2π → continuous
        for j in joints:
            if j.get("type") != "revolute":
                continue
            try:
                lower, upper = float(j.get("lower", 0.0)), float(j.get("upper", 0.0))
            except (TypeError, ValueError):
                continue
            if upper - lower <= 2 * math.pi + 1e-6:
                continue
            if _looks_like_degrees(lower, upper):
                j.update(lower=math.radians(lower), upper=math.radians(upper))
                try:
                    rest = float(j.get("rest", 0.0))
                except (TypeError, ValueError):
                    rest = None
                if rest is not None and lower - 1e-9 <= rest <= upper + 1e-9:
                    j["rest"] = math.radians(rest)
                notes.append(f"joint {j.get('name')}: limits looked like degrees ({lower:g}..{upper:g}) → radians")
                lower, upper = float(j["lower"]), float(j["upper"])
            span = upper - lower
            if span > 2 * math.pi + 1e-6:
                j.update(type="continuous", lower=0.0, upper=0.0, rest=0.0)
                notes.append(f"joint {j.get('name')}: revolute range {span:.2f} rad > 2π → continuous")

        # 2b. swapped or out-of-range limits → swap / clamp (see the docstring)
        for j in joints:
            if j.get("type") not in ("revolute", "prismatic"):
                continue
            try:
                lower, upper = float(j.get("lower", 0.0)), float(j.get("upper", 0.0))
            except (TypeError, ValueError):
                continue
            if upper < lower:
                j["lower"], j["upper"] = upper, lower
                notes.append(f"joint {j.get('name')}: limits swapped ({lower:g}..{upper:g} → {upper:g}..{lower:g})")
                lower, upper = upper, lower
            try:
                rest = float(j.get("rest", 0.0))
            except (TypeError, ValueError):
                continue
            if rest < lower - 1e-9 or rest > upper + 1e-9:
                j["rest"] = min(max(rest, lower), upper)
                notes.append(f"joint {j.get('name')}: rest {rest:g} outside [{lower:g},{upper:g}] → clamped to {j['rest']:g}")

        # 3. sub-parts outside the parent bbox → grow the parent
        for part in parts:
            bbox = part.get("bbox")
            if not (isinstance(bbox, dict) and isinstance(bbox.get("center"), (list, tuple))
                    and isinstance(bbox.get("extents"), (list, tuple))):
                continue
            try:
                lo = [float(c) - float(e) / 2 for c, e in zip(bbox["center"], bbox["extents"], strict=True)]
                hi = [float(c) + float(e) / 2 for c, e in zip(bbox["center"], bbox["extents"], strict=True)]
            except (TypeError, ValueError):
                continue
            grown = False
            for child in part.get("children") or []:
                cb = child.get("bbox") if isinstance(child, dict) else None
                if not (isinstance(cb, dict) and isinstance(cb.get("center"), (list, tuple))
                        and isinstance(cb.get("extents"), (list, tuple))):
                    continue
                try:
                    clo = [float(c) - float(e) / 2 for c, e in zip(cb["center"], cb["extents"], strict=True)]
                    chi = [float(c) + float(e) / 2 for c, e in zip(cb["center"], cb["extents"], strict=True)]
                except (TypeError, ValueError):
                    continue
                for a in range(3):
                    slack = max(SUBPART_SLACK_M, SUBPART_REL_SLACK * abs(hi[a] - lo[a]))
                    if lo[a] - clo[a] > slack or chi[a] - hi[a] > slack:
                        lo[a], hi[a] = min(lo[a], clo[a]), max(hi[a], chi[a])
                        grown = True
                        notes.append(f"part {part.get('name')}: bbox grown on {'xyz'[a]} around sub-part "
                                     f"{child.get('name')}")
            if grown:
                part["bbox"] = {"center": [(lo[a] + hi[a]) / 2 for a in range(3)],
                                "extents": [hi[a] - lo[a] for a in range(3)]}

        # 4. root_link that names no part: loose word-match against the part list
        root = data.get("root_link")
        if isinstance(root, str) and root.strip():
            names = {to_snake(p.get("name", "")): p.get("name") for p in parts if p.get("name")}
            rk = to_snake(root)
            if rk not in names:
                # same word-boundary rule as repair 1b, never bare substring ("arm" != "alarm")
                hits = [orig for k, orig in names.items()
                        if rk in k.split("_") or k.endswith(f"_{rk}") or k.startswith(f"{rk}_")]
                if len(hits) == 1:
                    notes.append(f"root_link '{root}' named no part; rewrote to '{hits[0]}'")
                    data["root_link"] = hits[0]
                elif not hits:
                    # 4b. nothing even loosely matches (a UUID, prose — ab_repairs
                    #     grand_piano 2026-08-29, three plans in a row): the one part no
                    #     joint names as a child is the root; unique → unambiguous
                    children = {to_snake(str(j.get("child", ""))) for j in joints}
                    roots = [orig for k, orig in names.items() if k not in children]
                    if len(roots) == 1:
                        notes.append(f"root_link '{root}' named no part; rewrote to the one link no joint moves: '{roots[0]}'")
                        data["root_link"] = roots[0]

        data["parts"], data["joints"], data["normalisations"] = parts, joints, notes
        return data

    @model_validator(mode="after")
    def _tree(self) -> ArticulatedPlan:
        links = {to_snake(p.name) for p in self.parts}
        root = to_snake(self.root_link)
        if root not in links:
            raise ValueError(f"root_link {self.root_link} is not a part")
        parent_of: dict[str, str] = {}
        for j in self.joints:
            p, c = to_snake(j.parent), to_snake(j.child)
            if p not in links or c not in links:
                unknown = ", ".join(n for n, k in ((j.parent, p), (j.child, c)) if k not in links)
                subs = sorted({c.name for part in self.parts for c in part.children})
                sub_note = (f" Sub-parts that exist but are NOT links: {', '.join(subs)} — a joint may "
                            f"only move a top-level part; name one of those exactly to promote it, or a real part."
                            if subs else "")
                raise ValueError(
                    f"joint {j.name} references unknown link(s) {unknown} — the parts in this plan are: "
                    f"{', '.join(sorted(links))}. Use those exact names (or add the missing part).{sub_note}")
            if c in parent_of:
                raise ValueError(f"link {j.child} has two parent joints")
            if c == root:
                raise ValueError(f"root link {self.root_link} cannot be a joint child")
            parent_of[c] = p
        # every non-root link must reach root
        for link in links - {root}:
            seen, cur = set(), link
            while cur != root:
                if cur in seen or cur not in parent_of:
                    raise ValueError(f"link {link} is not connected to root {self.root_link} (single-root tree required)")
                seen.add(cur)
                cur = parent_of[cur]
        self._check_mimics()
        return self

    def _check_mimics(self) -> None:
        """Every declared coupling must name a joint that exists, moves, and does not lead
        back to the joint that follows it — the rules are ``common.mimic_issues`` so the
        plan, the lint and the URDF loader cannot drift apart."""
        by_name = {j.name: j for j in self.joints}
        issues = mimic_issues([
            MimicSpec(key=to_snake(j.name), name=j.name, movable=j.type != "fixed",
                      target=to_snake(j.mimic.joint) if j.mimic else None,
                      multiplier=j.mimic.multiplier if j.mimic else 1.0)
            for j in self.joints])
        for i in issues:
            follower = by_name.get(i.joint)
            wanted = follower.mimic.joint if follower is not None and follower.mimic else i.target
            if i.kind == "out_of_range":
                continue  # the plan does not reject a coupling for overshooting a limit;
                          # the lint warns (contracts.common._driven_range says why)
            raise ValueError({
                "immobile": f"joint {i.joint}: a fixed joint cannot mimic {wanted}",
                "zero_multiplier": f"mimic of {wanted}: multiplier 0 means the joint cannot move; use type=fixed",
                "self": f"joint {i.joint}: mimics itself",
                "unknown_target": f"joint {i.joint}: mimic joint {wanted} is not a joint in this plan; "
                                  f"the joints are: {', '.join(sorted(x.name for x in self.joints))}",
                "immobile_target": f"joint {i.joint}: mimics {i.target}, which is fixed and never moves",
                "cycle": f"joint {i.joint}: mimic chain loops back through {i.detail}",
            }[i.kind])


class ZonePlan(BaseModel):
    name: str = Field(description="PascalCase zone name, e.g. Harbour")
    description: str
    bbox: BBox
    contents: list[str] = Field(default_factory=list, description="asset names placed in this zone")


class AssetPlan(BaseModel):
    name: str = Field(description="PascalCase asset name")
    kind: Literal["threejs", "blender_glb"] = Field(
        description="threejs = procedural module; blender_glb = built with bpy and compiled to a GLB"
    )
    description: str
    approx_size_m: Vec3
    instances_hint: int = 1


class EffectPlan(BaseModel):
    name: str
    kind: Literal["glsl_material", "postprocess", "particles", "animated_geometry"]
    description: str
    target: str = Field(default="", description="zone / asset / scene it applies to")


#: a camera name becomes a render FILENAME (``render_scene.mjs`` writes ``<name>_<t>.png``);
#: the charset ``render_glb.mjs`` already enforces for view names, plus a length bound.
CAMERA_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: a thinking model's self-reference leaked into structured output: measured 2026-08-28
#: (gear_cq_ss), the PLANNER emitted a part literally named "Gemini25FlashThinking" and
#: the generator faithfully modelled a placeholder pillar for it — judged 0.0.  High-
#: precision patterns only: "CameraFlash" or "ModelStand" must stay legal.
_MODEL_NAME_LEAK = re.compile(r"(?i)(gemini|gpt[-_ ]?\d|claude|llama|qwen|deepseek|placeholder)")


def reject_model_leak(v: str) -> str:
    """Reject (never mangle) part names that are model ids / placeholders — the planner
    is re-asked with this error, exactly like an unsafe camera name."""
    m = _MODEL_NAME_LEAK.search(v)
    if m:
        raise ValueError(
            f"part name {v!r} contains {m.group(1)!r} — a model id or placeholder leaked from "
            "thinking, not a component of the object.  Name the real part (e.g. GrinderWheel).")
    return v



class CameraPlan(BaseModel):
    name: str = Field(description="letters/digits/_/- only; it becomes a render filename")
    position: Vec3
    look_at: Vec3
    fov: float = 50.0
    purpose: str = ""

    @field_validator("name")
    @classmethod
    def _filename_safe(cls, v: str) -> str:
        """Reject (never mangle) unsafe names — the planner is re-asked with the error."""
        if not CAMERA_NAME_RE.fullmatch(v):
            raise ValueError(
                f"camera name {v!r} must match [A-Za-z0-9_-]{{1,64}}: it becomes a render "
                "filename (no spaces, dots, slashes or other path characters)")
        return v


class ScenePlan(BaseModel):
    title: str
    summary: str
    setting: str = Field(description="place, era, weather, time of day")
    mood: str = ""
    bounds: BBox
    environment: str = Field(description="sky, sun/moon, fog, ground, water — one paragraph")
    interior: bool = Field(
        default=False,
        description="the view is from INSIDE a built space (workshop, room, cabin, hall, shop): the "
        "environment module owns the enclosure — floor, walls and ceiling on the bounds' faces with "
        "openings where the windows and doors are — and the zones dress the inside; false outdoors",
    )
    zones: list[ZonePlan] = Field(min_length=1)
    assets: list[AssetPlan] = Field(default_factory=list)
    effects: list[EffectPlan] = Field(default_factory=list)
    animation: list[str] = Field(default_factory=list, description="what moves and how")
    cameras: list[CameraPlan] = Field(min_length=1, max_length=6)
    acceptance: list[AcceptanceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names(self) -> ScenePlan:
        assets = {to_snake(a.name) for a in self.assets}
        zones = set()
        for z in self.zones:
            k = to_snake(z.name)
            if k in zones:
                raise ValueError(f"duplicate zone {z.name}")
            zones.add(k)
            for c in z.contents:
                if to_snake(c) not in assets:
                    raise ValueError(f"zone {z.name} lists unknown asset {c}")
        return self


class ZonePlacement(BaseModel):
    """One asset's placement inside a zone, decided by the L2 zone director.

    Typed rows with concrete numbers on purpose (the RefDimension lesson: an
    open-ended dict maps to a property-less schema and the model answers ``{}``)."""

    asset: str = Field(description="asset name from the plan")
    count: int = Field(ge=1, description="how many instances in this zone")
    cluster: tuple[float, float] = Field(description="cluster centre (x, z) in WORLD meters, inside the zone bbox")
    spread_m: float = Field(ge=0, description="radius the instances scatter within (0 = exactly at the centre)")
    faces: str = Field(default="", description="what the instances face, e.g. 'the path', 'azimuth 220'")
    support: str = Field(default="ground", description="'ground' or the asset they stand on")


class ZoneLayout(BaseModel):
    """The L2 layout for ONE zone: where its planned contents actually go.

    Produced by a cheap structured call per zone (parallel, never an agent),
    validated deterministically against the zone bbox and the plan before it is
    handed to the zone builder — the builder realises a layout instead of
    inventing one."""

    zone: str = Field(description="the zone's name, exactly as planned")
    placements: list[ZonePlacement] = Field(default_factory=list)
    path_points: list[tuple[float, float]] = Field(
        default_factory=list, description="(x, z) polyline of the walkway through this zone, if any")
    mid_props: int = Field(default=0, ge=0, description="loose mid props (0.3-1.5 m) beyond the placements")
    small_props: int = Field(default=0, ge=0, description="small props (< 0.3 m)")
    ground_cover: int = Field(default=0, ge=0, description="instanced tufts / pebbles")
    notes: str = Field(default="", description="one line of layout intent, e.g. 'stalls face the lane'")


class RefDimension(BaseModel):
    """One reference dimension of the real object.  A LIST of typed rows, not a free
    ``dict[str, float]``: an open-ended object maps to a property-less ``{"type":
    "object"}`` in the Gemini structured-output schema, and the model then answers ``{}``
    every time (measured on the first live run of this wave)."""

    name: str = Field(description="width | depth | height | length | diameter | wall_thickness | …")
    meters: float = Field(description="the real-world size in METERS")


class SubAssembly(BaseModel):
    """One sub-assembly a real instance of the requested object has."""

    name: str = Field(description="what a catalogue would call it, e.g. 'burr mechanism'")
    purpose: str = Field(default="", description="what it does, one line")
    parts: list[str] = Field(default_factory=list, max_length=10,
                             description="the parts it is made of, named as a fitter would name them")
    material: str = Field(default="", description="dominant material / finish")


class EngineeringBrief(BaseModel):
    """The expanded engineering brief for ONE request: what a person who has actually held
    the object knows about it.  Produced by a cheap model call *before* planning
    (``tracks/brief.py``) and folded into the plan; never a deliverable of its own.

    The load-bearing fields are REQUIRED on purpose.  Measured on the first live run: with
    every field defaulted, gemini-3.7-flash answered three of four requests with
    ``sub_assemblies: []`` and ``signature_features: []`` — a schema that lets the model
    say nothing gets nothing.  Only the two genuinely-sometimes-empty lists keep defaults.
    """

    object_name: str = Field(description="PascalCase name of the thing")
    reference: str = Field(description="the real-world reference instance the numbers come from")
    one_line: str = Field(description="what it is, one sentence")
    dimensions_m: list[RefDimension] = Field(
        max_length=12, description="4-10 real-world reference dimensions in METERS, the small ones included")
    sub_assemblies: list[SubAssembly] = Field(max_length=10, description="3-8 assemblies a fitter would name")
    mechanism: str = Field(description="how it works / how force or material moves through it")
    visible_from_outside: list[str] = Field(
        max_length=16, description="8-16 things a viewer actually sees from outside — the only things worth modelling")
    signature_features: list[str] = Field(
        max_length=6, description="3-6 features a viewer uses to recognise it — absence makes it the wrong object")
    materials: list[str] = Field(max_length=12, description="'<part>: <material, colour, finish>' lines, 6-12 of them")
    hidden_inside: list[str] = Field(default_factory=list, max_length=10,
                                     description="real parts that are NOT visible and must not be modelled")
    not_present: list[str] = Field(default_factory=list, max_length=10,
                                   description="things a naive model would wrongly add")

    @property
    def is_useful(self) -> bool:
        """A brief with no assemblies AND no signature features tells the planner nothing —
        the caller discards it rather than pasting an empty block into the prompt."""
        return len(self.sub_assemblies) >= 2 or len(self.signature_features) >= 2


class PassPlan(BaseModel):
    name: str = Field(description="PascalCase pass name, e.g. Clouds, Bloom")
    kind: Literal["fullscreen", "geometry", "postprocess", "feedback"] = "fullscreen"
    description: str = Field(description="what this pass draws / computes, concrete")
    elements: list[str] = Field(
        default_factory=list, max_length=8,
        description="named things this pass draws, each with its numbers "
                    "(e.g. '7 gear wheels, 12-24 teeth, radii 0.06-0.22 screen units')")
    detail_hint: str = Field(default="", description="one line: what makes THIS pass read as real")


class GraphicsPlan(BaseModel):
    """Plan for the graphics track (GLSL shader / raw OpenGL program)."""

    title: str
    summary: str
    style: str = Field(description="visual style: palette, mood, references in words")
    resolution: tuple[int, int] = (1280, 720)
    duration_s: float = Field(default=8.0, description="loop length for judging / video")
    passes: list[PassPlan] = Field(min_length=1)
    uniforms: list[str] = Field(default_factory=list, description="uniform names the program exposes (u_time, u_resolution, ...)")
    motion: str = Field(default="", description="what animates over time and how")
    key_visuals: list[str] = Field(default_factory=list, description="3-8 visible elements a viewer must recognise")
    acceptance: list[AcceptanceItem] = Field(default_factory=list)


Plan = StaticPlan | ArticulatedPlan | ScenePlan | GraphicsPlan
