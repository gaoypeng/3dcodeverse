"""VLM judge for generated objects — the quality layer for prompts that have no ground truth.

Two modes, both judged from the 4 canonical renders (render_views.py) of the generated mesh:

  absolute   one object → JSON scores 1–5 on identity / structure / detail (+ overall) against the prompt
             (and the reference views, if the suite has them).  Cheap, but absolute LLM grades are noisy and
             drift between judge models — use them for triage, not for close comparisons.
  pairwise   two runs on the same prompt → winner a / b / tie / both_bad, judged in BOTH orders; a winner
             counts only when the two orders agree (position bias is the dominant failure of VLM judges —
             MT-Bench 65 % consistency, GPT-4V 51 %).  The pairwise rubric is the official 3DCodeBench arena
             prompt verbatim (metrics/llm_judge/prompts/image_judge.txt).

Backends: any OpenAI-compatible chat endpoint with image input (a local vLLM server running Qwen3.5-9B /
Qwen3-VL, OpenRouter, GPT) via --base-url, or Claude via --backend anthropic.  Results go to
<gen_dir>/judge_<tag>.jsonl (absolute) or <out>/pairwise_<a>_vs_<b>.jsonl.

    python -m llm.judge absolute --run qwen3_8b --suite 3dcodebench_text --judge-model gpt-5.4 --base-url …
    python -m llm.judge pairwise --run-a qwen3_8b --run-b official_gpt-5.5 --suite 3dcodebench_official_text …
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config
from ._jsonl import read_rows
from .render_views import VIEWS
from .suites import load_prompts, resolve

PAIRWISE_SYSTEM = (config.PKG_DIR / "data" / "official_prompts" / "image_judge.txt").read_text() \
    if (config.PKG_DIR / "data" / "official_prompts" / "image_judge.txt").exists() else ""

ABSOLUTE_SYSTEM = """You are an impartial expert judge of 3D models. An AI system was given a text prompt (and, for
image-to-3D tasks, reference images) and asked to produce a 3D object as code. You see four renders of the
resulting object from canonical viewpoints (azimuths 45/135/225/315 degrees). Judge ONLY from the renders.

Score each criterion on an integer scale 1-5 (1 = fails completely, 3 = partially right, 5 = fully convincing):
  identity   is it recognizably the requested object at all?
  structure  are the major parts present, in the right place, in plausible proportion, physically connected
             (no floating or interpenetrating pieces)?
  detail     shape fidelity and surface plausibility of the parts that exist; ornament and repeating features
             the prompt asks for are real geometry, not flat stand-ins.
  reference  (image-to-3D only, else null) faithfulness to the reference images: silhouette, proportions,
             distinctive features. Colour / material is NOT a criterion; renders may be untextured grey.
overall      your holistic 1-5 grade of "is this a good answer to the prompt", not an average.

