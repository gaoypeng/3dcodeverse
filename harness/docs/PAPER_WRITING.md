# Paper writing: everything measured, and where it came from

Everything a paper about this harness would need in one file: what the system is, what was
built, every experiment with its setup and numbers, the null results, the mechanism findings,
the methodology, and a claim → evidence index.  Written 2026-09-03 over the work of
2026-08-25 → 09-03; each number names the run directory that produced it.

The same facts live, in their own idiom, in `DECISIONS.md` (D36–D52, the decision log),
`COST.md` (§23, §29, §30, the cost measurements), `EVAL.md` (§8–§9, the protocol and the
noise floor) and `ARCHITECTURE.md`.  Tools referenced throughout:
`bench/prompts/{compare_v4,articulated_v2}.yaml` (batteries), `bench/ab_plan.py` (paired
A/B with an A/A mode), `bench/compare_backends.py` (harness vs one-shot),
`bench/paired_compare.py` (paired statistics), `bench/plan_stage_bench.py` +
`bench/plan_stage_report.py` (the loss-event channel, data in `bench/data/plan_stage/`),
`3dcv flywheel refine` (the transition corpus) and `toolkits/llamafactory/` (its training
format).

## 0. The system, in one paragraph

A request becomes an iterated loop: **plan → generate code → build → deterministic gates →
render → VLM judge → refine**, for N rounds under a wall-clock and dollar budget, delivering
the best round and recording every round.  The generator is a coding agent writing real
source (Blender `bpy`, CadQuery, three.js, GLSL, or a URDF robot plus a `bpy` link builder);
the gates are program analysis, not model calls (lint, connectivity, dimensional contract,
and — on the articulated track — a joint sweep that collides every link pair in every sampled
pose, plus a planned-motion check); the judge is a VLM scoring rendered views against a
rubric.  The recorded rounds are themselves the training corpus the project is built to
produce.  The **articulated track** is the hardest and the one this ledger studies: its
artefact has kinematics a static object does not, and it was the harness's weakest track at
the start of this work — 5 of 14 prompts died at the planner, and 213 of 214 gate errors in a
full battery were `joint_sweep`.

## 0.1 Contributions a paper could claim

1. **A measured advantage for an iterated, gated, judged loop over one-shot generation**, on
   two tracks, with paired confidence intervals and a judge-free corroboration (§3).
2. **An instrument section that most agent papers do not have**: an A/A calibration that
   states the noise floor and the number of prompts needed to resolve a given effect, and
   four honest null results reported as null (§2, §4).
3. **A cost model for agentic code loops**, with per-turn and per-round token accounting, the
   quadratic-in-turns growth, and the cache term that decides which savings are real (§6).
4. **A defect class specific to tool-using agents** — a tool's negative *verdict* delivered as
   a protocol *error* — with its retry cost, a 507× token amplification when the result
   carries an image, and the before/after measurement of the fix (§5.2).
5. **A cheap methodology for changes whose effect the judge cannot see**: isolate the stage,
   count the loss event, run n in the hundreds (§5.1).
6. **Coupled-mechanism support for URDF articulation** with a mechanism-level readout (§5.3).
7. **A refine corpus**: (judge issues + gate findings + instructions) → code diff → score
   delta, exported in the finetune pipeline's own format (§7).

## 0.2 Evaluation-protocol work that predates the measurements

These are prerequisites, and several are results in their own right (decision log D36–D43):

* **Tie-breaking on an even `n_samples`** followed a fixed direction, which made the
  `n_samples=2` fixed evaluator *strictly harsher* than n=1 or n=3 and not comparable with
  either; ties now follow the representative sample and the broken ids are recorded (D36).
* **`judge_prompt_hash`** (role prompt + rig rules + wire schema) is stored in every score,
  so a rubric revision cannot be silently compared across (D37).
* **`harness_git_sha`** was empty in every record because the probe looked for a `.git` that
  does not exist at that level (D38).
