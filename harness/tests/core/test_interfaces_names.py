"""docs/INTERFACES.md names only things that exist.

INTERFACES is the binding list of cross-package signatures, so a name it cites that no
longer resolves is a lie a reader will act on.  What counts as a *name* — everything else
is prose or a comment and is not checked, which keeps false positives out:

1. in a ```python fence, every ``from codeverse3d.<module> import a, b, (c, …)`` (each name
   an attribute or submodule of the module) and every ``import codeverse3d.<module>``;
2. in a fence, a line that STARTS with a signature ``X.y(`` / ``X.y:`` / ``X.y =`` whose
   ``X`` is a name rule 1 imported (``select.pick(…)``) or a codeverse3d subpackage
   (``tracks.common.generate_for(…)``);
3. in prose, a backticked dotted path whose first segment is ``codeverse3d`` or one of its
   top-level modules (``codeverse3d.proc``, ``addons.select.summarise``,
   ``agents/cli_common._enforce_scope``; a trailing ``(…)`` is allowed) — except in the
   "Events and records" section, whose dotted names are events (``round.done``), not code.

A name the file records as GONE sits in a comment (``# Gone: StopPolicy / …``) or is
written without a resolvable prefix, so it is never checked.  Pure python: importing the
package's modules starts no Blender, node or browser.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
import types
from pathlib import Path

import codeverse3d

HARNESS = Path(__file__).resolve().parents[2]
INTERFACES = HARNESS / "docs" / "INTERFACES.md"

TOP = {m.name for m in pkgutil.iter_modules(codeverse3d.__path__)} | {"codeverse3d"}
_FENCE = re.compile(r"^```python\n(.*?)^```", re.S | re.M)
_FROM = re.compile(r"^\s*from\s+(codeverse3d(?:\.\w+)*)\s+import\s+(\([^)]*\)|(?:[^\n\\#]|\\\n)+)", re.M)
_IMPORT = re.compile(r"^\s*import\s+(codeverse3d(?:\.\w+)*)", re.M)
_SIGNATURE = re.compile(r"^\s*([A-Za-z_]\w*)((?:\.\w+)+)\s*[(:=]", re.M)
_TICKED = re.compile(r"`([A-Za-z_]\w*(?:[/.]\w+)+)(?:\([^`]*\))?`")
_EVENTS = re.compile(r"^## Events and records\n.*?(?=^## |\Z)", re.S | re.M)
_FILE_SUFFIXES = (".py", ".json", ".jsonl", ".md", ".yaml", ".j2", ".js", ".mjs", ".cjs", ".glsl",
                  ".frag", ".urdf", ".png", ".glb")


def _import_names(clause: str) -> list[str]:
    clause = clause.strip().strip("()").replace("\\\n", " ")
    return [part.split(" as ")[0].strip() for part in clause.split(",") if part.strip()]


def _dotted(path: str) -> str:
    path = path.replace("/", ".")
    return path if path.startswith("codeverse3d.") else f"codeverse3d.{path}"


def cited_names(text: str) -> list[str]:
    """Every checked name as one fully qualified dotted path."""
    out: list[str] = []
    imported: dict[str, str] = {}
    fences = _FENCE.findall(text)
    for fence in fences:
        for module, clause in _FROM.findall(fence):
            for name in _import_names(clause):
                imported[name] = f"{module}.{name}"
                out.append(imported[name])
        out += _IMPORT.findall(fence)
    for fence in fences:
        for head, attrs in _SIGNATURE.findall(fence):
            if head in imported:
                out.append(imported[head] + attrs)
            elif head in TOP:
                out.append(_dotted(head + attrs))
    prose = _EVENTS.sub("", _FENCE.sub("", text))
    out += [_dotted(t) for t in _TICKED.findall(prose)
            if re.split(r"[/.]", t)[0] in TOP and not t.endswith(_FILE_SUFFIXES)]
    return out


def _step(obj: object, attr: str) -> object:
    if hasattr(obj, attr):
        return getattr(obj, attr)
    if isinstance(obj, types.ModuleType):  # a submodule the package does not import itself
        return importlib.import_module(f"{obj.__name__}.{attr}")
    raise AttributeError(attr)


def resolves(dotted: str) -> bool:
    obj: object = codeverse3d
    try:
        for attr in dotted.split(".")[1:]:
            obj = _step(obj, attr)
    except (AttributeError, ImportError):
        return False
    return True


def test_the_parser_reads_every_citation_form():
    text = ("```python\nfrom codeverse3d.proc import tail, x as y\n"
            "from codeverse3d.cost.tally import (tally,\n    timed)\n"
            "from codeverse3d.addons import select   # which round\n"
            "import codeverse3d.spatial.tools   # registers\n"
            "select.pick(run_dir) -> int\n"
            "tracks.common.generate_for(ctx) -> GenerationResult\n"
            "    # Gone: StopPolicy / pick_best_round\n```\n"
            "prose `codeverse3d.proc.tail`, `addons/select.package(ws)`, `record.json`, never `stop`\n"
            "## Events and records\n`round.done`\n")
    assert cited_names(text) == [
        "codeverse3d.proc.tail", "codeverse3d.proc.x", "codeverse3d.cost.tally.tally",
        "codeverse3d.cost.tally.timed", "codeverse3d.addons.select", "codeverse3d.spatial.tools",
        "codeverse3d.addons.select.pick", "codeverse3d.tracks.common.generate_for",
        "codeverse3d.proc.tail", "codeverse3d.addons.select.package",
    ]
    assert resolves("codeverse3d.addons.select.package") and resolves("codeverse3d.spatial.tools")
    assert not resolves("codeverse3d.proc.x") and not resolves("codeverse3d.nope.x")


def test_every_name_interfaces_cites_resolves():
    names = cited_names(INTERFACES.read_text(encoding="utf-8"))
    assert len(names) > 150, "the parser stopped finding INTERFACES' names"
    assert [n for n in names if not resolves(n)] == [], "docs/INTERFACES.md cites names the code no longer has"
