from __future__ import annotations

import hashlib

import pytest

from jurisparse_un.models.ids import make_artifact_id
from jurisparse_un.storage.contracts import build_artifact_object_path
from jurisparse_un.stages._shared import HTTPResponse
from jurisparse_un.stages.download import run


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_download_hash_and_artifact_id_are_deterministic() -> None:
    payload = run(
        download_items=[
            {"doc_version_id": "docv-1", "format": "pdf", "content_bytes": b"same"},
            {"doc_version_id": "docv-1", "format": "pdf", "content_bytes": b"same"},
            {"doc_version_id": "docv-1", "format": "pdf", "content_bytes": b"other"},
        ]
    )

    first, second, third = payload["downloaded_items"]
    same_sha = hashlib.sha256(b"same").hexdigest()
    other_sha = hashlib.sha256(b"other").hexdigest()
    assert first["artifact_sha256"] == same_sha
    assert second["artifact_sha256"] == same_sha
    assert third["artifact_sha256"] == other_sha
    assert first["artifact_id"] == make_artifact_id("docv-1", "pdf", same_sha)
    assert first["artifact_id"] == second["artifact_id"]
    assert first["artifact_id"] != third["artifact_id"]


def test_download_retries_with_backoff_and_rate_limit_offline() -> None:
    clock = _FakeClock()
    outcomes: list[HTTPResponse | Exception] = [
        TimeoutError("download timeout"),
        HTTPResponse(status_code=200, content=b"file-one"),
        HTTPResponse(status_code=200, content=b"file-two"),
    ]
    calls: list[tuple[str, float]] = []

    def request_fn(*, url: str, timeout_sec: float) -> HTTPResponse:
        calls.append((url, timeout_sec))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    payload = run(
        download_items=[
            {
                "doc_version_id": "docv-2",
                "format": "pdf",
                "url": "https://example.test/file-one.pdf",
            },
            {
                "doc_version_id": "docv-3",
                "format": "docx",
                "url": "https://example.test/file-two.docx",
            },
        ],
        request_fn=request_fn,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )

    assert [timeout for _, timeout in calls] == [10.0, 10.0, 10.0]
    first = payload["downloaded_items"][0]
    assert first["attempts"] == 2
    assert first["retry_backoff"] == [0.5]
    assert first["rate_limit_wait"] == []
    assert first["errors"] == ["TimeoutError: download timeout"]
    assert first["artifact_sha256"] == hashlib.sha256(b"file-one").hexdigest()

    second = payload["downloaded_items"][1]
    assert second["attempts"] == 1
    assert second["retry_backoff"] == []
    assert second["rate_limit_wait"] == [pytest.approx(0.2)]
    assert second["artifact_sha256"] == hashlib.sha256(b"file-two").hexdigest()
    assert clock.sleeps == [0.5, pytest.approx(0.2)]


def test_download_writes_explicit_gcs_metadata_fields() -> None:
    payload = run(
        download_items=[
            {
                "doc_version_id": "docv-10",
                "provider": "TBInternet",
                "doc_symbol": "CCPR/C/1/D/10/2024",
                "language": "en",
                "format": "pdf",
                "gcs_bucket": "unit-bucket",
                "content_bytes": b"content-for-gcs",
            }
        ]
    )

    downloaded = payload["downloaded_items"][0]
    artifact_sha256 = hashlib.sha256(b"content-for-gcs").hexdigest()
    expected_object_path = build_artifact_object_path(
        "tbinternet",
        "ccpr/c/1/d/10/2024",
        "en",
        "pdf",
        artifact_sha256,
        "pdf",
    )
    assert downloaded["storage_backend"] == "gcs"
    assert downloaded["gcs_bucket"] == "unit-bucket"
    assert downloaded["gcs_object_path"] == expected_object_path
    assert downloaded["gcs_uri"] == f"gs://unit-bucket/{expected_object_path}"
