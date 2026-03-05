from __future__ import annotations

import json

import pytest

from jurisparse_un.stages._shared import HTTPResponse
from jurisparse_un.stages.resolve import format_priority, run


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_resolve_selects_deterministic_format_priority() -> None:
    payload = run(
        source_items=[
            {
                "doc_symbol": "A/HRC/1",
                "download_options": [
                    {"format": "html", "url": "https://example.test/a.html"},
                    {"format": "docx", "url": "https://example.test/a.docx"},
                    {"format": "pdf", "url": "https://example.test/a.pdf"},
                ],
            }
        ]
    )

    resolved = payload["resolved_items"][0]
    assert payload["format_priority"] == list(format_priority)
    assert resolved["selected_download"] == {
        "format": "pdf",
        "url": "https://example.test/a.pdf",
    }


def test_resolve_retries_with_backoff_and_rate_limit_offline() -> None:
    clock = _FakeClock()
    outcomes: list[HTTPResponse | Exception] = [
        TimeoutError("resolver timeout"),
        HTTPResponse(
            status_code=200,
            content=json.dumps(
                [{"format": "html", "url": "https://example.test/first.html"}]
            ).encode("utf-8"),
        ),
        HTTPResponse(
            status_code=200,
            content=json.dumps(
                [{"format": "pdf", "url": "https://example.test/second.pdf"}]
            ).encode("utf-8"),
        ),
    ]
    calls: list[tuple[str, float]] = []

    def request_fn(*, url: str, timeout_sec: float) -> HTTPResponse:
        calls.append((url, timeout_sec))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    payload = run(
        source_items=[
            {"doc_symbol": "DOC-1", "download_page_url": "https://example.test/one"},
            {"doc_symbol": "DOC-2", "download_page_url": "https://example.test/two"},
        ],
        request_fn=request_fn,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    assert [timeout for _, timeout in calls] == [10.0, 10.0, 10.0]
    first = payload["resolved_items"][0]
    assert first["attempts"] == 2
    assert first["retry_backoff"] == [0.5]
    assert first["rate_limit_wait"] == []
    assert first["selected_download"]["format"] == "html"
    assert first["errors"] == ["TimeoutError: resolver timeout"]

    second = payload["resolved_items"][1]
    assert second["attempts"] == 1
    assert second["retry_backoff"] == []
    assert second["rate_limit_wait"] == [pytest.approx(0.2)]
    assert second["selected_download"]["format"] == "pdf"
    assert clock.sleeps == [0.5, pytest.approx(0.2)]
