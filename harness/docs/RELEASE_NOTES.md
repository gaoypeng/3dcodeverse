# Release notes

Newest first.

## Skills — 2026-08-25

A routed, spec-conformant skill library whose reads we can measure. Baseline `42a5457` →
`HEAD`; six commits from five parallel waves (machinery, four authoring waves) plus this
verification pass. Full story and the authoring contract: **`docs/SKILLS.md`**.

**Ships behind `CV3D_SKILLS`, default OFF.** §A/B below says why, with the numbers.

### What landed

* `codeverse/skills/` — 14 bundles authored to the open Agent Skills spec
  (agentskills.io), a typed R1–R24 route table, an atime read probe, and numbers pinned to
  live constants. `3dcv skills list|show|validate|report`, `3dcv doctor --skills`.
* Routing is automatic and gate-driven: the previous round's gate findings outrank the
  standing sheets, which is the input no CLI's own skill loader can see.
* `RoundRecord.skills` records what was listed, surfaced and read deep, with the index and
  body token cost, so the price of the feature is auditable per round rather than asserted.

### Native loading, proven live

`pytest -m live tests/skills/test_live_discovery.py` — gemini-cli 0.53.0, codex 0.149.0,
claude-code 2.1.245 and agy 1.1.20 each discover a materialised bundle, open `SKILL.md`,
open `references/` and follow what they read. Two things this settled that had only been
argued: `Skill` was **absent** from claude-code's `ALLOWED_TOOLS` (it would have denied its
own skill tool — fixed, and `3dcv doctor --skills` now checks it), and gemini-cli's
`activate_skill` consent does not block us under `--approval-mode yolo`.

### Defects the verification tests found

| # | What | Where |
|---|---|---|
| V-1 | Three language contracts told the agent to overlap parts by 2–4 mm / "≥ 2 mm" while `connectivity.py` WARNs above `PENETRATION_WARN_M` = 2 mm and its own `fix_hint` says "overlap by ≤ 2 mm". Two worked examples welded at 3–4 mm. | `prompts/{blender,cadquery,threejs}/contract.md` |
| V-2 | Four bundles' `verified:` dates were YAML **dates**, not strings; the spec says metadata is string→string. `validate_bundle` now reports it instead of coercing. | 4 × `SKILL.md`, `skills/loader.py` |
| V-3 | `cv3d-opengl-pipeline` shipped as `evidence: measured` on 5 graded runs. Now `mixed`. | `cv3d-opengl-pipeline/SKILL.md` |
| V-4 | The api-agent index quoted whole 1024-char descriptions: **780 tokens** for a five-skill session against a 300-token budget. It quotes the first clause now (342 worst case). | `skills/prompting.py` |
| V-5 | The live discovery smoke had never run and could not: it looked for a binary named after the agent kind, but `gemini-cli` runs `gemini` and `claude-code` runs `claude`. | `tests/skills/test_live_discovery.py` |
| V-6 | `cv3d-cadquery-forms` still quoted the contract's old unbounded "≥ 2 mm" weld, and the cadquery worked example welded at 3 mm. | `cv3d-cadquery-forms/SKILL.md`, `prompts/cadquery/contract.md` |
| V-7 | A body naming one of our constants was not required to pin it; `cv3d-glsl-craft` quoted `DUPLICATE_DIFF` at 1e-4 with no claim row behind it. | `cv3d-glsl-craft`, `tests/skills/test_freshness.py` |
| **V-8** | **The read probe was measuring git, not the agent.** See below — the single most consequential finding of the pass. | `skills/telemetry.py`, `skills/materialize.py` |

### V-8: the read probe was measuring git

The design's headline differentiator was read telemetry: `atime > mtime` on
`references/*.md` means the agent read the body, because nothing scans `references/`.
**Measured, that is false twice over.**

1. `Workspace.changed_files` runs `git add -A -N` then `git diff --numstat` after *every*
   agent session to compute `files_changed`, and git reads each untracked file to diff it.
   On a real git workspace that alone flips all four files of every bundle to "read", with
   no agent involved. Reproduced in `test_git_diff_alone_trips_the_control`.
2. A negative control — one bundle whose description does not match the task, prompt "do
   not read any files, do not activate any skill" — had codex 0.149.0, claude-code
   2.1.245, agy 1.1.20 and gemini-cli 0.53.0 **all** open `SKILL.md` and `references/`.
   Each touched only the discovery root it owns, which independently confirms the mapping.

