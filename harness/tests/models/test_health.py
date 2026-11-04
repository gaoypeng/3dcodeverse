"""Provider health probe (codeverse3d/models/health.py)."""

from __future__ import annotations


def test_probe_never_raises_when_every_call_fails(monkeypatch):
    import codeverse3d.models.health as health

    class Boom:
        def generate(self, _req):
            raise RuntimeError("provider on fire")

    monkeypatch.setattr(health, "_bare_model", lambda *a, **k: Boom())
    h = health.probe("gemini:gemini-3.8-flash", sample=3, timeout_s=1)
    assert not h.ok and h.n_ok == 0 and h.n_tried == 3
    assert h.reasons and "provider on fire" in h.reasons[0]


def test_probe_model_treats_a_503_as_final(monkeypatch, tmp_path):
    """"No retries" includes the storm branch, which max_attempts alone does not bound."""
    import codeverse3d.models.gemini as gm
    from codeverse3d import config
    from codeverse3d.contracts.chat import ChatMessage, ChatRequest, ChatResponse
    from codeverse3d.models.health import _bare_model

    # Exercise this offline without credentials or a user's config; no nested pytest needed.
    for name in ("C3D_GEMINI_API_KEYS", "GEMINI_API_KEYS", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "_USER_CONFIG", tmp_path / "config.yaml")
    monkeypatch.setattr(config, "_LEGACY_KEYS_FILE", tmp_path / "gemini_keys.env")
    monkeypatch.chdir(tmp_path)
    config.get_settings.cache_clear()
    assert not config.get_settings().gemini_api_keys

    captured: dict = {}

    def fake_rotate(pool, call, **kw):
        captured.update(kw)
        return ChatResponse(text="pong")

    monkeypatch.setattr(gm, "rotate_with_retries", fake_rotate)
    # a non-live test must pass with no credentials (PORT-2)
    monkeypatch.setattr(gm, "_default_keys", lambda: ["fake-key-for-tests"])
    m = _bare_model("gemini:gemini-3.8-flash", 30.0)
    assert m.storm_attempts == 0 and m.max_attempts == 1
    response = m.generate(ChatRequest(messages=[ChatMessage.user("pong")], max_output_tokens=8))
    assert response.text == "pong"
    assert captured.get("storm_attempts") == 0, captured.keys()
    assert captured.get("max_attempts") == 1
