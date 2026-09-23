"""Fixtures for the texturing tests: a small chair-like GLB (named nodes, instances),
its plan + spec, a fake image model, a fake render function and a fake judge."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh
from PIL import Image

from codeverse3d.contracts.artifacts import Judgment, RenderSet, RenderView
from codeverse3d.contracts.common import Language, Track, Usage
from codeverse3d.contracts.plan import BBox, PartPlan, StaticPlan
from codeverse3d.contracts.spec import Spec


def _box(ext: tuple[float, float, float], center: tuple[float, float, float]) -> trimesh.Trimesh:
    m = trimesh.creation.box(extents=ext)
    m.apply_translation(center)
    return m


@pytest.fixture
def chair_glb(tmp_path: Path) -> Path:
    """Y-up chair: Seat slab, Back slab, 4 Leg_i cylinders (instances), a tiny glass Knob."""
    scene = trimesh.Scene()
    seat = _box((0.44, 0.03, 0.42), (0, 0.45, 0))
    seat.visual = trimesh.visual.ColorVisuals(seat, face_colors=(150, 100, 60, 255))
    scene.add_geometry(seat, node_name="Seat", geom_name="Seat")
    back = _box((0.44, 0.35, 0.03), (0, 0.64, -0.195))
    back.visual = trimesh.visual.ColorVisuals(back, face_colors=(150, 100, 60, 255))
    scene.add_geometry(back, node_name="Back", geom_name="Back")
    leg = trimesh.creation.cylinder(radius=0.02, height=0.45, sections=24)
    leg.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0)))  # along Y
    leg.apply_translation((0, 0.225, 0))
    leg.visual = trimesh.visual.ColorVisuals(leg, face_colors=(90, 90, 95, 255))
    for i, (x, z) in enumerate([(-0.2, -0.19), (0.2, -0.19), (-0.2, 0.19), (0.2, 0.19)]):
        T = trimesh.transformations.translation_matrix((x, 0, z))
        scene.add_geometry(leg.copy(), node_name=f"Leg_{i}", geom_name=f"Leg_{i}", transform=T)
    knob = trimesh.creation.icosphere(subdivisions=1, radius=0.01)
    knob.apply_translation((0.22, 0.47, 0))
    knob.visual = trimesh.visual.ColorVisuals(knob, face_colors=(200, 220, 255, 255))
    scene.add_geometry(knob, node_name="Knob", geom_name="Knob")
    out = tmp_path / "object.glb"
    scene.export(out)
    return out


def _bbox(c: tuple[float, float, float], e: tuple[float, float, float]) -> BBox:
    return BBox(center=c, extents=e)


@pytest.fixture
def chair_plan() -> StaticPlan:
    return StaticPlan(
        object_name="TestChair", summary="a simple wooden chair with steel legs",
        overall_bbox=_bbox((0, 0, 0.4), (0.44, 0.42, 0.82)),
        parts=[
            PartPlan(name="Seat", role="seat", description="slab", bbox=_bbox((0, 0, 0.45), (0.44, 0.42, 0.03)), material="oiled oak wood"),
            PartPlan(name="Back", role="backrest", description="slab", bbox=_bbox((0, -0.2, 0.64), (0.44, 0.03, 0.35)), material="oiled oak wood"),
            PartPlan(name="Leg", role="leg", description="round steel tube", bbox=_bbox((0.2, 0.19, 0.225), (0.04, 0.04, 0.45)),
                     material="brushed steel", attach_to="Seat", instances=4),
            PartPlan(name="Knob", role="decor", description="tiny glass ball", bbox=_bbox((0.22, 0, 0.47), (0.02, 0.02, 0.02)),
                     material="clear glass", attach_to="Seat"),
        ],
    )


@pytest.fixture
def chair_spec() -> Spec:
    return Spec(id="t1", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="a simple oak chair with brushed steel legs")


def fake_render(glb: Path | str, out_dir: Path | str, *, views: Any, width: int = 512, height: int = 512, **_: Any) -> RenderSet:
    """Writes one flat PNG per view + a sheet; colour keyed by the GLB size (so before/after differ)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = Path(glb).stat().st_size % 200
    rviews = []
    for v in views:
        p = out_dir / f"view_{v.name}.png"
        Image.new("RGB", (64, 64), (seed + 30, 120, 160)).save(p)
        rviews.append(RenderView(name=v.name, path=str(p), width=64, height=64))
    sheet = out_dir / "sheet.png"
    Image.new("RGB", (128, 64), (seed + 30, 120, 160)).save(sheet)
    return RenderSet(views=rviews, contact_sheet=str(sheet), renderer="fake")


