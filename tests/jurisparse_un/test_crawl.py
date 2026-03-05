from __future__ import annotations

import hashlib

import pytest

from jurisparse_un.stages._shared import HTTPResponse
from jurisparse_un.stages.crawl import run


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_crawl_run_retries_with_backoff_and_rate_limit_offline() -> None:
    clock = _FakeClock()
    outcomes: list[HTTPResponse | Exception] = [
        TimeoutError("temporary timeout"),
        HTTPResponse(status_code=200, content=b"first-ok"),
        HTTPResponse(status_code=200, content=b"second-ok"),
    ]
    calls: list[tuple[str, float]] = []

    def request_fn(*, url: str, timeout_sec: float) -> HTTPResponse:
        calls.append((url, timeout_sec))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    payload = run(
        crawl_urls=["https://example.test/1", "https://example.test/2"],
        request_fn=request_fn,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    results = payload["crawl_results"]
    assert payload["request_policy"]["default_retries"] == 2
    assert [timeout for _, timeout in calls] == [10.0, 10.0, 10.0]

    first = results[0]
    assert first["attempts"] == 2
    assert first["retry_backoff"] == [0.5]
    assert first["rate_limit_wait"] == []
    assert first["errors"] == ["TimeoutError: temporary timeout"]
    assert first["body_sha256"] == hashlib.sha256(b"first-ok").hexdigest()

    second = results[1]
    assert second["attempts"] == 1
    assert second["retry_backoff"] == []
    assert second["rate_limit_wait"] == [pytest.approx(0.2)]
    assert second["body_sha256"] == hashlib.sha256(b"second-ok").hexdigest()
    assert clock.sleeps == [0.5, pytest.approx(0.2)]
