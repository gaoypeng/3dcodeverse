"""A deterministic code-quality vector for the flywheel: is this code worth learning from?

The judge scores the PICTURE; the gates score the GEOMETRY.  Nothing scored the CODE, and
the code is the dataset.  A run can ship a 0.85 render from a 900-line ``model.py`` with
one function, 400 unnamed numbers and three copies of the same loop — a fine artifact and
a poor training example.  This module measures the difference so ``flywheel export`` can
filter on it, the same way it already filters on ``score`` and ``complexity``.

Everything here is computed from the source tree of the BEST round, offline, in
milliseconds, with no model call.  Python languages (blender, cadquery, urdf_blender,
opengl_python) go through ``ast``; JavaScript / GLSL (threejs, scene_threejs, glsl_shader)
through a small tokenizer, because nothing in the harness's python side parses JS and
shelling out to node for a metric is not worth its cost.  The two paths report the same
fields; the JS numbers are approximate and say so (``method``).

What is measured, and why each one:

* ``magic_per_100loc`` — numeric literals that are neither bound to a name at module
  level nor one of the trivially-structural values (0, 1, 2, 0.5, -1, 180, 360 …).  The
  authoring contracts say "plan numbers = named constants at the top"; this is that rule
  as a number.  A dimension buried in a call cannot be found by a refine session and
  cannot be learned as a *design decision*.
* ``fn_len_max`` / ``fn_len_mean`` — a part is meant to be one function.  A 300-line
  function is a script, not a part library.
* ``dead_functions`` — defined and never referenced.  Scaffolding a model wrote and
  abandoned; the flywheel would learn to write it too.
* ``duplication`` — fraction of 6-line windows (whitespace-normalised) that occur more
  than once.  Symmetry by construction is the contract; copy-paste is its failure mode.
* ``docstring_cov`` (python only) — functions with a docstring.  Cheap, and the only
  in-band explanation of *why* a shape is what it is.
* ``const_names`` — module-level UPPER_CASE bindings.  The positive form of the magic
  count.

``index`` folds these into one 0-1 number with stated weights, so a dataset can be cut
at "index >= 0.6" the way it is cut at "score >= 0.7".  The weights are a first guess
and are recorded in the block so a later re-weighting can recompute from the fields.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

VERSION = 1
PY_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".mjs", ".glsl", ".frag", ".vert")
#: literals that are structure, not design: counting them as magic would punish every
#: ``range(2)``, ``* 0.5``, ``rotate(90)``.
_STRUCTURAL = frozenset({0, 1, 2, 3, 4, -1, 0.5, 90, 180, 360})
_WINDOW = 6
_WEIGHTS: dict[str, float] = {
    "magic": 0.30,
    "fn_len": 0.20,
    "dead": 0.15,
    "dup": 0.20,
    "doc": 0.05,
    "const": 0.10,
}


class CodeQuality(BaseModel):
    version: int = VERSION
    method: str = Field(description="ast (python) | tokens (js/glsl, approximate) | mixed")
    n_files: int = 0
    loc: int = 0
    n_functions: int = 0
    fn_len_mean: float = 0.0
    fn_len_max: int = 0
    magic_numbers: int = 0
    magic_per_100loc: float = 0.0
    const_names: int = 0
    dead_functions: int = 0
    docstring_cov: float | None = Field(default=None, description="python only")
    duplication: float = 0.0
    index: float = 0.0
    weights: dict[str, float] = Field(default_factory=lambda: dict(_WEIGHTS))


# ----------------------------------------------------------------------------- python
def _py_stats(src: str) -> dict[str, Any]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {"parsed": False}
    const_names: set[str] = set()
    bound_values: set[float] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.target is not None:
            targets = [node.target]
        for t in targets:
            names = [e.id for e in ast.walk(t) if isinstance(e, ast.Name)]
            if any(n.isupper() for n in names):
                const_names.update(n for n in names if n.isupper())
                for c in ast.walk(
                    node.value
                    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value
                    else ast.Constant(0)
                ):
                    if (
                        isinstance(c, ast.Constant)
                        and isinstance(c.value, (int, float))
                        and not isinstance(c.value, bool)
                    ):
                        bound_values.add(c.value)
    fns = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    fn_lens = [max(1, (n.end_lineno or n.lineno) - n.lineno + 1) for n in fns]
    docs = sum(1 for n in fns if ast.get_docstring(n))
    called: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            called.add(n.id)
        elif isinstance(n, ast.Attribute):
            called.add(n.attr)
    defined = {n.name for n in fns}
    dead = [d for d in defined if d not in called and not d.startswith("_") and d != "main"]
    # magic: numeric constants inside calls / binops, not in a module-level constant binding
    top_level_const_nodes: set[int] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for c in ast.walk(node):
                top_level_const_nodes.add(id(c))
    magic = 0
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)
        ):
            if id(n) in top_level_const_nodes or n.value in _STRUCTURAL or n.value in bound_values:
                continue
            magic += 1
    return {
        "parsed": True,
        "n_functions": len(fns),
        "fn_lens": fn_lens,
        "docs": docs,
        "dead": len(dead),
        "magic": magic,
        "const_names": len(const_names),
    }


# ----------------------------------------------------------------------------- js / glsl
_JS_FN = re.compile(
    r"\bfunction\b\s*[A-Za-z_$][\w$]*\s*\(|=>|\b(?:export\s+)?(?:async\s+)?function\s*\("
)
_JS_NAMED_FN = re.compile(
    r"\bfunction\s+([A-Za-z_$][\w$]*)|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
#: every UPPER_CASE name bound in a ``const`` statement — ``const A = 1, B = 2`` binds two
_JS_CONST_STMT = re.compile(r"\bconst\s+([^;]+?);", re.S)
_JS_UPPER_BIND = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\s*=")
_JS_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")
_JS_STRIP = re.compile(
    r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`", re.S
)


def _js_stats(src: str) -> dict[str, Any]:
    code = _JS_STRIP.sub(" ", src)
    lines = code.splitlines()
    consts: set[str] = set()
    for m in _JS_CONST_STMT.finditer(code):
        consts.update(_JS_UPPER_BIND.findall(m.group(1)))
    const_line_idx = {
        i
        for i, ln in enumerate(lines)
        if ln.lstrip().startswith("const ") and _JS_UPPER_BIND.search(ln)
    }
    magic = 0
    for i, ln in enumerate(lines):
        if i in const_line_idx:
            continue
        for m in _JS_NUM.finditer(ln):
            try:
                v = float(m.group(0))
            except ValueError:
                continue
            if v in _STRUCTURAL:
                continue
            magic += 1
    # crude function extents: count openers; lengths via brace depth from each 'function' / '=>'
    names = [g[0] or g[1] for g in _JS_NAMED_FN.findall(code)]
    fn_lens: list[int] = []
    for m in _JS_FN.finditer(code):
        depth = 0
        started = False
        length = 0
        for ch in code[m.start() :]:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
            elif ch == "\n":
                length += 1
            if started and depth == 0:
                break
            if length > 2000:
                break
        fn_lens.append(max(1, length))
    called = set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", code))
    exported = set(re.findall(r"\bexport\s+(?:function|const|let)\s+([A-Za-z_$][\w$]*)", code))
    dead = [n for n in names if n and n not in called and n not in exported]
    return {
        "parsed": True,
        "n_functions": len(fn_lens),
        "fn_lens": fn_lens,
        "docs": None,
        "dead": len(dead),
        "magic": magic,
        "const_names": len(consts),
    }


# ----------------------------------------------------------------------------- shared
def _duplication(sources: list[str]) -> float:
    seen: dict[str, int] = {}
    total = 0
    for src in sources:
        norm = [re.sub(r"\s+", " ", ln).strip() for ln in src.splitlines()]
        norm = [ln for ln in norm if ln and not ln.startswith(("#", "//"))]
        for i in range(0, max(0, len(norm) - _WINDOW + 1)):
            h = hashlib.blake2b(
                "\n".join(norm[i : i + _WINDOW]).encode(), digest_size=8
            ).hexdigest()
            seen[h] = seen.get(h, 0) + 1
            total += 1
    if not total:
        return 0.0
    dup = sum(c for c in seen.values() if c > 1)
    return round(dup / total, 4)


def _loc(src: str) -> int:
    return sum(
        1 for ln in src.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//"))
    )


def _index(q: CodeQuality) -> float:
    """0-1; 1 = tidy.  Each term is a saturating penalty, so one bad axis cannot go negative."""
    magic = max(0.0, 1.0 - q.magic_per_100loc / 25.0)  # 25 unnamed numbers per 100 lines = 0
    fn_len = max(0.0, 1.0 - max(0, q.fn_len_max - 60) / 240.0)  # a 300-line function = 0
    dead = max(0.0, 1.0 - q.dead_functions / max(q.n_functions, 1) * 2)
    dup = max(0.0, 1.0 - q.duplication * 3)  # a third duplicated = 0
    doc = q.docstring_cov if q.docstring_cov is not None else 0.5
    const = min(1.0, q.const_names / 8.0)
    w = _WEIGHTS
    return round(
        w["magic"] * magic
        + w["fn_len"] * fn_len
        + w["dead"] * dead
        + w["dup"] * dup
        + w["doc"] * doc
        + w["const"] * const,
        4,
    )


def measure(files: Mapping[str, bytes | str]) -> CodeQuality | None:
    """The vector for one code tree (``path -> bytes/str``).  ``None`` when nothing parses."""
    py: list[dict[str, Any]] = []
    js: list[dict[str, Any]] = []
    sources: list[str] = []
    loc = 0
    n = 0
    for path, body in files.items():
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        if path.endswith(PY_SUFFIXES):
            st = _py_stats(text)
            if st.get("parsed"):
                py.append(st)
                sources.append(text)
                loc += _loc(text)
                n += 1
        elif path.endswith(JS_SUFFIXES):
            st = _js_stats(text)
            js.append(st)
            sources.append(text)
            loc += _loc(text)
            n += 1
    stats = py + js
    if not stats:
        return None
    fn_lens = [x for s in stats for x in s["fn_lens"]]
    n_fn = sum(s["n_functions"] for s in stats)
    docs = [s["docs"] for s in py if s.get("docs") is not None]
    q = CodeQuality(
        method="ast" if py and not js else "tokens" if js and not py else "mixed",
        n_files=n,
        loc=loc,
        n_functions=n_fn,
        fn_len_mean=round(sum(fn_lens) / len(fn_lens), 1) if fn_lens else 0.0,
        fn_len_max=max(fn_lens) if fn_lens else 0,
        magic_numbers=sum(s["magic"] for s in stats),
        magic_per_100loc=round(100.0 * sum(s["magic"] for s in stats) / max(loc, 1), 2),
        const_names=sum(s["const_names"] for s in stats),
        dead_functions=sum(s["dead"] for s in stats),
        docstring_cov=round(sum(docs) / max(sum(s["n_functions"] for s in py), 1), 3)
        if py
        else None,
        duplication=_duplication(sources),
    )
    q.index = _index(q)
    return q


def code_quality_block(ws: Any, record: Any) -> dict[str, Any] | None:
    """``record.extra["code_quality"]`` for the BEST round's code tree, or None."""
    from codeverse.flywheel.sample import best_round_record, code_files_for_round

    try:
        rnd = best_round_record(record)
        files, source = code_files_for_round(ws, rnd)
    except Exception:  # noqa: BLE001 — a metric must never fail a run
        return None
    q = measure(files)
    if q is None:
        return None
    block = q.model_dump(mode="json")
    block["code_source"] = source
    return block
