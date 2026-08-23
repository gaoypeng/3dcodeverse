# harness vs one-shot — compare_v1

fixed judge: **gemini:gemini-3.1-pro-preview** (rubric static_object_v1, n_samples=2, acceptance = must_have list) · harness loop judge: settings default · harness rounds ≤ 3, ≤ $2.5 · cells: 6

Every arm's final `src/model.py` is re-built, re-rendered and judged by the same evaluator; a failed build scores 0.  `$gen` for harness arms is the whole run (planner + generator + its loop judge); for one-shot arms it is the single call (subscription CLIs report 0 unless the CLI returns a cost).

## arms

| arm | kind | n | mean | median | pass | build ok | $gen | $judge | min | tool calls | errors |
|---|---|---|---|---|---|---|---|---|---|---|---|
| oneshot:codex | oneshot | 2 | 0.786 | 0.786 | 50% | 100% | 0.20 | 0.091 | 2.1 | 0 | 0 |
| oneshot:claude-code | oneshot | 2 | 0.673 | 0.673 | 0% | 100% | 0.95 | 0.088 | 4.0 | 0 | 0 |
| oneshot:gemini:gemini-3.7-flash | oneshot | 2 | 0.000 | 0.000 | 0% | 0% | 0.01 | 0.000 | 0.9 | 0 | 0 |

## per prompt (score, ✓ = passed, ✗build = build failed)

| prompt | tier | oneshot:claude-code | oneshot:codex | oneshot:gemini:gemini-3.7-flash |
|---|---|---|---|---|
| cmp_easy_stool | easy | 0.65 | 0.90✓ | 0.00 ✗build |
| cmp_easy_desk_lamp | easy | 0.70 | 0.67 | 0.00 ✗build |

## pairwise arena (PairwiseJudge, both orders; ties = orderings disagree or equal)

| harness arm (A) | one-shot arm (B) | n | A wins | B wins | ties | A win rate | conf |
|---|---|---|---|---|---|---|---|

**Total cost**: generation $2.33 · fixed judge $0.36 · pairwise $0.00 (subscription CLIs report their own cost figure or 0).

## errors

- `cmp_easy_stool` / `oneshot:gemini:gemini-3.7-flash` (no_code): ModelError: Gemini API error 503: This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.
- `cmp_easy_desk_lamp` / `oneshot:gemini:gemini-3.7-flash` (build_failed): AttributeError: BMeshOpsModule: operator "create_cylinder" doesn't exist
