"""Shared bench plumbing: select_prompts / build_spec / settings-routed backends."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import bench.compare_backends as cb
import bench.run_bench as rb
from bench.run_bench import Battery, BenchOptions, build_spec, select_prompts, spec_for
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


def test_select_prompts_is_single_owner_for_both_drivers():
    """compare_backends deleted its private copy; both drivers filter identically."""
    assert cb.select_prompts is select_prompts and cb.default_run_track is rb.default_run_track


def test_build_spec_tags_and_budget():
    b = _battery()
    item = b.prompts[0]
    spec = build_spec(b, item, backends=rb.get_settings().backends(), rounds=2,
                      max_minutes=10, tag0="compare", extra_tags=("harness",))
    assert spec.tags[:4] == ["compare", b.name, item.tier, item.category] and "harness" in spec.tags
    assert spec.budget.max_rounds == 2 and spec.budget.max_minutes == 10.0
    assert spec.id == f"{b.name}/{item.id}"


def test_spec_for_routes_through_settings_backends():
    b = _battery()
    s = rb.get_settings()
    spec = spec_for(b, b.prompts[0], BenchOptions(generator="gemini-cli:gemini-3.7-flash"))
    assert spec.backends.generator == "gemini-cli:gemini-3.7-flash"
    # unset roles fall through to the settings defaults, not the Backends() literals
    assert spec.backends.planner == s.default_planner and spec.backends.judge == s.default_judge
    assert spec.backends.captioner == s.default_captioner


# ------------------------------------------------------- the harness arm is not blender-only
def test_the_harness_arm_looks_for_each_languages_own_entry_file():
    """``bench/_oneshot.MODEL_FILE`` is ``src/model.py`` because the ONE-SHOT arms are a
    blender-only comparison.  The harness arm is not, and gating it on that constant made
    every glsl / three.js / scene / moderngl cell ``no_code`` **0.0** while the run itself
    came back ``passed`` — measured 2026-08-25 on a glsl_shader A/A whose control wrote
    ``src/shader.frag`` and was scored zero for it.

    Four of the seven languages were affected, which is why no graphics or three.js bundle
    has ever had a readable A/B: both arms scored 0.0 and the rig called that "no effect".
    """
    from bench.compare_backends import entry_of
    from codeverse.contracts.common import ENTRY_FILE, Language

    for language, entry in ENTRY_FILE.items():
        spec = SimpleNamespace(language=language)
        assert entry_of(spec) == entry, f"{language.value} delivers {entry}"

    # the four that the hardcoded constant got wrong, named so the regression is legible
    assert {lang.value for lang, e in ENTRY_FILE.items() if e != "src/model.py"} == {
        "threejs", "scene_threejs", "glsl_shader", "opengl_python"}
    assert entry_of(SimpleNamespace(language=Language.GLSL_SHADER)) == "src/shader.frag"


def test_a_run_that_scored_nothing_says_so_in_its_row() -> None:
    """A worktree without node_modules made render_glb die, every round skip the judge and
    ten cells come back `status=plateau, score=None` — an arm that reads as healthy and
    measures nothing (2026-09-04).  The row carries the reason now."""
    from bench.run_bench import result_from_record

    item = SimpleNamespace(id="cpl_umbrella", tier="hard", category="mechanism")
    rounds = [SimpleNamespace(index=i, judgment=None) for i in range(3)]
    rec = SimpleNamespace(
        rounds=rounds, best_round=0, baseline_score=None, final_score=None, error="",
        total_usage=SimpleNamespace(cost_usd=1.76), status=SimpleNamespace(value="plateau"),
        spec=SimpleNamespace(backends=SimpleNamespace(generator="g", judge="j")))
    ws = SimpleNamespace(root=Path("/tmp/ws"))

    row = result_from_record(item, rec, 16.0, ws)
    assert row.score_final is None and row.status == "plateau"
    assert "no verdict in any of 3 round(s)" in row.errors

    judged = SimpleNamespace(index=0, judgment=SimpleNamespace(passed=True))
    rec.rounds = [judged, rounds[1]]
    assert result_from_record(item, rec, 16.0, ws).errors == ""   # one verdict is enough


def test_two_bench_run_batteries_pair_by_prompt(tmp_path: Path) -> None:
    """`compare_backends` writes one journal with an arm column; `bench run` writes a
    directory per arm.  Two of those directories are a paired comparison, and it must go
    through the same statistics — paired CI, exact sign test, "unsupported when the
    interval crosses zero" — rather than being recomputed by hand."""
    import json as _json

    from bench.paired_compare import latest_cells, paired, rows_from_bench_run

    def write(d: Path, scores: dict[str, float]) -> Path:
        d.mkdir(parents=True)
        (d / "results.jsonl").write_text("".join(
            _json.dumps({"id": k, "tier": "hard", "score_final": v, "status": "plateau",
                         "cost_usd": 1.0}) + "\n" for k, v in scores.items()))
        return d

    a = write(tmp_path / "arm_a", {"p1": 0.6, "p2": 0.4, "p3": 0.5})
    b = write(tmp_path / "arm_b", {"p1": 0.5, "p2": 0.3, "p3": 0.5})

    rows = rows_from_bench_run(a, "arm_a") + rows_from_bench_run(b, "arm_b")
    st = paired(latest_cells(rows), "arm_a", "arm_b")
    assert st.n == 3 and st.mean_delta == pytest.approx(0.0667, abs=1e-3)
    assert st.wins == 2 and st.losses == 0 and st.ties == 1
    assert st.verdict == "unsupported"  # CI [-0.077, +0.21] crosses zero at n = 3