Left alone, this metric would have reported 100% forever and we would have believed it.
`materialize_skills` now writes one **never-routed, never-indexed control bundle** beside
the real ones. Nothing should open it; when something does, `SkillsUsage.control_read` is
set, `probe_trustworthy` is false, and `deep_read_rate` returns `None` rather than a
confident number. `3dcv skills report` excludes those sessions and says how many it
dropped. The control says when the probe is blind; it does not make it see.

### Read rate, on an ordinary build task

Five routed bundles in the workspace, an ordinary "build a dining chair in bpy" prompt,
nothing about skills in it: codex, claude-code and agy each came back with 5 of 5 bundles
surfaced and deep, 8,041 body tokens. Those runs predate the control, so treat the rate as
an upper bound — but the behavioural evidence under it is not ambiguous: codex's
`src/parts/common.py` came back with `WELD_OVERLAP = 0.001` and "1 mm weld overlap with
the seat", which is `cv3d-part-contact`'s number, not the prompt corpus's 2–5 mm.

### New standing tests

`tests/skills/` — 712 tests: 650 in the default suite, 58 `slow`, 4 `live`.

* **Contradiction**, library-wide, on the *pre-scale value* rather than the rendered text —
  "1 cm" and "0.01" metres are one tolerance in two units, and failing that pair would
  teach the next author to delete the claim.
* **Freshness** — every tool, gate kind, rubric criterion, constant, switch, sibling skill
  and cookbook section a bundle names must still exist in the shipped source.
* **Contract agreement** — a skill and its language contract cannot state different
  numbers, arbitrated by the gate constant rather than by either document.
* **Routing properties** — the real library over 5,376 sessions per track, every corpus
  finding kind, and 2,000 seeded random walks: determinism, the cap, ordering,
  explainability, and junk degrading to `[]`.
* **Spec compliance** re-derived from the raw bytes, plus the reference validator
  (`pip install skills-ref` → `agentskills`, now in the `dev` extra): 14/14 valid, and its
  `read-properties` agrees with our loader field for field.
* **Budget** re-measured against the shipped descriptions; **packaging** asserts a built
  wheel holds all 14 `SKILL.md`, all 14 `references/` and all 9 `_claims`.
* **Corpus** recomputes each bundle's claimed evidence from `bench/out`.

<!-- AB-RELNOTES -->

### Known open

Listed in `docs/SKILLS.md` §9. The one that matters most: **`contract.md` was fixed but the
wider prompt corpus still teaches 2–5 mm weld overlap** — `tracks/generate_static.j2`,
`assemble_static.j2`, `generate_static_part.j2`, `system/harness_contract.md` and three
cookbooks. That is a prompt-corpus change with its own measurement, and it is the most
likely reason a contact skill would fail to move the number it targets.

---

## Stability pass — 2026-08-24

Sign-off for the first release intended to be called **stable**. Five parallel waves hunted
the harness for defects, a sixth verified them, three fixed them, and this pass reconciled,
re-tested and landed the result.

Baseline `d5dc135` → `HEAD`. 46 commits, all of them defect fixes, their regression tests, or
this document.

**Verdict: fit to ship.** One known-open item, no crash / data-loss / wrong-result defect
left open, the whole offline suite green on the 3.13 development interpreter and on a
Python 3.10 clean clone with no credentials.

---

### 1. What was hunted

Six independent sweeps over the 54 files the parallel waves changed:

| Sweep | Scope | Raised |
|---|---|---|
| `CP` | crash / hang / data-loss paths | 7 |
| `RS` | resume, restart and recovery | 9 |
| `SM` | settings, profiles, budgets, CLI surface | 11 |
| `CG` | connectivity + contract gates (the scoring gates) | 5 |
| `CQ` | cross-cutting correctness and concurrency | 5 |
| `PORT` | packaging, install, portability, CI | 8 |
| | **total raised** | **45** |

Each was then reproduced independently before any fix was written.

**42 of 45 verified real. 3 were dismissed on the evidence** and are *not* defects:

- **RS-5** — `compare_backends --redo-status` re-using a finished workspace. The driver
  already rebuilds the cell; the reported "old score" came from reading a stale row, which
  is the real defect and is CG-5.
- **RS-7** — the wall-clock ceiling "resetting on every resume". It is persisted; the
  reporter measured a run whose budget had been raised.
- **SM-05** — `CV3D_PLAN_FEATURES` accepted and ignored. Half of this is real and is
  tracked as CQ-5 (the switch nothing reads); the "all six switches silently accepted"
  half is not — they are validated and rejected.

