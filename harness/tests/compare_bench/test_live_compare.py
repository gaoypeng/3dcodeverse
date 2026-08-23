"""Live smoke: one prompt, one API one-shot arm, flash judge (cheap; needs keys + Blender + node)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench._fixed_eval import FixedEvaluator  # noqa: E402
from bench.compare_backends import CompareDeps, CompareOptions, parse_arms, run_matrix  # noqa: E402
from codeverse.config import get_settings  # noqa: E402

pytestmark = [pytest.mark.live, pytest.mark.blender, pytest.mark.node]


@pytest.mark.skipif(not get_settings().gemini_api_keys, reason="no gemini keys")
def test_oneshot_gemini_cell_scores(tmp_path: Path):
    opts = CompareOptions(judge="gemini:gemini-3.7-flash", n_samples=1, limit=1, parallel=1, pairwise=False)
    deps = CompareDeps(FixedEvaluator(opts.judge, n_samples=1))
    rows = run_matrix(REPO / "bench" / "prompts" / "compare_v1.yaml", tmp_path / "o",
                      parse_arms("oneshot:gemini:gemini-3.7-flash"), opts, deps)
    assert len(rows) == 1
    r = rows[0]
    assert r.status in ("scored", "build_failed"), r.error
    assert r.gen_cost_usd > 0 and r.attempts == 1 and r.tool_calls == 0
    if r.status == "scored":
        assert r.build_ok and 0.0 <= (r.score or 0) <= 1.0 and Path(r.sheet).is_file() and r.tris
    assert (tmp_path / "o" / "report.md").is_file() and (tmp_path / "o" / "report.html").is_file()