class FakeJudge:
    """Returns scripted verdicts in order (cycles the last one)."""

    name = "fake"

    def __init__(self, verdicts: list[tuple[float, dict[str, float]]], *, degraded: bool = False):
        self.verdicts = verdicts
        self.calls = 0
        self.degraded = degraded

    def judge(self, inp: Any) -> Judgment:
        overall, scores = self.verdicts[min(self.calls, len(self.verdicts) - 1)]
        self.calls += 1
        summary = "judge_error: boom" if self.degraded else "ok"
        return Judgment(rubric="static_object_v1", scores=scores, overall=overall, passed=overall >= 0.72, summary=summary,
                        usage=Usage(cost_usd=0.01))


# the material-plan answer for ``texture_pass(plan_model=FakeChatModel(default=PLAN_REPLY))``: a test
# that leaves plan_model None makes ``material_plan`` build a REAL model from the spec's planner id
# and then only passes on a box that happens to have keys (PORT-2)
PLAN_REPLY = {"parts": [
    {"part": "Seat", "texture_id": "oak_wood", "material_family": "wood",
     "subject": "light oak", "projection": "planar_y", "tile_size_m": 0.4},
    {"part": "Back", "texture_id": "oak_wood", "material_family": "wood",
     "subject": "light oak", "projection": "box"},
    {"part": "Leg", "texture_id": "steel", "material_family": "metal",
     "subject": "brushed steel", "projection": "cylinder", "metallic": 1, "roughness": 0.3},
], "notes": "fake plan"}


# --------------------------------------------------------------------------- fake image model
class FakeImageModel:
    """Deterministic procedural textures (seeded by the prompt) for offline tests.
    Records every prompt it was asked for in ``calls``.

    Lived in ``codeverse3d.texturing.generate`` until 2026-08-30, where nothing in a
    real run could reach it: no setting, env var or CLI flag selects it, and the
    keyless path raises ``ModelError`` rather than falling back to a fake."""

    provider = "fake"

    def __init__(self, model: str = "fake-image", *, latency_s: float = 0.0, fail_on: Sequence[str] = (),
                 usd_per_image: float = 0.001) -> None:
        self.model = model
        self.latency_s = latency_s
        self.fail_on = tuple(fail_on)
        self.usd_per_image = usd_per_image
        self.calls: list[str] = []
        self._lock = threading.Lock()

    @property
    def id(self) -> str:
        return f"fake-image:{self.model}"

    def generate_with_usage(self, prompt: str, *, size: int = 1024, n: int = 1, seed: int | None = None,
                            reference_images: Sequence[Any] = ()) -> tuple[list[Image.Image], Usage]:
        with self._lock:
            self.calls.append(prompt)
        if any(s in prompt for s in self.fail_on):
            from codeverse3d.models.base import ModelError

            raise ModelError(f"fake image model refused: {prompt[:40]}", retryable=False)
        if self.latency_s:
            time.sleep(self.latency_s)
        images = [procedural_texture(prompt, size=size, seed=(seed or 0) + i) for i in range(n)]
        usage = Usage(backend="fake-image", model=self.model, input_tokens=len(prompt.split()), output_tokens=1290 * n,
                      cost_usd=self.usd_per_image * n, latency_ms=int(self.latency_s * 1000))
        return images, usage


def procedural_texture(prompt: str, *, size: int = 256, seed: int = 0) -> Image.Image:
    """Prompt-seeded noise + stripes in a prompt-derived colour.  NOT tileable on
    purpose (so tests exercise ``make_tileable``): a linear gradient is added."""
    h = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(h + seed)
    base = np.array([(h >> 16) & 255, (h >> 8) & 255, h & 255], dtype=np.float32) / 255.0
    base = 0.25 + 0.6 * base
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) / size
    stripes = 0.08 * np.sin(2 * np.pi * (8 * x + 3 * np.sin(2 * np.pi * y)))
    noise = 0.06 * rng.standard_normal((size, size)).astype(np.float32)
    grad = 0.25 * x  # seam-breaking gradient
    lum = (stripes + noise + grad)[:, :, None]
    img = np.clip(base[None, None, :] + lum, 0.0, 1.0)
    return Image.fromarray((img * 255).astype(np.uint8), "RGB")
