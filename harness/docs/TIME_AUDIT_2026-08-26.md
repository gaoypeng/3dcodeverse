# Where the time goes — 3dcodeverse harness, storm day (2026-08-26) vs baseline (08-23/25)

Corpus: 51 storm-day runs (fancy_v1 28 cells, h2h_brilliana 12, h2h_scene 5, refs_v1_graphics 6; 45 finished) and
52 baseline runs (static_v2_flash 20, scenes_v1_flash 4, teaser 28).  All api-agent gemini-3.7-flash generator,
gemini-3.1-pro-preview judge.  Scripts (read-only, in this directory): `corpus.py` `turns.py` (shared),
`stage_times.py` (§1), `calls.py` (§2), `sessions.py` (§3), `keys.py` (§4), `judge_plan.py`, `storm_attempts.py`,
`deadline.py` (§5 inputs); their raw output is in `out_*.txt`, per-run rows in `stage_runs.jsonl`.

**How "waiting on the provider" was measured — and what is NOT recorded.**  `telemetry/cost.jsonl.latency_ms` is the
stopwatch of the *successful* attempt only (`gemini.py:_once`; `cost/instrument.py` uses `usage.latency_ms or ms`), so
backoff sleeps, failed 503 round-trips and KeyPool waits leave no row; only a call that fails outright gets an `error`
row whose latency is the whole wrapper span.  There is no `attempts`/`key` field.  `worker.log` (fancy cells only) has
the storm lines but no timestamps.  So: generator waiting = `t(assistant row) − t(previous transcript row) − latency`
per turn (`turns.py`; 88 % of baseline turns give < 1 s, i.e. harness overhead), a `model_error` row's span counts
fully; judge/planner waiting = stage span − Σ ok-latency.  "wait" below is thread-time (scoped rounds run 3–4
sessions in parallel), which is why it can exceed wall.

## 1. Run wall and stages — median / p90 seconds (n = runs (finished))

| corpus | track | n | wall | plan | generate | build | gates | render | judge | wait (thread) | session wait | judge wait | killed-run span |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| base | static/blender | 23 | 1811/3073 | 43/136 | 900/2102 | 0/1 | 4/69 | 3/68 | 116/448 | 321/1719 | 248/1712 | 1/275 | 0/654 |
| base | static/cadquery | 4 | 3288/5782 | 307/429 | 1873/3135 | 14/20 | 79/89 | 74/86 | 331/897 | 521/2054 | 285/1675 | 0/0 | 0/1724 |
| base | static/threejs | 3 | 2403/5152 | 459/584 | 1823/3118 | 0/1 | 66/117 | 66/107 | 123/1366 | 2118/3956 | 1905/3092 | 0/466 | 0/0 |
| base | graphics | 9 | 926/2003 | 77/122 | 663/1714 | 5/13 | 0/0 | 5/12 | 199/372 | 421/1090 | 275/1019 | 0/132 | 0/0 |
| base | scene | 10 | 4118/5097 | 50/120 | 2859/4122 | 8/23 | 22/83 | 21/81 | 284/490 | 1293/4214 | 1292/4096 | 0/103 | 0/296 |
| base | articulated | 3 | 5869/6376 | 325/417 | 3430/3876 | 1/2 | 177/195 | 2/41 | 98/1005 | 1191/1886 | 925/1145 | 0/573 | 0/2195 |
| storm | static/blender | 23(20) | 4726/5536 | 0*/577 | 1989/3127 | 0/0 | 1/18 | 1/16 | 38/116 | 5356/10333 | 5030/10333 | 0/4 | 2409/5161 |
| storm | static/cadquery | 8(7) | 6649/8344 | 0*/749 | 3159/5401 | 4/14 | 3/95 | 2/94 | 176/666 | 4116/5222 | 3002/4807 | 43/415 | 902/2265 |
| storm | static/threejs | 9 | 5328/7802 | 0*/548 | 3595/5501 | 0/0 | 3/5 | 1/3 | 94/430 | 6747/10174 | 6579/8401 | 27/370 | 1606/3648 |
| storm | graphics | 6 | 6003/8818 | 380/628 | 2971/3620 | 12/13 | 0/0 | 12/13 | 228/574 | 3605/6488 | 3223/5897 | 5/55 | 1001/2330 |
| storm | scene | 5(3) | 4404/4711 | 576/646 | 1406/2960 | 1/3 | 4/8 | 3/7 | 0/107 | 1218/2823 | 1026/2823 | 0/76 | 0/583 |

