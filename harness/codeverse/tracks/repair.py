"""Build → error-focused repair loop (no judge, no renders: cheap first).

``build_with_repair`` runs ``runtime.lint`` + ``runtime.build``; while the
build fails (or lint reports ERRORs) and attempts remain, it dispatches ONE
repair task carrying a structured error report (file:line, message, traceback
tail, lint findings with fix hints, the cookbook section matching the error
keywords).  Identical consecutive error signatures escalate (higher
temperature/thinking for single-shot; an explicit "same error again" notice
for agents) — the tell that the previous edit did not touch the cause.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from codeverse.contracts.artifacts import BuildResult, GateReport
from codeverse.contracts.common import Usage
from codeverse.prompts import render
from codeverse.tracks import skills_hook
from codeverse.tracks.common import RunContext, generate_for
from codeverse.tracks.generation import GenerationResult, GenerationTask
from codeverse.tracks.prompting import base_prompt_context, language_system_prompt

log = logging.getLogger(__name__)

#: How many times a build that failed IN THE HARNESS is simply re-run before the round
#: gives up.  Cheap (no model call) and bounded: the node driver already retries once
#: itself, so a failure that survives this many rebuilds is not transient.
MAX_HARNESS_REBUILDS = 2

MAX_REPAIR_CONTEXT_CHARS = 40_000
_TRACEBACK_TAIL_LINES = 40


class RepairOutcome(BaseModel):
    build: BuildResult
    lint: GateReport
    attempts: list[GenerationResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    repaired: bool = False
    max_attempts: int = Field(default=0, description="repair budget this outcome ran under")

    @property
    def ok(self) -> bool:
        return self.build.ok and not self.lint.errors


def error_signature(build: BuildResult, lint: GateReport) -> str:
    """Stable key for "is this the same failure as last time?"."""
    if not build.ok:
        first = (build.error_message or build.stderr_tail or "").strip().splitlines()
        head = first[0][:160] if first else ""
        return f"{build.error_type}|{build.error_file}|{build.error_line}|{head}"
    return "lint|" + "|".join(sorted(f"{f.target}:{f.message[:80]}" for f in lint.errors))


def format_error_report(build: BuildResult, lint: GateReport, cookbook: str = "", skills: str = "") -> str:
    """Compact, structured error report for the fixer (the ONLY thing it must fix).

    ``skills`` is the gate→skill pointer block: the bundles already in the workspace that
    answer THESE findings, named beside them.  Pointers, not text — the bodies are on
    disk, and the repair prompt is the volatile tail nobody caches."""
    lines: list[str] = []
    if not build.ok:
        loc = build.error_file + (f":{build.error_line}" if build.error_line else "") if build.error_file else "(unknown file)"
        lines.append(f"BUILD FAILED — {build.error_type or 'Error'} at {loc}")
        if build.error_message:
            lines.append(f"message: {build.error_message.strip()[:1500]}")
        tail = (build.stderr_tail or build.stdout_tail or "").strip().splitlines()[-_TRACEBACK_TAIL_LINES:]
        if tail:
            lines.append("traceback / log tail:")
            lines.append("```")
            lines.extend(line[:300] for line in tail)
            lines.append("```")
    if lint.errors:
        lines.append(f"LINT ERRORS ({lint.gate}):")
        lines.extend(f"- {f.as_line(with_target=True)}" for f in lint.errors)
    if skills.strip():
        lines.append("\n" + skills.strip())
    section = relevant_cookbook_section(cookbook, " ".join([build.error_type, build.error_message, build.stderr_tail[-2000:]]))
    if section:
        lines.append("\nRelevant cookbook section:\n" + section)
    return "\n".join(lines)


_HEADING = re.compile(r"^##+ .+$", re.M)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def relevant_cookbook_section(cookbook: str, error_text: str, *, max_chars: int = 12000) -> str:
    """Pick the cookbook ``##`` section sharing the most identifiers with the error."""
    if not cookbook or not error_text:
        return ""
    heads = list(_HEADING.finditer(cookbook))
    if not heads:
        return ""
    err_words = {w.lower() for w in _WORD.findall(error_text)}
    err_words -= {"error", "line", "file", "traceback", "most", "recent", "call", "last", "module"}
    best, best_score = "", 0
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(cookbook)
        body = cookbook[h.start():end]
        words = {w.lower() for w in _WORD.findall(body)}
        score = len(words & err_words)
        if score > best_score:
            best, best_score = body, score
    if best_score < 2:
        return ""
    return best[:max_chars].rstrip()


def files_for_repair(ctx: RunContext, build: BuildResult, lint: GateReport, files_hint: list[str]) -> dict[str, str]:
    """Current contents of the failing file(s) (≤ 40k chars total) for single-shot repair."""
    cands: list[str] = []
    if build.error_file:
        cands.append(_rel(ctx, build.error_file))
    for f in lint.errors:
        if f.target and "." in f.target:
            cands.append(_rel(ctx, f.target))
    cands.extend(files_hint)
    if not cands:
        for glob in getattr(ctx.runtime, "entry_globs", ()) or ():
            cands.extend(str(p.relative_to(ctx.ws.root)) for p in sorted(ctx.ws.root.glob(glob)))
    out: dict[str, str] = {}
    total = 0
    for rel in dict.fromkeys(cands):
        p = ctx.ws.root / rel
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        if total + len(text) > MAX_REPAIR_CONTEXT_CHARS:
            text = text[: max(0, MAX_REPAIR_CONTEXT_CHARS - total)] + "\n// ... truncated ..."
        out[rel] = text
        total += len(text)
        if total >= MAX_REPAIR_CONTEXT_CHARS:
            break
    return out


