"""Live texturing tests (real Gemini calls; need keys).  Run: pytest tests/texturing -m live -s"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeverse.config import get_settings
from codeverse.contracts.plan import StaticPlan
from codeverse.contracts.spec import Spec
from codeverse.models.gemini import GeminiImageModel
from codeverse.texturing.generate import seam_score
from codeverse.texturing.plan import compose_image_prompt, material_plan

pytestmark = pytest.mark.live
RUN = Path(__file__).resolve().parents[2] / "runs" / "e2e_chair_blender"


@pytest.fixture(autouse=True)
def _keys():
    if not get_settings().gemini_api_keys:
        pytest.skip("no Gemini keys")


def test_gemini_image_model_generates_a_tile(tmp_path: Path):
    model = GeminiImageModel()
    images, usage = model.generate_with_usage(compose_image_prompt("oiled walnut wood grain, warm brown", "wood"), size=1024, seed=1)
    assert len(images) == 1 and images[0].size == (1024, 1024)
    assert usage.cost_usd > 0.01 and usage.output_tokens > 0 and usage.backend == "gemini-image"
    assert 0.0 <= seam_score(images[0]) <= 1.0
    images[0].save(tmp_path / "walnut.png")


def test_material_plan_live_on_chair(tmp_path: Path):
    if not (RUN / "plan.json").is_file():
        pytest.skip("reference run missing")
    spec = Spec.model_validate_json((RUN / "spec.json").read_text())
    plan = StaticPlan.model_validate_json((RUN / "plan.json").read_text())
    sheet = RUN / "artifacts" / "renders" / "r01" / "sheet.png"
    tp = material_plan(spec, plan, sheet if sheet.is_file() else None, "gemini:gemini-3.7-flash", cache_dir=tmp_path, use_cache=False)
    assert tp.source == "vlm" and len(tp.parts) == len(plan.parts)
    assert tp.textured() and all("wood" in p.material_family or p.skip for p in tp.parts)
    assert 1 <= len(tp.texture_ids()) <= 4 and tp.usage.cost_usd > 0
