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
from codeverse.tracks.common import RunContext
from codeverse.tracks.generation import GenerationResult, GenerationTask, generate
from codeverse.tracks.prompting import base_prompt_context

log = logging.getLogger(__name__)

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


def relevant_cookbook_section(cookbook: str, error_text: str, *, max_chars: int = 3000) -> str:
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
                      max_attempts: int | None = None) -> RepairOutcome:
    """Lint+build; on failure run up to ``max_repair_attempts`` error-focused repairs."""
    files_hint = list(files_hint or [])
    max_attempts = ctx.spec.budget.max_repair_attempts if max_attempts is None else max_attempts
    build, lint = build_once(ctx)
    ctx.events.emit("build.done", round=round_index, ok=build.ok, lint_errors=len(lint.errors),
                    error=build.error_message[:200], duration_ms=build.duration_ms)
    outcome = RepairOutcome(build=build, lint=lint, max_attempts=max_attempts)
    prev_sig = ""
    repeats = 0
    attempt = 0
    while not outcome.ok and attempt < max_attempts:
        attempt += 1
        sig = error_signature(build, lint)
        repeats = repeats + 1 if sig == prev_sig else 0
        prev_sig = sig
        task = make_repair_task(ctx, build, lint, round_index=round_index, attempt=attempt, repeats=repeats,
                                label=f"{label}_repair{attempt}", files_hint=files_hint)
        ctx.events.emit("repair.attempt", round=round_index, attempt=attempt, repeats=repeats, signature=sig[:200])
        res = generate(ctx.ws, agent_id=ctx.agent_id, task=task, agent=ctx.agent, model=ctx.model,
                       settings=ctx.settings, budget=ctx.budget, events=ctx.events,
                       max_turns=ctx.policy.agent_max_turns, wrapup_turns=ctx.policy.agent_wrapup_turns)
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
                     repeats: int, label: str, files_hint: list[str]) -> GenerationTask:
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
                          round=round_index, kind="repair", temperature=temperature, thinking=thinking)


def _repair_system(ctx: RunContext) -> str:
    return (f"You repair {ctx.language.value} code that failed to build. Fix only the reported error(s) with the smallest "
            f"correct change; keep every part, name and dimension. {ctx.contract_text[:4000]}")
