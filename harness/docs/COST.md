# Cost audit — where the money actually goes

Measured on **61 recorded runs** (`runs/e2e_*` ×5, `bench/out/{static_v1_flash,
articulated_v1_flash,graphics_v1_flash,scenes_v1_flash}` ×46, the 10 cells of
`bench/out/compare_v1_live2`) on 2026-08-23.  Every number below comes from the
recorded telemetry, not from an estimate: `record.json`, `events.jsonl` and the
per-call `usage` rows inside `trajectories/*/transcript.jsonl`.

Reproduce:

```
3dcv cost show runs bench/out/static_v1_flash …          # console
3dcv cost show runs bench/out/* --md docs/cost_report.md # full tables
3dcv cost show runs --recheck                            # re-price with today's table
3dcv cost prices [--unverified]                          # the price table + provenance
python bench/cost_report.py bench/out/<battery>          # writes <battery>/cost_report.md
```

The audit is `codeverse/cost/audit.py`; it reconstructs a per-call ledger from
old runs (`codeverse/cost/reconstruct.py`), so it works on every run recorded so
far — no re-instrumentation needed.  `bench/out/compare_v1_full` (a partial,
superseded compare battery) is excluded.

---

## 1. Headline

| | |
|---|---|
| total spend | **$89.88** over 61 runs (26.4 h of run time, 7,558 model calls) |
| per run | $1.47 (median $0.95) |
| **per passing artifact** | **$2.50** — 36 of 61 runs passed |
| input tokens | 278.6M, of which **80% are cache reads** |
| output tokens | 3.94M (+0.66M thoughts) |
| generation : judging : planning | **90.3% : 7.2% : 0.8%** |
| identified waste | **$27.35 (30%)** — see §5 |
| recorded vs measured | `record.total_usage` says $85.08; the ledger finds **$89.88** (§6) |
| re-priced with the corrected table | **$92.67** (+3.1%, §7) |

**The money is generation, not judging.**  Nine dollars in ten are the coding
agent writing and rewriting code; the judge is a rounding error next to it
(7.2%), the planner is noise (0.8%).  Any cost programme that starts with "use a
cheaper judge" is optimising 7% of the bill — and, as §8 shows, would lose money.

---

## 2. Per stage

| key | USD | share | calls | input | cached | output | $/1k tok | model time |
|---|---|---|---|---|---|---|---|---|
| plan | $0.7234 | 0.8% | 56 | (not recorded) | – | – | – | 0.0 h |
| assets (scene) | $5.61 | 6.2% | 587 | 13.24M | 61% | 236.8k | $0.00041 | 1.0 h |
| env (scene) | $0.7762 | 0.9% | 92 | 1.96M | 62% | 19.1k | $0.00039 | 0.1 h |
| zones (scene) | $4.75 | 5.3% | 452 | 15.55M | 76% | 140.8k | $0.00030 | 0.7 h |
| baseline | $25.76 | 28.7% | 1,815 | 86.76M | 85% | 1.39M | $0.00029 | 4.0 h |
| repair | $1.31 | 1.5% | 152 | 5.08M | 79% | 49.9k | $0.00026 | 0.2 h |
| **refine** | **$42.94** | **47.8%** | 4,243 | 154.50M | 80% | 1.82M | $0.00027 | 8.4 h |
| judge | $6.48 | 7.2% | 126 | 1.33M | 8% | 212.1k | $0.00383 | 1.2 h |
| texture | $1.10 | 1.2% | 16 | (event only) | – | – | – | 0.0 h |
| other / unattributed | $0.43 | 0.5% | 19 | 235.2k | 10% | 67.3k | $0.00142 | 0.0 h |

`skeleton`, `assemble`, `gates` and `render` cost **$0.00**: they are
deterministic harness work.  Blender build 0.13 s, gates ~2 s, an 8-view GPU
render ~1.5 s — the "cheap first" law is holding.

## 3. Per role, track, backend, model

