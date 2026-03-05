from __future__ import annotations

from typing import Any, Literal, TypedDict


Phase = Literal[
    "INIT",
    "WAIT_PLAN_APPROVAL",
    "TASK_READY",
    "WORKER_RUNNING",
    "REVIEW_RUNNING",
    "WAIT_TASK_APPROVAL",
    "STOPPED",
    "COMPLETED",
    "FAILED",
]


class ApprovalState(TypedDict):
    required: bool
    kind: str | None
    reason: str | None
    details_paths: list[str]


class ActiveTaskState(TypedDict):
    task_id: str
    revision: int
    created_at: str


class FlowState(TypedDict):
    run_id: str
    phase: Phase
    active_task: ActiveTaskState | None
    iteration: int
    stall_count: int
    attempts: dict[str, int]
    last_good_commit: str | None
    approval: ApprovalState
    history: list[dict[str, Any]]
    # Legacy fields retained for backward compatibility with older state snapshots.
    current_task_id: str | None
    queue: list[str]
