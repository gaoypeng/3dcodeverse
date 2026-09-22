"""Did anyone actually READ it?  Measured from the CLI's own record where there is one.

The fact this whole system has to answer: ``read_cookbook`` was called by **0 of 16**
zone sessions and 1 of 4 env sessions on ``scenes_v1_flash``, although the prompt named
five chapters by title.  A tool that exists is not a tool that gets used, and shipping a
nicer file format without measuring reads would repeat that at a new price.

**Ground truth first (2026-09-22).**  Every CLI backend now folds its session's tool
calls into ``trajectories/<label>/transcript.jsonl`` (``agents/cli_common.record_tool_calls``:
claude-code's ``Skill``/``Read`` from stream-json, gemini-cli's ``activate_skill``/
``read_file`` from its chat record, codex's shell commands from ``exec --json``).  A
bundle is *surfaced* when a call activated it or named its ``SKILL.md``, *deep* when a
call named a file under its ``references/``, and the control is read when a call touched
it — ``SkillsUsage.evidence == "transcript"``.  Only when a session after the attach left
no trace does the probe fall back to the atime signals below, which the control proved
blind in 27 of 33 sessions (docs/SKILLS.md §9):

1. **atime probe** — the root filesystem is ext4 with ``relatime``, where the first read
   after a write bumps ``atime``.  ``materialize_skills`` stamps ``atime == mtime`` on
   every file it writes, so ``atime > mtime`` afterwards means the file was opened.  No
   parsing, and it works identically for all five backends.
2. **references/ depth probe** — signal 1 has an obvious false positive: a CLI's startup
   scan opens SKILL.md to read the frontmatter.  The design assumed nothing scans
   ``references/*.md``, so an atime bump there meant the agent read the body and followed
   it.  **Measured 2026-08-25, that assumption is false**, for two independent reasons:
   ``Workspace.changed_files`` runs ``git add -A -N`` + ``git diff`` after every session
   and git reads every untracked file to diff it; and all four CLIs open ``references/``
   while activating a skill, even one whose description does not match the task.  So the
   depth signal is kept — it is still the difference between "listed" and "opened" — but
   it is no longer trusted on its own, which is what signal 3 is for.  (A bundle that
   ships no ``references/`` cannot be probed for depth and says so via
   ``deep_measurable`` instead of quietly scoring 0.)
3. **the control bundle** — ``materialize.write_control`` puts one never-routed,
   never-indexed bundle beside the real ones.  Nothing should ever open it.  When it comes
   back opened, this session's atime evidence proves nothing, ``control_read`` is set and
   ``deep_read_rate`` returns ``None`` rather than a confident 100%.  It costs two small
   files and turns a metric that could silently read 100% forever into one that says when
   it cannot see.
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
SKILLS_FILE = "skills.jsonl"       # the per-session roll-up, written after it
#: seconds of slack: some filesystems round atime/mtime differently on the two writes
ATIME_EPSILON_S = 1.0


def _opened(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_atime > st.st_mtime + ATIME_EPSILON_S


def probe_reads(ws_root: Path, materialized: SkillsMaterialized) -> SkillsUsage:
    """What the sessions since the attach did with the bundles we attached, per skill.

    From their tool traces when every one of them left one; from atime otherwise."""
    from codeverse3d.skills.materialize import CONTROL_NAME

    root = Path(ws_root)
    control_present, control_read = _control_state(root)
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
        else:
            surfaced = any(_opened(b / "SKILL.md") for b in bundles)
            deep = any(_opened(f) for f in refs)
        # reading a reference means the bundle was reached, whatever SKILL.md's atime says
        # (relatime can miss the second access within 24 h, and some CLIs stream the body
        # out of their own index rather than re-opening the file)
        surfaced = surfaced or deep
        reads.append(SkillRead(
            name=name, surfaced=surfaced, deep=deep, deep_measurable=bool(refs),
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
    """``{name: (surfaced, deep)}`` from the tool calls the CLI sessions logged after ``since``.

    ``None`` — "no ground truth, use the fallback" — when no session ran after the attach,
    or when one of them left no ``tool_trace`` marker (a CLI that died before its backend
    could read its record, or a backend that cannot): that session's reads would be
    MISSING, not absent, and a partial trace would under-count exactly like a blind probe
    over-counts.  A session is a transcript with an ``invoke`` row at or after ``since``."""
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
    """(surfaced, deep) for one bundle.  Matched on ``<name>/SKILL.md`` and
    ``<name>/references/<file>`` anywhere in a call's arguments, so a relative path, an
    absolute one, codex's ``r1/<name>/SKILL.md`` root alias expanded or not, and a
    ``cat``/``sed`` shell line all count; a listing of ``references`` does not."""
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


def _control_state(root: Path) -> tuple[bool, bool]:
    """(a control was materialised, something opened it) — the probe's own falsification."""
    from codeverse3d.skills.materialize import CONTROL_NAME

    present = False
    opened = False
    for rel in (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT):
        d = root / rel / CONTROL_NAME
        if not d.is_dir():
            continue
        present = True
        for f in [d / "SKILL.md", *sorted((d / "references").glob("*.md"))]:
            opened = opened or _opened(f)
    return present, opened


def append_usage(ws_root: Path, usage: SkillsUsage, **context: Any) -> Path | None:
    """One JSON line per (run, round, session) — the file ``3dcode skills report`` reads."""
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
