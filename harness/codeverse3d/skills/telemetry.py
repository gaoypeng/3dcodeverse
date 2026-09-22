"""Did anyone actually READ it?  The measurement no CLI can do for us.

The fact this whole system has to answer: ``read_cookbook`` was called by **0 of 16**
zone sessions and 1 of 4 env sessions on ``scenes_v1_flash``, although the prompt named
five chapters by title.  A tool that exists is not a tool that gets used, and shipping a
nicer file format without measuring reads would repeat that at a new price.

Three signals, cheapest first:

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
from pathlib import Path
from typing import Any

from codeverse3d.contracts.run import SkillRead, SkillsUsage
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
    """What the session did with the bundles we attached, per skill."""
    root = Path(ws_root)
    control_present, control_read = _control_state(root)
    reads: list[SkillRead] = []
    for sel in materialized.selections:
        name = sel.name
        bundles = [root / r / name for r in (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT)]
        refs = [f for b in bundles for f in sorted((b / "references").glob("*.md"))]
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
    )


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


__all__ = ["ATIME_EPSILON_S", "SKILLS_FILE", "TELEMETRY_DIR", "append_usage", "probe_reads"]
