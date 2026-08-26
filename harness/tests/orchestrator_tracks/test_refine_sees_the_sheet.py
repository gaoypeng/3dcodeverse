"""The refine session gets the contact sheet the judge scored, not just the prose about it."""

from __future__ import annotations

from types import SimpleNamespace

from codeverse.tracks.prompting import judged_sheet


def test_the_judged_sheet_becomes_one_labelled_image(tmp_path):
    png = tmp_path / "sheet.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    last = SimpleNamespace(index=2, renders=SimpleNamespace(contact_sheet=str(png)))
    imgs = judged_sheet(last)
    assert len(imgs) == 1 and imgs[0].path == str(png)
    assert "round 2" in imgs[0].label and "judge" in imgs[0].label


def test_no_sheet_means_no_image_not_an_error(tmp_path):
    assert judged_sheet(SimpleNamespace(index=0, renders=None)) == []
    assert judged_sheet(SimpleNamespace(index=0, renders=SimpleNamespace(contact_sheet=""))) == []
    assert judged_sheet(SimpleNamespace(index=0, renders=SimpleNamespace(contact_sheet=str(tmp_path / "gone.png")))) == []