* **Failures of the arm are zeros; failures of the provider are dropped** (D41).  Before
  this, an outage scored a one-shot arm 0 while the harness arm hit by the same outage was
  dropped — biasing every comparison in the harness's favour.  Extended in this work with
  three more provider modes (§5.4).
* **Storm-degraded cells** (budget stop, ≤1 completed round, ≥40 min, money left) are
  flagged and excluded from an `all −degraded` row (D40); the first 40-prompt battery ran
  inside an all-day 503 storm and 37 of 40 harness cells hit the wall clock with 0–2 rounds.
* **A lint that raises kills the round**: every Python lint now parses through `safe_parse`
  (D43).
* **The offline suite is every test directory**: the removed CI job listed 7 of 24 (D39).

---

## 1. The battery and the arms

`bench/prompts/articulated_v2.yaml` — 14 prompts, 4 medium / 10 hard: architect lamp, tool
chest, camera tripod, dutch door, rolltop desk, folding workbench, parallel clamp, sash
window, grand piano, umbrella, casement window, step ladder, draw-leaf table, scissor mirror.
Three of them (umbrella, scissor mirror, folding workbench) are one-input coupled mechanisms
and are the lowest scorers throughout.  The static battery is `compare_v4.yaml`, 40 prompts.

Arms, as the drivers name them: `harness:<agent>:<model>` (the loop),
`oneshot:<model>` (one call, one answer), `oneshot+repair:<model>` (one call plus one repair
call on the build error).  The generator moved from an in-process `api-agent` to
`gemini-cli:gemini-3.7-flash` upstream on 2026-08-27; comparisons that cross that boundary
are marked as confounded wherever they appear below.

## 2. The instrument, and why it is the first result

Everything below is judged by a **fixed evaluator**: the delivered GLB re-rendered on a
14-view rig and scored by `gemini:gemini-3.1-pro-preview` with `n_samples=3` against the
`articulated_v1` rubric.  Arms are paired per prompt.  `--pin-plan` runs the planner once
and seeds both arms, removing the planner's spread from the difference.

**A/A calibration (2026-09-02, `bench/out/aa_articulated`).**  Two *identical* arms on
`articulated_v2` (14 prompts, generator `gemini-cli:gemini-3.7-flash`, 3 rounds, 60 min per
cell, plan pinned).  12 pairs survived (2 lost at the pinned plan to provider read timeouts
and redone; 0 budget-exhausted):

| quantity | value |
|---|--:|
| mean Δ (identical arms) | −0.046 |
| median Δ | −0.048 |
| paired sd | 0.225 |
| SE | 0.065 |
| 2 SE band | ±0.130 |
| sign | 3 up / 8 down (p = 0.227) |
| separated from noise | **no** |
| pairs needed to resolve ±0.02 | **~506** |

The rig printed **"revert"** from two runs of the same code.  That is the number every
other result on this battery has to be read against: **on 12–14 paired prompts the judge
mean cannot decide a single-switch change.**  It also explains the four null results in §4
without appealing to the changes themselves.

**The consequence: two channels.**
* The *judge channel* (mean score, paired) is reserved for comparisons big enough to clear
  ±0.13 — harness vs one-shot, not switch vs switch.
* The *loss-event channel* counts the events that destroy a run or a round: a plan that
  never validated, a tool call the model retried, a pose the mechanism cannot reach, a round
  with gate errors left.  These are cheap enough to measure at n in the hundreds, and they
  are what actually moved.

## 3. What the harness is worth (judge channel, where it resolves)

| comparison | battery | n pairs | means | Δ | 95 % CI | verdict |
|---|---|--:|---|--:|---|---|
| harness − one-shot + repair | `compare_v4_calm` (static, 40) | 40 | 0.633 vs 0.487 | **+0.146** | [+0.077, +0.215] | supported |
| harness − one-shot | `compare_v4_calm` | 40 | 0.633 vs 0.232 | **+0.401** | [+0.297, +0.505] | supported |
| harness − one-shot + repair | `compare_art_v2` (articulated, 14) | 14 | 0.309 vs 0.316 | −0.007 | [−0.288, +0.273] | parity — 5/14 harness runs died at the planner |
| harness − one-shot + repair | `compare_art_v3` (after the planner repair) | 14 | 0.468 vs 0.316 | **+0.152** | [+0.061, +0.243] | supported (sign p 0.039) |
| harness − one-shot + repair | `compare_art_v4` (3 harness arms) | 13–14 | — | **+0.19 … +0.33** | all exclude 0 | supported in every arm |

