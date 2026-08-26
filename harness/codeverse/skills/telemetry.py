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
   it is no longer trusted on its own, which is what signal 4 is for.  (A bundle that
   ships no ``references/`` cannot be probed for depth and says so via
   ``deep_measurable`` instead of quietly scoring 0.)
3. **exact reads for api-agent** — our own tool loop owns ``read_file``, so it logs every
   read of a skill path with its turn into ``telemetry/skill_reads.jsonl``.  api-agent is
   therefore the CALIBRATION arm: compare signals 1/2 against ground truth and report the
   probe's false-positive / false-negative rate rather than assuming it is exact.
4. **the control bundle** — ``materialize.write_control`` puts one never-routed,
   never-indexed bundle beside the real ones.  Nothing should ever open it.  When it comes
   back opened, this session's atime evidence proves nothing, ``control_read`` is set and
   ``deep_read_rate`` returns ``None`` rather than a confident 100%.  It costs two small
   files and turns a metric that could silently read 100% forever into one that says when
   it cannot see.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codeverse.skills.model import SkillRead, SkillsMaterialized, SkillsUsage
from codeverse.skills.prompting import AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT

log = logging.getLogger(__name__)

TELEMETRY_DIR = "telemetry"
READS_FILE = "skill_reads.jsonl"   # signal 3, written during the session
SKILLS_FILE = "skills.jsonl"       # the per-session roll-up, written after it
#: seconds of slack: some filesystems round atime/mtime differently on the two writes
ATIME_EPSILON_S = 1.0


def is_skill_path(rel: str) -> str:
    """The skill name a workspace-relative path belongs to, or '' — the one place the
    two discovery roots are recognised, so signal 3 cannot drift from what we wrote."""
    p = str(rel).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    for root in (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT):
        if p.startswith(root + "/"):
            rest = p[len(root) + 1:].split("/")
            return rest[0] if rest and rest[0] else ""
    return ""


def record_exact_read(ws_root: Path, rel_path: str, *, turn: int, label: str = "") -> None:
    """Signal 3: log one api-agent read of a skill file.  Never raises into the loop."""
    name = is_skill_path(rel_path)
    if not name:
        return
    try:
        d = Path(ws_root) / TELEMETRY_DIR
        d.mkdir(parents=True, exist_ok=True)
        with (d / READS_FILE).open("a") as fh:
            fh.write(json.dumps({"skill": name, "path": str(rel_path), "turn": int(turn), "label": label}) + "\n")
    except OSError as e:  # telemetry must never cost a run
        log.debug("could not record a skill read: %s", e)


def _exact_reads(ws_root: Path) -> dict[str, dict[str, Any]]:
    """name → {first_turn, deep} from ``skill_reads.jsonl`` (empty when absent)."""
    p = Path(ws_root) / TELEMETRY_DIR / READS_FILE
    if not p.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in p.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(row.get("skill") or "")
        if not name:
            continue
        rec = out.setdefault(name, {"first_turn": None, "deep": False})
        turn = row.get("turn")
        if isinstance(turn, int) and (rec["first_turn"] is None or turn < rec["first_turn"]):
            rec["first_turn"] = turn
        if "/references/" in str(row.get("path", "")):
            rec["deep"] = True
    return out


def _opened(path: Path) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return st.st_atime > st.st_mtime + ATIME_EPSILON_S


def probe_reads(ws_root: Path, materialized: SkillsMaterialized) -> SkillsUsage:
    """What the session did with the bundles we attached, per skill."""
    root = Path(ws_root)
    exact = _exact_reads(root)
    control_present, control_read = _control_state(root)
    reads: list[SkillRead] = []
    for sel in materialized.selections:
        name = sel.name
        bundles = [root / r / name for r in (AGENTS_SKILL_ROOT, CLAUDE_SKILL_ROOT)]
        refs = [f for b in bundles for f in sorted((b / "references").glob("*.md"))]
        surfaced = any(_opened(b / "SKILL.md") for b in bundles)
        deep = any(_opened(f) for f in refs)
        got = exact.get(name)
        if got:
            surfaced = True
            deep = deep or bool(got["deep"])
        # reading a reference means the bundle was reached, whatever SKILL.md's atime says
        # (relatime can miss the second access within 24 h, and some CLIs stream the body
        # out of their own index rather than re-opening the file)
        surfaced = surfaced or deep
        reads.append(SkillRead(
            name=name, surfaced=surfaced, deep=deep, deep_measurable=bool(refs),
            body_tokens=sel.skill.body_tokens, reason=sel.reason,
            first_seen_turn=got["first_turn"] if got else None,
        ))
    return SkillsUsage(
        listed=list(materialized.listed), reads=reads, index_tokens=materialized.index_tokens,
        body_tokens_read=sum(r.body_tokens for r in reads if r.deep),
        inlined=materialized.inlined,
        control_present=control_present, control_read=control_read,
    )


def _control_state(root: Path) -> tuple[bool, bool]:
    """(a control was materialised, something opened it) — the probe's own falsification."""
    from codeverse.skills.materialize import CONTROL_NAME

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
    """One JSON line per (run, round, session) — the file ``3dcv skills report`` reads."""
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


def deep_read_rate(usages: Sequence[SkillsUsage]) -> dict[str, tuple[int, int]]:
    """skill → (deep reads, times listed) across sessions — the metric with the teeth.

    Standing rule from the design: a skill under 20% over 20 sessions is merged or
    deleted.  A library that only ever grows is how this ends as bloat."""
    out: dict[str, list[int]] = {}
    for u in usages:
        for name in u.listed:
            out.setdefault(name, [0, 0])[1] += 1
        for r in u.reads:
            if r.deep:
                out.setdefault(r.name, [0, 0])[0] += 1
    return {k: (v[0], v[1]) for k, v in sorted(out.items())}


__all__ = ["ATIME_EPSILON_S", "READS_FILE", "SKILLS_FILE", "TELEMETRY_DIR", "append_usage",
           "deep_read_rate", "is_skill_path", "probe_reads", "record_exact_read"]
