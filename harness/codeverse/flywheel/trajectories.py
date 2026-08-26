"""Mine repair pairs from agent trajectories (``trajectories/<stage>_rNN/transcript.jsonl``).

Round-level repair pairs (``pairs.repair_pairs``) need a round whose build
failed.  Most build failures are fixed *inside* one agent session instead —
the ``api-agent`` transcript records every ``write_file`` / ``edit_file`` /
``build`` tool call, so the file state can be replayed::

    write_file ... → build FAIL (error) → edit/write ... → build OK
                      ^ rejected snapshot                     ^ chosen snapshot

Only the ``api-agent`` transcript format (``assistant`` turns with
``tool_calls`` + ``tool_result`` turns) is structured enough; CLI backends
(gemini-cli / codex / claude-code) log raw stdout lines and yield no pairs.

The base file state is the ``pre:<label>`` git commit taken by the harness
right before the session (nearest one not after the first transcript event);
``read_file`` results are not used (they may be truncated).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from codeverse.flywheel import _git
from codeverse.proc import read_jsonl_lenient
from codeverse.workspace import Workspace

BUILD_TOOLS = frozenset({"build", "run_build"})
MAX_PAIR_CODE = 200_000


class TrajectoryRepair(BaseModel):
    """One in-session repair: the last failing snapshot before a successful build."""

    trajectory: str = Field(description="trajectory dir name, e.g. refine_r00")
    stage: str
    round_index: int | None
    broken_turn: int
    fixed_turn: int
    error: str
    rejected: dict[str, str] = Field(description="path → code at the failing build")
    chosen: dict[str, str] = Field(description="path → code at the next successful build")
    changed_files: list[str]
    n_failures: int = Field(description="consecutive failing builds folded into this pair")


def parse_trajectory_name(name: str) -> tuple[str, int | None]:
    """``refine_r00`` → ``("refine", 0)``; ``zone_koi_pond_r01`` → ``("zone_koi_pond", 1)``."""
    stem, _, tail = name.rpartition("_r")
    if stem and tail.isdigit():
        return stem, int(tail)
    return name, None


def _base_commit(ws: Workspace, label: str, not_after: float | None) -> str | None:
    """Latest ``pre:<label>`` commit at or before ``not_after`` (epoch seconds)."""
    try:
        proc = subprocess.run(
            ["git", "log", "--format=%H %ct %s"], cwd=ws.root, capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():  # newest first
        sha, _, rest = line.partition(" ")
        ts, _, subject = rest.partition(" ")
        if subject.strip() != f"pre:{label}":
            continue
        if not_after is None or float(ts) <= not_after + 1.0:
            return sha
    return None


def _tracked(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts) and parts[0] in _git.CODE_ROOTS


def _normalise_path(ws: Workspace, raw: str) -> str:
    p = Path(raw)
    if p.is_absolute():
        try:
            p = p.relative_to(ws.root)
        except ValueError:
            return raw
    return p.as_posix()


class _Replay:
    """Applies write/edit tool calls to an in-memory tree; tracks whether it is exact."""

    def __init__(self, base: dict[str, str]):
        self.files = dict(base)
        self.exact = True  # False once an edit touched a file we never saw in full
        self.changed: set[str] = set()

    def write(self, path: str, content: str) -> None:
        self.files[path] = content
        self.changed.add(path)

    def edit(self, path: str, old: str, new: str, all_: bool) -> None:
        if path not in self.files:
            self.exact = False
            return
        text = self.files[path]
        if not old or old not in text:
            self.exact = False
            return
        self.files[path] = text.replace(old, new) if all_ else text.replace(old, new, 1)
        self.changed.add(path)


def read_events(transcript: Path) -> list[dict[str, Any]]:
    return read_jsonl_lenient(transcript)


def mine_trajectory(ws: Workspace, traj_dir: Path) -> list[TrajectoryRepair]:
    """Repair pairs from one trajectory folder (empty for non api-agent transcripts)."""
    transcript = traj_dir / "transcript.jsonl"
    if not transcript.is_file():
        return []
    events = read_events(transcript)
    calls: dict[str, dict[str, Any]] = {}
    for ev in events:
        if ev.get("kind") == "assistant":
            for c in ev.get("tool_calls") or []:
                if isinstance(c, dict) and c.get("id"):
                    calls[str(c["id"])] = c
    if not calls:
        return []
    stage, rnd = parse_trajectory_name(traj_dir.name)
    t0 = next((float(ev["t"]) for ev in events if "t" in ev), None)
    base_sha = _base_commit(ws, stage, t0)
    base: dict[str, str] = {}
    if base_sha:
        try:
            base, _ = _git.decode_text_files(_git.read_tree_at(ws, base_sha))
        except _git.GitReadError:
            base = {}
    rp = _Replay(base)
    broken: tuple[int, dict[str, str], str, set[str]] | None = None  # turn, snapshot, error, changed-before
    n_fail = 0
    out: list[TrajectoryRepair] = []
    for ev in events:
        if ev.get("kind") != "tool_result":
            continue
        call = calls.get(str(ev.get("call_id")))
        if call is None:
            continue
        name, args = str(call.get("name")), dict(call.get("arguments") or {})
        ok = bool(ev.get("ok"))
        turn = int(ev.get("turn", -1))
        if name == "write_file" and ok:
            path = _normalise_path(ws, str(args.get("path", "")))
            if _tracked(path):
                rp.write(path, str(args.get("content", "")))
        elif name == "edit_file" and ok:
            path = _normalise_path(ws, str(args.get("path", "")))
            if _tracked(path):
                rp.edit(path, str(args.get("old", "")), str(args.get("new", "")), bool(args.get("all", False)))
        elif name in BUILD_TOOLS:
            if not ok:
                n_fail += 1
                broken = (turn, dict(rp.files), str(ev.get("content", ""))[:4000], set(rp.changed))
                continue
            if broken is not None and rp.exact:
                b_turn, b_files, b_err, _ = broken
                changed = sorted(p for p in set(b_files) | set(rp.files) if b_files.get(p) != rp.files.get(p))
                if changed:
                    out.append(TrajectoryRepair(
                        trajectory=traj_dir.name, stage=stage, round_index=rnd, broken_turn=b_turn,
                        fixed_turn=turn, error=b_err, rejected=_limit(b_files), chosen=_limit(rp.files),
                        changed_files=changed, n_failures=n_fail,
                    ))
            broken, n_fail = None, 0
    return out


def _limit(files: dict[str, str], max_total: int = MAX_PAIR_CODE) -> dict[str, str]:
    out: dict[str, str] = {}
    total = 0
    for p in sorted(files):
        if total + len(files[p]) > max_total:
            break
        out[p] = files[p]
        total += len(files[p])
    return out


def mine_run(ws: Workspace) -> list[TrajectoryRepair]:
    """All in-session repair pairs of one run (every ``trajectories/*`` folder)."""
    root = ws.root / "trajectories"
    if not root.is_dir():
        return []
    out: list[TrajectoryRepair] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        out.extend(mine_trajectory(ws, d))
    return out