\* fancy_v1 cells get a pre-computed plan (`stage.cached`).  "killed-run span" = time inside a round that never reached
`round.done`: 18 of 51 storm runs ended with **zero rounds** (11 of 23 blender cells, median 4 523 s each), 14 of them
stopped by the bench `max_minutes 60` ceiling, which is only checked between stages, so they overshot it by 837 s
median / 1 560 s p90.  Per round: generate 314→1 008 s (blender), 989→2 049 s (threejs); judge 48→38 s (blender),
110→76 s (cadquery) — the judge stage is **not** slower on storm day at the median; its tail is (§2).  Build 0.1–4 s,
gates 1–80 s, render 1–90 s: together 1–4 % of storm wall, 3–19 % of baseline wall.

## 2. Per model call

Successful-call latency (s), from the ledger:

| model / role | corpus | n | p50 | p90 | p99 | max |
|---|---|---|---|---|---|---|
| gemini-3.7-flash generator | base | 8097 | 3.3 | 17.2 | 49.6 | 172 |
| gemini-3.7-flash generator | storm | 5867 | **8.3** | **31.1** | 74.9 | 239 |
| gemini-3.7-flash planner | base / storm | 60 / 36 | 13.7 / 23.4 | 32 / 43 | 53 / 68 | 76 / 68 |
| gemini-3.1-pro-preview judge | base / storm | 328 / 67 | 42.2 / 50.5 | 73 / 103 | 129 / 165 | 209 / 171 |

Even the calls that succeed are **2.5x slower** under the storm (think time per session 106 → 283 s, §3).
Calls per run (ok, median/p90): flash 176/292 base vs 102/213 storm; pro 5/15 vs 1/5.  Error rows: flash 41 → 133,
pro 0 → 5.  Retries from transcripts (a turn whose wall exceeds its latency by > 1 s):

| corpus | track | runs | calls/run | calls with ≥1 retry /run (share) | wait per retried call med/p90 | retry-wait per run med/p90 | 900-s give-up rows |
|---|---|---|---|---|---|---|---|
| base | static/blender | 22 | 186/302 | 10/31 (6 %) | 10.9/63.8 s | 263/1712 s | 1 |
| base | scene | 8 | 216/265 | 28/43 (13 %) | 19.8/116 s | 2120/4096 s | 4 |
| base | graphics | 9 | 25/28 | 3/8 (20 %) | 32/158 s | 275/1019 s | 17 |
| storm | static/blender | 23 | 125/257 | 33/65 (24 %) | **47/333 s** | **5030/10333 s** | 30 |
| storm | static/cadquery | 7 | 87/148 | 18/30 (21 %) | 55/389 s | 3103/4807 s | 3 |
| storm | static/threejs | 9 | 160/275 | 59/71 (32 %) | 40/198 s | 6579/8401 s | 8 |
| storm | graphics | 6 | 44/49 | 10/14 (33 %) | 76/387 s | 3223/5897 s | 24 |
| storm | scene | 3 | 36/243 | 4/49 (20 %) | 12/70 s | 1130/2823 s | 1 |

Where the storm-day wait goes (246 297 s over 5 799 turns): **30 % (72 921 s) is 66 `model_error` spans** — a call that
retried until the 900-s `RETRY_DEADLINE_S` (median span 923 s, p90 2 743 s = three consecutive give-ups on one turn
via `api_agent.MODEL_RETRIES=3`); 45 sessions were hit, ~1 430 s per storm run on average.  The other 70 % is ordinary
storm streaks.  From the 28 fancy worker.logs: 5 143 flash storm lines + 158 pro, 46 flash + 10 pro give-ups, 44 pro
read-timeouts (300 s each).  Logged **sleep is only 645 s/cell median (1 525 p90), 21 943 s in total = ~13 % of the
wait**; `(wait − sleep) / storm lines` = **21.5 s per failed attempt** (p90 28.5), and the streak depth at give-up
(median 18 of 60) says 900/18 = ~50 s per attempt late in a storm.  The cost of a 503 is the round-trip the provider
holds before rejecting, not the ≤ 5 s backoff.  Judge: 2 of 56 storm rounds lost 1 162 s and 927 s (3 × 300-s
timeouts before the deadline) then a second sample answered in 128 s → `judge_s` 1 818 / 1 593 s; judge wait sum
6 847 s / 56 rounds (median 1.8 s, p90 370 s).  Planner: storm plan stage 548 s median (p90 995) for 39 s of model
time → **492 s of waiting per run that plans**; baseline 69 s (17 s model).  Outside the run, the fancy cells spend a
further 252 s median / 935 s p90 in the driver's pairwise + final pro scoring (`cell.wall_s` − run span).

## 3. Per agent session (api-agent transcripts): thinking vs tools vs waiting

