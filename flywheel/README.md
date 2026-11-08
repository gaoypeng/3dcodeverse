# c3d-flywheel

The legacy reader of **3dcodeverse v0.0.x runs**.  Those releases ran a harness-side pipeline
(plan → vendor coding agent → build → gates → render → VLM judge → refine) and recorded every
run as a directory: `spec.json`, `record.json`, `src/` (a git repo, one commit per round),
`artifacts/`, `telemetry/cost.jsonl`, `events.jsonl`, optionally `deliverable/`.  The plugin
(v0.1+) writes none of that; this package reads what exists and turns it into a dataset, a
gallery, a cost audit or a hand-over folder.  It makes no model calls and needs no API key:
captioning, re-judging and judge calibration stayed in the v0.0.x releases.

Every record is read leniently (`c3d_flywheel.legacy`): fields later versions dropped
(`budget`, `backends`, the settings snapshot) are kept as data, the plan as a dict.

## Install

```
pip install -e harness            # the plugin package: contracts, conventions, workspace, proc
pip install -e flywheel           # this package; `pip install -e 'flywheel[parquet]'` for metadata.parquet / --pack
```

## Commands

A run is named by its directory, or by its slug under `--runs-dir` (default `./runs`).

```
c3d-flywheel show <run> [--section all|status|deliverable|evidence|cost] [--events N] [--runs-dir DIR]
c3d-flywheel status <run> [--events N] [--runs-dir DIR]          # = show --section status (works without record.json)
c3d-flywheel pick <run> [--round N] [--runs-dir DIR]             # choose a round (best judged, or N) → deliverable/ + selection.json
c3d-flywheel cost <run|root>... [--md FILE] [--recheck] [--limit N] [--per-run/--no-per-run] [--runs-dir DIR]
c3d-flywheel cost --runs-dir <root> [--recheck] [--md FILE]      # a whole battery
c3d-flywheel cost prices [--provider P] [--stale] [--unverified] [--days N]
c3d-flywheel gallery serve [ROOTS...] [--port N] [--host H] [--open/--no-open] [--reload] [--title T] [--runtime-js DIR]
c3d-flywheel gallery build [ROOTS...] [--out gallery.html] [--embed] [--title T] [--thumb-px N]
c3d-flywheel export <runs_root> <out_dir> [--min-score X] [--only-passed] [--include-unbuilt] [--captions-dir DIR] [--drop-duplicates] [--pack] [--tar-prefix P]
c3d-flywheel pairs <runs_root> <out.jsonl> [--min-delta X]
c3d-flywheel refine <runs_root> <out.jsonl> [--with-code]
```

A root is any tree of run directories (`runs/`, `eval/bench/out/<battery>/...`); nested battery
layouts (`cells/<prompt>/<arm>/run`) are found and named by their meaningful path segments.
`gallery serve` binds to loopback unless a host is typed; the GLB viewer uses the three.js
vendored in the plugin's `runtime_js/node_modules` when it is installed (no CDN).
