"""crawl stage contract scaffold."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Sequence

from jurisparse_un.stages._shared import (
    HTTPRequester,
    MonotonicFn,
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
    crawl_urls: Sequence[str] | None = None,
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
        "crawl stage applies timeout + bounded retries with retry_backoff.",
        "crawl stage applies request rate limit waits via rate_limit_wait.",
    ]
    payload = make_stage_payload(
        stage="crawl",
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

    urls = list(crawl_urls or [])
    fetch = request_fn or default_http_get
    rate_limit_state = RequestRateLimitState()
    crawl_results: list[dict[str, Any]] = []
    for url in urls:
        execution = perform_request_with_policy(
            url=url,
            request_fn=fetch,
            policy=policy,
            rate_limit_state=rate_limit_state,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )
        content = execution.response.content if execution.response else b""
        crawl_results.append(
            {
                "url": url,
                "ok": execution.response is not None and execution.response.status_code < 400,
                "status_code": execution.response.status_code if execution.response else None,
                "attempts": execution.attempts,
                "retry_backoff": execution.backoff_sleeps,
                "rate_limit_wait": execution.rate_limit_sleeps,
                "body_sha256": hashlib.sha256(content).hexdigest() if content else None,
                "errors": execution.errors,
            }
        )

    payload["crawl_results"] = crawl_results
    payload["items_discovered"] = len(crawl_results)
    return payload