Judge-free, same runs: on the static battery the harness builds 100 % of the time with 1.5
gate errors on average (75 % of cells at zero) against one-shot+repair's 100 % / 17.3 / 12 %
and one-shot's 50 % / 17.9 / 0 %.

The articulated parity → +0.152 transition is itself a result: the harness's articulated
advantage was hidden by **planner mortality**, not by the loop's quality.

## 4. The four null results (judge channel, where it does not resolve)

Every one of these is a real change, measured, that could not be resolved on this battery.
They are recorded so nobody re-runs them expecting a different answer.

| lever | battery / method | n pairs | Δ | 2 SE | sign | disposition |
|---|---|--:|--:|---|---|---|
| **Plan-time geometry re-ask** (`CV3D_PLAN_GEOMETRY`, D49) | `compare_art_v4` pf vs pf0 | 14 | +0.064 | ±0.25 | 6/6/2 | ships **OFF** |
| **Pro planner** (`gemini-3.1-pro` vs `3.7-flash`) | `compare_art_v4` pp vs pf | 13 | +0.090 | ±0.23 | 9/2/2 | keep flash: 2–10× plan cost, 5/14 cells lost to provider limits |
| **Deterministic repairs** (`CV3D_ART_REPAIRS`) | `ab_repairs`, plan pinned | 12 | −0.039 | ±0.105 | 2/5/5 | ships **OFF**; the axis flip fired in 1 of 12 cells |
| **Fewer turns** (`CV3D_FEWER_TURNS`) | `ab_fewer_turns`, plan pinned | 14 | +0.015 | ±0.126 | 6/5/3 | ships **OFF**; −12 % tool calls, $/cell unchanged |
| **Lean prompt** (`CV3D_LEAN_PROMPT`) | `wave2_lean`, plan pinned | 12 | +0.030 | ±0.076 | 8/3/1 | ships **OFF**; −45.7 % generate prompt chars, $3.51 vs $3.66 per cell; code removed 2026-09-03 in review (D52); the numbers stand, the second prompt path does not. |

Two of these carry a second, non-score reading worth keeping:

* The geometry re-ask **fired in 7 of 14 cells** and retro-fires on 8 of 14 recorded
  compare_art_v3 plans (mean score 0.362 for the plans it complains about vs 0.610 for the
  clean ones) — it identifies bad plans, it just does not fix enough of them to show.
* `CV3D_FEWER_TURNS` and `CV3D_LEAN_PROMPT` both cut tokens without cutting dollars.  The
  reason is in §6: the token growth is quadratic in turns but lands in the cache, so the
  bill is not where the tokens are.

## 5. What did move: loss events

### 5.1 Planner mortality (the one significant single-switch result)

**Method.** `bench/plan_stage_bench.py` runs the **plan stage alone** — no build, no
judge — at ≈$0.03 and ≈85 s per call, so 280 calls per arm is affordable where 14 judged
cells is not.  Both arms run in the same window against the same provider.  The 560 rows
are in the tree (`bench/data/plan_stage/*.jsonl`, `bench/plan_stage_report.py` prints the
table): a paper cites the p-value, so the data it comes from is committed.

**Failure class, measured first.** 200 calls over two code trees showed one class: the
planner writes **one top-level part** and hangs its joints off links it never lists, and
then reproduces that answer through **both** in-context re-asks.  The two re-asks shipped
the week before changed nothing (29/30 vs 28/30 valid, same window) — the model conditions
on its own broken output.

**The change** (`CV3D_PLAN_RESTART`, ON with a kill switch): on that failure the
conversation is rebuilt from the original request plus the rule the answer broke, instead
of appending the broken plan and a complaint.

