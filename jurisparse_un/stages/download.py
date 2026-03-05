"""download stage contract scaffold."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Sequence

from jurisparse_un.models.ids import make_artifact_id
from jurisparse_un.storage.contracts import build_artifact_object_path
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
        gcs_metadata = _build_gcs_metadata(
            item=item,
            kind=kind,
            artifact_sha256=artifact_sha256,
        )
        downloaded_items.append(
            {
                "doc_version_id": doc_version_id or None,
                "url": url or None,
                "kind": kind,
                "artifact_sha256": artifact_sha256,
                "artifact_id": artifact_id,
                **gcs_metadata,
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


def _build_gcs_metadata(
    *,
    item: dict[str, Any],
    kind: str,
    artifact_sha256: str | None,
) -> dict[str, str | None]:
    if artifact_sha256 is None:
        return {
            "storage_backend": None,
            "gcs_bucket": None,
            "gcs_object_path": None,
            "gcs_uri": None,
        }

    provider = _token_or_default(item.get("provider"), default="tbinternet")
    doc_symbol = _token_or_default(item.get("doc_symbol"), default="unknown-doc")
    language = _token_or_default(item.get("language"), default="en")
    extension = _artifact_extension(item=item, kind=kind)
    gcs_object_path = build_artifact_object_path(
        provider,
        doc_symbol,
        language,
        kind,
        artifact_sha256,
        extension,
    )
    gcs_bucket = _token_or_default(item.get("gcs_bucket"), default="jurisparse-artifacts")
    return {
        "storage_backend": "gcs",
        "gcs_bucket": gcs_bucket,
        "gcs_object_path": gcs_object_path,
        "gcs_uri": f"gs://{gcs_bucket}/{gcs_object_path}",
    }


def _artifact_extension(*, item: dict[str, Any], kind: str) -> str:
    extension = _token_or_default(item.get("extension"), default="").lstrip(".")
    if extension:
        return extension
    if kind in {"pdf", "docx", "html"}:
        return kind
    return "bin"


def _token_or_default(value: Any, *, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip().lower()
    return text if text else default
