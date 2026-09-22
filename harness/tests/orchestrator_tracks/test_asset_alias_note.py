"""The merge map the asset stage USED is what the zones are told (2026-09-07).

`run_asset_stage` folds near-identical props under a cap that depends on the soft budget at
the time (`MAX_ASSETS` or `DEGRADED_MAX_ASSETS`); `prepare` used to recompute the map from
the plan with `MAX_ASSETS` alone, so a degraded run told the zones merged assets were
"NOT AVAILABLE" while working shims existed.  The stage writes the note it used, and
`prepare` reads it back.
"""
from __future__ import annotations

from codeverse3d.tracks.scene_assets import read_dedupe_note, write_dedupe_note


def test_the_note_round_trips_and_an_empty_map_is_still_a_note(tmp_ws):
    assert read_dedupe_note(tmp_ws) is None, "no stage has run"
    write_dedupe_note(tmp_ws, {"SteppingStone": "PondRock"})
    assert read_dedupe_note(tmp_ws) == {"SteppingStone": "PondRock"}
    write_dedupe_note(tmp_ws, {})
    assert read_dedupe_note(tmp_ws) == {}, "a stage that merged nothing says so (not None)"
