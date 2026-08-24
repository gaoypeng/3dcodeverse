"""Provider health probe (codeverse/models/health.py)."""

from __future__ import annotations

from codeverse.models.health import DEFAULT_MIN_OK, Health


def test_a_model_answering_on_most_keys_is_healthy():
    assert Health("m", n_ok=3, n_tried=4).ok
    assert Health("m", n_ok=2, n_tried=4).ok, f"the bar is {DEFAULT_MIN_OK:.0%} of sampled keys"


def test_a_model_answering_on_almost_nothing_is_not():
    assert not Health("m", n_ok=1, n_tried=4).ok
    assert not Health("m", n_ok=0, n_tried=4).ok


def test_no_keys_probed_is_not_reported_as_healthy():
    """An empty probe must never read as a green light."""
    assert not Health("m", n_ok=0, n_tried=0).ok


def test_the_message_carries_the_provider_reason():
    h = Health("gemini:gemini-3.7-flash", n_ok=0, n_tried=4,
               reasons=("ModelError: Gemini API error 504: Deadline expired",))
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
    """The repo path itself contains "3dcodeverse", so a substring test over the whole
    command line counts every shell that merely cd'd into the tree — the first version
    of this reported 4 siblings on an idle machine."""
    from codeverse.models.health import _is_harness_argv

    assert _is_harness_argv(["python", "-m", "codeverse.cli.main", "bench", "run"])
    assert _is_harness_argv(["python", "bench/compare_backends.py", "--arms", "x"])
    assert _is_harness_argv(["/home/u/.local/bin/3dcv", "doctor"])
    assert _is_harness_argv(["/usr/bin/python3", "/x/y/run_bench.py"])
    # the false positives
    assert not _is_harness_argv(["/bin/bash", "-c", "cd /home/u/3dcodeverse/harness && ls"])
    assert not _is_harness_argv(["vim", "/home/u/3dcodeverse/harness/codeverse/cli/main.py"])
    assert not _is_harness_argv(["python", "-m", "pytest", "tests/"])
    assert not _is_harness_argv([])


def test_sibling_count_never_raises(monkeypatch):
    """A wrong number here must never block a run, so /proc trouble returns 0."""
    from pathlib import Path

    import codeverse.models.health as health

    def boom(_self):
        raise OSError("no /proc on this platform")

    monkeypatch.setattr(Path, "iterdir", boom)
    assert health.sibling_processes() == 0
