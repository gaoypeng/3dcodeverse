"""D112 (iteration 5, r1_07): the refine prompt carries the placement rows itself; the session had burned 15 minutes paging scene_probe.json."""

from __future__ import annotations

from codeverse3d.contracts.run import RoundRecord
from codeverse3d.orchestrator import TaskGroup, build_refine_instructions
from codeverse3d.spatial.scene_placement import placement_findings
from tests.orchestrator_tracks.test_env_stage_honest import LANGUAGES, _ctx
from tests.scene_gates.test_placement import _row, _table


def test_the_refine_prompt_states_each_failed_row_its_gap_and_tolerance(tmp_path, settings):
    track, ctx = _ctx(tmp_path, settings, LANGUAGES[0])
    ctx.tool_cards = "(tools)"
    gate = placement_findings(_table(*[_row(f"Boulder{i}", zone="FernHollow", gap=g) for i, g in enumerate((0.048, 0.046, 0.041))]),
                              indoor=True)
    tasks = build_refine_instructions(None, [gate], [], ctx.plan, max_tasks=6)
    group = TaskGroup(label="all", tasks=tasks, targets=sorted({t.target for t in tasks}), files=[])
    prompt = track._refine_task(ctx, group, RoundRecord(index=0, kind="baseline"), 1, parallel=False).prompt
    for name, gap in (("FernHollow/Boulder0", "0.048"), ("FernHollow/Boulder1", "0.046"), ("FernHollow/Boulder2", "0.041")):
        assert f"{name} is floating {gap} m above Ground (touches nothing; indoor tolerance 0.02 m" in prompt, name
    assert "`artifacts/scene_probe.json` is the probe driver's raw dump" in prompt
    assert "`check_placement` returns the placement rows" in prompt