#### Verified defects by severity

| Severity | Count | Fixed | Open |
|---|---|---|---|
| crash | 8 | 8 | 0 |
| data-loss | 2 | 2 | 0 |
| wrong-result | 19 | 19 | 0 |
| hang | 2 | 2 | 0 |
| usability | 10 | 10 | 0 |
| style | 1 | 1 | 0 |
| **total** | **42** | **42** | **0** |

A further **9 defects were found by the smoke matrix and the claude-code sweep** while
exercising the tree rather than reading it (`SMOKE1`–`SMOKE5`, `CC-1`, `CC-2`, and two found
at sign-off). 8 fixed, 1 open. **50 fixed in total.**

---

### 2. What was fixed

Every fix below landed with a regression test that was **proved to fail without it** — the
source change stashed, the test run, the change restored. The 24 tests belonging to the wave
that had not yet landed were re-proved against `main` at sign-off, as a batch, after rebasing.

#### Crashes (8)

| ID | Defect | Commit |
|---|---|---|
| CP-1 | one non-UTF-8 byte from a worker killed the whole `ab_plan` A/B driver | `bc9a56f` |
| CP-2 | Anthropic structured output bypassed `strip_control_chars`; a model NUL reached subprocess argv | `ae34f38` |
| CP-3 / RS-6 | `3dcv status` died on the truncated last line a killed run leaves — the one run you would point it at | `20ea52e` |
| RS-2 | a stale or unreadable cached stage result bricked `3dcv resume` permanently | `92077aa` |
| RS-3 | one truncated line in `results.jsonl` made a killed battery unresumable *and* unreportable | `e5c9eed` |
| SM-07 | negative `max_in_flight` validated, `doctor` called it "fits", first model call died in a bare semaphore `ValueError` | `c7a2363` |
| PORT-5 | `flywheel export` without `--pack` hard-crashed on a core-only install *after* writing a partial dataset | `7e44f71` |

CP-3 and RS-6 were the same defect reported twice.

#### Data loss (2)

| ID | Defect | Commit |
|---|---|---|
| CP-5 | one directory-shaped path in a generation envelope discarded the whole answer instead of skipping the bad block | `0884aa7` |
| RS-1 | `write_json_atomic` shared one `.tmp` name per path: concurrent writers published truncated JSON and crashed mid-save | `ba428eb` |

#### Wrong results (19)

The gates that decide a score, and the accounting that decides what a run cost.

| ID | Defect | Commit |
|---|---|---|
| CG-1 | every ground-touching component counted as "supported", so a detached part passed as long as it reached the floor | `ff1a2b5` |
| CG-2 | interpenetration was never detected between non-watertight parts — `penetration_depth` returned `(0.0, 0.0)`, indistinguishable from "no overlap" | `3085862` |
| CG-3 | `grounded` was one-sided, so a model exported below `y=0` marked every part grounded and disabled the floating check entirely | `ab7f4f2` |
| CG-4 | a plan part swallowed a sibling whose name ends in a digit — a false "missing from the GLB" ERROR and a real 0.75 cap on a perfect artifact | `29209d9` |
| CG-5 | `ab_plan --report-only` averaged the arms table over stale re-run rows, so the summary contradicted its own verdict | `7eccf2f` |
| CQ-1 | two outage classifiers disagreed: the harness cell was dropped while the one-shot cell took a hard 0.0 for the identical provider error | `c225757` |
| CQ-2 | parallel asset generation raced on one fixed temp filename, silently disabling the three.js asset gate for the losing asset | `63ba756` |
| CQ-3 / SM-01 | `--max-in-flight 8` silently discarded when the shell exported a cap: the preflight reserved 16, the children held 64 | `c7a2363`, `d4544f8` |
| CQ-4 | `3dcv bench` never adopted the outage classifier — a 60-minute 503 storm was reported as model latency (12.8× inflation) | `51e8a4f` |
| CP-6 / RS-8 | gallery and flywheel export reported "0 runs" as a success for compare / A-B battery directories | `971ad1a`, `3df7bc6` |
| RS-4 | the brief disk cache key ignored `spec.references`, so an `--image` run silently reused a brief generated without the image | `afe4415` |
| RS-9 | `metadata.parquet` silently dropped the complexity columns the exporter documents and writes | `bd2746e` |
| SM-02 | `pool_budget()` charged a sibling running UNLIMITED as holding 0, and treated a negative cap as free headroom | `e0d487d` |
| SM-03 | one `CV3D_JUDGE__*` variable disabled the profile's entire judge block — `CV3D_PROFILE=quality` judged at n=1 while advertising n=3 | `d2b4e17` |
| SM-04 | the two YAML config files were merged with `dict.update()`, so naming a section discarded every sibling key the user had set | `c05c7aa` |
| PORT-2 | the offline suite needed Gemini keys, so CI and every fresh clone were red | `139fbbc` |
| PORT-3 | `doctor` never checked `moderngl` or `cadquery` — all-green on an install where the graphics and CAD tracks could not run | `616fa46` |
| PORT-6 | `setup.sh` verified the wrong installation: the closing doctor ran whatever was first on `PATH`, ignoring `--python` | `5c79e09` |
| CC-1 | every claude-code `--model` ALIAS was recorded as the CLI's housekeeping model — see §5 | `1d27275` |
| CC-2 | `claude-opus-5[1m]`, the id the DEFAULT claude-code arm serves, had no price row — see §5 | `12fca2e` |

