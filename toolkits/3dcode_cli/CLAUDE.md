# CLAUDE.md — 3dcode toolkit & 3DCodeVerse

Orientation for coding agents (Claude Code et al.) working in this repo or using it to
author data. Read this first.

## What this project is

**3DCodeVerse** is a community-curated corpus that pairs **3D objects with the *code* that
builds them** — not static meshes, but runnable programs (Blender Python, CadQuery, …) plus
their reference images / prompt / renders. The goal: a large (TB-scale), multi-source,
multi-dialect, license-clean corpus for training & evaluating 3D-from-code generation.

**This repo (`3dcode`)** is the CLI toolkit contributors run **on their own machine** to
validate, fingerprint (dedup), executability-check, render, and upload (code → 3D) *projects*.
Data flows: `3dcode push` → private R2 staging + a validation report → a maintainer reviews in
a web Inbox → approve → `3dcode ingest` pulls it into the canonical store. Everything that can
run locally runs locally — nothing routes heavy compute/bandwidth through a central server.

## The data model (memorize this)

- A **source** = one origin/contributor (a top-level folder).
- A **project / sample** = one independent asset: a sub-folder with **code at the top**,
  `renders/` (images + `.glb`), an optional `prompt.txt`, and an auto-generated `meta.json`.

```
<source>/<project>/
  model.py            # the code — the core artifact (must be self-contained & runnable)
  renders/            # rendered images + mesh
  prompt.txt          # text prompt (optional)
  meta.json           # id, dialect, modality, dedup hashes, exec results, status
```

## The toolkit (commands)

`config` · `validate` · `check` (dedup vs corpus) · `anomalies` (length/char outliers) ·
`exec` (run the code, must build non-empty geometry) · `render` (clay/textured into renders/) ·
`push` (validate→fingerprint→upload→register) · `ingest` (admin: pull approved → core).

## If you are a coding agent AUTHORING / CONVERTING a sample

The corpus is built by turning assets into **standalone runnable code**. Do it as a loop with
your eyes open — never author blind:

1. Write **self-contained** code for the target dialect (no project-local imports; e.g. a
   Blender script must NOT `import infinigen` — inline what it needs).
2. `3dcode exec ./dir` → it must run **error-free AND produce a non-empty mesh**. Fix until green.
   (Blender Python is checked in Blender 5.0 **and** 5.1 — make it work in both.)
3. `3dcode render ./dir` → look at the render. Iterate until the shape/material is right.
4. `3dcode check ./dir` → if it's a near-duplicate of the corpus, drop or justify it.
5. `3dcode push ./dir --source <name>` → uploads + registers for review.

**Quality bar:** runs + builds geometry; grounded by a reference image and/or prompt; not a
near-duplicate; not absurdly long (anomaly-flagged); permissive `license`/`provenance` in meta.

## Code layout

```
threedcode/
  cli.py          # the `3dcode` entry (Typer)
  config.py storage.py(R2) schema.py hashing.py registry.py
  dialects/       # per-dialect adapters: blender_python, cadquery_dialect, build123d_dialect,
                  #   openscad_dialect, freecad_dialect (CadQuery & build123d = separate venvs)
  runners/        # runtime wrappers: blender_exec.py, blender_render.py, blender_render_mesh.py, freecad_exec.py
docs/contributing.md   # contributor onboarding
```

## Adding a new dialect

Add `threedcode/dialects/<name>_dialect.py` with `run(code_file) -> {status,...}` (exec) and
`render(code_file, renders_dir, blender_bin, mode) -> {status, images}` (render — export a mesh
and reuse `runners/blender_render_mesh.py` if the runtime can't render images itself). Wire it
into `dialects/__init__.py` (`run_dialect`/`render_dialect`) and add an optional extra in
`pyproject.toml`. Reference the proven renderers in the upstream
`infinigen/data_pipeline_operators/` (renderer.py, renderer_texture.py) for camera/lighting.

## Conventions

- Keep modules small and single-purpose. Match the existing style.
- Secrets live in `~/.config/3dcode/config.toml` / env — **never commit them**.
- Optional heavy deps (trimesh/imagehash, cadquery) are extras — base install stays light.
