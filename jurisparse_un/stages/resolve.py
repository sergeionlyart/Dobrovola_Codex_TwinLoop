"""resolve stage contract scaffold."""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

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

format_priority = ("pdf", "docx", "html")


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
    source_items: Sequence[dict[str, Any]] | None = None,
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
        "resolve stage applies retry_backoff and rate_limit_wait request policy.",
        "resolve picks selected_download by deterministic format_priority.",
    ]
    payload = make_stage_payload(
        stage="resolve",
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
    payload["format_priority"] = list(format_priority)

    fetch = request_fn or default_http_get
    rate_limit_state = RequestRateLimitState()
    resolved_items: list[dict[str, Any]] = []

    for source_item in source_items or []:
        execution: RequestExecution | None = None
        options = _coerce_download_options(source_item.get("download_options"))
        download_page_url = str(source_item.get("download_page_url", "")).strip()
        if not options and download_page_url:
            execution = perform_request_with_policy(
                url=download_page_url,
                request_fn=fetch,
                policy=policy,
                rate_limit_state=rate_limit_state,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
            )
            if execution.response is not None:
                options = _decode_download_options(execution.response.content)

        selected = _select_preferred_download(options)
        resolved_items.append(
            {
                "doc_symbol": source_item.get("doc_symbol"),
                "download_page_url": download_page_url or None,
                "available_downloads": options,
                "selected_download": selected,
                "attempts": execution.attempts if execution else 0,
                "retry_backoff": execution.backoff_sleeps if execution else [],
                "rate_limit_wait": execution.rate_limit_sleeps if execution else [],
                "errors": execution.errors if execution else [],
            }
        )

    payload["resolved_items"] = resolved_items
    return payload


def _decode_download_options(content: bytes) -> list[dict[str, str]]:
    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    return _coerce_download_options(parsed)


def _coerce_download_options(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    options: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        format_value = str(item.get("format", "")).strip().lower()
        url_value = str(item.get("url", "")).strip()
        if not format_value or not url_value:
            continue
        options.append({"format": format_value, "url": url_value})
    return options


def _select_preferred_download(options: Sequence[dict[str, str]]) -> dict[str, str] | None:
    if not options:
        return None
    order = {name: index for index, name in enumerate(format_priority)}

    def _key(item: dict[str, str]) -> tuple[int, str]:
        name = str(item.get("format", "")).lower()
        return (order.get(name, len(order)), str(item.get("url", "")))

    selected = min(options, key=_key)
    return {"format": selected["format"], "url": selected["url"]}
