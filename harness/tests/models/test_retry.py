"""with_retries / backoff_delay."""

from __future__ import annotations

import pytest

from codeverse.models.retry import backoff_delay, with_retries


def test_backoff_grows_and_caps():
    d = [backoff_delay(i, base_delay=1.0, max_delay=8.0, jitter=False) for i in range(1, 7)]
    assert d == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    j = backoff_delay(3, base_delay=1.0, max_delay=8.0)
    assert 2.0 <= j <= 4.0


def test_retries_until_success_and_reports():
    calls: list[int] = []
    slept: list[float] = []
    seen: list[tuple[int, float]] = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TimeoutError("flaky")
        return "ok"

    out = with_retries(
        fn,
        is_retryable=lambda e: isinstance(e, TimeoutError),
        attempts=5,
        base_delay=0.5,
        max_delay=2.0,
        on_retry=lambda n, e, d: seen.append((n, d)),
        sleep=slept.append,
        jitter=False,
    )
    assert out == "ok" and len(calls) == 3
    assert slept == [0.5, 1.0]
    assert [n for n, _ in seen] == [1, 2]


def test_non_retryable_raises_immediately():
    n = 0

    def fn():
        nonlocal n
        n += 1
        raise ValueError("bad")

    with pytest.raises(ValueError):
        with_retries(fn, is_retryable=lambda e: False, attempts=5, sleep=lambda s: None)
    assert n == 1


def test_exhausted_raises_last():
    def fn():
        raise TimeoutError("always")

    with pytest.raises(TimeoutError):
        with_retries(fn, is_retryable=lambda e: True, attempts=3, sleep=lambda s: None)