`CQ-3`/`SM-01` and `CP-6`/`RS-8` were each one defect found twice by different sweeps; both
landed twice and were reconciled at sign-off (§6).

#### Hangs (2)

| ID | Defect | Commit |
|---|---|---|
| CP-4 | `run_subprocess` ignored its own `timeout_s` when the child left a detached descendant holding the pipes — unbounded, on every build and render path | `3ba9532` |
| PORT-1 | the documented "pure-python subset" pytest line silently re-enabled the live tests: a command-line `-m` **replaces** `addopts`, it does not add to it | `616fa46` |

#### Usability (10) and style (1)

| ID | Defect | Commit |
|---|---|---|
| CP-7 | a stale `.git/index.lock` made every resume fail with a bare "exit status 128" | `2c32d4c` |
| SM-06 | `make --reference --no-run` ran the paid reference pass before honouring `--no-run` | `b743074` |
| SM-08 | `--texture` accepted on scene and graphics tracks, where the finalise pass could only ever raise and be swallowed | `2c32d4c` |
| SM-09 | `3dcv render` ignored `render.width` / `height` / `scene_width` / `scene_height` and hardcoded 768×768 | `ed60341` |
| SM-10 | an unknown profile name escaped as a raw `ValueError` traceback from every command, `3dcv doctor` included | `b743074` |
| SM-11 | `--max-usd` accepted negative values; the spec was written and the run then died unrecoverably on its first call | `2c32d4c` |
| CQ-5 | `plan_features.py` — "the one env switch every plan-loop change reads" — was read by zero callers | `d24b3e7` |
| PORT-4 | a wheel shipped no scene starter tree; one package-data glob pointed at a directory that does not exist | `2a13c37` |
| PORT-7 | the TL;DR install path died with "No module named pip"; pip and venv were missing from the prerequisites | `5c79e09` |
| PORT-8 | building left an untracked `harness/build/`, and the install docs' test counts were stale | `c3612eb` |

#### Found by running the thing, not reading it (9)

| ID | Defect | Status |
|---|---|---|
| SMOKE1 | `3dcv resume` had no terminal-state guard: resuming a finished run re-ran a **billed** plan stage and rewrote `status` from `passed` to `planning`, leaving the run stuck | fixed `3c5bd37` |
| SMOKE2 | `record.json` stores 29 absolute host paths; the intended repair only rebased *relative* paths, i.e. was a no-op against every record the harness writes | fixed `133f56b` + `7e5209b` |
| SMOKE3 | `agy:<bare model id>` could not launch — `build_argv` appended `--model` but never an `--effort`, which agy 1.1.19 requires for every gemini model | fixed `caefc43` |
| SMOKE4 | `CodingAgent.available()` is a false green light | **OPEN — see §4** |
| SMOKE5 | duplicate of SM-10 | fixed `b743074` |
| CC-1 | claude-code model attribution | fixed `1d27275`, §5 |
| CC-2 | `claude-opus-5[1m]` pricing | fixed `12fca2e`, §5 |
| SIGN-1 | the SMOKE2 fix reached `3dcv status` but **not** `3dcv show`, the command actually reported: `layout_cmd.print_evidence` still printed the raw stored path, so a moved run still showed a contact sheet that is not there next to an `object.glb` that resolves | fixed `7e5209b` |
| SIGN-2 | `3dcv doctor` reported **FAIL** on a correct base install: `scipy`, `networkx` and `pyarrow` were checked but never attributed to an extra, so a fresh clone was told its install was broken and given a remedy that could not clear the row | fixed `7321e9e` |

