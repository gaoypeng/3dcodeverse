# eval/ — two evaluations, kept apart from what they evaluate

| folder | what it evaluates | how |
|---|---|---|
| [`bench/`](bench/) | **the harness** (`../harness`): does plan → build → gates → judge → refine beat asking a model once? | prompt batteries run through the harness and through one-shot / bare-agent arms, judged by ONE fixed evaluator; paired A/B rigs for harness switches; offline re-judging and reports |
| [`llm/`](llm/) | **a bare LLM / VLM** on 3D coding — no harness in the loop | text → code and image → code over 3DCodeBench, the held-out dialect sets and ten harness batteries; every answer is executed; geometry and image metrics against references |

`docs/` holds the evaluation write-ups: `EVAL.md` (protocol, judge calibration, every recorded
comparison), `COMPLEXITY.md` (the complexity vector and the score-vs-complexity study),
`PAPER_WRITING.md`.  `tests/` tests the `bench` scripts.

The dependency is one-way: `bench` and `llm` import `codeverse3d` (the harness package); nothing under
`harness/` imports anything here, and the harness test suite passes without this folder.  The two
evaluations share one set of battery files — `bench/prompts/*.yaml` — and `llm` reads the ten it can
ask one-shot from there (`llm/config.py: HARNESS_BATTERIES`).

```bash
cd eval
pip install -e ../harness                     # once; both evaluations import codeverse3d
python -m bench.run_bench bench/prompts/static_objects_v1.yaml --generator gemini-cli:gemini-3.7-flash
python -m bench.report bench/out/static_objects_v1          # rebuild report.md + report.html
python bench/compare_backends.py --prompts bench/prompts/compare_v1.yaml --arms harness:gemini-cli:gemini-3.7-flash,oneshot:gemini:gemini-3.7-flash --out bench/out/compare_v1
python -m llm.config                          # doctor for the LLM evaluation: tools and assets it can / cannot find
python -m pytest                              # the bench tests (offline)
ruff check .
```

Run data is never in git: `bench/out/` (battery output) and `llm/data/prompts/*.jsonl` (built by
`python -m llm.build_prompts` from the gated Hub assets) are ignored.  Every bench report reads its
journals through `bench/_jsonl.py` (a truncated last line costs one row; the last row per cell wins)
and states its uncertainty through `bench/stats.py` (the 95 % t-interval, the exact sign test).  Every `bench` script bootstraps
its own import path — `../harness` for THIS tree's `codeverse3d`, `.` for the `bench` package — so an
editable install of a different checkout cannot silently stand in for the tree under test
(`tests/test_worktree_import.py`).
