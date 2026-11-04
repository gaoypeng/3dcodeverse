"""Corpus invariants for the shipped skill bundles and their numeric claims."""

from __future__ import annotations

import json

import pytest

from codeverse3d.addons.skill_targets import (
    check_claims,
    check_prompt_claims,
)
from codeverse3d.skills import bundle_dirs, iter_skills, skills_dir, validate_bundle

BUNDLES = bundle_dirs()
SKILLS = list(iter_skills()) if BUNDLES else []
pytestmark = pytest.mark.skipif(not BUNDLES, reason=f"no bundles in {skills_dir()} yet (the Author phase writes them)")

def test_every_bundle_is_valid_and_its_claims_are_current():
    """One corpus pass keeps structure, brevity, evidence and pinned values honest."""
    for bundle in BUNDLES:
        errors = validate_bundle(bundle)
        assert not errors, f"{bundle.name}: {errors}"

    for skill in SKILLS:
        errors = check_claims(skill)
        assert not errors, f"{skill.name}: {errors}"
    assert not check_prompt_claims(), check_prompt_claims()   # prompts/_claims.toml, the same check

_JS = "export const SPAN_M = 40;\n"

@pytest.mark.parametrize("row,doc,js", [
    ({"text": "0.5 m", "python": "codeverse3d.spatial.frame_metrics:NEAR_HIT_M", "format": "{:.1f} m"},
     "stay 10.5 m back", _JS),                                        # inside a longer number
    ({"text": "40 m", "js": "{js}:SPAN_M", "format": "{:.0f} m"}, "over 40 m", _JS.replace("40", "45")),  # JS moved
], ids=["token", "js"])
def test_a_claim_fails_when_its_text_or_its_constant_moves(tmp_path, row, doc, js):
    """N48: prompts/**, a bundle's references/ and JS constants are pinned like a SKILL body — and a
    short number only counts where it stands as its own token, on the line its ``context`` names."""
    (tmp_path / "x.js").write_text(js)
    (tmp_path / "doc.md").write_text(doc)
    cells = {**row, "key": "k", "file": "doc.md"}
    if "js" in cells:
        cells["js"] = cells["js"].format(js=tmp_path / "x.js")
    (tmp_path / "_claims.toml").write_text("[[claim]]\n" + "".join(f"{k} = {json.dumps(v)}\n" for k, v in cells.items()))
    assert check_prompt_claims(tmp_path), row

