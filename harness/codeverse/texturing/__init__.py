"""Text-to-image texturing: a language-agnostic pass on the canonical GLB
(objects) and a scene texture pack (scenes).  See ``run.texture_pass`` and
``scene_pack.scene_texture_pack``."""

from codeverse.texturing.apply import ApplyReport, apply_textures
from codeverse.texturing.gate import GateResult, SeamGateResult, judge_gate, seam_gate
from codeverse.texturing.generate import FakeImageModel, TextureAsset, TextureSet, generate_textures
from codeverse.texturing.plan import TexturePart, TexturePlan, default_plan, material_plan
from codeverse.texturing.run import TextureReport, texture_pass
from codeverse.texturing.scene_pack import ScenePack, scene_texture_pack, texture_pack_prompt
from codeverse.texturing.tile import make_tileable, seam_score

__all__ = [
    "ApplyReport", "apply_textures", "GateResult", "SeamGateResult", "judge_gate", "seam_gate",
    "FakeImageModel", "TextureAsset", "TextureSet", "generate_textures", "TexturePart", "TexturePlan",
    "default_plan", "material_plan", "TextureReport", "texture_pass", "ScenePack", "scene_texture_pack",
    "texture_pack_prompt", "make_tileable", "seam_score",
]
