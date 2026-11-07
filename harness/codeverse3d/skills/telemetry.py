"""Did the agent READ the routed skills?  Measured from the CLI's own tool trace where there is one.

A bundle is *surfaced* when a call activated it or named its ``SKILL.md``, *deep* when a call
named a file under its ``references/``.  When a session left no trace the probe falls back to
atime (``materialize_skills`` stamps ``atime == mtime``; a later atime means opened).  The
never-routed control bundle falsifies that fallback: if it reads as opened, ``deep_read_rate``
is ``None`` instead of a confident 100%.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse3d.contracts.run import SkillRead, SkillsUsage
from codeverse3d.proc import read_jsonl_lenient
from codeverse3d.skills.model import SkillsMaterialized
from codeverse3d.skills.prompting import AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT

log = logging.getLogger(__name__)

TELEMETRY_DIR = "telemetry"
SKILLS_FILE = "skills.jsonl"
#: seconds of slack: some filesystems round atime/mtime differently on the two writes
ATIME_EPSILON_S = 1.0


def _opened(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_atime > st.st_mtime + ATIME_EPSILON_S


def probe_reads(ws_root: Path, materialized: SkillsMaterialized) -> SkillsUsage:
    """Per skill, what the sessions since the attach did with the bundles (trace first, atime fallback)."""
    from codeverse3d.skills.materialize import CONTROL_NAME

    root = Path(ws_root)
    control_present, control_read, control_refs_read = _control_state(root)
    trace = trace_reads(root, [*materialized.listed, CONTROL_NAME], since=materialized.attached_at)
    if trace is not None:
        control_read = control_present and any(trace[CONTROL_NAME])
    reads: list[SkillRead] = []
    for sel in materialized.selections:
        name = sel.name
        bundles = [root / r / name for r in (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT)]
        refs = [f for b in bundles for f in sorted((b / "references").glob("*.md"))]
        if trace is not None:
            surfaced, deep = trace[name]
            # a shell read names no path the trace can match but bumps the reference's atime;
            # counts as proof only while the control's references stay untouched
            if not deep and not control_refs_read:
                deep = any(_opened(f) for f in refs)
        else:
            surfaced = any(_opened(b / "SKILL.md") for b in bundles)
            deep = any(_opened(f) for f in refs)
        # reading a reference means the bundle was reached, whatever SKILL.md's atime says
        surfaced = surfaced or deep
        reads.append(SkillRead(
            name=name, surfaced=surfaced, deep=deep,
            body_tokens=sel.skill.body_tokens, reason=sel.reason,
        ))
    return SkillsUsage(
        listed=list(materialized.listed), reads=reads, index_tokens=materialized.index_tokens,
        body_tokens_read=sum(r.body_tokens for r in reads if r.deep),
        inlined=materialized.inlined,
        control_present=control_present, control_read=control_read,
        evidence="transcript" if trace is not None else "atime",
    )


def trace_reads(ws_root: Path, names: Sequence[str], *, since: float = 0.0) -> dict[str, tuple[bool, bool]] | None:
    """``{name: (surfaced, deep)}`` from tool calls logged after ``since``.

    ``None`` (use the fallback) when no session ran after the attach or one left no
    ``tool_trace`` marker: a partial trace would under-count.
    """
    from codeverse3d.agents.cli_common import TOOL_CALL_ROW, TOOL_TRACE_ROW
    from codeverse3d.workspace import Workspace

    sessions = traced = 0
    calls: list[dict[str, Any]] = []
    for path in sorted(Workspace(ws_root).trajectories.glob("*/transcript.jsonl")):
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        rows = [r for r in read_jsonl_lenient(path, dicts_only=True) if _t(r) >= since]
        if not any(r.get("kind") == "invoke" for r in rows):
            continue
        sessions += 1
        if any(r.get("kind") == TOOL_TRACE_ROW for r in rows):
            traced += 1
            calls += [r for r in rows if r.get("kind") == TOOL_CALL_ROW]
    if not sessions or traced < sessions:
        return None
    return {n: _called(n, calls) for n in names}


def _t(row: dict[str, Any]) -> float:
    try:
        return float(row.get("t") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _called(name: str, calls: Sequence[dict[str, Any]]) -> tuple[bool, bool]:
    """(surfaced, deep) for one bundle, matched on ``<name>/SKILL.md`` and ``<name>/references/<file>``
    anywhere in a call's arguments; a listing of ``references`` does not count."""
    body = re.compile(rf"(?<![\w-]){re.escape(name)}/SKILL\.md")
    ref = re.compile(rf"(?<![\w-]){re.escape(name)}/references/[^\s\"'`]+")
    surfaced = deep = False
    for c in calls:
        if c.get("failed"):   # the CLI's own verdict: that read returned nothing
            continue
        text = json.dumps(c.get("args") or {}, ensure_ascii=False)
        deep = deep or bool(ref.search(text))
        surfaced = surfaced or c.get("skill") == name or bool(body.search(text))
    return surfaced or deep, deep


def _control_state(root: Path) -> tuple[bool, bool, bool]:
    """(a control was materialised, something opened it, something opened its ``references/``);
    the third is the stricter signal, since a CLI's discovery scan opens every ``SKILL.md``."""
    from codeverse3d.skills.materialize import CONTROL_NAME

    present = opened = refs_opened = False
    for rel in (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT):
        d = root / rel / CONTROL_NAME
        if not d.is_dir():
            continue
        present = True
        opened = opened or _opened(d / "SKILL.md")
        for f in sorted((d / "references").glob("*.md")):
            refs_opened = refs_opened or _opened(f)
    return present, opened or refs_opened, refs_opened


def append_usage(ws_root: Path, usage: SkillsUsage, **context: Any) -> Path | None:
    """One JSON line per (run, round, session)."""
    try:
        d = Path(ws_root) / TELEMETRY_DIR
        d.mkdir(parents=True, exist_ok=True)
        p = d / SKILLS_FILE
        with p.open("a") as fh:
            fh.write(json.dumps({**context, **usage.model_dump(mode="json")}) + "\n")
        return p
    except OSError as e:
        log.debug("could not append skills telemetry: %s", e)
        return None


__all__ = ["ATIME_EPSILON_S", "SKILLS_FILE", "TELEMETRY_DIR", "append_usage", "probe_reads", "trace_reads"]
