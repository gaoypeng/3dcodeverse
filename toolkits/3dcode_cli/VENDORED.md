# Vendored copy

This is the complete [gaoypeng/3dcode_toolkit](https://github.com/gaoypeng/3dcode_toolkit) repository
(everything except `.git/` and `curation_tools/`, which lives in `../curation/`), copied here so the whole data
toolchain is maintained in one place.

* upstream: https://github.com/gaoypeng/3dcode_toolkit
* copied at: 2026-08-28
* files: 25 here + 49 curation scripts in `../curation/` = the upstream repo's 74 tracked files

Edits made here do not flow upstream automatically. Decide which copy is canonical before changing either.

## Differences from upstream

* 2026-09-21 — the console command is `3dcode-data` here (upstream: `3dcode`).  The harness in
  `../../harness` took `3dcode` as its short command, and two distributions cannot install the same
  script name.  Unchanged: the distribution name `3dcode` (so `pip install "3dcode[dedup]"` still
  works), the python package `threedcode`, and the credentials file `~/.config/3dcode/config.toml`.
