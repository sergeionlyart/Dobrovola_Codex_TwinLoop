"""download stage contract scaffold."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Sequence

from jurisparse_un.models.ids import make_artifact_id
from jurisparse_un.stages._shared import (
    HTTPRequester,
    MonotonicFn,
    RequestExecution,
    RequestPolicy,
    RequestRateLimitState,
    SleepFn,
    default_http_get,
    make_stage_payload,
    perform_request_with_policy,
)


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
    download_items: Sequence[dict[str, Any]] | None = None,
    request_fn: HTTPRequester | None = None,
    sleep_fn: SleepFn = time.sleep,
    monotonic_fn: MonotonicFn = time.monotonic,
) -> dict[str, Any]:
    default_timeout_sec = 10.0
    default_retries = 2
    default_backoff_base_sec = 0.5
    default_rate_limit_rps = 5.0
    policy = RequestPolicy(
        timeout_sec=default_timeout_sec,
        retries=default_retries,
        backoff_base_sec=default_backoff_base_sec,
        rate_limit_rps=default_rate_limit_rps,
    )
    notes = [
        "download stage applies retry_backoff + rate_limit_wait request policy.",
        "download computes deterministic hashlib.sha256 artifact metadata.",
    ]
    payload = make_stage_payload(
        stage="download",
        config=config,
        run_id=run_id,
        dry_run=dry_run,
        from_manifest=from_manifest,
        from_db=from_db,
        notes=notes,
    )
    payload["request_policy"] = {
        "timeout_sec": default_timeout_sec,
        "default_retries": default_retries,
        "default_backoff_base_sec": default_backoff_base_sec,
        "default_rate_limit_rps": default_rate_limit_rps,
    }

    fetch = request_fn or default_http_get
    rate_limit_state = RequestRateLimitState()
    downloaded_items: list[dict[str, Any]] = []

    for item in download_items or []:
        execution: RequestExecution | None = None
        content = _coerce_bytes(item.get("content_bytes"))
        url = str(item.get("url", "")).strip()
        if content is None and url:
            execution = perform_request_with_policy(
                url=url,
                request_fn=fetch,
                policy=policy,
                rate_limit_state=rate_limit_state,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
            )
            if execution.response is not None:
                content = execution.response.content

        kind = _format_to_kind(str(item.get("format", "")))
        doc_version_id = str(item.get("doc_version_id", "")).strip()
        artifact_sha256 = hashlib.sha256(content).hexdigest() if content is not None else None
        artifact_id = (
            make_artifact_id(doc_version_id, kind, artifact_sha256)
            if artifact_sha256 and doc_version_id
            else None
        )
        downloaded_items.append(
            {
                "doc_version_id": doc_version_id or None,
                "url": url or None,
                "kind": kind,
                "artifact_sha256": artifact_sha256,
                "artifact_id": artifact_id,
                "attempts": execution.attempts if execution else 0,
                "retry_backoff": execution.backoff_sleeps if execution else [],
                "rate_limit_wait": execution.rate_limit_sleeps if execution else [],
                "errors": execution.errors if execution else [],
            }
        )

    payload["downloaded_items"] = downloaded_items
    return payload


def _coerce_bytes(value: Any) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return None


def _format_to_kind(format_name: str) -> str:
    normalized = format_name.strip().lower()
    if normalized in {"pdf", "docx", "html"}:
        return normalized
    return "binary"
