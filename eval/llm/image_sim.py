"""Render-based similarity between generated and reference views (the official 3DCodeBench image metric).

For each task: 4 rendered views of the generated mesh vs the 4 reference views (same turntable azimuths),
embedded with SigLIP-2 (google/siglip2-so400m-patch16-naflex, image features) or DINOv3
(facebook/dinov3-vitl16-pretrain-lvd1689m, pooler output) — the two encoders whose scores track the
3DCodeBench human arena (Pearson 0.964 / Spearman 0.972).  Two aggregates over the 4×4 cosine matrix:

  view_paired      mean of the diagonal (same azimuth) — penalises a 90/180° yaw mismatch
  best_assignment  Hungarian 1-to-1 matching — immune to view permutations

and two averages: conditional (tasks with all 4 renders) and penalized (missing renders = 0, over all tasks).
A text→image score (prompt vs rendered views, SigLIP-2 only) is available for text suites.

Weights are loaded from the HF cache (local_files_only fallback); needs torch + transformers + a GPU.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ENCODERS = {
    "siglip2": {"model_id": "google/siglip2-so400m-patch16-naflex", "kind": "siglip"},
    "siglip2-base": {"model_id": "google/siglip2-base-patch16-224", "kind": "siglip"},
    "dinov3": {"model_id": "facebook/dinov3-vitl16-pretrain-lvd1689m", "kind": "pooler"},
    "dinov2": {"model_id": "facebook/dinov2-base", "kind": "pooler"},
}
VIEWS = ["Image_005.png", "Image_015.png", "Image_025.png", "Image_035.png"]


class Encoder:
    def __init__(self, name: str = "siglip2", device: str = "cuda"):
        import torch
        from transformers import AutoImageProcessor, AutoModel, AutoProcessor

        spec = ENCODERS[name]
        self.name, self.kind, self.device = name, spec["kind"], device

        def load(cls, mid):
            try:
                return cls.from_pretrained(mid)
            except OSError:
                return cls.from_pretrained(mid, local_files_only=True)

        self.processor = load(AutoProcessor if self.kind == "siglip" else AutoImageProcessor, spec["model_id"])
        self.model = load(AutoModel, spec["model_id"]).to(device).eval()
        self.torch = torch

    def embed_images(self, paths: list[Path], batch_size: int = 16) -> np.ndarray:
        from PIL import Image

        out = []
        for i in range(0, len(paths), batch_size):
            imgs = [_flatten(Image.open(p)) for p in paths[i:i + batch_size]]
            inputs = self.processor(images=imgs, return_tensors="pt").to(self.device)
            with self.torch.no_grad():
                if self.kind == "siglip":
                    f = self.model.get_image_features(**inputs)
                    f = f if isinstance(f, self.torch.Tensor) else f.pooler_output
                else:
                    f = self.model(**inputs).pooler_output
            f = f / f.norm(dim=-1, keepdim=True)
            out.append(f.float().cpu().numpy())
        return np.concatenate(out, 0) if out else np.zeros((0, 1))

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        assert self.kind == "siglip", "text embeddings need SigLIP"
        inputs = self.processor(text=texts, return_tensors="pt", padding="max_length", truncation=True, max_length=64).to(self.device)
        with self.torch.no_grad():
            f = self.model.get_text_features(**inputs)
            f = f if isinstance(f, self.torch.Tensor) else f.pooler_output
        f = f / f.norm(dim=-1, keepdim=True)
        return f.float().cpu().numpy()


def _flatten(im):
    """RGBA renders on a transparent film → composite on the dark world colour so encoders see what a viewer sees."""
    from PIL import Image

    if im.mode == "RGBA":
        bg = Image.new("RGBA", im.size, (3, 3, 5, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def pair_scores(gen: list[np.ndarray | None], ref: list[np.ndarray | None]) -> dict:
    from scipy.optimize import linear_sum_assignment

    m = [[float(gen[i] @ ref[j]) if gen[i] is not None and ref[j] is not None else None for j in range(4)] for i in range(4)]
    diag = [m[v][v] for v in range(4) if m[v][v] is not None]
    vi = [i for i in range(4) if gen[i] is not None]
    vj = [j for j in range(4) if ref[j] is not None]
    assigned = None
    if vi and vj:
        sub = np.array([[m[i][j] for j in vj] for i in vi])
        r, c = linear_sum_assignment(-sub)
        assigned = float(sub[r, c].mean())
    return {"n_paired": len(diag), "view_paired": float(np.mean(diag)) if diag else None, "best_assignment": assigned, "cos_matrix": m}


def score_gen_dir(gen_dir: Path, rows: dict[str, dict], resolve, encoder_name: str = "siglip2", device: str = "cuda",
                  text_sim: bool = False) -> dict:
    """rows: {id: prompt row}; writes <gen_dir>/image_sim_<enc>.jsonl and returns the aggregate."""
    enc = Encoder(encoder_name, device)
    paths, index = [], []
    per = {}
    for tid, row in rows.items():
        refs = [resolve(p) for p in row["reference"].get("renders", [])]
        ref_by = {p.name: p for p in refs}
        gen_dir_views = gen_dir / tid / "exec" / "renders"
        per[tid] = {"gen": [None] * 4, "ref": [None] * 4}
        for k, v in enumerate(VIEWS):
            g = gen_dir_views / v
            if g.exists():
                index.append((tid, "gen", k)); paths.append(g)
            if v in ref_by and ref_by[v].exists():
                index.append((tid, "ref", k)); paths.append(ref_by[v])
    if not paths:
        return {"encoder": encoder_name, "n": len(rows), "error": "no renders"}
    embs = enc.embed_images(paths)
    for (tid, kind, k), e in zip(index, embs):
        per[tid][kind][k] = e
    text_emb = None
    if text_sim and enc.kind == "siglip":
        prompts = [next((m["content"] for m in rows[t]["messages"] if m["role"] == "user"), "") for t in rows]
        text_emb = dict(zip(rows, enc.embed_texts([p.replace("<image>", "").strip()[:800] for p in prompts])))
    recs = []
    for tid in rows:
        s = pair_scores(per[tid]["gen"], per[tid]["ref"])
        rec = {"id": tid, "n_gen_views": sum(g is not None for g in per[tid]["gen"]),
               "n_ref_views": sum(r is not None for r in per[tid]["ref"]), **{k: s[k] for k in ("n_paired", "view_paired", "best_assignment")}}
        if text_emb is not None:
            gv = [g for g in per[tid]["gen"] if g is not None]
            rec["text_image"] = float(np.mean([g @ text_emb[tid] for g in gv])) if gv else None
        recs.append(rec)
    with (gen_dir / f"image_sim_{encoder_name}.jsonl").open("w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    n = len(recs)
    full = [r for r in recs if r["n_paired"] == 4]

    def agg(field):
        cond = float(np.mean([r[field] for r in full])) if full else None
        pen = float(sum((r[field] or 0.0) for r in recs) / n) if n else None
        return {"conditional": cond, "penalized": pen}

    summ = {"encoder": encoder_name, "model_id": ENCODERS[encoder_name]["model_id"], "n": n, "n_full": len(full),
            "view_paired": agg("view_paired"), "best_assignment": agg("best_assignment")}
    if text_emb is not None:
        with_t = [r for r in recs if r.get("text_image") is not None]
        summ["text_image"] = {"conditional": float(np.mean([r["text_image"] for r in with_t])) if with_t else None,
                              "penalized": float(sum((r.get("text_image") or 0.0) for r in recs) / n) if n else None}
    return summ
