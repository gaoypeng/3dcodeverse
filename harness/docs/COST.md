# Cost audit — where the money actually goes

Measured on **61 recorded runs** (`runs/e2e_*` ×5, `eval/bench/out/{static_v1_flash,
articulated_v1_flash,graphics_v1_flash,scenes_v1_flash}` ×46, the 10 cells of
`eval/bench/out/compare_v1_live2`) on 2026-08-23.  Every number below comes from the
recorded telemetry, not from an estimate: `record.json`, `events.jsonl` and the
per-call `usage` rows inside `trajectories/*/transcript.jsonl`.

Reproduce:

```
3dcode cost show runs eval/bench/out/static_v1_flash …          # console
3dcode cost show runs eval/bench/out/* --md docs/cost_report.md # full tables
3dcode cost show runs --recheck                            # re-price with today's table
3dcode cost prices [--unverified]                          # the price table + provenance
```

The audit is `codeverse3d/addons/costreport/audit.py`; it reconstructs a per-call ledger from
old runs (`codeverse3d/cost/reconstruct.py`), so it works on every run recorded so
far — no re-instrumentation needed.  `eval/bench/out/compare_v1_full` (a partial,
superseded compare battery) is excluded.

> **Re-run 2026-08-23 (wave 3).**  Every figure in Part I below was recomputed
> after a de-duplication bug in the reconstruction was fixed (§6): a retried agent
> session is recorded as `<label>.a2` while the `generate.done` event that
> summarises the job carries the plain label, so the event was counted a *second*
> time on 14 runs.  The reconstructed total drops **$89.88 → $86.30 (−4.0 %)** and
> lands $1.22 above the runs' own `record.total_usage` ($85.08) instead of $4.80.
> The waste analysis (§5) and the round economics are unchanged — they are built
> from round records, not from those events.

---

## 1. Headline

| | |
|---|---|
| total spend | **$86.30** over 61 runs (26.4 h of run time, 7,544 model calls) |
| per run | $1.41 (median $0.95) |
| **per passing artifact** | **$2.40** — 36 of 61 runs passed |
| input tokens | 278.6M, of which **80% are cache reads** |
| output tokens | 3.94M (+0.66M thoughts) |
| generation : judging : planning | **89.8% : 7.5% : 0.8%** |
| identified waste | **$27.35 (32%)** — see §5 |
| recorded vs measured | `record.total_usage` says $85.08; the ledger finds **$86.30** (§6) |
| re-priced with the corrected table | **$89.10** (+3.2%, §7) |

(The pass count and the $ per passing artifact are this audit's.  Since 2026-09-22 a run is
not passed or failed — it runs its fixed rounds and a pick hands one over — so `3dcode cost`
reports $ per run and no longer counts passes.)

**The money is generation, not judging.**  Nine dollars in ten are the coding
agent writing and rewriting code; the judge is a rounding error next to it
(7.2%), the planner is noise (0.8%).  Any cost programme that starts with "use a
cheaper judge" is optimising 7% of the bill — and, as §8 shows, would lose money.

---

## 2. Per stage

| key | USD | share | calls | input | cached | output | $/1k tok | model time |
|---|---|---|---|---|---|---|---|---|
| plan | $0.7234 | 0.8% | 56 | (not recorded) | – | – | – | 0.0 h |
| assets (scene) | $5.61 | 6.5% | 587 | 13.24M | 61% | 236.8k | $0.00041 | 1.0 h |
| env (scene) | $0.7762 | 0.9% | 92 | 1.96M | 62% | 19.1k | $0.00039 | 0.1 h |
| zones (scene) | $4.49 | 5.2% | 451 | 15.55M | 76% | 140.8k | $0.00028 | 0.7 h |
| baseline | $25.06 | 29.0% | 1,814 | 86.76M | 85% | 1.39M | $0.00028 | 4.0 h |
| repair | $1.31 | 1.5% | 152 | 5.08M | 79% | 49.9k | $0.00026 | 0.2 h |
| **refine** | **$40.28** | **46.7%** | 4,231 | 154.50M | 80% | 1.82M | $0.00026 | 8.4 h |
| judge | $6.48 | 7.5% | 126 | 1.33M | 8% | 212.1k | $0.00383 | 1.2 h |
| texture | $1.10 | 1.3% | 16 | (event only) | – | – | – | 0.0 h |
| other / unattributed | $0.4776 | 0.6% | 19 | 235.2k | 10% | 67.3k | $0.00158 | 0.0 h |

