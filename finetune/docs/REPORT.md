# Fine-tuning for 3D code generation: results and lessons

Fine-tuning Qwen3.5-9B and Qwen3.8-27B on 3DCodeVerse. Every evaluation is **execution-based**: the generated
code is actually run (Blender 5.0.1 / CadQuery / OpenSCAD / glslangValidator / headless WebGL2); text
similarity is never used.

This document keeps only the **conclusions that changed how we work**. The per-run log is
[`../llm_finetune_exps.md`](../llm_finetune_exps.md); the original day-by-day study notes (in Chinese) are in
git history as
[`REPORT_FULL.md`](https://github.com/gaoypeng/3dcodeverse/blob/943e666926a255888e3e6a37774865d565bcc5ba/finetune/docs/REPORT_FULL.md).

---

## 1. Best models

| model | bench | blender | cadquery | openscad | glsl | threejs |
|---|---|---|---|---|---|---|
| **best+stopDPO** (greedy) | 95.8% | 92.2% | 39.5% | 82.0% | 49.5% | 57.5% |
| **md_9b_plus+stopDPO** (greedy) | 85.9% | 93.2% | 99.0% | 74.0% | 79.5% | 75.0% |
| **md_9b_plus+stopDPO** (sampled) | 86.8% | 91.3% | 98.0% | 82.0% | 85.0% | 42.5% |
| **stopDPO_r2** (sampled) | 89.1% | 92.2% | 97.5% | 94.0% | 87.0% | 60.0% |
| mmmix2 (SFT only, before the fix) | 93.9% | 92.2% | 94.5% | 6.0% | 60.0% | 45.0% |
| base Qwen3.5-9B (not fine-tuned) | 0.0% | 1.0% | 10.0% | 46.0% | 39.0% | 77.5% |

No single model is best on every dialect: `best+stopDPO` has the highest 3DCodeBench score, `stopDPO_r2`
the best dialect coverage, and `md_9b_plus+stopDPO` is the most balanced.

Every model with stop-DPO beats its own SFT base on four to five dialects — §3 explains why.

---

## 2. Three data regularities

### 2.1 What matters is the number of **distinct runnable programs**, not the number of rows

Three independent controls at the same compute budget: pairing one program with three caption variants
(3× the rows) does not raise the score; three times as many distinct programs does. Of the 1.09M rows built
from 270k distinct programs in the corpus, two thirds add nothing to training.

### 2.2 A dialect's score is driven mainly by **its own sample count**, but its share still matters

Four runs that change only the data mix (effective batch, epochs and cutoff all fixed):

| run | Blender samples | Blender share | total samples | bench | bench output length |
|---|---|---|---|---|---|
| rebalanced | 6,590 | 23.6% | 28k | 46.2% | 9,771 |
| full9b | 6,590 | 2.6% | **271k** | 58.5% | 10,054 |
| md_9b_plus | **21,012** | 7.1% | **296k** | 79.2% | 3,746 |
| mmmix2 | **21,012** | 11.0% | 192k | **93.9%** | **1,266** |

**Total size is not the lever**: going from 28k to 271k samples (10×) buys 12 pp. **Sample count is the main
lever**: restoring Blender from 6.6k to 21k samples buys 20.7 pp. **Share still has a residual effect**: the same
21,012 Blender samples score 93.9% at an 11% share but only 79.2% when 104k samples of other dialects dilute
them to 7.1%.

Output length moves with it (1,266 → 3,746 → 10,054 tokens): dilution does not just lower accuracy, it also
**triggers runaway generation** — two sides of the same effect.

A counter-intuitive corollary: **"use all the data" hurts the target dialect**. Sampling at corpus proportions
leaves Blender at 2.6% of the mix, so a model trained on 271k rows is 35 pp worse than one trained on 192k.

### 2.3 The corpus is extremely unbalanced across dialects, and that caps capability

| dialect | distinct programs | share |
|---|---|---|
| CadQuery | 141,204 | 49% |
| GLSL | 117,096 | 40% |
| Blender | 29,905 | 10% |
| **OpenSCAD** | **1,191** | **0.4%** |
| **three.js** | **340** | **0.1%** |

The problem with OpenSCAD and three.js is not the training method: **the corpus holds only about a thousand and
a few hundred programs** for them.

---

## 3. The key finding: the problem is behaviour, not knowledge

After fine-tuning, OpenSCAD scored 6% while **the untuned base scored 46%**. It looks like catastrophic
forgetting — it is not.

**Evidence**: the same weights score 6% greedy and 44% sampled. Greedy outputs average 30,166 tokens, while the
median training answer is 884 tokens and the 8,192 cutoff guarantees the model never saw a sample longer than 8k.
The longest "program" is 206 KB and 576 lines, of which 146 are distinct; most of it is one comment repeated
hundreds of times:

```
//The case is designed to be printed at 0.4mm gap between hinges and pencil
   holder holder holder holder holder holder holder holder holder ...
```

**Not a single line of geometry.** This is a greedy degeneration loop, not an inability to write OpenSCAD.

### The fix: let the model produce its own negatives

* negative = the model's greedy runaway output (never terminates, or is longer than anything it saw in training)
* positive = a sampled output for the same prompt that terminates normally
* **training-set prompts only** (evaluation suites never enter training)

3,687 prompts yielded 1,605 pairs; negatives have a median of 46,202 characters and positives 1,210 (35×).
One hour on one GPU:

| | bench | cadquery | openscad | glsl | threejs |
|---|---|---|---|---|---|
| before | 93.9% | 94.5% | **6.0%** | 60.0% | 45.0% |
| round 1 | 92.5% | 98.5% | 40.0% | 79.5% | 80.0% |
| **round 2 (sampled)** | 89.1% | **97.5%** | **94.0%** | **87.0%** | 60.0% |

Output length collapses with it: OpenSCAD drops from 30,166 tokens to 410.

**Three follow-on conclusions:**
1. **The pairs transfer, and generating your own is not worth it.** The same 1,605 pairs produced by mmmix2 work
   on three different models:

   | model trained | OpenSCAD | bench |
   |---|---|---|
   | mmmix2 (own pairs) | 6% → 94% | 93.9% → 89.1% |
   | dpo_exec_v2 (transferred) | 44% → 82% | 94.3% → 95.8% |
   | md_9b_plus (transferred) | 8% → 70% | 79.2% → 85.9% |

   A single-variable control on md_9b_plus (same base, same hyperparameters, only the pair source changed):
   transferred pairs give bench 85.9 / OpenSCAD 70.0 / three.js 75.0, self-generated pairs 85.4 / 50.0 / 72.5.
   **Transferred wins four metrics, self-generated two, all within noise** — and self-generation costs an extra
   2.5 hours. The right use is to **produce one batch of terminating samples with a healthy model and ship it as a
   reusable asset**.
2. **After training, greedy beats sampling** (82.0% vs 76.0%) — the behaviour is actually fixed, not bypassed by
   a different decoding.
3. **It explains an older result.** Much of the earlier gain from execution-feedback DPO (93.9% → 96.2%) was
   probably that it also taught the model to stop: running pair generation on dpo_exec_v2, 3,247 of 3,687 prompts
   already terminated under greedy, and **it produced 0 pairs**.

---

## 4. Evaluation: we were wrong four times

**4.1 Empty output scored as 0%.** The engine failed to start → empty directory → the scorer reported 0%. A real
0% and a failed launch look identical. Two healthy models were once recorded as 0/200 on every suite. An empty
directory is now **refused** by the scorer.

**4.2 The output cap scored "not finished" as "wrong".** A 6,144-token cap truncated 72 of 212 tasks, 36 of which
failed with `'(' was never closed`. But raising the cap to 131k **lowered** the score (69.3% → 64.6%, with 14×
longer outputs) — the small budget had been **forcing the model to converge**. Removing the hard-coded cap was
right, but the conclusion was the opposite of what we expected.

**4.3 Measure the noise floor, don't estimate it.** Same model, same entry point, three back-to-back runs in one
batch:

| bench | blender | cadquery | glsl | openscad | threejs |
|---|---|---|---|---|---|
| 0.0 pp | 0.0 pp | 0.5 pp | 1.5 pp | 2.0 pp | **5.0 pp** |

Across batches (hours apart) it reaches 6–8 pp. **Runs are comparable only when they finish back to back in one
session**, not merely "with the same script". An unverified ±2 pp guess once led us to dismiss a real signal.

**4.4 Long-output dialects need both greedy and sampled numbers — and which is better flips once the fix is in.**
Reporting greedy only is exactly what hid the §3 defect for the whole project; every earlier OpenSCAD / GLSL /
three.js greedy number systematically understated the real capability.

After stop-DPO the picture changes: the three fixed models are **clearly better greedy than sampled on three.js**
(75.0% vs 42.5%, 72.5% vs 52.5%), while OpenSCAD is still better sampled. The accurate advice is: **sample to work
around runaway generation while it is unfixed; use greedy once it is fixed** — on a dialect that no longer runs
away, sampling only adds noise. This is also the observable difference between "fixed" and "worked around".

---

## 5. Suspect the measuring tool before the data

This pattern came up six times in the project, and each time it saved a large amount of rework:

| symptom | actual cause |
|---|---|
| 4,192 shaders "fail to compile" | only 588 really needed changes; the rest were validator bugs (guessing with a regex whether iChannel is 2D or a cubemap, which is a **binding property, not a syntax property**; missing `iFrameRate`; using ES 3.10 while Shadertoy is ES 3.00) |
| every three.js output "renders blank" | `gl.readPixels` read an already-cleared buffer; after switching to screenshot analysis, 40/40 reference programs pass |
| half of the Blender samples fail to execute | Blender's bundled Python has no scipy, and 15/212 reference scripts import it. With it installed, 27B goes from 49.5% → 57.6% |
| render validator reports "every shader is black" | a failed screenshot discarded the valid measurement along with it |
| image evaluation produces 0 results | `multi_modal_data=` is not a parameter of `llm.chat()` |
| OpenSCAD at 6% after fine-tuning | greedy degeneration loop (§3) — reproduced on three independent models: 6%→44%, 8%→46%, 34%→80% |

---

## 6. Practical constraints in training and inference

* **LoRA under ZeRO-3 is 7.8× slower than DDP** (142 s/step vs 18 s/step). LoRA optimizer state is tiny to begin
  with, so the memory ZeRO-3 saves is paid for with a full-parameter all-gather every step. But 27B at cutoff 8192
  OOMs on an 80 GB card; the compromise is **DDP + cutoff 4096**.
* **27B multimodal only just fits on one completely empty 80 GB H100** (78.2/79.6 GB, cutoff 4096 +
  `image_max_pixels: 200704`). Any card someone else has touched fails — four multi-GPU attempts all came up 1–4 GB
  short. And its score (47.2%) is no better than 9B (48.6%).
* **vLLM's `gpu_memory_utilization` is a fraction of total memory, not of free memory.** On a shared machine a
  hard-coded 0.88 asks a card with 12 GB already taken for 70 GB. Derive it from the actual free memory, and scale the
  headroom with model size (4 GB of headroom is not enough for a 51 GB model; the KV-cache probe OOMs).
* **Qwen3.5/3.8 have linear-attention layers**, and vLLM needs one Mamba cache block per concurrent sequence. 27B has
  only 179 blocks left while the default `max_num_seqs` is 1024, so it must be lowered explicitly or the engine will
  not start — with an error message that looks nothing like out-of-memory.
* **When mixing multimodal datasets, image datasets must come before text datasets.** HuggingFace takes the schema
  from the first dataset; with text first, the `images` column is inferred as null and the image data cannot be cast
  into it.
* **Datasets over 2 GB must be stored as parquet, not JSON.** Arrow reading one large JSON raises
  `OverflowError: value too large to convert to int32_t`, which gives no hint that file size is the problem. A
  3.05 GB JSON becomes 1.04 GB as parquet and the problem disappears.
* **Five traps of a shared machine**, none of which exist on a dedicated one: (1) someone else's resident
  placeholder process can eat 135 GB, and one that declares 16 GiB actually used 33.8 GB; (2)
  `gpu_memory_utilization` is a fraction of total, not free, memory; (3) all four 27B OOMs happened on the card
  shared with someone else, and the run works on an empty card; (4) the GPU picker was hard-coded to look at the
  first four cards and never saw the other four after the machine grew to eight; (5) **there is a race between
  picking a GPU and using it** — a card that was free when picked is full when the engine starts, and the engine dies
  without producing any output. The evaluation entry point now re-checks before launch and retries on another card.
* **Never edit a script while it is running.** bash reads the file by byte offset, and an edit makes it execute
  misaligned bytes. This happened twice in the project.

---

## 7. Dataset: `3dcodeverse/3dcodeverse-2m`

Figures below are as of the end of this study (2026-08); the current numbers are on the
[dataset card](https://huggingface.co/datasets/3dcodeverse/3dcodeverse-2m).

Nine sources, **290,480 distinct runnable programs**, **1,932,722 training pairs** (text→code / image→code /
image+text→code × every caption variant), and 545,397 renders at training resolution.

The root `dataset_info.json` registers **609 subsets**; with `dataset_dir: REMOTE:3dcodeverse/3dcodeverse-2m` you can
train on any of them by name, without downloading or converting anything first. The format was verified with real
LLaMA-Factory training runs.

### Five silent failures fixed while building it

What they share is that **none of them raised an error; each quietly produced a plausible-looking result**:

| defect | consequence |
|---|---|
| metadata byte offsets were stale across the board (only 577 of 1,953 pointed at a real tar header, because the archives were repacked after the metadata was written) | **70% of the image pairs** lost |
| a file-tree source keyed samples by directory name, collapsing 340 samples into 240 | **100 samples paired with someone else's renders** (a mismatched code–image pair is worse than a missing image) |
| the length check ran before the code substitution, and the URDF `code` column is empty to begin with | **6,146 rows** lost |
| renders of three file-tree sources were read in place and never went through the compression step | the image datasets of three sources were **completely unusable** (6,616 image paths pointing at files that do not exist) |
| empty output scored as 0% by the scorer | healthy models recorded as all-zero |

The first four were found by **actually opening the data and looking**, not by reasoning about it.

### Usage (tested, not inferred from documentation)

Three registries: `dataset_info_text.json` (342 subsets, text only), `dataset_info_image.json` (276, with images),
`dataset_info.json` (618, all).

**Text-only data trains straight from the Hub** with nothing downloaded:

```yaml
dataset_dir: REMOTE:3dcodeverse/3dcodeverse-2m
dataset: 3dcv_shadertoy__33_text
```

**Image data must be downloaded first and pointed at locally.** `REMOTE:` does not work for image datasets:
LLaMA-Factory only joins `media_dir` onto relative paths when the data comes from local files (`converter.py`
restricts that logic to `load_from in ["script","file"]`); loading from the Hub hands the relative path straight to
the filesystem and raises `FileNotFoundError`. Both paths were run. The renders are 448 px JPEGs, about 13 GB across
the nine sources — **there is no need to download the 337 GB of tars**.

### Two design principles

**A converter must not decide benchmark boundaries for its users.** Originally any sample overlapping one of the
212 3DCodeBench factories was dropped, which emptied two folders entirely (they were 100% overlapping samples). Now
the overlap is **counted** in `qc.json` and the data is kept — the split is a training-time decision, not a
conversion-time one.

**Spot checks can falsify, not verify.** Spot-checking four datasets said "all paths are relative"; a full scan of
411 found **36,964 absolute paths in three of them**, a regression introduced by a rebuild that same day (reusing
already-extracted images went through a different branch). Global invariants need a full scan — that is why
`audit_pairs.py` exists, and its exit code can gate CI.

And the first version of that audit script reported all 1.94M rows as structurally invalid, because parquet returns
numpy arrays and `isinstance(x, list)` is false. **§5 applies to the checker you just wrote, too.**

---

## 8. Still open

* **The capability ceiling for OpenSCAD and three.js** is set by the corpus (1,191 and 340 programs). Stop-DPO
  rescues OpenSCAD from 6% to 94%, but that fixes behaviour; raising the ceiling needs more distinct programs.
* **No single model is best on every dialect**; gains on one dialect trade off against another.
* **27B shows no advantage over 9B** (multimodal 47.2% vs 48.6%) at a much higher cost.