| arm | plan calls | valid | `PlanningError` | provider | failure rate | 95 % CI |
|---|--:|--:|--:|--:|--:|---|
| restart ON | 280 | 274 | 2 | 4 | **0.7 %** | [0.2 %, 2.6 %] |
| restart OFF | 280 | 264 | 13 | 3 | **4.7 %** | [2.8 %, 7.9 %] |

Fisher exact two-sided **p = 0.0067**.  The restart fires on **14 %** of calls and recovers
**39 of 40**.  Cost per call is unchanged ($0.0345 vs $0.0337) and wall time is slightly
lower (82 s vs 86 s): one extra sample replaces two doomed re-asks.  Failures under OFF are
spread over six prompts, so this is not one pathological request.

### 5.2 Tool calls the model retried, and the token blow-up behind them

**The fault.** `spatial/mcp_server.py` returned `is_error = not obs.ok`, and `obs.ok` was
the tool's **verdict**.  So a joint sweep that ran correctly and reported a penetration
reached the model as a *broken call*, which it retried.  Recorded rate, 224 sessions:

| tool | calls | reported as an error | the harness's own gate, for comparison |
|---|--:|--:|---|
| `joint_sweep` | 1 445 | **62 %** | the round's `joint_sweep` gate fails 57 of 299 rounds (19 %) |
| `build` | 2 144 | 23 % | — (a build that does not compile *is* the answer) |
| `check_contract` | 895 | 17 % | — |
| `check_connectivity` | 923 | 14 % | — |

**The amplifier.**  `observation_content` attaches PNGs as MCP image parts.  gemini-cli's
*error* path stringifies the whole result (`safeJsonStringify(rawResponseParts)`), so a
275 kB articulation sheet arrived as **~366 k characters of base64 text ≈ 261 k prompt
tokens**, against **~516 tokens** as an inline image on the success path — a ~507× blow-up
that also escapes the vendor's own 40 000-character truncation (it fires only for a
single-text-part result).  Recorded per-request prompt jumps match the prediction
(+260 421, +256 907, +258 997, +257 840, +260 364, +261 477).  About **10 % of requests
carried ~225 k uncacheable tokens, 67 % of the entire uncached bill.**

This also explains an anomaly that looked like a caching defect: gemini-cli's uncached
tokens per request were flat at ~30 k regardless of session length, while the deleted
in-process agent converged to ~5.6 k.  It was not the CLI — a 260 k blob pushes the session
past the vendor's history-compaction thresholds, which rewrite the head and invalidate the
implicit-cache prefix.  Non-blob requests already cached as well as the old agent
(4 946 vs 5 639 uncached tokens per turn).  **Nothing about settings, tool ordering or
`GEMINI.md` needed to change** — a negative finding that saved an A/B budget.

**The change.**  `Observation` carries `failed` ("the tool could not run") beside `ok`
(the verdict); MCP `is_error` is `failed` alone; every affected tool's text now leads with
its verdict (`JOINT SWEEP: FAIL — penetration 3.0 mm > tolerance 1.0 mm`); the MCP boundary
bounds one result's text characters, image count and encoded image bytes.

**Measured after** (`bench/out/wave2_lean`, the same battery and config as the A/A above).
Both columns come from `bench/session_stats.py`, which is in the repo with its own tests:

| per generator request | before (`aa_articulated`) | after (`wave2_lean`) |
|---|--:|--:|
| sessions / requests | 111 / 2 594 | 102 / 2 722 |
| MCP tool calls reported as errors | 26.6 % (482 / 1 814) | **1.3 %** (25 / 1 863) |
| cache hit | 68.8 % | **89.2 %** |
| uncached prompt tokens | 38 390 | **13 945** (−64 %) |
| generator $ per round | mean 1.900, median 1.572 | **mean 1.242, median 0.950** (−35 % / −40 %) |

(The after column first read 1.4 % / 13 736 / \$0.967 over "108 sessions"; those counts
double-counted the `run/telemetry/trajectories` symlink.  Rates barely move, the counts do;
the script deduplicates and a test plants the symlink.)