`SIGN-1` and `SIGN-2` were found at sign-off by re-running the failed smoke rows and by the
clean-clone check respectively — neither was visible from the test suite, which was green.

A third sign-off finding, `SIGN-3`, was a **test** defect rather than a product one: the
`unique_tmp` regression test asserted that 8 short-lived threads get 8 distinct idents.
`threading.get_ident()` only promises uniqueness among *living* threads, so on the Python
3.10 floor the test failed `1 != 8` while passing on 3.13 — CI's 3.10 matrix job would have
gone red on push. The threads are now held at a barrier (`7942926`); no product change.

---

### 3. Notable behaviour changes

Things an operator will notice, beyond a bug no longer happening.

- **`3dcv resume` refuses a finished run.** A run that ended `passed` / `plateau` (and
  `budget` when no cap was raised) is refused with a message pointing at `3dcv status`.
  `--force` re-enters it, and says what that costs.
- **`3dcv make --profile <unknown>` and a bad `CV3D_PROFILE`** now exit **2** with
  `error: bad configuration: …` instead of a traceback. Any invalid settings value takes
  the same path.
- **`--max-usd` / `--max-minutes` reject negatives** before the workspace is created. `0`
  stays legal.
- **`--texture` is refused on the scene and graphics tracks**; `--profile quality` silently
  drops the texture pass there instead of recording one it could never run.
- **`3dcv render` honours the configured render size**, and gained `--width` / `--height`.
- **A battery directory is now a valid argument** to `flywheel export` and `gallery build`:
  the runs are discovered in all three layouts instead of reported as "0 runs, success".
- **`ab_plan` pins both children to the cap the admission check reserved**, and refuses a
  `--variant-env` that only sets switches nothing reads.
- **`3dcv bench` gained `--redo-status`**, so the new `infra_failed` classification is
  actionable the way `compare_backends`' already was.
- **`--reference --no-run` warns that it is about to spend ~$0.15** before it does. The
  paid pass still runs: it writes the grounded spec back, which is the artifact the flag
  asks for.

---

### 4. Known open

**One item. No crash, data-loss or wrong-result defect is open.**

#### SMOKE4 — `CodingAgent.available()` is a false green light

`available()` returned `(True, "ok")` for `agy:gemini-3.7-flash`, which could not launch at
all, and for `codex:gpt-5.1-codex`, which the ChatGPT-account backend rejects with
`HTTP 400 — model is not supported`. Its contract says "binary found / auth present / model
known"; it does not verify the last clause.

**Why it is not fixed.** The missing check cannot be done offline. Both ids are well-formed
for their vendor, so any prefix or vendor check passes them and catches neither case. There
is no allowlist of agy- or codex-served models in the repo, and inventing one would reject
legitimately new models on the day they ship — this repo added `gpt-5.6-sol`,
`claude-opus-5` and `gemini-3.7-flash` within the last weeks. The only check that catches
them is a live one-turn launch per CLI arm, which spends budget and cannot carry an offline
regression test. A docstring-only edit was deliberately **not** landed: it would look like a
fix while leaving the false green light in place.

The premise is also narrower than reported: the bench preflight
(`compare_backends.py:374`) never calls `available()` — it probes API models only and maps
subscription-CLI targets to `""`. The only callers are each agent's own `run()`.

**Workaround.** Use an effort-qualified agy id (`agy:gemini-3.7-flash-low`); as of
`caefc43` a bare id is resolved to one automatically, so this is now belt-and-braces. For
codex, use the tiers this repo actually runs — `gpt-5.6-sol` / `-terra` / `-luna`. Treat a
first-cell `HTTP 400 model is not supported` as a configuration error, not a scored zero.

**Recommended follow-up (budgeted, not this pass):** a `--launch-check` in the bench
preflight that runs one trivial turn per CLI arm and classifies a backend model rejection as
a config error; and narrowing `available()`'s docstring to what it actually verifies.

#### Pre-existing flakes (not from this pass, not fixed)

`tests/orchestrator_tracks/test_cost_latency.py::test_a_model_outage_escalates_the_asset_instead_of_losing_it`
and `tests/orchestrator_tracks/test_prompts_assets.py::test_scene_templates_render_and_asset_stage_with_blender`
fail when that directory is run **alone** and pass inside a full-suite run. Reproduced 3/3
at `d5dc135` with no fixes applied, so they predate this pass. Both pass in every full-suite
run reported here. Worth a separate look at their fan-out / ordering assumptions.

