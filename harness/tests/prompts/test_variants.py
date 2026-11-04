"""The variant layer (D105): selection, the prompt overlay, caches, activation, the record."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from jinja2 import TemplateNotFound

from codeverse3d import prompts
from codeverse3d.config import Settings, get_settings
from codeverse3d.contracts.common import Language
from codeverse3d.prompts import variants as V
from codeverse3d.prompts.variants import (
    VARIANTS_DIR,
    activate,
    resolve_variant,
    variant_info,
)
from tests.prompts.conftest import PROMPTS_DIR

TV_TOML = ('[variant]\nname = "tv"\nversion = 3\ngenerators = ["agy:gemini-4-argon"]\n'
           'description = "a test variant"\n')

def _clear() -> None:
    prompts._load.cache_clear()
    prompts._env_for.cache_clear()
    get_settings.cache_clear()

@pytest.fixture(autouse=True)
def _no_process_variant(monkeypatch):
    """No variant from this machine's environment, and no cached overlay from another test."""
    monkeypatch.delenv("C3D_VARIANT", raising=False)
    monkeypatch.delenv("C3D_VARIANTS", raising=False)
    _clear()
    yield
    _clear()

@pytest.fixture
def overlay(tmp_path: Path, monkeypatch) -> Path:
    """A tmp ``VARIANTS_DIR`` holding ``tv``: a system.md that shadows blender's, a template only the
    variant ships, and a role template that includes a BASE file (the ChoiceLoader fall-through)."""
    root = tmp_path / "variants"
    shutil.copytree(VARIANTS_DIR / "argon", root / "argon")   # the shipped one stays known beside tv
    tv = root / "tv"
    for d in ("blender", "tracks", "system"):
        (tv / d).mkdir(parents=True)
    (tv / "variant.toml").write_text(TV_TOML)
    (tv / "README.md").write_text("docs only\n")
    (tv / "blender" / "system.md").write_text("# OVERLAY SYSTEM\n")
    (tv / "tracks" / "only_in_variant.j2").write_text("overlay {{ x }}\n")
    (tv / "system" / "role_repair.j2").write_text("OVERLAY ROLE {{ contract }}\n{% include 'system/singleshot_format.md' %}")
    monkeypatch.setattr(V, "VARIANTS_DIR", root)
    _clear()
    return root

# --------------------------------------------------------------------------- selection
def test_resolution_order_including_the_served_model_lookup(overlay):
    """settings.variant > variants[id as typed] > variants['<kind>:<served model>'] > base; plain lookups."""
    table = {"agy:gemini-4-argon": "tv", "agy:gemini-4-argon-low": "argon", "claude-code:claude-opus-5": "tv"}
    assert resolve_variant(Settings(variant="argon", variants=table), "agy:gemini-4-argon") == "argon"
    assert resolve_variant(Settings(variants=table), "agy:gemini-4-argon-low") == "argon", "the id as typed wins"
    assert resolve_variant(Settings(variants=table), "agy:gemini-4-argon-high") == "tv", "then the served model"
    assert resolve_variant(Settings(variants=table), "agy:gemini-4-argon") == "tv"
    assert resolve_variant(Settings(variants=table), "single-shot:gemini:gemini-4-argon") == "", "no served spelling"
    assert resolve_variant(Settings(variant="  tv "), "x") == "tv"
    assert V.served_id("agy:gemini-4-argon-medium") == "agy:gemini-4-argon" and V.served_id("nocolon") == ""

def test_variant_toml_is_type_checked(overlay):
    (overlay / "tv" / "variant.toml").write_text('[variant\nname = "tv"\n')
    with pytest.raises(ValueError, match="not valid TOML"):
        variant_info("tv")

# --------------------------------------------------------------------------- the prompt overlay
def test_an_overlay_file_shadows_the_base_and_a_missing_one_falls_back(overlay):
    base_system = (PROMPTS_DIR / "blender" / "system.md").read_text()
    base_contract = (PROMPTS_DIR / "blender" / "contract.md").read_text()
    with activate("tv"):
        assert prompts.load_text("blender/system.md") == "# OVERLAY SYSTEM\n"
        assert prompts.load_text("blender/contract.md") == base_contract, "no overlay copy: the base file"
        assert prompts.render("tracks/only_in_variant.j2", x=1) == "overlay 1\n"
        rendered = prompts.render("system/role_repair.j2", contract="C")
        assert rendered.startswith("OVERLAY ROLE C\n")
        assert (PROMPTS_DIR / "system" / "singleshot_format.md").read_text().strip() in rendered, "a base include"
        # the catalog resolves through the same loader: callers do not change
        from codeverse3d.prompts.catalog import language_text

        assert language_text(Language.BLENDER, "system.md") == "# OVERLAY SYSTEM\n"
        assert language_text(Language.BLENDER, "contract.md") == base_contract
        with pytest.raises(FileNotFoundError):
            prompts.load_text("blender/nowhere.md")
    assert prompts.load_text("blender/system.md") == base_system
    with pytest.raises(TemplateNotFound):
        prompts.render("tracks/only_in_variant.j2", x=1)

# --------------------------------------------------------------------------- provenance

# --------------------------------------------------------------------------- the run

