#!/usr/bin/env python3
"""Assemble the self-contained CadQuery shim from the core + per-family modules.

- assemble(families=None) -> str : combined shim source (core + selected family
  modules), with each family module's `from articraft_cq import (...)` and
  `from __future__` lines stripped (the core already provides those names).
  families=None -> ALL families found in tools/shim_parts/.
- name2family : map of every geometry name -> its family module stem.

CLI:  python tools/assemble_shim.py            # write tools/_shimpkg/sdk.py (all families)
      python tools/assemble_shim.py --names    # print name -> family map
"""
import os, re, ast, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
TOOLS = _HERE
CORE = TOOLS + "/articraft_cq.py"
PARTS = TOOLS + "/shim_parts"

_IMPORT_CORE_RE = re.compile(r"^from articraft_cq import\b")
_FUTURE_RE = re.compile(r"^from __future__ import\b")


def _strip_core_imports(src):
    """Remove `from articraft_cq import ...` (incl. multi-line parenthesised)
    and `from __future__ ...` lines from a family module."""
    lines = src.splitlines()
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if _FUTURE_RE.match(ln):
            i += 1
            continue
        if _IMPORT_CORE_RE.match(ln):
            # consume continuation lines if parenthesised import
            if "(" in ln and ")" not in ln:
                i += 1
                while i < len(lines) and ")" not in lines[i]:
                    i += 1
                i += 1  # skip the line with ')'
            else:
                i += 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def _family_files(families=None):
    if not os.path.isdir(PARTS):
        return []
    stems = sorted(f[:-3] for f in os.listdir(PARTS) if f.endswith(".py") and not f.startswith("_"))
    if families is not None:
        stems = [s for s in stems if s in set(families)]
    return [(s, os.path.join(PARTS, s + ".py")) for s in stems]


def defined_names(src):
    """Top-level class/def/assignment names defined in a source string."""
    names = []
    try:
        tree = ast.parse(src)
    except Exception:
        return names
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
    return names


def name2family():
    m = {}
    for stem, path in _family_files():
        src = open(path).read()
        for n in defined_names(src):
            if not n.startswith("_"):
                m[n] = stem
    return m


def assemble(families=None):
    core = open(CORE).read()
    # core keeps its own __future__ at the very top; ensure it's first
    parts = [core]
    for stem, path in _family_files(families):
        body = _strip_core_imports(open(path).read())
        parts.append("\n\n# ===== family: %s =====\n%s" % (stem, body))
    return "\n".join(parts)


def main():
    if "--names" in sys.argv:
        import json
        print(json.dumps(name2family(), indent=1, ensure_ascii=False))
        return
    os.makedirs(TOOLS + "/_shimpkg", exist_ok=True)
    text = assemble()
    with open(TOOLS + "/_shimpkg/sdk.py", "w") as f:
        f.write(text)
    nm = name2family()
    print("wrote tools/_shimpkg/sdk.py  (%d chars)" % len(text))
    print("families:", sorted(set(nm.values())))
    print("geometry names exposed:", len(nm))


if __name__ == "__main__":
    main()
