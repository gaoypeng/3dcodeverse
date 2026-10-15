"""The named-import half of the JS import policy (``languages/_js_lint``): text parsing and
the per-lint export cache, no node.  The scene-level behaviour is in ``test_lint.py``."""

from __future__ import annotations

from pathlib import Path

from codeverse3d.contracts.artifacts import GateFinding, Severity
from codeverse3d.languages._js_lint import (
    NamedImport,
    check_named_imports,
    describe_exports,
    exported_names,
    module_exports,
    named_import_specs,
)


def test_named_import_specs_forms():
    src = (
        "import * as THREE from 'three';\n"             # namespace: no names
        "import d from './d.js';\n"                     # default only: no names
        "import x, { a, b as c } from './m.js';\n"      # default + named: the IMPORTED names
        "import {\n  p,\n  q as r,\n} from \"./multi.js\";\n"
        "// import { gone } from './gone.js';\n"
        "/* import { gone2 } from './gone.js'; */\n"
        "import{s}from'./tight.js';\n"
        "import {} from './empty.js';\n"
        "const later = import('./dyn.js');\n"
    )
    assert named_import_specs(src) == [
        NamedImport("./m.js", ("a", "b"), 3),
        NamedImport("./multi.js", ("p", "q"), 4),
        NamedImport("./tight.js", ("s",), 10),
    ]


def test_module_exports_forms():
    src = (
        "export function f() {}\nexport async function g() {}\nexport function* h() {}\n"
        "export class C {}\nexport const k = 1;\nexport let l = 2;\nexport var v = 3;\n"
        "const a = 1, b = 2;\nexport { a, b as bee };\nexport { z as zed } from './z.js';\n"
        "export * as ns from './ns.js';\nexport default function () {}\n"
        "// export const commented = 1;\nconst s = 'export const inString = 1';\n"
    )
    assert module_exports(src) == frozenset({"f", "g", "h", "C", "k", "l", "v", "a", "bee", "zed", "ns", "default"})
    assert module_exports("const x = 1;\n") == frozenset()
    # not enumerable from the text: another module's surface, or a destructuring pattern
    assert module_exports("export * from './all.js';\nexport const x = 1;\n") is None
    assert module_exports("export const { a, b } = obj;\n") is None


def test_exported_names_reads_the_file_or_says_unknown(tmp_path: Path):
    p = tmp_path / "m.js"
    p.write_text("export const one = 1;\n")
    assert exported_names(p) == frozenset({"one"})
    assert exported_names(tmp_path / "missing.js") is None


def test_describe_exports_lists_at_most_twelve_sorted():
    assert describe_exports(frozenset()) == "it exports nothing"
    assert describe_exports(frozenset({"b", "a"})) == "it exports: a, b"
    many = frozenset(f"n{i:02d}" for i in range(15))
    assert describe_exports(many) == "it exports: " + ", ".join(f"n{i:02d}" for i in range(12)) + ", …"


def _finding(spec: str, name: str, line: int, names: frozenset[str]) -> GateFinding:
    return GateFinding(gate="t", severity=Severity.ERROR, target=f"z.js:{line}", message=f"{spec}:{name}:{describe_exports(names)}")


def test_check_named_imports_only_judges_resolved_js_targets_and_reads_each_once(tmp_path: Path):
    src_root = tmp_path / "src"
    (src_root / "lib").mkdir(parents=True)
    (src_root / "zones").mkdir()
    materials = src_root / "lib" / "materials.js"
    materials.write_text("export const brick = 1;\nexport const granite = 2;\n")
    (src_root / "lib" / "all.js").write_text("export * from './materials.js';\n")
    (src_root / "lib" / "data.json").write_text("{}")
    zone = src_root / "zones" / "z.js"
    zone.write_text(
        "import { brick, concrete } from '../lib/materials.js';\n"
        "import { anything } from '../lib/all.js';\n"           # star re-export: unknown surface, never reported
        "import { x } from '../lib/missing.js';\n"              # missing target: check_imports's finding, not ours
        "import { Vector3, Nope } from 'three';\n"              # not ours to read
        "import { y } from '../../outside.js';\n"               # escapes src/: not ours
        "import { d } from '../lib/data.json';\n"               # not a JS module
        "import { granite as g } from '../lib/materials.js';\n"
    )
    cache: dict[Path, frozenset[str] | None] = {}
    out = check_named_imports(zone.read_text(), zone, src_root, make_finding=_finding, exports=cache)
    assert [(f.target, f.message) for f in out] == [("z.js:1", "../lib/materials.js:concrete:it exports: brick, granite")]
    assert cache == {materials.resolve(): frozenset({"brick", "granite"}), (src_root / "lib" / "all.js").resolve(): None}
    # the cache is the lint's one read of each target: a later call trusts it over the file
    cache[materials.resolve()] = frozenset({"brick", "granite", "concrete"})
    assert check_named_imports(zone.read_text(), zone, src_root, make_finding=_finding, exports=cache) == []
