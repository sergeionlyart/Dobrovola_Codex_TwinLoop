"""Shared helpers for stage scaffold contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPMessage
from typing import Any, Callable, Protocol
from urllib import request


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)


class HTTPRequester(Protocol):
    def __call__(self, *, url: str, timeout_sec: float) -> HTTPResponse: ...


SleepFn = Callable[[float], None]
MonotonicFn = Callable[[], float]


@dataclass(frozen=True)
class RequestPolicy:
    timeout_sec: float
    retries: int
    backoff_base_sec: float
    rate_limit_rps: float


@dataclass
class RequestRateLimitState:
    last_request_monotonic: float | None = None


@dataclass(frozen=True)
class RequestExecution:
    response: HTTPResponse | None
    attempts: int
    errors: list[str]
    backoff_sleeps: list[float]
    rate_limit_sleeps: list[float]


def retry_backoff(*, attempt: int, backoff_base_sec: float) -> float:
    safe_attempt = max(1, attempt)
    return backoff_base_sec * (2 ** (safe_attempt - 1))


def rate_limit_wait(
    *,
    last_request_monotonic: float | None,
    now_monotonic: float,
    rate_limit_rps: float,
) -> float:
    if last_request_monotonic is None or rate_limit_rps <= 0:
        return 0.0
    min_interval = 1.0 / rate_limit_rps
    elapsed = now_monotonic - last_request_monotonic
    return max(0.0, min_interval - elapsed)


def default_http_get(*, url: str, timeout_sec: float) -> HTTPResponse:
    with request.urlopen(url, timeout=timeout_sec) as http_response:
        status_code = int(getattr(http_response, "status", http_response.getcode()))
        headers = _headers_to_dict(http_response.headers)
        return HTTPResponse(
            status_code=status_code,
            content=http_response.read(),
            headers=headers,
        )


def _headers_to_dict(headers: HTTPMessage) -> dict[str, str]:
    return {key: value for key, value in headers.items()}


def perform_request_with_policy(
    *,
    url: str,
    request_fn: HTTPRequester,
    policy: RequestPolicy,
    rate_limit_state: RequestRateLimitState | None,
    sleep_fn: SleepFn,
    monotonic_fn: MonotonicFn,
) -> RequestExecution:
    attempts = 0
    errors: list[str] = []
    backoff_sleeps: list[float] = []
    rate_limit_sleeps: list[float] = []
    response: HTTPResponse | None = None
    state = rate_limit_state or RequestRateLimitState()
    max_attempts = policy.retries + 1

    while attempts < max_attempts:
        wait_seconds = rate_limit_wait(
            last_request_monotonic=state.last_request_monotonic,
            now_monotonic=monotonic_fn(),
            rate_limit_rps=policy.rate_limit_rps,
        )
        if wait_seconds > 0:
            sleep_fn(wait_seconds)
            rate_limit_sleeps.append(wait_seconds)

        attempts += 1
        try:
            candidate = request_fn(url=url, timeout_sec=policy.timeout_sec)
        except Exception as exc:  # noqa: BLE001
            state.last_request_monotonic = monotonic_fn()
            errors.append(_format_error(exc))
            if attempts >= max_attempts:
                break
            backoff_seconds = retry_backoff(
                attempt=attempts,
                backoff_base_sec=policy.backoff_base_sec,
            )
            sleep_fn(backoff_seconds)
            backoff_sleeps.append(backoff_seconds)
            continue

        state.last_request_monotonic = monotonic_fn()
        response = candidate
        if candidate.status_code == 429 or 500 <= candidate.status_code < 600:
            errors.append(f"HTTP {candidate.status_code}")
            if attempts >= max_attempts:
                break
            backoff_seconds = retry_backoff(
                attempt=attempts,
                backoff_base_sec=policy.backoff_base_sec,
            )
            sleep_fn(backoff_seconds)
            backoff_sleeps.append(backoff_seconds)
            continue
        break

    return RequestExecution(
        response=response,
        attempts=attempts,
        errors=errors,
        backoff_sleeps=backoff_sleeps,
        rate_limit_sleeps=rate_limit_sleeps,
    )


def _format_error(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return f"{exc.__class__.__name__}: {message}"


def make_stage_payload(
    *,
    stage: str,
    config: str | None,
    run_id: str | None,
    dry_run: bool,
    from_manifest: str | None,
    from_db: bool,
    notes: list[str],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "config": config,
        "run_id": run_id,
        "dry_run": dry_run,
        "from_manifest": from_manifest,
        "from_db": from_db,
        "notes": notes,
    }
