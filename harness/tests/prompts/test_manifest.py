"""The manifest gate: what every model is shown stays byte-identical to ``manifest.json``.

``tests/prompts/manifest.py`` says what is captured and how; ``python -m tests.prompts.manifest
--bless`` re-blesses it when a change MEANS to alter a payload (say why in the commit)."""

from __future__ import annotations

import pytest

from tests.prompts import manifest as M


@pytest.fixture(scope="module", params=M.ALL)
def scenario(request, tmp_path_factory) -> tuple[str, dict]:
    """One run per scenario for both tests below (module scope keeps them together)."""
    name = request.param
    return name, M.digest(M.run_scenario(name, tmp_path_factory.mktemp(name.replace(".", "_"))))


@pytest.mark.parametrize(("section", "hint"), [("payloads", M.PAYLOAD_HINT), ("stage_keys", M.STAGE_HINT)],
                         ids=["payloads", "stage_keys"])
def test_the_run_matches_the_manifest(scenario, section: str, hint: str) -> None:
    name, got = scenario
    stored = M.load()
    assert name in stored, f"{name} is not in {M.MANIFEST.name}: bless it"
    lines = M.diff(got[section], stored[name][section])
    assert not lines, f"{name}: {section} changed\n" + "\n".join(lines) + "\n\n" + hint


def test_the_manifest_holds_exactly_the_scenarios() -> None:
    assert sorted(M.load()) == M.ALL, "bless after adding or removing a scenario"
