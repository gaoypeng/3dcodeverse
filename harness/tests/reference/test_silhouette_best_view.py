"""``best_view_match``: score the render view the reference was actually shot from."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from codeverse3d.contracts.artifacts import RenderSet, RenderView, Severity
from codeverse3d.contracts.spec import ReferenceImage
from codeverse3d.judges.base import JudgeInput
from codeverse3d.judges.vlm_judge import ReferenceJudge
from codeverse3d.spatial.silhouette import best_view_match, compare_silhouette
from codeverse3d.tracks.static_object import silhouette_gate
from tests.judges.conftest import FakeChatModel
from tests.reference.conftest import make_spec


def _shape(path: Path, box, bg=(240, 240, 240), fg=(30, 30, 30)) -> Path:
    im = Image.new("RGB", (256, 256), bg)
    ImageDraw.Draw(im).rectangle(box, fill=fg)
    im.save(path)
    return path


def _views(tmp: Path) -> list[RenderView]:
    tmp.mkdir(parents=True, exist_ok=True)
    boxes = {"front": (40, 40, 90, 220),          # tall and narrow — wrong
             "front_right_high": (60, 60, 200, 200),  # square
             "right": (30, 90, 230, 170)}           # wide and flat — matches the reference
    return [RenderView(name=n, path=str(_shape(tmp / f"{n}.png", b)), width=256, height=256)
            for n, b in boxes.items()]


def test_best_view_picks_the_matching_camera_and_writes_the_diff(tmp_path: Path) -> None:
    views = _views(tmp_path / "r")
    ref = _shape(tmp_path / "ref.png", (20, 95, 236, 165))
    res = best_view_match(views, ref, diff_png=tmp_path / "d.png")
    assert (tmp_path / "d.png").is_file() and res["view"] == "right"


def test_the_gate_and_the_judge_read_one_measurement(tmp_path: Path) -> None:
    """N51: the gate and the judge score the same best-matching view (gate said IoU 0.26 on `front`, judge 0.995)."""
    views = _views(tmp_path / "r")
    ref = _shape(tmp_path / "ref.png", (20, 95, 236, 165))
    spec = make_spec(references=[ReferenceImage(path=str(ref), role="target")])
    ctx = SimpleNamespace(spec=spec, services=SimpleNamespace(silhouette=compare_silhouette))
    (finding,) = silhouette_gate(ctx, RenderSet(views=views)).findings
    assert finding.data["view"] == "right" and finding.data["iou"] > 0.8 and finding.severity == Severity.INFO
    jc = ReferenceJudge("fake:fake-1", chat_model=FakeChatModel([])).context(JudgeInput(spec=spec, renders=RenderSet(views=views)))
    assert f"right render vs reference ref.png: IoU {finding.data['iou']:.3f}" in jc.extra_text
