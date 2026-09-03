"""How many joints a battery's mechanisms really have, against how many the sweep drives.

A coupled mechanism (umbrella ribs on one runner, a pantograph, a folding brace) has ONE
input and many moving links.  Before ``<mimic>`` support the pose sampler drove every
movable joint independently and posed states the mechanism cannot reach; it now drives
``independent_joints()`` only.  Both counts are properties of the recorded URDF, so this
reads the difference off the artefacts without re-running anything:

    python bench/coupling_stats.py bench/out/wave2_lean

``joints`` is every movable joint (what the sampler used to drive), ``independent`` is
what it drives now, and ``coupled prompts`` is how many of the battery's prompts declared
a coupling at all — the mechanism claim in docs/PAPER_WRITING.md §5.3.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # `python bench/coupling_stats.py` from the repo root

from codeverse.spatial.joints_model import UrdfError, load_urdf  # noqa: E402


def _urdfs(root: Path) -> list[Path]:
    """Every built ``robot.urdf`` under ``root``, deduplicated by resolved path."""
    seen: dict[Path, Path] = {}
    for p in root.rglob("robot.urdf"):
        seen.setdefault(p.resolve(), p)
    return sorted(seen.values())


def _prompt_of(path: Path) -> str:
    """The battery cell a URDF belongs to (``cells/<prompt>/<arm>/run/...``)."""
    parts = path.parts
    return parts[parts.index("cells") + 1] if "cells" in parts else path.parent.name


def per_prompt(root: Path) -> str:
    """One row per prompt that declared a coupling: what it moves, what drives it."""
    rows: dict[str, list[tuple[int, int]]] = {}
    for path in _urdfs(root):
        try:
            robot = load_urdf(path, load_meshes=False)
        except (UrdfError, OSError):
            continue
        moves, free = len(robot.movable_joints()), len(robot.independent_joints())
        if moves != free:
            rows.setdefault(_prompt_of(path), []).append((moves, free))
    out = [f"{root.name}, per prompt (coupled URDFs only):",
           "| prompt | URDFs | movable joints (median) | driven per pose (median) | fewest driven |",
           "|---|--:|--:|--:|--:|"]
    for prompt in sorted(rows):
        cells = rows[prompt]
        out.append(f"| {prompt} | {len(cells)} | {statistics.median(m for m, _ in cells):.0f} | "
                   f"{statistics.median(i for _, i in cells):.0f} | {min(i for _, i in cells)} |")
    return "\n".join(out)


def survey(root: Path) -> dict:
    movable: list[int] = []
    independent: list[int] = []
    coupled_prompts: set[str] = set()
    prompts: set[str] = set()
    unreadable = 0
    for path in _urdfs(root):
        try:
            robot = load_urdf(path, load_meshes=False)
        except (UrdfError, OSError):
            unreadable += 1
            continue
        moves = [j for j in robot.joints.values() if j.movable]
        free = robot.independent_joints()
        prompts.add(_prompt_of(path))
        movable.append(len(moves))
        independent.append(len(free))
        if len(free) != len(moves):
            coupled_prompts.add(_prompt_of(path))
    coupled = [(m, i) for m, i in zip(movable, independent, strict=True) if m != i]
    return {"urdfs": len(movable), "unreadable": unreadable, "prompts": len(prompts),
            "coupled_prompts": len(coupled_prompts), "movable": movable, "independent": independent,
            "coupled": coupled}


def report(roots: list[Path]) -> str:
    lines = ["| battery | URDFs | prompts | prompts with a coupling | coupled URDFs | "
             "joints, coupled URDFs (median) | driven per pose there (median) | one-input |",
             "|---|--:|--:|--:|--:|--:|--:|--:|"]
    for root in roots:
        s = survey(root)
        if not s["urdfs"]:
            lines.append(f"| {root.name} | 0 | — | — | — | — | — | — |")
            continue
        # the claim is about the mechanisms that HAVE a coupling: an uncoupled cabinet
        # dilutes both medians towards each other and says nothing either way
        moves = [m for m, _ in s["coupled"]] or [0]
        free = [i for _, i in s["coupled"]] or [0]
        lines.append(
            f"| {root.name} | {s['urdfs']} | {s['prompts']} | {s['coupled_prompts']} | "
            f"{len(s['coupled'])} | {statistics.median(moves):.0f} | {statistics.median(free):.0f} | "
            f"{sum(1 for n in free if n == 1)} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--per-prompt", action="store_true", help="also break each battery down by prompt")
    ns = ap.parse_args(argv)
    print(report(ns.roots))
    for root in ns.roots if ns.per_prompt else []:
        print()
        print(per_prompt(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
