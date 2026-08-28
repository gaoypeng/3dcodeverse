"""ThreeJsRuntime error-record → BuildResult routing (pure python: no node needed)."""

from __future__ import annotations

from pathlib import Path

from codeverse.languages.threejs import ThreeJsRuntime
from codeverse.spatial.node import NodeResult
from codeverse.workspace import Workspace


def _res(error: dict) -> NodeResult:
    return NodeResult(rc=1, stdout="", stderr="", last_json={"ok": False, "error": error}, duration_ms=1)


def test_contract_error_part_routes_to_part_file(tmp_path: Path):
    ws = Workspace(tmp_path)
    (ws.src / "parts").mkdir(parents=True)
    (ws.src / "parts" / "seat_cushion.js").write_text("export function buildSeatCushion(T) {}\n")
    rt = ThreeJsRuntime()
    err = {"type": "ContractError", "message": "Mesh 'Top' (in part 'SeatCushion') has NaN/Infinity vertex positions",
           "file": "", "line": None, "frames": [{"file": "/abs/runtime_js/export_glb.mjs", "line": 9}], "part": "SeatCushion"}
    out = rt._from_error_record(_res(err), ws)
    assert out.error_type == "ContractError" and out.error_file == "src/parts/seat_cushion.js" and out.error_line is None
    assert out.census["part"] == "SeatCushion"
    # unknown part (no such file) → no guess; a src frame always wins over the part mapping
    assert rt._from_error_record(_res({**err, "part": "Nope"}), ws).error_file == ""
    assert rt._from_error_record(_res({**err, "file": "src/object.js", "line": 3}), ws).error_file == "src/object.js"
    # no frames at all (e.g. module-level failure) keeps the entry-file fallback
    assert rt._from_error_record(_res({**err, "part": "", "frames": []}), ws).error_file == "src/object.js"
