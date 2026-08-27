"""``edit_only``: a refine session may not overwrite part files the round did not name.

Measured 2026-08-25 over 128 judged refine rounds on static objects: the rounds that
REGRESSED had rewritten a median 6 of 9 part files, the rounds that improved 4 of 8, and
the regressions' worst-dropping criterion was structure_plausibility with zero gate errors
— working geometry the task never mentioned was replaced, and nothing deterministic saw
it. The prompt already said "keep everything else as it is"; only the tool can hold it.
"""

from __future__ import annotations

import pytest

from codeverse.agents.api_tools import FileTools, PathDenied


def test_a_scoped_session_cannot_overwrite_a_neighbouring_part(scoped_ws):
    ws = scoped_ws
    ft = FileTools(ws, ["src", "public"], edit_only=["src/parts/seat.py"], always_writable=["src/model.py"])
    assert not ft.write_file("src/parts/seat.py", "# new seat\n").is_error, "the named file is editable"
    with pytest.raises(PathDenied) as ei:  # the agent loop turns PathDenied into a tool error
        ft.write_file("src/parts/leg.py", "# rewritten leg\n")
    assert "leg.py" in str(ei.value) and "seat.py" in str(ei.value), "the refusal names the file and the allowed list"
    assert (ws.root / "src" / "parts" / "leg.py").read_text() == "# leg\n", "and nothing was written"
    assert ft.scope_denials == ["src/parts/leg.py"], "denials are counted so the rate can be measured"


def test_the_entry_file_is_writable_only_by_its_owner(scoped_ws):
    """Adding a part means a new file AND an import line in the entry: both must work for
    the owner.  Without ``always_writable`` (owns_entry=False) the entry is just another
    out-of-scope existing file — the api-agent side of CLI entry ownership."""
    ws = scoped_ws
    ft = FileTools(ws, ["src", "public"], edit_only=["src/parts/seat.py"], always_writable=["src/model.py"])
    assert not ft.write_file("src/parts/armrest.py", "# new part\n").is_error
    assert not ft.write_file("src/model.py", "# entry + import\n").is_error
    assert ft.scope_denials == []
    denied = FileTools(ws, ["src", "public"], edit_only=["src/parts/seat.py"])
    with pytest.raises(PathDenied):
        denied.write_file("src/model.py", "# hijack\n")


def test_no_scope_means_the_old_behaviour(scoped_ws):
    """Baseline sessions and unscoped refines (target 'overall') keep the whole tree."""
    ft = FileTools(scoped_ws, ["src", "public"])
    assert ft.edit_only is None
    assert not ft.write_file("src/parts/leg.py", "# fine\n").is_error


def test_scope_never_widens_the_write_roots(scoped_ws):
    """edit_only narrows; it must not let a listed path escape src/ or public/."""
    ws = scoped_ws
    (ws.root / "artifacts").mkdir(exist_ok=True)
    ft = FileTools(ws, ["src", "public"], edit_only=["artifacts/x.txt"])
    with pytest.raises(PathDenied, match="only allowed under"):
        ft.write_file("artifacts/x.txt", "nope")
