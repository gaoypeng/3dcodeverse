"""The named-import half of the JS import policy (``languages/_js_lint``)."""

from __future__ import annotations

from codeverse3d.languages._js_lint import (
    NamedImport,
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