Do NOT reward polygon count or visual flair when the object is wrong; a clean simple correct shape beats a busy
unrecognizable mess. Output STRICTLY one JSON object on one line, no markdown fences, no extra text:
{"identity": int, "structure": int, "detail": int, "reference": int|null, "overall": int, "reasoning": "<one to three sentences>"}"""


def _img_part(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    mt = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}}


def _renders(gen_dir: Path, tid: str) -> list[Path]:
    d = gen_dir / tid / "exec" / "renders"
    return [d / v for v in VIEWS if (d / v).exists()]


def _parse_json(text: str) -> dict | None:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    m = re.search(r"\{.*\}", cleaned, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class JudgeClient:
    def __init__(self, backend: str, model: str, base_url: str | None = None, temperature: float = 0.0, max_tokens: int = 600):
        self.backend, self.model, self.temperature, self.max_tokens = backend, model, temperature, max_tokens
        if backend == "openai":
            from openai import OpenAI
            self.client = OpenAI(base_url=base_url or os.environ.get("OPENAI_BASE_URL"),
                                 api_key=os.environ.get("OPENAI_API_KEY", "sk-local"), timeout=300, max_retries=2)
        elif backend == "anthropic":
            import anthropic
            self.client = anthropic.Anthropic(timeout=300, max_retries=2)
        else:
            raise ValueError(backend)

    def ask(self, system: str, parts: list[dict]) -> str:
        if self.backend == "openai":
            r = self.client.chat.completions.create(model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
                                                    messages=[{"role": "system", "content": system}, {"role": "user", "content": parts}])
            return r.choices[0].message.content or ""
        content = []
        for p in parts:
            if p["type"] == "text":
                content.append({"type": "text", "text": p["text"]})
            else:
                url = p["image_url"]["url"]
                mt, b64 = url.split(";base64,")
                content.append({"type": "image", "source": {"type": "base64", "media_type": mt[5:], "data": b64}})
        r = self.client.messages.create(model=self.model, system=system, max_tokens=self.max_tokens, temperature=self.temperature,
                                        messages=[{"role": "user", "content": content}])
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def _prompt_text(row: dict) -> str:
    return next((m["content"] for m in row["messages"] if m["role"] == "user"), "").replace("<image>", "").strip()


def _ref_parts(row: dict, max_refs: int = 4) -> list[dict]:
    refs = [resolve(p) for p in row.get("images", [])][:max_refs]
    if not refs:
        return []
    return [{"type": "text", "text": f"Reference image(s) the system was asked to reconstruct ({len(refs)}):"}] + [_img_part(p) for p in refs]


# ------------------------------------------------------------------------------------------------------ absolute
def judge_absolute_one(jc: JudgeClient, row: dict, gen_dir: Path) -> dict:
    tid = row["id"]
    rends = _renders(gen_dir, tid)
    if len(rends) < 4:
        return {"id": tid, "status": "NO_RENDERS", "n_renders": len(rends)}
    parts = [{"type": "text", "text": "PROMPT given to the system:\n" + _prompt_text(row)}] + _ref_parts(row) + \
            [{"type": "text", "text": "Four renders of the system's object (azimuths 45, 135, 225, 315 degrees):"}] + \
            [_img_part(p) for p in rends] + [{"type": "text", "text": "Return the JSON verdict now."}]
    t0 = time.time()
    try:
        raw = jc.ask(ABSOLUTE_SYSTEM, parts)
    except Exception as e:  # noqa: BLE001
        return {"id": tid, "status": "ERROR", "error": f"{type(e).__name__}: {str(e)[:200]}"}
    v = _parse_json(raw)
    if not v or "overall" not in v:
        return {"id": tid, "status": "PARSE_FAIL", "raw": raw[:500]}
    return {"id": tid, "status": "OK", **{k: v.get(k) for k in ("identity", "structure", "detail", "reference", "overall")},
            "reasoning": str(v.get("reasoning", ""))[:400], "latency_s": round(time.time() - t0, 1)}


def judge_absolute(run: str, suite: str, jc: JudgeClient, tag: str, workers: int = 4, limit: int | None = None,
                   out_root: Path = config.OUT_DIR, resume: bool = True) -> dict:
    gen_dir = out_root / run / suite
    rows = load_prompts(suite, limit)
    out_path = gen_dir / f"judge_{tag}.jsonl"
    done = {}
    if resume and out_path.exists():
        done = {r["id"]: r for r in read_rows(out_path)}
        done = {k: v for k, v in done.items() if v.get("status") == "OK"}
    todo = [r for r in rows if r["id"] not in done]
    recs = list(done.values())
    with ThreadPoolExecutor(workers) as ex:
        for i, fu in enumerate(as_completed({ex.submit(judge_absolute_one, jc, r, gen_dir): r for r in todo}), 1):
            recs.append(fu.result())
            if i % 20 == 0:
                print(f"[judge] {run}/{suite} {i}/{len(todo)}", flush=True)
    recs.sort(key=lambda r: r["id"])
    with out_path.open("w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ok = [r for r in recs if r["status"] == "OK"]
    n = len(rows)

    def mean(k, cond):
        vals = [r[k] for r in ok if isinstance(r.get(k), (int, float))]
        if not vals:
            return None
        return sum(vals) / len(vals) if cond else sum(vals) / n     # penalized: missing render / failure = 0

    summ = {"judge": f"{jc.backend}:{jc.model}", "tag": tag, "n": n, "n_judged": len(ok),
            "statuses": dict(Counter(r["status"] for r in recs)),
            **{f"{k}_cond": mean(k, True) for k in ("identity", "structure", "detail", "reference", "overall")},
            **{f"{k}_pen": mean(k, False) for k in ("identity", "structure", "detail", "overall")}}
    (gen_dir / f"judge_{tag}_summary.json").write_text(json.dumps(summ, indent=2))
    print(f"[judge] {run}/{suite}: judged {len(ok)}/{n} | overall cond {summ['overall_cond']} pen {summ['overall_pen']} "
          f"| identity {summ['identity_cond']} structure {summ['structure_cond']} detail {summ['detail_cond']}", flush=True)
    return summ


# ------------------------------------------------------------------------------------------------------ pairwise
def _pair_parts(row: dict, ra: list[Path], rb: list[Path]) -> list[dict]:
    parts = [{"type": "text", "text": "PROMPT given to both systems:\n" + _prompt_text(row)}] + _ref_parts(row)
    parts.append({"type": "text", "text": "Four renders of System A's 3D object:"})
    parts += [_img_part(p) for p in ra]
    parts.append({"type": "text", "text": "Four renders of System B's 3D object:"})
    parts += [_img_part(p) for p in rb]
    parts.append({"type": "text", "text": "Return the JSON verdict now."})
    return parts


def judge_pair_one(jc: JudgeClient, row: dict, dir_a: Path, dir_b: Path) -> dict:
    tid = row["id"]
    ra, rb = _renders(dir_a, tid), _renders(dir_b, tid)
    rec = {"id": tid, "a_rendered": len(ra) == 4, "b_rendered": len(rb) == 4}
    if len(ra) < 4 and len(rb) < 4:
        return {**rec, "winner": "both_bad", "status": "NO_RENDERS"}
    if len(ra) < 4:
        return {**rec, "winner": "b", "status": "A_NO_RENDER"}
    if len(rb) < 4:
        return {**rec, "winner": "a", "status": "B_NO_RENDER"}
    verdicts = []
    for swap in (False, True):
        x, y = (rb, ra) if swap else (ra, rb)
        try:
            raw = jc.ask(PAIRWISE_SYSTEM, _pair_parts(row, x, y))
        except Exception as e:  # noqa: BLE001
            return {**rec, "winner": None, "status": "ERROR", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        v = _parse_json(raw) or {}
        w = v.get("winner")
        if w in ("a", "b") and swap:
            w = "b" if w == "a" else "a"
        verdicts.append({"swap": swap, "winner": w, "reasoning": str(v.get("reasoning", ""))[:300]})
    w0, w1 = verdicts[0]["winner"], verdicts[1]["winner"]
    if w0 is None or w1 is None:
        final, status = None, "PARSE_FAIL"
    elif w0 == w1:
        final, status = w0, "OK"
    else:
        final, status = "tie", "DISAGREE"      # order-dependent verdict → not a winner
    return {**rec, "winner": final, "status": status, "verdicts": verdicts}


def judge_pairwise(run_a: str, run_b: str, suite: str, jc: JudgeClient, tag: str, workers: int = 4, limit: int | None = None,
                   out_root: Path = config.OUT_DIR) -> dict:
    dir_a, dir_b = out_root / run_a / suite, out_root / run_b / suite
    rows = load_prompts(suite, limit)
    recs = []
    with ThreadPoolExecutor(workers) as ex:
        for i, fu in enumerate(as_completed({ex.submit(judge_pair_one, jc, r, dir_a, dir_b): r for r in rows}), 1):
            recs.append(fu.result())
            if i % 20 == 0:
                print(f"[judge] pairwise {i}/{len(rows)}", flush=True)
    recs.sort(key=lambda r: r["id"])
    out_dir = out_root / "_pairwise"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{suite}__{run_a}__vs__{run_b}__{tag}.jsonl"
    with out_path.open("w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    c = Counter(r["winner"] for r in recs)
    decided = c.get("a", 0) + c.get("b", 0)
    summ = {"suite": suite, "run_a": run_a, "run_b": run_b, "judge": f"{jc.backend}:{jc.model}", "n": len(recs),
            "a_wins": c.get("a", 0), "b_wins": c.get("b", 0), "ties": c.get("tie", 0), "both_bad": c.get("both_bad", 0),
            "order_disagreements": sum(r.get("status") == "DISAGREE" for r in recs),
            "win_rate_a": (c.get("a", 0) + 0.5 * c.get("tie", 0)) / len(recs) if recs else None,
            "win_rate_a_decided": c.get("a", 0) / decided if decided else None}
    out_path.with_suffix(".summary.json").write_text(json.dumps(summ, indent=2))
    print(f"[judge] {suite} {run_a} vs {run_b}: A {summ['a_wins']} / B {summ['b_wins']} / tie {summ['ties']} / both_bad {summ['both_bad']} "
          f"(order disagreements {summ['order_disagreements']}) → win-rate A {summ['win_rate_a']:.3f}", flush=True)
    return summ


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["absolute", "pairwise"])
    ap.add_argument("--run", help="absolute: run name")
    ap.add_argument("--run-a")
    ap.add_argument("--run-b")
    ap.add_argument("--suite", required=True)
    ap.add_argument("--backend", choices=["openai", "anthropic"], default="openai")
    ap.add_argument("--judge-model", required=True)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--tag", default=None, help="label for the output files (default: judge model)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=config.OUT_DIR)
    a = ap.parse_args()
    jc = JudgeClient(a.backend, a.judge_model, a.base_url)
    tag = a.tag or re.sub(r"[^A-Za-z0-9._-]+", "_", a.judge_model)
    if a.mode == "absolute":
        if not a.run:
            raise SystemExit("--run is required for absolute mode")
        judge_absolute(a.run, a.suite, jc, tag, a.workers, a.limit, a.out)
    else:
        if not (a.run_a and a.run_b):
            raise SystemExit("--run-a and --run-b are required for pairwise mode")
        judge_pairwise(a.run_a, a.run_b, a.suite, jc, tag, a.workers, a.limit, a.out)


if __name__ == "__main__":
    main()
