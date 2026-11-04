import pytest

from codeverse3d.judges.pairwise import PairwiseJudge
from codeverse3d.models.base import ModelError
from tests.judges.conftest import FakeChatModel, image_parts, make_renders, make_spec


def _reply(w, conf=0.8):
    return {"winner": w, "confidence": conf, "reasons": [f"{w} has four legs"], "criteria_won": [{"criterion": "intent_fidelity", "winner": w}]}


def test_the_swap_decides_the_winner_and_its_confidence(tmp_path, cache_dir):
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

    # the orderings disagree (position bias: always first) → a tie
    model = FakeChatModel([_reply("A", 0.9), _reply("A", 0.9)])
    res = PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res.winner == "tie" and res.confidence <= 0.4 and res.error == "orderings disagree"

    # one ordering fails → the other's verdict at half confidence
    model = FakeChatModel(by_label={":fwd": [ModelError("x", retryable=True)],
                                    ":swap": [_reply("A", 0.8)]})  # swapped ordering: A=rb → b wins
    res = PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res.winner == "b" and res.confidence == 0.4