| role | USD | share | calls | $/1k tokens |
|---|---|---|---|---|
| generator | $81.14 | 90.3% | 7,341 | $0.00029 |
| judge | $6.48 | 7.2% | 126 | $0.00383 |
| planner | $0.72 | 0.8% | 56 | – |
| image (texturing) | $0.61 | 0.7% | 5 | – |

| track | runs | USD | $/run | passed | **$/passing artifact** | median rounds |
|---|---|---|---|---|---|---|
| static_object | 37 | $55.95 | $1.51 | 23 | **$2.43** | 2 |
| articulated_object | 13 | $19.19 | $1.48 | 8 | **$2.40** | 2 |
| scene | 5 | $14.14 | $2.83 | **0** | **∞** | 2 |
| graphics | 6 | $0.59 | $0.10 | 5 | **$0.12** | 1 |

The graphics track is **20x cheaper per passing artifact** than the object
tracks (one single-shot generation, compiler feedback, no tool loop).  The scene
track has not produced a single passing artifact in this data set at $2.83/run.

| model | USD | share | calls | cached | note |
|---|---|---|---|---|---|
| gemini-3.7-flash | $74.08 | 82.4% | 7,400 | 80% | generator + planner + cheap judge |
| gemini-3.1-pro-preview | $6.23 | 6.9% | 109 | 7% | default judge |
| (event-only rows) | $5.40 | 6.0% | 44 | – | plan / texture / pairwise: cost recorded, tokens not |
| gpt-5.6-sol (codex) | $2.26 | 2.5% | 3 | 92% | one telescope run |
| claude-fable-5 (claude-code) | $1.90 | 2.1% | 2 | 0% | recorded as `claude-haiku-4-5` — see §7 |

## 4. Tokens, caching and the clock

**80% of every prompt is context we have already sent.**  259.8M of the agent
loop's input tokens correspond to only 13.3M tokens of *final* context — the
transcript is re-sent **19.5x** on average (median 22 model turns per session,
p90 52).  Implicit caching absorbs most of that: cache reads cost $16.98, and
the same traffic with a 0% hit rate would have cost **$242.70 instead of $89.88**.
Caching is already saving 63% of the bill.

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
| regression — a round scored **below** the best and was discarded | 19 | $14.23 | 15.8% |
| post-budget — a round completed after the budget was already gone | 9 | $8.76 | 9.7% |
| zero-delta round — score moved < 0.005 | 4 | $1.68 | 1.9% |
| unpromoted judge — a verdict on a round that never became best | 23 | $1.37 | 1.5% |
| repair loop that never converged (run did not pass) | 4 | $1.31 | 1.5% |
| **total** | 59 | **$27.35** | **30.4%** |

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

## 6. $4.80 of spend is invisible to `record.total_usage`

On 14 of 61 runs the reconstructed ledger is **larger** than the run's own
`total_usage` — $4.80 (5.6% of all spend) that the record, and therefore the
`BudgetGuard`, never saw:

* rounds the budget cut *after* the work was done (the round is not appended to
  `record.rounds`, and its cost is not folded into the total);
* retried agent sessions (`<label>.a2_rNN`) whose usage lands in the trajectory
  but not in the round;
* post-hoc texture passes (`furn_hard_rolltop_desk` ran four, $0.656).

Worst case: `art_hard_door_handle`, $1.38 off-record on a $4.45 run.

## 7. Price hygiene (checked 2026-08-23)

`models/pricing.py` now carries a `PROVENANCE` row per price — source URL,
`checked` date and a status of `verified` / `inferred` / `unverified` — exposed
as `price_provenance(provider, model)` and printed by `3dcv cost prices`.  Every
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
| verified (no change) | – | every other gemini / anthropic / openai row | now flagged `verified` rather than "approximate" |
| still `inferred` / `unverified` | – | `claude-haiku-4`, `o1-mini`, `gpt-5-codex`, `gpt-5.1-codex`, `gpt-5.2-codex`, `gemini-2.5-flash-image` | not listed on the pricing pages; `3dcv cost prices --unverified` lists them |

Per-image prices now live in the price table (`Price.image_usd`,
`IMAGE_USD_BY_SIZE`, `per_image_usd()`), and a test asserts they never drift from
`models/gemini_image.IMAGE_USD`.

