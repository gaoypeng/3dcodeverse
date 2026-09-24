"""Every runtime's build answers a missing entry file the same way (audit 2026-09-24 N21)."""

from __future__ import annotations

import json

import pytest

from codeverse3d.contracts.common import ENTRY_FILE, Language
from codeverse3d.languages.base import get_runtime


@pytest.mark.parametrize("language", list(Language))
def test_a_missing_entry_is_one_typed_repairable_failure(tmp_ws, language: Language) -> None:
    """Regression: a scene with no scene.js was a `scene_probe` harness failure (never repaired)
    and urdf a `LintError`; now all seven return MissingEntryFile, as build.json says too."""
    res = get_runtime(language).build(tmp_ws)
    assert (res.ok, res.error_type, res.error_file, res.harness_failure) == \
        (False, "MissingEntryFile", ENTRY_FILE[language], False)
    assert json.loads((tmp_ws.artifacts / "build.json").read_text())["error_type"] == "MissingEntryFile"
