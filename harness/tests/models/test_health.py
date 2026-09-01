"""Provider health probe (codeverse/models/health.py)."""

from __future__ import annotations

from codeverse.models.health import DEFAULT_MIN_OK, Health


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
    from codeverse.models.health import PROBE_TOKENS, _probe_prompt

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
    import codeverse.models.health as health

    class Boom:
        def generate(self, _req):
            raise RuntimeError("provider on fire")

    monkeypatch.setattr(health, "_bare_model", lambda *a, **k: Boom())
    h = health.probe("gemini:gemini-3.7-flash", sample=3, timeout_s=1)
    assert not h.ok and h.n_ok == 0 and h.n_tried == 3
    assert h.reasons and "provider on fire" in h.reasons[0]


# --------------------------------------------------------------------------- siblings
def test_sibling_detection_matches_arguments_not_the_repo_path():
    from codeverse.models.health import _is_harness_argv

    harness_commands = (
        ["python", "-m", "codeverse.cli.main", "bench", "run"],
        ["python", "bench/compare_backends.py", "--arms", "x"],
        ["/home/u/.local/bin/3dcv", "doctor"],
        ["/usr/bin/python3", "/x/y/run_bench.py"],
        ["/home/u/miniconda3/bin/python3.13", "/home/u/miniconda3/bin/3dcv", "make"],
        ["/usr/bin/python3", "/usr/local/bin/3dcodeverse", "doctor"],
        ["python", "-m", "bench.ab_plan", "--prompts", "x"],
        ["python", "-m", "bench.compare_backends", "--arms", "x"],
        ["python", "-m", "bench.run_bench", "b.yaml"],
    )
    other_commands = (
        ["/bin/bash", "-c", "cd /home/u/3dcodeverse/harness && ls"],
        ["vim", "/home/u/3dcodeverse/harness/codeverse/cli/main.py"],
        ["python", "-m", "pytest", "tests/"],
        [],
        ["ls", "-la", "/home/u/3dcodeverse"],
        ["tar", "czf", "b.tgz", "/home/u/.local/bin/3dcv"],
        ["python", "-c", "print(1)", "/home/u/.local/bin/3dcv"],
    )
    assert all(map(_is_harness_argv, harness_commands))
    assert not any(map(_is_harness_argv, other_commands))


def test_probe_model_treats_a_503_as_final(monkeypatch):
    """ "No retries" must include the storm branch.  rotate_with_retries' capacity-storm
    branch does NOT consume max_attempts, so max_attempts=1 alone still retried a 503
    up to 60 times (bounded only by the 900 s deadline) — a "30-second" probe that could
    take 15 minutes.  Observed 2026-08-24 in the parked compare_v2 preflight log."""
    import codeverse.models.gemini as gm
    from codeverse.models.health import _bare_model

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

    from codeverse.contracts.chat import ChatMessage, ChatRequest

    with contextlib.suppress(Exception):  # the fake returns a non-response; only the kwargs matter
        m.generate(ChatRequest(messages=[ChatMessage.user("pong")], max_output_tokens=8))
    assert captured.get("storm_attempts") == 0, captured.keys()
    assert captured.get("max_attempts") == 1


# --------------------------------------------------------------------------- pool budget
def _fake_proc(monkeypatch, procs):
    """procs: {pid: (argv list, env dict)} — a fake /proc for pool_budget()."""
    import os
    from pathlib import Path

    import codeverse.models.health as health

    me = os.getpid()

    class Entry:
        def __init__(self, pid):
            self.name = str(pid)
            self._pid = pid

        def __truediv__(self, leaf):
            argv, env = procs[self._pid]
            data = {
                "cmdline": "\0".join(argv).encode() + b"\0",
                "environ": b"\0".join(f"{k}={v}".encode() for k, v in env.items()),
            }[leaf]
            return _Blob(data)

    class _Blob:
        def __init__(self, data):
            self._data = data

        def read_bytes(self):
            return self._data

    monkeypatch.setattr(Path, "iterdir", lambda self: [Entry(p) for p in procs if p != me])
    return health


