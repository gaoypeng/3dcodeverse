"""Shared bench plumbing: select_prompts / build_spec / settings-routed backends."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import bench.run_bench as rb
from bench.run_bench import Battery, BenchOptions, select_prompts, spec_for
from tests.conftest import BATTERY


def _battery() -> Battery:
    return Battery.load(BATTERY)


def test_select_prompts_ids_tiers_limit():
    b = _battery()
    assert select_prompts(b) == b.prompts
    only = select_prompts(b, ids=[b.prompts[0].id])
    assert [p.id for p in only] == [b.prompts[0].id]
    tiers = {p.tier for p in b.prompts}
    tier = sorted(tiers)[0]
    assert all(p.tier == tier for p in select_prompts(b, tiers=[tier]))
    assert len(select_prompts(b, limit=1)) == 1
    assert select_prompts(b, ids=["nope"]) == []


def test_spec_for_routes_through_settings_backends():
    b = _battery()
    s = rb.get_settings()
    spec = spec_for(b, b.prompts[0], BenchOptions(generator="gemini-cli:gemini-3.7-flash"))
    assert spec.backends.generator == "gemini-cli:gemini-3.7-flash"
    # unset roles fall through to the settings defaults, not the Backends() literals
    assert spec.backends.planner == s.default_planner and spec.backends.judge == s.default_judge
    assert spec.backends.captioner == s.default_captioner


def test_the_harness_arm_looks_for_each_languages_own_entry_file():
    """Gating on the blender-only MODEL_FILE scored every glsl/three.js/scene cell 0.0 (2026-08-25)."""
    from bench.compare_backends import entry_of
    from codeverse3d.contracts.common import ENTRY_FILE

    for language, entry in ENTRY_FILE.items():
        assert entry_of(SimpleNamespace(language=language)) == entry, language.value


def test_a_run_that_scored_nothing_says_so_in_its_row(tmp_path: Path) -> None:
    """An arm whose every round skipped the judge must say so in its row, not read as healthy (2026-09-04)."""
    from bench.run_bench import result_from_record
    from codeverse3d.contracts.artifacts import Judgment
    from codeverse3d.contracts.common import Language, Track, Usage
    from codeverse3d.contracts.run import RoundRecord, RunRecord, RunStatus
    from codeverse3d.contracts.spec import Spec
    from codeverse3d.workspace import Workspace

    item = SimpleNamespace(id="cpl_umbrella", tier="hard", category="mechanism")
    spec = Spec(id="cpl_umbrella", track=Track.STATIC_OBJECT, language=Language.BLENDER, prompt="an umbrella")
    rounds = [RoundRecord(index=i, kind="refine") for i in range(3)]
    rec = RunRecord(spec=spec, workspace=str(tmp_path), status=RunStatus.MAX_ROUNDS, rounds=rounds,
                    total_usage=Usage(cost_usd=1.76))
    ws = Workspace(tmp_path)

    row = result_from_record(item, rec, ws)
    assert row.score_picked is None and row.picked_round is None and row.status == "max_rounds"
    assert "no verdict in any of 3 round(s)" in row.errors

    judged = RoundRecord(index=0, kind="baseline", judgment=Judgment(rubric="r", scores={}, overall=0.6, passed=False))
    rec.rounds = [judged, rounds[1]]
    row = result_from_record(item, rec, ws)
    assert row.errors == "" and (row.score_picked, row.picked_round) == (0.6, 0)   # one verdict is enough
