# eval/ — evaluating a bare LLM / VLM on 3D coding

[`llm/`](llm/) asks a model one-shot — no plugin, no tools in the loop — over 3DCodeBench, the held-out
dialect sets and ten prompt batteries (`llm/batteries/*.yaml`); every answer is executed and scored with
geometry and image metrics against references.  `docs/` holds the earlier evaluation write-ups
(`EVAL.md`, `COMPLEXITY.md`, `PAPER_WRITING.md`); the harness benchmark they describe (`eval/bench`) ran
on the v0.0.x CLI harness and lives in those releases.

```bash
cd eval
python -m llm.config                          # what the evaluation can / cannot find
python -m pytest llm/tests                    # offline tests
```

Run data is never in git: `llm/data/prompts/*.jsonl` is built by `python -m llm.build_prompts` from the
gated Hub assets.