The residual 1.3 % are genuine `Observation.error` cases (`joint_sweep` before a build,
`check_*` with no readable GLB).  **This is the largest effect found in the whole effort,
and it has no signature in the judge mean at n = 14.**

### 5.3 Coupled mechanisms

**The fault.**  `<mimic>` was a lint WARN reading "ignored by the harness", and the sweep
drove every movable joint independently.  An umbrella was therefore posed with each rib at
a different angle — a state the mechanism cannot reach — and the judge scored that, while
the fix hints told the agent to move links that are not free to move.  The three
lowest-scoring prompts on the battery (umbrella, scissor mirror, folding workbench) are all
one-input mechanisms.

**The change.**  `Joint` carries a `Mimic(joint, multiplier, offset)` parsed from `<mimic>`;
`load_urdf` refuses a dangling name, a fixed driver, a zero multiplier and a cycle;
`resolve_q` expands one input through the chain and `fk` applies it (a value passed for a
driven joint is ignored); `pose_samples` drives `independent_joints()` only; the motion
check probes a driven joint **through its driver**; `JointPlan.mimic` makes the coupling a
planner decision and the skeleton writes it; the lint checks it instead of dismissing it.

**Measured** (`wave2_lean`, both arms): the planner and agent declared couplings in **8 of
14 prompts** (umbrella 6 joints, folding workbench 4, step ladder 4, scissor mirror 3,
rolltop desk 3, one each on casement window / grand piano / architect lamp), and the sampled
poses now drive **1–2 joints** where the same prompts previously produced **5–6-joint**
random combinations.  The score effect on the three coupled prompts is inside the noise band
at n = 2 per side (umbrella 0.35/0.65 → 0.54/0.64, scissor 0.46/0.54 → 0.49/0.60, workbench
0.91/0.60 → 0.60/0.60); the **pose count is the readout that resolves.**

### 5.4 Provider-failure accounting

Three failure modes were reclassified from "the arm scored 0" to "the provider failed, drop
and redo" (D41): a Gemini stream exceeding its attempt budget, a 504 "Deadline expired",
and a `finish_reason=PROHIBITED_CONTENT` (the content filter tripping mid-JSON on a
furniture plan).  Without this the batteries above would have carried zeros that measure the
weather.  Related: gemini-cli gets a third rotated attempt on a quota 429 (one battery lost
4 of 10 cells to two per-minute 429s in a row while other keys were answering), and
`render_glb` retries once on a transient failure (two cells had lost their in-loop judge to
a render timeout under load).

## 6. Where the money goes (the cost model)

Measured over 133 recorded generation rounds, before the fix in §5.2: **~4.0 M input tokens
per round** (75 % cached), 62 k output + 36 k thoughts, 33 tool calls, **≈$1.39**.  At list
prices the input side is ~68 % of the bill.

Per-turn telemetry from 1 063 in-process sessions (30 401 turns) gives
`input(t) = 12 364 + 1 822·t` and a session total ∝ n^1.60 — pure history re-send, not a few
huge tool results (the largest single per-turn increment ever recorded is 25 026 tokens; the
top 1 % of increments carry 7 % of appended tokens).  Halving the turns by exact truncation
of those transcripts cuts tokens 68 % but dollars only 55 %, **because the quadratic part
lands in the 10×-cheaper cache**.  That is why both turn/prompt-shape levers in §4 moved
tokens and not dollars, and why the §5.2 fix — which removes *uncached* tokens — is the one
that moved the bill.

## 7. The corpus the loop produces

