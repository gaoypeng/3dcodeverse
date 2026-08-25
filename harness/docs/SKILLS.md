# Skills — the authoring contract

A **skill** is a task-scoped rule sheet the harness attaches to a coding session
automatically, from the track, the language, the round kind, the plan, and **the previous
round's gate findings**. It is not a renamed cookbook chapter: the cookbook
(`codeverse/prompts/<lang>/cookbook.md`) stays the reference manual with the copyable
code; a skill states rules and numbers and names the cookbook section to fetch.

`SKILL.md` is an open standard (agentskills.io), which is why the format below is not
ours to bend: claude-code, codex, gemini-cli and agy discover these bundles themselves.
Only `api-agent` needs harness-side injection.

Everything is behind `CV3D_SKILLS` (default **off**). See `codeverse/skills/config.py`.

## 1. Layout

```
codeverse/skills/<name>/SKILL.md          the bundle (package data — it ships in the wheel)
codeverse/skills/<name>/references/*.md   depth: worked examples, long tables. ONE level.
codeverse/skills/_claims/<name>.toml      numbers pinned to live constants (outside the bundle)
```

`references/` is not optional decoration. Nothing scans it, so an `atime` bump there is
the only honest evidence that an agent read the body rather than a discovery scan reading
the frontmatter. A bundle without it reports `deep_measurable: false` and can never score
on the read metric.

## 2. Frontmatter

```yaml
---
name: cv3d-part-contact          # == the directory name, lowercase a-z0-9 and '-', <= 64
description: >-                  # 1-1024 chars, and it must say WHAT and WHEN
  ... the only text a CLI indexes ...
license: Apache-2.0
metadata:                        # string -> string only
  evidence: measured             # measured | mixed | inherited-unverified
  verified: 2026-08-25           # ISO date the claims were last checked against this repo
  corpus: static_v2_flash n=47   # optional, when the body quotes corpus statistics
---
```

Hard rules the loader enforces (`codeverse/skills/loader.py`):

* `name` equals the parent directory name — every discovery implementation keys off it.
* **No `<` or `>` anywhere in the frontmatter.** The spec's prompt-injection rule.
* Only spec keys at the top level; our own fields live under `metadata`.
* Body <= **350 lines** and <= **2,500 estimated tokens** (half the spec's recommendation,
  because docs/COST.md §4 shows we pay for every prompt byte on every turn, not once).
* A bundle labelled `inherited-unverified` is **not routed** unless
  `CV3D_SKILLS_UNVERIFIED=on`. Today that is cadquery and threejs: zero graded runs each.

## 3. Body rules (enforced by `tests/skills/test_library.py`)

* No code fence longer than 20 lines — code belongs in the cookbook, one library of truth.
* Never restate a frame, unit or naming rule; `codeverse/conventions.py` is the only place
  those exist, and two truths are worse than one.
* Every number that came from a live constant gets a `_claims` row, so moving the constant
  breaks the test that ships the skill quoting it.
* Every corpus percentage carries its battery, its n and its date.
* Two skills that can be attached to the same session may not disagree about a claim key.

A claim row:

```toml
[[claim]]
key    = "penetration_error_m"
text   = "10 mm"                                              # must appear in the body
python = "codeverse.spatial.connectivity:PENETRATION_ERROR_M"
scale  = 1000
format = "{:.0f} mm"
```

## 4. Routing

Nobody writes `skills: [...]`. `codeverse/skills/registry.py` holds the typed route table
(R1-R24) and `finding_kind()`, the one place a gate message is pattern-matched.
`codeverse/skills/router.py` turns `(track, language, kind, plan signals, findings)` into a
ranked, capped set with a reason per selection. Gate-fired rows (priority >= 90) always
outrank standing rows, so a repair round spends its budget on what actually broke, and at
most `CV3D_SKILLS_MAX` (default 5) bundles are attached.

Adding a bundle means adding its rows to that table in the same commit — an unroutable
bundle pays the index and never helps, and a test fails on both halves of that.

## 5. Commands

```
3dcv skills list --routes
3dcv skills show cv3d-part-contact
3dcv skills validate
3dcv skills report bench/out/static_v2_flash
3dcv doctor --skills
```

`report` is the one that matters: deep-read rate per skill per backend. Targets are
>= 60% for CLI backends and >= 80% for api-agent, and a skill under 20% over 20 sessions
is merged or deleted. A library that only ever grows is how this ends as bloat.