**Two costing bugs the audit surfaced** (both in files owned by other engineers —
see the hand-off notes):

1. **gemini-cli runs were under-billed ~3.7x.**  `e2e_bench_threejs` recorded
   $0.68; its own `stdout.json` stats say 14.19M prompt tokens (13.47M cached) +
   128.8k output ⇒ **$2.09**.  The session was recorded before
   `Usage.input_tokens` became `tokens.prompt`; the raw envelope is still on
   disk, so `3dcv cost show --recheck` rebuilds the true number.  Across the
   three gemini-cli runs the correction is **+$3.43**.
2. **claude-code attributes the cost to the wrong model.**  The envelope's
   `modelUsage` shows `claude-fable-5` did the work ($1.165) and
   `claude-haiku-4-5` answered one utility call ($0.0015); the harness records
   the *first* served model, so the run is filed as haiku with
   `input_tokens=2`.  The dollar is right (the CLI reports `total_cost_usd`), the
   attribution is not.  Rows from such backends are marked
   `price_source="provider-reported"` and are never re-priced from the token
   table.

Re-pricing everything with the corrected table moves the total from $89.88 to
**$92.67 (+3.1%)**: +$3.43 gemini-cli, −$0.50 codex, −$0.13 elsewhere.

**Not modelled** (documented in `pricing.py`): cache *storage* fees for explicit
Gemini caching, Anthropic 1-hour cache writes, batch/flex discounts, data
residency (1.1x) and fast-mode multipliers, per-search server-tool fees.

## 8. Model routing — what to run where

| role | model | $/call | measured quality | when |
|---|---|---|---|---|
| planner | `gemini:gemini-3.7-flash` ★ | $0.013 | no failure attributable to the planner | always — the plan is 0.8% of a run |
| generator | `api-agent:gemini:gemini-3.7-flash` ★ | $0.52 | compare_v1 mean 0.835 (best arm) | default for every track with tools |
| generator | `single-shot:gemini:gemini-3.7-flash` | $0.05 | graphics: 5/6 passed, median 0.810 | glsl / opengl — one file, compiler feedback |
| generator | `gemini-cli:gemini-3.7-flash` | $0.73 | 0.827 — same as api-agent, 2x price, 2x wall | only when you need the CLI itself |
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

`codeverse/cost/routing.py` holds this table in code
(`default_route(Role.JUDGE)`, `pro_break_even()`, `samples_for_precision()`).

## 9. Ranked optimisation opportunities

Savings are estimated **on this data set** (61 runs, $89.88) unless stated.

| # | change | est. saving | confidence | where |
|---|---|---|---|---|
| 1 | **Stop refining after r01 unless the last delta ≥ 0.05.**  r02+r03 cost $12.16 and bought +1.44 score points across 8 of 20 rounds. | **$9–12 (10–13%)** | high (measured) | `RoundPolicy` (min_delta 0.02 → 0.05 from r02, or plateau_window 1 after r01) |
| 2 | **Check the budget *before* starting a round, against the estimated round cost**, not only between steps. | **$8.76 (9.7%)** | high (measured waste) | `orchestrator/budget.py` + `tracks/steps.py`; use `codeverse.cost.estimate_call` / median round cost |
| 3 | **Cap agent turns at ~25 and compact old tool results.**  39.3% of the agent bill is turn ≥20; 19 of the 33 sessions that ran ≥50 turns were cut off by their own budget. | **$8–15 (9–17%)**, needs a quality check | medium | `AgentJob.max_turns`, `api_agent` transcript compaction |
| 4 | **Fold the off-record spend into the budget** (retried sessions, cut rounds, texture passes). | $0 saved, **$4.80 of blindness removed** | high | §6; the ledger (`codeverse.cost.record_call`) makes it automatic |
| 5 | **Scene track: 73% of scene spend is assets+zones, 0/5 passed.**  Trim the per-zone context (each zone session re-sends the whole scene contract) and judge assets before zones start. | ~$1/run of $2.83 | medium | `tracks/scene*.py` |
| 6 | **Drop `oneshot:claude-code` from default compare arms** ($1.04/artifact at 0.673 — the worst score per dollar measured). | bench-only | high | `bench/compare_backends.py` arms |
| 7 | **Gate the texture pass on the materials criterion** (< 0.7) — 5 passes ran, 2 shipped, $1.10 spent. | ~$0.5 | medium | `texturing/run.py` |
| 8 | **Keep the pro judge; do not "save" money there** (§8). | avoids a **$45** increase | high | – |
| 9 | **Price-table corrections** (§7) — accuracy, not savings: the reported bill was 3.1% low overall and 3.7x low on gemini-cli runs. | – | done | `models/pricing.py` |
| 10 | **Do not chase prompt caching on judge/plan calls in general** — measured below the cache floor (§4). | – | done (measured) | – |
| 11 | **Grow the judge's stable head past one cache block** (system + rubric + defect checklist ≈ 3.5k → ≥ 4,088 tokens, e.g. by moving the shared scoring rules and a fixed worked example into the system block).  Measured: one cached block = 4,079 tokens = $0.0073 per pro verdict. | ~$0.8 (112 verdicts) | medium | `judges/prompt_builder.py` |
| 12 | **Know that `n_samples > 1` pays full price for every sample**: the montage shuffle voids the cache (measured 0 cached vs 4,079 on an identical resend, −22%/sample).  Keep the shuffle — it is the noise control — but count it when choosing `n`. | – | measured | `judges/prompt_builder.py` |

