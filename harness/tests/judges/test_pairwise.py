import pytest

from codeverse3d.judges.pairwise import PairwiseJudge
from codeverse3d.models.base import ModelError
from tests.judges.conftest import FakeChatModel, image_parts, make_renders, make_spec


def _reply(w, conf=0.8):
    return {"winner": w, "confidence": conf, "reasons": [f"{w} has four legs"], "criteria_won": [{"criterion": "intent_fidelity", "winner": w}]}


def test_agreement_across_swap(tmp_path, cache_dir):
    ra, rb = make_renders(tmp_path / "a"), make_renders(tmp_path / "b")
    # ordering 1: A=ra,B=rb → "A" wins; ordering 2 (swapped): A=rb,B=ra → "B" wins → both say ra
    model = FakeChatModel(by_label={":fwd": [_reply("A", 0.9)], ":swap": [_reply("B", 0.7)]})
    res = PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res.winner == "a" and res.confidence == 0.8
    # the two orderings run concurrently but land in (fwd, swap) order; usage sums across both
    assert [(o["swapped"], o["confidence"]) for o in res.orderings] == [(False, 0.9), (True, 0.7)]
    assert res.usage.cost_usd == pytest.approx(0.002) and res.usage.input_tokens == 2000
    reqs = {r.label: r for r in model.requests}  # orderings run in parallel: look up by label
    labels = [p.label for p in image_parts(reqs["pairwise:fwd"])]
    assert len(labels) == 2  # one 2×2 montage per side
    assert labels[0].startswith("CANDIDATE A — MONTAGE 1/1 — SHADED views: top-left = front_right_high") and labels[1].startswith("CANDIDATE B — MONTAGE")
    # swapped ordering puts rb first as "A"
    labels2 = [p.label for p in image_parts(reqs["pairwise:swap"])]
    assert labels2[0].startswith("CANDIDATE A — MONTAGE")


def test_disagreement_is_tie(tmp_path, cache_dir):
    ra, rb = make_renders(tmp_path / "a"), make_renders(tmp_path / "b")
    model = FakeChatModel([_reply("A", 0.9), _reply("A", 0.9)])  # position bias: always first
    res = PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res.winner == "tie" and res.confidence <= 0.4 and res.error == "orderings disagree"


def test_tie_and_errors(tmp_path, cache_dir):
    ra, rb = make_renders(tmp_path / "a", sheet=False), make_renders(tmp_path / "b", sheet=False)
    model = FakeChatModel([_reply("tie", 0.6), _reply("tie", 0.6)])
    res = PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res.winner == "tie" and res.confidence == 0.6
    assert len(image_parts(model.requests[0])) == 2  # still one montage per side without a sheet
    model2 = FakeChatModel(default=ModelError("down", retryable=True))
    res2 = PairwiseJudge("fake:fake-1", chat_model=model2, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res2.winner == "tie" and res2.confidence == 0.0 and "ModelError" in res2.error


def test_single_ordering_success_halves_confidence(tmp_path, cache_dir):
    ra, rb = make_renders(tmp_path / "a"), make_renders(tmp_path / "b")
    model = FakeChatModel(by_label={":fwd": [ModelError("x", retryable=True)],
                                    ":swap": [_reply("A", 0.8)]})  # swapped ordering: A=rb → b wins
    res = PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res.winner == "b" and res.confidence == 0.4


def test_unswap_reasons():
    from codeverse3d.judges.pairwise import _unswap_text
    assert _unswap_text("Candidate A beats candidate B", True) == "Candidate B beats candidate A"
    assert _unswap_text("Candidate A beats candidate B", False) == "Candidate A beats candidate B"


def test_a_scene_pair_is_ranked_as_a_scene_from_the_views_the_judge_saw(tmp_path, cache_dir):
    """Every candidate used to be ranked as an OBJECT from its whole render set: a scene's
    own camera sank behind the overview rig, and a view the verdict judge never saw
    (``judge=False``) could fill a montage slot."""
    from codeverse3d.contracts.artifacts import RenderSet, RenderView
    from codeverse3d.contracts.common import Language, Track
    from tests.judges.conftest import draw_chair

    def scene_renders(d):
        d.mkdir(parents=True)
        names = [("overview_front_right", True), ("overview_back_left", True), ("overview_top", True),
                 ("eye_front", True), ("unjudged_extra", False), ("Establishing", True)]
        return RenderSet(views=[RenderView(name=n, path=str(draw_chair(d / f"{n}.png")), judge=j)
                                for n, j in names], renderer="fake")

    model = FakeChatModel(by_label={":fwd": [_reply("A")], ":swap": [_reply("B")]})
    spec = make_spec(track=Track.SCENE, language=Language.SCENE_THREEJS)
    PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(
        spec, scene_renders(tmp_path / "a"), scene_renders(tmp_path / "b"))
    labels = [p.label for p in image_parts(next(r for r in model.requests if r.label == "pairwise:fwd"))]
    assert "top-left = Establishing" in labels[0]
    assert not any("unjudged_extra" in lbl for lbl in labels)