`3dcv flywheel refine` exports every refine round as the transition it was: the previous
round's judge issues and improvement plan, the gate ERROR findings with their fix hints, the
instruction lines the sessions were actually handed, the unified `src/` diff between the two
recorded commits, and the score delta with an improved/unchanged/regressed label.  On the
recorded corpus, re-exported through the shipped CLI on 2026-09-03 (`bench/out`, 21
batteries): **254 rows from 87 runs across 13 batteries**, the only drops being 54
cross-battery symlink aliases (`duplicate_run`), median diff 26.2 kB over 2 changed files,
**112 improved / 81 unchanged / 59 regressed / 2 unscored**, median gain on an improved
round +0.197, 119 rows carrying gate ERRORs.  Both sides come from the round's own recorded
commit, not from `HEAD`.  (The same export read 205 rows on 2026-09-02; the corpus grows
with every battery, so the selector is the date.)

`toolkits/llamafactory/build_refine_sft.py` turns the improved transitions into the message
shape the finetune pipeline consumes: **112 samples** (92 articulated, 20 static; median
answer 25.4 kB, median brief 28.2 kB), answered with the files the round produced in the
`=== FILE: path ===` envelope.  This is the one thing the harness produces that a one-shot corpus cannot: *given
a judged, gated object and a list of what is wrong with it, write the corrected files.*

The pre-existing `flywheel/pairs.py` saw 57 % fewer transitions and carried neither the
instructions nor a diff.

## 8. Conclusions

1. **The harness's advantage over one-shot generation is real and resolvable**: +0.146 on 40
   static prompts, +0.152 to +0.33 on 14 articulated ones, with judge-free corroboration
   (gate errors 1.5 vs 17.3; build rate 100 % vs 50 %).
2. **A 12–14-prompt battery cannot resolve a single-switch change.**  The A/A puts the noise
   floor at ±0.13 with a paired sd of 0.225 and asks for ~506 pairs to resolve ±0.02.  Four
   plausible levers landed inside that band; none of them is thereby shown to do nothing.
3. **The changes that mattered were found by counting loss events, not by scoring artefacts.**
   Planner mortality 4.7 % → 0.7 % (p = 0.0067); retried tool calls 26.6 % → 1.3 %; uncached
   tokens −64 %; dollars per round −38 %; sampled poses of a coupled mechanism from 5–6
   joints to 1–2.  Every one of these is invisible to the judge mean at this n.
4. **The biggest single defect was in the harness's own plumbing**, not in the model or the
   prompt: one line mapping a tool's verdict to a protocol error cost a quarter of all tool
   calls to retries and two thirds of the uncached token bill.  It was found by reading
   recorded tool statistics against the harness's own gate outcomes.
5. **Cost intuitions about agent loops need the cache term.**  Tokens grow ∝ n^1.6 in turns
   while dollars grow ∝ n^1.2–1.3; cutting turns or trimming prompts moves the first and not
   the second.  Removing *uncacheable* payload moves the bill.
6. **Secondary, uncontrolled:** the paired sd of a battery on the fixed code is 0.131 against
   the A/A's 0.225 on the old code (~172 vs ~506 pairs for ±0.02).  Different runs and
   different arms, so it is an observation — but a retry storm is variance, and the direction
   is the one the fix predicts.

## 9. What a paper can claim from this, and what it cannot

**Can:**
* The harness beats one-shot and one-shot+repair on both tracks, with paired CIs and a
  judge-free corroboration that does not depend on the VLM.
* A measured account of *why* an agentic 3D loop is expensive (history re-send, cache
  behaviour, tool-call retries) with per-request and per-round numbers.
* A significant, cheap fix to planner mortality, and the method that made it measurable:
  isolate the stage, measure the loss event, run n in the hundreds.
* Coupled-mechanism support with a mechanism-level readout (poses per mechanism), not a
  score claim.
* An honest instrument section: the A/A calibration, the n_for_power arithmetic, and four
  null results reported as null.

**Cannot:**
* Attribute any of the four §4 levers a score effect.
* Credit the §5.2 fix with a score improvement — it is a cost and a retry result; the score
  on the same battery moved inside noise.
* Compare across the api-agent → gemini-cli generator change (the static regression in the
  wave-2 battery is confounded by exactly that, plus ~140 upstream commits and a rubric
  wording change), or across judge rubric revisions.
* Treat the mimic score deltas as evidence: n = 2 per side.

## 9.1 Claim → evidence index

