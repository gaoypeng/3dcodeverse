# Reviewing offline cinematic frames

`codeverse3d.judges.cinematic.review_frames` accepts the shared `RenderSet`
directly. It can review authored Blender Cycles frames without creating an
invalid `scene` / `blender` `Spec`, changing the language registry, or claiming
that the main Three.js scene pipeline renders Blender shaders.

```python
from codeverse3d.contracts.artifacts import RenderSet, RenderView
from codeverse3d.cost.instrument import run_ledger
from codeverse3d.judges.cinematic import review_frames

frames = RenderSet(
    renderer="Blender Cycles CUDA",
    views=[RenderView(name="Hero", path="/absolute/path/hero.png", judge=True)],
)
with run_ledger("/absolute/path/case_revision", run="cinematic_study"):
    judgment = review_frames("The intended environment and artistic quality.", frames)
```

The adapter reuses the harness's metered `ChatModel`, strict rubric schema,
`parse_judge_output`, defect caps, weighted scores and `Judgment`. It sends at most
four explicitly selected views by default, verifies image files before making a
provider call, preserves their MIME types, and excludes `judge=False` views.
Provider failures propagate; a missing review is not recorded as a quality score.

`cinematic_v1` assesses composition, lighting, geometry, surfaces, place and spatial
coherence. Its six defect checks include accidental hero clipping, obvious
primitive assembly, repeated natural forms, undressed ground, floating geometry
and incoherent lighting. Scores are computed by code after the model reports
observations. The 0.82 threshold is an editorial target, **not a calibrated claim
of perceptual accuracy**. VLM review remains fallible; visual inspection and
independent evidence are required, especially for clipping and support judgments.

This interface deliberately makes no motion, frame-rate or model-superiority
claim from a still image. Temporal validation, equal-budget comparisons and
multi-sample calibration remain separate work. The existing `scene_v1` and
orchestration defaults are unchanged.

The developing sources and retained case evidence live outside this repository.
The public-facing scene gallery identifies them
as visual-development studies, not a controlled model benchmark.

Validation: `python -m pytest tests/judges -q -n4` — 136 passed during the first
integration, including evidence selection, missing/corrupt images and deterministic
primitive-assembly cap tests. Actual Gemini Pro reviews were recorded for the
initial castle and outpost frames; both correctly remained below the target, but
individual observations still require checking against the images.