def test_pool_budget_accounts_for_caps_defaults_and_unlimited_siblings(monkeypatch):
    health = _fake_proc(
        monkeypatch,
        {
            101: (
                ["python", "-m", "codeverse.cli.main", "bench", "run"],
                {"CV3D_MAX_IN_FLIGHT": "16"},
            ),
            102: (
                ["python", "bench/compare_backends.py", "--arms", "x"],
                {"CV3D_RATE__MAX_IN_FLIGHT": "16"},
            ),
            103: (
                ["/bin/bash", "-c", "cd /home/u/3dcodeverse && sleep 1"],
                {},
            ),  # not a harness process
        },
    )
    pb = health.pool_budget()
    assert (pb.siblings, pb.used, pb.headroom) == (2, 32, 32), str(pb)
    assert pb.fits(16) and pb.fits(32) and not pb.fits(33) and not pb.fits(64)

    health = _fake_proc(
        monkeypatch, {201: (["python", "-m", "codeverse.cli.main", "make", "a chair"], {})}
    )
    pb = health.pool_budget()
    assert pb.used == 64 and pb.headroom == 0 and not pb.fits(1)

    health = _fake_proc(
        monkeypatch,
        {
            401: (
                ["python", "-m", "codeverse.cli.main", "bench", "run"],
                {"CV3D_MAX_IN_FLIGHT": "0"},
            ),
        },
    )
    pb = health.pool_budget()
    assert (pb.siblings, pb.used, pb.headroom) == (1, 64, 0)

    health = _fake_proc(
        monkeypatch,
        {
            501: (
                ["python", "-m", "codeverse.cli.main", "bench", "run"],
                {"CV3D_MAX_IN_FLIGHT": "16"},
            ),
            502: (["python", "-m", "bench.compare_backends"], {"CV3D_MAX_IN_FLIGHT": "-8"}),
        },
    )
    pb = health.pool_budget()
    assert pb.used == 16 + health.POOL_KNEE and pb.headroom == 0


def test_an_ab_plan_driver_is_not_charged_for_its_children(monkeypatch):
    """The driver spawns one capped cell child per arm and makes no model calls of its own.

    It sets no cap on itself (``--max-in-flight`` is the CHILD cap), so charging it would
    book the 64 default — the whole knee — on top of the children that actually hold the
    traffic, and every sibling would refuse to launch."""
    health = _fake_proc(
        monkeypatch,
        {
            301: (
                ["python", "-m", "bench.ab_plan", "--prompts", "p.yaml", "--max-in-flight", "16"],
                {},
            ),
            302: (
                ["python", "-m", "bench.ab_plan", "cell", "--arm", "control"],
                {"CV3D_MAX_IN_FLIGHT": "16"},
            ),
            303: (
                ["python", "-m", "bench.ab_plan", "cell", "--arm", "variant"],
                {"CV3D_MAX_IN_FLIGHT": "16"},
            ),
        },
    )
    pb = health.pool_budget()
    assert (pb.siblings, pb.used, pb.headroom) == (3, 32, 32), str(pb)
    # the same three processes under the script spelling read identically
    health = _fake_proc(
        monkeypatch,
        {
            311: (["python", "bench/ab_plan.py", "--prompts", "p.yaml"], {}),
            312: (
                ["python", "bench/ab_plan.py", "cell", "--arm", "control"],
                {"CV3D_MAX_IN_FLIGHT": "8"},
            ),
        },
    )
    assert health.pool_budget().used == 8
    # a compare_backends driver runs its cells in THREADS, in itself: it is charged
    health = _fake_proc(
        monkeypatch,
        {321: (["python", "-m", "bench.compare_backends"], {"CV3D_MAX_IN_FLIGHT": "16"})},
    )
    assert health.pool_budget().used == 16


def test_pool_budget_never_raises(monkeypatch):
    from pathlib import Path

    import codeverse.models.health as health

    def boom(_self):
        raise OSError("no /proc")

    monkeypatch.setattr(Path, "iterdir", boom)
    pb = health.pool_budget()
    assert pb.siblings == 0 and pb.used == 0 and pb.fits(64)