---

### 5. The claude-code arms, and Sonnet 5

Two defects here, both fixed, both affecting **recorded cost and model attribution** rather
than generated artifacts.

#### CC-1 — every `--model` ALIAS recorded the wrong model

`claude -p` bills **two** models per session: the work model, plus a small background model
the CLI uses for its own housekeeping. claude 2.1.243 lists the **auxiliary** one first in
`modelUsage`. `usage_from_envelope` did:

```python
served = list(modelUsage.keys())
if model not in served:
    usage.model = served[0]
```

which fires exactly when `--model` is an alias. So `claude-code:sonnet`, `claude-code:opus`
and the default arm all recorded the model that did **0.9% of the tokens**: in the probe,
`claude-sonnet-5` did the work at $0.02284 while `claude-haiku-4-5-20251001` took the label
for a $0.000955 side-call. Full ids passed verbatim were unaffected — the bug bit only the
spelling the arms are actually configured with.

It reached `CallCost.model` and `OneShotResult.usage`, so **a sonnet-vs-default comparison
was labelled haiku-vs-haiku**.

Fixed by `primary_served_model()`: pick the `modelUsage` entry whose token counts match the
envelope's own top-level `usage` block (which reports the main conversation only), falling
back to the dearest entry; an id we passed verbatim is kept as-is.

Verified live at sign-off:

| arm | recorded `usage.model` before | after | price lookup |
|---|---|---|---|
| `claude-code:sonnet` | `claude-haiku-4-5-20251001` | `claude-sonnet-5` | exact |
| `claude-code:opus` | `claude-haiku-4-5-20251001` | `claude-opus-5` | exact |
| default (no `--model`) | `claude-haiku-4-5-20251001` | `claude-opus-5[1m]` | exact |

#### CC-2 — the default arm's model had no price

`claude-opus-5[1m]` is the 1M-context Opus 5 variant the **default** claude-code arm serves.
`[1m]` is not a version suffix, so it cannot prefix-match `claude-opus-5`, and the id had no
row: `match=unknown`, `estimate_cost` → `$0.00`, `3dcv cost prices` calling the default arm
unknown — while a recorded cell had billed **$1.218038** under exactly that key.

