"""Which model should do which job — with the measured trade-off behind it.

Every number in :data:`ROUTES` was measured on this box (see ``docs/COST.md``):
prices from ``models/pricing.py`` (checked 2026-08-23), $/call from the 61
recorded runs, judge noise from ``judges/calibration.py`` (n=3 on the e2e
rounds), generator quality from ``bench/out/compare_v1_live2``.

The one non-obvious result: **a noisy cheap judge is not cheap.**  Averaging
flash to pro's precision needs ``(σ_flash/σ_pro)²`` samples — and even then it
does not buy pro's *correlation* with the deterministic gates, so a biased rank
stays biased.  :func:`samples_for_precision` and :func:`pro_break_even` do that
arithmetic for a caller that wants to decide at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from codeverse.cost.types import Role

#: judge score noise (std of the overall score over n=3 samples) and the
#: correlation of the score with deterministic gate errors, per judge model.
#: Source: ``judges/calibration.py`` on the e2e rounds, 2026-08-23.
JUDGE_NOISE: dict[str, tuple[float, float]] = {
    "gemini-3.7-flash": (0.083, -0.33),
    "gemini-3.1-pro-preview": (0.030, +0.63),
}


@dataclass(frozen=True)
class Route:
    """One recommended (role, model) pairing with its measured price and caveat."""

    role: Role
    model_id: str
    usd_per_call: float  # measured median from the recorded runs
    quality: str
    when: str
    default: bool = False


ROUTES: tuple[Route, ...] = (
    # ---------------------------------------------------------------- planner
    Route(Role.PLANNER, "gemini:gemini-3.7-flash", 0.013,
          "plans are structured output; no measured failure attributable to the planner",
          "always — the plan is 0.8% of a run; a pro planner would double that for no measured gain",
          default=True),
    # -------------------------------------------------------------- generator
    Route(Role.GENERATOR, "gemini-cli:gemini-3.6-flash", 0.52,
          "compare_v1: mean judge 0.835 on easy static objects (best arm measured)",
          "the arm compare_v1 was measured on; kept for comparison against those numbers"),
    Route(Role.GENERATOR, "single-shot:gemini:gemini-3.7-flash", 0.05,
          "graphics track: 5/6 passed, median 0.810, $0.099 per run",
          "glsl_shader / opengl_python — one file, compiler feedback, no tool loop needed"),
    # the runtime default since 2026-08-28 (contracts.common.Backends.generator):
    # the default=True route must name what actually runs, not the arm we measured first
    Route(Role.GENERATOR, "gemini-cli:gemini-3.7-flash", 0.73,
          "compare_v1: 0.827 — statistically the same as the since-deleted api-agent arm at 2x the price",
          "the default; ~2x the 3.6 arm's price for quality measured as the same, so the number to beat",
          default=True),
    Route(Role.GENERATOR, "codex:gpt-5.6-sol", 1.93,
          "one-shot 0.786 for $0.20; inside the harness loop $1.93/run (long cached context at $0.40/M)",
          "strong one-shot baseline; expensive as a loop generator"),
    Route(Role.GENERATOR, "oneshot:claude-code", 1.04,
          "compare_v1: 0.673 mean, 0/2 passed — the weakest score per dollar measured here",
          "not recommended for bulk generation on this battery"),
    # ------------------------------------------------------------------ judge
    Route(Role.JUDGE, "gemini:gemini-3.1-pro-preview", 0.060,
          "σ 0.030, pearson(gate errors, score) +0.63",
          "every decision that persists: best-round selection, pass/fail, dataset tiering",
          default=True),
    Route(Role.JUDGE, "gemini:gemini-3.7-flash", 0.027,
          "σ 0.083 and pearson −0.33 (anti-correlated with the gates)",
          "in-loop refine hints only, where a wrong rank costs one round, never for pass/fail"),
    # ------------------------------------------------------------------ image
    Route(Role.IMAGE, "gemini:gemini-3.1-flash-image", 0.067,
          "per 1024² tile; a texture pass is ~2 tiles + plan + gate ≈ $0.13",
          "texture passes only, and only when the before/after gate can ship the result",
          default=True),
    Route(Role.CAPTIONER, "gemini:gemini-3.7-flash", 0.010,
          "captions are post-hoc and cheap; no measured quality difference worth pro",
          "always", default=True),
)


def samples_for_precision(model: str, target_std: float, *, noise: dict[str, tuple[float, float]] | None = None) -> int:
    """How many samples of ``model`` are needed to reach ``target_std``
    (σ_eff = σ / √n).  Returns 1 when the model is already precise enough."""
    table = noise or JUDGE_NOISE
    sigma = table.get(model, (0.0, 0.0))[0]
    if sigma <= 0 or target_std <= 0 or sigma <= target_std:
        return 1
    return int(ceil((sigma / target_std) ** 2))


def pro_break_even(
    *,
    cheap_model: str = "gemini-3.7-flash",
    pro_model: str = "gemini-3.1-pro-preview",
    cheap_usd: float = 0.027,
    pro_usd: float = 0.060,
) -> dict[str, float]:
    """The arithmetic behind "use pro": what matching pro's precision with the
    cheap judge would cost, per verdict."""
    pro_sigma = JUDGE_NOISE.get(pro_model, (0.03, 0.0))[0]
    n = samples_for_precision(cheap_model, pro_sigma)
    return {
        "samples_needed": float(n),
        "cheap_matched_usd": n * cheap_usd,
        "pro_usd": pro_usd,
        "pro_is_cheaper_by": n * cheap_usd - pro_usd,
        "ratio": (n * cheap_usd / pro_usd) if pro_usd else 0.0,
    }


def routing_table() -> list[tuple[str, str, str, str, str]]:
    """Rows for the report: (role, model, $/call, quality, when)."""
    return [(str(r.role), r.model_id + (" *" if r.default else ""), f"${r.usd_per_call:.3f}", r.quality, r.when)
            for r in ROUTES]
