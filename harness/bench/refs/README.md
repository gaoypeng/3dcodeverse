# bench/refs — reference photos per prompt id

One folder per battery prompt id, holding photos of the REAL thing the prompt names:

```
bench/refs/<prompt_id>/<what_it_shows>.png|jpg|jpeg|webp
```

`bench/run_bench.py::discover_references` attaches every image in the folder to the prompt's
`Spec.references` for every bench driver (`3dcv bench run`, `bench/compare_backends.py`,
`bench/ab_plan.py`), in sorted filename order, with the file stem (underscores → spaces) as the
note the agent and the judge read.  A prompt may also list `references:` paths in its yaml.

* object tracks (static / articulated): the first image is the `target` (silhouette IoU is
  measured against it, `ReferenceJudge`), the rest are `detail`.
* graphics / scene: every image is a `likeness` reference — the agent gets the photos on its
  first message with a note saying to take the physics (dominant colour, structure, where the
  light sits, how much stays dark), not the composition; the judge is `LikenessJudge` (the same
  photos beside the frames, no silhouette).

So "with references" vs "without" is the same battery with the folder present or absent.
Keep photos you have the right to use; name the file by what it shows.  First folder:
`tsr_gfx_aurora_ridge/` (two aurora photographs, 2026-08-26).
