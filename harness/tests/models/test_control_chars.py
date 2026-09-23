"""Control characters in model output: a 2026-08-24 plan carried \\u0000 where it meant ×/±,
and the NUL killed a codex cell at Popen.  strip_control_chars + the argv backstop."""

from __future__ import annotations

import json

from codeverse3d.models.schema_utils import parse_json_lenient, strip_control_chars

THE_REAL_PAYLOAD = (
    '{"summary": "Overall envelope is 0.078 \\u0000 0.300 \\u0000 0.240 m",'
    ' "acceptance": [{"text": "height is 0.240 m \\u0000 0.005 m"}]}'
)


def test_the_plan_that_killed_a_cell_now_parses_clean():
    out = parse_json_lenient(THE_REAL_PAYLOAD)
    blob = json.dumps(out)
    assert "\x00" not in blob and "\\u0000" not in blob
    # a space, never deletion: "0.078" and "0.300" must not fuse into "0.0780.300"
    assert out["summary"] == "Overall envelope is 0.078   0.300   0.240 m"
    assert out["acceptance"][0]["text"] == "height is 0.240 m   0.005 m"


def test_strip_control_chars_recurses_and_preserves_safe_text():
    dirty = {"a": "x\x00y", "b": ["p\x07q", {"c": "r\x1bs"}], "n": 3, "ok": None, "t": True}
    assert strip_control_chars(dirty) == {
        "a": "x y",
        "b": ["p q", {"c": "r s"}],
        "n": 3,
        "ok": None,
        "t": True,
    }
    keep = "def f():\n\tx = 1\r\n"
    assert strip_control_chars(keep) == keep
    text = "0.078 × 0.300 m ± 0.005 — café 日本語 🔧"
    assert strip_control_chars(text) == text


def test_the_subprocess_backstop_names_the_offender(tmp_path):
    import pytest

    from codeverse3d.agents.cli_common import run_with_watchdog

    poisoned = "harness_instructions: envelope is 0.078 \x00 0.300 m"
    with pytest.raises(ValueError, match=r"argv\[2\] contains a NUL at offset \d+"):
        run_with_watchdog(["echo", "ok", poisoned], cwd=tmp_path, env=None, soft_timeout_s=5)