Not recommended on the evidence: cheaper generation models (a bare one-shot
flash scored 0.14 and produced no buildable code in 3 of 5 cells — the loop, not
the model, is what makes the artifact), and cheaper judges (§8).

## 10. The cost ledger (`codeverse/cost/`)

```python
from codeverse.cost import record_call, load_ledger, summarise

record_call(res.usage, run=ws.slug, round=idx, stage="refine", role="generator",
            label=job.label, outcome=res.exit_reason,
            ledger=ws.root / "cost_ledger.jsonl")     # one append-only JSONL row

rows = load_ledger(ws.root / "cost_ledger.jsonl")
summarise(rows).dimension("stage")["judge"].cost_usd
```

One row per call: `ts, run, round, stage, role, backend, provider, model,
input/cached/output/thoughts tokens, cache_write, tool_calls, the three unit
prices actually used, price_source + price_approximate + price_checked
(provenance), cost_usd, recorded_usd, latency_ms, cache_hit, outcome, n_calls,
source`.  Writing never raises and never blocks a run; unknown models are
recorded at $0 **and flagged**, never silently dropped.

Until the call sites are wired, `codeverse.cost.reconstruct` rebuilds the same
rows from `record.json` + `events.jsonl` + `trajectories/**` — that is what this
audit runs on, and it reconciles to `record.total_usage` on every run (or says
why it does not, §6).

Two helpers exist for the callers:

* `estimate_call(model_id, prompt=…, n_images=…, output_tokens=…)` →
  `CostEstimate` before the call; `CostGuard(budget_usd).check(est)` decides.
  An unpriceable model is **allowed but flagged** — refusing to run because we
  cannot price something would be worse than running it.
* `Block(...)` + `order_blocks(...)` + `prefix_signature(...)` for cache-friendly
  prompt assembly, and `prefix_report(prompts)` to measure whether a family of
  prompts really shares a prefix (§4).

## 11. Caveats

* `plan`, `texture` and `pairwise` costs come from events that record a dollar
  but no tokens, so those rows have no token composition ($6.13 of $89.88).
  Wiring `record_call` at those call sites fixes it.
* Wall clock per run is the sum of the stage/round clocks (the event span
  includes hours a bench run spent queued behind other runs, and
  `budget.elapsed_min` restarts on `3dcv resume`).
* "Regression" waste is an upper bound: a round that scored worse still produced
  the diff that informed the next refine task.
* The compare battery is only 2 prompts × 5 arms; its per-arm numbers are
  directional, not significant.
* Prices were read from the providers' public pages on 2026-08-23 and are the
  standard, non-batch, global-endpoint tiers.