The ledger dollar was never wrong (claude-code is in `PROVIDER_PRICED_BACKENDS`, so
`price_call` returns the CLI's own reported cost), but every estimate and every price-hygiene
report was. Note the ordering trap: **fixing CC-1 alone would have broken the suite**, because
it makes the default arm start recording this id and
`test_every_recorded_model_id_resolves_to_a_price` then fails.

Added `Price(5.00, 25.00, 0.50, cache_write=6.25)`, provenance `provider-cost / inferred /
checked 2026-08-24`. Not guessed: the probe billed `costUSD 0.034620` for `in=2, out=4,
1h-cache-write=3451`, which is exactly `2×5.00 + 4×25.00 + 3451×10.00` per 1M — the standard
Opus 5 rates with the 1h cache write at 2× input (not modelled, as for every other Anthropic
row). A test pins that a bracketed suffix still cannot borrow a sibling's price:
`claude-sonnet-5[1m]` stays `unknown`.

#### Sonnet 5 pricing — checked, and correct

The `claude-sonnet-5` row is `$2.00 / $10.00` per 1M (cached `$0.20`, cache write `$2.50`).
This was re-verified at sign-off against the live model documentation on 2026-08-24, because
a widely-cached price table still describes `$2 / $10` as an *introductory* rate expiring
2026-08-31 with `$3 / $15` resuming after. **That is stale.** The current published rate for
Claude Sonnet 5 is $2 / input MTok and $10 / output MTok, unqualified — the row and its
provenance note ("the launch rate became the standard rate; the 2026-09-01 increase was
cancelled") are right, and no 1 September repricing is pending. Opus 5 confirmed at $5 / $25
and Haiku 4.5 at $1 / $5 on the same check.

#### Still true about the CLI arms

`codex:gpt-5.1-codex` is rejected by a ChatGPT-account backend; the tiers this repo runs are
`gpt-5.6-sol` / `-terra` / `-luna`. `available()` will not warn you (§4).

---

### 6. Reconciliation notes for reviewers

Three things worth knowing about how this landed.

1. **An entire wave had not landed.** Twelve commits fixing 16 defect ids (CP-2, CP-6, CP-7,
   RS-3, RS-8, SM-01, SM-04, SM-08, SM-11, CG-3, CQ-1, CQ-4, PORT-5, PORT-6, PORT-7, SMOKE2,
   SMOKE5) existed only on a detached worktree branched from `d5dc135`. They were rebased
   onto `main` at sign-off, six conflicts resolved by hand, and all 24 of their regression
   tests re-proved to fail against `main` before landing.

2. **Three defects were fixed twice**, by different waves, and were reconciled rather than
   double-landed:
   - **CQ-3 / SM-01** — `main`'s fix already assigned the cap and popped the nested spelling.
     Only the genuinely new part of the second fix was kept: `--variant-env` now refuses
     either spelling of the cap.
   - **CP-6 / RS-8** — one wave raised a loud error, the other **discovered** the runs.
     Discovery won and subsumes the error: `3dcv cost --runs-dir` already accepts a battery
     root, so refusing would have made the exporters the only commands that did not. It also
     fixes the gallery half, which the error-only fix did not touch. The anti-silence guard
     is kept for the case discovery cannot rescue — a battery-shaped directory holding no
     records. The superseded test was rewritten to assert the stronger behaviour.
   - **SM-10 / SMOKE5** — kept `main`'s typed exit code **2**; the duplicate test was
     retained for its extra assertion (no workspace is created for a rejected flag) and
     aligned to that contract.

3. **`runtime_js/node_modules` must stay untracked.** It was tracked by accident (a
   `git add -A` inside a worktree, where it is a symlink) and untracked again in `6275853`;
   one wave's cherry-pick then replaced the real directory with a self-referential symlink
   and destroyed it, and it was restored with `npm ci`. `git ls-files
   harness/runtime_js/node_modules` prints nothing at this commit, and `57d2e8a` extends
   `.gitignore` to cover the symlink form so it cannot come back the same way.

---

### 7. Verification

#### Test suite

| Run | Interpreter | Command | Result |
|---|---|---|---|
| Whole offline suite | 3.13.9 | `python -m pytest -q --timeout=900` (`addopts = -m "not live"`) | **2257 passed, 6 skipped, 24 deselected** (512 s) |
| Lint | 3.13.9 | `ruff check .` (target-version py310) | **All checks passed** |
| Clean clone, CI's own command | **3.10.21** | `pytest tests/core tests/models tests/judges tests/orchestrator_tracks tests/flywheel_cli tests/bench_prompts tests/cost -q -m "not live and not blender and not node"`, every credential unset and `$HOME` redirected | **1295 passed, 13 skipped, 16 deselected** |

The deselected are `live`; **no live test was run and no provider was billed by the suite.**
The skips are data-dependent (recorded runs a fresh clone does not have).

#### Clean clone

`git clone` → fresh `python3.10 -m venv` → `pip install -e harness` → `3dcv doctor` →
offline subset. Python **3.10.21**, the `requires-python` floor.

- install: clean, `3dcodeverse-0.1.0` plus 50 dependencies
- `import codeverse` resolves inside the clone
- `3dcv doctor`: exit 0. `python deps` **WARN** — `14/23 importable`, remedy
  `pip install -e 'harness[cad,flywheel,graphics,mcp,mesh,urdf]'` (this row was **FAIL**
  before `7321e9e`; see SIGN-2). `three` / `puppeteer` / `chrome webgl` FAIL as expected —
  `npm install` in `runtime_js` is a separate documented step and is not part of the pip
  install.
- offline subset: green, with **no credentials in the environment**, which is what a CI
  runner has (PORT-2).

#### Smoke matrix

55 cells. **47 ok, 0 failed, 8 skipped.** All four cells that failed on the hunt were
re-run at sign-off and now pass; the remaining 43 ok cells are carried forward from the hunt
and were not re-executed individually — the full offline suite covers them.

Every skip was blocked by the **concurrency budget**, not by a provider outage: the house
rule is that the sum of every harness process's `CV3D_MAX_IN_FLIGHT` stays at or under 64,
and `pool_budget()` read headroom 0 for the whole hunt window. The health gate itself
*passed* both models (`gemini-3.7-flash` 3/4 keys, `gemini-3.1-pro-preview` 3/4). Nothing in
the fix or sign-off passes called a model except the four subscription-CLI probes noted below.

| # | Area | Cell | Result |
|---|---|---|---|
| 1 | env | worktree isolation + `node_modules` symlink + import resolves inside | ok |
| 2 | lint | `ruff check .` | ok |
| 3 | env | concurrency preflight (`pool_budget()`) | ok |
| 4 | track × language | `make` static_object × blender (`--no-run`) | ok |
| 5 | track × language | `make` static_object × cadquery (`--no-run`) | ok |
| 6 | track × language | `make` static_object × threejs (`--no-run`) | ok |
| 7 | track × language | `make` articulated_object × urdf_blender (`--no-run`) | ok |
| 8 | track × language | `make` scene × scene_threejs (`--no-run`) | ok |
| 9 | track × language | `make` graphics × glsl_shader (`--no-run`) | ok |
| 10 | track × language | `make` graphics × opengl_python (`--no-run`) | ok |
| 11 | track × language | live 1-round run, all 7 pairs | skipped — pool headroom 0 |
| 12 | profile | `economy` resolves | ok |
| 13 | profile | `balanced` resolves | ok |
| 14 | profile | `quality` resolves (distinct dials: judge n=3, 2 candidates, $8) | ok |
| 15 | profile | unknown name rejected cleanly | **ok** (was failed — SM-10) |
| 16 | CLI | `tools list` (18 tools, track-scoped) | ok |
| 17 | CLI | `tools measure` / `tools check_connectivity --json-out` | ok |
| 18 | CLI | `make` | ok |
| 19 | CLI | `resume` on a completed run | **ok** (was failed — SMOKE1) |
| 20 | CLI | `resume` a `--no-run` spec | skipped — pool headroom 0 |
| 21 | CLI | `status` | ok |
| 22 | CLI | `render` default + `--mode wire` (real GPU) | ok |
| 23 | CLI | `judge` | skipped — pool headroom 0 |
| 24 | CLI | `mcp` — real `ClientSession` over stdio, 13 tools, `call_tool` | ok |
| 25 | CLI | `show` | ok |
| 26 | CLI | record path portability (`show` after relocation) | **ok** (was failed — SMOKE2 / SIGN-1) |
| 27 | CLI | `migrate-runs` (idempotent on second pass) | ok |
| 28 | CLI | `flywheel export --pack --drop-duplicates` | ok |
| 29 | CLI | `flywheel pairs` | ok |
| 30 | CLI | `flywheel caption` | skipped — pool headroom 0 |
| 31 | CLI | `flywheel index` | ok |
| 32 | CLI | `flywheel dedupe` | ok |
| 33 | CLI | `flywheel gallery` | ok |
| 34 | CLI | `gallery build --embed` | ok |
| 35 | CLI | `gallery serve` + HTTP 200 | ok |
| 36 | CLI | `bench run` (resume path, 0 model calls) | ok |
| 37 | CLI | `bench report` | ok |
| 38 | CLI | `cost <slug>` (ledger reconciles to −0.00%) | ok |
| 39 | CLI | `cost --runs-dir <battery>` | ok |
| 40 | CLI | `cost prices` / `--stale` | ok |
| 41 | CLI | `cost cache` | ok |
| 42 | CLI | `cost profiles` | ok |
| 43 | CLI | `cost estimate` | ok |
| 44 | CLI | `doctor` | ok |
| 45 | CLI | `doctor --live` (one `pong`, $0.00023) | ok |
| 46 | CLI | `texture show` | ok |
| 47 | CLI | `texture pass` | skipped — pool headroom 0 |
| 48 | CLI | `texture scene-pack` | skipped — pool headroom 0 |
| 49 | backend | `claude-code:sonnet` 1-turn (subscription) | ok |
| 50 | backend | `codex:gpt-5.6-sol` 1-turn (subscription) | ok |
| 51 | backend | `agy:gemini-3.7-flash-low` 1-turn (subscription) | ok |
| 52 | backend | `agy:<bare model id>` | **ok** (was failed — SMOKE3, re-verified live: `pong`) |
| 53 | backend | `gemini-cli` 1-turn | skipped — pool headroom 0 (`available()` ok) |
| 54 | backend | `api-agent` 1-turn | skipped — pool headroom 0 (`available()` ok) |
| 55 | models | health gate probe, both models | ok |

Sign-off re-runs of rows 15, 19, 26 and 52 used four subscription-CLI or offline calls and
did not touch the Gemini key pool.

#### House rules

- All work done in `git worktree`s; the main tree was never edited while a battery was
  running, and `git -C /home/yipeng/3dcodeverse status --short` is empty at this commit.
- `git ls-files harness/runtime_js/node_modules` prints nothing.
- Python floor 3.10 held, and now actually exercised (SIGN-3 was a 3.10-only failure).
- Nothing in the fix or sign-off passes added in-flight load to the shared key pool.