| corpus | track | sessions | turns med/p90 | duration med/p90 | think s | tool s | wait s | think % | tool % | wait % |
|---|---|---|---|---|---|---|---|---|---|---|
| base | static/blender | 155 | 25/60 | 158/446 | 106 | 6 | 12 | 57 | 11 | 32 |
| base | static/cadquery | 10 | 30/60 | 511/1659 | 277 | 23 | 129 | 56 | 9 | 34 |
| base | static/threejs | 32 | 19/38 | 298/715 | 137 | 25 | 117 | 43 | 12 | 45 |
| base | graphics | 24 | 8/17 | 141/754 | 87 | 5 | 31 | 42 | 2 | 56 |
| base | scene | 82 | 20/30 | 336/933 | 128 | 32 | 90 | 35 | 14 | 52 |
| base | articulated | 6 | 34/38 | 1259/1825 | 602 | 213 | 361 | 45 | 23 | 32 |
| storm | static/blender | 130 | 20/45 | **1528**/2408 | 283 | 4 | **976** | 22 | 2 | **77** |
| storm | static/cadquery | 22 | 26/49 | 1802/2403 | 417 | 28 | 1025 | 30 | 2 | 68 |
| storm | static/threejs | 57 | 23/46 | 1288/2181 | 340 | 10 | 707 | 29 | 3 | 69 |
| storm | graphics | 23 | 9/17 | 841/2932 | 179 | 5 | 612 | 17 | 1 | 83 |
| storm | scene | 11 | 19/57 | 581/890 | 194 | 15 | 207 | 29 | 4 | 67 |

Tool execution (pooled, median / p90 / seconds per session): blender `render_views` 0.8/61 → 9.6 s, `render_sheet`
0.9/61 → 7.4, `isolate` 0.7/61 → 3.9, `build` 0.5/0.7 → 2.3, `check_connectivity` 0.13/0.5 → 0.6; cadquery `build`
3.3/5.9 → 17 s; threejs `render_views` 2.5/87 → 16 s, `build` 1.0/1.7 → 5; graphics `gl_probe` 1.0/1.3 → 2.2,
`build` 4.8/6 → 2.0, `gl_frames` 2.8/3.2 → 1.9; scene `scene_views` 6.1/16 → 25 s, `scene_probe` 2.4/16 → 17,
`build` 3.1/8.3 → 15; articulated `joint_sweep` 50/93 → 259 s.  read/write/edit/list/check_contract/measure ≈ 0.
Renders have a 60-s-cap tail (p90 = 61 s) but total tool time is ≤ 30 s per session everywhere except `joint_sweep`.
Sessions past their 1 800-s job timeout (exit timeout/error): 97 on storm day, overshoot 182 s med / 1 154 s p90,
41 355 s in total — the deadline is only checked at the top of a turn, never inside the retry loop.

## 4. Key rotation — evidence and mechanism

**No telemetry row records which key served a call.**  `keys.py` scanned 1 542 files (cost.jsonl, transcripts,
result.json, rounds, record.json): zero occurrences of the `"key": "…xxxx"` that `gemini.py:_once` puts into
`ChatResponse.raw`, and no key-like field anywhere (only `settings.key_pool_size`).  Per-key distribution and
"is one key hammered" are therefore unanswerable from the corpus — that is the finding; `KeyPool.stats()` exists but is
only surfaced by `3dcv doctor --live`, never persisted.  How rotation works today (`keypool.py`, `retry.py`,
`storm.py`, `gemini.py`, `agents/api_agent.py`, `judges/vlm_judge.py`):
1. `KeyPool.acquire` is **round-robin per call**: the cursor advances past the chosen key, so consecutive calls (and
   consecutive retries of one call) land on different keys by construction, subject to per-key RPM (1 000/min) and TPM
   (1 M/min, reserved from the estimated prompt tokens, reconciled on report), a ≤ 5 s 429 cooldown, health ≥ 0.3,
   a 1-h bench for dead keys, and a process-wide `max_in_flight = 64` semaphore; it blocks ≤ 120 s if nothing is ready.
2. Key-scoped errors (401/403, invalid key) rotate at once for free; 429 rotates for free with a 0.5 s pause while an
   untried key remains, then counts against `max_attempts = 6` with backoff 1·2^n capped at `MAX_WAIT_S = 5`.
3. **503/529 = "storm" branch**: sleep `min(5, 2^storm × [0.75, 1.25))` s, re-acquire (a new key), up to 60 storm
   attempts that do NOT consume `max_attempts`, bounded only by `RETRY_DEADLINE_S = 900` — checked **after** a failed
   attempt returns, so a 300-s read timeout (`model_timeout_s`) can push one call to ~1 200 s.  Other retryables
   (timeouts, bad JSON, empty candidates) get the 6-attempt/900-s budget.  `StormGate` exists but is OFF (lost its A/B).
