"""Suite registry + prompt loading.

A *suite* is one prompt file under data/prompts/<name>.jsonl plus how to run and score it.  Everything a
runner needs is derived from the rows themselves (dialect, images, reference), so adding a suite = adding a
JSONL; the table below only pins per-suite decoding defaults and which metrics apply.

Decoding defaults follow finetune/docs/REPORT.md §13.4: greedy for Blender/CadQuery, and for the
long-output dialects (OpenSCAD / GLSL / three.js) sampling at T=0.7 is the *primary* protocol because
greedy decoding degenerates into runaway loops there; run both when you can afford it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config


@dataclass(frozen=True)
class SuiteSpec:
    name: str
    dialect: str
    kind: str                      # "gt" (reference mesh), "exec" (execution only), "rubric" (no GT, judge optional)
    temperature: float = 0.0
    max_new_tokens: int = 8192
    n_points: int = 10000
    metrics: tuple[str, ...] = ("exec",)
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


SUITES: dict[str, SuiteSpec] = {
    "3dcodebench_text": SuiteSpec("3dcodebench_text", "blender", "gt", 0.0, 8192, 10000, ("exec", "geometry"),
                                  "212 Infinigen-family objects, structured instruction → bpy; GT mesh", ("headline", "text")),
    "3dcodebench_desc": SuiteSpec("3dcodebench_desc", "blender", "gt", 0.0, 8192, 10000, ("exec", "geometry"),
                                  "same 212 objects, short natural description", ("text",)),
    "3dcodebench_img1": SuiteSpec("3dcodebench_img1", "blender", "gt", 0.0, 8192, 10000, ("exec", "geometry"),
                                  "image→code: 1 GT view", ("vlm",)),
    "3dcodebench_img4": SuiteSpec("3dcodebench_img4", "blender", "gt", 0.0, 8192, 10000, ("exec", "geometry"),
                                  "image→code: 4 GT views", ("vlm",)),
    "3dcodebench_img4text": SuiteSpec("3dcodebench_img4text", "blender", "gt", 0.0, 8192, 10000, ("exec", "geometry"),
                                      "image+text→code: 4 GT views + description", ("vlm",)),
    # the official 3DCodeBench protocol (paper arXiv:2606.01057): raw-python contract, T=0.7, 16k budget,
    # exec + SigLIP-2 / DINOv3 view similarity + squared unit-sphere Chamfer, conditional and penalized
    "3dcodebench_official_text": SuiteSpec("3dcodebench_official_text", "blender", "gt", 0.7, 16384, 8192,
                                           ("exec", "geometry", "chamfer_official", "image_sim"),
                                           "official text_to_3D: description prompt, official system prompt, official metrics", ("official", "text")),
    "3dcodebench_official_img4": SuiteSpec("3dcodebench_official_img4", "blender", "gt", 0.7, 16384, 8192,
                                           ("exec", "geometry", "chamfer_official", "image_sim"),
                                           "official image_to_3D: 4 GT views, official system prompt, official metrics", ("official", "vlm")),
    "heldout_blender": SuiteSpec("heldout_blender", "blender", "gt", 0.0, 8192, 5000, ("exec", "geometry"),
                                 "103 held-out bioinspired3d/non-test-factory prompts; GT = executed reference", ("heldout",)),
    "heldout_cadquery": SuiteSpec("heldout_cadquery", "cadquery", "gt", 0.0, 8192, 5000, ("exec", "geometry"),
                                  "200 held-out DeepCAD/Articraft prompts; GT = executed reference", ("heldout",)),
    "heldout_openscad": SuiteSpec("heldout_openscad", "openscad", "gt", 0.7, 8192, 5000, ("exec", "geometry"),
                                  "50 held-out Thingiverse prompts; GT = executed reference", ("heldout", "long-output")),
    "heldout_glsl": SuiteSpec("heldout_glsl", "glsl", "exec", 0.7, 8192, 0, ("exec", "render"),
                              "200 held-out Shadertoy prompts; compile + WebGL render check", ("heldout", "long-output")),
    "heldout_threejs": SuiteSpec("heldout_threejs", "threejs", "exec", 0.7, 12288, 0, ("exec",),
                                 "40 held-out single-file three.js pages; headless-Chromium check", ("heldout", "long-output")),
}


def _battery_specs() -> dict[str, SuiteSpec]:
    out = {}
    for p in sorted(config.PROMPTS_DIR.glob("harness_*.jsonl")):
        name = p.stem
        with p.open() as f:
            first = json.loads(f.readline())
        dialect = first["dialect"]
        out[name] = SuiteSpec(name, dialect, "rubric", 0.7 if dialect in ("glsl", "threejs") else 0.0, 8192, 0,
                              ("exec", "render") if dialect == "glsl" else ("exec",),
                              f"harness battery {name}: execution + optional VLM judge, no GT", ("rubric",))
    return out


def all_suites() -> dict[str, SuiteSpec]:
    return {**SUITES, **_battery_specs()}


def get_suite(name: str) -> SuiteSpec:
    s = all_suites().get(name)
    if s is None:
        raise KeyError(f"unknown suite {name!r}; known: {sorted(all_suites())}")
    return s


def prompt_path(name: str) -> Path:
    return config.PROMPTS_DIR / f"{name}.jsonl"


def load_prompts(name: str, limit: int | None = None) -> list[dict]:
    p = prompt_path(name)
    if not p.exists():
        raise FileNotFoundError(f"{p} — run `python -m llm.build_prompts` first")
    rows = [json.loads(l) for l in p.open() if l.strip()]
    return rows[:limit] if limit else rows


def resolve(rel: str | None) -> Path | None:
    return (config.DATA_DIR / rel) if rel else None


def expand(names: list[str]) -> list[str]:
    """Expand suite names / tags: 'all', 'text', 'vlm', 'heldout', 'rubric', 'headline', 'long-output'."""
    known = all_suites()
    out: list[str] = []
    for n in names:
        if n == "all":
            out += [k for k, s in known.items() if "rubric" not in s.tags]
        elif n in known:
            out.append(n)
        else:
            hit = [k for k, s in known.items() if n in s.tags]
            if not hit:
                raise KeyError(f"unknown suite or tag {n!r}")
            out += hit
    seen, uniq = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq
