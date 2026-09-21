# Per-benchmark records (raw fact sheets, 2026-09-08)


<!-- ===== recent2026.md ===== -->

# 2026-era / cross-cutting entries (collected by lead agent)

## 3DCodeBench (Gao et al., arXiv 2606.01057, May 31 2026)
- Paper https://arxiv.org/abs/2606.01057 ; HTML https://arxiv.org/html/2606.01057 ; site https://www.3dcodebench.com/ ; code https://github.com/gaoypeng/3dcodebench ; data HF `YipengGao/3DCode`
- Task: text (object description / procedural instruction / factory-level spec) or 4 canonical multi-view reference images (45/135/225/315 deg azimuth) -> standalone Blender 5.0 Python script
- Data: 212 Infinigen-derived object categories; 26K prompt/code/mesh triplets; 12,963 fine-tune instances (site says 12,720 factory instances); 212-instance eval set; median script 387 lines, mean 531. GT = Infinigen factories migrated by a VLM agent into standalone bpy + baked GLB. Dedup via "Class Deduplication" in Experience Library. No explicit difficulty tiers.
- Env: Blender 5.0, 240 s wall-clock timeout, fresh subprocess per run, Cycles renders from 4 canonical views.
- Metrics: Exec_i = 1[script runs within 240 s and produces >=1 mesh]; SigLIP-2 view-paired cosine similarity (4 views), DINOv3-ViT-L16 same; Chamfer on exported GLB point clouds (normalized), Uni3D 3D-3D cosine and cross-modal (text->pc, image->pc); each reported "conditional" (only successes) or "penalized" (failures = 0). Human: 3DCodeArena pairwise votes (a/b/tie/both bad; tie & both-bad = 0.5/0.5), Bradley-Terry MLE Elo per modality, 1000-resample bootstrap CI; ~3,100 votes at writing.
- Correlation with human Elo: SigLIP-2 Pearson r=0.964; DINOv3 Spearman rho=0.972; executability weakest correlate.
- Results (Table A.1, best thinking level, avg text+image): GPT-5.5 exec 0.906 / SigLIP2 0.834 / DINOv3 0.576 / CD 0.059 / Uni3D 0.562 / Elo 1163; Gemini 3.1 Pro 0.725/0.824/0.569/0.069/0.567/1147; Claude Opus 4.7 0.910/0.814/0.545/0.067/0.490/1006; Gemma 4 31B (open) 0.582/0.801/0.518/0.076/0.494/952; Gemma 4 26B 0.517/0.786/0.483/0.077/0.435/859; Claude Haiku 4.5 0.502/0.761/0.413/0.095/0.363/799. Gemini 2.5 Pro (7.1% exec) and GPT-5.4 Nano (6.1%) excluded (<10% exec, Blender 5.0 API cutoff).
- Findings: 2 retries with traceback lift exec 0.702->0.974 (+27.2 pp) and SigLIP-2 +0.128; native coding-agent harness reaches ~0.995 exec but no conditional quality gain (Uni3D 3D-3D -0.003 matched subset). Failures mostly API mismatches; successful renders suffer disconnected/floating parts. Thinking budget: lightweight models +~19 pp exec minimal->high, frontier plateau.
- No 7-9B open model; smallest open = Gemma 4 26B. License CC BY 4.0 (arXiv).

## P3D-Bench (Yang et al., arXiv 2606.11152, Jun 9 2026)
- https://arxiv.org/html/2606.11152v1 ; project https://lucasqaq.github.io/p3d/
- Tasks: Text-to-3D (descriptive vs parametric spec) -> JSON/OpenSCAD; Image-to-3D (single render) -> CadQuery/OpenSCAD/Three.js; Assembly-3D (image + part annotations) -> CadQuery/OpenSCAD.
- Data: 400 text cases from Text2CAD v1.1 (176,017 programs), 400 image cases + 203 assembly cases from Fusion 360 Gallery (8,251 assemblies). Filtering: deterministic executable/renderable check, MLLM review (Gemini 3.1 Pro) for ambiguity/quality, DINOv2 near-duplicate removal, complexity-balanced sampling over easy/medium/hard tiers. Prompts: GPT-5.5 writes descriptive + parametric specs; Claude Opus 4.6 labels parts; MLLM consistency check; static validators cross-check every numeric value against GT geometry.
- Env: format-specific compilers, multiview renders; mesh alignment = normalize, translate, rotate (min CD), bounded scale/position refinement.
- Metrics: Valid (binary compile+render); Geometry bucket: CD, F@0.05, F@0.01, Normal Consistency, IoU (CSG for single parts, voxel for assemblies); Topology bucket: NoOE (edges w/ exactly 2 faces), InvN (reversed normals), NM (non-manifold); Judge bucket (Gemini 3.1 Pro): QA-S (4 semantic MCQ/case), QA-P (8 parametric Qs/case), J-Sem/J-Geo/J-Aes on 1-10; Part bucket: Claude Opus 4.6 decomposes prediction, fidelity gate CD<5e-4 & IoU>0.95, PartFS (Hungarian-matched F-score), PartMatchF1. Buckets = equal-weight means in [0,1]; invalid outputs get worst value.
- Results: Text-to-3D descriptive judge: GPT-5.5 0.875 (valid 0.998), Gemini 3.1 Pro 0.865, Claude Opus 4.6 0.850. Image-to-3D avg: GPT-5.5 Geo 0.549 / Judge 0.562 / Valid 0.979. Assembly: GPT-5.5 Geo 0.586 / Part 0.629. J-Sem ~0.80 but J-Geo ~0.35 on assemblies; PartMatchF1 ~0.50.
- Error taxonomy: syntax (73% of text-descriptive failures), undefined reference (~16% CadQuery), parameter (55% Image-to-3D CadQuery), geometry (24-27% CadQuery). Three.js meshes non-manifold -> excluded from topology/part scoring. Multi-turn: Gemini +0.03 avg, GPT-5.5 ~0.
- Open 7-9B: none (domain models Text2CAD 363M, cadrille 2B, CAD-Coder 13B). Availability: project page only; license arXiv non-exclusive.

## Text2CAD-Bench (Wang et al., arXiv 2605.18430, May 18 2026)
- https://arxiv.org/html/2605.18430v1 ; CC BY 4.0; code/leaderboard "released after accept" (URL placeholder).
- Task: text -> CadQuery. 600 human-curated examples x 2 prompt styles = 1,200 prompts. Tiers: L1 basic 200, L2 intermediate 200 (booleans), L3 advanced 100 (sweep/loft/shell/freeform), L4 real-world 100. Prompt styles: geometric (global->local->details->summary) vs sequence (command-style); L1 avg 89.9 vs 81.5 words, L3 429.4 vs 173.6. Three engineering annotators verify executability, description-geometry correspondence, complexity, cross-style consistency. Few-shot examples from held-out set.
- Env: Python 3.10, CadQuery 2.4, trimesh, isolated, 60 s timeout, STEP -> point cloud.
- Metrics: CD bidirectional mean NN distance, 30,000 points/mesh, x10^3; Invalidity Rate (fail/timeout/degenerate); IoU on 256^3 voxels; L4: GLM-4.6V judge on 8 multi-view renders, 0-10 across Overall/External/Functional/Extended/Detail features + overall similarity; 3 human annotators re-scored stratified L4 subset -> consistent trend, no same-family bias.
- Results: GPT-5.2 L1 CD 44.31 / IR 11.1% / IoU 0.59 (geometric prompts) -> L3 CD 93.46 / IR 68.0% / IoU 0.23. Text2CAD IR 2-11% but CD 219-267, IoU ~0 (executable but unfaithful). L4: Gemini-3-Flash lowest IR 17% but lowest feature scores. No open 7-9B model reported.
- Pitfalls: survivorship bias (CD/IoU on executed subset only when IR varies); CadQuery-only; single-turn.

## BenchCAD (Zhang et al., arXiv 2605.10865, May 2026)
- https://arxiv.org/html/2605.10865v1 ; data HF `BenchCAD/BenchCAD` (CC-BY-4.0); code GitHub `BenchCAD/BenchCAD-main` (MIT); site https://benchcad.github.io/BenchCAD_webpage/
- Tasks: Vision2Code (4 orthographic views -> CadQuery), Vision QA / Code QA (numeric answers), Code Edit (program + instruction -> CadQuery).
- Data: 17,900 execution-verified CadQuery programs over 106 industrial part families (52 anchored to ISO/DIN/EN/ASME/IEC tables); 2,400 QA items; 748 edit pairs; easy/medium/hard tiers; 10 families held out OOD; expert visual sign-off.
- Env: sandboxed subprocess, 30 s budget, degenerate filter volume <= 1e-6 mm^3, error quarantine (parse/import/runtime/timeout).
- Metrics: voxel IoU (rotation-invariant variant App. M), CD, exec_pct, Essential-Op Recall (helix/loft/sweep/twist-extrude), Feature-F1, Edit normalized accuracy = clip((IoU_gen - IoU_orig)/(1 - IoU_orig),0,1), QA accuracy (+-5% ratios, exact ints).
- Results: qwen3-VL-2B RL (iid) IoU 0.752 / exec 98.9% / total 0.768; CADEvolve v3 0.750 / 92.7% / 0.601; Claude Opus 4.7 thinking IoU 0.267 / exec 90.4% / 0.378; GPT-5.3 thinking 0.207 / 67.5% / 0.212. Vision QA best Gemini 3.1 Pro 0.587.
- Findings: advanced ops near-zero recall in pretrained models; ~64% of "successful" edits silently corrupt unrelated features; hard-tier drop >45 pts.

## CADFS (Pyatov et al., arXiv 2605.01925, May 2026)
- https://arxiv.org/abs/2605.01925 ; site https://voyleg.github.io/cadfs/
- Task: text or multi-view images -> Onshape FeatureScript program (15 ops). 451k real designs (subset of ABC, superset of DeepCAD/Text2CAD), ~15% discarded by reproduce-original validation. Annotations by Annotator+Reviewer LLM pair with FeatureScript docs.
- Metrics (App.): CD = symmetric mean squared NN distance, 100k points, median, relative to model size, x10^3; Edge CD (points near edges, r=0.004, |n_i.n_j|<0.2); Normal Consistency; COV/MMD/JSD on 3k shapes x10 repeats with 2k points (DeepCAD protocol); IR = N_inv/N_gen (kernel error or invalid geometry/topology).
- Table 4 (text-conditioned, their test set): Qwen2-VL-2B CD 0.06 / ECD 8.5 / NC 98.9 / MMD 8.10 / COV 84.9 / JSD 0.59 / IR 10%; Qwen3-8B 0.06 / 7.8 / 98.9 / 8.04 / 85.0 / 0.60 / IR 8%.
- Table 1 comparison: Fusion360 8,625; DeepCAD 179k; Text2CAD 170k; Omni-CAD 275k; cadrille 170k; CAD-Recode 1M synthetic; CADFS 451k real.

## CADEvolve (Elistratov et al., arXiv 2602.16317, Feb 2026)
- https://arxiv.org/html/2602.16317v1 ; code https://github.com/zhemdi/CADEvolve ; data https://huggingface.co/datasets/kulibinai/cadevolve ; model https://huggingface.co/kulibinai/cadevolve-rl1 ; CC BY 4.0
- Task: 8 views (6 ortho + 2 iso, 2x4 grid) -> CadQuery. Data: 7,945 evolved generators -> ~800k scripts -> ~1.3M canonicalized (scale longest extent = 200, 24 rotations).
- Metrics: CD 8,192 vs 8,192 points x10^3, MEDIAN "to reduce invalidity bias"; volumetric IoU mean; IR = fail to compile or non-watertight/degenerate.
- Results vs cadrille-RL: DeepCAD CD 0.16 vs 0.17, IoU 91.1 vs 92.2, IR 0.1 vs 0.1; Fusion360 0.16/84.0/0.2 vs 0.17/84.6/0.1; MCB 0.52/55.2/0.4 vs 0.87/47.6/2.5.

## Text-to-CadQuery (Xie & Ju, arXiv 2505.06507, May 2025)
- https://arxiv.org/abs/2505.06507 ; CC BY 4.0. 170k CadQuery annotations converted from Text2CAD; six open models fine-tuned; top-1 exact-match 58.8% -> 69.3%, CD -48.6% (abstract; per-size numbers not extracted).

## BlenderRAG (Rondelli et al., arXiv 2605.00632, May 2026)
- https://arxiv.org/abs/2605.00632 ; https://github.com/MaxRondelli/BlenderRAG ; CC BY 4.0
- Task: text -> Blender Python. Dataset: 500 expert-validated (text, code, image) triplets, 50 categories (25 indoor / 25 outdoor) x 10 variants; code drafted by Claude Opus 4.1 from detailed prompts then expert-validated.
- Eval: only 30 out-of-distribution prompts (novel objects absent from dataset). Metrics: compilation success % (code executes w/o error); semantic alignment = normalized CLIP cosine between prompt and rendered image. Table 1 (Base -> RAG): Claude Sonnet 4.5 compile 43.3 -> 76.7, CLIP 0.544 -> 0.780; GPT-5 Chat 56.6 -> 66.7, 0.267 -> 0.777; Gemini 3 Flash 53.3 -> 80.0, 0.498 -> 0.770; Mistral Large 10.1 -> 56.7, 0.327 -> 0.769; average 40.8 -> 70.0, 0.409 -> 0.774. No open 7-9B model; no Chamfer or VLM judge in the paper (only compile + CLIP); n=30 means each compile-rate point is 3.3 pp.

## CodeGen-3D (Ji et al., IEEE Access 14:18181-18192, Jan 2026)
- https://scholarworks.sjsu.edu/faculty_rsca/6835/ ; DOI 10.1109/ACCESS.2026.3654948
- Task: text -> Blender script; 100 prompts from Objaverse captions (Cap3D / DiffuRank / Llama-based). Metrics: error rate (compile+runtime), multi-view CLIP (avg & max), GPT-4o pairwise VLM judge. Eight general LLMs + BlenderLLM + iterative agent: failure 3-4% (specialized/iterative) vs 21-92% (general); iterative agent 33.3% GPT-4o win rate. Finding: judge preferences diverge from CLIP alignment. No code URL found.

## LL3M (Lu et al., arXiv 2508.08228, Aug 2025)
- https://arxiv.org/abs/2508.08228 ; https://threedle.github.io/ll3m — multi-agent Blender-script system; abstract gives no quantitative benchmark. Not a benchmark.

## SceneActBench (Zhao et al., arXiv 2607.22393, Jul 2026)
- https://arxiv.org/abs/2607.22393 — 210 source instances -> 520 task cases, 5 tasks, agent-environment loop, geometric metrics vs hidden GT, 11 proprietary VLM configs score 38.6-50.2. Whether outputs are code: not established from abstract.

## Other hits not yet verified
- "LLM-based 3D Model Generation of MHE for OpenSCAD" (Procedia CS 2026) https://www.sciencedirect.com/science/article/pii/S1877050926000773 — 403 on fetch; search snippet: functions in simple/intermediate/advanced tiers by number of components.
- ModelRift "OpenSCAD LLM Benchmark" blog https://modelrift.com/blog/openscad-llm-benchmark/ — fetch timed out.

<!-- ===== cad.md ===== -->

# Survey: benchmarks / datasets / metrics for LLM & VLM generation of CAD code (2021 – Sep 2026)

Compiled 2026-09-08 from primary sources (arXiv abstract + HTML pages, GitHub READMEs, HF dataset cards, project pages). Every number below is copied from the cited source; "not found" means the source I could reach did not state it. Numbers were NOT recomputed or inferred.

Conventions used repeatedly across the field (see the cross-cutting notes at the end):
- **CD** = Chamfer distance, almost always reported ×10³ on shapes normalised to a unit cube / [-0.5,0.5]³; point counts vary 1k–30k and are frequently unstated.
- **IR** = invalidity ratio (fraction of generations that do not execute / do not yield a valid solid). **VSR** = valid-shape / valid-syntax rate = 1 − IR (definitions differ).
- **IoU** = volumetric IoU on voxel grids (resolution varies 96³–256³) or exact CSG.

---

## 0. Foundational datasets (not LLM benchmarks per se, but every later benchmark derives from them)

### DeepCAD (Wu, Xiao, Zheng — ICCV 2021)
- URLs: paper https://arxiv.org/abs/2105.09492 ; code+data https://github.com/rundiwu/DeepCAD ; parser https://github.com/rundiwu/onshape-cad-parser
- Task: unconditional/autoencoding generation of sketch-and-extrude command sequences (DeepCAD JSON / 17-d command vectors). Not text-conditioned.
- Size/construction: 178,238 CAD models "parsed from Onshape public documents with links from ABC dataset" (README). Standard split used by all derivatives: 90/5/5 → test = 8,046 models (count stated by CAD-Recode https://arxiv.org/html/2412.14042 and cadrille https://arxiv.org/html/2505.22914). Sketch+extrude only.
- Metrics defined here and inherited everywhere: command accuracy, parameter accuracy, Chamfer distance, invalid ratio (autoencoding); COV, MMD, JSD (generation) — README https://github.com/rundiwu/DeepCAD. GenCAD states DeepCAD-style CD is computed on 2,000 sampled points https://arxiv.org/html/2409.16294.
- Pitfalls reported by derivatives: heavy class imbalance — Text2CAD v1.0 notes ~25% cuboids and ~8% cylinders (HF card https://huggingface.co/datasets/SadilKhan/Text2CAD); CADmium filtered cuboids to 5% https://arxiv.org/html/2507.09792; FlexCAD/SkexGen remove duplicates and invalid sequences https://arxiv.org/html/2411.05823. Fusion360 Gallery is NOT in ABC, so it is the usual "clean" OOD set (CADmium https://arxiv.org/html/2507.09792).
- License: MIT (repo). Source models fall under Onshape ToS (SGP-Bench notes this https://arxiv.org/html/2408.08313v3).

### Fusion 360 Gallery (Willis et al. — SIGGRAPH 2021)
- URLs: https://github.com/AutodeskAILab/Fusion360GalleryDataset ; license https://github.com/AutodeskAILab/Fusion360GalleryDataset/blob/master/LICENSE.md ; build123d port https://github.com/zalo/Fusion360GalleryDataset-build123d
- Subsets: Reconstruction 8,625 sketch+extrude sequences; Segmentation 35,680 parts; Assembly 8,251 assemblies / 154,468 parts (README). Test slice used by CAD-Recode/cadrille = 1,725 models https://arxiv.org/html/2412.14042.
- Formats: JSON sequences, STEP, meshes. A community port of 7,683 models as executable build123d scripts exists (zalo repo).
- License: custom "Fusion 360 Gallery Dataset License" — non-commercial research only, no redistribution of the whole dataset, downstream restrictions must be preserved (LICENSE.md). This matters: several 2026 benchmarks (CADBench, P3D-Bench, CADEngBench) build on it.

---

## 1. Understanding / QA benchmarks over CAD programs

### SGP-Bench — "Can Large Language Models Understand Symbolic Graphics Programs?" (Qiu, Liu et al., 2024; ICLR 2025)
- URLs: paper https://arxiv.org/abs/2408.08313 (HTML v3 https://arxiv.org/html/2408.08313v3) ; code https://github.com/sgp-bench/sgp-bench ; HF `sgp-bench/sgp-bench`, `sgp-bench/sgp-mnist` ; project https://sgp-bench.github.io/
- Task: program → multiple-choice QA (the model sees ONLY the code, never the render). SVG part: 1,085 programs / 4,340 questions (Kaggle SVG-icons). CAD part: 2,400 programs / 2,400 questions = 1,000 DeepCAD (3D) + 700 Fusion360 Reconstruction (3D complex) + 700 SketchGraphs (2D sketches). "Each of our 3 CAD subsets follows a different language syntax" — i.e. native DeepCAD / Fusion360 / SketchGraphs JSON-style DSLs, NOT CadQuery. Also SGP-MNIST (1,000 digit SVGs) and an invariance test (translation/rotation perturbation of the code).
- Construction: questions + 4 options generated by GPT-4o from renders; human agreement checked on 500 sampled questions.
- Metric: accuracy.
- Results (Table 1, CAD accuracy 3D-DeepCAD | 3D-Fusion360 | 2D-SketchGraphs): Llama-3-8B 0.550|0.633|0.472; Mistral-7B-v0.3 0.495|0.551|0.481; Qwen-1.5-7B 0.486|0.560|0.426; DeepSeek-Coder-V2-16B 0.547|0.611|0.521; Llama-3-70B 0.634|0.694|0.619; Qwen-2-72B 0.692|0.753|0.669; GPT-3.5-Turbo 0.576|0.654|0.530; GPT-4-Turbo 0.716|0.762|0.694; GPT-4o 0.733|0.782|0.711; Claude 3.5 Sonnet 0.742|0.769|0.727. SGP-MNIST: GPT-4o ≈13%.
- Pitfalls: MNIST result shows models do not "render in their head"; invariance test exposes sensitivity to numeric perturbation.
- License: code MIT; "data license follows the license of the original data source" (Onshape ToS for DeepCAD, CC for others).
- Note: CAD-Assistant (below) reports GPT-4o baseline 0.686 (2D) / 0.782 (3D) on SGP-Bench CAD QA.

### MM-CAD (Bharathi et al. — Computer Graphics Forum 2026)
- URL: https://onlinelibrary.wiley.com/doi/10.1111/cgf.70523 (paywalled; code/HF link not found)
- 33,816 unique CAD models unified from eleven benchmark datasets with isometric renders, point clouds, human-curated multi-level captions, and 4,376 real hand-drawn sketches; positioned for retrieval / RAG. Output-language, metrics and LLM numbers: not found (abstract only).

---

## 2. Text/image → CAD **command sequence** (DeepCAD JSON style) — the Text2CAD family

### Text2CAD (Khan et al. — NeurIPS 2024 spotlight)
- URLs: paper https://arxiv.org/abs/2409.17106 (HTML https://arxiv.org/html/2409.17106) ; code https://github.com/SadilKhan/Text2CAD ; data https://huggingface.co/datasets/SadilKhan/Text2CAD ; project https://sadilkhan.github.io/text2cad-project/
- Task: text (4 levels: L0 abstract, L1 beginner, L2 intermediate, L3 expert with relative values) → DeepCAD-style sequential sketch+extrude parameters (minimal JSON / vector). Built-in model is a 363M transformer, not an LLM.
- Data: DeepCAD ~170K models; ~150K train / ~8K val / ~8K test; 4 prompts per model → ~600K train / ~32K test+val prompts; total ~660K annotations. Prompts: LLaVA-NeXT captions from multi-view renders + Mixtral (paper) two-stage CoT prompting from "minimal JSON" metadata. v1.1 (HF card) re-annotated with Qwen2-VL-14B + Qwen2.5-72B-Instruct-8bit, adds `description`/`keywords` fields, and filters training set to ~5% basic shapes (v1.0 had ~25% cuboids, ~8% cylinders). Renders: 10 Blender images per model (8 circular side views + top + bottom).
- Metrics (expert prompts): F1 on lines/arcs/circles/extrusions via Hungarian matching of loops; CD ×10³ (point count not stated in the page I read); IR = share of sequences that are invalid (e.g. identical start/end points, zero extrusion depth). GPT-4V pairwise judge on multi-view renders (1,000 samples/level); user study with 5 CAD designers × 100 samples/level.
- Baselines (Table 1, expert level): DeepCAD (adapted) Line/Arc/Circle/Extr F1 76.78/20.04/65.14/88.72, median CD 32.82, mean CD 97.93, IR 10.00; Text2CAD 81.13/36.03/74.25/93.31, median CD 0.37, mean CD 26.41, IR 0.93. GPT-4V preference lead grows from −2.8% (beginner) to +27.18% (expert).
- Pitfalls the paper reports: LLaVA-NeXT sensitive to perspective distortion; DeepCAD imbalance; failures when prompt names an object instead of parameters.
- License: paper CC BY 4.0; dataset CC BY-NC-SA 4.0 (HF card). Test split public.

### Text2CAD-Bench ("A Benchmark for LLM-based Text-to-Parametric CAD Generation", May 2026)
- URLs: https://arxiv.org/abs/2605.18430 (HTML https://arxiv.org/html/2605.18430v1). Code/leaderboard "to be released after accept" — no URL yet.
- Task: text → **CadQuery** → STEP. 600 human-curated examples in 4 tiers: L1 200 (primitives), L2 200 (booleans/standard features), L3 100 (sweep/loft/shell/freeform), L4 100 real-world domains (industrial 40%, consumer 25%, medical 15%, architectural 10%, educational 10%). Each has two prompt styles: "geometric" (global→local→detail) and "sequence" (step-by-step). Authored by three mechanical-engineering contributors, human-in-the-loop.
- Execution: Python 3.10, CadQuery 2.4, isolated env, 60 s timeout, up to 3 retries with error feedback; official APIs, no hyper-parameter tuning (temperature not stated).
- Metrics: CD (bidirectional NN, 30,000 points/mesh, unit-bounding-box normalisation, ×10³); IR (fail / timeout / degenerate geometry); IoU at 256³ voxels; L4: GLM-4.6V judge on 8 multi-view renders, 0–10 across 5 feature questions.
- Results (Table 1, L1→L3 CD / IR / IoU): GPT-5.2 44.31/11.1%/0.59 → 93.46/68%/0.23; Claude-4.5-Sonnet 52.62/20.3%/0.54 → 70.13/70%/0.25; DeepSeek-V3.2 53.15/13.3%/0.54 → 101.23/69%/0.17; Qwen3-max 84.54/21.4%/0.40 → 148.58/92%/0.07; MiniMax-M2.1 65.92/24.8%/0.45 → 114.33/91%/0.26; GLM-4.7 67.50/13.7%/0.44 → 92.70/83%/0.25; Gemini-3-Flash 66.82/17.7%/0.44 → 91.61/12.8%/0.20. Domain models: Text2CAD CD 220+ with IR 2–11%; CADFusion CD 190–242, IR 51–70%; Text2CADQuery CD 227–277, IR 29–90%. L4 IR: GPT-5.2 61%, Claude-4.5 54%, DeepSeek-V3.2 55%, Gemini-3-Flash 17%, MiniMax 81%. No ~7–9B open model reported.
- Pitfalls: executability, geometric precision and feature understanding are "largely independent" dimensions; domain-specific models have low IR but poor CD (valid-but-wrong); geometric prompts win at L1–L2, sequence prompts at L3.
- License: CC BY 4.0 (paper).

### CAD-Llama (Fudan, 2025)
- URLs: https://arxiv.org/abs/2505.04481 (HTML https://arxiv.org/html/2505.04481)
- Task: text → SPCC ("Structured Parametric CAD Code", Python-like pseudocode with hierarchical descriptions) → DeepCAD sequence. Base LLaMA3-8B full FT + LoRA r=256 instruction tuning on 48K multitask examples.
- Data: DeepCAD deduplicated 178K→100K; two-stage GPT-4o annotation (component-level from renders+sketches, then global); 70M GPT-4o tokens.
- Metrics: text-to-CAD ACC_T (command acc + parameter acc + success ratio combined), median CD, MMD, JSD; unconditional COV/MMD/JSD/SR/Novelty; BLEU/ROUGE for captioning.
- Results (text-to-CAD): GPT-4 ACC_T 20.03% / MCD 25.62; LLaMA3-8B 17.26% / 17.33; Text2CAD 69.91% / 20.64; CAD-Llama-INS 84.72% / 10.53.
- Pitfalls: parameter errors in complex cases; text–CAD misalignment.
- License: CC BY 4.0 (paper). Code/data URL: not found.

### CADFusion (Microsoft — ICML 2025)
- URLs: https://arxiv.org/abs/2501.19054 (HTML https://arxiv.org/html/2501.19054) ; code https://github.com/microsoft/CADFusion
- Task: text → stringified sketch-and-extrude sequence. LLaMA-3-8B-Instruct + LoRA (r=32); alternates sequential learning (SL) and visual-feedback DPO (VF).
- Data: DeepCAD (SkexGen-processed) 20K text–CAD pairs (GPT-4o captions, human refined) 90/5/5; ~1,500 preference pairs per VF round from 1,000 prompts.
- Metrics: F1-Sketch (avg lines/arcs/circles), F1-Extrusion, CD, COV, MMD, JSD, IR (fail to render), LVM score = GPT-4o 0–10 judge, human rank (6 judges).
- Results (Table 1): GPT-4o F1-S 82.96 / F1-E 85.72 / CD 68.50 / IR 37.93 / LVM 6.60; Text2CAD 63.94 / 92.13 / 30.23 / — / 3.37; CADFusion 85.22 / 92.79 / 19.89 / 6.20 / 8.96.
- Pitfalls the paper states: GPT-4o used as judge may bias toward GPT-4o; LVMs degrade with multi-image input so single view used; 20K human-annotated beats 170K unannotated; human eval only ~50 unique samples.
- License: CC BY-NC-SA 4.0.

### FlexCAD (Microsoft — ICLR 2025)
- URLs: https://arxiv.org/abs/2411.05823 (HTML https://arxiv.org/html/2411.05823) ; code https://github.com/microsoft/FlexCAD
- Task: controllable generation at sketch/extrusion/face/loop/curve level via hierarchy-aware masking; Llama-3-8B LoRA (~3.4M params). DeepCAD 178,238 seqs 90/5/5, dedup per SkexGen.
- Metrics: Novel, Unique, PV (prediction validity), JSD, COV, MMD, human "Realism".
- Results (sketch-level): FlexCAD COV 65.6 / MMD 1.19 / JSD 0.82 / PV 93.4%; Hnc-CAD 62.4/1.21/1.07/72.6%; SkexGen 60.6/1.27/1.51/68.7%; GPT-4o 58.2/1.34/1.43/62.3%.
- License: arXiv non-exclusive.

### CAD-Editor (Microsoft — ICML 2025)
- URLs: https://arxiv.org/abs/2502.03997 (HTML https://arxiv.org/html/2502.03997) ; code https://github.com/microsoft/CAD-Editor ; weights https://huggingface.co/microsoft/CAD-Editor
- Task: (original CAD sequence, edit instruction) → edited sequence. 120K synthetic triplets from DeepCAD via design-variation models + LVLM difference captioning; DeepCAD 90/5/5; test 2,000 examples manually checked, 5 outputs each → 10,000 evaluated; 2,000 rated by 5 crowd workers each. Llama-3-8B-Instruct LoRA r=32.
- Metrics: VR (valid ratio), JSD, CD, D-CLIP (directional CLIP), H-Eval (binary success). JSD/CD/D-CLIP ×10².
- Results: GPT-4o-Basic VR 63.2 / JSD 1.10 / CD 2.30 / D-CLIP −1.08 / H-Eval 7.22; GPT-4o-IC 84.5/0.70/1.55/−0.11/15.6; CAD-Editor 95.6/0.65/1.18/0.11/43.2.
- License: CC BY-NC-SA 4.0.

### CAD-GPT (AAAI 2025)
- URLs: https://arxiv.org/abs/2412.19663 (HTML https://arxiv.org/html/2412.19663v1) ; code https://github.com/SiyuWang0906/CAD-GPT ; project https://OpenIWIN.github.io/CAD-GPT/
- Task: single image or text → DeepCAD sequence with spatial-reasoning tokens; LLaVA-1.5-7B.
- Data: DeepCAD dedup; image→CAD 162K (fixed-angle OpenCascade renders); text→CAD 18K (InstructGPT-generated, GPT-4o filtered, manually curated).
- Metrics: CD, IR, ACC_cmd, ACC_param.
- Results: GPT-4 image IR 64.37% / CD 62.64, text IR 76.97% / CD 187.52; DeepCAD image IR 23.16% / CD 23.78; CAD-GPT image IR 1.61% / CD 9.77, text IR 7.43% / CD 28.33.
- License: arXiv non-exclusive.

### CAD-MLLM / Omni-CAD (2024)
- URLs: https://arxiv.org/abs/2411.04954 (HTML https://arxiv.org/html/2411.04954) ; code https://github.com/CAD-MLLM/CAD-MLLM ; project https://cad-mllm.github.io/
- Task: text / multi-view image / point cloud (any combination) → command sequence; Vicuna-7B LoRA.
- Data: Omni-CAD 453,220 models after augmentation from ABC via Onshape API (DeepCAD methodology); captions by InternVL2-26B from 4 views; 9:1 split → 425,726 train / 27,494 test.
- Metrics: CD on [−0.5,0.5]³, F-score @0.05, normal consistency, plus topology metrics SegE, DangEL (dangling-edge length), SIR (self-intersection ratio), FluxEE (flux enclosure error).
- Results: point-cond CD 1.85 vs DeepCAD 4.51 (×100), F 90.88 vs 71.83; image-cond CD 3.77 vs InstantMesh 5.38; text: user-study 4.16/5 vs Michelangelo 1.16/5.
- License: CC BY 4.0 (paper).

### GenCAD (2024/2025)
- URLs: https://arxiv.org/abs/2409.16294 (HTML https://arxiv.org/html/2409.16294) ; project https://gencad.github.io/
- Task: image → DeepCAD command sequence via contrastive CSR + diffusion prior. DeepCAD 178,238 → 168,674 valid solids; 152,530 / 8,515 / 7,629; 5 scale variants per model → 845,105 grayscale 448² renders + sketch versions.
- Metrics: CD (2,000 points), IR, COV/MMD/JSD, Recall@B. Results: command acc 99.51%; image retrieval R@2048 60.77% vs ResNet 3.91%; conditional COV 81.37 (image). GPT-4-class baselines: not found.
- License: arXiv non-exclusive. Its command sequences are the source of **GenCAD-Code** (below).

### CADCrafter (CVPR 2025)
- URLs: https://arxiv.org/abs/2504.04753 (HTML https://arxiv.org/html/2504.04753v1)
- Task: unconstrained single/multi-view photos → command sequence (latent diffusion + DPO with code-checker feedback). Train on DeepCAD renders (8×4-view sets); RealCAD test = 150 DeepCAD-test models 3D-printed and photographed (4 views, iPhone).
- Metrics: ACC_cmd, ACC_para, median CD, IR (fail to compile). DeepCAD: 84.62% / 73.31% / 0.026 / 0.036 vs Img2CAD 80.57% / 68.77% / 0.160 / 0.288; RealCAD 83.18% / 66.89%.
- License: CC BY 4.0. Code: not found.

### Img2CAD (VLM-assisted conditional factorization — SIGGRAPH Asia 2025)
- URLs: https://arxiv.org/abs/2408.01437 (HTML https://arxiv.org/html/2408.01437) ; code https://github.com/qq456cvb/Img2CAD
- Task: single image → sketch-extrude program; discrete structure by fine-tuned Llama-3.2, continuous params by regressor; GPT-4V/4o for dataset creation. Data: CAD-ified ShapeNet — 1,026 chairs, 3,243 tables, 305 cabinets (PartNet + GPT-4V, ~30% kept after manual review).
- Metrics: CD on uniformly sampled surface points; part Seg-Acc, Seg-mIoU. Chair CD: GPT-4o 0.3806; DeepCAD-End2End 0.2346; Img2CAD 0.0984.
- License: arXiv non-exclusive.

---

## 3. Text/image/point-cloud → **executable CAD code** (CadQuery / PythonOCC / FreeCAD / FeatureScript / OpenSCAD)

### CADPrompt + CADCodeVerify (Alrashedy et al. — ICLR 2025)
- URLs: https://arxiv.org/abs/2410.05340 (HTML https://arxiv.org/html/2410.05340) ; code+data https://github.com/Kamel773/CAD_Code_Generation ; ICLR page https://iclr.cc/virtual/2025/poster/30576
- Task: NL prompt → **CadQuery** (chosen over OpenSCAD because Python-native and more concise). 200 objects "selected from a collection of modular CAD objects" of DeepCAD (Wu et al. 2021), stratified by mesh complexity / geometric complexity / compile difficulty, with simple vs complex splits. Prompts: 2 annotators write, a 3rd picks the better, a 4th verifies all 200; ground-truth CadQuery by one CAD expert validated against STL in Blender. Each entry: STL, OBJ, JSON commands, NL prompt, expert Python.
- Metrics: Point-cloud distance (bidirectional mean NN, 1,000 sampled points, unit-cube normalisation, ICP alignment); Hausdorff; IoGT (intersection over ground-truth bounding volume); compile rate. Refinement ≤2 iterations; compile-error repair loop.
- Results (few-shot, best refinement, IQR in parentheses): GPT-4 IoGT 0.944 (0.028) / PCD 0.127 (0.135) / HD 0.419 (0.356) / compile 96.5%; Gemini 1.5 0.939 / 0.147 / 0.492 / 85.0%; CodeLlama 0.935 (IQR 0.957) / 0.185 (IQR 1.620) / 0.582 / 73.5%. CADCodeVerify gives 7.30% PCD reduction and +5.5% success on GPT-4 vs 3D-Premise self-refinement; ~9% compile-rate gain on Hard split at first refinement where baselines drop 20%.
- Pitfalls: geometric-solver baseline "accessed ground truth" (upper bound); huge IQR for CodeLlama means medians hide a bimodal distribution; only 1,000 points for PCD.
- License: repo has no license file (README). Test set fully public (it IS the dataset) — heavily reused: CADmium, EvoCAD, CADTestBench, IterCAD all evaluate on it.

### CAD-Coder — "An Open-Source VLM for CAD Code Generation" (Doris et al., IDETC 2025) + GenCAD-Code
- URLs: https://arxiv.org/abs/2505.14646 (HTML https://arxiv.org/html/2505.14646) ; code https://github.com/anniedoris/CAD-Coder ; data https://github.com/anniedoris/GenCAD-Code , https://huggingface.co/datasets/CADCODER/GenCAD-Code , https://huggingface.co/datasets/CADCODER/real_photo_test ; weights https://huggingface.co/CADCODER/CAD-Coder
- Task: single isometric image → **CadQuery**. GenCAD-Code: 163,671 image–script pairs converted from GenCAD/DeepCAD .h5 vectors by `h5tocadquery.py`; 147,289 train / 7,355 test / 9,027 val; avg script 611 tokens, 99.9% < 3,000. Real-photo test: 400 photos of 3D-printed objects (repo) — paper evaluated 5 printed objects. Base LLaVA-1.5 (Vicuna-13B + CLIP-ViT-L-336).
- Metrics: VSR = % scripts that execute; IoU_best = IoU after centroid removal, RMS-radius-of-gyration scaling, PCA alignment over all valid rotation combos ("SolidAlign" in repo).
- Results (100 test samples): CAD-Coder VSR 100% / IoU_best 0.675; GPT-4.5 84% / 0.524; Qwen2.5-VL-72B 94% / 0.352; LLaVA-v1.5-13B 0% / 0.0.
- Pitfalls: only 100 test images; fillet unseen in training tested via prompting.
- License: code Apache-2.0; GenCAD-Code repo has no license stated; no dedup/filtering documented.

### Text-to-CadQuery (2025)
- URLs: https://arxiv.org/abs/2505.06507 (HTML https://arxiv.org/html/2505.06507) ; code https://github.com/Text-to-CadQuery/Text-to-CadQuery
- Task: Text2CAD prompts → **CadQuery**. ~170K pairs: Text2CAD minimal-JSON translated to CadQuery by Gemini 2.0 Flash with execution-error feedback loop + human validation; 90/5/5.
- Metrics: CD (10,000 points/mesh, normalised, ×10³, mean and median); IR (execution failure); "evaluation score" = Gemini 2.0 Flash shown Blender renders of generated vs GT STL and asked "same 3D object?" → % Yes. (The abstract's "top-1 exact match 69.3%" is this Gemini score for Qwen2.5-3B, not string match.)
- Results (Table 3; median CD / mean CD / IR / Gemini): CodeGPT-small-124M 0.234/13.520/9.4%/60.28%; GPT-2-medium 0.223/12.567/15.6%/60.31%; GPT-2-large 0.221/12.175/13.8%/63.61%; Gemma3-1B 0.204/11.609/8.4%/66.86%; Qwen2.5-3B 0.191/10.229/6.5%/69.30%; Mistral-7B (LoRA) 0.219/11.835/1.32%/65.38%. Text2CAD transformer baseline median CD 0.370, 58.80%.
- Pitfalls: 7B Mistral underperforms 3B (authors blame insufficient data); numerically unstable arcs with near-collinear points. Text2CAD-Bench later reports this model at CD 227–277 with IR 29–90% on its OOD tiers.
- License: CC BY 4.0.

### CAD-Coder — "Text-to-CAD Generation with Chain-of-Thought and Geometric Reward" (2025; distinct from Doris et al.)
- URLs: https://arxiv.org/abs/2505.19713 (HTML https://arxiv.org/html/2505.19713). Code: not found.
- Task: Text2CAD prompts → CadQuery. Data: 110K text–CadQuery–3D triplets built by having DeepSeek-V3 generate candidates, executing, keeping lowest-CD; tiers 8K (CD<1e-4), 70K (CD<1e-3), 32K lower; plus 1.5K CoT samples. Base Qwen2.5-7B-Instruct; GRPO with reward = λ_geo·R_geo + λ_fmt·R_fmt, R_geo piecewise (1.0 if CD<1e-5, 0 if CD>0.5, linear between).
- Metrics: mean CD, median CD, IR.
- Results: Text2CAD 29.29 / 0.37 / 3.75%; GPT-4o 133.52 / 45.91 / 93.00%; DeepSeek-V3 186.69 / 107.57 / 51.96%; CAD-Coder (full) 6.54 / 0.17 / 1.45%.
- Pitfalls stated: thin structures suffer "reward hacking due to sparse point sampling"; multi-component alignment and internal cavities fail; extrude-vs-cut confusion. (Note GPT-4o IR 93% here vs 37.93% in CADFusion vs 11% in Text2CAD-Bench — prompt/format dependence is large.)
- License: arXiv non-exclusive.

### CAD-Recode (Rukhovich et al. — ICCV 2025)
- URLs: https://arxiv.org/abs/2412.14042 (HTML https://arxiv.org/html/2412.14042) ; code https://github.com/filaPro/cad-recode ; data https://huggingface.co/datasets/filapro/cad-recode ; project https://cad-recode.github.io/
- Task: point cloud (256 FPS points) → **CadQuery**; Qwen2-1.5B + linear point projector.
- Data: 1M procedurally generated CadQuery scripts (random sketch primitives + booleans + extrudes) filtered by Python syntactic checks and BRepCheck_Analyzer; HF card: ~1M train / ~1K val, CC-BY-NC-4.0. Eval: DeepCAD test 8,046; Fusion360 1,725; CC3D 2,973 real scans.
- Metrics: CD ×10³ with 8,192 points (mean and median); IoU from meshes (%); IR. Test-time: 10 candidates, pick min-CD (so numbers are best-of-10 against GT — an oracle selection).
- Results: DeepCAD mean CD 0.30 / median 0.16 / IoU 92.0 / IR 0.4%; Fusion360 0.35 / 0.15 / 87.8 / 0.5%. Prior SOTA CAD-SIGNet: DeepCAD median 0.28, IoU 77.6; Fusion360 0.48, 65.6.
- Pitfalls: best-of-10 selection uses GT; procedural data → domain gap (cadrille reports ~60% CC3D IoU for SFT-only).
- License: paper arXiv non-exclusive; dataset CC BY-NC 4.0.

### cadrille (ICLR 2026)
- URLs: https://arxiv.org/abs/2505.22914 (HTML https://arxiv.org/html/2505.22914) ; code https://github.com/col14m/cadrille ; weights https://huggingface.co/maksimko123/cadrille , https://huggingface.co/maksimko123/cadrille-rl
- Task: point cloud / multi-view image / text → CadQuery in one Qwen2-VL-2B; SFT on CAD-Recode 1M + DeepCAD/Text2CAD ~160K, then online RL (GRPO / Dr.CPPO) with reward = 10·IoU − 10·[invalid]; hard-example mining (avg reward < 7.5).
- Eval sets: DeepCAD 8,046 (PC/Img/Text), Fusion360 1,725, CC3D 2,973, Omni-CAD 27K+.
- Metrics: median CD (8,192 pts, ×10³), IoU %, IR.
- Results (IoU, SFT→RL): PC DeepCAD 87.1→90.2, Fusion360 79.8→85.0, CC3D 61.8→67.9; Image DeepCAD 86.1→92.2, Fusion360 77.6→84.6, CC3D 56.1→65.0. IR ~10% (SFT) → <0.2% (RL).
- Pitfalls: different datasets have incompatible operation vocabularies so naive mixing fails; RL sidesteps the need for GT sequences.
- License: Apache-2.0 (repo).

### CADmium (Mila — 2025, v2 Jan 2026)
- URLs: https://arxiv.org/abs/2507.09792 (HTML https://arxiv.org/html/2507.09792) ; data/models https://huggingface.co/collections/chandar-lab/cadmium-6866b402be81f39321af98d4
- Task: text → JSON CAD sequence (NOT CadQuery), fine-tuning Qwen2.5-Coder 1.5B/3B/7B/14B with greedy decoding. 170K+ DeepCAD models re-annotated with GPT-4.1 "human-like" descriptions; filtered so cuboids 25%→5%, cylinders 8%→5%; 118,299 / 8,925 / 8,046. Annotation quality judged by Gemma-3-12B and Mistral-Small-3.2-24B on 10K triplets.
- Metrics: IR; median CD (points not stated); F1 (Hungarian on loops); new: Sphericity Discrepancy Ψ=(6V)^(1/3)/(π^(1/3)·s-ish, see paper), Discrete Mean Curvature Difference (dihedral angles), Exact Euler Characteristic Match (χ=V−E+F); also SegE/DangEL/SIR/FluxEE from CAD-MLLM.
- Results — CADPrompt OOD (Table 1, Text2CAD vs Qwen2.5-Coder-14B): IR 1.57% vs 6.28%; median CD 127.70 vs 116.75; Arc F1 0.00 vs 0.22; Circle F1 0.31 vs 0.61; DMCD 13.39 vs 8.42; EECM 0.66 vs 0.68. CADmium test by scale 1.5B/3B/7B/14B: IR 11.86/8.51/5.93/3.33%; median CD 0.31/0.24/0.27/0.23; EECM 0.87/0.89/0.88/0.89. Fusion360: IR 22.62/23.38/16.79/12.58%; median CD 110.18/76.34/92.07/76.05.
- Pitfalls: "simple metrics often fail to reflect quality"; point-cloud metrics ignore internal edges; median-CD chosen because means are outlier-dominated; 7B non-monotone vs 3B.
- License: dataset+code MIT, models Apache-2.0.

### ProCAD — "Clarify Before You Draw" (ICML 2026)
- URLs: https://arxiv.org/abs/2602.03045 (HTML https://arxiv.org/html/2602.03045v2) ; code+data https://github.com/BoYuanVisionary/Pro-CAD
- Task: possibly-ambiguous text → (clarifying questions) → CadQuery; two Qwen2.5-7B-Instruct agents. Test set 2,469 (≈1:1 unambiguous/ambiguous), from DeepCAD shapes with CAD-Recode CadQuery programs; ambiguity injected by GPT-5-mini (under-specified / inconsistent), kept only if original CD<2e-4, perturbed CD>2e-4 and ≥10× degradation.
- Metrics: mean/median CD ×10³, IR, LLM-judge Efficiency/Resolution.
- Results: Claude Sonnet 4.5 mean CD 3.10 / median 0.09 / IR 4.8%; GPT-4o-mini 9.98 / 0.14 / 7.5%; ProCAD 0.85 / 0.08 / 0.9%.
- License: CC BY 4.0.

### CAD-RL / ExeCAD — "From Intent to Execution" (2025)
- URLs: https://arxiv.org/abs/2508.10118 (HTML https://arxiv.org/html/2508.10118v1). Code/HF: not found.
- Task: NL + structured design language (+ render) → CadQuery; Qwen2.5-VL with CoT cold start + RL (executability, geometric, GPT-4o "external" rewards). ExeCAD = 16,540 samples refined from GenCAD-Code + Text2CAD with GPT-4o in 4 stages.
- Metrics: executability (binary), IoU %, mean/median CD.
- Results (structured input): GPT-4o IoU 47.7 / CD 65.52 / exec 72.72%; Qwen2.5-VL 31.6 / 114.64 / 62.30%; InternVL3 43.5 / 82.72 / 72.03%; CAD-Coder (re-impl.) 70.4 / 29.63 / 95.44%; CAD-RL 78.7 / 15.21 / 98.83%.
- License: not found.

### CME-CAD / CADExpert (Dec 2025)
- URL: https://arxiv.org/abs/2512.23333
- CADExpert: 17,299 instances of orthographic projections with dimension annotations + expert CoT + executable CadQuery + renders; multi-expert SFT + RL. Numbers/license/code: not found (abstract only).

### EvoCAD (Oct 2025)
- URLs: https://arxiv.org/abs/2510.11631 (HTML https://arxiv.org/html/2510.11631v1) ; code https://github.com/toprei/evo-cad
- Task: evolutionary search over CadQuery programs with GPT-4V/GPT-4o generation and o3-mini fitness ranking; evaluated on CADPrompt (~80% watertight subset). Population 6, 4 generations.
- Metrics: PCD, HDD, IoU, Dice, plus Topology Error |χ(O)−χ(Ô)| and Topology Correctness (binary Euler match).
- Pitfall (explicit): "objects with incorrect topologies can have better spatial metrics than those with correct topologies"; non-watertight outputs cannot be scored on volume/topology at all.
- License: arXiv non-exclusive.

### Seek-CAD (ICLR 2026)
- URLs: https://arxiv.org/abs/2505.17702 (HTML https://arxiv.org/html/2505.17702) ; data https://github.com/Sunny-Hack/Seek-CAD
- Task: text → Python (custom CADShape class over PythonOCC) with DeepSeek-R1 local inference + Gemini-2.0 visual/CoT self-refinement; training-free. Dataset 40K models from ABC via Onshape API/FeatureScript in SSR (Sketch, Sketch-feature, Refinements: extrude/revolve/fillet/chamfer/shell). Eval 500 models held out.
- Metrics: CD, HD, IoGT, G-Score (Gemini 1–5), Novelty (>80% similarity to corpus), pass@k compile.
- Results (best of compared 3D-PreMise / CADCodeVerify / CAD-Llama): CD 0.1979, HD 0.5566, IoGT 0.7226, G-Score 3.5185, Novelty 64.04%.
- License: arXiv non-exclusive; repo license not stated.

### Query2CAD (2024)
- URLs: https://arxiv.org/abs/2406.00144 (HTML https://arxiv.org/html/2406.00144) ; code+data https://github.com/akshay140601/Query2CAD
- Task: NL → **FreeCAD Python macros**, run via PyAutoGUI-driven FreeCAD, BLIP-2/VQAScore(≥0.9) feedback loop. 57 hand-written queries (21 easy / 20 medium / 16 hard).
- Metric: success = exact match to spec by manual inspection ("any deviation … is a failure").
- Results: GPT-4-Turbo 53.6% → 73.2% (1 refinement) → 76.7%; GPT-3.5-Turbo 32.7% → 53.4% (3 refinements); GPT-4-Turbo by tier easy 95.23% / medium 70% / hard 41.7%; CodeLlama-70B "extremely bad".
- Pitfalls: 69% of failures are non-executable code; BLIP2 captions weaker than human feedback; tiny set.
- License: arXiv non-exclusive.

### OpenECAD (Computers & Graphics 2024)
- URLs: https://arxiv.org/abs/2406.09913 (HTML https://arxiv.org/html/2406.09913)
- Task: 2D view(s) (default / transparent / orthographic, 640×480) → Python-style DSL over PythonOCC (`add_sketchplane`, `add_line`, `add_extrude` …). TinyLLaVA with OpenELM-450M / Gemma-2B / Phi-2 → 0.55B/0.89B/2.4B/3.1B. Data from DeepCAD: 100K/150K/200K pairs at 1K/2K/3K token limits.
- Metric: custom 100-point score (executability 10 + curve accuracy 45 + loop count 5 + loop quality 40), 625 test designs.
- Results (default view): 0.55B 83.89; 0.89B 86.05; 2.4B 88.80; 3.1B 85.84; GPT-4o-mini 39.72.
- Pitfalls: context-length truncation, concave/convex confusion. Data "available on request" only.

### LLM4CAD (Li, Sun, Sha — IDETC 2024 / JCISE 25(2) 2025)
- URLs: https://asmedigitalcollection.asme.org/computingengineering/article/25/2/021005/1208543/ ; IDETC https://asmedigitalcollection.asme.org/IDETC-CIE/proceedings-abstract/IDETC-CIE2024/88407/V006T06A015/1208976 ; preprint PDF https://sidilab.net/wp-content/uploads/2025/01/llm4cad_jcise_preprint.pdf (pages 403'd / binary for me — details below from secondary summaries of the paper)
- Task: text / text+sketch / text+image of mechanical parts → **CadQuery** → STL. Synthetic parts in 5 classes with MTurk NL descriptions incl. dimensions: 621 shafts, 671 nuts, 692 flanges, 680 springs, 661 gears.
- Metrics: Cap1 = syntactically correct program rate; Cap2 = IoU of parsed 3D shape. Exact GPT-4 / GPT-4V per-class numbers: not found in accessible text. Finding: GPT-4V text-only beats multimodal on average; multimodal helps on springs/gears. Follow-up "LLM4CAD Fine-Tuned: Dataset and Experiments" (JMD preprint https://sidilab.net/wp-content/uploads/2025/01/llm4cad_jmd_preprint.pdf).
- License / data link: not found.

### 3D-PreMise (Jan 2024)
- URL: https://arxiv.org/abs/2401.06437
- LLM program synthesis for industrial parametric shapes with visual self-correction; dataset of test programs with problem descriptions + GT code. Size/API/metrics/numbers: not found on abstract page. Used as the self-refinement baseline in CADPrompt and Seek-CAD.

### "Don't Mesh with Me" (Nov 2024)
- URLs: https://arxiv.org/abs/2411.15279 (HTML https://arxiv.org/html/2411.15279) ; project mxmws.github.io/dont_mesh_with_me
- Task: text → CSG as Python over the OpenMC library (half-spaces + cylinders), NOT OpenSCAD. 37,220 ABC parts converted BREP→CSG via GEOUNED; GPT-4o captions; DeepSeek-Coder-1B fine-tuned. Results: 96.5% syntax+logic correct, 82.2% surface utilisation. No external baselines.

### Human-in-the-Loop OpenSCAD evaluation (Sep 2025)
- URL: https://arxiv.org/abs/2509.07010 (HTML https://arxiv.org/html/2509.07010)
- Single L-bracket case study, **OpenSCAD**, GPT-4 only, four input modalities. Metrics: triangle count, area/volume ratio, Euler characteristic, volumetric accuracy, surface alignment, dimensional fidelity, Hausdorff, PCA/ICP alignment. Not a benchmark; CC BY 4.0. (An MHE/OpenSCAD paper in Procedia CS 2026 exists https://www.sciencedirect.com/science/article/pii/S1877050926000773 — 403 for me.) **No dedicated multi-model OpenSCAD LLM benchmark was found; the only OpenSCAD-format multi-model evaluation is P3D-Bench (below).**

### CAD2Program (AAAI 2025)
- URLs: https://arxiv.org/abs/2412.11892 (HTML https://arxiv.org/html/2412.11892) ; project https://manycore-research.github.io/CAD2Program
- 2D engineering drawing (raster) → Python dataclass shape program of cabinet primitives; Mini-InternVL-1.5-2B; 368K cabinets (364K/2K/2K). Metrics: model-retrieval acc 93.80%, reconstruction F1 (IoU>0.5 TP) 82.76%, parameter acc 97.21%. CC BY 4.0. Domain-specific (furniture), not general CAD.

### Ortho2CAD (Jul 2026)
- URL: https://arxiv.org/abs/2607.08891
- Orthographic drawings → CadQuery; >1M synthetic drawings (pythonOCC first-angle projections from STEP) + 100 manually dimensioned; metrics valid-code % and mean IoU; GPT-5.5 best with 100% valid code. Per-model table/license: not found.

### CAD-Assistant (ICCV 2025)
- URLs: https://arxiv.org/abs/2412.13810 (HTML https://arxiv.org/html/2412.13810) ; code https://github.com/dimitrismallis/CAD-Assistant
- Tool-augmented GPT-4o agent executing Python on **FreeCAD**. Evaluated on SGP-Bench CAD QA (700 2D + 3D), SketchGraphs autoconstraining (700), hand-drawn parameterization. Results: 2D QA 0.686→0.791; 3D QA 0.782→0.857; autoconstrain PF1 0.693→0.979, CF1 0.274→0.484. License CC BY-NC-ND 4.0.

---

## 4. Large-scale program datasets with new representations (2026)

### CADFS (CVPR 2026)
- URLs: https://arxiv.org/abs/2605.01925 (HTML https://arxiv.org/html/2605.01925v1) ; data https://huggingface.co/datasets/VladPyatov/CADFS ; project https://voyleg.github.io/cadfs/
- 451K Onshape public designs (subset of ABC, superset of DeepCAD/Text2CAD) as executable **FeatureScript** across 15 operations (sketch, extrude, revolve, sweep, loft, fillet, chamfer, shell, hole, booleans, patterns…). Text by gpt-oss-120B annotator+reviewer. Qwen2-VL-2B (also Qwen3-8B). Test: 7,278 DeepCAD-test + ~9K new all-operation designs.
- Metrics: CD, Edge-CD, normal consistency, COV/MMD/JSD, IR. Text-to-CAD on DeepCAD test: −40% CD, −64% ECD vs cadrille. ~15% of reconstructed programs fail validation.
- License: HF dataset CC BY 4.0 (search result); paper arXiv non-exclusive.

### CADEvolve (Feb 2026)
- URLs: https://arxiv.org/abs/2602.16317 (HTML https://arxiv.org/html/2602.16317) ; code https://github.com/zhemdi/CADEvolve
- Program-evolution pipeline (gpt-5-mini) → 7,945 generators → ~8×10⁵ programs → ~1.3M canonicalised CadQuery scripts (SFT set 2,720,481). Qwen2-VL-2B, SFT + Dr.GRPO/CPPO.
- Image2CAD results (median CD ×10³ / IoU % / IR %): cadrille-RL DeepCAD 0.17/92.2/0.1, Fusion360 0.17/84.6/0.1, MCB 0.87/47.6/2.5; CADEvolve-C big RL1 0.15/92.6/0.2, 0.16/87.2/0.5, 0.62/51.4/2.3; RL2 0.16/91.1/0.1, 0.16/84.0/0.2, 0.52/55.2/0.4. IR here = non-compiling OR non-watertight.
- License: CC BY 4.0.

### HistCAD (Feb 2026)
- URLs: https://arxiv.org/abs/2602.19171 (HTML https://arxiv.org/html/2602.19171) ; anon code https://anonymous.4open.science/r/HistCAD-68C2
- 170,236 executable sequences in a software-independent IL with 19 constraint types + fillet/chamfer/helix: DeepCAD 153,534 + Fusion360 8,609 + Industrial 8,093 (research-agreement only). Execution adapters: JiuShao POWER and Fusion 360 API. Benchmark = constraint-aware editability: ER, cPCSR, OES = ER×cPCSR. Qwen3-8B LoRA on full HistCAD: OES 73.33%, ER 83.67%, cPCSR 87.36%. GPT-5.5 qualitative only.

### UniCAD (Jun 2026)
- URLs: https://arxiv.org/abs/2606.05058 (HTML https://arxiv.org/html/2606.05058)
- 1,448,150 models (DeepCAD + Text2CAD + CAD-Recode) with CadQuery, 8-view renders, sketch-style images, point clouds+normals, QA pairs; test = DeepCAD 8,046. Qwen2-VL-2B.
- Metrics: CD ×10³ (avg squared NN), voxel IoU at 0.02 resolution in unit cube (≈50³), 4-way QA accuracy.
- Results: UniCAD-MLLM PC 0.17/89.7, text 0.20/86.3, multi-view 0.17/89.8, sketch 0.22/84.2, QA 90.0%; CAD-Recode PC 0.18/87.1; cadrille text 0.20/82.1; Text2CAD 0.37/71.5; GPT-4o QA 0.77. Fusion360 zero-shot PC IoU 84.3 vs CAD-Recode 79.1.
- Pitfall: sketch-extrude only; no fillet/chamfer/freeform. CC BY 4.0; release promised.

---

## 5. 2026 evaluation-first benchmarks

### CADBench (DeCoDELab, May/Jun 2026) — "A Multimodal Benchmark for AI-Assisted CAD Program Generation"
- URLs: https://arxiv.org/abs/2605.10873 (HTML https://arxiv.org/html/2605.10873v1) ; data https://huggingface.co/datasets/DeCoDELab/CADBench (78.4 GB parquet, DOI 10.57967/hf/8666)
- 18,000 samples, six families from DeepCAD, Fusion 360, ABC, MCB, Objaverse; five input modalities (single-view grayscale, multi-view, photorealistic PBR, clean mesh, Gaussian-noised mesh) → CadQuery. 1.4M+ generations over 11 systems.
- Metrics (all after continuous Procrustes alignment; GT scaled to max bbox edge 1.0): symmetric CD on surface samples; voxel IoU; Surface-IoU (bidirectional coverage, τ = 1% of bbox diagonal); VSR = executes AND valid shape (syntax errors, failed ops, non-manifold all invalid); token count; op count.
- Results (Table 2 aggregate: IoU / SIoU / CD / VSR): CADFit 0.895/0.685/0.038/1.000; CAD-Recode 0.506/0.462/0.064/0.919; CADEvolve 0.611/0.456/0.071/0.974; cadrille 0.555/0.482/0.062/0.945; Claude Opus 4.7 0.306/0.110/0.106/0.807; Gemini 3.1 Pro 0.295/0.108/0.092/0.737; GPT-5.4 0.124/0.028/0.114/0.517; Kimi K2.6 0.142/0.041/0.116/0.616; Qwen3.5-27B 0.011/0.002/0.179/0.336; Qwen3.5-9B 0.000/0.000/0.199/0.099; CAD-Coder 0.288/0.113/0.159/0.951.
- Pitfalls: mesh-conditioned ≫ image-conditioned; specialists collapse under modality shift; metric rankings disagree across models; Appendix D notes some source datasets' licences constrain redistribution.
- License: paper arXiv non-exclusive; HF card license not stated.

### BenchCAD (May 2026) — "A Comprehensive, Industry-Standard Benchmark for Programmatic CAD"
- URLs: https://arxiv.org/abs/2605.10865 (HTML https://arxiv.org/html/2605.10865v1) ; data HF `BenchCAD/BenchCAD` ; code https://github.com/BenchCAD/BenchCAD-main ; third-party leaderboards https://llm-stats.com/benchmarks/benchcad
- 17,900 execution-verified CadQuery programs, 106 industrial part families hand-crafted by domain experts, 49% anchored to ISO/DIN/EN/ASME/IEC tables. Tasks: Vision2Code, Code Edit, QA (code- or image-conditioned, 6–12 templates/family). 10 families held out OOD. Sandbox subprocess, 30 s wall clock; parse/runtime/timeout/degenerate volume → excluded.
- Metrics: voxel IoU at 256³ (+ rotation-invariant over 24 axes), CD, Hausdorff, Feature-F1, essential-op recall, execution rate; Edit accuracy = clip((IoU(gen,tgt) − IoU(orig,tgt)) / (1 − IoU(orig,tgt)), 0, 1); QA accuracy (±5% for ratios, exact for integers). Contamination control: blind baselines (black image / no code) as floors; standard-table parameter sampling.
- Results Vision2Code (IoU / CD / exec): CADEvolve v3 0.7497/0.0080/92.7%; cadrille-RL 0.0683/0.3507/91.2%; Claude Opus 4.7 (thinking) 0.2670/0.0240/90.4%; Gemini 3.1 Pro (thinking) 0.2790/0.0250/79.8%; GPT-4o 0.1884/0.0623/87.0%; Qwen3-VL-2B RL (IID) 0.7520/0.0041/98.9%, OOD 0.7140/0.0047/99.1%. Code Edit: GPT-5.3 0.865, Claude Opus 4.7 0.853, Gemini 3.1 Pro 0.837. Vision QA: Gemini 0.587, Claude 0.526, GPT-5.3 0.513; Code QA 0.80+.
- Pitfalls: ~15–20 pt code-vs-image QA gap ("holistic spatial and detailing deficit"); misses twist-extrude/loft/helix; edits break unrelated features.
- License: data CC BY 4.0, eval code MIT.

### CADTestBench — "Text-to-CAD Evaluation with CADTests" (May 2026)
- URLs: https://arxiv.org/abs/2605.07807 (HTML https://arxiv.org/html/2605.07807v1) ; code https://github.com/dimitrismallis/CADTestBench ; data https://huggingface.co/datasets/dimitrismallis/CADTestBench
- Replaces CD/IoU with executable boolean B-Rep predicates (CadQuery API) derived from the prompt, not from the reference: 200 CADPrompt programs → 5,937 tests (3,037 detailed / 2,900 abstract, ~15/sample) in 6 categories, plus 1,275 CAD mutants; tests authored by Claude-Sonnet-4.6 over 4 refinement rounds; mutation score >90%.
- Metrics: Pass-Rate (all tests pass), Requirement Score, per-category Acc, IR (runtime errors count as failures), Mutation Score. Human agreement: CADTests F1 0.938 vs VLM-judge 0.549 vs CD AUC 0.663.
- Results (detailed prompts, IR / RS / PR): Text2CAD 0.005/0.405/0.025; CadCodeVerify(GPT-4.1) 0.025/0.794/0.410; DeepSeek-V3 ReAct 0.035/0.752/0.370; Qwen3-Coder-480B ReAct 0.025/0.793/0.425; Claude-4.6-Sonnet ReAct 0/0.874/0.580; +CADTests-log 0/0.897/0.625. Abstract prompts: Claude ReAct 0/0.929/0.715, +log 0.962/0.810.
- Pitfall stated: CD/IoU "fail to account for the space of valid design variations" unconstrained by the prompt. License CC BY 4.0.

### MUSE (May/Jun 2026) — manufacturable / functional / assemblable text-to-CAD
- URLs: https://arxiv.org/abs/2605.28579 (HTML https://arxiv.org/html/2605.28579) ; project+leaderboard https://dong7313.github.io/muse-benchmark/ ; code https://github.com/dong7313/muse
- 106 multi-component design instances (CNC, 3D-print, laser-cut; timber/PLA) with Design Specifications: expert seed models → Claude Opus 4.7 expansion → GPT-5.5 spec synthesis → human review. Output CadQuery. Three-stage: code check (executes) → OCCT geometric checks (watertight, manifold, no self-intersection, no overlap) → rubric VLM judge (Gemini 3.1 Pro; also GPT-5.5/GPT-4o tested) on functionality / manufacturability / assemblability. Human agreement r=0.713 (sub-criteria), ≈0.83 (instance).
- Results (code / geometry / final %): GPT-5.5 77.36/70.75/52.36; Claude Opus 4.7 76.42/60.38/39.47; Gemini 3.1 Pro 65.09/58.49/43.40; Claude 3.7 Sonnet 46.23/23.58/12.74; GPT-4o 36.79/13.21/5.03; GLM-5.1 31.13/27.36/18.87; Qwen-2.5-72B 50.00/20.75/4.40; Qwen-3.5-122B 27.36/13.21/8.33; Llama-3.1-70B 31.13/23.58/2.20; MiniMax-M2.7 18.87/10.38/5.66.
- Pitfall: "failure cascade" executable → valid geometry → engineering-ready. License CC BY 4.0 (data), MIT (code).

### P3D-Bench (Jun 2026) — parametric 3D generation in JSON / OpenSCAD / CadQuery / Three.js
- URLs: https://arxiv.org/abs/2606.11152 (HTML https://arxiv.org/html/2606.11152v1) ; project https://lucasqaq.github.io/p3d/ (code/HF: "future work")
- 400 text cases + 400 image cases (Text2CAD v1.1 single parts) + 203 annotated Fusion 360 Gallery assemblies. Descriptive vs parametric specs written by an MLLM pipeline and auto-verified against source geometry. Every output executed and rendered; 4-step alignment (normalise, translate, rotate, bounded scale/position refinement minimising bidirectional CD).
- Metrics: Valid (binary); CD, F@0.05, F@0.01, normal consistency, IoU (CSG for parts, voxel for assemblies); topology NoOE/InvN/NM; MLLM bucket QA-S/QA-P accuracy + J-Sem/J-Geo/J-Aes 1–10 by Gemini 3.1 Pro; PartFS / PartMatchF1 for assemblies.
- Results: Text-3D parametric (JSON/OpenSCAD avg, Geo / IoU / Valid): GPT-5.5 0.706/0.812/0.999; Gemini 3.1 Pro 0.699/0.776/1.000; Claude Opus 4.6 0.692/0.788/0.995; DeepSeek V4 Pro 0.647/0.735/0.968; Kimi K2.6 0.679/0.716/0.988; Text2CAD Judge 0.055. Image-3D (CadQuery/OpenSCAD/Three.js avg, Geo / Judge / Valid): GPT-5.5 0.549/0.562/0.979; Gemini 0.552/0.540/0.970; Claude 0.525/0.431/0.975; cadrille 0.235/0.010/0.820. Best PartMatchF1 ≈0.5 on assemblies.
- Pitfalls (explicit): **OpenSCAD is the most consistent format; CadQuery shows high syntax-failure rates; Three.js yields non-manifold triangle soups**; JSON QA-P>QA-S because prompts were derived from JSON (leakage). License arXiv non-exclusive.

### IterCAD-Bench (Jun 2026)
- URLs: https://arxiv.org/abs/2606.13368 (HTML https://arxiv.org/html/2606.13368v2)
- Proprietary 11,000 drawing-to-code + 200 editing tasks, plus "Text2CAD Bench" (8,046 DeepCAD-test parts with text specs) and "CADPrompt Bench" (200). Output CadQuery (`import cadquery as cq`, result in `r`). New metric: CD-Tolerance-Recall curve and AUC-TR ∈[0,1] that counts non-executing samples (survivor-bias-free). Models: GPT-5, Gemini-2.5/3-flash-lite, GLM-4.6v, InternVL3.5-30B/8B, Qwen3.5-35B/4B, Claude-3.7, GPT-4o, DeepSeek-V3, Qwen2.5-72B/7B — numbers not extracted. License CC BY-NC-SA 4.0; code link not found.

### CADSmith (Mar 2026)
- URL: https://arxiv.org/abs/2603.26512 (HTML https://arxiv.org/html/2603.26512)
- 100 prompts with hand-written CadQuery references in tiers T1 50 / T2 25 / T3 25 (1–3, 3–8, 5–15 ops). CadQuery 2.6.1. Metrics: CD = avg bidirectional squared NN over 10,000 surface points; F1 at 1.0 mm absolute threshold; voxel IoU at 1.0 mm. Claude Sonnet generator, Claude Opus validator, GPT-4 baseline; numbers not extracted. CC BY 4.0; code link not found.

### CADGenBench (Hugging Face, 2026)
- URLs: code https://github.com/huggingface/cadgenbench ; data https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data ; leaderboard https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench ; submissions https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-submissions
- 81 mechanical-part fixtures: 49 Generation (engineering drawing/description → solid) + 32 Editing (change to an existing STEP). Tool-agnostic: submit one STEP per fixture. Ground truth private (`cadgenbench-data-gt`, only the Space reads it). CAD Score = hard validity gate (well-formed watertight B-Rep) then weighted mean of shape similarity (surface-distance F1 + volume IoU), interface match (authored keep-in/keep-out volumes), topology match (Betti numbers b0,b1,b2); weights not stated. Reference agent: LiteLLM (Claude / GPT-5.5 / Gemini), build123d and CadQuery, iterative render-review loop. Leaderboard numbers: dynamic, not captured. License Apache-2.0.

### Parametric CAD Bench (gNucleus, cadbench.ai, 2026)
- URLs: https://cadbench.ai/ ; https://www.gnucleus.ai/cad-bench/news/parametric-cad-bench ; HF dataset "cad-gen-freecad"
- Agents author **FreeCAD Part Design** models from NL; three tracks (part generation, assembly, complex workflow); six scoring axes (geometry accuracy, constraint/assembly correctness, parametric correctness incl. rebuild after dimension change, topology, agent workflow success, efficiency); weights not published. Leaderboard (combined score / cost): claude-opus-5 via claude-code 0.906 / $113.24; grok-4.6 mini-swe-agent 0.888 / $119.46; grok-4.6 grok-build 0.884 / $76.60; gpt-5.6-sol codex 0.865 / $97.58; grok-4.5 grok-build 0.837 / $36.59. License: not found.

### CADEngBench (Aug 2026) — "It looks like CAD, but does it work?"
- URL: https://arxiv.org/abs/2608.09296 (HTML https://arxiv.org/html/2608.09296)
- CADEngBench-P: 300 parts (159 BenchCAD + 141 Fusion 360 Gallery) → 600 tasks (generate + edit) in layers L0 validity (execute, B-Rep solid, STEP export, STEP re-import — all required), L1 engineering/DFM requirements, L2-Z parametric perturbation, L2-E functional edits with preservation, L3 CalculiX FEA agreement. CADEngBench-A: 150 Fusion360 assembly-joint pairs (Entity@k, Typed@k, MRR, A2 E2E). Output CadQuery with `DEFAULT_PARAMS` / `PARAMETER_MANIFEST` / `build()`.
- Models: GPT-5.2, Claude Sonnet 4.5, Gemini 3 Flash, GLM-4.6V, Kimi K2.5, Mistral Medium 3.5, Llama 4 Maverick, Qwen3.5-35B (per-model table not extracted).
- Headline pitfalls: 58.1% of executable CAD violates engineering requirements; editing success 99.6% for isolated features vs 40.2–45.8% under join/cut coupling; FEA agreement uncorrelated with reach (ρ=0.024); generation vs assembly ranks ρ=0.048. License / repo: not captured.

### RealCADBench (Sep 2026)
- URL: https://arxiv.org/html/2609.03773 (code "to be publicly released")
- 12,632 tasks in 19 factory-automation categories from an industrial marketplace + purchased CAD repository; reported slice 1,770 (1,745 parts across text 568 / 2D drawing 236 / real picture 568 / rendered image 373 + 25 assemblies). Output **FreeCAD 1.1.1 Python** exporting result.stl (mm). Multi-stage verification by five CAD practitioners; text tasks generated by Gemini 3.0 Pro from photos.
- Metrics: executability (non-empty export); Solid IoU and Surface IoU after signed-PCA alignment + uniform scale, 96³ voxels with 2% padding; Judge = Kimi K2.6 rubric vs the input evidence (not the reference).
- Results (Table 4, exec / solid IoU / surface IoU / judge): GPT-5.5 0.931/0.383/0.139/73.79; Claude Opus 4.8 0.892/0.408/0.143/68.31; Gemini 3.1 Pro Preview 0.883/0.429/0.158/73.33; GPT-5.4 0.801/0.380/0.134/61.19; Kimi K3 0.342/0.448/0.166/72.23; Qwen3.8-27B 0.796/0.359/0.113/56.30. Also Qwen3-VL-8B/32B, Doubao Seed 2.0 Pro evaluated (numbers not extracted). Per-regime exec: text 0.565, drawing 0.678, photo 0.799, render 0.812.
- Pitfalls: "no single model leads all four metrics"; executability and IoU do not co-move (Kimi K3: 34% exec but highest IoU among survivors — survivor bias). License arXiv non-exclusive.

---

## 6. Not found / could not verify
- "Text2CAD-Edit", "TextCAD-Verify", "MMCAD" (as an LLM benchmark — only MM-CAD CGF dataset found), "CAD-Bench 2026" (as a distinct name — the 2026 items are CADBench, BenchCAD, Text2CAD-Bench, CADEngBench, RealCADBench, P3D-Bench, MUSE, CADGenBench, Parametric CAD Bench): **not found**.
- A dedicated multi-model **OpenSCAD** LLM benchmark: not found; P3D-Bench is the only one that scores OpenSCAD output across many models. A **build123d** benchmark: only CADGenBench's reference agent uses build123d; zalo's Fusion360→build123d conversion is a dataset, not a benchmark.
- LLM4CAD per-class GPT-4/GPT-4V numbers, 3D-PreMise dataset size, CME-CAD numbers, Ortho2CAD/IterCAD/CADSmith per-model tables: not extracted (paywall/403 or not in fetched text).

## 7. Cross-cutting observations (all traceable to entries above)
1. **Chamfer conventions are incompatible across papers**: 1,000 pts + ICP (CADPrompt), 2,000 pts (DeepCAD/GenCAD), 8,192 pts (CAD-Recode/cadrille), 10,000 pts squared (Text-to-CadQuery, CADSmith), 30,000 pts unit-bbox (Text2CAD-Bench); alignment ranges from none to Procrustes (CADBench) to bounded-scale refinement (P3D-Bench) to PCA (RealCADBench, CAD-Coder). Median vs mean matters enormously (Text2CAD: median 0.37 vs mean 26.41).
2. **IR/VSR definitions differ**: execution only (Text-to-CadQuery), execution+valid solid (CADBench), execution+watertight (CADEvolve), execution+STEP round-trip (CADEngBench), non-empty export (RealCADBench). Several benchmarks explicitly count failures as worst-case rather than excluding them (CADTestBench, IterCAD AUC-TR, MUSE cascade) because excluding them produces survivor bias (RealCADBench Kimi K3 example).
3. **Best-of-N vs greedy**: CAD-Recode/cadrille pick min-CD of 10 candidates vs GT; CADmium uses greedy; Text2CAD-Bench uses API defaults with 3 error-feedback retries; most papers do not state decoding — the finetune/CLAUDE.md note that greedy collapses on boilerplate-heavy dialects is consistent with P3D-Bench's finding that CadQuery has the highest syntax-failure rate while OpenSCAD is most stable.
4. **Frontier-model IR on text→CadQuery varies 11%–93% for "GPT-4o-class" depending on prompt format** (Text2CAD-Bench L1 11.1% for GPT-5.2; CADFusion 37.93%; CAD-Coder-CoT 93.00%; CAD-GPT 76.97%) — numbers are not comparable across papers.
5. **Topology/validity-aware metrics** are the 2025–26 trend: Euler characteristic (CADmium, EvoCAD), SegE/DangEL/SIR/FluxEE (CAD-MLLM), Betti numbers (CADGenBench), OCCT watertight/manifold (MUSE), property-based CADTests, FEA (CADEngBench), rebuild-after-edit (Parametric CAD Bench, HistCAD, BenchCAD edit).
6. **Contamination**: DeepCAD-derived test sets (8,046) are shared by Text2CAD, CADmium, UniCAD, IterCAD "Text2CAD Bench" and are in the training data of most fine-tuned models; only BenchCAD (blind baselines, OOD families), CADGenBench (private GT), RealCADBench (purchased industrial data), MUSE (expert-authored) and Text2CAD-Bench (hand-authored) attempt contamination control.
7. **Licensing**: Fusion 360 Gallery is non-commercial with no redistribution; CAD-Recode data CC BY-NC; Text2CAD data CC BY-NC-SA; CAD-Editor/CADFusion CC BY-NC-SA; CADmium MIT; BenchCAD/MUSE/CADTestBench CC BY 4.0; CADGenBench Apache-2.0.

<!-- ===== blender_scene.md ===== -->

# Survey: benchmarks / datasets / metrics for LLM+VLM generation of Blender Python, procedural-3D code, scene-layout programs and animation code

Compiled 2026-09-08 from primary sources (arXiv HTML/abs, GitHub, HF). Every number below was read from the cited page; "not found" means the source I fetched did not state it. Entries are grouped: (A) Blender-code object/editing benchmarks, (B) scene-layout programs, (C) animation / 4D code, (D) procedural materials, (E) CAD-code adjacent, (F) text-to-3D evaluators / judges (non-code but reused as metrics), (G) spatial-reasoning prerequisites, (H) items searched for but not found / out of scope.

---

## A. Blender-Python object generation and graphics-editing benchmarks

### A1. BlenderGym — 2025, CVPR 2025 (Highlight)
- **URLs**: paper https://arxiv.org/abs/2504.01786 (HTML https://arxiv.org/html/2504.01786); code https://github.com/richard-guyunqi/BlenderGym-Open; data https://huggingface.co/datasets/richard-guyunqi/BG_bench_data; project/leaderboard https://blendergym.github.io/ ; CVPR page https://openaccess.thecvf.com/content/CVPR2025/html/Gu_BlenderGym_Benchmarking_Foundational_Model_Systems_for_Graphics_Editing_CVPR_2025_paper.html
- **Task**: start-scene `.blend` + start Python script + rendered start/goal image pair → edited Python script that reproduces the goal scene when executed (code-based 3D reconstruction/editing). Five task types: procedural geometry (50), lighting (40), procedural material (40), blend-shape (75), object placement (40) = 245 instances (https://arxiv.org/html/2504.01786).
- **Dataset construction**: hand-crafted start/goal pairs derived from BlenderAlchemy prototypes, Infinigen and BlenderKit assets; each instance ships base `.blend`, start/goal scripts, renders and language description. No train/test split; human baseline run on ~20% of instances. HF card: 1,391 rows, 1.96 GB, single "train" split, license field empty (https://huggingface.co/datasets/richard-guyunqi/BG_bench_data).
- **Execution env**: Blender Python API with Infinigen wrapper; ≥3 camera views per scene (≥1 "comprehensive" view); VLM sees 2 views, evaluation uses all views; 8-minute wall-clock limit per instance (matched to the human baseline). Blender version: not found in paper, README or HF card.
- **Metrics**: Photometric Loss (PL) = pixel-space difference of renders; N-CLIP = (1 − CLIP score), "lower is better"; Chamfer Distance (CD) on 3D geometry (blend-shape task). PL and N-CLIP reported ×10⁻³ for blend-shape/placement and ×10⁻² for geometry/material/lighting; averaged per task (https://arxiv.org/html/2504.01786).
- **Baselines (Table 1, best-of-1 pipeline)**: GPT-4o blend-shape PL 9.14 / N-CLIP 20.47 / CD 0.904; lighting PL 2.41 / N-CLIP 2.40; material PL 3.65 / N-CLIP 8.94. Claude 3.5 Sonnet blend-shape PL 12.79 / N-CLIP 27.96 / CD 1.962; lighting PL 2.90 / N-CLIP 4.05. Open 7–9B models (Qwen2-VL-7B, InternVL2-8B) "15–20% worse" and marked "–" on geometry/material when >75% of outputs are non-executable. Human baseline (10 Blender users): blend-shape PL 0.934 / N-CLIP 9.12 / CD 0.399; lighting PL 1.24 / N-CLIP 1.63; material PL 0.629 / N-CLIP 3.04. Leaderboard lists 14 systems incl. GPT-4-Turbo best material PL 8.812 (https://blendergym.github.io/).
- **Inference scaling**: verifier re-selection k 1→64 improves selection; "scaled InternVL2-8B outperform unscaled or slightly scaled GPT-4o and Claude 3.5 Sonnet"; optimal generation/verification compute split shifts toward verification at higher budgets.
- **Pitfalls reported**: generator hallucinates non-existent visual differences; non-executable scripts dominate for open models; irrelevant edits in 80+ line procedural scripts; verifier position bias ("Qwen consistently favor the second edit candidate in the pair, regardless of permutation"); human–VLM verifier agreement Claude 3.5 Sonnet 0.66 vs inter-human 0.79; verifiers miss physical implausibility.
- **License/availability**: paper under arXiv non-exclusive license; data public on HF (no license field); site CC BY-SA 4.0 (Nerfies template); leaderboard at project page.

### A2. BlenderLLM / BlendNet / CADBench — Dec 2024, arXiv
- **URLs**: paper https://arxiv.org/abs/2412.14203 (HTML https://arxiv.org/html/2412.14203); code https://github.com/FreedomIntelligence/BlenderLLM; data HF `FreedomIntelligence/BlendNet`, `FreedomIntelligence/CADBench`, model `FreedomIntelligence/BlenderLLM` (https://huggingface.co/FreedomIntelligence/BlenderLLM).
- **Task**: text instruction → bpy script → Blender render of a single CAD-style object.
- **Dataset**: BlendNet — 135 seed instructions × 16 categories, Self-Instruct-expanded to ~50k instructions; 8 instruction types × 5 length levels; scripts by GPT-4o (gpt-4o-2024-08-06); 4 renders/object verified by GPT-4o (89.7% agreement with humans on 10k samples); 2k human-verified (12 annotators, 3 arbitrators, 30% QC) + 6k GPT-verified in paper; GitHub README says 12k pairs (2k manual + 10k GPT-4o). CADBench = 500 simulated (uniform over categories/types) + 200 "Wild" scraped from BlenderArtists/Reddit/Discord (https://arxiv.org/html/2412.14203; https://github.com/FreedomIntelligence/BlenderLLM).
- **Execution env**: Blender renders 4 views per script; script is "executable" if Blender renders without error; Blender version not found.
- **Metrics**: three dimensions/eight sub-dimensions — Attributes (shape, color, size, proportion, texture), Spatial (space, contact/distance), Instruction execution; per-sample 12–15 binary checkpoints judged by GPT-4o E(l,I,s,cᵢ)∈{0,1}, image-based for visual props and script-based for numeric props; sub-dimension → dimension → overall averages; E_syntax = N_error/N_total. Judge reliability: human–human κ 0.883, GPT-4o–human κ 0.791 on 200 outputs.
- **Baselines (CADBench-Sim Avg / E_syntax)**: BlenderLLM (Qwen2.5-Coder-7B-Instruct + 3 self-improvement rounds) 0.748±0.085 / 3.4%; o1-Preview 0.687 / 15.6%; Claude-3.5-Sonnet 0.593 / 15.6%; GPT-4o 0.565 / 21.4%; DeepSeek-V2.5 0.479 / 25.2%; Qwen2.5-Coder-7B 0.353 / 31.4%. CADBench-Wild: BlenderLLM 0.664 / 3.5%; o1-Preview 0.583 / 17.5%; GPT-4o 0.444 / 28.5%; Gemini-1.5-Pro 0.380 / 38.0%.
- **Pitfalls**: basic CAD only (no advanced materials/internals), single-turn only, Blender only; syntax errors 15–38% for frontier zero-shot.
- **License**: repo Apache-2.0; paper CC BY 4.0. Test set public; no live leaderboard.

### A3. BlenderAlchemy — 2024, ECCV 2024
- **URLs**: paper https://arxiv.org/abs/2404.17672 (HTML https://arxiv.org/html/2404.17672); code https://github.com/ianhuang0630/BlenderAlchemyOfficial; project https://ianhuang0630.github.io/BlenderAlchemyWeb/
- **Task**: text and/or reference image → edits of an existing Blender Python program (procedural material, blend-shape/geometry-node/placement, lighting). Edit-only; cannot build scenes from scratch.
- **Eval set**: text-material: multiple descriptions on one Infinigen wooden starter material; image-material: 5 Infinigen materials toward shared targets (some DALL-E 3 targets); 32 text prompts in user study. Starter materials from Infinigen (some BlenderKit).
- **Execution env**: 512×512 renders of a sphere, fixed camera/lighting; d=4 iterations × b=8 hypotheses; VLM GPT-4V. Blender version not found.
- **Metrics**: CLIP similarity (avg ViT-B/32 and ViT-L/14 vs text); photometric loss; LPIPS; pairwise VLM state evaluator V(w₁,w₂,I); MTurk 592 pairwise comparisons.
- **Numbers**: text-material CLIP ViT-B/32 28.2 vs BlenderGPT 25.2; ViT-L/14 24.0 vs 21.1; preferred over Paint3D 73%, over TEXTure 56%; human agreement with VLM evaluator 71%; removing vision drops ViT-B/32 to 25.7.
- **Pitfalls**: "out-of-the-box VLMs have a poor understanding of the visual consequences of Blender program edits"; correct edits are sparse in program space.
- **License**: paper CC BY 4.0; code on GitHub.

### A4. 3D-PreMise — Jan 2024, arXiv
- **URLs**: https://arxiv.org/abs/2401.06437 (HTML https://arxiv.org/html/2401.06437v1); code/data: not found (paper gives only an issue-report link).
- **Task**: fully-specified text description of an industrial-style object → bpy program → mesh; evaluates self-correction with a visual interface.
- **Dataset**: 57 samples (furniture, toys, decorative items) built for the paper; prompts "almost fully defined, leaving little space of ambiguity".
- **Metrics**: Pass/fail via normalized point-cloud bidirectional matching within threshold δ; Chamfer distance d_CD = (1/|P_S'|)Σ min‖x−y‖² + (1/|P_T'|)Σ min‖x−y‖².
- **Numbers**: GPT-4 only: best Pass@1 17.5% (one-shot CoT, greedy); Pass@3 19.3%; Pass@5 19.3%.
- **Pitfalls**: spatial-precision errors most frequent; GPT-4 fixes programming mistakes from visual feedback but misses common-sense visual errors, hallucinates on visual feedback, struggles with geometric math; "redundant scaling" bug pattern.
- **License**: not found.

### A5. SceneCraft — 2024, ICML 2024 (oral)
- **URLs**: https://arxiv.org/abs/2403.01248 (HTML https://arxiv.org/html/2403.01248); PMLR https://proceedings.mlr.press/v235/hu24g.html; code: not found (no repo linked on arXiv/HF paper page).
- **Task**: text → relational scene graph → Blender Python with numerical layout constraints → render → GPT-4V critique loop; up to ~100 assets retrieved from TurboSquid via CLIP top-10.
- **Eval**: 40 synthetic queries with ground-truth constraints (20 library-learning / 20 test) + Sintel movie scenes (first half train, second half test).
- **Metrics**: CLIP similarity; Constraint Score (annotator-written scoring functions per constraint); Layout-matrix cosine similarity (Sintel); FVD; human win-rate on text fidelity / composition / aesthetics.
- **Numbers**: SceneCraft vs BlenderGPT — CLIP 69.8 vs 24.7; constraint 88.9 vs 5.6; human win text-fidelity 76.8% vs 12.7%, composition 83.6% vs 11.4%, aesthetics 74.5% vs 14.5%; Sintel layout similarity 69.3 vs 27.5, scene CLIP 82.7 vs 41.8, FVD 317 vs 574.
- **Pitfalls**: initial scripts "do not produce the correct layout outright" (wrong constraints or faulty constraint functions); library learning needs ≥20 GT-constraint examples; asset pool = TurboSquid.
- **License**: paper CC BY 4.0; Blender version not found; code not released per pages fetched.

### A6. 3D-GPT — Oct 2023, arXiv (also IEEE Xplore 2025)
- **URLs**: https://arxiv.org/abs/2310.12945 (HTML https://arxiv.org/html/2310.12945); code https://github.com/Chuny1/3DGPT; project https://chuny1.github.io/3DGPT/3dgpt.html
- **Task**: text → Infinigen function parameters / Python calls (task-dispatch, conceptualization, modeling agents) → Blender scene.
- **Eval**: 100 ChatGPT-generated nature-scene descriptions; flower-type prompts for fine control; sequential-edit prompts.
- **Metrics**: CLIP score (cosine in CLIP space); failure rate (datatype/parsing/missing-parameter); Shannon diversity of parameters.
- **Numbers**: full system CLIP 29.16; w/o task-dispatch agent 22.79; w/o conceptualization agent 21.51 with failure rate 3.6%.
- **Pitfalls**: limited curve/shading control; quality bounded by procedural functions; text-only.
- **License**: arXiv non-exclusive; Blender version not found.

### A7. Infinigen / Infinigen Indoors — CVPR 2023 / CVPR 2024 (data source, not an LLM benchmark)
- **URLs**: https://arxiv.org/abs/2306.09310; https://github.com/princeton-vl/infinigen; https://infinigen.org/
- **Role**: fully procedural Blender generator (plants, animals, terrain, indoor objects) whose factory scripts are the ground truth for 3DCodeBench, MeshCoder (Infinigen Indoors), Code2Worlds and 3D-GPT. License BSD-3-Clause (https://arxiv.org/abs/2306.09310 search summary; repo). No LLM eval numbers itself.

### A8. 3DCodeBench / 3DCodeData / 3DCodeArena — May/Jun 2026, arXiv
- **URLs**: https://arxiv.org/abs/2606.01057 (HTML https://arxiv.org/html/2606.01057v1); code https://github.com/gaoypeng/3dcodebench; data https://huggingface.co/datasets/YipengGao/3DCode; site 3dcodebench.com (per paper).
- **Task**: text (three caption styles: object description / procedural instruction / factory-level spec) or 1–4 reference renders → standalone Blender 5.0 Python that procedurally builds one object; settings: single-shot, multi-turn error feedback, coding-agent harness (Claude Code / Codex CLI / Gemini CLI, 600–900 s budget).
- **Dataset**: 212 Infinigen categories (one eval instance each, seed-addressable); ground-truth factory scripts median 387 / mean 531 lines; 3DCodeData corpus 212×60 seeds = 12,720 instances (paper says 12,963 for fine-tuning; README says 12,720) with 2 captions, 4 renders, textured + white GLB; captions validated by Gemini 3.1 Pro and humans. Released logs: 81,605 generated scripts / 82,042 trials + 2,767 agent transcripts.
- **Execution env**: Blender 5.0 Cycles, sandboxed headless subprocess, 240 s timeout, 4 canonical views (azimuth 45/135/225/315°). Multi-turn: up to two stateless retries, each a fresh call with truncated stderr (head-70%/tail-30%, 33K-char cap per HTML; the paper's summary elsewhere says 3K).
- **Metrics**: Executability 𝕀[ℰ(f)≠∅ ∧ |Mesh(ℰ(f))|≥1] (empty scene = failure); view-paired SigLIP-2 and DINOv3 cosine; Chamfer distance and Uni3D 3D–3D / cross-modal cosine on GLBs; "conditional" (over successes) vs "penalized" (failures = 0) aggregation; 3DCodeArena pairwise human votes (a / b / tie / both-bad; ties count 0.5/0.5) → Bradley–Terry MLE → Elo (400/ln10 per logit), 1000-resample bootstrap CI; ~3,100 votes over 12 models. SigLIP-2 Pearson r=0.964 with Elo, DINOv3 Spearman ρ=0.972. VLM-judge pairwise prompt in Appendix F.2; rubric dimensions not enumerated in the fetched excerpt.
- **Baselines (exec / SigLIP-2 / Uni3D 3D–3D / Elo)**: GPT-5.5 0.906 / 0.834 / 0.562 / 1163; Claude Opus 4.7 0.910 / 0.814 / 0.490 / 1006; Gemini 3.1 Pro 0.725 / 0.824 / 0.567 / 1147; Claude Sonnet 4.6 0.804 / 0.813 / 0.525 / 1015; GPT-5.4 0.866 / 0.817 / 0.552 / 1074; Gemini 3.5 Flash 0.464 / 0.824 / 0.519 / 1119; Gemma 4 31B 0.582 / 0.801 / 0.494 / 952; Claude Haiku 4.5 0.502 / 0.761 / 0.363 / 799. Excluded (<10% exec): Gemini 2.5 Pro 7.1% text-to-3D, GPT-5.4 Nano 6.1%. Multi-turn: aggregate exec 0.702→0.974; agent harness exec 0.747→0.973 with ΔSigLIP-2 ≈ −0.010 (shape quality unchanged).
- **Pitfalls**: physical plausibility (disconnected parts, floating primitives) is the bottleneck beyond executability; Blender 4.x→5.0 API deprecation is the dominant failure; thinking budget helps small models 15–19 exec points, frontier ≤5; multi-view input barely helps except the largest models.
- **License**: paper CC BY 4.0; factory scripts BSD-3-Clause; eval code MIT; test set public; live arena leaderboard.

### A9. VIGA + BlenderBench — Jan 2026, arXiv
- **URLs**: https://arxiv.org/abs/2601.11109 (HTML https://arxiv.org/html/2601.11109); code https://github.com/Fugtemypt123/VIGA (MIT); data https://huggingface.co/datasets/DietCoke4671/blenderbench; project fugtemypt123.github.io/VIGA-website; overview https://www.emergentmind.com/topics/blenderbench
- **Task**: target image(s) → iterative code-render-inspect edits of a Blender scene. Three tracks: camera adjustment, fixed-view multi-step editing, exploratory/compositional editing; 10 instances each (HTML says 27 tasks total; emergentmind says 30 episodes). Scenes 3–12 objects (primitives + imported assets), deliberate style/arrow-prompt domain gaps.
- **Metrics**: PL (pixel-wise L2 to target); N-CLIP (negative CLIP cosine); VLM Score 0–5 by GPT-4o over task completion, spatial accuracy, detail accuracy, visual quality; success rate (executable trajectories); relative improvement. max_iterate_round=10; best-of-N selected by CLIP.
- **Numbers**: GPT-4o best-of-4 PL — one-shot 48.16 / 7.36 / 30.14 (T1/T2/T3), BlenderAlchemy 14.50 / 1.95 / 20.62, VIGA 5.47 / 2.94 / 12.62; avg improvement +124.7% on BlenderBench, +35.32% BlenderGym (abstract; +26.88% GPT-4o best-of-1 in text), +117.17% SlideBench. VLM score best-of-1: GPT-4o one-shot 0.58/2.75/0.25 → VIGA 1.44/3.58/1.53; Qwen3-VL-8B one-shot 0.28/1.61/1.25 → VIGA 1.31/3.33/2.25.
- **Pitfalls**: bounded by VLM spatial perception; GPT-4o "struggles with active spatial exploration" vs GPT-5 / Claude Sonnet 4; Blender version not found.
- **License**: code MIT; paper arXiv non-exclusive.

### A10. SceneActBench — Jul 2026, arXiv
- **URLs**: https://arxiv.org/abs/2607.22393 (HTML https://arxiv.org/html/2607.22393); code https://github.com/Feinaldo2/SceneActBench; data https://huggingface.co/datasets/FEInaldo/SceneActBench; project https://feinaldo2.github.io/sceneactbench-project-page/
- **Task**: images (1 or ~11 calibrated views, or 32/144 video frames) + anonymized GLBs → agent drives headless Blender via MCP (`get_scene_info`, `get_object_info`, `execute_blender_code`, `render_scene_view`, + `read_reference_frames`) to output object poses / camera pose JSON / articulated states / furnished-scene GLB / animated GLB. Five tasks: Layout, Camera, Articulated, Reconstruction, Dynamic; step budgets 30/30/60/35/80.
- **Dataset**: 210 sources → 520 cases: 100 3D-FRONT rooms (3–7 objects, 27 categories), 100 S2O articulated containers (32-frame sequences), 10 Kenney CC0 dynamic scenes (144 frames @24fps); assets re-centered, re-scaled, names anonymized to remove answer leakage.
- **Metrics** (deterministic, no VLM judge): ADD-S (Hungarian-matched nearest-surface distance, m); camera PE (m) and AE (deg, ignores roll); MPE = max over movable parts of geometry error or unreproduced range; F@5% after ICP + Hungarian; MME (max normalized mover trajectory error, unmatched=1) and LE (static centroid NN distance / 2S); overall = mean of five normalized task scores q↓=100·max(0,1−m/u), u=4 m (layout, camera), 90° (AE), 1 otherwise; seed 0 point sampling.
- **Numbers (Overall)**: Doubao Seed 2.0 Pro High 50.2; Claude Opus 4.6 High 48.9; GPT-5.4 Medium 48.7; GPT-5.4 High 48.7 (Layout 84.1, Articulated 73.8, Dynamic 46.7); Qwen 3.7 Plus 46.2; Gemini 3.1 Pro 45.4; Claude Sonnet 5 39.5; MiniMax M3 38.6. Reconstruction F@5% 0.088–0.104 for top-3.
- **Pitfalls**: rankings driven by balance not wins; multi-view helped 9/11 configs, hurt 2; similar scores hide different failure stages (Doubao moves 13/391 parts vs Claude 255/391 at similar MPE); tool-call volume anti-correlated with score (ρ=−0.68); single run per case; only 10 dynamic scenes.
- **License**: arXiv non-exclusive; data on HF; Blender version not found.

### A11. DreamHouse (physical generative reasoning) — Mar 2026, arXiv (Salesforce)
- **URLs**: https://arxiv.org/abs/2603.24866v1 (HTML https://arxiv.org/html/2603.24866v1); project https://luluyuyuyang.github.io/dreamhouse
- **Task**: 5 orthographic 512×512 renders (front/back/left/right/iso; "Frame" or occluded "Facade" condition) → Blender Python that builds a timber-frame house scene graph (foundation, floor, walls, roof members).
- **Dataset**: 26,543 procedurally generated, human-validated structures, 13 styles, 133–1,548 members each (mean 673); JSON configs → procedural bpy generator; all pass the 10-test suite before release. Eval: 1,200 structures per protocol.
- **Execution env**: headless Blender + Cycles (version not found); validation runs on the scene graph, no physics sim.
- **Metrics**: Structural pass = all 10 deterministic tests (load path, IRC span limits, 16"/24" spacing, standard lumber dims, L/360 deflection, roof coverage ≥0.70, gap ≤0.20, cantilever limits, stability index =1, dual-end connection), no partial credit; Visual fidelity S = mean over views of max(0, 1−10·MSE) on alpha-masked renders, threshold 0.6; Topological fidelity T = 0.3·census + 0.4·Hungarian match (δ=0.3 m) + 0.3·voxel IoU; Joint pass = structural ∧ S≥0.6 (primary).
- **Numbers (Planner-Atomic, Frame/Facade)**: GPT-5 struct 0.792/0.637, visual 0.312/0.287, joint 0.035/0.019; Claude Opus 4.5 struct 0.716/0.779, visual 0.406/0.377, joint 0.071/0.064 (best joint 7.1%); Gemini 3 Flash struct 0.454/0.539, joint 0.031/0.036; Planner-Managed lifts Gemini struct to 0.785. Open models (Qwen3.5-397B-A17B, Qwen3-VL-8B/30B, Kimi K2.5) show "fundamental" failures (wrong rafter orientation, scale). Visual-feedback refinement: Claude +0.033 S, 70.3% improved, 68.5% structural retention.
- **Pitfalls**: structural validity and visual fidelity orthogonal; protocol choice swings results more than model choice (33 pp); topological score does not separate pass/fail; "a generated house mesh may score well on Chamfer distance while failing every structural test".
- **License**: CC BY 4.0.

### A12. 3D-CoS (code-synthesis 3D reconstruction) — Jun 2026, arXiv / OpenReview
- **URLs**: https://arxiv.org/abs/2606.10478 (HTML https://arxiv.org/html/2606.10478); https://openreview.net/forum?id=0TV81QK0l8; code: not found.
- **Task**: single RGB image → Blender 4.4 Python reconstructing the object; five workflows: single-call (~12k tokens), planning/blueprint (~26k), RAG over 21,102 Blender API functions (~34k), few-shot (~21k), part-wise agent with execution verification (~170k). Editing track on BlendNet-E (55 objects with hand-written edit instructions).
- **Dataset**: ModelNet10, 100 objects / 10 categories, easy/hard split.
- **Metrics**: CD (bidirectional NN surface distance), F@5%, Spatial-Balanced Recall@2%, Normal Consistency@5%; input-view silhouette IoU, depth NRMSE, normal MAE; upright-preserving similarity alignment; editing: CLIPsim, CLIPdir, 1–5 user study.
- **Numbers**: Gemini 3 Pro CD 0.0249 / F@5% 0.8532; o3 0.0318 / 0.7949; Claude Sonnet 4 0.0368 / 0.7394; Qwen2.5-VL-72B 0.0492 / 0.6702; InternVL3.5-38B 0.0642 / 0.5584; LLaVA-OV-72B 0.0674 / 0.5186; InstantMesh 0.0191 / 0.9060; MeshCoder 0.0442 / 0.7053; editing CLIPsim 0.0578 vs point-cloud baseline 0.0142.
- **Pitfalls**: agent workflow raises part coverage but introduces assembly inconsistencies; still short of feed-forward reconstructors.
- **License**: CC BY 4.0.

### A13. Thinking in Blender (SEIG, staged executable inverse graphics) — Jun 2026, arXiv (Cornell)
- **URLs**: https://arxiv.org/abs/2606.02580 (HTML https://arxiv.org/html/2606.02580); code: not found.
- **Task**: single image → four-stage generator-verifier loop (scene init, geometry, material, composition/lighting) emitting Blender Python; model Claude Opus 4.7.
- **Data**: NeRF-synthetic (7/8 scenes), VoxHammer (13 object scenes), in-the-wild set.
- **Metrics**: PSNR, SSIM, LPIPS, DreamSim, DINO, CLIP. NeRF: PSNR 13.58, SSIM 0.6881, LPIPS 0.3493; best on 5/6 metrics.
- **Pitfalls**: early-stage errors propagate; high compute. License arXiv non-exclusive.

### A14. EZBlender (Plan-and-ReAct agent) — Jan 2026, arXiv
- **URLs**: https://arxiv.org/abs/2601.07143 (HTML https://arxiv.org/html/2601.07143); code https://github.com/Aztech-Lab/EZ_Blender
- **Task**: text/visual prompt → Blender edits; evaluated on BlenderGym's 245 scenes plus a 5-scenario / 50-episode multi-task benchmark (shape keys, materials, lighting, background, camera) and 85 text-prompt scenes.
- **Metrics/numbers**: CLIP 30.21 (shape keys, text); visual-prompt similarity 0.9816 (blend shape); multi-task completion 78.67% (Scene 1); latency 37.35 s; $0.0241/edit (GPT-4o); claims ~7× faster than BlenderAlchemy, 67% fewer tokens.
- **Pitfalls**: camera posing and lighting remain hard due to VLM 3D perception. License arXiv non-exclusive.

### A15. LL3M (Large Language 3D Modelers) — Aug 2025, arXiv (UChicago)
- **URLs**: https://arxiv.org/abs/2508.08228 (HTML https://arxiv.org/html/2508.08228v1); project https://threedle.github.io/ll3m
- **Task**: text → Blender 4.4 Python via planner (GPT-4o), BlenderRAG retrieval over 1,729 doc pages, coder (Claude 3.5 Sonnet), critic/verifier (Gemini 2.0 Flash), user-proxy edits.
- **Eval**: 17 target objects, qualitative gallery vs BlenderMCP; complex-op count 5.86× with RAG vs 1.20; execution errors 2.43 vs 3.29; ~4 min create + ~6 min refine; 59% of user edits done in one instruction. No formal benchmark/user study.
- **License**: CC BY 4.0; code availability not stated.

### A16. ShapeCraft — Oct 2025, NeurIPS 2025
- **URLs**: https://arxiv.org/abs/2510.17603 (HTML https://arxiv.org/html/2510.17603); OpenReview https://openreview.net/pdf?id=skS03tzYNw; project https://sanbingyouyong.github.io/shapecraft/
- **Task**: text → Graph-based Procedural Shape (GPS) → wrapped bpy scripts; parser/coder Qwen3-235B-A22B, evaluator Qwen-VL-Max.
- **Eval**: 26 long-form functional prompts from MARVEL-40M+ (Objaverse-derived).
- **Metrics**: IoGT (point-cloud intersection over GT), Hausdorff, CLIP ViT-B/32 over 10 views, VQA pass rate (5 yes/no questions per prompt judged by Qwen-VL-Max on multi-view renders).
- **Numbers (IoGT / Hausdorff / CLIP / VQA)**: ShapeCraft 0.471 / 0.415 / 27.27 / 0.44; BlenderLLM 0.455 / 0.511 / 26.99 / 0.43; 3D-PreMise 0.385 / 0.527 / 26.76 / 0.33; CADCodeVerify 0.334 / 0.511 / 25.94 / 0.34; LLaMA-Mesh 0.346 / 0.464 / 25.72 / 0.28; MVDream 0.427 / 0.411 / 26.84 / 0.42.
- **Pitfalls**: ambiguous/brief/creative prompts break decomposition; complex topology/organic detail weak. License CC BY 4.0; code release not stated.

### A17. MeshCoder — Aug 2025, arXiv (InternRobotics)
- **URLs**: https://arxiv.org/abs/2508.14879 (HTML https://arxiv.org/html/2508.14879v2); code https://github.com/InternRobotics/MeshCoder; data https://huggingface.co/datasets/InternRobotics/MeshCoderDataset; project https://daibingquan.github.io/MeshCoder/
- **Task**: point cloud → part-segmented Blender Python using a custom API (primitives, translation sweep, bridge-loop, boolean, array, fill-grid…); Llama-3.2-1B + LoRA with triplane shape tokenizer.
- **Dataset**: 1M object–code pairs, 41 categories from Infinigen Indoors; ~10M part-level pairs; 70/15/15 split; 100K pairs released per HF search summary.
- **Metrics**: L2 Chamfer on 100k points; voxel IoU at 32³. MeshCoder CD 0.063×10⁻² / IoU 86.75% vs PLAD 1.87 / 67.62%, Shape2Prog 6.00 / 45.03%.
- **Pitfalls**: human-made objects only; no executability rate reported. License arXiv non-exclusive.

### A18. Proc3D — Jan 2026, arXiv (USF / Adobe)
- **URLs**: https://arxiv.org/abs/2601.12234 (HTML https://arxiv.org/html/2601.12234)
- **Task**: text → Procedural Compact Graph (engine-agnostic; compiles to Blender/Substance/Unity) with editable parameters.
- **Dataset**: 63k instruction–graph pairs from 21k PartNet models (chairs, tables, trash cans, storage, beds); LLaVA captions + LLaMA-3-70B instructions at 3 lengths; 1,000 hand-annotated.
- **Metrics/numbers**: compile rate PCG 89% (GPT-4o ICL) vs 45% LLaMA-Mesh vs 30% raw Blender code; fine-tuned LLaMA-3 8B/70B compile 98%; ULIP 0.15 (70B-FT) vs 0.11 SDFusion / 0.08 LLaMA-Mesh; OOD ULIP drops to 0.08; tokens 702 vs 6,048 for geometry-nodes code.
- **Pitfalls**: cube-primitive only; five categories. License CC BY 4.0; repo not confirmed.

### A19. LLaMA-Mesh — Nov 2024, arXiv (NVIDIA) — mesh-as-text, not code
- **URLs**: https://arxiv.org/abs/2411.09595 (HTML https://arxiv.org/html/2411.09595v1); https://research.nvidia.com/labs/toronto-ai/LLaMA-Mesh
- **Task**: text ↔ OBJ text (v/f lines), 64-bin quantization, ≤500 faces; 31k Objaverse meshes ×4 rotations = 125k; Cap3D captions.
- **Metrics**: qualitative vs MeshXL/Unique3D; language retention MMLU 61.74 vs 66.07, PIQA 79.16 vs 81.01, HellaSwag 77.35 vs 79.19, GSM8K 62.09 vs 77.18. No execution/geometry benchmark. License CC BY 4.0. Used as a baseline in ShapeCraft (A16) and Proc3D (A18).

---

## B. Scene-layout programs / LLM scene synthesis

### B1. Holodeck — CVPR 2024 (AI2)
- **URLs**: https://arxiv.org/abs/2312.09067 (HTML https://arxiv.org/html/2312.09067); code https://github.com/allenai/Holodeck (Apache-2.0); CVPR https://openaccess.thecvf.com/content/CVPR2024/html/Yang_Holodeck_Language_Guided_Generation_of_3D_Embodied_AI_Environments_CVPR_2024_paper.html
- **Task**: text → GPT-4 floor plan, asset selection from 51,464 GPT-4V-annotated Objaverse assets, relational constraints (global/distance/position/alignment/rotation) → solver → AI2-THOR scene (Unity render; Blender optional).
- **Eval**: 120 paired residential scenes vs ProcTHOR (preferred 59.8% asset selection, 56.9% layout, 64.4% overall); 260 diverse scenes/52 types rated 1–5 (Holodeck beats ProcTHOR on 28/52); layout ablation MRR 0.706 for constraint solver; CLIP score = 100×cosine (OpenCLIP ViT-L/14 LAION-2B) of top-down view vs "a top-down view of [scene type]"; ObjectNav on NoveltyTHOR 20.4% SR / 0.127 SPL vs ProcTHOR 4.11% / 0.015.
- **Pitfalls**: "Absolute method performs no better than Random" when LLM outputs coordinates directly (collisions, OOB); struggles with restaurants, domain-specific assets; GPT-4 cultural bias; ~$0.2/room, ~3 min.
- **License**: code Apache-2.0; requires GPT-4o-2024-05-13 and Unity 2020.3.25f1.

### B2. LayoutGPT — NeurIPS 2023
- **URLs**: https://arxiv.org/abs/2305.15393 (HTML https://arxiv.org/html/2305.15393); code https://github.com/weixi-feng/LayoutGPT (also UCSB-AI/LayoutGPT); OpenReview https://openreview.net/forum?id=Xu8aG5Q8M3
- **Task**: text → CSS-style layout (2D boxes or 3D furniture with size/position/orientation) via in-context exemplars.
- **Data**: 3D-FRONT bedrooms 3,397/453/423 and living rooms 690/98/53 (rectangular floors only); NSR-1K 2D numerical/spatial benchmark.
- **Metrics**: OOB rate = % scenes with furniture outside floor plan; KL divergence of category distributions; FID over 4 camera renders. NSR-1K: precision/recall of predicted objects, layout accuracy, GLIP image accuracy.
- **Numbers (GPT-3.5)**: bedrooms OOB 43.26% (ATISS 49.88%), KL 0.0995 (ATISS 0.0113), FID 28.37 (30.02); living rooms OOB 64.16% (83.02%), FID 76.34 (85.40). NSR-1K numerical P/R 94.81/96.49, layout acc 86.33%; spatial layout acc 82.54%.
- **Pitfalls**: rare furniture underrepresented; overlaps/OOB persist. License CC BY 4.0.

### B3. I-Design — ECCV 2024 workshops
- **URLs**: https://arxiv.org/abs/2404.02838 (HTML https://arxiv.org/html/2404.02838); project https://atcelen.github.io/I-Design/
- **Task**: text → multi-agent LLM dialogue → scene graph → backtracking placement (collision-free, in-bounds) → Objaverse retrieval.
- **Eval**: 40 prompts (10 each functionality/layout/materials/atmosphere); 20 scenes vs LayoutGPT. GPT-4V grading 0–10 on four criteria; OOB rate; Bounding-Box Loss (overlap volume); object count.
- **Numbers**: I-Design vs LayoutGPT: objects 18.2 vs 6.2; OOB 0.0% vs 57.6%; BBL 0.33 vs 7.58; GPT-4V avg 5.7 vs 4.8.
- **Pitfalls**: placement termination failures; retrieved-asset dimension mismatch. License CC BY-SA 4.0.

### B4. LayoutVLM — CVPR 2025 (Stanford/Google)
- **URLs**: https://arxiv.org/abs/2412.02193 (HTML https://arxiv.org/html/2412.02193); project https://ai.stanford.edu/~sunfanyun/layoutvlm/; CVPR PDF https://openaccess.thecvf.com/content/CVPR2025/papers/Sun_LayoutVLM_Differentiable_Optimization_of_3D_Layout_via_Vision-Language_Models_CVPR_2025_paper.pdf
- **Task**: instruction + unlabeled assets + floor plan (visually marked images) → initial poses + differentiable spatial relations (distance, on-top-of, align-with, point-towards, against-wall) → optimization.
- **Eval**: 11 room types × 3 rooms, up to 80 assets; Objaverse assets; GPT-4-authored instructions.
- **Metrics**: Collision-Free %, In-Boundary %, GPT-4o positional and rotational coherency, PSA = GPT-4o rating weighted by physical plausibility (0–100); user–GPT Kendall τ 0.46–0.61.
- **Numbers (PSA avg)**: LayoutVLM 58.8; I-Design 40.0; LayoutGPT 16.6; Holodeck 5.6. VLMs: GPT-4o primary; LLaVA-NeXT-Interleave fine-tuned on ~9k 3D-FRONT rooms. No Claude/Gemini/Qwen numbers.
- **Pitfalls**: "occasional failures … due to suboptimal VLM initializations". License CC BY-NC-ND 4.0.

### B5. SceneEval / SceneEval-500 — Mar 2025 (v3 Mar 2026), arXiv (SFU)
- **URLs**: https://arxiv.org/abs/2503.14756 (HTML https://arxiv.org/html/2503.14756v3); code https://github.com/3dlg-hcvc/SceneEval; site https://3dlg-hcvc.github.io/SceneEval/
- **Task**: evaluation framework for text-conditioned indoor scene generators (works on any layout output with meshes).
- **Data**: 500 descriptions (100 hand-written, 400 o4-mini-generated + manual validation), 10 room types, easy (≤4 furniture) / medium (5–8, ≤3 small) / hard (≥9), annotated with counts (eq/gt/lt/ge/le), attributes, 13 object–object and 10 object–architecture relations.
- **Metrics**: fidelity — CNT, ATR (GPT-4o-2024-08-06 on renders), OOR/OAR (geometric ray-casting/point tests after VLM relation mapping); plausibility — COL (mesh intersection), SUP (VLM support-type + ray cast), NAV (largest free component / total free), ACC (functional sides unblocked), OOB (<99% of floor points inside → OOB). Qwen2.5-VL-7B tested as open judge (lower, same trend).
- **Numbers (CNT / ATR / OOR / OAR / COL↓ / SUP)**: Holodeck 32.64 / 28.49 / 11.52 / 37.27 / 15.91 / 63.21; LayoutVLM 35.59 / 20.20 / 6.03 / 19.39 / 32.13 / 76.90; LayoutGPT 11.84 / 8.05 / 1.18 / 4.87 / 11.46 / 30.13 (OOB 72.25%); InstructScene 14.14 / 11.53 / 3.59 / 10.20 / 55.00 / 80.79; DiffuScene 11.99 / 9.28 / 3.20 / 8.21 / 31.81 / 75.40; ATISS 11.18 / 7.40 / 1.07 / 8.03 / 50.36 / 90.90.
- **Pitfalls**: "even the best method satisfies fewer than 30% of attribute requirements"; retrieval fails on color/material/style; LLM-generated descriptions lose diversity with long history. License arXiv non-exclusive; code on GitHub.

### B6. Open-Universe Indoor Scene Generation (Aguina-Kang et al.) — Mar 2024, arXiv
- **URLs**: https://arxiv.org/abs/2403.09675 (HTML https://arxiv.org/html/2403.09675)
- **Task**: text → Python-embedded declarative DSL (`adjacent()`, `facing()`, `aligned()`, `mounted_on_wall()`) → solver; retrieval from un-annotated Objaverse + ULIP renders.
- **Eval**: 3 closed-universe room types; 59 open-universe prompts / 6 categories; 2AFC human studies (35 and 24 participants); retrieval P/R on Cap3D; orientation accuracy on 450 objects (~94%).
- **Numbers**: preferred over ATISS 79%, DiffuScene 81%, LayoutGPT 65% (51–76% by prompt type); LLM errors per 1000 objects ~50 → ~31 with staged synthesis (hallucination, misuse, contradiction, unsatisfiability).
- **License**: paper CC BY 4.0; code "upon publication".

### B7. The Scene Language — Oct 2024, CVPR 2025 Highlight (Stanford)
- **URLs**: https://arxiv.org/abs/2410.16770 (HTML https://arxiv.org/html/2410.16770v2); code https://github.com/zzyunzhi/scene-language
- **Task**: text/image → Lisp-style Python DSL (program + words + embeddings) with Claude 3.5 Sonnet; renderers: Mitsuba, Minecraft, 3DGS-SDS, MIGC.
- **Eval**: 99 numeric + 88 generic prompts; Prolific study 103 participants; CLIP (OpenCLIP ViT-H-14, 120 views); counting accuracy; 4D: dynamic degree via RAFT.
- **Numbers**: alignment 85.65% vs GraphDreamer 3.56% / MVDream 10.79%; CLIP 0.351 vs 0.297 / 0.312; counting 1.0 vs 0.11; 4D CLIP 0.341 vs 4D-fy 0.352 but motion 5.9% vs 0.2%; image LPIPS 0.681 vs 0.811.
- **License**: arXiv non-exclusive; code on GitHub.

### B8. HDSL — Jun 2026, arXiv
- **URLs**: https://arxiv.org/abs/2606.09738 (HTML https://arxiv.org/html/2606.09738)
- **Task**: text → XML/CSS-style hierarchical DSL for indoor scenes, LLM agents generate + localized edits (HRAG).
- **Eval**: 6 MIT-Scene categories × 5 runs; metrics NObj, OOB (fraction of objects intersecting room boundary), PIoU (mean pairwise 3D bbox IoU), CLIP, time.
- **Numbers**: HDSL NObj 58.19 / OOB 0.05 / PIoU 0.07 / CLIP 20.99 / 383 s; Holodeck 43.15 / 0.06 / 2.45 / 19.81 / 552 s; I-Design 13.20 / 0.54 / 0.01 / 19.58 / 849 s; editing 5.22× fewer tokens; 20-user study 82.2% edits match.
- **Pitfalls**: Objaverse coverage; static only. License arXiv non-exclusive.

### B9. SceneGenAgent / SceneInstruct — Oct 2024, arXiv (THU + Siemens) — industrial scenes, C# not bpy
- **URLs**: https://arxiv.org/abs/2410.21909 (HTML https://arxiv.org/html/2410.21909); code https://github.com/THUDM/SceneGenAgent
- **Task**: text → C# for Siemens Process Simulate (Tecnomatix API). Benchmark 40 hand-written descriptions / 5 categories; SceneInstruct 3,002 synthesized descriptions, 21,756 instances (placement 5,704, verification 12,397, reassignment 2,587).
- **Metric**: Pass@1 judged by 3 experts comparing render to description. GPT-4o 81.0%; GLM-4-Plus 73.5%; Llama3.1-70B 73.0% → 78.5% fine-tuned; CodeLlama-34B tuned 59.5%; "all generated code fails to compile without API guidance".
- **License**: code Apache-2.0; dataset CC BY 4.0.

---

## C. Animation / 4D / cinematic code

### C1. Code2Worlds / Code4D — Feb 2026, arXiv (PKU)
- **URLs**: https://arxiv.org/abs/2602.11757 (HTML https://arxiv.org/html/2602.11757); code https://github.com/AIGeeksGroup/Code2Worlds; site https://aigeeksgroup.github.io/Code2Worlds
- **Task**: text → Blender 4.3 bpy for 4D animated scenes (soft body, fluid, particles, rigid body, atmosphere) on Infinigen factories; Gemini 3 backbone; VLM-Motion Critic loop.
- **Benchmark**: Code4D — 10 prompts (Appendix D); release "upon acceptance".
- **Metrics**: O-CLIP / S-CLIP / Style-CLIP; VBench motion smoothness, subject/background consistency, temporal flicker; GPT-4o SGS (scene geometry), HRS (hybrid realism), Richness; manual failure rate (interpenetration, gravity, collision errors).
- **Numbers**: Code2Worlds O-CLIP 0.2655 / SGS 61.4 / Style-CLIP 0.6734 vs ImmerseGen 0.2417 / 43.5 / 0.5991 vs Infinigen 0.2431 / 35.5 / 0.6671; scene S-CLIP 0.2432, Richness 62.3, HRS 55.4, failure 10%; video motion smoothness 0.9952 vs AnimateDiff failure 70%, Hunyuan 30%.
- **License**: CC BY-NC-SA 4.0.

### C2. SimWorlds / 4DBuildBench — Jul 2026, arXiv (CMU/Harvard/UC Merced)
- **URLs**: https://arxiv.org/abs/2607.01766 (HTML https://arxiv.org/html/2607.01766); site https://dynsimworlds.github.io
- **Task**: text → Blender 5.1 procedural dynamic scenes where physics is solved (cloth, fluid, rigid, particles, soft body) by planner-coder-reviewer agents (Claude Opus 4.7) with bl_rna-derived knowledge base.
- **Benchmark**: 50 hand-authored prompts: 9 per dynamic category × 5 + 5 static; difficulty D1 single solver / D2 within-category / D3 cross-category.
- **Metrics**: engine-state audit — MPR (mean fraction of per-actor predicates satisfied; 42 typed predicates incl. anti-cheat for faked keyframes), SPR (fraction of declared spatial relations holding under BVH distance test); itemized VLM judge (GPT-5.5, 5 frames, binary verdicts on objects/relations/actions/quality/aesthetics → mean fraction).
- **Numbers**: SimWorlds MPR 0.87 / SPR 0.89 / VLM 0.82 vs VIGA 0.67 / 0.70 / 0.78. 70–120 min per scene (bake-dominated).
- **Pitfalls**: judge is still an LLM/VLM; sampled frames can mask wrong intermediate dynamics; text-only. License CC BY 4.0.

### C3. GPT4Motion — Nov 2023 (v2 Apr 2024), arXiv
- **URLs**: https://arxiv.org/abs/2311.12631
- **Task**: text → GPT-4-written Blender physics script (rigid drop/collision, cloth, liquid) → renders → Stable Diffusion video. Quantitative metrics/numbers: not found in abstract; code URL not found; license arXiv non-exclusive.

### C4. Keyframer — Feb 2024, arXiv (Apple) — 2D CSS animation, not 3D
- **URLs**: https://arxiv.org/abs/2402.06071 (HTML https://arxiv.org/html/2402.06071v1); https://github.com/apple/ml-keyframer; https://machinelearning.apple.com/research/keyframer
- **Task**: SVG + text → CSS animation code (GPT-4). 13-participant study, two 15-min tasks; 223 designs; 90.4% of generated CSS syntactically clean, 6.7% errors; satisfaction 3.9/5. No benchmark; license CC BY 4.0.

### C5. Animation2Code — Jun 2026, arXiv (UC Berkeley) — web animation, not Blender
- **URLs**: https://arxiv.org/abs/2606.28593 (HTML https://arxiv.org/html/2606.28593); site anya-ji.github.io/animation2code-website
- **Task**: video → HTML/CSS/JS reproducing it; 1,069 CodePen animations (80/20 split), 7–10,657 LOC, headless Chromium 1024×768 @30 fps.
- **Metrics**: appearance = DreamSim + DTW; temporal = CoTracker3 tracklets, direction/speed agreement, Chamfer aggregation; both on animated-region crops; human validation 65 annotators / 600 pairs, ROC-AUC 0.89 appearance / 0.73 temporal.
- **Numbers (exec / appearance / temporal)**: GPT-5.4 100% / 0.84 / 0.29; Gemini 3 Flash 100% / 0.80 / 0.30; Claude Sonnet 4.6 99.5% / 0.82 / 0.29; Qwen3-VL-8B 80.4% / 0.70 / 0.24; LLaMA 4 Scout 97.7% / 0.62 / 0.21. Temporal capped ~0.31 for all.
- **License**: dataset CC BY 4.0, sources MIT.

### C6. Cutscene Agent / CutsceneBench — Apr 2026, arXiv (Kuaishou) — Unreal Engine 5, not Blender
- **URLs**: https://arxiv.org/abs/2604.25318 (HTML https://arxiv.org/html/2604.25318); project https://kuaishou-gamemind.github.io/cutscene_agent/
- **Task**: script → UE5 Level Sequence via tool calls (animation, dialogue, camera). 65 scenarios, tiers S1–S5.
- **Metrics**: L1 tool-use (selection accuracy, parameter validity, call completeness, efficiency, dependency compliance); L2 structure (track completeness, camera coverage, temporal consistency); L3 LLM-judge on rendered video, 4 dims × 0–25.
- **Numbers (CC / CamC / L3)**: Claude Opus 4.6 100% / 96.4% / 50.2; Claude Sonnet 4.6 98.4 / 89.5 / 41.7; GPT-5.4 95.7 / 93.5 / 42.4; Qwen 3.5 Plus 94.5 / 89.3 / 30.0; Qwen2.5-72B 56.6 / 66.2 / excluded.
- **License**: CC BY 4.0; code availability not stated.

---

## D. Procedural material generation

### D1. VLMaterial — Jan 2025, arXiv (MIT) (ICLR 2025 per proceedings PDF)
- **URLs**: https://arxiv.org/abs/2501.18623; ICLR PDF https://proceedings.iclr.cc/paper_files/paper/2025/file/5393eaf0b723278738788520ec54e4b4-Paper-Conference.pdf; code/data https://github.com/mit-gfx/VLMaterial
- **Task**: single image → Blender 3.3 procedural material as Python code; LLaVA-NeXT (LLaMA-3 8B + CLIP ViT-L/14) with LoRA.
- **Dataset**: 3,663 collected (BlenderKit 2,411, Infinigen 60, packs 1,192) → 1,640 valid → ~550K via GPT-4o-mini graph crossovers (50.4K) + 10× parameter perturbation; filters: ≤2,048 tokens, JPEG ≥12 KB, executes. Test: 44 Blender (ID), 64 Substance (OOD), 64 real photos.
- **Metrics**: style loss (L1 VGG Gram + 0.1·L1 of 16×16 downsample); SWD; CLIP cosine; program correctness = valid fraction (N=50 tries, K=20 valid, pick lowest style loss).
- **Numbers (Blender set, style / SWD / CLIP / correctness)**: VLMaterial 0.019 / 1.760 / 0.856 / 0.911; GPT-4o-mini 0.041 / 3.478 / 0.728 / 0.294; BlenderAlchemy 0.027 / 2.381 / 0.777; Cond. MatFormer 0.034 / 2.828 / 0.686. User study 16 participants: 91% visual / 94% graph preference over BlenderAlchemy.
- **Pitfalls**: fails >~30 nodes or >2,000 tokens; worse on <4-node materials; budget-sensitive. License: paper arXiv non-exclusive; "first public Blender procedural material dataset".

### D2. MaterialApprentice — Jul 2026, arXiv (UCSD)
- **URLs**: https://arxiv.org/abs/2607.13318 (HTML https://arxiv.org/html/2607.13318v2); site https://materialapprentice.github.io/
- **Task**: text/image → process traces retrieved from 158 YouTube tutorials (29 categories) + 10 own → compiled Blender 3.0–4.5 shader node graphs.
- **Eval**: 110 BlenderKit materials + 35 photos; 25 coarse + 25 fine edits; 5 expert artists; 150 MTurk raters.
- **Metrics**: CLIP cosine, SWD, style loss, GPT-5 forced choice, user preference %, execution rate, node detection mAP50 0.98.
- **Numbers**: image-to-material CLIP 0.852 vs VLMaterial 0.856 vs BlenderMCP 0.781; preference 92% over BlenderMCP; editing CLIP 0.260 vs BlenderMCP 0.237, 100% preference.
- **License**: arXiv non-exclusive; code/data "will be available".

---

## E. CAD-code adjacent (CadQuery/OpenSCAD, not bpy) — included because the metric designs transfer

### E1. CadCodeVerify / CADPrompt — Oct 2024 arXiv, ICLR 2025
- **URLs**: https://arxiv.org/abs/2410.05340; ICLR PDF https://proceedings.iclr.cc/paper_files/paper/2025/file/81a934cd364e18ea6fdeaf57a93c17d4-Paper-Conference.pdf
- **Task**: text → CadQuery; CADPrompt = 200 expert-annotated prompts+scripts. Metrics Chamfer, IoU, Hausdorff, compile/execute rate. GPT-4 with CadCodeVerify: −7.30% point-cloud distance, +5.0% success. Per-model numbers for Gemini 1.5 Pro / CodeLlama: not found in abstract. Code URL: not found.

### E2. Text2CAD-Bench — May 2026, arXiv
- **URLs**: https://arxiv.org/abs/2605.18430 (HTML https://arxiv.org/html/2605.18430v1)
- **Data**: 600 human+AI co-authored CadQuery examples, L1 200 / L2 200 / L3 100 / L4 100 (real-world domains), two prompt styles (geometric / sequence).
- **Metrics**: CD ×10³ bidirectional; Invalidity Rate (syntax/runtime/60 s timeout/degenerate); IoU at 256³ voxels; L4: GLM-4.6V judge, 8 views, five 0–10 questions + overall.
- **Numbers (L1-Geo CD / L3-Geo CD / L3 IR)**: GPT-5.2 44.31 / 93.46 / 68%; Claude-4.5-Sonnet 52.62 / 70.13 / 70%; DeepSeek-V3.2 53.15 / 101.23 / 69%; Qwen3-max 84.54 / 148.58 / 92%; Gemini-3-Flash 66.82 / 91.61. Domain models (Text2CAD, CADFusion) IR 2–11% but CD 209–277, IoU ≈0.
- **Pitfalls**: executability, geometric precision and feature understanding are decoupled. License CC BY 4.0; leaderboard pending.

### E3. BenchCAD — May 2026, arXiv
- **URLs**: https://arxiv.org/abs/2605.10865 (HTML https://arxiv.org/html/2605.10865v1); HF `BenchCAD/BenchCAD`; site https://benchcad.github.io/BenchCAD_webpage/; leaderboard https://llm-stats.com/benchmarks/benchcad
- **Data**: 17,900 expert-verified CadQuery programs, 106 industrial families (52 tied to ISO/DIN/ASME…), 49 operations; 30 s timeout, volume check, expert visual sign-off; BenchCAD-QA 2,400; BenchCAD-Edit 748.
- **Tasks/metrics**: image→code (exec %, voxel IoU incl. rotation-invariant, CD, essential-operation recall), code edit (headroom-normalized accuracy), vision QA, code QA. Vision QA mean 0.587 vs code QA 0.838; edit accuracy GPT-4o 0.615 → GPT-5.3-thinking 0.865; 64% of "successful" edits silently corrupt other features.
- **License**: data CC BY 4.0; code MIT.

### E4. P3D-Bench — Jun 2026, arXiv (NJU)
- **URLs**: https://arxiv.org/abs/2606.11152 (HTML https://arxiv.org/html/2606.11152v1); project https://lucasqaq.github.io/p3d/
- **Data**: 400 text + 400 image + 203 annotated assemblies from Text2CAD v1.1 and Fusion 360 Gallery; outputs JSON / OpenSCAD / CadQuery / Three.js; easy/medium/hard.
- **Metrics**: geometry (CD, F@0.05, F@0.01, normal consistency, IoU), topology (open edges, inverted normals, non-manifold), MLLM judge (semantic/geometric/aesthetic + QA banks), part (PartMatchF1, PartFS), Valid.
- **Numbers (GPT-5.5, Geo / Topo / Judge / Valid)**: text-param 0.71 / 0.997 / 0.81 / 0.999; image 0.55 / 0.91 / 0.56 / 0.98; assembly 0.59 / 0.97 / 0.54 (Part 0.61) / 0.99. Others: Gemini 3.1 Pro, Claude Opus 4.6, Kimi K2.6, Qwen3.6-Plus, DeepSeek V4 Pro, GLM-5.1, MiMo v2.5 Pro (numbers not in fetched excerpt).
- **Pitfalls**: assemblies hardest; semantic ≫ geometric precision (~0.8 vs ~0.35). License arXiv non-exclusive.

---

## F. Text-to-3D evaluators and judges (non-code; reused as metrics for code-generated assets)

### F1. T3Bench — Oct 2023, arXiv (Tsinghua)
- **URLs**: https://arxiv.org/abs/2310.02977 (HTML https://arxiv.org/html/2310.02977v1); code https://github.com/THU-LYJ-Lab/T3Bench; site https://t3bench.com
- **Data**: 300 GPT-4-generated, manually filtered prompts (100 single object / 100 with surroundings / 100 multi-object), ROUGE-L dedup.
- **Metrics**: Quality = multi-view (161 icosahedron vertices × 5 focal lengths) ImageReward + regional convolution / graph mean pooling to detect Janus inconsistency (Spearman 0.752 with humans); Alignment = BLIP captions from 12 views merged by GPT-4, recall-oriented scoring (Spearman 0.765).
- **Numbers (avg over quality+alignment, single / surroundings / multi)**: ProlificDreamer 49.4 / 44.8 / 35.8; Magic3D 37.0 / 35.4 / 25.7; DreamFusion 24.4 / 24.6 / 16.1.
- **License**: arXiv non-exclusive; code public.

### F2. GPTEval3D — Jan 2024, arXiv
- **URLs**: https://arxiv.org/abs/2401.04092; code https://github.com/3DTopia/GPTEval3D; site https://gpteval3d.github.io/
- **Protocol**: GPT-4V generates prompts, does pairwise comparisons under user-defined criteria, Elo from comparisons; "strongly align with human preference". Kendall τ numbers, prompt/model counts: not found in abstract.

### F3. 3DGen-Bench / 3DGen-Arena — Mar 2025, arXiv
- **URLs**: https://arxiv.org/abs/2503.21745 (HTML https://arxiv.org/html/2503.21745v1); site https://zyh482.github.io/3DGen-Bench/; HF dataset (per paper).
- **Data**: 1,020 prompts (510 text / 510 image), 22 generators (9 T23D, 13 I23D), 68,400 pairwise votes + 56,100 absolute scores over 5 dims (geometry plausibility, geometry details, texture quality, geometry–texture coherence, prompt alignment).
- **Metrics**: 3DGen-Score (CLIP-based multi-view win-prob) alignment 0.725 T23D / 0.767 I23D vs CLIP 0.661 / 0.531; 3DGen-Eval (MLLM judge).
- **License**: prompts/annotations MIT; assets per source model.

---

## G. Spatial-reasoning prerequisites for 3D-software agents (not code generation)

### G1. MultiView-Bench — Jul/Aug 2026, arXiv (Yale)
- **URLs**: https://arxiv.org/abs/2607.08970 (HTML https://arxiv.org/html/2607.08970); site hantaozhangrichard.github.io/MultiView-Bench
- **Task**: Blender-rendered multi-view images with visible axes (GUI-like) → world-centric object coordinates (±X/0, ±Y/0, ±Z/0); 5 variants × 100 + 20 extended (2,500+ instances). 3-DoF accuracy: GPT-5.6 Sol 63.3%, Claude Opus 4.8 56.3%, Gemini 3.5 Flash 54.3%, GPT-5 49.0%, GPT-4o 2.0%. Pitfalls: axis confusion, rotation brittleness. License CC BY 4.0.

### G2. Open3D-VQA — Mar 2025, ACM MM 2025
- **URLs**: https://arxiv.org/abs/2503.11094; code https://github.com/EmbodiedCity/Open3D-VQA.code
- **Task**: aerial-view spatial VQA (73k pairs, 7 tasks, real + simulated); 13 MLLMs; relative relations easier than absolute distances; 3D-LLMs no advantage. Not a code benchmark; license arXiv non-exclusive.

---

## H. Searched for but not found / out of scope

- **3D-Fixup** (SIGGRAPH 2025, https://arxiv.org/abs/2505.10566) — diffusion-based 3D-aware photo editing; no code generation or Blender benchmark.
- **GenEval-3D / 3D-GenEval** — no such benchmark found; only GenEval (T2I, https://arxiv.org/html/2310.11513) and GenEval 2 (https://arxiv.org/html/2512.16853v1).
- **BlenderMCP** (https://github.com/ahujasid/blender-mcp) — tool, not a benchmark; no paper with metrics found. It appears as a baseline in LL3M (A15) and MaterialApprentice (D2), and SceneActBench (A10) uses an MCP interface.
- **"Blender-Bench"** — only Blender's own GPU benchmark (https://opendata.blender.org/); the LLM "BlenderBench" is VIGA's (A9).
- **MotionScript** (https://arxiv.org/abs/2312.12634) — natural-language motion captioning, not code. **ChatAnimation**, **AniCraft** (UIST 2024 MR prototyping) — not LLM code benchmarks.
- **MatAgent** — materials-science discovery agents (https://arxiv.org/pdf/2504.00741), not graphics materials.
- **SceneProg** — no benchmark by that name found; nearest are Scene Language (B7), Open-Universe DSL (B6), HDSL (B8). **SceneScript** (Meta) is reconstruction-to-structured-language, not LLM code generation.
- **SCOPE** (https://github.com/HindsboNikolaj/SCOPE, HRI 2026) — PTZ-camera agent tasks in Blender (536 tasks, GPT-4o-judged accuracy, best 73.8% Qwen3-30B-A3B+Moondream3, MIT); no code generation.
- **BlenderGPT** — used as a baseline in SceneCraft/BlenderAlchemy; no standalone benchmark found.
- **Infinigen-based LLM benchmark** — the only ones found are 3DCodeBench (A8) and 3D-GPT (A6); Code2Worlds (C1) and MeshCoder (A17) also build on Infinigen.
- **LLM Blender keyframe-animation benchmark** — none with keyframe-level metrics; closest are 4DBuildBench (C2, solver-based), Code4D (C1), SceneActBench Dynamic/Articulated tracks (A10), Animation2Code (C5, web).

<!-- ===== shader_web.md ===== -->

# Survey: benchmarks / datasets / metrics for LLM+VLM generation of shaders, three.js/WebGL scenes, and symbolic graphics programs

Compiled 2026-09-08. Coverage window: 2023 – mid 2026. Every claim carries a URL; "not found" means I could not locate the fact in a primary source (I did not guess). Numbers are copied from the cited page; where a page was gated (HF 401) I say so.

Organisation:
- Part A — GLSL / Shadertoy (ShaderEval family, shaders21k, 2025–26 shader-LLM papers, public micro-evals)
- Part B — three.js / WebGL / browser 3D (WorldCoder-Bench, P3D-Bench, ArtifactsBench, Web-Bench, WebGen-Bench, WebDev Arena, Mage/Unity)
- Part C — Symbolic graphics programs: SGP-Bench, SGP-GenBench, SVG benchmarks (brief), VCode
- Part D — Analogous "code → render → compare" benchmarks: Design2Code family, Sketch2Code, Interaction2Code, FullFront, WebIGBench, Image2Struct, Plot2Code, ChartMimic, SWE-bench Multimodal, TikZ (DaTikZ/TikZero, vTikZ), Manim (ManiBench, ManimBench, DTVBench), VisPlotBench, Vision2Code, InteractScience
- Part E — Procedural material / node-graph and Blender-program benchmarks (VLMaterial, MultiMat, BlenderGym, SceneCraft)
- Part F — Cross-cutting metric definitions and pitfalls (what transfers to a GLSL / three.js eval)
- Part G — Things searched for and NOT found

---------------------------------------------------------------------------------------------------

## PART A — GLSL / Shadertoy

### A1. ShaderMatch / ShaderEval-2 ("Evaluating Language Models for Computer Graphics Code Completion")
- **Name; year/venue**: ShaderMatch benchmark + metric (a.k.a. ShaderEval task 2, "shadereval-2: FunctionCompletion"). Kels, Dahou, Mathiak. *2025 IEEE/ACM International Workshop on Large Language Models for Code (LLM4Code)*, co-located with ICSE 2025, pp. 96–103, DOI 10.1109/LLM4Code66737.2025.00017. Author "Vipitis" = Jan Kels (HHU Düsseldorf); co-authors at GESIS.
  - https://conf.researchr.org/details/icse-2025/llm4code-2025-papers/13/Evaluating-Language-Models-for-Computer-Graphics-Code-Completion
  - https://ieeexplore.ieee.org/document/11028297/
- **URLs**: no arXiv version found. Leaderboard + metric space: https://huggingface.co/spaces/Vipitis/Shadermatch ; metric code: https://huggingface.co/spaces/Vipitis/Shadermatch/raw/main/shadermatch.py ; inputs: https://huggingface.co/datasets/Vipitis/Shadereval-inputs ; results: https://huggingface.co/datasets/Vipitis/Shadereval-results ; raw generations (gated): https://huggingface.co/datasets/Vipitis/Shadereval-runs ; dataset builder: https://github.com/Vipitis/shadertoys-dataset ; bigcode-evaluation-harness task PR: https://github.com/bigcode-project/bigcode-evaluation-harness/pull/173
- **Task**: partial code → GLSL. Zero-shot *function completion*: the model receives `model_inp` = the user-written comment preceding a function + the function header, and must produce the function body; the completed function is spliced back into the full Shadertoy "image" pass and rendered. (Shadereval-inputs card; conf page abstract.)
- **Dataset size & construction**: paper abstract says **467 function headers**; the current HF card (v0.3) says **394 functions** (v0.2 had 257) — both from https://huggingface.co/datasets/Vipitis/Shadereval-inputs. Single `test` split (~726 kB). Sources: the shaders21k/"shaders20k" Shadertoy subset (years 2013–2021) plus the Shadertoy public API (2022–2023). Pipeline (GitHub README): download via Shadertoy API (API key) → "annotate" (flatten nested render passes; extract license and functions with tree-sitter-glsl v0.1.9; license detection via scancode-toolkit) → "filter". Every retained function was validated with wgpu-shadertoy to confirm it is actually necessary for the shader (removing it changes the frames). Fields: comment, header, body, image_code, id, author, date, license (SPDX identifiers, e.g. MIT, CC0-1.0), func_bytes, functions, model_inp, function_frequency, header_frequency. Licensing: per-shader SPDX tags; builder scripts Apache-2.0; "shader code are under their respective license". Dedup / leakage: the Shadereval-runs dataset is *gated* "to prevent training use"; the santacoder model card explicitly warns of contamination for the earlier Shadertoys-fine training set (see A3). Difficulty tiers: not found.
- **Execution environment**: wgpu-shadertoy (wgpu-py, i.e. WebGPU/wgpu-native, headless) — from shadertoys-dataset README and the metric code. Resolution **(512, 288)** ("matching thumbnail dimensions"). Rendered at **10 timesteps: [0.0, 0.1, 0.2, 0.5, 1.0, 1.6787, 2.0, 2.31, 3.333, 17]** chosen to avoid periodic patterns (shadermatch.py). Space README admits the timestamps "are not methodologically justified" and could miss shaders with particular periodicity. Timeouts: not found.
- **Metric — exact definition (from shadermatch.py + README)**: two-step — static code comparison, then frame-render comparison. Checks run in this order and each sample gets exactly one of **eight labels**:
  1. `incomplete_generation` — generation contains the marker "// incomplete generation!" (function never closed / cut off).
  2. `c0-clone` — exact string match with the reference.
  3. `c1-clone` — "lexical similarity": only whitespace/comments differ.
  4. `c2-clone` — "syntactic similarity": only identifiers differ.
  5. `code_error` — shader fails to compile.
  6. `c4-clone` — "semantic similarity": all 10 timesamples render identical images. Image equality is **exact pixel equality** via `PIL.ImageChops.difference(...).getbbox() is None` — no tolerance.
  7. `single_color` — all rendered frames are a single uniform colour (treated as failure).
  8. `variation` — renders differ from the reference at some timestep ("potentially better or worse than reference").
  Derived leaderboard aggregates (analysis.py): `error_rate = code_error + incomplete_generation`; `clone_rate = c0+c1+c2+c4`; leaderboard sorted by error_rate ascending. https://huggingface.co/spaces/Vipitis/Shadermatch/raw/main/analysis.py
- **Generation settings**: Shadereval-results card: temperature 0.2, top_p 0.95, seed 0 (22 models). Number of samples per prompt: not found.
- **Reported baseline numbers (Shadereval-results, fraction of test set per label)** — https://datasets-server.huggingface.co/rows?dataset=Vipitis/Shadereval-results&config=default&split=train :

| model | incomplete | c0 | c1 | c2 | c4 | code_error | single_color | variation |
|---|---|---|---|---|---|---|---|---|
| deepseek-coder-6.7b-base | 0.1285 | 0.0428 | 0.0600 | 0.0043 | 0.0942 | 0.1799 | 0.0171 | 0.4732 |
| deepseek-coder-7b-base-v1.5 | 0.1328 | 0.0321 | 0.0514 | 0.0043 | 0.0921 | 0.1842 | 0.0236 | 0.4797 |
| deepseek-coder-5.7bmqa-base | 0.1285 | 0.0321 | 0.0535 | 0.0064 | 0.0814 | 0.1949 | 0.0236 | 0.4797 |
| deepseek-coder-1.3b-base | 0.1413 | 0.0128 | 0.0471 | 0.0043 | 0.0514 | 0.1799 | 0.0321 | 0.5310 |
| CodeQwen1.5-7B | 0.1285 | 0.0343 | 0.0428 | 0.0193 | 0.0835 | 0.1906 | 0.0278 | 0.4732 |
| StarCoder2-15B | 0.096 | 0.021 | 0.090 | 0.011 | 0.094 | 0.231 | 0.024 | 0.433 |
| StarCoder2-7B | 0.199 | 0.009 | 0.032 | 0.004 | 0.081 | 0.231 | 0.028 | 0.415 |
| StarCoder2-3B | 0.208 | 0.006 | 0.039 | 0.009 | 0.045 | 0.225 | 0.026 | 0.443 |
| CodeLlama-13b-hf | 0.1092 | 0.0343 | 0.0450 | 0.0064 | 0.0878 | 0.2056 | 0.0171 | 0.4946 |
| CodeLlama-7b-hf | 0.1734 | 0.0300 | 0.0407 | 0.0064 | 0.0707 | 0.2441 | 0.0300 | 0.4047 |
| Llama-3.1-8B | 0.167 | 0.002 | 0.047 | 0.004 | 0.056 | 0.208 | 0.036 | 0.480 |
| Yi-Coder-9B | 0.150 | 0.024 | 0.049 | 0.013 | 0.088 | 0.225 | 0.017 | 0.435 |
| Yi-Coder-1.5B | 0.291 | 0.011 | 0.021 | 0.002 | 0.036 | 0.214 | 0.026 | 0.398 |
| granite-20b-code-base | 0.1413 | 0.0214 | 0.0664 | 0.0064 | 0.0878 | 0.2120 | 0.0150 | 0.4497 |
| granite-8b-code-base | 0.1328 | 0.0128 | 0.0578 | 0.0086 | 0.0792 | 0.2463 | 0.0171 | 0.4454 |
| granite-3b-code-base | 0.1991 | 0.0150 | 0.0385 | 0.0128 | 0.0642 | 0.2120 | 0.0107 | 0.4475 |
| codegemma-7b | 0.1306 | 0.0107 | 0.0407 | 0.0021 | 0.0578 | 0.2570 | 0.0214 | 0.4797 |
| codegemma-2b | 0.2355 | 0.0086 | 0.0300 | 0.0000 | 0.0471 | 0.2227 | 0.0343 | 0.4218 |
| stable-code-3b | 0.126 | 0.017 | 0.021 | 0.006 | 0.066 | 0.257 | 0.026 | 0.480 |
| phi-2 | 0.373 | 0.004 | 0.006 | 0.000 | 0.009 | 0.345 | 0.013 | 0.251 |
| phi-1_5 | 0.373 | 0.002 | 0.002 | 0.000 | 0.019 | 0.355 | 0.024 | 0.225 |
| phi-1 | 0.122 | 0.002 | 0.000 | 0.000 | 0.015 | 0.608 | 0.024 | 0.229 |

  Derived (my arithmetic on the table above): deepseek-coder-6.7b-base error_rate = 0.308 (this is the "31 % fail to generate working code" from the abstract), clone_rate = 0.201; StarCoder2-15B error_rate 0.327, clone_rate 0.216; CodeQwen1.5-7B error_rate 0.319. No frontier/closed models appear in the results set (only open code models ≤20B). Model metadata (size, FIM support, #languages, whether GLSL was in pretraining) is in https://huggingface.co/spaces/Vipitis/Shadermatch/raw/main/models.csv (e.g. StarCoder2-15B "glsl-trained: yes"; CodeLlama "unknown"; phi "no").
- **Pitfalls / observations**: (i) exact-pixel c4 match is strict — tiny float differences push a sample into `variation`, so "variation" (0.22–0.53) is the dominant bucket and is *not* a quality score; (ii) `single_color` is a specific degenerate mode (1–4 % of samples) that a naive image metric would score as "similar" for dark references; (iii) `incomplete_generation` is large for small models (up to 37 %) — a max-token / stop-sequence artefact; (iv) timestamps not principled; (v) GLSL called "a low-resource language rarely found in pretraining datasets"; (vi) raw generations gated to limit contamination.
- **License / availability**: metric + space open (HF); inputs public with per-shader SPDX licenses; generations gated; results public.

### A2. ShaderEval-1 (2023, "return completion")
- **Name; year**: ShaderEval task 1 "shadereval-1: ReturnCompletion" (Vipitis, 2023; bigcode-evaluation-harness PR #173). https://github.com/bigcode-project/bigcode-evaluation-harness/pull/173 ; space https://huggingface.co/spaces/Vipitis/ShaderEval (README only holds space config).
- **Task**: partial GLSL function body up to the `return` → predict the return statement. Subset "return_completion" of Shadertoys-fine; two features: body-before-return and the return statement; evaluator keeps everything before the semicolon, strips whitespace.
- **Metric**: `exact_match` (HF evaluate). 
- **Numbers**: only found for the fine-tuned santacoder model card: 0.567 on 300 samples (greedy), 0.597 on all samples (greedy). The card states "The model results can't be trusted for this simple benchmark" because of data contamination and "an undefined license" for the training data. https://huggingface.co/Vipitis/santacoder-finetuned-Shadertoys-fine
- **Pitfall**: superseded by ShaderMatch precisely because exact-match on a return line is contamination-prone and blind to semantics.

### A3. Shadertoys / Shadertoys-fine datasets (Vipitis)
- **URLs**: https://huggingface.co/datasets/Vipitis/Shadertoys ; https://huggingface.co/datasets/Vipitis/Shadertoys-fine ; builder https://github.com/Vipitis/shadertoys-dataset . The two HF cards returned HTTP 401 (gated) to my fetcher; facts below come from search snippets of the cards and the GitHub README.
- **Size / fields**: Shadertoys "contains over 44k renderpasses collected from the Shadertoy.com API"; a datapoint is the whole shader code + API info + metadata (fields: num_passes, has_inputs, name, type, code, title, description, tags, license, author, source). Shadertoys-fine is "a finer variant" (function-level rows) used for fine-tuning. Train/test split sizes: not found (gated).
- **Construction**: Shadertoy API download → annotate (tree-sitter-glsl function extraction, scancode license detection) → filter to an Arrow dataset. Alternative source "shaders20k" (Shadertoy subset of shaders21k). License: shaders keep their own license (many Shadertoy shaders default to CC BY-NC-SA 3.0; the dataset annotates SPDX per shader); builder Apache-2.0.
- **Leakage**: the author's own note that Shadertoys-fine-trained models are contaminated for ShaderEval-1 (A2).

### A4. shaders21k (Baradad et al., NeurIPS 2022) — corpus, not an LLM benchmark
- https://github.com/mbaradad/shaders21k ; https://mbaradad.github.io/shaders21k/ ; paper "Procedural Image Programs for Representation Learning" https://openreview.net/pdf?id=wJwHTgIoE0P
- 21k OpenGL fragment shaders: 1k from TwiGL + 20k from Shadertoy; provides rendered frames (384×384, "979 frames per second" on one GPU) for visual representation learning. Used by ShaderEval as the 2013–2021 source. Predecessor: "Learning to See by Looking at Noise" https://arxiv.org/pdf/2106.05963 .

### A5. AI Co-Artist (GLSL shader evolution with GPT-4), arXiv 2512.08951 (Nov 2025)
- Yuksel & Sawaf. https://arxiv.org/abs/2512.08951 ; html https://arxiv.org/html/2512.08951v1
- Task: text/parent-selection → GLSL fragment shaders (Picbreeder-style evolutionary UI; audio-reactive shaders; VLMs as "autonomous aesthetic judges" for closed-loop evolution).
- Evaluation: user studies only — "novice users created 4.2 shaders on average with AI assistance versus 0.6 without; experts 6.8 vs 2.9; time to first viable output reduced by 60 %" (from search snippet of the HTML). No public benchmark dataset, no compile-rate table found. Code link: not found.

### A6. ShadAR (IEEE ISMAR-Adjunct 2025; arXiv 2602.17481, Feb 2026)
- Mei, Wendt, Mueller, Gugenheimer. https://arxiv.org/abs/2602.17481
- Text → **HLSL** shader (per themoonlight review: "an LLM interprets to generate corresponding HLSL code") compiled live to an AR headset viewport. 2-page demo paper; no dataset, no metrics, no numbers. Code: not found.

### A7. Artificial Analysis MicroEval "LLM Ultimate Challenge: Interactive GLSL Shader Art" (2025)
- https://artificialanalysis.ai/microevals/llm-ultimate-challenge-interactive-glsl-shader-art-1756340323607
- Single prompt: one self-contained HTML file using **three.js + a GLSL fragment shader** rendering an animated Julia set with mouse-wheel zoom (cursor-centred), drag-pan, mouse-x drives animation, space pauses; a specified palette (indigo→magenta→orange→yellow). Scoring is human thumbs-up voting. 16 models; those with 1 vote: Qwen3 Coder 480B A35B, MiniMax M1 40k, Kimi K2, Mistral Medium 3, Grok 4, DeepSeek V3 0324, Llama 4 Maverick, Claude 4 Sonnet; o3 and Gemini 2.5 Pro among the 0-vote models. Date not shown. Not a rigorous benchmark, but the only public multi-model GLSL+three.js comparison found.

### A8. Blog: "Generative AI for Procedural 2D Shaders: Teaching a Language Model to Draw With Math" (Medium, Jul 2026)
- https://medium.com/@niloufarmj/generative-ai-for-procedural-2d-shaders-teaching-a-language-model-to-draw-with-math-2647e15679b6 — fetch returned 403; content not verified. Listed only as a pointer.

---------------------------------------------------------------------------------------------------

## PART B — three.js / WebGL / browser 3D / game engines

### B1. WorldCoder-Bench: Benchmarking Physically Grounded 3D World Synthesis (arXiv 2606.01869, Jun 2026)
- Lu, Xu, Yu, Jiang, Yu, Wang, Yang, Zhang, Wang, He, Liang. https://arxiv.org/abs/2606.01869 ; html https://arxiv.org/html/2606.01869 ; code/data: anonymous.4open.science/r/WorldCoder-Bench/ (per abstract). License CC BY 4.0.
- **Task**: natural-language spec (+ optional .glb assets) → executable **three.js** scene whose runtime state must satisfy hidden behavioural contracts (spatial/physical constraints, user controls in sync with engine state).
- **Dataset**: 2,026 expert-curated tasks; Simulation 649 / Rendering 620 / Application 757; 15 fine-grained domains "including physics, shaders, materials, game, animation, architecture". WorldCoder-Core = 205 tasks (primary leaderboard); WorldCoder-Robust = 615 parameter-perturbed variants (3 per Core task). Construction: 5 PhD researchers hand-crafted seeds over 2 months with peer review → LLM-assisted expansion to 10k+ candidates → annotator filtering → runtime validation in headless browser, expert-authored contracts, unstable tasks discarded → anti-contamination via randomised physical constants/object counts/prompt rewrites.
- **Execution**: headless Chromium via Playwright; three.js version-locked from a local archive; fixed browser config; program must expose a standardised runtime-state interface `window.__3D_STATE__`; deterministic action sequences with state snapshots; outcomes labelled Runtime_Crash / Check_Fail / Check_Pass with auditable traces.
- **Metrics**: "StateProbe" protocol. Primary **Verification Coverage (V-Cov)** = proportion of hidden assertions that pass; diagnostics: Affordance Coverage (required objects/controls present), State Coverage (intermediate states reachable), Transition Coverage (action-triggered post-conditions). Contracts calibrated by mutation testing. Also "Return on Automation" and "Time Efficiency Multiplier" (cost/time savings adjusted by correctness).
- **Numbers**: best GPT-5.4 27.8 % V-Cov on Core, 19.9 % on Robust; Gemini 3.1 Pro 26.5 %; Qwen 3.6-Plus 25.3 %; Claude Opus 4.6 17.5 % (Core). 9 frontier models; no ~7–9B models reported (not found).
- **Pitfalls**: DOM/screenshot-based scoring is essentially uncorrelated with hidden state correctness — Kendall τb = −0.02 over 1,434 model-task pairs; dominant failures are "state-schema drift and broken interaction chains rather than missing scene elements".

### B2. P3D-Bench: Benchmarking MLLMs for Parametric 3D Generation and Structural Reasoning (arXiv 2606.11152, Jun 2026)
- Yang, Hu, Lin, Zhou, Xu, Zhang, Liu, Yao (Nanjing Univ., Envision). https://arxiv.org/html/2606.11152v1 ; project https://lucasqaq.github.io/p3d/ (page body empty at fetch time); code/data links: not found.
- **Task**: text or single rendered image → program in one of four formats: minimal JSON, OpenSCAD, CadQuery, **Three.js**; executed and rendered by the benchmark. Three sub-tasks: Text-to-3D (single part), Image-to-3D (multi-part), Assembly-3D (part + assembly annotations).
- **Dataset**: from Text2CAD v1.1 (176,017 single-part programs) + Fusion 360 Gallery (8,251 assemblies) → dedup/filter with DINOv2 embeddings → complexity-balanced sampling → 400 Text-to-3D, 400 Image-to-3D, 203 Assembly-3D cases.
- **Execution**: Three.js executed headless, producing triangulated meshes; other formats via their native toolchains.
- **Metrics**: Validity (compiles + renders); Geometry (F-score@0.05/0.01, Chamfer distance, normal consistency, IoU); Topology (no-open-edge ratio, inverted-normal ratio, non-manifold ratios); MLLM judge (semantic/geometric/aesthetic ratings + QA accuracy on spec constraints); Part-level (Hungarian-matched part F-scores, part-count accuracy).
- **Numbers**: GPT-5.5 Text-to-3D JSON: Geo 0.696 / Topo 0.997 / Judge 0.773 / Valid 1.000; Image-to-3D OpenSCAD: 0.567 / 1.000 / 0.592 / 1.000; Assembly-3D CadQuery: 0.570 / 0.948 / 0.527 / 0.985; Three.js on Image-to-3D ≈ Geo 0.556 / Topo 0.828 / Judge 0.569. Open ~7–9B numbers: not found in the fetched text.
- **Pitfalls**: Three.js scores low on topology because triangulated output lacks manifold guarantees; semantic alignment (~0.8) far exceeds geometric precision (~0.35); CadQuery failures are parameter/geometry errors while JSON/OpenSCAD failures are syntax; PartMatchF1 ≈ 0.5 for the best model.

### B3. ArtifactsBench (Tencent Hunyuan, arXiv 2507.04952, Jul 2025)
- https://arxiv.org/abs/2507.04952 ; html https://arxiv.org/html/2507.04952 ; code https://github.com/Tencent-Hunyuan/ArtifactsBenchmark ; data https://huggingface.co/datasets/tencent/ArtifactsBenchmark (CC-BY-NC-4.0).
- **Task**: text prompt → interactive visual artifact (HTML/JS/SVG). 1,825 tasks, 9 domains: Game Development, SVG Generation, Web Applications, Simulations, Data Science, Management Systems, Multimedia Editing, Quick Tools, Others; 74 fine classes; dynamics tiers Static Visual / Mild-to-Moderate Dynamics / High Dynamics / Intensive Interactive. Dataset card shows Canvas/HTML5 games and at least one explicit **three.js task ("Rubik's Cube")**; no WebGL/shader-specific tasks visible.
- **Construction**: expert sourcing + LLM generation → automated filtering of incomplete/non-interactive items → two-stage dedup (MinHash + semantic similarity); difficulty assigned retrospectively from aggregate model performance to a 30/40/30 Easy/Medium/Hard split.
- **Execution**: sandboxed headless Chromium via Playwright at 1024×768; three staged screenshots (before / during / after interaction).
- **Metric**: MLLM-as-Judge (Gemini-2.5-Pro; Qwen2.5-VL-72B as second referee) with a per-task 10-item checklist; dimensions split into vision-oriented (layout, fidelity, motion, feedback, UX) and code-oriented (logic correctness, robustness, modularity, scalability, redundancy avoidance). Score reported 0–100.
- **Numbers**: paper — Gemini-2.5-Pro ≈ 59, Claude 4.0 Sonnet ≈ 57, DeepSeek-R1-0528 ≈ 51, Qwen 7–14B models 25–32. Leaderboard v1.2 (Aug 2025, GitHub README): GPT-5 72.55, Claude Opus 4.1 59.76, GPT-OSS-120B 57.69. Human agreement: 90.95 % pairwise (Gemini judge), 71.34 % (Qwen judge); 94.4 % ranking consistency with WebDev Arena. JanusCoder reports ArtifactsBench task scores of 80 (8B) / 86 (14B) / GPT-4o 85 — https://arxiv.org/html/2510.23538 (note: different scoring version).
- **Pitfalls**: all models underperform on Intensive-Interactive and project-level tasks; judge quality varies strongly with judge model (91 % vs 71 % agreement).

### B4. Web-Bench (ByteDance, arXiv 2505.07473, May 2025)
- Xu, Mao, Guan, Feng. https://arxiv.org/abs/2505.07473 ; https://github.com/bytedance/web-bench (Apache-2.0) ; data https://huggingface.co/datasets/bytedance-research/Web-Bench
- **Task**: 50 projects × 20 sequentially dependent tasks (1,000 tasks) covering Web Standards (HTML, CSS, JS, DOM, SVG, **WebGL**, TypeScript) and frameworks (React, Vue, Redux, Next.js, Vite, Prisma). Search snippet confirms a project that is "a 3D version of the Snake game by three.js with a portal mechanism added" (https://arxiv.org/pdf/2505.07473). Designed by engineers with 5–10 years' experience; 4–8 h per project for a senior engineer.
- **Execution / metric**: Web-Agent feeds each task + current project files to the LLM, extracts code, runs Playwright E2E tests. Pass@1 (and Error@1 in the repo; exact definition not fetched).
- **Numbers**: Claude 3.7 Sonnet 25.1 % Pass@1 (best); open-model numbers not fetched (leaderboard on HF Space per README).
- **Observations**: much harder than SWE-bench Verified (65.4 %) for the same model.

### B5. WebGen-Bench (NeurIPS 2025 D&B; arXiv 2505.03733)
- Lu et al. https://arxiv.org/abs/2505.03733 ; https://openreview.net/forum?id=q2VpjD7k1V
- Text instruction → multi-file website from scratch; instructions by humans + GPT-4o; 3 major / 13 minor categories; **647 test cases** (GPT-4o-generated, manually filtered) executed by a web-navigation agent; appearance judged by GPT-4o. Training set WebGen-Instruct 6,667 instructions.
- Numbers: Bolt.diy + DeepSeek-R1 27.8 % (best off-the-shelf); Qwen2.5-Coder-32B-Instruct fine-tuned on Bolt.diy trajectories 38.2 %. No 3D/WebGL-specific subset found.

### B6. WebDev Arena (LMArena, launched Dec 2024)
- https://news.lmarena.ai/webdev-arena/ ; https://arena.ai/blog/webdev-arena
- Live human-preference leaderboard: two anonymous models build a web app in a sandbox, humans vote; Bradley-Terry Arena Score. By May 2026: 288,203 votes across 77 models. Has a fine-grained category "3D Graphics and Shaders" (per DeepWiki/aiwiki summaries of the category taxonomy: https://deepwiki.com/lmarena/lmarena.github.io/2.3-webdev-arena). Per-category scores for the 3D/shader slice: not found in fetched pages. ArtifactsBench uses WebDev Arena as its human-preference gold standard (94.4 % ranking consistency).

### B7. Mage: Multi-Axis Evaluation of LLM-Generated Executable Game Scenes Beyond Compile-Pass Rate (arXiv 2605.07342, May 2026)
- Liu & Tatar. https://arxiv.org/abs/2605.07342 (companion: "Grounding Machine Creativity in Game Design Knowledge Representations…" https://arxiv.org/pdf/2603.07101)
- **Task**: NL / intermediate representation → **Unity C#** scene code. 26 hand-crafted Unity goal patterns; 858 generation attempts; 4 open-weight LLMs (7B–30B); two IR granularities.
- **Metrics (four axes)**: compile success; runtime success; structural fidelity; mechanism adherence (F1 against the goal-pattern mechanisms).
- **Numbers**: direct NL→C#: 43 % mean runtime-pass but mechanism F1 ≈ 0.12; IR-conditioned: runtime rate roughly halved but F1 up to 1.00. Compile-pass rate was anti-correlated with functional correctness. Benchmark, replay logs, per-record metrics released (URL not in abstract).
- **Pitfall**: "compile-correctness divergence" — the same phenomenon as ShaderMatch's `variation`/`single_color`: code that runs but does nothing meaningful.

### B8. Unreal Blueprint / OpenGL C++ LLM benchmarks
- Not found as benchmarks. Only tooling: NodeToCode (Blueprint→C++ via LLMs) https://github.com/protospatial/NodeToCode ; a Unity unit-test-generation paper https://dl.acm.org/doi/pdf/10.1145/3663532.3664466 . No OpenGL-C++ LLM benchmark located.

---------------------------------------------------------------------------------------------------

## PART C — Symbolic graphics programs and SVG

### C1. SGP-Bench: "Can Large Language Models Understand Symbolic Graphics Programs?" (ICLR 2025 Spotlight; arXiv 2408.08313)
- Qiu, Liu, Feng, Liu, Xiao, Collins, Tenenbaum, Weller, Black, Schölkopf. https://arxiv.org/abs/2408.08313 ; html https://arxiv.org/html/2408.08313v3 ; https://openreview.net/forum?id=Yk87CwhBDx ; https://github.com/sgp-bench/sgp-bench ; https://sgp-bench.github.io/
- **Task**: program text (SVG or CAD DSL) → answer multiple-choice questions about the *rendered image* without seeing it (semantic understanding), plus **semantic consistency** (same questions on SE(2)-perturbed programs — translation/rotation — which changes numerics but not semantics; also reduces leakage risk).
- **Dataset**: SVG 4,340 questions on 1,085 programs across 19 categories (Semantic 1,085 / Color 864 / Shape 1,217 / Count 819 / Reasoning 355); CAD 2,400 questions (3D DeepCAD 1,000; 3D-complex Fusion360 700; 2D SketchGraphs 700); SGP-MNIST 1,000 (100 per digit). Questions generated by rendering + GPT-4o captioning/question generation, then human verification; 4 options, balanced answers. Sources: Kaggle SVG Icons (SVGrepo, permissive), DeepCAD, Fusion360, SketchGraphs; MNIST-SVG CC BY-SA 3.0.
- **Numbers (accuracy, SVG / CAD)**: Claude 3.5 Sonnet 67.4 / 74.2; GPT-4o 63.3 / 73.3; Llama-3.1-70B 57.4 / 68.8; Mistral-Large2 57.2 / 71.0; Qwen-2-72B 53.7 / 69.2; **Llama-3.1-8B 46.5 / 57.4**. Symbolic Instruction Tuning (72k programs with VLM-written descriptions) lifts a baseline from 46.7 → 51.4.
- **Pitfalls**: performance tracks general reasoning strength; SGP-MNIST is near-chance for LLMs (per paper's framing); no rendering is needed at eval time, so this is an *understanding*, not generation, benchmark.

### C2. SGP-GenBench: "Symbolic Graphics Programming with Large Language Models" (TMLR; arXiv 2509.05208, Sep 2025)
- Chen, Zhang, Huang, Qiu, Zhang, Wen, Liu. https://arxiv.org/abs/2509.05208 ; html https://arxiv.org/html/2509.05208 ; https://spherelab.ai/SGP-Gen/ ; OpenReview https://openreview.net/forum?id=JfK8EHJAQn
- **Task**: text → SVG. Three parts: Scene (COCO-val, 1,024 captions), Object (SGP-Object-val, 930 internet SVGs with AI captions), Compositional (SGP-CompBench, 3,200 prompts: attribute binding colour/shape/texture, spatial 2D/3D/implicit, numeracy 3–10 objects).
- **Metrics**: SigLIP text–image cosine; DINO image–image vs reference; VQA-Score; HPS v2; compositional: Gemini-2.5-Flash judge 0–100 (numeracy = 20 % total count + 20 % item presence + 60 % count-per-item). RL reward = format validity + SigLIP/DINO alignment.
- **Numbers**: VQA avg — Claude 3.7 Sonnet Thinking 0.584 (comp 84.8); Gemini 2.5 Pro Preview 0.563 (76.2); **Qwen-2.5-7B base 0.325 (comp 8.8) → +RL 0.596 (comp 60.8)**.
- **Observation**: RL "induces finer object decomposition and improved scene coherence"; a 7B model matches frontier on VQA after RL — an existence proof that render-and-compare rewards work for symbolic graphics.

### C3. SVG benchmarks (brief, metrics only)
- **StarVector / SVG-Bench** (arXiv 2312.11556, v3 May 2025, CC BY 4.0): image/text → SVG; SVG-Bench = 10 datasets, 3 tasks (Image-to-SVG, Text-to-SVG, diagram generation); metric emphasis on DinoScore plus SSIM/LPIPS/MSE (SVGenius reports StarVector SSIM 37.60–56.53). https://arxiv.org/abs/2312.11556 ; https://github.com/joanrod/star-vector ; https://starvector.github.io/starvector/
- **SVGEditBench** (CVPRW 2024) — MSE on rasterised edit results; **SVGEditBench V2** (arXiv 2502.19453): 1,683 (original SVG, edited GT, instruction) triplets from SVG emoji sets; four metrics spanning raster-based, contour-based and description-based comparisons. https://arxiv.org/abs/2502.19453 ; https://github.com/mti-lab/SVGEditBenchV2
- **VGBench** (arXiv 2407.10972): VGQA 4,279 (SVG 2,228 / TikZ 1,139 / Graphviz 912) + VGen 5,845 (SVG 2,000 / TikZ 2,000 / Graphviz 1,845); generation metrics Long-CLIP score and FID; understanding accuracy (zero-shot, SVG/TikZ/Graphviz): GPT-4o 64.4/79.8/80.8; GPT-4 54.9/81.0/84.5; Llama-3-70B 53.4/71.1/69.5; **Llama-3-8B 40.0/54.5/58.8**; Qwen2-72B 53.9/78.6/79.6. https://arxiv.org/abs/2407.10972 ; https://vgbench.github.io
- **LLM4SVG** (CVPR 2025, arXiv 2412.11102): SVGX-SFT (580k instruction pairs); learnable semantic tokens; metrics FID / CLIPScore / Aesthetic / HPS (reported e.g. GPT-2-XL variant FID 64.11, CLIPScore 0.3496, Aesthetic 5.9836, HPS 0.2485 per liner review). https://arxiv.org/abs/2412.11102 ; https://ximinng.github.io/LLM4SVGProject/
- **Reason-SVG** (CVPR 2026, arXiv 2505.24499): "Drawing-with-Thought" SFT + GRPO with hybrid reward (reasoning presence, structural validity, semantic alignment, visual quality); SVGX-DwT-10k. Numbers not in abstract. https://arxiv.org/abs/2505.24499
- **SVGenius** (ACM MM 2025, arXiv 2506.03139, CC BY 4.0): 2,377 queries, 24 domains, 8 tasks (Perceptual QA, Semantic QA; Bug Fixing, Code Optimization, Style Editing; Text-to-SVG, Image-to-SVG, Style Transfer), 18 metrics (HPS, Aesthetic, SSIM, LPIPS, DINO; CLIP, rCLIP; PSS path-structure similarity, MSE, rMSE; compression ratio, repair accuracy; 5 LLM-scored style-transfer criteria). Complexity tiers by path count (easy 2.14 / medium 9.87 / hard 16.02 paths). 22 models; e.g. Text-to-SVG easy: Claude 21.35 HPS; Image-to-SVG easy: Claude 23.70 PSS, GPT-4o 23.43. Findings: universal degradation with complexity; "<7B models exhibit complete failure on challenging instances"; style transfer unsolved. https://arxiv.org/abs/2506.03139 ; https://github.com/ZJU-REAL/SVGenius-Bench
- **VCode** (Microsoft/Oxford, arXiv 2511.02778): image → SVG that preserves symbolic meaning; **CodeVQA** metric = a VLM answers the original questions using only the rendered SVG; GPT-5 46.8 vs raw-image upper bound 61.7. https://arxiv.org/abs/2511.02778
- **Vector-Bench** (arXiv 2607.19056, Jul 2026): 40 SVG surgical-edit tasks, 34 endpoints; binary spec reward with attribute-aware perceptual tolerances; Unintended Change Rate; best model 15.0 % full-spec success despite 43.7 % mean repair progress. https://arxiv.org/abs/2607.19056

---------------------------------------------------------------------------------------------------

## PART D — Analogous code→render→compare benchmarks (web, plots, TikZ, Manim)

### D1. Design2Code (NAACL 2025; arXiv 2403.03163; CC BY 4.0)
- Si, Zhang, Li, Yang, Liu, Yang. https://arxiv.org/abs/2403.03163 ; html https://arxiv.org/html/2403.03163 ; https://aclanthology.org/2025.naacl-long.199.pdf
- Screenshot → HTML/CSS. 484 real webpages from C4 validation (from 14k after automatic length/layout screening, then manual filtering); Design2Code-HARD = 80 pages (26 % >500 tags, 19 % non-English).
- **Metrics (exact)**: high-level *CLIP* = cosine of CLIP-ViT-B/32 embeddings of screenshots with detected text boxes masked; low-level, on text blocks matched by Jonker–Volgenant assignment: *Block-Match* = total size of matched blocks / total size of all blocks (penalises missing and hallucinated); *Text* = character-level Sørensen–Dice (2·overlap / total chars); *Position* = 1 − max(|xq−xp|, |yq−yp|) on normalised coords; *Color* = CIEDE2000 difference; final visual score = average of the five.
- **Numbers (Block/Text/Position/Color/CLIP)**: GPT-4o 93.0/98.2/85.5/84.1/90.4; GPT-4V 85.8/97.4/80.5/73.3/86.9; Gemini 1.0 Pro Vision 80.2/94.6/72.3/66.2/84.4; fine-tuned Design2Code-18B (CogAgent) 78.5 Block / 96.4 Text; GPT-4o on HARD Block-Match 56.6.
- **Human eval / pitfalls**: 49 % of GPT-4V outputs judged interchangeable with originals; 64 % preferred AI pages over originals; human judgment *negatively* correlated with Text similarity (people weigh layout/colour); self-revision prompting gave minimal gains; text-augmented prompting lifted LLaVA Block-Match 50.4 → 68.4.

### D2. Sketch2Code (arXiv 2410.16232, Oct 2024)
- Li, Zhang, Yang. https://arxiv.org/abs/2410.16232 ; html https://arxiv.org/html/2410.16232 ; https://salt-nlp.github.io/Sketch2Code-Project-Page/
- Hand-drawn wireframe sketch → HTML. 731 sketches over the 484 Design2Code pages, drawn by Prolific annotators with UI expertise (boxes with X = images, curly lines = text; ~18 % pages sketched by 2 designers, 16.5 % by ≥3).
- Metrics: Design2Code visual similarity + new **Layout Similarity** = weighted average of IoU over 7 component types (text, image, video, nav, form/table, button, divider); 69.2 % agreement with humans. Interactive-agent modes: feedback-following vs question-asking.
- Numbers (Layout / Text IoU / Image IoU / human satisfaction): Claude 3.5 Sonnet 21.64/22.51/10.47/36 %; GPT-4o 19.20/17.12/16.19/30 %; Gemini 1.5 Pro 18.25/16.44/14.69/22 %; Claude 3 Opus 12.86/10.43/12.67/10 %; open 8B models (InternVL2-8B, LLaVA-1.6-8B) layout similarity < 11.
- Pitfalls: feedback rounds give ≤7.1 % visual / 2.7 % layout gains; question-asking hurts after 3–4 rounds; practitioners still prefer question-asking.

### D3. Interaction2Code (ASE 2025; arXiv 2411.03292)
- Xiao et al. https://arxiv.org/abs/2411.03292 ; html https://arxiv.org/html/2411.03292 ; https://github.com/WebPAI/Interaction2Code ; https://huggingface.co/datasets/whale99/Interaction2Code
- Interactive prototype (screenshots before/after interaction) → HTML/JS. 127 webpages, 374 interactions, 15 page types, 31 interaction categories (CommonCrawl + GitHub).
- Metrics: CLIP, SSIM, Position = 1 − max(|xo−xg|, |yo−yg|), BLEU text; widget-level Widget Similarity, Widget Match Rate, Implement Rate. Ten failure types: missing element; no interaction; wrong element; wrong element type; wrong element position; wrong post-interaction position; wrong effect type; effect on wrong element; partial implementation; wrong function.
- Numbers (CLIP / SSIM / Text): Claude-3.5-Sonnet 0.7130/0.5242/0.5835; GPT-4o 0.7059/0.5259/0.5007; Qwen2.5-VL-72B 0.6573/0.4529/0.4456; Gemini-1.5-flash 0.6151/0.4761/0.4801.
- Pitfalls: interaction generation lags full-page generation; visually subtle interactions fail; single-modality descriptions insufficient.

### D4. FullFront (arXiv 2505.17399, May 2025)
- Sun, Wang, Gu, Li, Cheng. https://arxiv.org/abs/2505.17399 ; https://github.com/Mikivishy/FullFront
- Tasks: Webpage Design (50), Perception QA (1,800 MCQ), Code Generation 400 = Image-to-Code 200 + Text-to-Code 50 + Interaction Authoring 100 + Code Refinement 50. Pages are real sites rewritten to clean HTML by GPT-4o then refined by Claude 3.7 Sonnet (avoids copyright/external deps).
- Metrics: CLIP score; Gemini visual score on 10 criteria; DOM-tag-sequence structural similarity; content-type similarity; human expert baseline.
- Numbers: Claude 3.7 Sonnet / Gemini 2.5 Pro ≈ 55 % on perception vs 95 %+ humans; top code scores ≈ 0.68–0.75, open models rarely > 0.60.

### D5. WebIGBench (arXiv 2606.00154, Jun 2026)
- Wu, Dong, Gao, Chen, Huang, Xiao, Liao. https://arxiv.org/abs/2606.00154 ; https://github.com/anoa12159-hue/WebIGBench_eval
- 103 real complex interactive webpages, 5 interaction action types, 871 interactive actions; metrics: visual fidelity, code structure, interaction consistency (new automated pipeline). Model numbers not in abstract.

### D6. Image2Struct (NeurIPS 2024 D&B; arXiv 2410.22456; CC BY 4.0)
- Roberts, Lee, Wong, Yasunaga, Mai, Liang. https://arxiv.org/abs/2410.22456 ; https://github.com/stanford-crfm/image2struct/ ; results https://crfm.stanford.edu/helm/image2struct/v1.0.1/
- Image → LaTeX / HTML / LilyPond, rendered and compared to the input (round-trip). Renewable data stream (arXiv, GitHub, IMSLP). Five image metrics: pixel similarity, Inception-vector cosine, LPIPS, SSIM, earth-mover similarity; plus compilation success. 14 VLMs; best scores 0.830 (LaTeX equations) vs 0.402 (sheet music).

### D7. Plot2Code (NAACL Findings 2025; arXiv 2405.07990)
- https://arxiv.org/abs/2405.07990 ; https://aclanthology.org/2025.findings-naacl.164/
- Plot image (+ instruction) → matplotlib/plotly code. 132 matplotlib + 150 Python-plotly + 86 R-plotly = 368 plots, each with source and GPT-4-written instruction. Metrics: code pass rate; text-match ratio (fine-grained text overlap in rendered plot); GPT-4V rating 1–10 on overall visual similarity (appearance, colours, shapes, positions). GPT-4V rating correlates with humans "for datasets of a certain size".

### D8. ChartMimic (ICLR 2025; arXiv 2406.09961)
- https://arxiv.org/abs/2406.09961 ; https://github.com/ChartMimic/ChartMimic
- 4,800 human-curated (figure, instruction, code) triplets; 22 chart types (18 regular + 4 advanced), 201 subcategories; tasks Direct Mimic and Customized Mimic. Metrics: execution rate; low-level text/layout/type/color; high-level GPT-4V score. GPT-4o 82.2 Direct / 61.6 Customized; 3 proprietary + 14 open models. JanusCoder-8B/14B report 64.72 / 66.68 on Customized vs GPT-4o 59.4 (https://arxiv.org/html/2510.23538).

### D9. SWE-bench Multimodal (ICLR 2025; arXiv 2410.03859)
- Yang et al. https://arxiv.org/abs/2410.03859
- 617–619 issue-resolution instances from 17 user-facing JavaScript repos (web UI, diagramming, data visualisation, syntax highlighting, interactive mapping); each with ≥1 image in issue or tests. Metric: resolve rate via repo tests. SWE-agent 12 %, next best 6 %; 83.5 % of issues need the image. Specific repo names (e.g. three.js) not listed in the abstract page fetched.

### D10. TikZ: DaTikZ / AutomaTikZ / TikZero (ICCV 2025 highlight; arXiv 2503.11509, CC BY 4.0)
- Belouadi et al. https://arxiv.org/abs/2503.11509 ; html https://arxiv.org/html/2503.11509 ; https://github.com/potamides/DeTikZify ; AutomaTikZ https://arxiv.org/pdf/2310.00367 ; TikZilla (2026) https://arxiv.org/html/2603.03072v2
- Caption → TikZ, compiled by LaTeX. DaTikZ v3: 456,469 TikZ programs, ~170k captioned; test 1,000 captioned samples.
- Metrics: CLIPScore (caption↔image), DreamSim (perceptual image similarity vs reference), KID (distribution), cBLEU, TED (TeX edit distance), MTE (mean token efficiency ≈ compile reliability).
- Numbers (DSim↑ / KID↓ / CLIP↑ / cBLEU↑ / TED↓ / AVG↑): TikZero+ 56.30/1.83/24.18/1.99/59.01/87.04; TikZero(cos) 52.83/5.10/10.05/1.60/65.51/85.60; GPT-4o 56.46/2.84/31.79/0.33/58.51/79.02; Qwen2.5-Coder-32B 54.47/5.49/24.87/0.29/59.86/48.59. Human BWS with 13 annotators on 100 items.
- **Pitfall (important for CLIP metrics)**: CLIPScore is inflated by models that copy caption text into the drawing; ROT13-redacting text dropped score ratios from 50 % to 34–51 %, exposing "string matching" rather than visual quality.

### D11. vTikZ — "LLM Code Customization with Visual Results: A Benchmark on TikZ" (EASE 2025; arXiv 2505.04670)
- Reux, Acher, Khelladi, Barais, Quinton. https://arxiv.org/abs/2505.04670 ; https://github.com/IV2C/VTikZ ; https://huggingface.co/datasets/CharlyR/vtikz
- 100 curated edit scenarios from 45 TikZ programs (50 scientific diagrams, 50 animal drawings; 25 add / 13 remove / 62 update; 41 easy / 36 medium / 23 hard). Metrics: CompileMetric, LocationMetric (correct lines edited), SuccessCustomizationMetric (matches parameterised solution or image), CrystalBLEU similarity, LineMetric. Results (N=5, T=0.7): GPT-4o 28 % success (57.0 sim, 75.3 line); Llama-3.3-70B ≈16 %; DeepSeek-R1-Distill-70B ≈14 %. Failure taxonomy over 225 wrong variants: feature not found 67, wrong edit 65, partial 41, too many features 19, misunderstanding 20, no change 13.

### D12. Manim: ManiBench (arXiv 2603.13251), ManimBench / ManimTrainer (arXiv 2604.18364), DTVBench
- ManiBench (Oli, CC BY 4.0): 12 problems, 5 difficulty levels; Alignment Score = Σ(wᵢ·pᵢ·tᵢ)/Σwᵢ (presence × timing of required visual events); Coverage Score = weighted 4 dims (math annotation 35 %, visual mapping 30 %, numeric evidence 20 %, structural clarity 15 %); judged automatically by AST/regex heuristics, no human eval. Zero-shot render success: Claude-Sonnet-4 and Kimi-K2.5 8/12 (66.7 %), Gemini-2.5-Pro and Qwen3.5-Plus 33.3 %, Qwen3-235B 25 %, Qwen-2.5-Coder 0 %; mean coverage 0.17. Failure modes named: **syntactic hallucination** (valid Python, non-existent/deprecated Manim APIs) and **visual-logic drift** (renders but the narrative is wrong). https://arxiv.org/html/2603.13251 ; https://github.com/nabin2004/ManiBench
- ManimBench (Silva, Lotfi, Ihianle, Shahtahmassebi, Bird): 17 open sub-30B LLMs × 9 train/inference strategies; metrics Render Success Rate and Visual Similarity to reference video (similarity function not stated in abstract). Qwen3-Coder-30B + GRPO + renderer-in-the-loop-with-docs: 94 % RSR, 85.7 % VS (+3 pp VS over GPT-4.1). Code: https://github.com/SuienS/manim-trainer ; https://arxiv.org/abs/2604.18364
- DTVBench (in JanusCoder, arXiv 2510.23538): 102 tasks (52 Manim from 3Blue1Brown segments + 50 Wolfram demonstrations). https://arxiv.org/html/2510.23538v1

### D13. VisPlotBench / VisCoder2 (arXiv 2510.23642) and JanusCoder (arXiv 2510.23538)
- VisPlotBench: 888 executable tasks across 8 of 12 languages (Python, JavaScript, TypeScript, C++, R, HTML, SVG, LaTeX, Asymptote, Mermaid, LilyPond, Vega-Lite); Jupyter-kernel execution with strict timeouts; metrics Execution Pass Rate, LLM-judge Task Score, Visual Score. VisCoder2-7B 70.9 % (76.4 % with self-debug), 32B 73.1 % (82.4 %), GPT-4.1 63.4 % (82.4 %), Qwen2.5-Coder-7B 51.2 %. https://arxiv.org/html/2510.23642
- JanusCoder: JanusCode-800K (charts, web UI, SVG, Manim, Mathematica, R/MATLAB, artifacts, scientific demos); evaluated on PandasPlotBench, ArtifactsBench, DTVBench, ChartMimic, DesignBench, WebCode2M, InteractScience; no shader/three.js domain listed. https://arxiv.org/html/2510.23538 ; https://github.com/InternLM/JanusCoder

### D14. Vision2Code (arXiv 2605.11307, May 2026)
- Periasami, Wang, Dhingra. 2,169 test examples from 15 source datasets: charts, geometry, graphs, scientific imagery, documents, 3D spatial scenes; VLM rater with dataset-specific rubrics + deterministic guardrails + render-success diagnostics; "leading models perform well on regular chart- and graph-like visuals but remain weak on spatial scenes". https://arxiv.org/abs/2605.11307 ; https://image2code.github.io/vision2code/

### D15. InteractScience (arXiv 2510.09724)
- Interactive scientific-demonstration web code; hybrid eval = programmatic functional tests (simulated user actions) + visually-grounded qualitative tests with reference snapshots and VLM checklists; 30 LLMs; 5 disciplines. Size: not found in fetched snippet. https://arxiv.org/abs/2510.09724 ; https://huggingface.co/datasets/internlm/InteractScience

### D16. Other web design-to-code corpora (for scale reference only)
- WebSight v0.1 823k / v0.2 1.92M synthetic pages; Web2Code 1,179.7k instruction pairs (NeurIPS 2024 D&B, https://arxiv.org/html/2406.20098v2); WebCode2M 2.56M real pages (https://arxiv.org/pdf/2404.06369); DesignBench (https://arxiv.org/pdf/2506.06251); WebCoderBench (https://arxiv.org/html/2601.02430v1); UI2App (https://arxiv.org/pdf/2607.06306); Cookie-Bench (https://arxiv.org/pdf/2605.30000).

---------------------------------------------------------------------------------------------------

## PART E — Procedural materials / node graphs / Blender programs

### E1. VLMaterial (ICLR 2025 Spotlight; arXiv 2501.18623)
- Li, Wu, Solar-Lezama, Zheng, Shi, Bickel, Matusik. https://arxiv.org/abs/2501.18623 ; https://proceedings.iclr.cc/paper_files/paper/2025/file/5393eaf0b723278738788520ec54e4b4-Paper-Conference.pdf
- Image → Python program that builds a Blender procedural material (node graph) whose flat render matches the image. Open-source procedural-material dataset + LLM-driven program-level augmentation; VLM fine-tuning. Metric names/values not in the abstract page (search snippet mentions RL with image-similarity rewards). Code: not found on abstract page.

### E2. MultiMat (ICLR 2026 poster; arXiv 2509.22151)
- Belouadi, Boubekeur, Kaiser. Multimodal program synthesis for procedural material node graphs (DAG) from image and text; new production-quality material dataset; claims SOTA on unconditional and conditional synthesis. Numbers not in abstract. https://arxiv.org/abs/2509.22151

### E3. MatFormer (SIGGRAPH 2022) — pre-LLM transformer over 2,816 procedural graphs; https://dl.acm.org/doi/10.1145/3528223.3530173 ; "Reflecting Process Expertise in Procedural Material Generation" (2026, retrieval-augmented LLM → Blender shader nodes) https://arxiv.org/html/2607.13318v2 . "Infinite Texture": not found as a benchmark.

### E4. BlenderGym (CVPR 2025; arXiv 2504.01786)
- Gu, Huang, Je, Yang, Guibas. https://arxiv.org/abs/2504.01786 ; html https://arxiv.org/html/2504.01786
- Start Blender scene + goal render → edit Python to reproduce the goal. 245 scenes: procedural geometry 50, lighting 40, procedural material 40, blend shapes 75, object placement 40. Metrics: Photometric Loss (pixel), N-CLIP = 1 − CLIP similarity, Chamfer distance (geometry tasks). Human baseline far ahead (blend shapes: human PL 0.934×10⁻³ vs GPT-4o 9.14×10⁻³). VLM-verifier alignment with humans 0.66 (Claude 3.5 Sonnet) vs 0.79 inter-human.

### E5. SceneCraft (ICML 2024; arXiv 2403.01248) — text → Blender Python with up to ~100 assets; scene-graph planning; human assessment; https://arxiv.org/abs/2403.01248 . Related: LL3M https://arxiv.org/pdf/2508.08228 , BlenderRAG https://arxiv.org/pdf/2605.00632 , Procedural Scene Programs https://arxiv.org/pdf/2510.16147 .

### E6. Analogue in audio: "Benchmarking LLM Code Generation for Audio Programming with Visual Dataflow Languages" (arXiv 2409.00856) — LLM writes Max/MSP-style patches via metaprogramming vs raw JSON nodes; finding: metaprogramming yields more semantically correct code when well-formed. https://arxiv.org/abs/2409.00856

---------------------------------------------------------------------------------------------------

## PART F — Cross-cutting metric definitions and pitfalls (what transfers to a GLSL / three.js eval)

1. **Staged outcome labels beat scalar similarity.** ShaderMatch's ordered labels (incomplete → clone levels → compile error → semantic clone → single_color → variation) and Mage's four axes both show that "compiles" and "renders" are weak proxies: Mage found compile-pass anti-correlated with mechanism F1 (≈0.12 at 43 % runtime pass); WorldCoder-Bench found DOM/screenshot scoring uncorrelated with hidden-state correctness (τb = −0.02).
2. **Multi-timestamp rendering** is the shader-specific idea (10 fixed, non-periodic times at 512×288 in ShaderMatch; ArtifactsBench uses 3 interaction-staged screenshots; ManiBench scores temporal presence×timing). ShaderMatch's own README flags that its timestamps are unjustified.
3. **Degenerate outputs must be named explicitly**: `single_color` (ShaderMatch, 1–4 % of samples), "structurally vacuous" scenes (Mage), "visual-logic drift" and "syntactic hallucination" of non-existent APIs (ManiBench), copied caption text gaming CLIPScore (TikZero ROT13 test).
4. **Image metrics used across the field**: exact pixel equality (ShaderMatch), pixel/SSIM/LPIPS/Inception-cosine/earth-mover (Image2Struct), CLIP with text masked (Design2Code), DINO/SigLIP/VQA-Score/HPSv2 (SGP-GenBench), DreamSim/KID (TikZero), Photometric/N-CLIP/Chamfer (BlenderGym), F-score/Chamfer/IoU/normal consistency + mesh topology ratios (P3D-Bench for three.js meshes).
5. **Judge rubrics**: GPT-4V 1–10 overall similarity (Plot2Code), GPT-4V low/high-level (ChartMimic), Gemini-2.5-Pro per-task 10-item checklist (ArtifactsBench: 90.95 % pairwise human agreement; Qwen2.5-VL-72B only 71.34 %), Gemini-2.5-Flash 0–100 compositional (SGP-GenBench), Gemini 10-criteria visual score (FullFront), MLLM semantic/geometric/aesthetic + spec-QA (P3D-Bench), CodeVQA (VCode). Design2Code shows human preference anti-correlates with text-exactness metrics.
6. **Sampling**: ShaderEval results were produced at T=0.2/top_p 0.95; vTikZ at T=0.7 with N=5 (success 28 % for GPT-4o even with 5 tries); ManimBench/VisCoder2 report large gains from renderer-in-the-loop self-debug (GPT-4.1 63.4 → 82.4 % exec pass). No paper found reporting the greedy-decoding degeneration effect for GLSL specifically (the finetune/docs note in this repo is a local observation).
7. **Contamination controls seen**: gated generations (Shadereval-runs), SE(2) perturbation (SGP-Bench), parameter-randomised Robust split (WorldCoder), renewable fresh data stream (Image2Struct), retrospective difficulty assignment + MinHash/semantic dedup (ArtifactsBench), DINOv2 dedup (P3D-Bench).
8. **Open ~7–9B vs frontier gaps** (headline): ShaderMatch only covers open ≤20B code models (best error_rate ≈ 0.31; no frontier numbers exist). SGP-Bench Llama-3.1-8B 46.5 vs Claude 3.5 Sonnet 67.4 (SVG). VGBench Llama-3-8B 40.0 vs GPT-4o 64.4 (SVG QA). ArtifactsBench Qwen 7–14B 25–32 vs Gemini-2.5-Pro ≈59. Sketch2Code open 8B < 11 layout vs Claude 3.5 Sonnet 21.6. SGP-GenBench shows the gap closes with render-reward RL on Qwen-2.5-7B (0.325 → 0.596 VQA).

---------------------------------------------------------------------------------------------------

## PART G — Searched and NOT found (as of 2026-09-08)

- A dedicated **text-to-GLSL / text-to-shader benchmark** with a public test set and rendered-image metrics (searched "text-to-shader", "ShaderLLM", "ShaderGPT", "ShaderBench", "GLSL LLM evaluation 2026", "shader generation LLM benchmark 2025/2026"). Only ShaderMatch (function completion), the AA MicroEval (single prompt, human votes), and system papers (AI Co-Artist, ShadAR) exist. "ShaderGPT" is a 14islands/Slanted demo tool (https://www.slanted.de/shadergpt/), not a paper.
- Any **WGSL or HLSL LLM benchmark** (only ShadAR uses HLSL, no eval).
- A **ThreeJS-Bench** by that name. The closest are WorldCoder-Bench (three.js scenes, state contracts), P3D-Bench (three.js as one of four output formats), Web-Bench (WebGL/three.js among 50 projects), ArtifactsBench (a few three.js tasks), and the WebDev Arena "3D Graphics and Shaders" category (no per-category numbers found).
- **p5.js / Processing** LLM benchmark: none found; p5.js appears only as one "arena" in a commercial multi-arena coding leaderboard (search snippet, no primary source) and in Spellburst (HCI system). CreativeBench (arXiv 2603.11863) is about creative *code* generation generally, not creative coding.
- **Unreal Blueprint** and **OpenGL C++** LLM benchmarks: none.
- "**Infinite Texture**" as an LLM/procedural-shader benchmark: none.
- Per-model results table from the ShaderMatch *paper* PDF itself (IEEE/ResearchGate blocked); the HF Shadereval-results dataset was used instead and matches the abstract's 31 % figure.
- Gated HF cards (Shadertoys, Shadertoys-fine): train/test split counts.
- GPT-4-class or Claude-class numbers on ShaderMatch: none published.

<!-- ===== methodology.md ===== -->

# Methodology survey: code benchmarks, VLM/LLM judges, image metrics, 3D shape metrics

Compiled 2026-09-08 from primary sources (arXiv abstract/HTML pages, GitHub READMEs, project pages).
Every number below is taken from the cited URL; "not found" means the source I could read did not state it.
Rendering note: arXiv-HTML math sometimes duplicates digits when fetched (e.g. "1010" for 10, "0.20.2" for 0.2, "80×80×" for 80×). Where that happened I record the de-duplicated value and flag it.

Format per item: **name** — year/venue — URL(s) — what it measures — exact definition — human-correlation / bias numbers — pitfall — transfer to a 3D-code benchmark.

---

## A. Code benchmarks (construction, metric definitions, noise reporting)

### A1. HumanEval / Codex (Chen et al. 2021)
- URLs: https://arxiv.org/abs/2107.03374 ; full text https://ar5iv.labs.arxiv.org/html/2107.03374
- Measures: functional correctness of Python functions from docstrings, 164 problems, "an average of 7.7 tests per problem".
- Hand-written to avoid contamination: "It is important for these tasks to be hand-written, since our models are trained on a large fraction of GitHub, which already contains solutions to problems from a variety of sources."
- Unbiased pass@k estimator: generate n >= k samples per problem, c = number correct; pass@k = E_problems[ 1 - C(n-c, k) / C(n, k) ]. "One may be tempted to estimate pass@k with 1-(1-p̂)^k where p̂ is the empirical estimate of pass@1, but we show that it is biased in Appendix A." Numerically stable implementation: product form 1 - prod_{i=n-c+1}^{n} (1 - k/i) (np.prod in the paper's code listing).
- Sampling: n = 200 samples per problem. Temperature: "For a 679M parameter model, the optimal temperature for pass@1 is T*=0.2 and the optimal temperature for pass@100 is T*=0.8."
- Metric caveat (match-based metrics): "BLEU score may not be a reliable indicator of functional correctness by showing that functionally inequivalent programs generated by our model (which are guaranteed to disagree with the reference solution on some input) often have higher BLEU scores than functionally equivalent ones."
- Transfer to 3D-code: use the unbiased estimator with n>k samples per prompt; sample at T≈0.2 for pass@1 and higher T for pass@k; hand-author prompts (do not scrape existing OpenSCAD/Blender tutorials verbatim); do not use text similarity to reference code as the score.

### A2. HumanEval+ / EvalPlus (Liu et al. NeurIPS 2023)
- URLs: https://arxiv.org/abs/2305.01210 ; full text https://arxiv.org/html/2305.01210
- Measures: same tasks, 80× more tests: "EvalPlus extends the test-cases of the popular HumanEval benchmark by 80× to build HumanEval+" (HumanEval+ has "764.1 tests" per task on average; HumanEval+-mini distils by 47× to "16.1 tests for each task").
- Pipeline: "EvalPlus first uses ChatGPT to generate a set of high-quality seed inputs ... we perform type-aware mutation to quickly generate numerous new inputs ... We use differential testing as the oracle to cross-check the output."
- Ground truth found wrong: "18 defects (11% of problems) even in the original ground-truth in HumanEval" — "Unhandled edge-case: five prior ground-truths fail to handle corner-case inputs ... Bad logic: 10 prior ground-truths incorrectly implement the desired functionality; and Performance issue: three inefficient implementations."
- Effect on scores: "up-to 19.3% (pass@1) / 24.9% (pass@10) / 28.9% (pass@100) reduction over the evaluated models" (HTML rendered as pass@11/1010/100100). Ranking flips: "WizardCoder-CodeLlama and Phind-CodeLlama ... can actually outperform the proprietary ChatGPT" on HumanEval+ but not on HumanEval.
- Pitfall: thin test suites (7.7 tests) let wrong code pass and even the reference solutions were wrong 11% of the time.
- Transfer: reference 3D solutions need adversarial checking (re-render/re-mesh at many parameter values, differential testing across two implementations); expect ~10% of hand-written GT to be defective and budget a verification pass; report how many GT were fixed.

### A3. MBPP (Austin et al. 2021)
- URL: https://ar5iv.labs.arxiv.org/html/2108.07732
- "The Mostly Basic Programming Problems dataset contains 974 short Python programs constructed by crowd-sourcing to an internal pool of crowdworkers who have basic knowledge of Python." Each item: "a short problem statement, a single self-contained Python function solving the problem specified, and three test cases".
- Hand-verified subset: "we manually inspected, edited, and pruned a subset of the questions, yielding 426 hand-verified questions, which we refer to as the edited dataset."
- Sampling: "we use temperature sampling (with temperature 0.5) to generate 80 samples of code and then execute the code contained in the samples against tests". Greedy vs sampling: "lower temperatures (more greedy decoding) perform better with only a single evaluation allowed, but higher temperature, less greedy strategies begin to solve more tasks within a budget of 10 samples."
- Pitfall: crowd-written prompts were "somewhat ambiguous"; the sanitized subset exists because of that.
- Transfer: keep a "sanitized" hand-edited subset; three tests per task is too few (see A2).

### A4. LiveCodeBench (Jain et al. 2024)
- URLs: https://arxiv.org/abs/2403.07974 ; full text https://arxiv.org/html/2403.07974
- "we have collected 511 problems from contests across three competition platforms – LeetCode, AtCoder, and CodeForces occurring from May 2023 to the present (May 2024)"; 182 Easy / 206 Medium / 123 Hard using "problem difficulty ratings (sourced from the competition websites)".
- Contamination control by release-date window: "we only consider problems released after the model's cutoff date to ensure that the model has not encountered the exact problem in the training dataset"; evidence: "DeepSeek-Instruct and GPT-4-O perform considerably worse on problems released since September and November 2023 (their release and cutoff dates respectively!)".
- Four scenarios: code generation, self-repair, code execution, test output prediction.
- Tests: LLM-generated: "we use a LLM (here GPT-4-Turbo) to generate tests for the problems", about 17 tests per problem on average (HTML rendered "1717").
- pass@1 protocol: "we generate 10 candidate answers for each problem ... We use nucleus sampling with temperature 0.2 and top_p 0.95" (HTML rendered "1010", "0.20.2", "0.950.95").
- Overfitting finding: "models cluster into two groups, ones that perform well on both benchmarks and others that perform well on HumanEval but not on LiveCodeBench".
- Transfer: timestamp every 3D task and report scores per release window; n=10 at T=0.2 is a cheap pass@1 estimator; a self-repair scenario (model sees render/compile error) is directly applicable to 3D code.

### A5. LiveCodeBench Pro (2025)
- URLs: https://arxiv.org/abs/2506.11928 ; full text https://arxiv.org/html/2506.11928
- 584 problems "drawn from top-tier contests including Codeforces, ICPC series, and IOI series"; tiers by Elo: Easy "≤2000", Medium "(2000,3000]", Hard ">3000".
- Olympiad medalists annotate categories (knowledge-heavy / logic-heavy / observation-heavy) and do "a line-by-line analysis of failed model-generated submissions".
- Result: "without external tools, the best model achieves only 53% pass@1 on medium-difficulty problems and 0% on hard problems"; "o3-mini commits 34 more algorithm logic errors than human contestants".
- Transfer: expert-annotated failure taxonomy (per-failure line-by-line labelling) is the model for a 3D "defect checklist"; difficulty tiers defined by an external, model-independent rating.

### A6. SWE-bench (Jimenez et al. ICLR 2024)
- URLs: https://arxiv.org/abs/2310.06770 ; full text https://arxiv.org/html/2310.06770 ; https://www.swebench.com
- "2,294 software engineering problems drawn from real GitHub issues and corresponding pull requests across 12 popular Python repositories"; pipeline from ~90,000 PRs → 11,407 post-conversion → 2,294 post-validation (Table 10).
- FAIL_TO_PASS: "We filter out task instances without at least one test where its status changes from a fail to pass (henceforth referred to as fail-to-pass test)". PASS_TO_PASS: tests that must keep passing so "prior functionality is properly maintained".
- Resolution rule: "If the patch applies successfully and all of these tests pass we consider the proposed solution to have successfully resolved the issue." Metrics: % Resolved, % Apply.
- Environment (original paper): conda envs — "Create executable contexts as conda envs. based on latest task instance per version." (Docker came with the Verified harness, A7.)
- Task ambiguity: the paper does not analyse it (that came from A7).
- Transfer: FAIL_TO_PASS/PASS_TO_PASS maps to "the new geometry check must go from failing to passing, and the existing scene checks must still pass" for edit-style 3D tasks; containerise Blender/OpenSCAD versions.

### A7. SWE-bench Verified (OpenAI, Aug 2024)
- URLs: https://openai.com/index/introducing-swe-bench-verified/ (403 to fetch; text mirrored at https://github.com/irthomasthomas/undecidability/issues/933) ; https://www.swebench.com/verified.html
- "93 software developers experienced in Python" annotated "1,699 random samples from the SWE-bench test set"; each sample reviewed by three annotators; severity labels "[0, 1, 2, 3]"; "Labels 0 and 1 are minor; labels 2 and 3 are severe"; samples with ensemble severity ≥2 on either the problem statement or the FAIL_TO_PASS tests were removed.
- Fractions filtered: "38.3% of samples were flagged for underspecified problem statements"; "61.1% were flagged for unit tests that may unfairly mark valid solutions as incorrect"; "68.3% of SWE-bench samples being filtered out" overall; 500 kept.
- Effect: GPT-4o (Agentless) went from 16% to 33.2%; harness moved to "containerized Docker environments".
- Later note: OpenAI stopped reporting it ( https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/ ; details not fetched).
- Transfer: budget a 3-annotator, 0–3 severity pass over every 3D task for (a) underspecified prompt and (b) over-specific checks; expect to discard a large fraction; report the discard rate.

### A8. SWE-bench Multimodal (Yang et al. 2024)
- URLs: https://arxiv.org/abs/2410.03859 ; full text https://arxiv.org/html/2410.03859
- "617 task instances collected from 17 JavaScript libraries used for web interface design, diagramming, data visualization, syntax highlighting, and interactive mapping"; "Each SWE-bench M task instance contains at least one image in its problem statement or unit tests." Dev 102 / test 517.
- Validation: consistency testing (10 runs per task), and "the authors manually inspect each task instance" for image necessity; environments with "Node.js and Chrome, to support JavaScript execution, visual testing, and webpage rendering in-browser"; "Designing environments ... took an average of ten hours of manual labor per repository".
- Visual tests: "pixel-level visual testing" via screenshot comparison (69 instances), actual/expected image pairs (67 instances from carbon), plus functional unit tests; Puppeteer/Pixelmatch are "commonplace".
- Result: best 12.2% (SWE-agent M + GPT-4o).
- Transfer: closest analogue to render-based grading; run each task's checks N times to confirm determinism before admitting it; pixel-diff tests need tolerances.

### A9. BigCodeBench (Zhuo et al. 2024)
- URLs: https://arxiv.org/abs/2406.15877 ; full text https://arxiv.org/html/2406.15877 ; https://github.com/bigcode-project/bigcodebench ; Hard subset https://huggingface.co/blog/terryyz/bigcodebench-hard
- "1,140 fine-grained tasks" covering "723 function calls from 139 popular libraries across 7 domains"; "each task encompasses 5.6 test cases with an average branch coverage of 99%".
- Authoring: "Data Synthesis, Semi-automatic Program Refactoring and Testing Case Generation, and Human Curation"; "20 authors as annotators for one year in total, with 75% of them having more than 5 years of experience".
- Splits: Complete = "code generation based on the structured docstrings"; Instruct = "natural-language-oriented instructions".
- Metric: "we report Pass@1 with greedy decoding for the main experiments in the zero-shot setting". Calibrated Pass@1: "models constantly omit the essential code in the generation and hence fail the tests, we calibrate the generation quality by adding the missing setup" (i.e. re-insert the missing imports/setup before executing).
- Human performance: "97% (32 out of 33) of sampled tasks can pass all test cases" (11 annotators writing solutions).
- BigCodeBench-Hard: 148 tasks; selection: Stack Overflow query embedding similarity ("a similarity score above 0.7 is a good threshold"), 626 after dedup, then keep tasks that "require more than two libraries", solution length > 426 tokens, and "solve rate below 50%".
- Pitfall: README: "batch inference results could vary from batch sizes to batch sizes ... if you want to get more deterministic results for greedy decoding, please set --bs to 1".
- Transfer: LLM-drafted + human-refined tasks with branch-coverage targets; a "calibrated" variant that forgives boilerplate omissions (imports, `import bpy`) isolates modelling ability from prompt-format failures; a Hard subset chosen by real-user-query similarity and solve rate.

### A10. Design2Code (Si et al. 2024)
- URLs: https://arxiv.org/abs/2403.03163 ; full text https://arxiv.org/html/2403.03163
- 484 real webpages from C4 validation (127.9k → 14k after auto-filtering → 484 after manual curation "to check the independence from external files, the absence of sensitive content, and proper formatting").
- Metrics: high-level Visual Similarity = "similarity of their CLIP embedding" with "CLIP-ViT-B/32 after resizing screenshots to squares"; low-level: Block-Match = "total sizes of all matched blocks divided by the total sizes of all blocks ... including unmatched ones"; Text = "character-level Sørensen-Dice similarity"; Position = "1−max(abs(xq−xp),abs(yq−yp))" on normalised coordinates; Color = "CIEDE2000 color difference formula".
- Human eval: pairwise, "5 human annotators" per pair with majority vote (≥3); "49% of the AI-generated webpages are considered exchangeable with the reference webpages" and "webpages generated by GPT-4V are preferred in 64% cases".
- Design2Code-HARD: "80 hard examples" (26% >500 HTML tags, 19% non-English).
- Auto-vs-human: logistic regression on the automatic metrics predicts human pairwise preference with "79.9% accuracy"; Block-Match and Position most positively associated; Text similarity had the "negative and least significant association with human judgment"; simulated vs annotated win rates r = 0.975 (Pearson), τ = 0.931 (Kendall).
- Transfer: decompose the score into a global embedding similarity plus low-level element matches (part count/position/colour) and validate the decomposition against pairwise human votes; text/code similarity is a poor proxy.

### A11. Web-Bench (2025)
- URL: https://arxiv.org/html/2505.07473
- "50 projects, each consisting of 20 tasks with sequential dependencies", "designed by engineers with 5 to 10 years of experience"; "Test: end-to-end (E2E) test with Playwright", 3.6 E2E cases per task, 72.4 per project.
- Metric: pass_tasks@1 = "number of tasks passed on first attempt before any failure" (sequential; a task that fails twice ends the project's evaluation). "SOTA (Claude 3.7 Sonnet) achieves only 25.1% Pass@1".
- Transfer: multi-step scene-building tasks with dependency chains; report the first-failure index.

### A12. WebGen-Bench (2025)
- URL: https://arxiv.org/html/2505.03733
- "101 instructions", "647 test cases (4–11 per instruction)", test cases reviewed by "Two computer science Ph.D. students".
- Grader: a UI agent (WebVoyager) executes the test and returns YES/NO/PARTIAL; alignment with manual judgement "Alignment Rate = N_Manual=Agent / N_total × 100%": Claude-3.5-Sonnet 90.3%, DeepSeek-R1 86.1%, DeepSeek-V3 94.4%.
- Transfer: agentic graders (a VLM that interacts with the scene/viewport) need an explicit alignment-rate audit vs manual grading.

### A13. ArtifactsBench (2025)
- URLs: https://arxiv.org/abs/2507.04952 ; full text https://arxiv.org/html/2507.04952
- "1,825 diverse tasks" across nine domains (413 games, 123 SVG, 441 web, 75 simulation, 122 data science, 314 management, 118 multimedia, 179 tools, 40 other); difficulty split 559/611/655.
- Pipeline: "We standardize execution with headless Chromium (Playwright) at 1024×768 resolution and deterministic seeds. We capture three staged screenshots (before/during/after scripted interaction)"; judge gets "temporal evidence, the original task, the model's full answer, and a fine-grained checklist"; "10-dimension checklist ... five vision-oriented and five code-oriented dimensions", 10-point scale each, absolute (not pairwise). Judges: Gemini-2.5-Pro and Qwen2.5-VL-72B.
- Human agreement: "94.4% normalized Footrule consistency" with WebDev Arena ranking ("versus 69.4% for WebBench"); expert pairwise agreement on a 280-instance study: Gemini-2.5-pro 90.95% ("For a given query with m model responses, we can form m(m−1)/2 unique pairs ... count the number of pairs for which the MLLM referee and the human judges agree on the rank ordering"); "Incorporating execution screenshots markedly improves agreement". Checklist calibration "achieves Cohen's κ≥0.8 among annotators".
- Contamination: "MinHash + semantic similarity" and "screenshot perceptual hashing".
- Pitfall: "For highly complex, long-horizon, or state-dependent interactions ... discrete sampling may not fully capture the fluidity, correctness, and robustness of the entire interactive experience."
- Transfer: the closest published template for a render-then-VLM-judge 3D benchmark: fixed renderer settings + deterministic seeds, per-task checklist, absolute 10-point sub-scores, agreement reported both as rank-consistency with an arena and pairwise agreement with experts.

### A14. Aider polyglot (Dec 2024)
- URL: https://aider.chat/docs/leaderboards/
- "225 challenging Exercism coding exercises across C++, Go, Java, JavaScript, Python, and Rust"; two attempts (second after test feedback); leaderboard reports pass rate and "Percent cases well formed" (edit-format compliance).
- Transfer: separate "correct edit format" from "correct result" — for 3D code, separate parse/compile from geometric correctness.

### A15. MultiPL-E (Cassano et al. 2022)
- URL: https://arxiv.org/abs/2208.08227
- "a system for translating unit test-driven code generation benchmarks to new languages" — HumanEval/MBPP prompts+tests compiled into "18 additional programming languages"; pass@k; "Codex matches or even exceeds its performance on Python for several other languages".
- Transfer: port one task spec into OpenSCAD / Blender-Python / three.js / GLSL dialects with the same geometric oracle.

### A16. Noise, variance, contamination
- **Adding Error Bars to Evals** (Miller 2024) — https://arxiv.org/html/2411.00640 — recommendations: "Computing standard errors of the mean using the Central Limit Theorem"; "When questions are drawn in related groups, computing clustered standard errors"; "Reducing variance by resampling answers and by analyzing next-token probabilities"; "When two models are being compared, conducting statistical inference on the question-level paired differences"; "Using power analysis". Resampling: "Var(sᵢ) = σᵢ²/K" with K resamples per question. Power: to detect a 3% difference at 80% power "the eval will need to contain at least n ≈ 969 independent questions". Reporting: "reporting the standard error of the mean alongside (beneath) the mean".
- **A Sober Look at Progress in LM Reasoning** (2025) — https://arxiv.org/html/2504.07086 — "Pass@1 values show surprisingly high standard deviation—ranging from 5 to 15 percentage points across seeds" (AIME'24); "A change in just one question shifts Pass@1 by 2.5–3.3 percentage points"; "Bootstrapping over 30 runs substantially stabilizes Pass@1 estimates and should be considered a minimal standard"; temperature changes cause "large variations in performance (upto 15%)", top_p "upto 8%"; hardware/cluster differences "up to 8% for OpenRS-1.5B and 6% for DeepSeek-R1-Distill-7B on AIME'24"; "Report the mean and standard deviation".
- **Non-Determinism of "Deterministic" LLM Settings** (2024) — https://arxiv.org/abs/2408.04667 — "accuracy variations up to 15% across naturally occurring runs" and "a gap of best possible performance to worst possible performance up to 70%" over eight tasks × 10 runs at supposedly deterministic settings.
- **GSM1k** (Zhang et al. 2024) — https://arxiv.org/abs/2405.00332 — "GSM1k is designed to mirror the style and complexity of the established GSM8k benchmark"; "accuracy drops of up to 8%, with several families of models showing evidence of systematic overfitting"; "a positive relationship (Spearman's r^2 = 0.36) between a model's probability of generating an example from GSM8k and its performance gap".
- **Evaluating LLMs is a minefield** (Narayanan & Kapoor, Oct 2023) — https://www.cs.princeton.edu/~arvindn/talks/evaluating_llms_minefield/ — three problems: prompt sensitivity ("Are you measuring something intrinsic to the model or is it an artifact of your prompt?"), construct validity, contamination (GPT-4 "perfect results on a coding benchmark before September 5, 2021 and zero afterwards").
- **Greedy vs sampling / degeneration in code**:
  - Hot or Cold? AdapT (2023) — https://arxiv.org/abs/2309.02772 — "code tokens can be divided into two categories: challenging tokens that are difficult to predict and confident tokens that can be easily inferred"; adaptive temperature per token beats fixed-temperature decoding (numbers not in abstract).
  - Code Copycat Conundrum (2025) — https://arxiv.org/html/2504.12608v1 — "10,399 code snippets containing repetition, among which 9,346 cases (89.9%) exceeded the predefined maximum token limit"; 20 repetition patterns at character/statement/block level; "Top-k Sampling with k=10 achieved rep metric improvements of 72.3%" vs greedy; their DeRep gives "208.3% over greedy search" Pass@1 improvement on their repetition set.
  - Rethinking Repetition Problems of LLMs in Code Generation (2025) — https://arxiv.org/abs/2505.10402 — structural repetition "possesses a fixed structure, which can be inherently reflected in grammar"; grammar-based RPG decays likelihood of repetition-inducing tokens.
- Transfer: report mean ± SE over ≥10 seeds (≥30 for small sets) and question-level paired differences; clustered SE if several prompts share a scene/object; watch for greedy-decoding repetition loops that hit the token cap (boilerplate-heavy dialects) and treat "hit max tokens" as a distinct failure class; a contamination probe = re-authored twin prompts (GSM1k style).

---

## B. VLM-as-judge and image-generation evaluation protocols

### B1. GenEval (Ghosh et al. NeurIPS 2023)
- URL: https://arxiv.org/html/2310.11513
- Six tasks: single object, two object, counting, colors, position, attribute binding; "553 prompts" from templates over "80 MS COCO class names", "11 basic color terms", counts "2, 3, or 4", positions "above, below, to the left of, or to the right of".
- Pipeline: "Mask2Former trained on MS COCO" (Swin-S); "For all tasks except for counting, we take objects with a confidence above the default threshold of 0.3. For counting, we find that a higher threshold of 0.9 gives the highest human agreement"; colours by "CLIP ViT-L/14" zero-shot on the cropped, background-masked object.
- Human study: "6,000 annotations on a total of 1,200 images", "5 annotations for each image"; "GenEval obtains 83% agreement with human annotators, where pairwise interannotator agreement is 88%"; on unanimous cases "GenEval obtains 91% overall agreement ... while CLIPScore obtains 87%".
- Failure modes: "Holes in the object which are incorporated into the segmentation mask can mislead downstream color classification"; "Images with overlapping objects of the same type are difficult for object detectors"; "Simpler artistic renderings are out-of-distribution for the detector".
- GenEval 2 (Dec 2025) — https://arxiv.org/html/2512.16853v1 — benchmark drift: "GenEval is now saturated, with Gemini 2.5 Flash Image achieving a score of 96.7%"; detector breaks as "the T2I output distribution shifted farther from COCO"; deviation from human scores grew to "17.7%"; GenEval 2: 800 prompts, Soft-TIFA scorer ("generates one question per atom of each prompt ... assigns soft scores"), "AUROC of 94.5%" vs VQAScore 92.4% / TIFA 91.6%; "85.1% of the data points were labeled unanimously by the 3 annotators".
- Transfer: detector-based checks on renders (object present, count, colour, relative position) are cheap and human-aligned at release but drift; thresholds per sub-task must be tuned against a human set and re-validated over time.

### B2. T2I-CompBench / T2I-CompBench++ (Huang et al. 2023 / TPAMI 2025)
- URLs: https://arxiv.org/html/2307.06350 ; v3 (++) https://arxiv.org/html/2307.06350v3 ; https://github.com/Karine-Huang/T2I-CompBench
- ++: 8,000 prompts, four categories (attribute binding: color/shape/texture; object relationships: 2D/3D-spatial, non-spatial; generative numeracy; complex).
- Metrics: "Disentangled BLIP-VQA" for attributes; UniDet-based spatial rule: first object is left of the second "if x₁<x₂, |x₁-x₂|>|y₁-y₂|" and IoU < 0.1; CLIPScore for non-spatial; 3-in-1 = average of CLIPScore, BLIP-VQA, UniDet for complex; MLLM: MiniGPT4-CoT, GPT-4V, ShareGPT4V.
- Human correlation (Kendall τ / Spearman ρ): CLIP — color 0.1938/0.2773, shape 0.0555/0.0821, texture 0.2890/0.4008, 2D-spatial 0.2741/0.3548; BLIP-VQA — color 0.6297/0.7958, shape 0.2707/0.3795, texture 0.5177/0.6995; UniDet 2D-spatial 0.4756/0.5136; MiniGPT4-CoT — color 0.3156/0.4151, shape 0.1300/0.1805, texture 0.3453/0.4664, 2D-spatial 0.1096/0.1239; GPT-4V — color 0.5242/0.6465, shape 0.2668/0.3402, texture 0.3944/0.4987, 2D-spatial 0.3456/0.4038, 3D-spatial 0.2560/0.3202, numeracy 0.3777/0.4651, non-spatial 0.4756/0.5337, complex 0.5070/0.5942.
- Pitfall: "Share-CoT tends to give less diverse ratings, regardless of the prompt provided. GPT-4V can comprehend the evaluation prompts, but it is less able to convert into exact grades."
- Transfer: specialised per-category checkers (VQA for attributes, detector for spatial) beat both CLIP and a general MLLM; MLLMs are weakest at converting judgement into calibrated numeric grades — prefer binary/checklist questions.

### B3. LLM-as-a-judge / MT-Bench (Zheng et al. NeurIPS 2023)
- URL: https://arxiv.org/html/2306.05685
- Position bias (Table 2, fraction consistent when answer order swapped): GPT-4 "65.0%" default / "66.2%" rename; Claude-v1 "23.8%" / "56.2%"; GPT-3.5 "46.2%" / "51.2%"; "Only GPT-4 outputs consistent results in more than 60% of cases." Mitigation: swap positions and only count a win if consistent, otherwise tie.
- Verbosity bias ("repetitive list" attack): fooled Claude-v1 and GPT-3.5 in "91.3%" of cases, GPT-4 only "8.7%".
- Self-enhancement: "GPT-4 favors itself with a 10% higher win rate; Claude-v1 favors itself with a 25% higher win rate ... GPT-3.5 does not favor itself" — "our study cannot determine whether the models exhibit a self-enhancement bias".
- Limited reasoning: math-grading failures "14/20" default, "6/20" CoT, "3/20" reference-guided; recommendation "a reference-guided method, in which we first generate LLM judge's answer independently, and then display it as a reference answer in the judge prompt".
- Agreement: "The agreement under setup S2 (w/o tie) between GPT-4 and humans reaches 85%, which is even higher than the agreement among humans (81%)."
- Transfer: always evaluate both orderings; give the judge a reference (GT render) rather than asking it to reason from scratch; report agreement with and without ties.

### B4. MLLM-as-a-Judge (Chen et al. ICML 2024)
- URL: https://arxiv.org/html/2402.04788
- "4,414 image-instruction pairs" from "10 diverse domains"; three settings: Scoring (1–5), Pair Comparison (with tie), Batch Ranking.
- GPT-4V agreement: scoring Pearson 0.490 (70% human agreement); pair comparison 0.636 accuracy (79.3% agreement); batch ranking Levenshtein 0.361 (62.1%). Gemini: 0.304 / 0.509 / 0.432.
- Biases: egocentric ("GPT-4V exhibits a slight degree of Egocentricity"); position ("LLaVA replicates this sequence in 88.2% of responses"); length ("GPT-4V and Gemini showing average gains of 0.6 and 0.75 points, respectively" when answers were expanded); hallucination worse in batch ranking than pair comparison.
- Transfer: pairwise > absolute scoring > batch ranking for VLM judges; expect ~0.6 scoring-correlation ceilings; verbosity of model output (long code comments) can inflate judge scores if the judge sees the code.

### B5. Judging the Judges (Thakur et al. 2024)
- URL: https://arxiv.org/html/2406.12624
- TriviaQA, "a random sample of 400 questions"; humans: "Scott's π of 96.2±1.07", "average percentage agreement was 98.52%±0.42%". GPT-4 Turbo and Llama-3 70B ≈ 88 Scott's π; lexical "contains" baseline ≈ 64 π yet rank-correlation "ρ of 0.99"; "Most judge models have rank correlations above 0.7".
- Pitfalls: "high percent agreement can still give scores that differ 10-20 points from the human-assigned scores"; leniency ("evaluate responses as 'correct' when their evaluation criteria are not completely aligned", P+ "significantly higher than 0.5"); prompt sensitivity ("other models lose alignment with increased instructions").
- Transfer: report Cohen's κ / Scott's π, not raw agreement; a cheap deterministic check can rank models as well as a judge even when its per-item agreement is poor.

### B6. GPT-4V(ision) as a Generalist Evaluator (Zhang et al. 2023)
- URL: https://arxiv.org/html/2311.01361
- Correlations with humans: captioning Pearson 0.401/Spearman 0.499 (hard-negative 0.687/0.612); text-to-image relevance 0.653/0.577, visual clarity 0.318/0.194, object accuracy 0.587/0.584; image editing Spearman 0.4450 (semantic) and 0.4090 (realism) vs "CLIPScore gets Spearman's correlation of 0.0437 for Semantic Consistency"; multi-image-to-text 0.826/0.794.
- Position bias: consistency "92%" image-to-text vs "51.5%" text-to-image (Table 7). Pairwise agreement with humans on text-to-image "41.43%" (random 33%).
- Pitfall: "GPT-4V attributed higher scores for visual clarity compared to those determined by human evaluators"; weaker on generated images than on captions.
- Transfer: expect VLM judges to be far better at "does the render match the text" than at "is the render high quality"; position consistency on images can be near chance — swap.

### B7. VIEScore (Ku et al. 2023) and ImagenHub (Ku et al. 2023)
- URLs: https://arxiv.org/html/2312.14867 ; https://arxiv.org/abs/2310.01596
- VIEScore: Semantic Consistency (SC) and Perceptual Quality (PQ) sub-scores 0–10; overall O = [min(α1..αi)·min(β1..βi)]^(1/2) using minimums "to emphasize the importance of meeting all criteria without exception".
- Spearman with humans over 7 tasks: human-human 0.45; GPT-4o 0.40; GPT-4V 0.33; LLaVA 0.09. "VIEScore (with open-source MLLM) is significantly weaker"; MLLMs "fail to detect minor changes made in image editing, such as small patch edits".
- ImagenHub: 7 tasks, SC and PQ human ratings; "inter-worker agreement of Krippendorff's alpha on 76% models with a value higher than 0.4"; "None of the existing automatic metrics has a Spearman's correlation higher than 0.2 except subject-driven image generation".
- Transfer: geometric-mean-of-minimums aggregation penalises any single failed criterion; report human-human correlation as the ceiling; small local defects are invisible to MLLMs.

### B8. GenAI-Bench / VQAScore (Lin et al. 2024) and GenAI-Arena (Jiang et al. 2024)
- URLs: https://arxiv.org/html/2406.13743 ; https://arxiv.org/html/2406.04485
- VQAScore = "the probability of a 'Yes' answer to a simple question like 'Does this figure show {text}?'"; GenAI-Bench "1,600 prompts sourced from professional designers"; 3 raters, 1–5 Likert, Krippendorff α 0.72 (images).
- Pairwise accuracy on GenAI-Bench (image): VQAScore 64.1, CLIPScore 50.8, PickScore 57.1, HPSv2 49.6, ImageReward 56.6; Pearson/Kendall: VQAScore 49.9/39.8, CLIPScore 16.4/11.8, PickScore 35.4/25.0, ImageReward 35.0/24.0, HPSv2 13.9/9.6.
- GenAI-Arena: anonymous side-by-side, four options, Bradley–Terry; "over 9000 votes" (6300 T2I, 1154 edit, 2024 video); MLLM judges vs human votes: "the best model GPT-4o only achieves an average accuracy of 49.19" (45.59 / 53.54 / 48.46); Gemini-1.5-Pro 48.94; open-source 0–37.81. "93.07%" of sampled votes judged reasonable.
- Transfer: a yes/no VQA probability is a stronger alignment metric than CLIP or preference models; MLLM pairwise preference on generated visuals is near chance for aesthetics — restrict the judge to verifiable questions.

### B9. DreamBench++ (Peng et al. 2024)
- URL: https://arxiv.org/html/2406.16855
- "150 images and 1,350 prompts"; GPT-4o rates concept preservation and prompt following on "integers ranging from 0 (very poor) to 4 (excellent)" with task-specific guidelines and a two-step "Internal Thinking" prompt; "7 human annotators", "each instance is rated by at least two humans".
- Alignment: "DreamBench++ achieves 79.64% and 93.18% agreement with human's evaluation in concept preservation and prompt following ... +32.59% and +37.23% higher than traditional DINO and CLIP metrics" (Table 4 uses mean Pearson).
- Transfer: a rubric-anchored 0–4 integer scale with explicit "what to look at" (shape, colour, texture) is more human-aligned than embedding similarity for identity preservation.

### B10. Preference/quality scorers: HPS v2, PickScore, ImageReward
- HPS v2 (Wu et al. 2023) — https://arxiv.org/html/2306.09341 — "HPD v2 comprises 798,090 human preference choices on 433,760 pairs of images"; 57 contractors; test groups annotated by 10 annotators; prompts rewritten by ChatGPT to remove style words; accuracy on HPD v2 test: CLIP ViT-H/14 65.1, Aesthetic 76.8, ImageReward 74.0, HPS v1 77.6, PickScore 79.8, HPS v2 83.3; single-human vs averaged-human 85.0.
- PickScore (Kirstain et al. 2023) — https://arxiv.org/html/2305.01569 — "over 500,000 examples and 35,000 distinct prompts"; preference accuracy: Random 56.8, Human Expert 68.0, Aesthetics 56.8, CLIP-H 60.8, ImageReward 61.1, HPS 66.7, PickScore 70.5; model-ranking correlation with real users PickScore 0.790 vs CLIP-H 0.313, ImageReward 0.492, HPS 0.670; "FID (-0.900) ... surprisingly, exhibits a strong negative correlation".
- ImageReward (Xu et al. 2023) — https://arxiv.org/html/2304.05977 — "8,878 prompts, resulting in 136,892 compared pairs"; dimensions alignment/fidelity/harmlessness, 7-level ratings; annotator-vs-ensemble agreement 53.9%±5.8%, researcher-vs-ensemble 73.4%±6.2%; preference accuracy ImageReward 65.14%, BLIP 57.76%, Aesthetic 57.35%, CLIP 54.82%.
- Transfer: these are trained on photoreal/artistic T2I distributions; on renders of generated 3D they are out-of-distribution (see T3Bench/MATE-3D numbers); FID can anti-correlate with humans.

### B11. CLIPScore, DINO, LPIPS, SSIM
- CLIPScore (Hessel et al. 2021) — https://ar5iv.labs.arxiv.org/html/2104.08718 — "CLIP-S(c,v)=w∗max(cos(c,v),0)" with "w=2.5"; RefCLIP-S = harmonic mean of CLIP-S and max reference cosine; Kendall τ Flickr8K-Expert: CLIP-S 51.2, RefCLIP-S 53.0, CIDEr 43.9, SPICE 44.9, BLEU-4 30.8; Flickr8K-CF τb: 34.4 / 36.4 / 24.6 / 24.4 / 16.9. Stated weakness on captions needing "richer contextual knowledge". Insensitivity to composition is documented by others: T2I-CompBench shape τ 0.0555 (B2), GenAI-Bench pairwise 50.8 ≈ chance (B8).
- DINO / CLIP-I / CLIP-T (DreamBooth, Ruiz et al. 2022) — https://ar5iv.labs.arxiv.org/html/2208.12242 — DINO = "average pairwise cosine similarity between the ViT-S/16 DINO embeddings of generated and real images"; CLIP-I same with CLIP; CLIP-T = "average cosine similarity between prompt and image CLIP embeddings"; DINO preferred because its self-supervised training distinguishes instances, whereas CLIP is trained on text and ignores details absent from captions.
- LPIPS (Zhang et al. 2018) — https://ar5iv.labs.arxiv.org/html/1801.03924 — unit-normalise deep features per channel, weight channels by w^l, L2, spatial average, sum over layers; BAPPS 2AFC agreement (all distortions): human 73.9%, PSNR 63.2%, SSIM 63.1%, FSIM 63.8%, LPIPS lin 69.2–70.0%, tune 69.6–69.8%, scratch 70.0–70.2%. Caveat: SSIM "was not designed for situations where geometric distortion is a large factor".
- SSIM (Wang et al. 2004) — https://en.wikipedia.org/wiki/Structural_similarity_index_measure ; CW-SSIM paper https://live.ece.utexas.edu/publications/2009/sampat_tip_nov09.pdf — spatial-domain SSIM "is highly sensitive to translation, scaling, and rotation of images"; alternatives (CW-SSIM) were built to be "insensitive to 'nonstructured' geometric image distortions ... caused by nuisance factors, such as, relative movement of the image acquisition device".
- Transfer: pixel/structure metrics (SSIM/LPIPS) only make sense with identical camera, lighting and framing; DINO similarity is the better identity/shape proxy than CLIP-I; CLIPScore is near chance on compositional/spatial errors.

### B12. MMMU / MMMU-Pro (Yue et al. 2024)
- URLs: https://arxiv.org/html/2311.16502 ; https://arxiv.org/html/2409.02813
- MMMU: "11.5K meticulously collected multimodal questions from college exams, quizzes, and textbooks", "30 subjects and 183 subfields", "30 highly heterogeneous image types", collected by "over 50 university students"; dedup by "lexical overlap and source URL similarity"; "Approximately 10% of the problems, classified as very easy" removed; expert accuracy "88.6%"; GPT-4V "55.7%"; error analysis of 150 GPT-4V errors: "35%" perceptual, "29%" lack of knowledge, "26%" reasoning.
- MMMU-Pro: "We begin by filtering out questions that can be answered by text-only LLMs" (four text models, 10 trials, drop if ≥3 of 4 correct in the majority of trials); options "from four to ten"; "1730 questions" plus a vision-only screenshot version ("3,460 questions"); drops "ranging from 16.8% to 26.9% across models" (GPT-4o 69.1% → 49.7%).
- Transfer: run a text-only ablation (no render / no image) to detect shortcut solvability of 3D judging prompts; expand distractors.

### B13. 3D-spatial VLM benchmarks (brief)
- 3DSRBench — https://arxiv.org/abs/2412.07825 (ICCV); VSI-Bench — https://arxiv.org/abs/2412.14171 ("5,000 QA pairs that span eight 3D spatial cognition tasks" from ScanNet/ScanNet++/ARKitScenes); SpatialRGPT-Bench — https://arxiv.org/pdf/2406.01584 (nuScenes/KITTI/SUNRGBD/ARKitScenes/Hypersim); Open3D-VQA — https://arxiv.org/abs/2503.11094 ("73k QA pairs spanning 7 general spatial reasoning tasks"). VisionArena notes "current VLMs struggle with spatial reasoning and planning tasks" ( https://arxiv.org/abs/2412.08687 ).
- Transfer: VLM judges are weakest exactly on relative-position/metric questions — verify positions from the scene graph, not from the judge.

### B14. VisionArena (Chou et al. 2024)
- URL: https://arxiv.org/html/2412.08687
- "230K real-world conversations", "73K unique users, 45 VLMs"; VisionArena-Bench: 500 prompts, GPT-4o judge vs a GPT-4-Turbo anchor; "Spearman Correlation: 97.3%", Kendall 89.7% with live arena; "Length is by far the most influential stylistic factor"; user-vs-expert agreement "0.72 (excluding ties) and 0.56 (including ties)".
- Transfer: anchor-based pairwise judging reproduces arena rankings; length bias must be controlled (strip comments/prose from code before judging).

### B15. Text-to-3D evaluation protocols
- **T3Bench** (He et al. 2023) — https://arxiv.org/html/2310.02977 — 300 prompts (100 each: single object, single object with surroundings, multiple objects), GPT-4 generated, filtered for proper nouns. Quality: render "from 161 locations" (level-2 icosahedron), five focal lengths, ImageReward per view, "regional convolution" for t=3, take "the highest score from all viewpoints". Alignment: 12-view BLIP captions → GPT-4 merges into one 3D caption → GPT-4 scores 1–5 vs prompt. Human correlation: quality Spearman 0.784 / Kendall 0.636; alignment Spearman 0.780 / Kendall 0.701; single-view CLIP Spearman 0.638; "1,260 scores" on 30% of prompts.
- **GPTEval3D** (Wu et al. CVPR 2024) — https://arxiv.org/html/2401.04092 — criteria: text–asset alignment, 3D plausibility, texture details, geometry details, texture–geometry coherency; pairwise: "a large image containing renderings of the 3D asset from four or nine viewpoints" plus surface-normal renders, both orderings; Elo: "σ=arg min_σ ∑_{i≠j} A_ij log(1+10^{(σ_j−σ_i)/400})", init 1000; ~110 GPT-4 prompts on a complexity×creativity grid. Kendall τ vs human Elo: alignment 0.821, plausibility 0.641, coherency 0.564, texture 0.821, geometry 0.795, avg 0.710; CLIP-E avg 0.628, Aesthetic-S 0.671, PickScore 0.562. Human expert κ > 0.53; general users ≈ 0.3. Limitations: "GPT-4V hallucinations", position bias, adversarial attacks, "quadratically growing" comparisons.
- **3DGen-Bench / 3DGen-Arena** (2025) — https://arxiv.org/html/2503.21745 — "1,020 prompts, including 510 texts and 510 images"; 19 models; "13,680 battle pairs" → "68,400 unique votes" (expert), "56,100" score entries, "8,045 anonymous votes"; five dimensions (geometry plausibility, geometry details, texture quality, geometry–texture coherence, prompt–asset alignment); shown as "360° panoramic videos" in "normal maps, textureless geometry, and fully textured renderings"; 3DGen-Score (CLIP-ViT-H/14, multi-view) pairwise alignment 0.725 (T2-3D) / 0.767 (I2-3D), Kendall τ 0.711 / 0.856; 3DGen-Eval (LLaVA) 0.672 / 0.731; both beat GPT-4V on several dimensions.
- **Eval3D** (Duggal et al. CVPR 2025) — https://arxiv.org/html/2504.18509 — consistency-among-tools metrics: geometric consistency (analytic normals vs normals predicted by a depth model, "pixel-wise angular difference" inliers at 23°); semantic consistency (per-vertex DINO variance across views); structural consistency (Stable-Zero123 novel view vs actual render, DreamSim); text–3D alignment (TIFA-style QA with LLaVA-NeXT-7B, "globally aligned if and only if, for some viewpoint, the answers of all its neighboring views are aligned"); aesthetics (GPT-4o Elo or ImageReward). 160 prompts (80 single / 80 multi), 10 annotators. Human agreement: geometric 83.0% (GPT-4V 46.9%, ImageReward 44.3%); structural 69.2% (68.9% / 63.7%); aesthetics 87.4% (85.6%, T3Bench 76.5%); text-3D 88.7% (72.8%, T3Bench 56.7%); inter-annotator 83.1%–97.2%.
- **MATE-3D / HyperScore** (ICCV 2025) — https://arxiv.org/html/2412.11170v1 — 160 prompts × 8 methods = "1,280 textured meshes"; "11-level impairment scale (ranging from 0 to 10)" per ITU-T P.910; "1,280×4×21=107,520 annotations" (21 subjects); SRCC overall: CLIPScore 0.510, ImageReward 0.623, HyperScore 0.792.
- **T23D-CompBench / Rank2Score** (2025) — https://arxiv.org/html/2509.23841v1 — "3,600 textured meshes", "twelve fine-grained quality dimensions", "129,600 reliable individual annotations"; SRCC Rank2Score 0.908 vs HyperScore 0.809, Q-Align 0.709, HPS v2 0.688.
- **3D Arena** (2025) — https://arxiv.org/abs/2506.18787 — "123,243 votes from 8,096 users across 19 state-of-the-art models"; ELO; iso3d 100 prompts; "99.75% user authenticity"; presentation bias: "Gaussian splat outputs achieving a 16.6 ELO advantage over meshes" and "textured models receiving a 144.1 ELO advantage over untextured models"; recommends "multi-criteria assessment, task-oriented evaluation, and format-aware comparison".
- **3D-DefectBench** (2026) — https://arxiv.org/html/2607.10826 — factorial study of VLM defect-detection pipelines: camera (6-view oblique / 14-view equatorial / 14-view multi-ring), visual input (RGB, RGB+depth/normals, geometry-only, normal-only), prompt schema (compact binary / definition-guided / rubric-guided binary / rubric-guided checklist), 12 judge models; nine binary defects (5 geometry, 4 texture); "VLM model explains the most variance"; "RGB is worth far more than any added geometric channel" (model-choice gap ≈0.14 macro MCC vs ≈0.04 for visual input); "The 14-view equatorial and 14-view multi-ring turntables do not improve on the compact six-view oblique turntable (best-vs-worst camera gap ∼0.02 macro MCC)"; best VLM 0.298 expert-geometry MCC vs held-out expert 0.519; recommended default "six-view oblique RGB turntable with a rubric-guided checklist prompt".
- **Cyc3D** (2026) — https://arxiv.org/abs/2608.28080 — "View-Cycle Structural Consistency, a closed-loop render-regenerate-align protocol"; usability axes "geometric structure, reference-image fidelity, mesh discretization and efficiency, and UV parameterization quality"; "even the strongest methods achieve cycle-stability scores below 48". Human-agreement numbers: not found in abstract.
- **Cap3D** (Luo et al. 2023) — https://arxiv.org/abs/2306.07279 — multi-view BLIP-2 captions, CLIP filtering, GPT-4 consolidation; "660k 3D-text pairs" on Objaverse; judged against "41k human annotations"; "Cap3D surpasses human-authored descriptions in terms of quality, cost, and speed".
- Transfer: (i) multi-view grids + normal maps + both orderings + Elo is the standard VLM-judge protocol for 3D, with Kendall τ ≈ 0.7 vs humans; (ii) untextured/format presentation shifts human votes by >100 Elo, so fix the presentation format; (iii) consistency-based metrics (Eval3D) beat MLLM judges on geometry; (iv) a 6-view oblique RGB turntable is as good as 14 views for defect detection; (v) Cap3D-style multi-view captioning is a defensible way to author prompts from existing assets.

---

## C. 3D shape metric methodology

### C1. Chamfer distance / F-score / IoU — Tatarchenko et al. CVPR 2019
- URL: https://ar5iv.labs.arxiv.org/html/1905.03678
- CD(G,R) = (1/|R|)∑_r min_g ||r−g||₂ + (1/|G|)∑_g min_r ||g−r||₂ (they use L2, non-squared, mean over both directions).
- F-score = "the harmonic mean between precision and recall"; precision = "the percentage of reconstructed points that lie within a certain distance to the ground truth"; recall = "the percentage of points on the ground truth that lie within a certain distance to the reconstruction"; sampled "10K points from the surface"; IoU at 128³.
- Why not CD/IoU: CD "significantly perturbed by the geometric layout of outliers"; IoU is "dominated by the interior parts of objects" and "Low to mid-range scores indicate a significant discrepancy between two shapes". Recommendation: F-score "at distance thresholds of 1% and below" of the bounding-volume side.
- Finding: retrieval and clustering baselines "outperform recent state-of-the-art methods" — networks do recognition, not reconstruction.
- Transfer: report F@τ (τ = 1% of the normalised bbox side) as the headline shape metric, CD as secondary; always include a retrieval baseline (nearest training asset) to show the benchmark is not solvable by recall.

### C2. Occupancy Networks protocol (Mescheder et al. CVPR 2019)
- URL: https://ar5iv.labs.arxiv.org/html/1812.03828
- Volumetric IoU: "randomly sampling 100k points from the bounding volume and determining if the points lie inside our outside the ground truth / predicted mesh"; Chamfer-L1: "the mean of an accuracy and and a completeness metric. The accuracy metric is defined as the mean distance of points on the output mesh to their nearest neighbors on the ground truth mesh. The completeness metric is defined similarly, but in opposite direction"; "randomly sampling 100k points from both meshes and using a KD-tree"; units: "we use 1/10 times the maximal edge length of the current object's bounding box as unit 1" (this is where the ×10 convention comes from); normal consistency: "the mean absolute dot product of the normals in one mesh and the normals at the corresponding nearest neighbors in the other mesh"; watertight GT via Stutz et al. code.
- Transfer: 100k surface samples, CD-L1 in units of bbox/10, normal consistency as a cheap surface-quality signal; requires watertight meshes for IoU (non-watertight OpenSCAD/Blender output must be handled explicitly).

### C3. Point-set distribution metrics — Achlioptas et al. 2018, PointFlow (Yang et al. 2019)
- URLs: https://ar5iv.labs.arxiv.org/html/1707.02392 ; https://ar5iv.labs.arxiv.org/html/1906.12320
- CD (squared form) d_CH = ∑_{x∈S1} min_y ||x−y||₂² + ∑_{y∈S2} min_x ||x−y||₂²; EMD d_EMD = min_φ:S1→S2 ∑ ||x−φ(x)||₂ (bijection; equal cardinality).
- JSD: voxel-occupancy marginal distribution divergence; COV: "the fraction of the point clouds in B that were matched to point clouds in A"; MMD: "the average of distances in the matching"; 1-NNA: "Leave-one-out accuracy of the 1-NN classifier", 50% = indistinguishable.
- Pitfalls: "CD fails to distinguish the inferiority of the r-GAN samples" when density clusters (one summand vanishes); JSD "Only considers the marginal point distributions but not the distribution of individual shapes"; MMD "Very insensitive to low-quality point clouds"; COV "Does not evaluate the quality of generated point clouds". PointFlow uses 2048 points, data "normalized to have zero-mean per axis and unit-variance globally".
- Transfer: for unconditional/creative 3D-code generation use 1-NNA with CD and EMD; for conditional tasks use per-item CD/F-score; be explicit whether CD is squared-L2 (Achlioptas/PointFlow) or L1/L2-mean (OccNet/Tatarchenko) — values are not comparable across conventions.

### C4. Density-aware Chamfer Distance (Wu et al. NeurIPS 2021)
- URLs: https://arxiv.org/abs/2111.12702 ; https://arxiv.org/html/2111.12702
- CD pitfalls: "CD is usually insensitive to mismatched local density", "unbounded value range induces a heavy influence from the outliers"; toy example: "CD hardly changes with the imbalance ratio" at low noise, and "under a high noise level, higher imbalance even results in a lower CD". EMD "dominated by global distribution while overlooks the fidelity of detailed structures" and slow.
- DCD: d_DCD(S1,S2) = ½[ (1/|S1|)∑_{x∈S1}(1 − (1/n_ŷ)·e^{−α||x−ŷ||₂}) + (1/|S2|)∑_{y∈S2}(1 − (1/n_x̂)·e^{−α||y−x̂||₂}) ], ŷ = NN of x in S2, n_ŷ = query frequency of ŷ; bounded [0,1]; "we fix α=1000 for evaluation"; training α∈[40,100].
- Transfer: a bounded, density-aware variant is safer than raw CD when generated meshes have wildly different tessellation density (typical of procedural code output).

### C5. Alignment and normalisation before CD — Monnier et al. 2022 (UNICORN)
- URL: https://ar5iv.labs.arxiv.org/html/2204.10310 (Appendix C.1)
- "Meshes are centered and normalized so that they exactly fit inside the cube of unit length [-0.5,0.5]³; this is important to obtain results that are comparable. Second, we sample 100k points on the mesh surfaces."; gradient-based ICP "minimizes by gradient descent the Chamfer-L2 distance between the point clouds by jointly optimizing 3 translation parameters, 6 rotation parameters and 3 scaling parameters" (Adam, lr 0.01, 100 iterations; classical ICP "diverge[s] when optimizing scale"); "an ICP pre-processing is crucial for an unbiased 3D reconstruction evaluation" — identical shapes under a different canonical transform otherwise score "dramatically poor".
- Transfer: for code-generated shapes whose origin/axis convention is unspecified (OpenSCAD vs Blender Z-up etc.), normalise to the unit cube and run scale-aware ICP (or evaluate at a small set of canonical rotations) before CD/F-score; report both aligned and unaligned numbers.

### C6. CAD-specific: DeepCAD (Wu et al. ICCV 2021)
- URLs: https://ar5iv.labs.arxiv.org/html/2105.09492 ; https://github.com/ChrisWu1997/DeepCAD
- CD: "we evaluate CD by uniformly sampling 2000 points on the surfaces of reference shape and recovered shape"; median CD reported (Table 2: 0.787 ×10³ for DeepCAD) because of outliers; Invalid ratio = "the percentage of the output CAD models that fail to be converted to point clouds" (i.e. the CAD kernel cannot build a valid solid); Table 2 invalid ratios: Ours+Aug 2.72, Ours 3.30, Alt-ArcMid 3.26, Alt-Trans 3.30, Alt-Rel 3.51, Alt-Regr 4.32 (%). Generation: COV 78.13, MMD 1.45 (×10²), JSD 3.76 (×10²), "randomly sample a reference set of 1000 shapes and generate 3000 shapes", repeated three times; README: `python evaluate_ae_cd.py --src ... --parallel` "for chamfer distance and invalid ratio".
- Handling empty/degenerate output: DeepCAD excludes invalid models from CD and reports the invalid ratio separately (no penalty CD is assigned) — the two numbers must be read together.
- Transfer: report (a) invalid/empty-mesh ratio, (b) median CD over valid outputs, (c) mean CD or F-score over all with a defined penalty for invalid outputs; never silently drop failures.

---

## Cross-cutting recommendations distilled (each traceable to the items above)
1. Estimator: unbiased pass@k with n>k samples (A1); n=10 at T=0.2/top-p 0.95 for pass@1 (A4); greedy only with batch size 1 and a repetition/token-cap failure class (A9, A16).
2. Noise: mean ± SE via CLT, clustered SE for prompts sharing a scene, paired differences between models, ≥10 seeds (A16); ~1,000 independent items to detect 3-point gaps (A16).
3. Task validation: 3-annotator 0–3 severity for underspecified prompt and over-specific checker (A7); adversarial test augmentation of GT (A2); text-only ablation of the judge (B12); retrieval baseline (C1).
4. Automatic geometry: F@1% + CD-L1 (×10 bbox units) + normal consistency on 100k samples after unit-cube normalisation and scale-aware ICP (C1, C2, C5); DCD if tessellation density varies (C4); invalid ratio + median CD separately (C6).
5. VLM judge: multi-view grid (6 oblique views suffice for defects; 4–9 views + normals for pairwise), both orderings, reference render supplied, per-task checklist with binary questions, absolute 10-point or 0–4 rubric sub-scores, report κ / Scott's π and rank correlation with a human arena (A13, B3, B4, B5, B9, B15).
6. Image metrics: CLIPScore is near chance on composition (B2, B8); SSIM/LPIPS need identical camera/lighting (B11); DINO for identity (B11); preference models are OOD on renders (B10, B15).