`skeleton`, `assemble`, `gates` and `render` cost **$0.00**: they are
deterministic harness work.  Blender build 0.13 s, gates ~2 s, an 8-view GPU
render ~1.5 s (that battery's rig; 14 views since D47) — the "cheap first" law is holding.

## 3. Per role, track, backend, model

| role | USD | share | calls | $/1k tokens |
|---|---|---|---|---|
| generator | $77.52 | 89.8% | 7,327 | $0.00028 |
| judge | $6.48 | 7.5% | 126 | $0.00383 |
| planner | $0.72 | 0.8% | 56 | – |
| image (texturing) | $0.61 | 0.7% | 5 | – |
| other (event-only) | $0.97 | 1.1% | 30 | – |

| track | runs | USD | $/run | passed | **$/passing artifact** | median rounds |
|---|---|---|---|---|---|---|
| static_object | 37 | $54.45 | $1.47 | 23 | **$2.37** | 2 |
| articulated_object | 13 | $17.34 | $1.33 | 8 | **$2.17** | 2 |
| scene | 5 | $13.92 | $2.78 | **0** | **∞** | 2 |
| graphics | 6 | $0.59 | $0.10 | 5 | **$0.12** | 1 |

The graphics track is **18x cheaper per passing artifact** than the object
tracks (one single-shot generation, compiler feedback, no tool loop).  The scene
track has not produced a single passing artifact in this data set at $2.83/run.

| model | USD | share | calls | cached | note |
|---|---|---|---|---|---|
| gemini-3.7-flash | $74.13 | 85.9% | 7,400 | 80% | generator + planner + cheap judge |
| gemini-3.1-pro-preview | $6.23 | 7.2% | 109 | 7% | default judge |
| gpt-5.6-sol (codex) | $2.26 | 2.6% | 3 | 92% | one telescope run |
| claude-fable-5 (claude-code) | $1.90 | 2.2% | 2 | 0% | recorded as `claude-haiku-4-5` — see §7 |
| (event-only rows) | $1.78 | 2.1% | 30 | – | plan / texture / pairwise: cost recorded, tokens not |

## 4. Tokens, caching and the clock

**80% of every prompt is context we have already sent.**  259.8M of the agent
loop's input tokens correspond to only 13.3M tokens of *final* context — the
transcript is re-sent **19.5x** on average (median 22 model turns per session,
p90 52).  Implicit caching absorbs most of that: cache reads cost $16.98, and
the same traffic with a 0% hit rate would have cost **$239.12 instead of $86.30**.
Caching is already saving 64% of the bill.

The uncached remainder is still ~$40 of that traffic, and the bill grows
quadratically with session length (the 262 agent sessions that kept a per-turn
transcript — $69.1 of the $81.1 generator spend):

| model turns | USD | share of agent spend |
|---|---|---|
| 1–10 | $23.70 | 34.3% |
| 11–20 | $18.26 | 26.4% |
| 21–30 | $11.76 | 17.0% |
| 31–40 | $7.65 | 11.1% |
| 41–50 | $4.81 | 7.0% |
| 51–60 | $2.90 | 4.2% |

**39.3% of the agent bill is spent from turn 20 onwards.**  33 sessions ran ≥50
model turns for $19.57 — and 19 of those 33 ended `exit_reason="budget"`, i.e.
the money bought a session that was cut off rather than finished.

**Measured cache behaviour (live, gemini-3.7-flash, 2026-08-23)** — the same
content in two orders, 6 calls each, one key:

| arrangement | prompt | cached tokens | $/call |
|---|---|---|---|
| stable prefix first, volatile tail last | 17.8k | 0 → **12,265 (69%)** | $0.0144 → **$0.0052** |
| volatile first, same stable text after | 17.8k | **0 on every call** | $0.0134 |
| stable first, fresh 29.7k corpus | 29.7k | 0 → **24,549 (83%)**, one miss in four | $0.0223 → **$0.0057** |
| volatile first, fresh 29.7k corpus | 29.7k | **0 on all four calls** | $0.0223 |

Same tokens, same model, **2.6–3.9x the price** for the wrong order (and the
implicit cache is best-effort: one call in four missed even in the good arm).  A second
experiment sized the cache floor (fresh prefix per size, 3 calls each):

| repeated prefix | cached on call 2–3 |
|---|---|
| 2.8k tokens | 0 |
| 5.4k tokens | 0 |
| 8.2k tokens | 4,080 once, then 0 (unreliable) |
| 11.1k tokens | 0 |
| 16.8k tokens | 12,264 (73%) |
| 28.2k tokens | 24,548 (87%) |

Implicit caching works in ~4,088-token blocks and only becomes reliable above
~12–16k tokens of *identical* prefix.  Key rotation does **not** break it (all 22
keys share the cache).

A third experiment used a **real judge payload** — `build_judge_messages` on
`e2e_chair_blender` r01 (9,454 tokens: system + brief + plan + gates + three
montages), sent to `gemini-3.1-pro-preview`:

| judge call | cached | $/call |
|---|---|---|
| first send | 0 | $0.0333 |
| identical resend (×2) | **4,079 (43%)** | **$0.0259 (−22%)** |
| same content, montages shuffled — what `n_samples > 1` does today (×3) | **0** | $0.0333 |
| next round (same images, changed gate/measurement text) | **0** | $0.0333 |

So: the judge's stable head (system + rubric + defect checklist ≈ 3.5k) is just
under one cache block, and every per-round or per-sample difference sits early
enough to void it.  Consequences:

* the agent loop already sits far above the floor — that is why it gets 80%;
* **judge and plan calls get almost nothing** (7% measured over 112 real
  verdicts).  Two cheap fixes exist (§9 #11–12), worth ~$1 on this data set —
  the real lever on judging is *how many verdicts*, not *how cheap each prompt*.

**Clock.**  26.4 h of run time against 35.5 h of model time (fan-out rounds run
sessions in parallel).  On the 37 runs with no parallel fan-out the split is 9.04 h wall vs 7.94 h
model — **88% of the clock is waiting for a model**, 12% is the harness
(build + gates + render + git).  Cost and latency have the same owner.

## 5. Where a dollar bought nothing

| waste | n | USD | share |
|---|---|---|---|
| regression — a round scored **below** the best and was discarded | 19 | $14.23 | 16.5% |
| post-budget — a round completed after the budget was already gone | 9 | $8.76 | 10.1% |
| zero-delta round — score moved < 0.005 | 4 | $1.68 | 1.9% |
| unpromoted judge — a verdict on a round that never became best | 23 | $1.37 | 1.6% |
| repair loop that never converged (run did not pass) | 4 | $1.31 | 1.5% |
| **total** | 59 | **$27.35** | **31.7%** |

> **2026-09-22 — three of these kinds are gone.**  "Regression", "zero-delta round" and
> "unpromoted judge" measured money against the IN-RUN best round, and the owner removed
> the in-run best with the judgement stops: a run is now the baseline + `--rounds` refine
> rounds, each built on the round before it, every round is kept, and which one to hand
> over is picked after the run (`addons/select`).  A round that scored lower is a candidate
> for that pick, not waste, so `3dcode cost` no longer reports those kinds (nor the
> `cost.round` `regression` / `zero_delta` flags).  "Repair loop that never converged" is
> now repair money spent in a round that still did not build; post-budget stays.

Concrete examples:

* `tool_med_hand_drill` r01: **$2.48** to go from 0.576 to **0.560**.
* `plant_hard_bonsai` r01: **$2.36** to go from 0.384 to **0.134**, then r02
  spent another $1.29 *after* the budget was exhausted.
* `tool_hard_bench_vise` r02: **$2.02** spent on a round that started after
  `budget.exceeded` had already fired.

Marginal economics of a refine round:

| round | n | USD | median $ | rounds that improved | total score gained | **$ per score point** |
|---|---|---|---|---|---|---|
| r00 baseline | 54 | $35.58 | $0.557 | – | – | – |
| r01 | 33 | $20.99 | $0.475 | 22/33 | +4.26 | **$4.93** |
| r02 | 16 | $9.18 | $0.383 | 7/16 | +1.35 | **$6.80** |
| r03 | 4 | $2.98 | $0.569 | 1/4 | +0.09 | **$33.49** |

The first refine round is the good buy.  r02 is a coin flip; r03 costs 7x r01
per point of score.

## 6. $1.22 of spend is invisible to `record.total_usage` (was reported as $4.80)

**Corrected 2026-08-23.**  The first pass of this section said *"14 of 61 runs,
$4.80 (5.6% of all spend)"*.  Most of that was a bug in the **reconstruction**,
not money the record lost.  `cost/reconstruct.py` de-duplicates a
`generate.done` event against the agent sessions that already accounted for the
same work, and it keyed that on the session's own label — but a retried session
records itself as `<label>.a2` (and a wrap-up as `<label>.wrapup`) while the
event carries the plain job label.  The retry's dollars therefore sat in a
different bucket, the job's pool ran dry, and the event was added on top of the
sessions.  `_base_label()` now strips the attempt suffix before the lookup, and a
test pins the three worst runs.

Re-running the audit with the fixed code
(`3dcode cost show <paths> --md <out>`, or the script in the wave-3 scratchpad):

| | before the fix | after |
|---|---|---|
| reconstructed total | $89.88 | **$86.30** |
| Σ `record.total_usage` | $85.08 | $85.08 |
| runs whose ledger exceeds their record by > $0.005 | 14 | **4** |
| **off-record total** | $4.80 | **$1.22** |

What is left is real, and it is one thing plus small change:

| run | record | ledger | off-record | why |
|---|---|---|---|---|
| `furn_hard_rolltop_desk` | $1.2165 | $1.8727 | **$0.6562** | four post-hoc texture passes, run after the record was written |
| `cmp_easy_stool/harness_api-agent…` | $0.3434 | $0.6118 | $0.2685 | compare cell: the fixed evaluator's judge is outside the harness run's total |
| `cmp_easy_stool/harness_gemini-cli…` | $0.8810 | $1.0437 | $0.1627 | idem |
| `e2e_bench_threejs` | $0.6840 | $0.8186 | $0.1347 | a gemini-cli session re-priced from its own `stdout.json` stats (§7 #1) |

The other 19 runs with a positive gap are all under half a cent.  The three worst
offenders of the old table reconcile to the **cent** once the double count is
gone — `art_hard_door_handle` $3.0690 vs $3.0690, `tool_med_hand_drill` $4.0210 vs
$4.0210, `art_easy_laptop` $1.1223 vs $1.1223 (reconstructed rows excluding the
synthetic `residual` filler) — which is the independent recount in the wave-2
hand-off (`waste-and-accounting/MEASUREMENTS.md` §1, "$0.00 genuinely
off-record") reproduced by the shipped code.
`tests/cost/test_reconstruct.py` pins all three plus a synthetic retry.

**A second recount reported 25 runs / $4.65; it reproduces exactly when the RE-PRICED ledger is compared with the recorded record.total_usage (the original $4.80 / 14 runs used the old price table and a higher per-run threshold). Both numbers are correct under their own method; the re-priced 25-run figure is the current one.**  That figure
is not reproducible against this tree: with the current code the same 61 runs give
28 runs with any positive gap / $4.797 *before* the de-duplication fix and 23 /
$1.222 after, on either the recorded or the re-priced price table.  The numbers
above are what `audit_runs()` prints today, and the script that prints them is in
the scratchpad; anyone who can reproduce 25/$4.37 should say which run set it used.

**Both holes are now closed at the source, so this section is about history:**
the live ledger (§12) writes one row per call at the time of the call, the
`BudgetGuard` charges the planner, aborted rounds, retried sessions and the
texture pass through the same door, and a post-hoc pass joins the run's ledger
(`3dcode texture pass` opens it with `create=False`).  A run recorded from now on
cannot have an off-record dollar; the reconstruction path exists for the 61 runs
recorded before it.

## 7. Price hygiene (checked 2026-08-23)

Every row of `models/pricing.PRICES` carries its provenance — source, `checked` date and a
status of `verified` / `inferred` / `unverified` (one table since 2026-09-22; the parallel
`PROVENANCE` dict is gone) — exposed as `price_provenance(provider, model)` and printed by
`3dcode cost prices`.  Every
row was reconciled against the providers' live pricing pages.  **Corrections
made:**

| row | was | is | effect |
|---|---|---|---|
| `anthropic:claude-sonnet-5` | 3.00 / 15.00 / 0.30 | **2.00 / 10.00 / 0.20** | we were over-billing sonnet-5 by 50% (the launch rate became standard) |
| `openai:gpt-5.6-sol` | 5.00 / 30.00 / 0.50 (approx) | **4.00 / 20.00 / 0.40** | the one codex run drops $1.86 → $1.42 |
| `gemini:gemini-3.1-flash-image` | 0.25 / 1.50 / 0.025 | **0.50 / 3.00 / 0.05 + $0.067 per 1K image** | image output is billed per image ($60/M image tokens), not as text |
| `gemini:gemini-2.5-flash` cached | 0.075 | **0.03** | 2.5x over-billing on cache reads |
| `gemini:gemini-2.5-flash-lite` cached | 0.025 | **0.01** | idem |
| `gemini:gemini-3.1-pro-preview`, `gemini-3-pro-preview`, `gemini-2.5-pro` | one tier | **+ >200k tier** (4.00 / 18.00 / 0.40) | `estimate_cost` now applies the long-context tier automatically |
| added | – | `claude-haiku-3-5`, `claude-mythos-5` | were unknown ⇒ silently $0 |
| added 2026-08-24 | – | `openai:gpt-5.6-terra` **2.00 / 12.00 / 0.20**, `openai:gpt-5.6-luna` **0.20 / 1.20 / 0.02** | the other two codex tiers were unknown ⇒ silently $0 (luna was cut 80% on 2026-07-30) |
| verified (no change) | – | every other gemini / anthropic / openai row | now flagged `verified` rather than "approximate" |
| still `inferred` / `unverified` | – | `claude-haiku-4`, `o1-mini`, `gpt-5-codex`, `gpt-5.1-codex`, `gpt-5.2-codex`, `gemini-2.5-flash-image` | not listed on the pricing pages; `3dcode cost prices --unverified` lists them |

Per-image prices live in the price table only (`Price.image_usd`,
`IMAGE_USD_BY_SIZE`, `per_image_usd()`); the duplicate table in the image model is
gone (2026-08-29) and `GeminiImageModel` prices with `per_image_usd(size=<generated
px>)` directly — a 2K image was billed 0.067 (the 1K rate) instead of 0.134 until then,
and a 512 request is billed as the 1K image it actually generates.

**Two costing bugs the audit surfaced** (both in files owned by other engineers —
see the hand-off notes):

1. **gemini-cli runs were under-billed ~3.7x.**  `e2e_bench_threejs` recorded
   $0.68; its own `stdout.json` stats say 14.19M prompt tokens (13.47M cached) +
   128.8k output ⇒ **$2.09**.  The session was recorded before
   `Usage.input_tokens` became `tokens.prompt`; the raw envelope is still on
   disk, so `3dcode cost show --recheck` rebuilds the true number.  Across the
   three gemini-cli runs the correction is **+$3.43**.
2. **claude-code attributes the cost to the wrong model.**  The envelope's
   `modelUsage` shows `claude-fable-5` did the work ($1.165) and
   `claude-haiku-4-5` answered one utility call ($0.0015); the harness records
   the *first* served model, so the run is filed as haiku with
   `input_tokens=2`.  The dollar is right (the CLI reports `total_cost_usd`), the
   attribution is not.  Rows from such backends are marked
   `price_source="provider-reported"` and are never re-priced from the token
   table.

Re-pricing everything with the corrected table moves the total from $86.30 to
**$89.10 (+3.2%)**: +$3.43 gemini-cli, −$0.50 codex, −$0.13 elsewhere.

**Not modelled** (documented in `pricing.py`): cache *storage* fees for explicit
Gemini caching, Anthropic 1-hour cache writes, batch/flex discounts, data
residency (1.1x) and fast-mode multipliers, per-search server-tool fees.

## 8. Model routing — what to run where

| role | model | $/call | measured quality | when |
|---|---|---|---|---|
| planner | `gemini:gemini-3.7-flash` ★ | $0.013 | no failure attributable to the planner | always — the plan is 0.8% of a run |
| generator | `gemini-cli:gemini-3.7-flash` ★ | $0.73 | 0.827 on compare_v1 | default for every track with tools since 2026-08-28 |
| generator | `single-shot:gemini:gemini-3.7-flash` | $0.05 | graphics: 5/6 passed, median 0.810 | glsl / opengl — one file, compiler feedback |
| generator | `gemini-cli:gemini-3.6-flash` | $0.52 | compare_v1 mean 0.835 (best arm measured) | the cheaper arm, and the one every recorded battery was run on |
| generator | `codex:gpt-5.6-sol` | $1.93 in-loop / $0.20 one-shot | one-shot 0.786 | strong one-shot baseline, expensive loop |
| generator | `oneshot:claude-code` | $1.04 | 0.673, 0/2 passed | not for bulk generation |
| judge | `gemini:gemini-3.1-pro-preview` ★ | $0.060 | σ 0.030, pearson(gate errors, score) **+0.63** | every decision that persists |
| judge | `gemini:gemini-3.7-flash` | $0.027 | σ 0.083, pearson **−0.33** | in-loop hints only |
| image | `gemini:gemini-3.1-flash-image` ★ | $0.067/tile | a texture pass ≈ $0.13 | only behind the before/after gate |

**When does the pro judge pay for itself?**  Flash is 2.2x cheaper per verdict,
but its noise is 2.8x larger.  Matching pro's σ = 0.030 by sampling flash needs
`(0.083/0.030)² = 8` samples = **$0.216 per verdict, 3.6x the price of one pro
verdict** — and sampling still cannot fix the sign of flash's correlation with
the deterministic gates (−0.33 vs +0.63), so a flash ranking stays biased no
matter how many samples you buy.  Pro is the cheap option for anything that
persists.  Concretely, the 112 verdicts in this data set cost $6.48 with the pro
judge and would cost $24.2 as "cheap" flash at n=8.

This table is its one home (the `cost/routing.py` copy of it, which only the markdown cost
report printed, went on 2026-09-22).

## 9. Ranked optimisation opportunities

Savings are estimated **on this data set** (61 runs, $86.30) unless stated.

| # | change | est. saving | confidence | where |
|---|---|---|---|---|
| 1 | ~~**Stop refining after r01 unless the last delta ≥ 0.05.**  r02+r03 cost $12.16 and bought +1.44 score points across 8 of 20 rounds.~~  **Removed 2026-09-22**: the round count is fixed (owner) — spend less with a lower `--rounds` or a profile, not with a judgement stop. | $9–12 (10–13%) | high (measured) | `--rounds` / `cost/profiles.py` |
| 2 | **Check the budget *before* starting a round, against the estimated round cost**, not only between steps. | **$8.76 (9.7%)** | high (measured waste) | `orchestrator.py` (`BudgetGuard`) + `tracks/steps.py`; use `codeverse3d.cost.estimate_call` / median round cost |
| 3 | ~~**Cap agent turns at ~25 and compact old tool results.**  39.3% of the agent bill is turn ≥20; 19 of the 33 sessions that ran ≥50 turns were cut off by their own budget.~~  **Tested, rejected: +$0.02 and −0.21 score** (A/B, n=3 per arm, §17) — the cap is off by default. | est. $8–15, **measured $0** | high (A/B) | `DEFAULT_AGENT_MAX_TURNS=0`; `$C3D_AGENT_MAX_TURNS` / `Settings.limits.agent_max_turns` still set one |
| 4 | **Fold the off-record spend into the budget** (cut rounds, post-hoc texture passes). | $0 saved, **$1.22 of blindness removed** | high | §6; the ledger (`codeverse3d.cost.record_call`) makes it automatic |
| 5 | **Scene track: 73% of scene spend is assets+zones, 0/5 passed.**  Trim the per-zone context (each zone session re-sends the whole scene contract) and judge assets before zones start. | ~$1/run of $2.83 | medium | `tracks/scene*.py` |
| 6 | **Drop `oneshot:claude-code` from default compare arms** ($1.04/artifact at 0.673 — the worst score per dollar measured). | bench-only | high | `eval/bench/compare_backends.py` arms |
| 7 | **Gate the texture pass on the materials criterion** (< 0.7) — 5 passes ran, 2 shipped, $1.10 spent. | ~$0.5 | medium | `texturing/run.py` |
| 8 | **Keep the pro judge; do not "save" money there** (§8). | avoids a **$45** increase | high | – |
| 9 | **Price-table corrections** (§7) — accuracy, not savings: the reported bill was 3.1% low overall and 3.7x low on gemini-cli runs. | – | done | `models/pricing.py` |
| 10 | **Do not chase prompt caching on judge/plan calls in general** — measured below the cache floor (§4). | – | done (measured) | – |
| 11 | **Grow the judge's stable head past one cache block** (system + rubric + defect checklist ≈ 3.5k → ≥ 4,088 tokens, e.g. by moving the shared scoring rules and a fixed worked example into the system block).  Measured: one cached block = 4,079 tokens = $0.0073 per pro verdict. | ~$0.8 (112 verdicts) | medium | `judges/prompt_builder.py` |
| 12 | **Know that `n_samples > 1` pays full price for every sample**: the montage shuffle voids the cache (measured 0 cached vs 4,079 on an identical resend, −22%/sample).  Keep the shuffle — it is the noise control — but count it when choosing `n`. | – | measured | `judges/prompt_builder.py` |

Not recommended on the evidence: cheaper generation models (a bare one-shot
flash scored 0.14 and produced no buildable code in 3 of 5 cells — the loop, not
the model, is what makes the artifact), and cheaper judges (§8).

## 10. The cost ledger (`codeverse3d/cost/`)

```python
from codeverse3d.cost import record_call, load_ledger, summarise

with run_ledger(ws.root):                       # <run>/telemetry/cost.jsonl (cost_ledger.jsonl = symlink alias)
    record_call(res.usage, run=ws.slug, round=idx, stage="refine", role="generator",
                label=job.label, outcome=res.exit_reason)     # one append-only JSONL row

rows = load_ledger(ws.root)                     # telemetry/cost.jsonl, else the pre-2026-08-23 root file
summarise(rows).dimension("stage")["judge"].cost_usd
```

One row per call: `ts, run, round, stage, role, backend, provider, model,
input/cached/output/thoughts tokens, cache_write, tool_calls, the three unit
prices actually used, price_source + price_approximate + price_checked
(provenance), cost_usd, recorded_usd, latency_ms, cache_hit, outcome, n_calls,
source`.  Writing never raises and never blocks a run; unknown models are
recorded at $0 **and flagged**, never silently dropped.

Until the call sites are wired, `codeverse3d.cost.reconstruct` rebuilds the same
rows from `record.json` + `events.jsonl` + `trajectories/**` — that is what this
audit runs on, and it reconciles to `record.total_usage` on every run (or says
why it does not, §6).

One helper exists for the callers:

* `estimate_call(model_id, prompt=…, n_images=…, output_tokens=…)` →
  `CostEstimate` before the call (the `CostGuard` that decided on it had no caller and
  was deleted 2026-08-28).  An unpriceable model is **allowed but flagged**
  (`price_source="unknown"`, $0) — refusing to run because we cannot price something
  would be worse than running it.

## 11. Caveats

* `plan`, `texture` and `pairwise` costs come from events that record a dollar
  but no tokens, so those rows have no token composition ($2.50 of $86.30).
  Wiring `record_call` at those call sites fixes it.
* Wall clock per run is the sum of the stage/round clocks (the event span
  includes hours a bench run spent queued behind other runs).  `budget.elapsed_min`
  is cumulative ACTIVE minutes: the budget snapshot carries `active_s` across
  `3dcode resume`, so prior sessions' minutes still count against `max_minutes`
  and downtime between sessions never does.
* "Regression" waste is an upper bound: a round that scored worse still produced
  the diff that informed the next refine task.
* The compare battery is only 2 prompts × 5 arms; its per-arm numbers are
  directional, not significant.
* Prices were read from the providers' public pages on 2026-08-23 and are the
  standard, non-batch, global-endpoint tiers.

---

# Part II — the controls (wave 2, 2026-08-23)

Part I above is the audit: where $89.88 went.  Part II is what was built on top of
it and, for each item, **what it measured** — including the two things that turned
out not to pay.

## 12. The live ledger

`codeverse3d.cost.instrument` meters the two places money is actually spent:

* **`MeteredChatModel`** wraps everything `models.get_chat_model` hands out, so one
  `CallCost` row is appended per `ChatModel.generate` — planner, judges,
  captioner, texturing and single-shot generation.
* **`MeteredAgent`** wraps everything `agents.get_coding_agent` hands out: it sets
  the ambient round/stage for the session (so the rows above land in the right
  bucket) and, for a backend whose calls we cannot see (gemini-cli / claude-code / codex /
  agy), records one session row from `AgentResult.usage`.

  **Which backends those are is a property of the backend, not a guess.**  The
  first version asked "did anybody write a ledger row while this session ran?"
  (a thread-local counter).  That is wrong in both directions, and both were
  reproduced: a gemini-cli session during which *any* in-process tool billed a
  model — a texture pass, a captioner — looked metered, so its session row was
  dropped and **$1.23 of the reproduction vanished**; and an in-process session
  whose turns ran in a worker thread looked unmetered and was counted **twice**.
  Every shipped backend is a vendor CLI, so the session row is always written now
  (the `meters_own_calls` opt-out went with the last in-process agent), and
  `tests/cost/test_instrument.py` pins the dropped-session direction.

**Attribution.**  A call is filed under what *it* says it is, not under what
surrounds it: `cost.context.attribute()` puts an explicit stage/role first, then a
label that names a job of its own (`judge:…`, `planner`, `texture_gate`,
`caption…`, `pairwise:…`), then the ambient agent session.  A spatial tool that
bills a model inside a refine session used to land on `stage=refine /
role=generator`; it now lands on its own stage.  A *generation* label still yields
to the session, which knows more (best-of-N runs `job.kind="candidate"` while the retired api-agent's
recorded turns were labelled `api-agent:baseline:tN`).  A session row is filed by the task
KIND through `cost.types.stage_for_label` (candidate → candidate, zone → zones, compose →
assemble, rebuild → repair, asset / asset_fix → assets); before 2026-08-29 the scene kinds
landed in `other`.  Candidate generation sessions carry `stage=candidate` with labels
`baseline_c<k>`; candidate judge money is booked `stage=judge`, and the pairwise tie-break
`stage=pairwise` (its ledger row carries `role=judge`, label `pairwise:…`).  Role, label and
round live on the `CallCost` row, never on the guard — `BudgetGuard.charge/add` take
`(usage, *, stage, enforce)` only since 2026-08-30 (D45).  `audit.lost_candidate` counts
generator sessions only and reads both label forms — `baseline_c<k>` (live) and
`c<k>:baseline` (reconstructed).

**Who opens a ledger.**  `3dcode make` / `3dcode resume` (`cli.main._run_track`),
`3dcode texture pass` (with `create=False`), **and the bench drivers** —
`eval/bench/run_bench.py` opens one per prompt and `eval/bench/compare_backends.py` one per
cell (plus a nested one for a harness arm's own run, and one for the pairwise
arena).  The batteries produce most of the runs in this repo, so until wave 3
most priced rows were going to the per-process fallback log.  `run_ledger` nests by holding
ContextVar tokens (`bound_run` / `bound_ledger`); there is no process-wide default, so
`--parallel N` keeps N ledgers apart.  A thread that may bill a model must therefore be
spawned through `proc.fan_out`, which copies the context — never a bare pool (D45(b): the
old save-and-restore republished a sibling’s run the moment the first cell exited).  A call
with no run context still lands in the per-process log under `cache_dir/cost/`.

The row carries run, round, stage, role, label, backend/provider/model, the four
token counts, the three **unit prices actually used** plus their provenance
(`price_source` / `price_approximate` / `price_checked`), $, latency,
`cache_hit`, outcome and `n_calls`.  Writing never raises and never blocks: a
pricing failure logs and keeps the recorded dollar.

**Where it lands.**  `<run>/telemetry/cost.jsonl` (the run-layout telemetry
bucket), with `<run>/cost_ledger.jsonl` left as a relative symlink so
`record.telemetry.live_ledger_path` and the `telemetry/usage.jsonl` alias keep
working — one physical copy.  A call made with no run context (a `3dcode judge`
outside a run, a bench script, a notebook) goes to a per-process log under
`<cache_dir>/cost/`; `C3D_COST_LEDGER=off` disables writing entirely.

**No double counting.**  `BudgetGuard` only buckets and enforces; `MeteredAgent` /
`MeteredChatModel` are the one writer of `telemetry/cost.jsonl`, and `BaseTrack.run`
opens the run ledger itself.  The guard's own aggregate writer and its
`per_call_metering()` sentinel were deleted 2026-08-29: every production entry point
(`3dcode make`, the bench drivers) opened `run_ledger` first, so it never wrote there —
and with `C3D_COST_LEDGER=off` it wrote anyway, which is now really off.

**Reading it.**  `cost.reconstruct.reconstruct_run` prefers a live ledger and
falls back to rebuilding from trajectories / verdicts / events, so
`3dcode cost` and the run layout's `telemetry/cost.json`
both pick the live rows up automatically and the 61 recorded runs keep auditing
(`RunLedger.source` says `live` or `reconstructed`).

### Verified on two fresh live runs

| run | track / generator | rows | ledger | `record.total_usage` | difference |
|---|---|---|---|---|---|
| `verify_graphics` | graphics / api-agent flash | 12 | **$0.195275** | $0.195275 | **+0.000 %** |
| `prof_chair_balanced` | static_object / api-agent flash | 55 | **$0.510770** | $0.510770 | **+0.000 %** |

Both are well inside the 1 % target.  The chair run also shows the audit's §6
blindness closed: its retried `baseline.a2` session (6 calls, $0.046) is a
first-class set of rows, and a model error mid-session is recorded as an
`outcome="error"` row at $0 instead of vanishing.

```
3dcode cost <slug>              # one run: stage/role/model + a reconciliation block
3dcode cost --runs-dir <root>   # a battery, aggregated
3dcode cost cache <slug>        # per session: cold first call, cached share, saved $ / cold $
```

## 13. Cache-friendly prompt ordering — measured, and REVERTED

Wave 2 reordered the object-track generation prompts stable-prefix-first: the
standing contract, the frame doc, the cookbook excerpt, the standing rules, the
tool cards and the output format were hoisted into one shared include
(`prompts/tracks/_object_head.j2`) that `generate_static.j2`,
`generate_articulated.j2` and `refine_object.j2` each began with, byte-identical.

**That include is gone (2026-08-23).**  The head was not *shared* with anything
already being sent — it was **duplicated into every prompt**, so the shared prefix
it created was paid for four times over.  Measured with `cost.prefix_report` on
the real templates (one baseline + three refine prompts of one run, same context;
the helper has been deleted since — `caching.py` keeps only the session-cache half):

| | shared prefix | per-prompt tokens | mean per call |
|---|---|---|---|
| task-first (shipped, restored) | 46 tokens | 5,072 / 1,325 / 1,325 / 1,325 | **2,261** |
| stable-head-first (reverted) | **4,970 tokens** | 5,110 / 5,211 / 5,211 / 5,211 | **5,186** |

**+2,925 tokens on the average call, +3,886 on every refine call** — and the task
prompt is message 0 of an agent session, re-sent on every turn, so the run-level
figure is that times the session's turn count.  Against it: the 4,970-token prefix
is far below the **~12k floor** §4 measured for Gemini's implicit cache, so on a
real run the first turn still reports `cached_tokens = 0`.  The wave-2 note read
the two arms as "≈ $0.00" run-level and kept the ordering as a free precondition;
an independent re-measure showed the delta is not zero, it is a **net increase**,
and the only arm that reached the cache floor did so by padding the head to 24k —
i.e. by buying the cache block with more tokens than it returns.

**Is there anywhere the prefix is genuinely long enough?**  Measured, no:

| candidate prefix, sent on every turn without duplication | tokens |
|---|---|
| api-agent system prompt (`AGENTS.md`, contract + cookbook note) | 4,879 |
| its tool declarations (16 tools, JSON schemas) | 1,691 |
| **total, already first in every request** | **6,570** |

6.6k is still under the floor, and it is already at position 0 — there is no
reordering left to do there.  `3dcode cost cache <slug>` still reports
what a run's sessions actually cached.  The rule that measurement encodes: measure the prefix
against the floor *before* reordering a prompt family, and never pad to reach it.

## 14. The judge payload — 768 px saves bytes, not dollars

Re-judged 8 recorded rounds (5 static / articulated, 1 threejs, 1 compare cell, 1
shader) at 1024 px and 768 px, `gemini-3.1-pro-preview`, n=2 per size, detail
crops held constant.

**Payload per verdict** (1024 px): 3.0k–4.6k text tokens, 3–5 images, **311–714 KB**
of PNG.  At 768 px the same montages are **229–541 KB (−26 % bytes)**.

| | 1024 px | 768 px |
|---|---|---|
| mean overall (8 rounds) | **0.6609** | **0.6536** |
| sd across rounds | 0.0976 | 0.1079 |
| mean within-round σ (n=2) | **0.0326** | **0.0421** |
| **input tokens, 16 verdicts** | **156,834** | **156,874** |
| total $ | $0.8779 | $0.8617 |

Per-round Δ(768−1024): mean **−0.0074**, sd 0.0576, worst case −0.135
(`e2e_cabinet_urdf`, where the 768 arm's own within-round σ was 0.155 — noise, not
signal).

The mean difference is inside the judge's noise, so by the pre-registered rule the
cheaper size would be adoptable — **except that it is not cheaper**: Gemini bills a
montage at the same tile count at both sizes (**+0.03 % tokens, not −20 %**), and
768 px measurably *raises* sampling σ (0.0326 → 0.0421, +29 %).  **1024 px stays
the default for every profile.**

### Payload v3 (D47, 2026-08-31): 5 montages for the 14-view rig

The rig A/B (eval/docs/EVAL.md judge-experiments log) re-priced the verdict: the adopted
payload C (14 views + clay, 5 montages + 2 crops) bills **$0.155/verdict at pro n=3
(40.9k input tokens)** against the old 8-view payload's $0.146–0.150/30.0–34.0k — a
+3–6 % price for the only payload change that has survived multiplicity (+0.038
same-cap).  The rejected 14-singles arm B cost $0.198 & 67.5k tok/verdict for a
*negative* delta.  `Settings.judge.montages` is 5 under every profile (economy included: the
flash replica prices a C verdict at ~$0.032, and 3 montages would silently drop the
low ring + poles).

### Conditional slices (D48, 2026-08-31): the dirty verdict got CHEAPER

The gate-ERROR-only slice channel (docs/DECISIONS.md D48) measured **$0.154/dirty
verdict vs $0.172 baseline** (n=3, 3.1-pro, 14 conn-dirty rows) — the two extra
small PNGs are outweighed by shorter narration — and a clean round's payload is
byte-identical, so at production dirty ratios the channel amortises to **≤ $0**.
The slice render itself is local CPU (shapely + matplotlib, a few seconds).  No
profile carries a dial for it; `C3D_JUDGE__SLICES=off` is the kill switch.

### The crop-count experiment — INCONCLUSIVE, not adopted

The other payload lever is the **image count**.  The same round
(`e2e_chair_blender` r01, `gemini-3.1-pro-preview`, n=1) was re-judged at 2 / 1 / 0
detail crops **twice, independently**:

| detail crops | input tokens | $/verdict (pro) | draw A overall | draw B overall |
|---|---|---|---|---|
| 2 (the default) | 9,454 | $0.0480 | 0.595 | 0.600 |
| 1 | 8,255 | $0.0449 | **0.600** | **0.552** |
| 0 | 7,058 | $0.0403 | 0.600 | 0.600 |

One crop is **1,198 input tokens ≈ $0.0024/verdict**.  Draw A read the 1-crop arm
as unchanged and wave 2 adopted it for the economy profile; draw B read the same
arm **0.048 lower** — **1.6× the pro judge's measured σ of 0.030** §8, and larger
than the effect being claimed.  Two draws that disagree by more than the
instrument's noise measure nothing.

**Not adopted.**  No profile carries a crop count, and
`Settings.judge.detail_crops` keeps its default of 2 — the previous image budget,
restored.  Buying a fifth of a cent per verdict is not worth a payload change we
cannot show is harmless, and at n=1 per arm the experiment cannot show it: it
needs ~n=8 per arm to resolve 0.048 against σ=0.030, which costs more than the
change saves on the 112 verdicts in this data set.  A caller that wants a smaller
payload can still say so (`VlmJudge(detail_crops=…)` / `C3D_JUDGE__DETAIL_CROPS`);
no profile says it for them.

Both halves of §14 confirm §8's conclusion: on judging, the lever is *how many
verdicts*, not how big each one is.

## 15. Profiles — one name for the whole dial

`Settings.profile` + `3dcode make --profile economy|balanced|quality` set model per
role, judge samples, refine rounds, best-of-N width, the texture pass and the
budget ceilings together (`codeverse3d/cost/profiles.py`).

**One resolver, both entry points.**  `codeverse3d.cli._common.resolve_dial` is the
only place the dial is read, and it returns a `ResolvedDial` with every field.
`--profile X` *forces* the dial over a value the user stated in `config.yaml` /
`C3D_*`; `C3D_PROFILE=X` (or `profile:` in the config file) sets the same dial
as a *default*, so a value you stated yourself survives it.  With nothing else
stated the two paths resolve **identically** — which they did not before: the CLI
read `candidates` and `texture` off the *flag* rather than off the resolved
profile, so `C3D_PROFILE=quality` silently ran best-of-1 with no texture pass.
`tests/cost/test_profiles.py` asserts every field of the dial from both entry
points, per profile.  An explicit CLI flag still beats both, and
`Spec.options.profile` now records the resolved name **whichever way it was
given**, so `3dcode resume` reproduces it.

| | economy | balanced | quality |
|---|---|---|---|
| generator | `single-shot:gemini:gemini-3.7-flash` | `gemini-cli:gemini-3.7-flash` | `gemini-cli:gemini-3.7-flash` |
| judge | flash, n=2 | **pro, n=1** | **pro, n=3** |
| refine rounds | 2 | 4 | 4 |
| best-of-N | 1 | 1 | 2 |
| agent turn cap | – (see below) | – | – |
| judge payload | 1024 px, 2 crops | 1024 px, 2 crops | 1024 px, 2 crops |
| texture pass | off | off | on |
| ceilings | $1.50 / 30 min | $5 / 60 min | $8 / 90 min |
| expected from the audit | ~$0.30/run | ~$1.47/run | ~$3.20/run |

Why these numbers: r01 buys a score point for $4.93 and r02 for $6.80 while r03
costs $33.49 (§5), so economy stops at 2; the pro judge is *cheaper* than matching
its precision with flash (§8), so balanced and quality both use it and only
economy trades precision for price; best-of-2 and the texture pass are the two
levers measured to raise a score, so they belong to quality.

**Two dials wave 2 set were removed as unmeasurable (2026-08-23).**

* **economy's 20-turn agent cap.**  Economy's generator is `single-shot:`, which
  opens no agent session — the cap could never fire, and no recorded economy run
  shows it firing.  The cap it was modelled on also lost its own A/B in this wave
  (§17: a 28-turn cap cost $0.02 more and 0.205 of a score point over 3 runs per
  arm), so there is no default cap anywhere now: no profile carries a turn cap
  and `tracks.generation.DEFAULT_AGENT_MAX_TURNS` is 0.
  `Settings.limits.agent_max_turns` / `C3D_AGENT_MAX_TURNS` remain the knob for a
  run or a machine that wants one by name.
* **economy's 1 detail crop.**  §14: two independent draws of the crop experiment
  disagreed by 1.6× the judge's σ over a $0.0024/verdict saving.  Every profile
  sends the full payload.

**`texture: false` now actually prevents the texture pass.**  Three call sites used
to answer "does this run texture?" independently — `Spec.options.texture`, a
`texture` spec tag, `ctx.extra["texture"]` — and, worse, the `texture_pass` spatial
tool is registered for every object track, so the coding agent could buy a pass in
any run.  That is why the quality run below shows a ledger 9.5 % above its record:
the pass ran twice, once from inside a round-2 agent session.
`codeverse3d.texturing.run.texture_requested(spec)` is now the single owner;
the CLI hand-over after the run (`make`/`resume` → `addons.select.package`, which textures the
PICKED round since 2026-09-22 — finalise no longer does) and the tool both ask it, and the tool refuses with a
usage error (costing $0) in a run whose spec says no.  `3dcode texture pass <slug>`
is an explicit user instruction and is unaffected.

### One live run per profile, same prompt

`"a mid-century wooden dining chair with four tapered legs and a curved slatted
backrest"`, static_object / blender, 2026-08-23 (during a flash 503 storm, so the
minutes include provider backoff):

| profile | baseline → final | rounds | cost | minutes | stop | judge |
|---|---|---|---|---|---|---|
| economy | 0.600 → **0.700** | 3 | **$0.3030** | 14.0 | max_rounds | flash n=2 |
| balanced | 0.700 → **0.856** ✅ passed | 2 | **$0.5108** | 5.3 | pass | pro n=1 |
| quality | 0.600 → **0.600** | 3 | **$1.8079** (ledger $1.9802) | 14.3 | diminishing_returns | pro n=3 |

Every dial landed: economy really ran single-shot + flash at n=2 and stopped at 2
refine rounds, quality really ran best-of-2 + pro at n=3 and shipped a texture
pass (Δ materials +0.55, Δ overall +0.148 on the textured GLB).  Economy came in
at $0.303 against the $0.30 predicted from the audit; balanced at $0.51 against
$1.47 (this prompt passed at round 1, the audit's median run does not).

**The honest result is that quality lost this one**: it cost 3.5× balanced and
finished 0.256 lower.  Two mechanisms, both visible in the numbers — best-of-2
picked a 0.600 baseline where balanced's single baseline happened to be 0.700, and
a pro judge at n=3 has less noise to be lucky with (its three verdicts were
0.600 / 0.600 / 0.600).  **n=1 run per profile is an existence check, not an
experiment**: it proves the dial is wired end to end, and it says nothing
significant about which profile scores better.  The audit's per-arm numbers (§8)
remain the quality evidence.

One number worth carrying forward: at `n=3` the judge is **31.5 % of that run's
bill** ($0.62 of $1.98, 11 calls) against 6.0 % at `n=1` on the balanced run —
§8's "keep the pro judge" is about *which* judge, not about how many samples of
it, and `n=3` is a calibration setting, not a production one.

Its ledger is also **$0.172 larger than `record.total_usage` (+9.5 %)**: the
texture pass ran twice — once as a tool call inside the round-2 agent session,
once post-loop — and the post-loop pass is outside the run total.  The ledger made
that visible per call instead of invisible (§6); the *second* pass should never
have happened, and cannot now: `texture_requested()` is the single owner of the
decision and the tool refuses in a run that did not ask (above).  These three
runs also predate the caveats above — they were run with the (now reverted)
prompt reordering and with economy's 1-crop payload, so the economy row's judge
payload and all three runs' prompt sizes differ slightly from what ships today.

## 16. Price maintenance

`3dcode cost prices` prints the table with provenance and now **flags** every row it
cannot stand behind: `stale>90d` (checked date older than the window),
`approximate` (the `Price.approximate` flag) and the non-`verified` statuses
(`inferred` / `unverified`).  `--stale` shows only flagged rows, `--days N` moves
the window, `--unverified` keeps the old status filter.  Today: 52 rows, 6 flagged
(`claude-haiku-4`, `o1-mini`, `gpt-5-codex`, `gpt-5.1-codex`, `gpt-5.2-codex`,
`gemini-2.5-flash-image`), none stale.

Two tests hold the line: `test_supported_model_ids_resolve_to_a_price` (no
silent $0 — every model id a default, route, CLI example or backend contract exposes
must have a row) and
`test_every_price_row_was_checked_within_the_maintenance_window` (re-read the
providers' pages and bump `CHECKED`; never raise the threshold).  Every ledger row
carries the provenance of the price it used, so a dollar can always be traced to a
row and a date.

## 17. Two round-level controls that did not survive their own measurement

Wave 2 landed four round-level controls.  Two of them were re-measured, failed,
and are gone; this section is the evidence, so nobody re-adds them from §5/§9.

### The 28-turn agent cap — A/B'd, rejected, removed

§4 counted 39.3 % of the agent bill at turn ≥ 20 and 19 of 33 long sessions cut
off by their own budget, and estimated $14.29 net for a graceful 28-turn cap
(`waste-and-accounting/turn_cap_report.txt`).  That counterfactual assumed the
turns past 28 buy nothing.  A controlled A/B says they do:

| arm | run 1 | run 2 | run 3 | **mean $** | **mean score** |
|---|---|---|---|---|---|
| `agent_max_turns=28` | $1.430 / 0.354 | $1.668 / 0.600 | $1.047 / 0.482 | **$1.381** | **0.479** |
| uncapped (backend default) | $1.001 / 0.852 | $1.603 / 0.600 | $1.477 / 0.600 | **$1.360** | **0.684** |

Same prompt (an articulated desk lamp), same generator
(`gemini-cli:gemini-3.6-flash`), same judge (`gemini-3.1-pro-preview`),
same budget and box; 3 runs per arm; scripts and run dirs in
`verifier/ab_uncapped.py` + `verifier/live/vstatic_hard_lamp + vlamp_cap60*`.  The cap **cost $0.02 more
and 0.205 of a score point**: a session stopped at turn 28 leaves work the next
round pays for again, and the wrap-up session it buys is not free either.

So there is no default cap: `tracks.generation.DEFAULT_AGENT_MAX_TURNS = 0` leaves
`AgentJob.max_turns` at the backend's own default.  The plumbing stays for a machine
that chooses one — `$C3D_AGENT_MAX_TURNS` > `Settings.limits.agent_max_turns` (the
per-policy / per-task / `generate(max_turns=…)` knobs, never set by any caller, went
2026-09-22) — and a cap that IS set still lands gracefully
(wrap-up session, `generate.turn_cap` event).  **Nothing sets one by default any
more**: the profiles independently dropped their own caps in the same wave
(`cost/profiles.py` carries none), so a cap now only exists when a
run, a bench arm or `$C3D_AGENT_MAX_TURNS` asks for it by name.

### `skip_judge_reason` — two of four branches removed

A skip only saves money if the verdict is never bought.  Reproductions were run
out of tree (no such test ships here):

| branch | verdict | why |
|---|---|---|
| `no file change` | **removed** | unreachable: a generation result is `ok` only when a file changed, so `run_generation_tasks` raises `RoundFailed` (→ the loop's `no_change` plateau) before any judge question is asked |
| `build not repaired within the repair budget` | **removed** | a broken build never reaches the judge at all (`run_round` judges only when `build.ok`), so it only ever fired on a round that BUILT but still lint-failed — and the loop's `rejudge_round` then bought the same verdict one iteration later.  Measured on the reproduction: 3 rounds "skipped", 2 verdicts re-bought as `judge.retry`, and the last round left **without a score — it was the best of the three (0.7)** and could not be promoted |
| `budget already exceeded` | kept, then **removed 2026-09-22** | the loop's next `budget_ok` check ends the run, so the verdict was never bought later.  Guard, not a measured saving: 0 of the 108 recorded rounds bought a verdict after the budget ended.  Removed with the in-run best: a round's verdict is now what lets a pick choose it after the run, and the generation it scores is already paid for |
| `no judge` / `no renders` | **kept** | pre-existing guards (nothing to buy).  The third pre-existing reason, `gate errors` behind `judge_on_gate_errors=False`, went 2026-09-21: no caller ever set it |

Two changes were needed to make the kept branches real:

1. `rejudge_round` re-buys a verdict only after the judge was *tried* and failed
   or came back degraded — never one that policy deliberately skipped.  Otherwise
   every skip is a `judge.retry` with the same price tag.
2. `pick_best_round` never promoted a round that did not build.  (Removed 2026-09-22 with
   the in-run best; `addons/select.pick` picks among JUDGED rounds only, so an unbuilt or
   unjudged round is never handed over by score.)

The two controls that DID survive this correction — the regression "switch then stop"
rule and the r03+ marginal stop, which cut 2 runs / **$1.14** with **0 best rounds lost**
over the 107 recorded rounds (`waste-and-accounting/replay_stops.py`) — were **removed on
2026-09-22** with every other judgement stop: the owner fixed the round count (baseline +
`--rounds`, cut short only by the clock or a hard failure) and moved the choice of round to
after the run.  Every round still emits its `cost.round` event.

---

# Part III — throughput: using the 22 keys (wave 3, 2026-08-24)

Parts I and II are about **money**.  Part III is about **wall clock**: the owner has
22 Gemini keys at 1 000 RPM / 1 000 000 TPM each and asked whether the harness rotates
them and whether more parallelism would buy speed.  It does rotate — `KeyPool` has done
round-robin + per-key RPM buckets + 429 cooldown + dead-key benching since wave 1.  The
finding is that **rotation was never the constraint**: the pool was running at 0.7 % of
its request quota and 1.6 % (median) of its token quota.

Everything below is measured on this box (24 cores, 22 keys, `gemini-3.7-flash`), from
the 6 014 live priced calls in `eval/bench/out/**` and three probes; the reusable one is
`eval/bench/concurrency_probe.py`.

## 18. The baseline: 3 % of the quota, and 17.7 h asleep

| | measured | pool capacity | used |
|---|---|---|---|
| peak requests/minute | 154 | 22 000 | **0.7 %** |
| peak prompt-tokens/minute | 4.00 M | 22.0 M | **18.2 %** |
| median prompt-tokens/minute | 0.36 M | 22.0 M | **1.6 %** |
| peak concurrent calls | 13 | 64 (§20) | 20 % |
| mean concurrent calls | 2.05 | 64 | **3.2 %** |
| 429s in 6 014 calls | 4 | — | all on one key |
| 503 "capacity storm" waits | **2 833** | — | **17.72 h of thread time asleep** |

Reconstructed per battery from `(ts, latency_ms)` in the cost ledgers — `mean_if` is
the time-weighted mean of concurrent Gemini calls, `idle` the share of the span with
none in flight:

| battery | span | `--parallel` | calls | mean_if | peak_if | idle | model time | 503 sleep |
|---|---|---|---|---|---|---|---|---|
| static_v2_flash | 2.49 h | 3 | 4 453 | 2.57 | 10 | 5.3 % | 6.40 h | 1.65 h (504 waits) |
| articulated_v2_flash | 5.95 h | 2 | 1 335 | 0.94 | 3 | 29.1 % | 5.60 h | 4.67 h (560) |
| graphics_v2_flash | 1.10 h | 3 | 67 | 1.07 | 4 | 41.3 % | 1.18 h | 1.23 h (131) |
| compare_v2_full | 1.41 h | 3 | 36 | 1.31 | 4 | 41.0 % | 1.85 h | 1.16 h (79) |

Where a worker's time went, against the six candidates:

* **our own thread pools — the dominant sink.**  `max_parallel_agents=6`,
  `max_parallel_builds=3`, `bench --parallel 2`, `fan_out(max_workers=4)`: the harness
  never *asked* for more than 13 concurrent calls out of a measured ceiling of 64.
* **the pool's RPM buckets — ~0.**  154 RPM peak against a 22 000 RPM bucket.
* **provider 429 — ~0.**  4 events in 6 014 calls, all on one key, all rotated away free.
* **provider 503 storms — 22 – 39 % of every battery's wall clock**, 17.7 h in total,
  ≈108 % of all model generation time.  Model-wide, so rotation cannot help (§21).
* **subprocesses — negligible for build.**  351 recorded blender builds cost **325 s in
  total** (mean 0.9 s, p50 0.2 s); a fresh build of a recorded `model.py` is 0.37 s.
* **the model generating — 36 – 86 %** of worker time (p50 3.3 s, p90 14.5 s, p99 53 s).

There is nothing to read from the provider: a live `POST …:generateContent` returns no
`x-ratelimit-*` header at all, only `x-gemini-service-tier: standard`.  Headroom has to
be modelled from the quota, which is what `Settings.rate` now does.

## 19. TPM, not RPM, is the binding limit

Prompt tokens dominate completely — over the corpus, output + thoughts were **2 % of
input** (243.9 M in vs 4.0 M out) — and a generator call averages **41 739 prompt
tokens**.  At that size 1 M TPM is ~24 calls/min per key (528 pool-wide) while the RPM
quota would allow 1 000 per key.  So the meaningful limiter is a *token* rate:

* `KeyPool` had a `tpm_per_key` hook that was never set (`None`).  It is now wired from
  `Settings.rate.tpm_per_key` (default 1 000 000) via `shared_pool`, whose registry key
  now includes the quota so two different quotas cannot silently share one set of buckets.
* `acquire(tokens_hint=…)` **reserves** the estimated prompt tokens of the pending call.
  The estimate is `codeverse3d.models.retry.request_tokens`, a thin wrapper over
  `cost.guard.estimate_call` that walks the whole request (system prompt, tool schemas,
  response schema, tool results, a flat 1 290 per image).
* `report(key, outcome, tokens=actual, reserved=hint)` **reconciles**: the bucket is
  charged `actual - hint`, so an over-estimate is refunded, an under-estimate is paid off
  (the bucket may go negative and is cleared by the next refill), and a call that never
  reached the model — 429 or capacity storm — gets the whole reservation back.

That is what makes a 200 k judge verdict and a 2 k caption schedule differently, and §20
shows it is not theoretical.

**Deleted 2026-09-22: the buckets never engaged.**  Every number above was measured while
the in-process api-agent sent the generator's 42 k-token turns through this pool.  Since it
went (2026-08-28) the generator is a vendor CLI that makes its own calls and reserves
nothing here, and the harness's own traffic is the planner, the judge, captions and
texture images: over the cost ledgers 2026-08-29 .. 09-22 (2 080 Gemini calls, 11 084
round-trips) the busiest key peaked at **39 k prompt tokens/min — 3.9 % of its 1 M TPM** —
and **26 calls/min, 2.6 % of its 1 000 RPM** (re-counted on the ledgers on this box:
2.7 % and 0.7 %).  So the per-key RPM/TPM buckets, the reservation (`tokens_hint`,
`request_tokens`) and its reconciliation, and `Rate.rpm_per_key` / `tpm_per_key` are
gone; the pool rotates, cools a 429'd key down, benches a dead one and caps what is in
flight (§20, §23).  If a workload ever comes back that fills a key's quota, the 429 it
earns is rotated away for free (§27) — that is the signal to measure again.

## 20. The measured knee — and why one number is not enough

`eval/bench/concurrency_probe.py` runs a fixed workload at several in-flight levels with a
fresh `KeyPool` each time, so the `ok / 429 / 5xx` counters are exact deltas.

**Generator-shaped (12 k-token prompt, 128 calls per level):**

| in-flight | wall | calls/min | Mtok/min | mean_if | p50 | 429 | 5xx | $ |
|---|---|---|---|---|---|---|---|---|
| 16 | 259.8 s | 29.6 | 0.75 | 12.7 | 17.6 s | 0 | 32 | 0.71 |
| 32 | 175.0 s | 43.9 | 1.12 | 24.5 | 28.6 s | 0 | 35 | 0.72 |
| **64** | **105.4 s** | **72.9** | **1.86** | 24.6 | 13.4 s | 0 | 67 | 0.70 |
| 128 | 162.9 s | 47.2 | 1.20 | 22.7 | 20.5 s | 0 | 41 | 0.71 |

Throughput rises **2.5x** from 16 to 64 and falls back 35 % at 128 — the knee is 64.
Cost per call is flat within ±2 % across the whole range: **concurrency buys wall clock,
not money.**

**Judge-shaped (200 k-token prompt, 32 calls per level):**

| in-flight | calls/min | Mtok/min | 429 |
|---|---|---|---|
| 8 | 19.8 | 8.50 | 0 |
| **16** | **46.8** | **20.05** | 2 |
| 32 | 29.5 | 12.66 | 3 |

429s appear exactly where the arithmetic predicts: 16 x 200 k ≈ **20.0 M prompt-tokens
per minute against a 22 M pool quota** — a direct confirmation of the 1 M TPM per key
(measured onset ≈ 0.91 M/key) and the reason a single fixed concurrency number is wrong:
*the same in-flight count that is 3 % of quota for a caption is 91 % of it for a judge
verdict.*  The TPM buckets, not a call counter, are what keep the big-call regime honest.

**The subprocess ceiling is a different, much lower number.**  32 replays of a recorded
blender `model.py`:

| workers | 1 | 4 | 8 | **16** | 24 | 32 |
|---|---|---|---|---|---|---|
| builds/min | 155.9 | 590.3 | 1 051.7 | **1 392.0** | 1 167.6 | 1 018.7 |

Knee = **16 concurrent blender builds** on 24 cores.  Headless-Chrome renders could not
be measured cleanly (12 distinct GLBs at 1 – 12 workers, forwards and backwards, gave
2.5 – 220 renders/min with single renders from 1.5 s to 153 s): that variance is the
renderer's own tail, not parallelism.  Renders are treated as "1.5 – 3 s typical with a
minutes-long tail" and the subprocess pools are sized from the clean build number.

**64 model calls vs 16 subprocesses is 4x apart, so the two are capped separately:**
`Settings.rate.max_in_flight` is the number of machine-wide in-flight slots `KeyPool` takes
(network-bound, provider-sized — §23), `Settings.limits.max_parallel_*` size the thread pools
(CPU-bound, core-sized).

### Defaults changed, each from a number above

| knob | was | now | basis |
|---|---|---|---|
| `Rate.tpm_per_key` | *(unset)* | 1 000 000 | owner's quota; 429 onset measured at ~0.91 M/key |
| `Rate.rpm_per_key` | 900 | 1 000 | owner's quota (never binding: 0.7 % used) |
| `Rate.max_in_flight` | *(none)* | 64 | §20 knee |
| `Limits.max_parallel_agents` | 6 | 12 | build knee 16, derated for the render tail |
| `Limits.max_parallel_builds` | 3 | 8 | build knee 16 |
| `bench --parallel` (both drivers) | 2 / 3 | 8 | a cell holds ~0.9 calls in flight → 8 cells ≈ 7, far inside the 64 knee |
| `fan_out(max_workers=…)` fallback | 4 | 8 | inside both knees (every real caller states its own) |

## 21. Making 503 storms cheap: one shared wait instead of thirty

A 429 is per key and rotation cures it.  A 503 *"this model is currently experiencing
high demand"* is **model-wide**: every key sees it at once, so each worker that meets a
storm independently spends a full failed round-trip to learn what its siblings already
know, then sleeps on its own private backoff schedule.  That is the 2 833 waits / 17.7 h
in §18.

`codeverse3d/models/retry.py` adds a process-wide `StormGate` per model (`retry.py:449`):

* the first worker to see a 503 calls `hit()`, which closes the gate for a short,
  escalating window (never longer than `MAX_WAIT_S` — patience comes from the *number*
  of waits, not the length of one);
* every other worker parks in `enter()` instead of issuing a call that is almost
  certain to fail;
* once the window elapses exactly **one** worker is let through as a probe, under a
  lease so a crashed prober cannot wedge the gate.  Its success (`ok()`) reopens the
  gate for everybody.

It never adds waiting when no storm is running.  **And it lost its own A/B, so it ships
OFF.**  Same workload (48 calls, 32 in flight, 12 k prompts), alternated to control for
drift in the provider's mood:

| round | gate | wall | calls/min | mean_if | p50 | max | 5xx attempts | parked |
|---|---|---|---|---|---|---|---|---|
| 1 | off | 96.0 s | **30.0** | 14.27 | 25.8 s | 71.0 s | 18 | — |
| 1 | on | 158.3 s | 18.2 | 5.54 | 11.7 s | **158.2 s** | 27 | 140 s |
| 2 | off | 63.4 s | **45.4** | 13.20 | 15.6 s | 63.4 s | 6 | — |
| 2 | on | 144.1 s | 20.0 | 9.92 | 16.0 s | **142.8 s** | 20 | 100 s |

The gate costs **45 – 56 % of throughput**, consistently, and the two columns that
explain why are `5xx` and `max`:

* it produced **more** 503s, not fewer (27 vs 18, 20 vs 6) — the opposite of its purpose.
  A gate that reopens on one probe's success releases all 31 parked workers at the same
  instant, and that thundering herd is exactly the burst the provider was 503-ing.
* median latency **improved** (11.7 s vs 25.8 s in round 1 — parking really does spare a
  worker some doomed round-trips) while the maximum blew out to the whole run length:
  with one probe at a time, whichever call keeps losing the race is starved.

Underneath both is the same misreading: Gemini's 503s here are **intermittent, not an
outage** — most calls succeed while some 503 — so treating the first 503 as "the model
is down" parks 31 healthy workers for nothing.  A shared signal only pays when the
failure really is all-or-nothing.

The A/B was run during *intermittent* 503s, which is what the logs show most of the
time.  A single sustained outage is the case the gate was designed for and is not
covered by this measurement — that is why the mechanism is kept rather than deleted.
So `Settings.rate.storm_gate` defaulted to **False**, and the mechanism was kept so the
experiment stayed reproducible — until 2026-09-22, when it was **deleted**: §27 made a
storm mean "every key in the pool 503'd inside this one call", so the gate could close
only after a whole pool's worth of failed round-trips, and in four weeks shipped OFF
nobody switched it on.

**Caveat on the knee under a storm.**  §20's knee optimises *throughput* — total calls
per minute.  A bench cell is judged on *latency* instead: it has a `--max-minutes`
budget, and under the sustained 503 storm of 2026-08-24 03:00–05:00 one planner call
took **2 218 s** and cells hit the old 45-minute ceiling before producing code.  When
the provider is capacity-limited, extra concurrency cannot add throughput but does add
per-cell latency, so a long battery run in that state wants a *bigger time budget*
(`--max-minutes 180`), not a smaller `--parallel`.

What *did* make storms cheap is smaller and already in the retry loop: no single wait
may exceed `MAX_WAIT_S` = 3 s (5 s until 2026-08-27; patience comes from the number of attempts, 60 of them),
so a storm no longer blocks a worker for minutes — and with `--parallel 8` and
`max_in_flight 64` the other seven cells keep working through it.  Deferring a
storm-hit item to the back of the queue was **not** built: at the gate-off settings a
whole 128-call level costs 6 – 35 storm attempts and **zero failures**, which does not
justify a scheduler rewrite in `fan_out`.

`3dcode doctor --live` prints the pool's live picture — keys, in-flight and peak in-flight,
429/5xx/dead counts this process.

## 22. The retry budget was counted in attempts, so a "90-minute" run took 200

Two independent bugs let a single provider outage burn hours of paid wall clock on
2026-08-24.  Both are cost-control failures, not model failures.

**The storm budget was never a time budget.**  §21's storm branch is bounded by
`storm_attempts = 60` with each wait capped at `MAX_WAIT_S`, and its docstring claimed
patience of "60 × ≤5 s ≈ 5 min".  It never counted the read timeout each doomed
attempt burns first: with `model_timeout_s = 300`, one logical call is
60 × (300 + 5) s = **5.1 hours**.  Measured on a fake clock, a call in a sustained
storm consumed **5.59 h**; with the deadline it consumes **15.1 min**:

| | one call in a sustained storm |
|---|---|
| attempts-only budget (before) | 5.59 h |
| `RETRY_DEADLINE_S = 900 s` (after) | 15.1 min |

`rotate_with_retries` now takes `max_total_s` and stops retrying once the wall clock
says so, in both the storm branch and ordinary backoff.  A slow call that is *making
progress* is never cut — the deadline bounds retrying, not the call.  Past it the cell
is `infra_failed`, which is excluded from every rate and re-runnable with
`--redo-status infra_failed` (`eval/docs/EVAL.md` §7).

**The wall-clock ceiling is only checked when money is spent.**  `BudgetGuard.check()`
is called from `spend()`/`charge()`, so a run whose calls never *complete* is never
tested against `max_minutes`: nothing is billed, so nothing is checked.  That is how a
`--max-minutes 90` run reached **200 minutes**.  The retry deadline is the root fix —
every call now terminates within ~15 min and charges or raises, so the ceiling is
evaluated again — but the residual remains real: **`max_minutes` is enforced at
billing points, not on a timer.**  Closing it properly means giving the runner a
reference to the guard and checking at stage boundaries; not done here, because it is
plumbing through several layers and deserves its own measured change.

## 23. The key pool was per-PROCESS, so N batteries multiplied the quota by N — the slots are machine-wide now

§20 measured the concurrency knee at 64 in-flight and shipped it as the default.  That
number was measured with **one process and nothing else running**, and the limiter it
configures is per-process by construction:

```python
_pools: dict[tuple[str, ...], KeyPool] = {}      # codeverse3d/models/gemini.py — MODULE level
def shared_pool(...):  """One KeyPool per distinct (key list, quota) so limiters are process-wide."""
```

Process-wide is not machine-wide.  Every `bench/run_bench.py`, every `compare_backends.py`
and every `3dcode make` is its own OS process with its own pool, each believing it owns the
whole 22-key quota and each allowing its own 64 in-flight.  On 2026-08-24 six batteries
from different waves ran at once: **~384 concurrent calls against a quota sized for 64**.

What that did, measured on the same keys and the same model within 15 minutes:

| condition | `gemini-3.7-flash` trivial call |
|---|---|
| ~15 of our processes running | **0 / 8 succeeded** |
| machine quiet (we killed everything) | **3 / 12 succeeded** (25 %), 7.4 – 25.5 s |
| machine quiet, `gemini-3.1-pro-preview` | **4 / 4 succeeded**, 3.1 – 29.7 s |

Read it honestly, in both directions:

* **The provider really was degraded.**  25 % success at four concurrent trivial calls is
  not something we caused, and pro was healthy on the same keys at the same moment, so
  this was flash-specific capacity on Google's side — not our key quota.
* **Our own concurrency turned a degraded service into a total outage.**  0/8 under load
  versus 3/12 quiet is the same model, same keys, minutes apart.

Three consequences:

1. **The knee is a per-process number and must be divided by the number of concurrent
   harness processes.**  Running six batteries at `max_in_flight = 64` is asking for
   6 × the concurrency the sweep found optimal — well past the point where §20 measured
   throughput *falling* (128 in-flight was worse than 64).
2. **It reframes §21's negative result.**  The StormGate lost its A/B, and the conclusion
   was "gemini's 503s are intermittent, so parking is a waste".  But that A/B ran inside
   ONE process while five other batteries kept hammering: a per-process gate can neither
   see nor slow the traffic actually causing the storm, so the polite process paid the
   latency and its siblings took the capacity it freed.  The gate was measured in a
   setting where it could not win.  Its negative result stands for the configuration
   tested and should NOT be read as "back-pressure does not help".
3. **The operational rule until 2026-09-22** was to keep the SUM of `max_in_flight` across
   every harness process at or below the knee (64), with `health.pool_budget()` reading each
   sibling's cap out of `/proc/<pid>/environ`.  It was advice — `eval/bench/ab_plan.py`
   refused to start on it, `3dcode make` and `bench run` never looked — and it guessed
   siblings from argv against a hard-coded list of eval entry points, which missed
   `python -m bench.ab_plan` for a day, missed every pip console script for longer, had
   to learn not to charge an ab_plan driver for its children, and could not see a process
   whose cap was unparsable.

**Machine-wide slots (2026-09-22).**  `max_in_flight` is now N lock files,
`<cache_dir>/slots/gemini/00.lock …`, and a harness API call holds one with
`fcntl.flock(LOCK_EX | LOCK_NB)` for exactly as long as it is out (`models.retry.Slots`,
taken inside `KeyPool.acquire` / `try_acquire`, handed back by `release`).  Every process
draws from the same files, starting its sweep at a random slot, so the machine never has
more calls in flight than the largest N any process runs with, and a process with a smaller
N only ever uses its own first N.  A process that finds every slot busy waits inside the
call's own budget; nothing refuses to start any more, and the `/proc` scan, its entry-point
lists and ab_plan's admission check are gone.  ab_plan still pins both children to its
`--max-in-flight` (16 by default) — the two arms now share those 16 slots.

What a slot guards is one harness API call — planner, judge, caption, texture image,
single-shot generation — the unit §20 measured.  A vendor CLI session takes its key from the
same pool (rotation, cooldowns, the dead-key bench) but no slot: a session is minutes of
agent time, and 64 of them holding slots would starve every judge and planner call on the
box.  Their concurrency stays bounded per process by `Limits.max_parallel_agents`.

The deferral this section used to end on was about staleness — a crashed holder stranding
its share of a file-locked bucket.  flock has none: the kernel drops the lock when the
holder's descriptors close, SIGKILL and the OOM killer included (`proc.exclusive` relies on
the same property).  `tests/models/test_slots.py`, six runs on this box: 8 processes × 6 holds
× 0.15 s on 4 slots peaked at exactly 4 in flight (mean 3.84 – 3.93, wall 1.83 – 1.87 s
against a 1.80 s floor); caps of 2 and 4 in one directory peaked at 4 together and 2 among
the N=2 processes; a slot held by a SIGKILLed process came back 37 – 70 ms after the kill
(one 50 ms poll); 12 threads of one process on 3 slots peaked at exactly 3; with every slot
held elsewhere a waiter gave up at 0.000 s and 0.300 s for deadlines of 0 and 0.3 s.
`3dcode doctor` prints the `in-flight slots` row: how many are busy machine-wide right now.

## 24. A preflight probe must look like the work it is guarding

The §23 preflight passed `gemini-3.7-flash` and the battery immediately lost its first
two cells — `infra_failed`, 15.1 and 15.3 min, $0.00 generated.  The gate was wrong in
two independent ways, both now fixed, both worth stating because they generalise to any
health check in front of a batch job.

**1. The probe was five tokens; the work is twelve thousand.**  During this degradation
a trivial prompt answered while the planner calls beside it were still 503-ing — the
fidelity wave observed the same thing independently.  Re-measured at workload size
(~10 k tokens), the same service that looked fine on tiny prompts is not:

| model | trivial prompt | ~10 k-token prompt |
|---|---|---|
| `gemini-3.7-flash` | 4 / 4 | **4 / 6** |
| `gemini-3.1-pro-preview` | 4 / 4 | **4 / 6** |

`probe` now sends `PROBE_TOKENS = 8000` of filler and still asks for a one-word answer,
so it costs input tokens — the thing under test — and about $0.003 per preflight.

**2. The bar was 50 %, which for a pipeline means zero.**  A preflight guards a
BATTERY, and one cell is dozens of model calls that must all land: at per-call success
p a 20-call cell completes with p²⁰, so p = 0.5 is not "half healthy".  Retries soften
it to roughly p ≥ 0.7 for a coin-flip chance at a cell, so `DEFAULT_MIN_OK` is now
**0.75** over `DEFAULT_SAMPLE = 6`.  The asymmetry settles the tie: refusing wrongly
costs one 2-minute re-probe, starting wrongly costs hours.  This one is a judgement
call, not a measured optimum, and it is written here so it can be revisited with data.

What did work, in production, on those two lost cells: they were recorded
`infra_failed` with `score=None` rather than a hard 0.0 (§7 of `eval/docs/EVAL.md`), and the
retry deadline (§22) stopped each at ~15 min instead of the 56-87 min the same cells
burned earlier the same morning.

## 25. List price is not the bill, and a subscription costs none

`Usage.cost_usd` answers *"what would these tokens cost at list price?"*.  That is the
right number for a report, a $/complexity point, or a flywheel record — it is comparable
across backends and independent of who is paying.  It is the wrong number to call a bill,
because a backend on a flat-rate local subscription bills no dollars.

The harness had exactly one number and used it for both.

**Measured 2026-08-25**, `tsr_scn_temple_night`, `codex:gpt-5.6-sol`, `--profile
quality`.  The two Blender hero-asset sessions were priced at OpenAI list rates
(`agents/codex.py` → `estimate_cost("openai", …)`) and the run crossed the profile's
soft cap 6.8 minutes in:

```
budget.degraded  cost $7.712 exceeds soft cap $4.40 (55% of $8.00)
asset.judge_skipped  BronzeCenser  reason=soft_budget
asset.judge_skipped  StoneLantern  reason=soft_budget
```

Both heroes went unjudged and every later stage ran degraded — over a bill of **$0.00**.
The run's own cost ledger said $0.00 the whole time; `PROVIDER_PRICED_BACKENDS` and
`meters_own_calls()` had already got the ledger side right.  Only the guard disagreed
with it, and the guard is the half that changes what the run does.

**The split.**  The ledger and the reports keep pricing everything; the budget enforces
only what is billed.  `cost/billing.py` names the flat-rate backends
(`SUBSCRIPTION_BACKENDS` = codex, claude-code, agy, antigravity — `CLAUDE.md`
"Environment" is the source of that list), and `BudgetGuard` accumulates a second
counter, `billed_usd`, alongside `spent.cost_usd`.  `summary()` reports both:
`spent_usd` (billed, real dollars) and `notional_usd` (list price, what the reports want).

`gemini-cli` is deliberately **not** exempt: it authenticates with an API key, so its
tokens draw on a real per-token quota even when that quota is free.  Unknown backends
bill by default — a new provider nobody classified must be enforced, not exempted.
Getting it wrong the other way reports a run as free when someone was billed for it.

The corollary for benchmarking: an arm on a subscription backend and an arm on an API
backend do not bill comparably, so compare them on `notional_usd`, never on `spent_usd`.


## 27. A 503 is per key at any instant — rotate before you wait (2026-08-26)

The retry path treated Gemini's *"This model is currently experiencing high demand"* (503) as
model-wide: no rotation, a ≤ 5 s sleep per storm attempt (60 of them), the 900 s per-call
deadline — and, when the gate was on, every worker in the process parked.  Measured with one
tiny request per key fired in parallel, three rounds 20 s apart, in the middle of the day's
storm: `gemini-3.7-flash` answered on **15/22, 18/22 and 21/22 keys** while **5, 4 and 1**
keys returned 503 at the same instant (successful latencies 1.3–38 s; the slow ones were the
same few keys).  So at any moment most keys work and the next key is the cure.

`rotate_with_retries` adds the 503'd key to the call's failed set and rotates to a fresh key
for free (no sleep, no budget, no `max_attempts`) — **exactly the 429 rule: while an untried
key remains, rotation is free**.  It is a storm, and the bounded wait budget applies, only
once **every** key in the pool has 503'd inside one logical call (a 22-key pool rotates 21
times first).  What this buys per call is the whole storm wait it used to pay first (median
5 s × the storm streak, up to 900 s); what it costs is one more round-trip on a fresh key.
Follow-ups worth measuring: rank keys by recent latency (the 30 s keys are consistent), and
record the key index in `telemetry/usage.jsonl` so the distribution of calls per key can be
read instead of probed.

**Amended 2026-08-27 (owner's rule):** the first cut of this gated the rotation behind a
`storm_quorum` of 6 distinct 503'd keys — so a 22-key pool stopped rotating and started
sleeping with 16 keys untried.  The probes above say only **1–7 of 22 keys** are 503 at any
instant, and the 2026-08-26 logs carry **548 "capacity storm" lines with 0 429s**: the pool
had spare quota the whole time and the waits were pure loss.  `storm_quorum` is gone; the
guard is now `len(failed_keys) < len(pool)`.



**Follow-up, same day, from the time audit (51 storm-day runs vs 52 baseline; scripts retired 2026-08-28 — findings summarised in §28 below).**
Three accelerations, all additive and on by default:

*A caller clips the retry budget to what it can afford* (`ChatRequest.max_wait_s`, None = the
model's `RETRY_DEADLINE_S`).  Of the 246 297 s a storm-day run spent waiting on the provider,
**30 % (72 921 s) was 66 `model_error` spans** in which one call retried until the 900 s deadline —
median span 923 s, p90 2 743 s, i.e. three consecutive give-ups on ONE agent turn through
`api_agent.MODEL_RETRIES`; 45 sessions were hit, **~1 430 s per run**.  `GeminiModel` now passes
`max_total_s = min(900, max_wait_s)` to `rotate_with_retries`.  An agent turn asks for
`max(20, min(120, session time left))` (a successful storm-day call is 8.3 s p50 / 31 s p90) and
stops retrying once the session deadline has passed instead of sleeping 2 + 4 s past it (97
sessions overshot their timeout by 182 s median / 1 154 s p90); a judge sample gets
`SAMPLE_BUDGET_S = 240` for all its attempts (the verdict is 42 s p50 / 73 s p90, 50 / 103 s under
the storm; two rounds lost 1 162 s and 927 s to 3 × 300 s timeouts before a second sample answered
in 128 s); the planner 300 s (13.7 s p50 / 32 s p90, max 76 s; the storm-day plan stage waited
492 s median for 39 s of model time).  Anthropic / OpenAI honour the same `max_total_s =
ChatRequest.max_wait_s` (clipped to `RETRY_DEADLINE_S`) and a raised `ModelError` carries
`attempts` on every provider: since 2026-09-22 they run `rotate_with_retries` itself over a
fresh one-key pool (`parts.retry_one_key`: no cooldown, no storm patience, no hedge — the
same `max_attempts` × ≤ 3 s backoff their own `with_retries` loop gave them, now deleted).


*The retry of a 503 is hedged across keys* (`rotate_with_retries(hedge=2)`; `Settings.rate.hedge`,
`C3D_RATE__HEDGE=1` for the A/B).  Logged sleep was only 645 s per cell median — **13 % of the
wait**; `(wait − sleep) / storm lines` = **21.5 s per failed attempt** (p90 28.5, ~50 s late in a
storm): the cost of a 503 is the round-trip the provider holds before rejecting, not the ≤ 5 s
backoff.  Storm streaks average **4.7 attempts** (1 101 episodes / 5 143 lines).  From a call's
first 503 on, every further attempt is issued on two distinct fresh keys at once — the extra key
from `KeyPool.try_acquire`, never waited for, each request holding its own `max_in_flight` slot —
and the first success is returned; a loser finishes its own round-trip, reports its outcome to
the pool (a late success still reports its tokens) and releases its slot; when both fail it is
ONE storm attempt.  Expected rounds per streak drop from ~4.7 to ~1.7, i.e. ~60 % of the retry
wait: **≈ 930 s/run blender, 1 400 s threejs, 600 s cadquery, 450 s graphics**.  A 503 bills
nothing, so the hedge is free while it storms; only a success-then-success wastes one call
(cents for a chat turn — `GeminiImageModel` keeps `hedge=1` because an image is billed per image).


*The ledger records the key and the attempt count.*  `keys.py` scanned 1 542 files of the corpus
for the `"key": "…xxxx"` that `gemini.py:_once` puts in `ChatResponse.raw` and found none, so
"is one key hammered" was unanswerable.  `rotate_with_retries(stats=)` hands back `attempts`
(round-trips issued, hedged siblings included; 1 = clean) and `hedged`; `GeminiModel` puts both
in `raw` and `attempts` on the raised `ModelError`; `cost/instrument.py` copies the key suffix
(last 4 chars, never more) and `attempts` onto every `CallCost` row (`key`, `attempts`, both
defaulted so old rows load); `3dcode cost` adds a per-key table and a
`tries/call` column (`CostBucket.attempts_per_call`) whenever the ledger carries them.

## 28. Where the time goes — the 2026-08-26 audit

51 storm-day runs against 52 baseline runs, every stage and every model call, scripts in
`bench/time_audit/` (read-only over `bench/out`; a one-off, deleted 2026-08-28).  The numbers that decide what to build next:

* A blender object run is **1 811 s** median on a healthy provider and **4 726 s** under the storm;
  build + gates + render together are 3–19 % of a baseline run and 1–4 % of a storm run.  The
  agent session is where the time is: baseline 57 % model thinking / 11 % tools / 32 % waiting;
  storm day **22 % / 2 % / 77 %**.
* Waiting is not the backoff sleep.  Logged sleeps are ~13 % of the wait; a failed 503 costs a
  **21–50 s held round-trip** before the provider rejects it, and streaks average 4.7 attempts.
  **30 % of all storm-day waiting (72 921 s) is 66 give-up spans** — one call retrying to the
  900 s `RETRY_DEADLINE_S`, up to three times per turn through `api_agent.MODEL_RETRIES`,
  never clipped to the session's or the round's remaining budget.  18 of 51 storm runs ended
  with zero rounds; 14 of them overshot the bench ceiling by 837 s median because the ceiling is
  only checked between stages.
* Successful calls are also slower under the storm: flash p50 3.3 → 8.3 s, p90 17 → 31 s; the
  judge (pro) p50 42 → 50 s.  The judge is not the bottleneck at the median; its tail is
  (2 rounds lost 1 100 s each to three 300 s read timeouts).  The planner waits 492 s median
  per storm-day run for 39 s of model time.
* No telemetry row records which key served a call, so per-key distribution was unanswerable
  from the corpus (the §24 probe answered it directly).

Ranked by measured seconds per storm-day run: (1) clip the per-call retry budget to the
remaining session / round budget (~1 430 s/run) — (2) hedge a 503 retry across 2–3 keys
(~900–1 400 s/run) — (3) hedge / cap the planner and judge calls (~400 s/run + the judge tail)
— (4) plan cache on re-runs (548 s) — (5) no sleep on 503 (≤ 13 %; landed with §24) — (6) enforce
the ceiling inside a round (837 s sooner on killed runs) — (7) flash as the loop judge (~60 s
baseline) — (8) 30 s render cap (≤ 100 s).  (1)–(3) and the key/attempt ledger fields landed the same afternoon (§27's follow-up paragraphs).

**Addendum 2026-08-27 — the render tail's root cause.**  The 30–100 s renders (and the 330 s
timeouts) were never rendering: node's own `timing_ms` reported ~0.55 s for the same GLBs while the
Python wall clock read 100+ s, and 8 concurrent renders finished in 1.0 s total.  The cost was a
**wedged shared browser**: the daemon's Chrome degrades after hours alive (WSL GPU decay + WebGL
pages leaked by SIGKILLed clients — every render timeout leaks one), and then *every* CDP roundtrip
(`newPage`, `page.close`) stalls ~100 s while the heartbeat keeps it alive forever.  Fixed in
`runtime_js/`: (1) `gpu_launch.cjs` connect canary — one bounded `pages()` (6 s) per connect; an
unresponsive or overgrown (>12 pages) browser is poisoned and a fresh daemon spawned (~1 s once,
measured: leak 14 pages → next render 3.5 s, then 0.7 s steady); (2) `browser_daemon.cjs` reaps
pages older than `C3D_PAGE_TTL_MS` (default 8 min > every harness ceiling); (3) cleanup steps in
`render_glb.mjs` / `host_page.mjs` are time-bounded (≤3 s each) — the record is on disk before
cleanup runs.  Step-level stderr breadcrumbs (`[render_glb] <step> +ms`) stay in for the next audit.

## 29. Fewer turns — first turn read out (`C3D_FEWER_TURNS`, `turns_v1`, 2026-08-26)

Six blender prompts, plan-pinned pairs, flash, 3 rounds, storm afternoon; variant = `build` folds
connectivity + contract in, `write_file` returns a lint verdict, refine inlines ≤ 3 files, the
baseline prompt asks for every file in turn one.

| prompt | score off → on | sessions | flash calls | model s | wall s | $ |
|---|---|---|---|---|---|---|
| dining_chair | 0.946 → 0.969 | 6 → 6 | 177 → 96 | 1327 → 731 | 1459 → 1716 | 1.66 → 1.08 |
| hand_drill | 0.600 → 0.528 | 6 → 15 | 142 → 272 | 1498 → 3343 | 1991 → 3068 | 1.82 → 3.16 |
| coffee_grinder | 0.937 → 0.600 | 7 → 3 | 291 → 100 | 2505 → 1225 | 5190 → 3528 | 2.54 → 1.50 |
| machinist_vise | 0.576 → 0.600 | 6 → 10 | 183 → 170 | 1427 → 1495 | 2648 → 2116 | 2.28 → 2.19 |
| spiral_stair | 0.961 → 0.750 | 3 → 2 | 83 → 66 | 631 → 510 | 1534 → 1268 | 1.22 → 1.04 |
| drafting_table | 0.600 → 0.928 | 7 → 3 | 223 → 77 | 1338 → 432 | 3026 → 899 | 2.51 → 1.21 |

Paired, n=6: score **−0.041** (sd 0.229, sign 3/6 — inside the 0.202 floor); variant/control
ratios **calls 0.81, model time 0.91, wall 0.89, cost 0.88**.  Per session the variant spends
~25 % fewer turns and never calls `check_connectivity` / `check_contract` (build carries them),
but the number of refine sessions per run is what moves the total (6 → 15 on the drill, 7 → 3 on
the table): the fan-out is decided by how many refine targets the judge and the folded-in gate
findings produce, not by the switch.  The two 0.6 results on the ON arm are `missing_must`
caps — sessions that finished earlier and verified less.  `read_file` did not drop (41–47 on
the vise / drill): the reads are of *other* part files, which the ≤ 3-file inlining does not
cover.

Verdict: stays **OFF** by default.  Keep the two mechanical pieces (checks folded into `build`,
the write verdict) — they cost nothing and remove a class of turn; the next turn of this loop
caps refine fan-out per round and inlines the whole part set under a size budget, and A/Bs the
turn-discipline prompt on its own, since "finish in fewer turns" is where the must-item misses
come from.

**Deleted 2026-09-22 (owner).**  The switch stayed OFF and the follow-up never ran, so the whole
bundle went: `C3D_FEWER_TURNS` / `Limits.fewer_turns`, the checks folded into `build`, the
turn-discipline block in four templates, and the refine prompt's inlined files for agent
sessions (single-shot still gets them: it has no read tool).  The OFF prompts render byte for
byte as before.  The mechanical half never outlived the api-agent anyway: `write_file` was
that agent's tool.

## 30. A tool's FAIL verdict is not an MCP error (2026-09-02)

`spatial/mcp_server.py` returned `is_error = not obs.ok`, and `obs.ok` was the tool's
**verdict** — so every gate that ran and answered FAIL reached the model as a broken call.

**Selector** (every number in this section, unless another one is named): the files
`eval/bench/out/*/**/run/trajectories/*/stdout.json` **as they stood at 2026-09-02 20:20 UTC**
— the corpus is live and grows with every battery, so a later re-run reads larger
numbers (2026-09-03 00:30: 390 files, 6 154 calls, 60 of 302 rounds) — 386 of them, of
which **224 are non-empty** — one per gemini-cli session that reported stats; the other 162 are
zero-byte (the CLI died or was killed before printing its JSON) and every one of the
386 belongs to a gemini-cli arm.  `run/telemetry/trajectories` is a symlink to the same directory:
count it once (a glob that follows it doubles every number).  Per file, sum
`stats.tools.byName["mcp_3dcode_<tool>"]`'s `count` and `fail`; `fail` is gemini-cli's
own tally of results that arrived with `is_error`.  Over those 224 sessions: **6 127
MCP tool calls, 1 720 (28 %) reported as errors.**

| tool | calls | reported as an error | the harness's own gate, for comparison |
|---|---|---|---|
| `joint_sweep` | 1 445 | 62 % | the round's `joint_sweep` GATE fails 57 of the 299 rounds that ran it (19 %) |
| `build` | 2 144 | 23 % | — (a build that does not compile IS the answer) |
| `check_contract` | 895 | 17 % | — |
| `check_connectivity` | 923 | 14 % | — |
| `isolate` | 35 | 49 % | — (these ARE mostly real usage errors: an unknown part name) |

(The gate column is a second selector: all 239 `run/record.json` under `eval/bench/out/`,
411 rounds.  An earlier draft of this section quoted 217 sessions / 1 404 sweeps / 63 %
— that was the same corpus minus the `.attempt1` retry sessions and did not reproduce;
these numbers do, with the glob above.)

A vendor CLI treats an errored tool call as a call that did not happen and retries it.
A retried request carries the whole session context: **a mean 118 700 prompt tokens**
(9 250 main-role requests over the 224 sessions), 73 % of them cache hits, so
**$0.030 per retry** at `gemini-3.7-flash`'s $0.75/M input + $0.075/M cached — a
*cache-blended* rate, not the list price ($0.089 at full input rate), and ~$0.040 with
the response the retry also pays for.

### The expensive half: an errored result re-sends its images as TEXT

An independent offline investigation of the recorded requests (its own selector: the
per-request logs, not the session stats above) found the retry is not the main bill.
`observation_content` attaches PNGs as MCP `ImageContent`.  On the success path
gemini-cli calls `transformMcpContentToParts` and sends a real `inlineData` part; on the
**error** path (bundle `chunk-DFPYJMVX.js:274288`) it builds
`"MCP tool '<name>' reported tool error ... with response: " + safeJsonStringify(rawResponseParts)`
— the image's base64 goes into the prompt as text:

| the same articulation contact sheet | bytes / chars | prompt tokens |
|---|---|---|
| `inlineData` part (success path) | 274 572 B | ≈ 516 |
| base64 inside an error string (error path) | 366 096 chars | ≈ 261 497 (**~507×**) |

It also escapes gemini-cli's own 40 000-char truncation (bundle `:349408`), which only
fires for a single-text-part MCP result or the shell tool.  The predicted jump matches
recorded per-request prompt jumps of +260 421, +256 907, +258 997, +257 840, +260 364 and
+261 477 tokens.  ~10 % of requests carried ~225 000 freshly *uncacheable* tokens, and that
10 % is **67 % of the whole uncached bill (~$400 of $873)** on the corpus it measured.
Non-blob requests already cache as well as the old in-process agent did (median 4 946
uncached tokens vs 5 639 per turn), so nothing about settings, tool ordering or `GEMINI.md`
needs changing — the blob is the whole anomaly.

`joint_sweep` is exactly where the two findings meet: it always attaches the articulation
contact sheet, and 900 of its 1 445 calls were errors.  Corroboration inside the session
stats above: the 205 sessions with ≥ 1 errored `joint_sweep` have a 73 % cache-hit rate and
a median session averaging 106 250 prompt tokens per request, against 88 % and 78 349 for
the 19 without (confounded — those are also the articulated runs — but it points the same way).

### The fix

A second flag, not a threshold.  `Observation.failed` is what the MCP server reports as
`is_error`; `ok` stays the verdict.  `Observation.error` builds every failure caught at the
`ToolDef.call` boundary (exception, `ToolUsageError`, missing or
unreadable artefact), and three tools set `failed` on a result they compose themselves,
where the failure is a fact about the result rather than an exception.  Which places those
are is a property of the code, so the list lives there (`spatial/registry.Observation.error`)
and not in three documents.

Every negative verdict now LEADS its text with `… FAIL` (`gate_observation` for the gates,
`JOINT SWEEP: FAIL — penetration …`, `FRAME GATE: FAIL — n error(s)`, `RENDER: FAIL — n
console error(s)`, `BUILD FAILED` as before) so the model reads the answer instead of
retrying the question.

And because the harness now decides what is an error, `mcp_server` bounds what one result
can hand over: `MAX_TEXT_CHARS` = 6 000 characters of text (twice the largest per-tool
limit — an Observation built by hand never passes through `observe`'s truncation), and
`max_images_for`: 4 images on an `ok` result, **1** on a FAIL verdict, **0** on a `failed`
one.  The vendor's own 40 000-char guard does not fire for a multi-part result, so this is
the only ceiling on the error path.

### Exit codes DID change for one command

`3dcode tools <name>` exits 1 on `not obs.ok` — the rule is unchanged, but `scene_probe`'s
`ok` changed meaning (it used to mean "the probe tool ran", the workaround for this bug),
so **`3dcode tools scene_probe` on a failing scene gate now exits 1 where it exited 0**.
Kept deliberately: every other gate tool already exited 1 on a FAIL, and the exit code
speaks to the human or script at the terminal, not to the model — the MCP boundary is the
one place where calling a verdict an error costs money.  The CLI panel prints three states
(`ok` / `FAIL` / `error`) to match.

What to watch on the next battery: in `tools stats`, `joint_sweep` / `build` / `check_*`
error counts → near zero (only genuine failures — no URDF, unreadable GLB, bad arguments),
calls per run down by the retries that used to follow each of them, per-request prompt
tokens without the ~260 k spikes, and the uncached share of `telemetry/cost.jsonl` down
with them.  The 62 % vs 19 % gap between the sweep TOOL's verdict and the round's sweep
GATE is a separate question — the tool flags any overlap past `tol_m` and any floating
link, while `sweep_findings` downgrades small rest overlaps and hinge gaps to WARN.

**Measured after the fix** (`eval/bench/out/wave2_lean`, the same battery and config as the
`aa_articulated` A/A above, 2026-09-03).  Both columns are printed by
`python eval/bench/session_stats.py eval/bench/out/aa_articulated eval/bench/out/wave2_lean`, which reads
each session's own stats block and each round's recorded `usage`:

| per generator request | before (`aa_articulated`) | after (`wave2_lean`) |
|---|--:|--:|
| sessions with stats / killed before printing | 60 / 102 | 59 / 86 |
| main-role requests | 2 537 | 2 657 |
| MCP tool calls reported as errors | 26.6 % (482 / 1 814) | **1.3 %** (25 / 1 863) |
| cache hit | 69 % | **90 %** |
| uncached prompt tokens per request | 38 521 | **13 480** (−65 %) |
| generator $ per round (median; the script prints medians only) | 1.572 | **0.950** (−40 %) |

**Correction (2026-09-03), and what it does NOT explain.**  The after column first read
1.4 % (48 / 3 480), 13 736 uncached tokens and median \$0.967 over "108 sessions / 5 082
requests".  It was computed by hand, by a method that is not in the tree, and
`eval/bench/session_stats.py` does not reproduce it: the ratios are 1.87x on MCP calls and on
requests but only 1.06x on sessions, and no mechanism tested accounts for that shape —
following the `run/telemetry/trajectories` symlink doubles the file count exactly (2.0x on
both batteries), counting every tool instead of the MCP ones gives 2 756 not 3 480, and no
union of recorded batteries lands on those totals.  The BEFORE column reproduces to the
digit, which says the method changed between the columns rather than the data.  The
hand-computed after column is therefore **withdrawn**, not explained; the table above is
what the committed script prints, and the script is what a later run should be compared
against.

The residual 1.3 % are genuine `Observation.error` cases: `joint_sweep` with no
`artifacts/robot.urdf` yet or an unparseable one, `check_connectivity` / `check_contract`
with no readable GLB.  The cache recovery is the larger half of the saving and was NOT
predicted by the retry argument alone: the base64 blobs were breaking the implicit-cache
prefix, so a request after one cost ~5x a normal request even when nothing was retried.

Secondary observation, not a controlled comparison: the paired sd of the battery run on
this code is 0.131 against the A/A's 0.225 on the old code (n_for_power for ±0.02: ~172
pairs against ~506).  Different runs and different arms, so it is an observation — but the
direction is the one the fix predicts, since a retry storm is variance.
