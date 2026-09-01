"""Cost/quality profiles — one name that sets the whole dial coherently.

``3dcv make --profile economy|balanced|quality`` picks a :class:`Profile`; it
resolves the model per role, the judge sample count, the number of refine
rounds, the best-of-N width, the judge payload, the texture pass and the budget
ceilings in one consistent move.  Mixing knobs by hand is how a run ends up
paying for a pro judge on a single-shot artifact, or running four refine rounds
on a graphics shader that passed at round 0.

``--profile X`` and ``CV3D_PROFILE=X`` (or ``profile:`` in ``config.yaml``)
resolve to the **same** dial: ``codeverse.cli._common.resolve_dial`` is the one
place that reads it, and ``tests/cost/test_profiles.py`` asserts every
field of the resolved dial from both entry points.  The only difference is
precedence — the flag forces the dial over a value you stated yourself, the
env var yields to it.

Every number is taken from the measurements in ``docs/COST.md``:

* **economy** — single-shot generation (graphics: 5/6 passed at $0.10/run; a
  one-file generator plus compiler feedback needs no tool loop), the flash judge
  at ``n=2`` (σ 0.083/√2 ≈ 0.059 — enough to rank two rounds, not enough for a
  pass/fail that persists) and **2 refine rounds** (r01 buys a score point for
  $4.93, r02 for $6.80, r03 for $33.49).  It does **not** shrink the judge
  payload and does **not** carry its own turn cap — both were measured and
  neither paid (see the field comments below).
* **balanced** — exactly today's defaults (gemini-cli generator, pro judge at
  n=1, 4 rounds, no best-of-N, no texture): the arm every number in the audit
  was measured on.
* **quality** — gemini-cli + the pro judge at ``n=3`` (σ 0.030/√3 ≈ 0.017),
  best-of-2 baselines and the texture pass behind its before/after gate.

The expected $ per profile comes from the same 61 runs; see
:func:`Profile.expectation` and ``docs/COST.md`` §8–9.
"""

from __future__ import annotations

from dataclasses import dataclass

#: profile names in increasing order of spend
PROFILE_NAMES = ("economy", "balanced", "quality")

DEFAULT_PROFILE = "balanced"


@dataclass(frozen=True)
class Profile:
    """One coherent setting of the cost/quality dial."""

    name: str
    #: model per role ("" = leave the settings default alone)
    generator: str = ""
    planner: str = ""
    judge: str = ""
    captioner: str = ""
    #: run shape
    rounds: int = 4                  # refine rounds after the baseline
    candidates: int = 1              # best-of-N baselines
    judge_samples: int = 1           # VLM judge samples per verdict
    max_turns: int = 0               # agent turn cap (0 = the backend's own default)
    texture: bool = False            # run the derived texture pass
    #: judge payload
    judge_max_px: int = 1024         # measured: 768 bills the same and is noisier — see PROFILES
    judge_montages: int = 5  # 14-view rig needs 5 (D47); 3 silently drops the low ring + poles
    judge_detail_crops: int = 2      # one crop ≈ 1,198 input tokens ($0.0024 on the pro judge);
                                     # NOT reduced by any profile — the score effect is inside the
                                     # judge's own noise in both directions (docs/COST.md §14)
    #: budget ceilings a run of this shape should not need to exceed
    max_minutes: float = 60.0
    #: measured expectation (docs/COST.md) — reported by ``3dcv cost profiles``
    expected_usd: float = 0.0
    expected_score: str = ""
    note: str = ""

    def expectation(self) -> str:
        return f"~${self.expected_usd:.2f}/run, {self.expected_score}"


