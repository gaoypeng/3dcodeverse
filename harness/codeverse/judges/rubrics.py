"""Rubrics: typed criteria + weights + floors + deterministic caps, loaded from YAML.

A rubric is data, not prose: every criterion carries *anchored* descriptions
(what 1.0 / 0.7 / 0.4 / 0.1 look like) so two judges read the same scale, an
optional hard ``floor`` (a criterion below its floor fails the verdict no matter
the weighted mean) and a ``kind`` (``visual`` = scored by the VLM,
``measured`` = scored in code and merely *shown* to the VLM).

``caps`` are rules that bound the overall score from deterministic gate
findings (build error → 0.0, floating part → ≤ 0.6 ...) — see ``caps.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from codeverse.contracts.artifacts import Severity

RUBRICS_DIR = Path(__file__).resolve().parent / "rubrics"
ANCHOR_LEVELS = ("1.0", "0.7", "0.4", "0.1")
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


class RubricError(ValueError):
    """Raised for unknown / malformed rubrics."""


class Criterion(BaseModel):
    id: str
    weight: float = Field(gt=0.0, le=1.0)
    title: str = ""
    description: str = Field(description="what this criterion judges, one paragraph")
    anchors: dict[str, str] = Field(description="score level → what it looks like; keys 1.0/0.7/0.4/0.1")
    floor: float | None = Field(default=None, ge=0.0, le=1.0, description="hard floor: below → verdict fails")
    kind: Literal["visual", "measured"] = "visual"

    @field_validator("id")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not _IDENT.match(v):
            raise ValueError(f"criterion id must be snake_case identifier, got {v!r}")
        return v

    @field_validator("anchors")
    @classmethod
    def _anchors(cls, v: dict[str, str]) -> dict[str, str]:
        v = {str(k): str(t).strip() for k, t in v.items()}
        missing = [lvl for lvl in ANCHOR_LEVELS if lvl not in v]
        if missing:
            raise ValueError(f"anchors missing levels {missing}; need {ANCHOR_LEVELS}")
        return v

    @property
    def label(self) -> str:
        return self.title or self.id.replace("_", " ")


class CapRule(BaseModel):
    """Bounds the overall score when a deterministic signal fires.

    ``when="gate"`` rules match ``GateFinding`` records: ``gate`` is an fnmatch
    pattern on the gate name (``"build*"``), ``severity`` the minimum severity,
    ``kinds`` a list of tokens any of which must appear in ``finding.data.kind``
    / ``finding.data.code`` or, as a fallback, in the lower-cased message.
    ``when="acceptance"`` fires when any ``must`` acceptance item is not verified.
    ``when="console"`` fires when the render set reports console errors (scenes).
    """

    id: str
    cap: float = Field(ge=0.0, le=1.0)
    when: Literal["gate", "acceptance", "console"] = "gate"
    gate: str = Field(default="*", description="fnmatch pattern on GateFinding.gate")
    severity: Severity = Severity.ERROR
    kinds: list[str] = Field(default_factory=list)
    note: str = ""


class Rubric(BaseModel):
    name: str
    version: int = 1
    track_hint: str = Field(default="", description="static_object | articulated_object | scene | asset | reference")
    pass_threshold: float = Field(ge=0.0, le=1.0)
    criteria: list[Criterion] = Field(min_length=1)
    caps: list[CapRule] = Field(default_factory=list)
    extra_instructions: str = ""
    description: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Rubric:
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate criterion ids in rubric {self.name}")
        total = sum(c.weight for c in self.criteria)
        if abs(total - 1.0) > 0.02:
            raise ValueError(f"rubric {self.name}: weights sum to {total:.3f}, expected 1.0")
        cap_ids = [c.id for c in self.caps]
        if len(cap_ids) != len(set(cap_ids)):
            raise ValueError(f"duplicate cap ids in rubric {self.name}")
        return self

    # ------------------------------------------------------------------ helpers
    @property
    def weights(self) -> dict[str, float]:
        return {c.id: c.weight for c in self.criteria}

    def criterion(self, cid: str) -> Criterion:
        for c in self.criteria:
            if c.id == cid:
                return c
        raise KeyError(f"rubric {self.name} has no criterion {cid!r}")

    def visual_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.kind == "visual"]

    def measured_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.kind == "measured"]

    def weighted_overall(self, scores: dict[str, float]) -> float:
        """Σ w·s / Σ w over the rubric's criteria.  Every criterion must be present."""
        missing = [c.id for c in self.criteria if c.id not in scores]
        if missing:
            raise RubricError(f"cannot score rubric {self.name}: missing criteria {missing}")
        num = sum(c.weight * float(scores[c.id]) for c in self.criteria)
        den = sum(c.weight for c in self.criteria)
        return max(0.0, min(1.0, num / den))

    def floors_hit(self, scores: dict[str, float]) -> list[tuple[str, float, float]]:
        """(criterion id, score, floor) for every criterion scored below its floor."""
        out = []
        for c in self.criteria:
            if c.floor is not None and c.id in scores and float(scores[c.id]) < c.floor:
                out.append((c.id, float(scores[c.id]), c.floor))
        return out

    def content_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


def list_rubrics() -> list[str]:
    return sorted(p.stem for p in RUBRICS_DIR.glob("*.yaml"))


def rubric_from_dict(data: dict, *, name_hint: str = "") -> Rubric:
    try:
        return Rubric(**data)
    except Exception as e:  # pydantic ValidationError / TypeError
        raise RubricError(f"invalid rubric {name_hint or data.get('name')!r}: {e}") from e


@lru_cache(maxsize=32)
def load_rubric(name: str) -> Rubric:
    """Load ``rubrics/<name>.yaml`` (or a path to a yaml file) into a validated ``Rubric``."""
    path = Path(name)
    if not (path.suffix in (".yaml", ".yml") and path.is_file()):
        path = RUBRICS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise RubricError(f"unknown rubric {name!r}; available: {list_rubrics()}")
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise RubricError(f"rubric file {path} must be a mapping")
    data.setdefault("name", path.stem)
    return rubric_from_dict(data, name_hint=path.stem)
