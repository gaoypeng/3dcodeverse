"""One command: generate → execute → score → report, for one model over one or more suites.

    python -m llm.run_eval --backend vllm --model /path/Qwen3-8B --run qwen3_8b --suites headline heldout
    python -m llm.run_eval --backend openai --base-url http://localhost:8000/v1 --model Qwen3-8B --run q8 --suites all
    python -m llm.run_eval --backend reference --run reference --suites all        # executor self-test
    python -m llm.run_eval ... --samples 4 --temperature 0.7                          # pass@k

Outputs land in $C3D_EVAL_OUT/<run>/<suite>[/sN]/ and a report.md is rebuilt over the whole output root.
Stages can be skipped/resumed: --stages gen exec score (default: all); generation of an existing gens.jsonl is
reused unless --force.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import config, generate, report
from .execute import execute_dir
from .score import score_dir
from .suites import expand, get_suite, load_prompts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["vllm", "openai", "anthropic", "reference"], required=True)
    ap.add_argument("--model", default=None, help="model path / HF id / API model name")
    ap.add_argument("--run", required=True, help="output folder name (model tag)")
    ap.add_argument("--suites", nargs="+", default=["headline"], help="suite names or tags: all headline text vlm heldout rubric long-output")
    ap.add_argument("--out", type=Path, default=config.OUT_DIR)
    ap.add_argument("--limit", type=int, default=None, help="first N prompts of every suite (smoke tests)")
    ap.add_argument("--temperature", type=float, default=None, help="override the suite default")
    ap.add_argument("--max-new-tokens", type=int, default=None, help="override the suite default")
    ap.add_argument("--samples", type=int, default=1, help="N sampled generations per prompt (pass@k); seeds 0..N-1")
    ap.add_argument("--think", action="store_true", help="leave the model's thinking mode on (default: enable_thinking=False)")
    ap.add_argument("--stages", nargs="+", default=["gen", "exec", "render", "score"], choices=["gen", "exec", "render", "score"],
                    help="render = 4 canonical views of every OK mesh (suites that score image_sim, or any suite when --judge-model is set)")
    ap.add_argument("--render-workers", type=int, default=2)
    ap.add_argument("--render-engine", default="CYCLES", choices=["CYCLES", "BLENDER_EEVEE"])
    ap.add_argument("--image-encoders", nargs="*", default=["siglip2", "dinov3"])
    ap.add_argument("--force", action="store_true", help="regenerate even if gens.jsonl exists")
    ap.add_argument("--ref", choices=["executed", "canonical"], default="executed")
    # vllm
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--max-model-len", type=int, default=16384)
    ap.add_argument("--gpu-mem", type=float, default=0.90)
    ap.add_argument("--max-num-seqs", type=int, default=None)
    ap.add_argument("--max-images", type=int, default=4)
    # api
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--workers", type=int, default=8, help="API concurrency")
    ap.add_argument("--exec-workers", type=int, default=None)
    ap.add_argument("--score-workers", type=int, default=4, help="geometry-metric processes (each loads full meshes; keep small on <64 GB RAM)")
    # optional VLM judge (absolute 1-5 grades from the 4 renders; see judge.py)
    ap.add_argument("--judge-model", default=None, help="run judge.py absolute mode after scoring, e.g. gpt-5.4 or a local VLM")
    ap.add_argument("--judge-backend", choices=["openai", "anthropic"], default="openai")
    ap.add_argument("--judge-base-url", default=None)
    a = ap.parse_args()

    suites = expand(a.suites)
    run_dir = a.out / a.run
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_args.json").write_text(json.dumps(vars(a), default=str, indent=2))
    print(f"[run] {a.run}: suites {suites} -> {run_dir}", flush=True)

    plan = []   # (suite, sample_idx, gen_dir)
    for s in suites:
        for k in range(a.samples):
            gen_dir = run_dir / s / (f"s{k}" if a.samples > 1 else "")
            plan.append((s, k, gen_dir))

    t0 = time.time()
    if "gen" in a.stages:
        engine = None
        for s, k, gen_dir in plan:
            spec = get_suite(s)
            rows = load_prompts(s, a.limit)
            if (gen_dir / "gens.jsonl").exists() and not a.force:
                print(f"[gen] {s} s{k}: gens.jsonl exists, skipping (use --force)", flush=True)
                continue
            temp = a.temperature if a.temperature is not None else spec.temperature
            if a.samples > 1 and temp == 0.0:
                temp = 0.7
            mnt = a.max_new_tokens or spec.max_new_tokens
            if a.backend == "reference":
                generate.reference_generate(rows, gen_dir)
            elif a.backend == "vllm":
                if engine is None:
                    engine = generate.VllmEngine(a.model, tp=a.tp, max_model_len=a.max_model_len, gpu_mem=a.gpu_mem,
                                                 max_images=a.max_images if any(r.get("images") for r in rows) else 0,
                                                 max_num_seqs=a.max_num_seqs)
                engine.generate(rows, gen_dir, temp, mnt, seed=k, no_think=not a.think)
            else:
                generate.api_generate(a.backend, a.model, rows, gen_dir, temp, mnt, workers=a.workers, base_url=a.base_url,
                                      no_think=not a.think)
        if engine is not None:
            del engine
            try:
                import gc
                import torch
                gc.collect()
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
        print(f"[run] generation done in {(time.time() - t0) / 60:.1f} min", flush=True)

    if "exec" in a.stages:
        for s, k, gen_dir in plan:
            if not gen_dir.exists():
                continue
            execute_dir(gen_dir, get_suite(s).dialect, workers=a.exec_workers)
    if "render" in a.stages:
        from .render_views import render_dir
        for s, k, gen_dir in plan:
            if gen_dir.exists() and ("image_sim" in get_suite(s).metrics or a.judge_model) and (gen_dir / "exec_results.jsonl").exists():
                render_dir(gen_dir, workers=a.render_workers, engine=a.render_engine)
    if "score" in a.stages:
        for s, k, gen_dir in plan:
            if not gen_dir.exists():
                continue
            score_dir(gen_dir, s, ref=a.ref, workers=a.score_workers, image_encoders=tuple(a.image_encoders))
    if a.judge_model:
        from .judge import JudgeClient, judge_absolute
        jc = JudgeClient(a.judge_backend, a.judge_model, a.judge_base_url)
        tag = "".join(c if c.isalnum() or c in "._-" else "_" for c in a.judge_model)
        for s, k, gen_dir in plan:
            if gen_dir.exists():
                judge_absolute(a.run, s if a.samples == 1 else f"{s}/s{k}", jc, tag, workers=a.workers, limit=a.limit, out_root=a.out)
    report.build(a.out)
    print(f"[run] done in {(time.time() - t0) / 60:.1f} min; report at {a.out / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