#: the three dials.  Generator ids are harness backend ids; judge/planner ids are
#: ``<provider>:<model>``.
PROFILES: dict[str, Profile] = {
    "economy": Profile(
        name="economy",
        generator="single-shot:gemini:gemini-3.7-flash",
        planner="gemini:gemini-3.7-flash",
        judge="gemini:gemini-3.7-flash",
        captioner="gemini:gemini-3.7-flash",
        rounds=2, candidates=1, judge_samples=2, texture=False,
        # max_turns=0 (2026-08-23): economy's generator is ``single-shot:``, which opens no
        # agent session, so the 20-turn cap this profile used to carry could never fire.  And
        # the cap it was modelled on did not survive its own A/B — 3 runs per arm, a 28-turn
        # cap cost $0.02 MORE and 0.205 of a score point (docs/COST.md §17) — so there is no
        # default cap anywhere now.  A caller who wants one still has
        # ``Settings.limits.agent_max_turns`` / ``$CV3D_AGENT_MAX_TURNS``.
        max_turns=0,
        # 1024 px, not 768: measured on 8 recorded rounds, Gemini bills a montage the same at
        # both sizes (156,834 vs 156,874 input tokens over 16 verdicts) while 768 raised the
        # sampling σ 0.033 → 0.042.
        # detail_crops=2, i.e. the same payload as every other profile.  Dropping to 1 crop saves
        # 1,198 input tokens ($0.0024/verdict on pro) and was adopted on a single draw that read
        # 0.595 → 0.600; an independent re-draw of the same round read 0.600 → 0.552, a 0.048
        # swing = 1.6x the pro judge's measured σ (0.030).  Two draws that disagree by more than
        # the instrument's noise do not license a payload cut worth a fifth of a cent, so the
        # experiment is recorded as INCONCLUSIVE in docs/COST.md §14 and the budget is unchanged.
        judge_max_px=1024, judge_montages=5, judge_detail_crops=2,
        max_minutes=30.0,
        expected_usd=0.30,
        expected_score="graphics 0.81 median (5/6 passed); objects clear the gates less often "
                       "— a one-shot flash generator scored 0.14 on compare_v1's hard cells",
        note="single-shot generator + flash judge n=2 + 2 refine rounds; no best-of-N, no texture, "
             "full judge payload",
    ),
    "balanced": Profile(
        name="balanced",
        generator="gemini-cli:gemini-3.7-flash",
        planner="gemini:gemini-3.7-flash",
        judge="gemini:gemini-3.1-pro-preview",
        captioner="gemini:gemini-3.7-flash",
        rounds=4, candidates=1, judge_samples=1, max_turns=0, texture=False,
        judge_max_px=1024, judge_montages=5, judge_detail_crops=2,
        max_minutes=60.0,
        expected_usd=1.47,
        expected_score="0.835 mean on compare_v1, 36/61 runs passed ($2.50 per passing artifact) "
                       "— MEASURED ON gemini-cli:gemini-3.6-flash, not on this dial's generator",
        note="today's defaults.  The generator moved to 3.7-flash on 2026-08-28 and these two "
             "expectations have NOT been re-measured on it: docs/COST.md §12 puts the 3.7 CLI arm "
             "at $0.73/call against 3.6's $0.52 and 0.827 against 0.835, so expect this run to "
             "cost more and score about the same until a battery says otherwise",
    ),
    "quality": Profile(
        name="quality",
        generator="gemini-cli:gemini-3.7-flash",
        planner="gemini:gemini-3.7-flash",
        judge="gemini:gemini-3.1-pro-preview",
        captioner="gemini:gemini-3.7-flash",
        rounds=4, candidates=2, judge_samples=3, max_turns=0, texture=True,
        judge_max_px=1024, judge_montages=5, judge_detail_crops=2,
        max_minutes=90.0,
        expected_usd=3.20,
        expected_score="best-of-2 lifted the stool baseline 0.563 → 0.612 and the texture pass "
                       "0.686 → 0.701; judge σ 0.017 at n=3 "
                       "— MEASURED ON gemini-cli:gemini-3.6-flash, not on this dial's generator",
        note="gemini-cli + pro judge n=3 + 4 rounds + best-of-2 + texture pass.  The generator "
             "moved to 3.7-flash on 2026-08-28 and the $3.20 / score expectations above have NOT "
             "been re-measured on it (same caveat as balanced: docs/COST.md §12 puts the 3.7 CLI "
             "arm at $0.73/call vs 3.6's $0.52), so expect this dial to cost more and score about "
             "the same until a battery says otherwise",
    ),
}


def get_profile(name: str | None) -> Profile:
    """The named profile (case-insensitive).  Unknown names raise ``ValueError``."""
    key = (name or DEFAULT_PROFILE).strip().lower()
    if key not in PROFILES:
        raise ValueError(f"unknown profile {name!r}; known: {', '.join(PROFILE_NAMES)}")
    return PROFILES[key]


def profile_table() -> list[tuple[str, str, str, str, str, str, str]]:
    """Rows for ``3dcv cost profiles``: name, generator, judge, shape, montage, $, note."""
    rows = []
    for name in PROFILE_NAMES:
        p = PROFILES[name]
        shape = f"{p.rounds}r × {p.candidates}cand, judge n={p.judge_samples}" + (
            f", ≤{p.max_turns} turns" if p.max_turns else "")
        rows.append((p.name, p.generator, p.judge, shape,
                     f"{p.judge_max_px}px/{p.judge_detail_crops}crop" + (" +texture" if p.texture else ""),
                     f"${p.expected_usd:.2f}", p.expected_score))
    return rows


__all__ = ["DEFAULT_PROFILE", "PROFILES", "PROFILE_NAMES", "Profile", "get_profile", "profile_table"]
