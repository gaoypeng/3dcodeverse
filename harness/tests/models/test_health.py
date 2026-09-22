"""Provider health probe (codeverse3d/models/health.py)."""

from __future__ import annotations

from codeverse3d.models.health import DEFAULT_MIN_OK, Health


def test_health_threshold_requires_a_real_success_rate():
    for n_ok, n_tried, expected in (
        (6, 6, True),
        (3, 4, True),
        (2, 4, False),
        (4, 6, False),
        (1, 4, False),
        (0, 0, False),
    ):
        assert Health("m", n_ok=n_ok, n_tried=n_tried).ok is expected, (
            f"{n_ok}/{n_tried}; threshold={DEFAULT_MIN_OK:.0%}"
        )


def test_the_probe_prompt_is_the_size_of_real_work():
    """A five-token probe answers when a 12 k-token planner call does not — that false
    green light is exactly what this gate must not give."""
    from codeverse3d.models.health import PROBE_TOKENS, _probe_prompt

    prompt = _probe_prompt()
    approx_tokens = len(prompt) // 4
    assert approx_tokens > 0.5 * PROBE_TOKENS, f"probe is only ~{approx_tokens} tokens"
    assert "pong" in prompt, (
        "the answer must still be one word, so the probe costs input not output"
    )


def test_the_message_carries_the_provider_reason():
    h = Health(
        "gemini:gemini-3.7-flash",
        n_ok=0,
        n_tried=4,
        reasons=("ModelError: Gemini API error 504: Deadline expired",),
    )
    text = str(h)
    assert "0/4" in text and "504" in text, text


def test_probe_never_raises_when_every_call_fails(monkeypatch):
    import codeverse3d.models.health as health

    class Boom:
        def generate(self, _req):
            raise RuntimeError("provider on fire")

    monkeypatch.setattr(health, "_bare_model", lambda *a, **k: Boom())
    h = health.probe("gemini:gemini-3.7-flash", sample=3, timeout_s=1)
    assert not h.ok and h.n_ok == 0 and h.n_tried == 3
    assert h.reasons and "provider on fire" in h.reasons[0]


def test_probe_model_treats_a_503_as_final(monkeypatch):
    """ "No retries" must include the storm branch.  rotate_with_retries' capacity-storm
    branch does NOT consume max_attempts, so max_attempts=1 alone still retried a 503
    up to 60 times (bounded only by the 900 s deadline) — a "30-second" probe that could
    take 15 minutes.  Observed 2026-08-24 in the parked compare_v2 preflight log."""
    import codeverse3d.models.gemini as gm
    from codeverse3d.models.health import _bare_model

    captured: dict = {}

    def fake_rotate(pool, call, **kw):
        captured.update(kw)
        return "unused"

    monkeypatch.setattr(gm, "rotate_with_retries", fake_rotate)
    # a non-live test must pass with NO credentials in the environment (a fresh clone,
    # the CI runner): _bare_model builds a real GeminiModel, which refuses to construct
    # without keys, so hand it a fake one instead of borrowing this box's (PORT-2).
    monkeypatch.setattr(gm, "_default_keys", lambda: ["fake-key-for-tests"])
    m = _bare_model("gemini:gemini-3.7-flash", 30.0)
    assert m.storm_attempts == 0 and m.max_attempts == 1
    import contextlib

    from codeverse3d.contracts.chat import ChatMessage, ChatRequest

    with contextlib.suppress(Exception):  # the fake returns a non-response; only the kwargs matter
        m.generate(ChatRequest(messages=[ChatMessage.user("pong")], max_output_tokens=8))
    assert captured.get("storm_attempts") == 0, captured.keys()
    assert captured.get("max_attempts") == 1
