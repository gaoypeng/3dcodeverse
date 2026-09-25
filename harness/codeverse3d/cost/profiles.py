"""Cost/quality profiles — one name that sets the whole dial coherently.

``3dcode make --profile economy|balanced|quality`` picks a :class:`Profile`; it
resolves the model per role, the judge sample count, the number of refine
rounds, the best-of-N width, the texture pass and the budget ceilings in one
consistent move.  Mixing knobs by hand is how a run ends up
paying for a pro judge on a single-shot artifact, or running four refine rounds
on a graphics shader that passed at round 0.

``--profile X`` and ``C3D_PROFILE=X`` (or ``profile:`` in ``config.yaml``)
resolve to the **same** dial: ``codeverse3d.cli._common.resolve_dial`` is the one
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
  neither paid (see the note on :class:`Profile`).
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

from codeverse3d.contracts.common import Backends

#: profile names in increasing order of spend
PROFILE_NAMES = ("economy", "balanced", "quality")

DEFAULT_PROFILE = "balanced"


@dataclass(frozen=True)
class Profile:
    """One coherent setting of the cost/quality dial.

    NOT dials: the judge payload (``Settings.judge`` max_px / montages / detail_crops) and
    the agent turn cap (``Settings.limits.agent_max_turns``) are the same under every
    profile, because each cut was measured and none paid.  768 px bills a montage the same
    as 1024 (156,834 vs 156,874 input tokens over 16 verdicts) and raised the judge's σ
    0.033 → 0.042; one detail crop instead of two saves 1,198 tokens ($0.0024/verdict on
    pro) but two draws of it disagreed by 0.048 = 1.6x the pro judge's σ (INCONCLUSIVE,
    docs/COST.md §14); 3 montages drop the 14-view rig's low ring + poles (D47); a 28-turn
    cap cost $0.02 MORE and 0.205 of a score point (§17).
    """

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
    texture: bool = False            # run the derived texture pass
    #: scene_blender judged frames: Cycles samples (adaptive + OIDN), owner D1 2026-09-24
    blender_samples: int = 32
    #: budget ceilings a run of this shape should not need to exceed
    max_minutes: float = 60.0
    #: measured expectation (docs/COST.md) — reported by ``3dcode cost profiles``
    expected_usd: float = 0.0
    expected_score: str = ""
    note: str = ""

    def expectation(self) -> str:
        return f"~${self.expected_usd:.2f}/run, {self.expected_score}"


#: the backend defaults (``contracts.common.Backends``, the one place they are written): a
#: row names a model literally only where it departs from them.
_D = Backends()

#: the three dials.  Generator ids are harness backend ids; judge/planner ids are
#: ``<provider>:<model>``.
PROFILES: dict[str, Profile] = {
    "economy": Profile(
        name="economy",
        generator="single-shot:gemini:gemini-3.7-flash",
        planner=_D.planner,
        judge="gemini:gemini-3.7-flash",
        captioner=_D.captioner,
        rounds=2, candidates=1, judge_samples=2, texture=False, blender_samples=16,
        max_minutes=30.0,
        expected_usd=0.30,
        expected_score="graphics 0.81 median (5/6 passed); objects clear the gates less often "
                       "— a one-shot flash generator scored 0.14 on compare_v1's hard cells",
        note="single-shot generator + flash judge n=2 + 2 refine rounds; no best-of-N, no texture, "
             "full judge payload",
    ),
    "balanced": Profile(
        name="balanced",
        generator=_D.generator,
        planner=_D.planner,
        judge=_D.judge,
        captioner=_D.captioner,
        rounds=4, candidates=1, judge_samples=1, texture=False,
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
        generator=_D.generator,
        planner=_D.planner,
        judge=_D.judge,
        captioner=_D.captioner,
        rounds=4, candidates=2, judge_samples=3, texture=True, blender_samples=64,
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


def profile_table() -> list[tuple[str, str, str, str, str, str]]:
    """Rows for ``3dcode cost profiles``: name, generator, judge, shape (+texture), $, note."""
    rows = []
    for name in PROFILE_NAMES:
        p = PROFILES[name]
        shape = f"{p.rounds}r × {p.candidates}cand, judge n={p.judge_samples}" + (" +texture" if p.texture else "")
        rows.append((p.name, p.generator, p.judge, shape, f"${p.expected_usd:.2f}", p.expected_score))
    return rows


__all__ = ["DEFAULT_PROFILE", "PROFILES", "PROFILE_NAMES", "Profile", "get_profile", "profile_table"]