4. Above that, `api_agent._generate` retries a failed *turn* 3x (sleep 2, 4 s) and `VlmJudge._sample` retries a
   sample 3x, each retry re-entering the full 900-s machine: one turn can burn 2 700 s; a session's `timeout_s` and the
   run's `max_minutes` are only checked between turns / stages.  Each `ab_plan` cell is its own process (own pool).
What costs wall clock under a 503 storm: (a) every attempt is **sequential and single-flight** — one request in the
air per logical call, and a failed 503 costs 20–50 s of held round-trip before the ≤ 5 s sleep even starts; (b) no
hedging across keys, though each attempt already lands on a fresh key; (c) the 900-s budget is per *call*, multiplied
by the outer 3x loops, and never clipped to the remaining session/round/run budget; (d) the judge's 200 k-token call
gets the same 300-s read timeout x 3 before its deadline; (e) a scoped baseline round waits for the slowest of 3–4
sessions, so one session's give-up chain stalls the round and the run runs into the 60-min ceiling with nothing built.

## 5. Accelerations, ranked by measured seconds per storm-day run (base-day gain in brackets)

1. **Clip the per-call retry budget to the remaining session/round/run budget and cap it (~120 s inside an agent
   turn; a turn that still fails ends the session as `error` and the round salvages what exists).**  Removes the 66
   give-up spans: 72 921 s / 51 runs = **~1 430 s/run mean** (~800 s per span at a 120-s cap), plus the 837 s median
   overshoot on the 32 ceiling-killed runs and the 41 355 s of session overshoot.  Code: `retry.py` (`max_total_s`
   from caller), `api_agent._generate` (check `t_deadline` inside `MODEL_RETRIES`), `vlm_judge._sample`,
   `tracks/generation.py` (`timeout_s = min(agent_timeout_s, budget remaining)`).  [base: 7 137 s / 52 runs ≈ 140 s]
2. **Hedge the retry of a 503: after the first 503, issue the next attempt on 2–3 keys concurrently, take the first
   success (a 503 bills nothing, so the hedge is free while storming).**  Storm streaks average 4.7 attempts (1 101
   episodes / 5 143 lines) at 21–50 s each; a 3-way hedge cuts expected rounds to ~1.7, i.e. ~60 % of the retry
   wait: 33 retried calls × 47 s × 0.6 ≈ **930 s/run blender**, 59 × 40 × 0.6 ≈ **1 400 s threejs**, ~600 s cadquery,
   ~450 s graphics.  Code: `retry.py` + `gemini.py` (a small thread fan-out), validate with `concurrency_probe.py`.
3. **Planner and judge: hedge on a second key after ~1.5 × p90 (60 s planner, 150 s judge) and cap the sample at
   ~200 s instead of 3 × 300 s.**  Planner: 492 s median waiting per run that plans (h2h/refs/scene; **~400 s/run**);
   judge: 3 150 s over the 2 timed-out rounds = **~56 s/round mean**, `judge_s` p90 451 → ~170 s.  Code
   (`vlm_judge._sample`, planner call site) or a role-specific `model_timeout_s` (today one global 300 s).
4. **Pre-compute / cache the plan per prompt** (settings — `ab_plan` already does it: fancy plan stage = 0 s):
   **548 s/run** saved on any re-run or second arm of the same prompt on storm day; 69 s baseline.
5. **Rotate to the next key with no (or 0.5 s) sleep on 503** (settings: `GeminiModel(base_delay, storm_max_delay)`):
   the sleeps are 645 s/cell median, 1 525 p90 — **≤ 13 % of the wait**; only worth it together with 2.
6. **Enforce the bench ceiling inside a round** (code, `lifecycle` budget check in the session fan-out): the 14
   zero-round killed runs would fail **837 s (p90 1 560 s) sooner**; they produce nothing either way unless salvage
   builds the parts that did finish (2 of 4 sessions typically complete at ~1 500–1 900 s).
7. **Cheaper loop judge** (setting `--loop-judge gemini:gemini-3.7-flash`, pro for the final): judge model time is
   12 % of baseline blender wall (116 s/run); flash p50 21 s vs pro 42 s → **~60 s/run base, ~20 s storm**.
8. **Shorter render tools** (code: 30-s render cap): render/isolate p90 = 61–91 s tail, total ≤ 30 s/session →
   **≤ ~100 s/run**; `joint_sweep` (259 s/session, articulated) is the only tool worth batching.
9. Parallel judge samples — already parallel (`fan_out`; baseline `judge_s` 52 s vs Σ samples 100 s): **0 s**.
Not fixable from here: successful flash calls are 2.5x slower during the storm (p50 8.3 s), which alone adds ~180 s
of think time per session; the only lever is fewer turns.
