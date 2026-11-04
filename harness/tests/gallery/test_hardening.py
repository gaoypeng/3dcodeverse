"""CSV cells must never reach a spreadsheet as formulas."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from codeverse3d.addons.gallery.compare import CSV_COLUMNS, csv_safe, export_csv
from codeverse3d.addons.gallery.index import build_index
from tests.flywheel_cli.conftest import make_fake_run


def test_csv_cells_never_reach_a_spreadsheet_as_formulas(tmp_path: Path):
    assert csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert csv_safe("+1") == "'+1" and csv_safe("-1") == "'-1" and csv_safe("@cmd") == "'@cmd"
    assert csv_safe("a chair") == "a chair"
    assert csv_safe(3) == 3 and csv_safe(None) is None
    runs = tmp_path / "runs"
    make_fake_run(runs, "formula_prompt", prompt="=2+5|cmd", scores=(0.5, 0.9))
    text = export_csv(build_index([runs]).entries())
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == list(CSV_COLUMNS) and len(rows) == 2
