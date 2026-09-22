# 3dcodeverse (repo root)

The harness lives in **`harness/`** — read `harness/CLAUDE.md` +
`harness/docs/ARCHITECTURE.md` before touching it.  Install with
`pip install -e harness`.  Run tests with
`cd harness && python -m pytest tests -q -m "not live"`.

**`eval/`** evaluates things and is never imported by the harness: `eval/bench` evaluates the HARNESS
(batteries — `cd eval && python -m bench.run_bench <battery.yaml>`, which replaced `3dcode bench` —
A/B rigs, reports; its tests: `cd eval && python -m pytest`), `eval/llm` evaluates a bare LLM/VLM
(was `finetune/3dcodeverse_eval`).  Read `eval/README.md` first.

Other top-level folders are separate components (`toolkits/` data tooling, `finetune/` training).
