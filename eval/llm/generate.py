"""Generation: turn prompt rows into <gen_dir>/<id>/{code.<ext>, raw.txt} + gens.jsonl, one backend at a time.

Backends
  vllm       in-process vLLM engine (local open models; text and image prompts; one load for many suites)
  openai     any OpenAI-compatible chat endpoint: a vLLM server (`vllm serve …`), OpenRouter, GPT, or a proxy —
             set --base-url / OPENAI_BASE_URL and OPENAI_API_KEY (dummy key ok for local servers)
  anthropic  Claude models through the Anthropic SDK (ANTHROPIC_API_KEY)
  reference  writes the suite's reference program as the "answer" — the executor/metric self-test

Every generated answer goes through `extract.extract(text, dialect)` (dialect-aware fenced-block selection,
think-block stripping) and the extraction metadata is kept per row, so format failures are separable from
capability failures in the report.

gens.jsonl row: {id, n_new_tokens, finished, truncated, has_code_block, latency_s, extract{…}, error?}
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config
from ._jsonl import read_rows, write_rows
from .executors import CODE_EXT
from .extract import extract, summarize
from .suites import resolve


def _data_url(path: Path) -> str:
    """Images as JPEG data-URLs (thumbnail 768 px, quality 88) — identical to finetune/eval/generate_vllm_img.py."""
    import io
    from PIL import Image

    im = Image.open(path).convert("RGB")
    im.thumbnail((768, 768))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def to_openai_messages(row: dict) -> list[dict]:
    """Chat messages in OpenAI content-parts form; `<image>` placeholders become image parts placed first."""
    msgs = []
    images = [resolve(p) for p in row.get("images", [])]
    for m in row["messages"]:
        if m["role"] == "user" and images:
            text = m["content"].replace("<image>", "").strip()
            parts = [{"type": "image_url", "image_url": {"url": _data_url(p)}} for p in images]
            parts.append({"type": "text", "text": text})
            msgs.append({"role": "user", "content": parts})
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    return msgs


def write_answer(gen_dir: Path, row: dict, text: str, meta: dict) -> dict:
    d = gen_dir / row["id"]
    d.mkdir(parents=True, exist_ok=True)
    code, xmeta = extract(text or "", row["dialect"])
    (d / f"code.{CODE_EXT[row['dialect']]}").write_text(code + "\n")
    (d / "raw.txt").write_text(text or "")
    rec = {"id": row["id"], **meta, "has_code_block": "```" in (text or ""), "extract": xmeta}
    return rec


def finish(gen_dir: Path, recs: list[dict], info: dict) -> None:
    recs.sort(key=lambda r: r["id"])
    write_rows(gen_dir / "gens.jsonl", recs)   # run_eval skips a stage whose gens.jsonl exists
    stats = summarize([r["extract"] for r in recs])
    n = max(1, len(recs))
    stats.update(info, n_rows=len(recs), truncated=sum(bool(r.get("truncated")) for r in recs),
                 errors=sum(bool(r.get("error")) for r in recs),
                 mean_new_tokens=round(sum(r.get("n_new_tokens") or 0 for r in recs) / n, 1))
    (gen_dir / "gen_stats.json").write_text(json.dumps(stats, indent=2))
    print(f"[gen] {gen_dir.name}: {len(recs)} rows | truncated {stats['truncated']} | no_fence {stats['no_fence']} "
          f"| multi_block {stats['multi_block']} | mean_new_tokens {stats['mean_new_tokens']}", flush=True)


# ----------------------------------------------------------------------------------------------------------- vLLM
class VllmEngine:
    def __init__(self, model: str, tp: int = 1, max_model_len: int = 16384, gpu_mem: float = 0.90,
                 max_images: int = 4, max_num_seqs: int | None = None, dtype: str = "bfloat16"):
        # WSL2: vLLM ≥0.28 refuses to start without pinned memory ("UVA is not available") unless this is set
        os.environ.setdefault("VLLM_WSL2_ENABLE_PIN_MEMORY", "1")
        # no CUDA toolkit on this box: FlashInfer's JIT sampler needs nvcc; the torch sampler does not
        os.environ.setdefault("VLLM_USE_FLASHINFER_SAMPLER", "0")
        from vllm import LLM

        self.model = model
        kw = {"max_num_seqs": max_num_seqs} if max_num_seqs else {}
        self.llm = LLM(model=model, dtype=dtype, tensor_parallel_size=tp, max_model_len=max_model_len,
                       gpu_memory_utilization=gpu_mem, enable_prefix_caching=True, trust_remote_code=True,
                       limit_mm_per_prompt={"image": max_images}, **kw)

    def generate(self, rows: list[dict], gen_dir: Path, temperature: float, max_new_tokens: int, seed: int = 0,
                 no_think: bool = True, top_p: float = 0.95) -> None:
        from vllm import SamplingParams

        gen_dir.mkdir(parents=True, exist_ok=True)
        sp = SamplingParams(temperature=temperature, top_p=top_p if temperature > 0 else 1.0, max_tokens=max_new_tokens, seed=seed)
        kw = {"chat_template_kwargs": {"enable_thinking": False}} if no_think else {}
        msgs = [to_openai_messages(r) for r in rows]
        t0 = time.time()
        outs = self.llm.chat(msgs, sp, use_tqdm=True, **kw)
        el = time.time() - t0
        recs, ntok = [], 0
        for r, o in zip(rows, outs):
            out = o.outputs[0]
            ntok += len(out.token_ids)
            recs.append(write_answer(gen_dir, r, out.text, {"n_new_tokens": len(out.token_ids),
                                                             "finished": out.finish_reason == "stop",
                                                             "truncated": out.finish_reason == "length"}))
        finish(gen_dir, recs, {"backend": "vllm", "model": self.model, "temperature": temperature, "seed": seed,
                               "max_new_tokens": max_new_tokens, "no_think": no_think, "tok_per_s": round(ntok / max(el, 1e-6), 1),
                               "wall_s": round(el, 1)})


# ----------------------------------------------------------------------------------------------------- API backends
def _openai_call(client, model: str, row: dict, temperature: float, max_new_tokens: int, no_think: bool,
                 extra_body: dict | None) -> dict:
    t0 = time.time()
    body = dict(extra_body or {})
    if no_think:
        body.setdefault("chat_template_kwargs", {"enable_thinking": False})
    resp = client.chat.completions.create(model=model, messages=to_openai_messages(row), temperature=temperature,
                                          max_tokens=max_new_tokens, extra_body=body or None)
    ch = resp.choices[0]
    text = ch.message.content or ""
    usage = resp.usage
    return {"text": text, "n_new_tokens": getattr(usage, "completion_tokens", None) if usage else None,
            "finished": ch.finish_reason == "stop", "truncated": ch.finish_reason == "length",
            "latency_s": round(time.time() - t0, 1)}


def _anthropic_call(client, model: str, row: dict, temperature: float, max_new_tokens: int) -> dict:
    t0 = time.time()
    system = "\n".join(m["content"] for m in row["messages"] if m["role"] == "system")
    images = [resolve(p) for p in row.get("images", [])]
    content = []
    for p in images:
        mt = mimetypes.guess_type(str(p))[0] or "image/png"
        content.append({"type": "image", "source": {"type": "base64", "media_type": mt,
                                                     "data": base64.b64encode(p.read_bytes()).decode()}})
    user = "\n".join(m["content"] for m in row["messages"] if m["role"] == "user").replace("<image>", "").strip()
    content.append({"type": "text", "text": user})
    resp = client.messages.create(model=model, system=system or None, max_tokens=max_new_tokens,
                                  temperature=temperature, messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    return {"text": text, "n_new_tokens": resp.usage.output_tokens, "finished": resp.stop_reason == "end_turn",
            "truncated": resp.stop_reason == "max_tokens", "latency_s": round(time.time() - t0, 1)}


def api_generate(backend: str, model: str, rows: list[dict], gen_dir: Path, temperature: float, max_new_tokens: int,
                 workers: int = 8, base_url: str | None = None, no_think: bool = True, extra_body: dict | None = None,
                 resume: bool = True) -> None:
    gen_dir.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    if resume and (gen_dir / "gens.jsonl").exists():
        for r in read_rows(gen_dir / "gens.jsonl"):
            if not r.get("error"):
                done[r["id"]] = r
    todo = [r for r in rows if r["id"] not in done]
    if backend == "openai":
        from openai import OpenAI
        client = OpenAI(base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
                        api_key=os.environ.get("OPENAI_API_KEY", "sk-local"), timeout=600, max_retries=2)
        call = lambda r: _openai_call(client, model, r, temperature, max_new_tokens, no_think, extra_body)  # noqa: E731
    elif backend == "anthropic":
        import anthropic
        client = anthropic.Anthropic(timeout=600, max_retries=2)
        call = lambda r: _anthropic_call(client, model, r, temperature, max_new_tokens)  # noqa: E731
    else:
        raise ValueError(backend)
    recs = list(done.values())
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(call, r): r for r in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            r = futs[fu]
            try:
                res = fu.result()
                recs.append(write_answer(gen_dir, r, res.pop("text"), res))
            except Exception as e:  # noqa: BLE001
                recs.append(write_answer(gen_dir, r, "", {"error": f"{type(e).__name__}: {str(e)[:300]}"}))
            if i % 20 == 0:
                print(f"[gen] {gen_dir.name} {i}/{len(todo)}", flush=True)
    finish(gen_dir, recs, {"backend": backend, "model": model, "temperature": temperature, "max_new_tokens": max_new_tokens,
                           "no_think": no_think, "wall_s": round(time.time() - t0, 1), "resumed": len(done)})


def reference_generate(rows: list[dict], gen_dir: Path) -> None:
    gen_dir.mkdir(parents=True, exist_ok=True)
    recs = []
    for r in rows:
        code = r["reference"].get("code") or ""
        fence = {"blender": "python", "cadquery": "python", "openscad": "openscad", "glsl": "glsl", "threejs": "html"}[r["dialect"]]
        text = code if code.lstrip().startswith("```") else f"```{fence}\n{code}\n```"
        recs.append(write_answer(gen_dir, r, text, {"n_new_tokens": 0, "finished": True, "truncated": False}))
    finish(gen_dir, recs, {"backend": "reference", "model": "reference", "temperature": 0.0, "max_new_tokens": 0})
