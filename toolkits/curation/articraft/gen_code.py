#!/usr/bin/env python3
"""Generate a MINIMAL self-contained CadQuery code.py for an Articraft with_mesh
sample (tree-shaken: only the shim symbols the model actually uses are inlined).

code.py = future import + base imports + (any extra imports the used family
symbols need) + CORE shim + only-used family symbols (dependency closure) +
the original model body (sdk/future imports removed) + epilogue building
`result = object_model.to_cq()`.

Only needs `import cadquery` (+ numpy/OCP only if a used family — e.g. gears —
requires it). Tree-shaking drops unused geometry classes/helpers, so files are
far smaller than whole-family inlining.

Usage: python tools/gen_code.py <sample_name|dir> [--out PATH] [--inplace]
"""
import os, re, ast, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("ARTICRAFT_ROOT", os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
DD = ROOT + "/data/articraft_with_mesh"
sys.path.insert(0, _HERE)
import assemble_shim as _asm

_FUTURE = "from __future__ import annotations"
_CORE_SRC = None
_NAME2FAM = None
_CORE_SYMS = {}     # sym -> (text, refs) in core file order
_CORE_IMPORTS = []
_FAM_SYMS = {}      # fam -> {sym: (text, refs_set)} in file order
_FAM_IMPORTS = {}   # fam -> [import source lines]
# always-seed runtime so the to_cq()/build path survives even if only referenced dynamically
_RUNTIME_SEED = {
    "ArticulatedObject", "Part", "Visual", "Articulation", "Origin", "Material",
    "ArticulationType", "_Tf", "_to_shape", "_CQMesh", "ValidationError",
    "mesh_from_cadquery",
}


def _blocks(src):
    """Split a module source into (import_lines, {sym:(text,refs)}) preserving
    top-level order. Captures decorators via line slicing (get_source_segment
    would DROP @dataclass/@property)."""
    tree = ast.parse(src)
    lines = src.split("\n")
    imports, syms = [], {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append("\n".join(lines[node.lineno - 1:node.end_lineno]))
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        else:
            continue
        if not names:
            continue
        start = node.lineno
        for d in getattr(node, "decorator_list", []) or []:
            start = min(start, d.lineno)
        seg = "\n".join(lines[start - 1:node.end_lineno])
        refs = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}
        for nm in names:
            syms[nm] = (seg, refs)
    return imports, syms


def _load():
    global _CORE_SRC, _NAME2FAM, _CORE_IMPORTS, _CORE_SYMS
    if _CORE_SRC is not None:
        return
    core = open(_asm.CORE).read()
    core = "\n".join(l for l in core.splitlines() if l.strip() != _FUTURE)
    _CORE_SRC = core
    # core import lines: keep math/cadquery; drop the redundant ones we emit in header
    cimps, csyms = _blocks(core)
    _CORE_IMPORTS = [i for i in cimps if i.strip() not in ("import math", "import cadquery as cq")]
    _CORE_SYMS = csyms
    _NAME2FAM = _asm.name2family()
    for stem, path in _asm._family_files():
        src = _asm._strip_core_imports(open(path).read())
        imps, syms = _blocks(src)
        _FAM_IMPORTS[stem] = imps
        _FAM_SYMS[stem] = syms


def _closure(fam, seed):
    syms = _FAM_SYMS[fam]
    need, stack = set(), [s for s in seed if s in syms]
    while stack:
        s = stack.pop()
        if s in need:
            continue
        need.add(s)
        for r in syms[s][1]:
            if r in syms and r not in need:
                stack.append(r)
    return need


def _imported_sdk_names(src):
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and (node.module == "sdk" or (node.module or "").startswith("sdk.")):
            out += [a.name for a in node.names]
    return out


def _model_node_names(nd):
    """Top-level names a node defines: def/class name, or assign targets
    (incl. tuple/list unpacking like `A, B = f()` and annotated assigns)."""
    if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [nd.name]
    out = []
    if isinstance(nd, ast.Assign):
        for t in nd.targets:
            if isinstance(t, ast.Name):
                out.append(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                out += [e.id for e in t.elts if isinstance(e, ast.Name)]
    elif isinstance(nd, ast.AnnAssign) and isinstance(nd.target, ast.Name):
        out.append(nd.target.id)
    return out


def _treeshake_model(body):
    """Keep only build_object_model and its FULL closure — geometry helpers AND
    the module-level constants it uses AND any functions those constants call
    (e.g. `KNOB_CENTER_X, KNOB_CENTER_Y = _key_center(...)`). Drops run_tests()
    and other test-only / dead top-level code. The epilogue rebuilds
    object_model = build_object_model()."""
    try:
        tree = ast.parse(body)
    except Exception:
        return body
    lines = body.split("\n")
    sym_nodes = {}     # name -> [defining nodes in order] (a name may be (re)defined
    refs_union = {}    #   multiple times, e.g. SHAFT_Y built in two steps where the
    for nd in tree.body:  # second references the first — keep ALL, not just the last
        nms = _model_node_names(nd)
        if not nms:
            continue
        r = {x.id for x in ast.walk(nd) if isinstance(x, ast.Name)}
        for nm in nms:
            sym_nodes.setdefault(nm, []).append(nd)
            refs_union.setdefault(nm, set()).update(r)
    if "build_object_model" not in sym_nodes:
        return body  # unusual structure -> keep whole (safe)
    keep, st = set(), ["build_object_model"]
    while st:
        s = st.pop()
        if s in keep or s not in sym_nodes:
            continue
        keep.add(s)
        st += [r for r in refs_union[s] if r in sym_nodes and r not in keep]
    kept_ids = {id(nd) for n in keep for nd in sym_nodes[n]}
    dropped = set(sym_nodes) - keep
    out = []
    for nd in tree.body:
        if isinstance(nd, (ast.Import, ast.ImportFrom)):
            pass  # always keep imports
        elif _model_node_names(nd):                       # a definition / assignment
            if id(nd) not in kept_ids:
                continue
        else:                                              # bare statement (Expr / If __main__ / call)
            names = {x.id for x in ast.walk(nd) if isinstance(x, ast.Name)}
            if names & dropped:                            # e.g. bare run_tests() / report = run_tests()
                continue
        a = nd.lineno
        for d in getattr(nd, "decorator_list", []) or []:
            a = min(a, d.lineno)
        out.append("\n".join(lines[a - 1:nd.end_lineno]))
    return "\n".join(out)


def _strip_model_imports(src):
    lines = src.splitlines()
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("from __future__ import"):
            i += 1
            continue
        if s.startswith("import sdk") or re.match(r"^from sdk(\.|\s)", s):
            if "(" in ln and ")" not in ln:
                i += 1
                while i < len(lines) and ")" not in lines[i]:
                    i += 1
                i += 1
            else:
                i += 1
            continue
        out.append(ln)
        i += 1
    return "\n".join(out)


def gen_code_text(sample_dir):
    _load()
    src_path = os.path.join(sample_dir, "articraft_source_code.py")
    if not os.path.exists(src_path):
        src_path = os.path.join(sample_dir, "code.py")
    model_src = open(src_path).read()

    # unified symbol graph over core + all families. Some names exist in BOTH
    # core and a family (or two families). Use LAST-DEFINITION-WINS, matching
    # Python runtime if every copy were emitted in order core -> sorted(families):
    # a name in core AND a family -> the family copy is the real one (core may
    # hold only a stub, e.g. repair_loft); a name in two families -> identical
    # copies, last sorted family wins. The closure refs MUST come from the SAME
    # copy that gets emitted, so build combined + last_owner with that order.
    combined, last_owner = {}, {}
    for s, (t, r) in _CORE_SYMS.items():
        combined[s] = r; last_owner[s] = "core"
    for fam in sorted(_FAM_SYMS):
        for s, (t, r) in _FAM_SYMS[fam].items():
            combined[s] = r; last_owner[s] = fam

    body = _treeshake_model(_strip_model_imports(model_src))
    try:
        seed = {nd.id for nd in ast.walk(ast.parse(body)) if isinstance(nd, ast.Name)} | set(_RUNTIME_SEED)
    except Exception:
        seed = set(_imported_sdk_names(model_src)) | set(_RUNTIME_SEED)
    need, stack = set(), [s for s in seed if s in combined]
    while stack:
        s = stack.pop()
        if s in need:
            continue
        need.add(s)
        for r in combined[s]:
            if r in combined and r not in need:
                stack.append(r)

    # emit each needed symbol ONCE, from its last_owner copy (the one Python would
    # actually use) — drops the shadowed earlier duplicate (core stub / first copy).
    emitted_shim = set()
    core_blocks = []
    for s, (t, r) in _CORE_SYMS.items():
        if s in need and last_owner[s] == "core":
            core_blocks.append(t); emitted_shim.add(s)
    fam_chunks, extra_imports, used_fams = [], [], []
    for fam in sorted(_FAM_SYMS):
        blocks = []
        for s, (t, r) in _FAM_SYMS[fam].items():
            if s in need and last_owner[s] == fam:
                blocks.append(t); emitted_shim.add(s)
        if not blocks:
            continue
        used_fams.append((fam, len(blocks)))
        for imp in _FAM_IMPORTS[fam]:
            if imp not in extra_imports:
                extra_imports.append(imp)
        fam_chunks.append("\n# ===== %s (%d syms) =====\n%s" % (fam, len(blocks), "\n\n".join(blocks)))

    # cq_gears' `cq.Workplane.gear(SpurGear(...))` plugin: some models call it
    # directly. It's a module-level attribute-assign (not a tree-shakeable symbol),
    # so emit it explicitly whenever the gears family is inlined.
    if any(f == "gears" for f, _ in used_fams):
        fam_chunks.append(_GEAR_PLUGIN)

    # Inlining puts shim helpers AND the model's own helpers in ONE namespace. If a
    # model defines a PRIVATE helper with the same name as a shim helper, the model
    # copy (emitted last) shadows the shim one and breaks the shim's internal calls
    # (seen: model `_v_norm`->tuple shadowing sweeps `_v_norm`->float). Rename the
    # colliding shim-private helpers (+ their refs within the shim) out of the way.
    model_names = set()
    try:
        for nd in ast.parse(body).body:
            model_names.update(_model_node_names(nd))
    except Exception:
        pass
    collisions = {n for n in emitted_shim if n.startswith("_") and n in model_names}
    if collisions:
        rmap = {c: "_shim" + c for c in collisions}

        def _ren(txt):
            for c, nw in rmap.items():
                txt = re.sub(r"\b" + re.escape(c) + r"\b", nw, txt)
            return txt
        core_blocks = [_ren(t) for t in core_blocks]
        fam_chunks = [_ren(t) for t in fam_chunks]

    epilogue = (
        "\n\n# ---- self-contained REST-pose assembly ----\n"
        "try:\n    object_model\nexcept NameError:\n    object_model = build_object_model()\n"
        "result = object_model.to_cq()\n"
    )
    code_parts = ["\n# ===== shim core =====\n" + "\n\n".join(core_blocks)] + fam_chunks
    code_parts += ["\n# ===== model =====\n" + body, epilogue]
    code_body = "\n".join(code_parts)
    header = [_FUTURE, "", "import cadquery as cq", "import math"]
    # some models do `pathlib.os.X` (worked pre-3.13 when pathlib re-exported os)
    if "pathlib.os" in code_body:
        header += ["import os as _os_c, pathlib as _pl_c",
                   "if not hasattr(_pl_c, 'os'): _pl_c.os = _os_c"]
    # prune import lines whose names are never referenced in the body
    header += _prune_imports(_CORE_IMPORTS + extra_imports, code_body)
    return "\n".join(["\n".join(header)] + code_parts), [f for f, _ in used_fams]


_GEAR_PLUGIN = '''
# ===== gears cq.Workplane plugin (cq_gears workflow) =====
def _gear_plugin(self, gear_, *a, **k):
    body = gear_.build(*a, **k)
    if hasattr(body, "val"):
        body = body.val()           # Workplane -> Shape
    return self.eachpoint(lambda loc: body.located(loc), True)


def _addgear_plugin(self, gear_, *a, **k):
    return self.union(_gear_plugin(self, gear_, *a, **k))


cq.Workplane.gear = _gear_plugin
cq.Workplane.addGear = _addgear_plugin
'''


def _prune_imports(imports, body):
    out = []
    for line in imports:
        try:
            node = ast.parse(line.strip()).body[0]
        except Exception:
            out.append(line); continue
        if isinstance(node, ast.Import):
            names = [a.asname or a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [a.asname or a.name for a in node.names]
        else:
            out.append(line); continue
        if not names or any(re.search(r"\b" + re.escape(n) + r"\b", body) for n in names):
            out.append(line)
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: gen_code.py <sample_name|dir> [--out PATH] [--inplace]")
        sys.exit(1)
    arg = sys.argv[1]
    sample_dir = arg if os.path.isdir(arg) else os.path.join(DD, arg)
    name = os.path.basename(sample_dir.rstrip("/"))
    txt, fams = gen_code_text(sample_dir)
    if "--inplace" in sys.argv:
        out = os.path.join(sample_dir, "code.py")
    elif "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    else:
        d = os.path.join(os.environ.get("CLAUDE_JOB_DIR", "/tmp"), "tmp", "gen")
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, name + ".code.py")
    with open(out, "w") as f:
        f.write(txt)
    print("wrote", out, "| families:", fams, "| lines:", txt.count(chr(10)) + 1)


if __name__ == "__main__":
    main()