| claim | number | where the data is | caveat |
|---|---|---|---|
| harness > one-shot+repair, static | +0.146 [+0.077, +0.215], n=40 | `bench/out/compare_v4_calm` | calm provider window; the storm run of the same battery gives +0.028 with a CI crossing 0 |
| harness > one-shot, static | +0.401 [+0.297, +0.505] | same | — |
| harness > one-shot+repair, articulated | +0.152 [+0.061, +0.243], n=14 | `compare_art_v3` | after the planner repair; before it, parity |
| harness > one-shot+repair, articulated, three arms | +0.19 … +0.33 | `compare_art_v4_{pf,pf0,pp}` merged with `compare_art_v2` one-shot rows | one-shot rows come from an earlier run of the same battery/judge |
| judge-free advantage | 1.5 vs 17.3 gate errors; 100 % vs 50 % build | `compare_v4_calm` | — |
| noise floor of the battery | paired sd 0.225, 2 SE ±0.130, ~506 pairs for ±0.02 | `aa_articulated` | identical arms |
| geometry re-ask | +0.064 ±0.25, fired 7/14 | `compare_art_v4_pf` vs `pf0` | — |
| pro vs flash planner | +0.090 ±0.23 | `compare_art_v4_pp` vs `pf` | 5/14 pro cells lost to provider limits |
| deterministic repairs | −0.039 ±0.105, fired 1/12 | `ab_repairs` | plan pinned |
| fewer turns | +0.015 ±0.126, −12 % tool calls, $/cell flat | `ab_fewer_turns` | plan pinned |
| lean prompt | +0.030 ±0.076, $3.51 vs $3.66 | `wave2_lean` | plan pinned |
| planner mortality | 4.7 % → 0.7 %, Fisher p = 0.0067, n = 560 calls | `local/out/plandeg_restart_{on,off}.jsonl` | plan stage only, both arms same window |
| tool calls reported as errors | 26.6 % → 1.3 % | `aa_articulated` vs `wave2_lean` stats envelopes | same battery/config, different weather |
| cache hit | 69 % → 89 % | same | same |
| uncached tokens per request | 38 390 → 13 945 | same | same |
| generator $ per round | median 1.572 → 0.950 | round records of both runs | same |
| base64 amplification | 261 k tokens vs 516 for one 275 kB sheet | measured on a recorded blob + vendor bundle | arithmetic + matching recorded prompt jumps |
| token growth in turns | input(t) = 12 364 + 1 822·t; total ∝ n^1.60; dollars ∝ n^1.19–1.34 | 1 063 in-process sessions, 30 401 turns | the in-process agent, since gemini-cli emits no per-turn usage |
| coupled mechanisms declared | 8 of 14 prompts; poses drive 1–2 joints (was 5–6) | `wave2_lean` URDFs and round records | — |
| refine corpus | 254 transitions, 112 SFT samples (2026-09-03) | `3dcv flywheel refine`, `toolkits/llamafactory/build_refine_sft.py` over `bench/out` | corpus grows with every battery |

## 9.2 Open questions

* **Everything in §4 is unresolved, not refuted.**  Resolving ±0.02 on this battery needs
  ~172–506 paired prompts depending on the code; a paper that wants those effects has to pay
  for the prompts or find a judge-free readout for each.
* **The judge is one model.**  No cross-judge check has been run (it needs a non-Gemini key).
  `judge_prompt_hash` makes the comparison auditable but does not remove the dependence.
* **The mimic result is mechanical, not scored.**  A battery of coupled mechanisms (say 10
  prompts, all one-input) would let the pose count and the score be read together.
* **The static regression in wave 2 is confounded** by the api-agent → gemini-cli change; a
  clean static before/after on one generator has not been run.
* **The refine corpus has not been trained on.**  112 samples is a probe, not a training set;
  the interesting experiment is whether a model finetuned on refine transitions repairs
  better than one finetuned on one-shot pairs.
* **Planner mortality is now 0.7 %, not 0.**  The residue is two calls in 276 that still
  failed after a restart; both died at the validation cap, which the 2026-09-03 change
  (a restart no longer spends a re-ask slot) addresses without being measured yet.