def _rel(ctx: RunContext, path: str) -> str:
    p = path.replace("\\", "/")
    root = str(ctx.ws.root).replace("\\", "/").rstrip("/") + "/"
    if p.startswith(root):
        return p[len(root):]
    return p.lstrip("./")


def build_once(ctx: RunContext) -> tuple[BuildResult, GateReport]:
    lint = ctx.runtime.lint(ctx.ws)
    build = ctx.runtime.build(ctx.ws, timeout_s=ctx.settings.limits.build_timeout_s)
    return build, lint


def build_with_repair(ctx: RunContext, *, round_index: int, label: str, files_hint: list[str] | None = None,
                      max_attempts: int | None = None, timeout_s: int | None = None) -> RepairOutcome:
    """Lint+build; on failure run up to ``max_repair_attempts`` error-focused repairs.

    ``timeout_s`` clips every repair session (a scene asset's window is a share of the
    run, not the 1 800 s agent default: measured 2026-09-07, a 25-minute scene could
    spend 70 minutes on one hero's three repairs)."""
    files_hint = list(files_hint or [])
    max_attempts = ctx.spec.budget.max_repair_attempts if max_attempts is None else max_attempts
    build, lint = build_once(ctx)
    ctx.events.emit("build.done", round=round_index, ok=build.ok, lint_errors=len(lint.errors),
                    error=build.error_message[:200], duration_ms=build.duration_ms)
    outcome = RepairOutcome(build=build, lint=lint, max_attempts=max_attempts)
    prev_sig = ""
    repeats = 0
    attempt = 0
    rebuilds = 0
    # the rebuilds are gated by their OWN bound, not by the repair budget: a round run
    # with max_repair_attempts=0 (a baseline arm) still gets its harness retries
    while not outcome.ok and (attempt < max_attempts or build.harness_failure):
        # A build that failed because the HARNESS could not run it is not a defect the
        # agent can fix, and handing it over costs the round its whole repair budget on
        # working code.  Measured 2026-09-05 (scene_textures/japanese_garden): three
        # repairs against "scene probe produced no result (driver output lost)" rewrote
        # 5, then 14, then 3 files — the 14 included env.js and every zone — and deleted
        # the texture use the arm existed to measure, before the fourth build passed on
        # its own.  Re-run the build instead; the driver's own retry has already fired,
        # so this is the second line, bounded and cheap (no model call).
        if build.harness_failure:
            if rebuilds >= MAX_HARNESS_REBUILDS:
                log.warning("build kept failing in the harness (%s); giving up without a repair",
                            build.error_message[:160])
                break
            rebuilds += 1
            ctx.events.emit("build.harness_retry", round=round_index, attempt=rebuilds,
                            error=build.error_message[:200])
            build, lint = build_once(ctx)
            outcome.build, outcome.lint = build, lint
            ctx.events.emit("build.done", round=round_index, ok=build.ok, lint_errors=len(lint.errors),
                            harness_retry=rebuilds, error=build.error_message[:200],
                            duration_ms=build.duration_ms)
            continue
        attempt += 1
        sig = error_signature(build, lint)
        repeats = repeats + 1 if sig == prev_sig else 0
        prev_sig = sig
        task = make_repair_task(ctx, build, lint, round_index=round_index, attempt=attempt, repeats=repeats,
                                label=f"{label}_repair{attempt}", files_hint=files_hint, timeout_s=timeout_s)
        ctx.events.emit("repair.attempt", round=round_index, attempt=attempt, repeats=repeats, signature=sig[:200])
        res = generate_for(ctx, task)
        outcome.attempts.append(res)
        outcome.usage = outcome.usage + res.usage
        if not res.ok:
            log.warning("repair attempt %d produced no files (%s)", attempt, res.notes)
        build, lint = build_once(ctx)
        outcome.build, outcome.lint = build, lint
        ctx.events.emit("build.done", round=round_index, ok=build.ok, lint_errors=len(lint.errors), attempt=attempt,
                        error=build.error_message[:200], duration_ms=build.duration_ms)
    outcome.repaired = outcome.ok and attempt > 0
    return outcome


def make_repair_task(ctx: RunContext, build: BuildResult, lint: GateReport, *, round_index: int, attempt: int,
                     repeats: int, label: str, files_hint: list[str], timeout_s: int | None = None) -> GenerationTask:
    """Render prompts/tracks/repair.j2 for the current strategy."""
    report = format_error_report(build, lint, ctx.cookbook_text, skills_hook.repair_pointers(ctx, lint))
    files = files_for_repair(ctx, build, lint, files_hint) if ctx.single_shot else {}
    prompt = render("tracks/repair.j2", **base_prompt_context(
        ctx, error_report=report, files=files, repeats=repeats, attempt=attempt,
        failing_files=sorted(files) if files else [build.error_file] if build.error_file else files_hint))
    ctx.record_prompt("repair", prompt)
    temperature = min(1.0, 0.3 + 0.25 * repeats)
    thinking = ("medium", "high", "high")[min(repeats, 2)]
    return GenerationTask(label=label, prompt=prompt, system=_repair_system(ctx), files_hint=sorted(files) or files_hint,
                          round=round_index, kind="repair", temperature=temperature, thinking=thinking, timeout_s=timeout_s)


def _repair_system(ctx: RunContext) -> str:
    return language_system_prompt(ctx.language, role="repair", contract=ctx.contract_text)
