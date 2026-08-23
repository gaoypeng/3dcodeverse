from codeverse.judges.pairwise import PairwiseJudge
from codeverse.models.base import ModelError
from tests.judges.conftest import FakeChatModel, image_parts, make_renders, make_spec


def _reply(w, conf=0.8):
    return {"winner": w, "confidence": conf, "reasons": [f"{w} has four legs"], "criteria_won": [{"criterion": "intent_fidelity", "winner": w}]}


def test_agreement_across_swap(tmp_path, cache_dir):
    ra, rb = make_renders(tmp_path / "a"), make_renders(tmp_path / "b")
    # ordering 1: A=ra,B=rb → "A" wins; ordering 2 (swapped): A=rb,B=ra → "B" wins → both say ra
    model = FakeChatModel([_reply("A", 0.9), _reply("B", 0.7)])
    res = PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res.winner == "a" and res.confidence == 0.8 and len(res.orderings) == 2
    assert res.usage.cost_usd > 0
    labels = [p.label for p in image_parts(model.requests[0])]
    assert labels[0].startswith("CANDIDATE A — contact sheet") and labels[1].startswith("CANDIDATE B")


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
    assert len(image_parts(model.requests[0])) == 8  # 4 views per side without a sheet
    model2 = FakeChatModel(default=ModelError("down", retryable=True))
    res2 = PairwiseJudge("fake:fake-1", chat_model=model2, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res2.winner == "tie" and res2.confidence == 0.0 and "ModelError" in res2.error


def test_single_ordering_success_halves_confidence(tmp_path, cache_dir):
    ra, rb = make_renders(tmp_path / "a"), make_renders(tmp_path / "b")
    model = FakeChatModel([ModelError("x", retryable=True), _reply("A", 0.8)])  # swapped ordering: A=rb → b wins
    res = PairwiseJudge("fake:fake-1", chat_model=model, cache_dir=cache_dir).compare(make_spec(), ra, rb)
    assert res.winner == "b" and res.confidence == 0.4


def test_unswap_reasons():
    from codeverse.judges.pairwise import _unswap_text
    assert _unswap_text("Candidate A beats candidate B", True) == "Candidate B beats candidate A"
    assert _unswap_text("Candidate A beats candidate B", False) == "Candidate A beats candidate B"
