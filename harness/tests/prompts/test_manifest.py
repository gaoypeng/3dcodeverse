"""The manifest gate: what every model is shown stays byte-identical to ``manifest.json``.

``tests/prompts/manifest.py`` says what is captured and how; ``python -m tests.prompts.manifest
--bless`` re-blesses it when a change MEANS to alter a payload (say why in the commit)."""

from __future__ import annotations

import pytest

from tests.prompts import manifest as M


@pytest.mark.parametrize("name", M.ALL)
def test_the_run_matches_the_manifest(name: str, tmp_path) -> None:
    """Check both sections in one case so xdist cannot run the same scenario twice."""
    stored = M.load()
    assert name in stored, f"{name} is not in {M.MANIFEST.name}: bless it"
    got = M.digest(M.run_scenario(name, tmp_path))
    changes = []
    for section, hint in (("payloads", M.PAYLOAD_HINT), ("stage_keys", M.STAGE_HINT)):
        lines = M.diff(got[section], stored[name][section])
        if lines:
            changes.append(f"{name}: {section} changed\n" + "\n".join(lines) + "\n\n" + hint)
    assert not changes, "\n\n".join(changes)


def test_the_manifest_holds_exactly_the_scenarios() -> None:
    assert sorted(M.load()) == M.ALL, "bless after adding or removing a scenario"
