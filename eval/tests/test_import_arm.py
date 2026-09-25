"""The import arm scores files another tool produced (no model asked); every arm records its tokens (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from bench.compare_backends import CompareDeps, CompareOptions, parse_arm, run_cell  # noqa: E402
from bench.run_bench import Battery  # noqa: E402
from codeverse3d.contracts.common import Usage  # noqa: E402
from tests.conftest import BATTERY, GOOD, FakeBackend, FakeEvaluator  # noqa: E402


def test_import_arm_needs_an_existing_directory(tmp_path):
    assert parse_arm(f"import:{tmp_path}").kind == "import"
    with pytest.raises(ValueError):
        parse_arm(f"import:{tmp_path / 'missing'}")


def test_import_arm_scores_the_file_named_after_the_prompt(tmp_path):
    battery = Battery.load(BATTERY)
    item = battery.prompts[0]
    src = tmp_path / "design"
    src.mkdir()
    (src / f"{item.id}.py").write_text(GOOD.format(score=0.66))
    ev = FakeEvaluator()
    r = run_cell(battery, item, parse_arm(f"import:{src}"), tmp_path / "out",
                 CompareOptions(judge="gemini:x", loop_judge="gemini:x"), CompareDeps(ev))
    assert (r.status, r.score) == ("scored", 0.66) and len(ev.evaluated) == 1 and ev.evaluated[0].endswith(item.id)
    assert r.gen_cost_usd == 0 and r.gen_input_tokens == 0


def test_import_arm_without_the_file_is_no_code(tmp_path):
    battery = Battery.load(BATTERY)
    src = tmp_path / "design"
    src.mkdir()
    r = run_cell(battery, battery.prompts[0], parse_arm(f"import:{src}"), tmp_path / "out",
                 CompareOptions(judge="gemini:x", loop_judge="gemini:x"), CompareDeps(FakeEvaluator()))
    assert (r.status, r.score) == ("no_code", 0.0) and "no " in r.error


def test_a_one_shot_cell_records_its_tokens(tmp_path):
    battery = Battery.load(BATTERY)

    class Counted(FakeBackend):
        def generate(self, prompt, *, out_dir, timeout_s=0, label=""):
            res = super().generate(prompt, out_dir=out_dir, timeout_s=timeout_s, label=label)
            res.usage = Usage(input_tokens=1200, cached_tokens=200, output_tokens=800, thoughts_tokens=300, cost_usd=0.01)
            return res

    backend = Counted(["```python\n" + GOOD.format(score=0.5) + "```"])
    r = run_cell(battery, battery.prompts[0], parse_arm("oneshot:gemini:gemini-3.8-flash"), tmp_path,
                 CompareOptions(judge="gemini:x", loop_judge="gemini:x"),
                 CompareDeps(FakeEvaluator(), oneshot_backend=lambda t: backend))
    assert (r.gen_input_tokens, r.gen_output_tokens, r.gen_cached_tokens) == (1200, 1100, 200)


def test_the_report_names_an_import_arms_assets_without_its_path(tmp_path):

    battery = Battery.load(BATTERY)
    item = battery.prompts[0]
    src = tmp_path / "some" / "design"
    src.mkdir(parents=True)
    (src / f"{item.id}.py").write_text(GOOD.format(score=0.7))
    from bench.compare_backends import run_matrix
    run_matrix(BATTERY, tmp_path / "out", [parse_arm(f"import:{src}")],
               CompareOptions(judge="gemini:x", loop_judge="gemini:x", ids=[item.id], pairwise=False),
               CompareDeps(FakeEvaluator()))
    assets = list((tmp_path / "out" / "report_assets").glob("*"))
    assert assets and all("/" not in a.name and a.is_file() for a in assets)