* **The restart trigger was narrowed after the readout.**  The measured arm restarted on any
  dangling link reference; it now also requires a collapsed plan, which is the class the
  200-call survey found.  A same-window re-measurement (narrow vs the wide trigger, 280
  calls each) is what decides whether §5.1's number carries over unchanged.

## 9.3 The switches this work added, and their state

Every one is read at call time and registered in `tracks/plan_features.LIVE_SWITCHES`, which
a test enforces by grepping the tree; a switch nothing reads once produced "keep, mean delta
+0.344" on two byte-identical arms, which is why the registry exists.

| switch | what it does | default | why |
|---|---|---|---|
| `CV3D_PLAN_GEOMETRY` | plan-time geometry re-ask (attachment gap, hinge pivot, swept collision) | **off** | +0.064 ±0.25, no measurable gain (§4) |
| `CV3D_PLAN_RESTART` | re-sample a collapsed plan (one top-level part **and** dangling links) from the original request | **on**, kill switch | 4.7 % → 0.7 % planner mortality, p = 0.0067 (§5.1); trigger narrowed 2026-09-03, re-measurement in flight |
| `CV3D_ART_REPAIRS` | axis flip on a reversed joint + buried-link check | **off** | −0.039 ±0.105, fired 1/12 (§4) |
| `CV3D_LEAN_PROMPT` | drop duplicated contract/tool cards, select cookbook chapters, focus the refine prompt | **off** | +0.030 ±0.076, no cost saving (§4); **removed from the tree 2026-09-03**; last carried on `ziyao/articulated-wave-2` before commit `554b52b`. |
| `CV3D_FEWER_TURNS` (pre-existing) | fold gate checks into build, inline refine files | **off** | +0.015 ±0.126, dollars flat (§4) |
| `CV3D_AXIS_REPAIR` (upstream) | deterministic axis rewrite from the measured motion | on | upstream's, kept |

Not switched, because they are bug fixes rather than levers: the MCP verdict/failed split
(§5.2), `<mimic>` support (§5.3), the provider-failure markers and retries (§5.4), the
truncation and degenerate-plan re-asks around the planner (§5.1).

## 10. Reproduction

```bash
cd harness
# the instrument
python bench/ab_plan.py --prompts bench/prompts/articulated_v2.yaml --aa --pin-plan \
    --generator gemini-cli:gemini-3.7-flash --judge gemini:gemini-3.1-pro-preview \
    --n-samples 3 --rounds 3 --out bench/out/aa_articulated
# a switch A/B (generation-side switches only; plan-side is refused for --pin-plan)
python bench/ab_plan.py --prompts bench/prompts/articulated_v2.yaml \
    --variant-env CV3D_FEWER_TURNS=1 --pin-plan --out bench/out/fewer_turns
# harness vs one-shot
python bench/compare_backends.py --prompts bench/prompts/articulated_v2.yaml \
    --arms harness:gemini-cli:gemini-3.7-flash,oneshot+repair:gemini:gemini-3.7-flash \
    --judge gemini:gemini-3.1-pro-preview --judge-samples 3 --out bench/out/art
# the loss-event channel: the plan stage alone, one row per call
python bench/plan_stage_bench.py --tree . --label restart_on --reps 20 \
    --out bench/data/plan_stage/restart_on.jsonl --env CV3D_PLAN_RESTART=1
python bench/plan_stage_report.py bench/data/plan_stage/*.jsonl
# the corpus the loop produces
3dcv flywheel refine bench/out refine.jsonl --with-code
python toolkits/llamafactory/build_refine_sft.py refine.jsonl --out refine_sft.jsonl
```

Recorded runs referenced above (not committed): `bench/out/{compare_v4_calm, compare_art_v2,
compare_art_v3, compare_art_v4_pf, compare_art_v4_pf0, compare_art_v4_pp, ab_repairs,
ab_fewer_turns, aa_articulated, wave2_lean, wave2_static}`.
