from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from codexflow.codex_runner import CodexExecError, run_codex_exec
from codexflow.git_ops import (
    branch_exists,
    checkout_branch,
    current_branch,
    current_commit,
    diff_summary,
    merge_ff,
    working_tree_clean,
)
from codexflow.io import read_json, write_json_atomic, write_text
from codexflow.lock import LockError, file_lock
from codexflow.models import FlowState
from codexflow.paths import FlowPaths, PromptResolutionError
from codexflow.render import (
    render_decision_markdown,
    render_plan_markdown,
    render_report_markdown,
    render_task_markdown,
)

DEFAULT_MANAGER_TIMEOUT_SEC = 600
DEFAULT_WORKER_TIMEOUT_SEC = 1800
DEFAULT_MAX_ATTEMPTS_WORKER = 2
MAX_PLAN_INVARIANT_RETRIES = 2
PRODUCT_CHECK_WORKTREE_ADD_MAX_ATTEMPTS = 2
PRODUCT_CHECK_NOTE_LIMIT = 600
PRODUCT_CHECK_WORKTREE_SNAPSHOT_LINE_LIMIT = 40
TECHSPEC_COVERAGE_TARGET_PCT = 100.0
RUNTIME_TRACKED_PATHS: tuple[str, ...] = (
    ".codexflow/reports",
    ".codexflow/approvals",
    ".codexflow/tasks",
    ".codexflow/_tmp",
    ".codexflow/_archive",
    ".codexflow/state.json",
    ".codexflow/plan.json",
    ".codexflow/plan.md",
)
PLAN_DOD_INVARIANT_RULES: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    (
        "DoD explicitly states page_index remains 1-based",
        (
            ("page_index",),
            ("1-based", "1 based", "first page = 1", "first page=1"),
        ),
    ),
    (
        "DoD explicitly states idempotency with no duplicates except ingest_runs",
        (
            ("idempotency", "idempotent"),
            ("duplicate", "duplicates", "no duplicate", "no duplicates"),
            ("ingest_runs", "ingest runs"),
        ),
    ),
    (
        "DoD explicitly states lookup_sync resolves dynamic TBSearch IDs without hardcoded TreatyID/DocTypeID",
        (
            ("lookup_sync", "lookup sync"),
            ("dynamic", "resolve", "resolves"),
            ("tbsearch", "tb search"),
            ("hardcoded", "hard-coded"),
            ("treatyid", "doctypeid", "treaty id", "doc type id"),
        ),
    ),
    (
        "DoD explicitly states needs_ocr=true means no segments in MVP-1",
        (
            ("needs_ocr=true", "needs_ocr = true", "needs_ocr"),
            (
                "no segments",
                "segments not created",
                "segments are not created",
                "do not create segments",
            ),
            ("mvp-1", "mvp1"),
        ),
    ),
)
PLAN_QUALITY_GATE_INVARIANT_RULES: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    (
        "Quality gates explicitly reject plans missing page_index 1-based assertion",
        (
            ("gate",),
            ("manager-plan validation", "manager plan validation", "plan validation"),
            ("reject", "rejects", "rejected"),
            ("explicit", "explicitly"),
            ("page_index",),
            ("1-based", "1 based"),
        ),
    ),
    (
        "Quality gates explicitly reject plans missing idempotency/no-duplicates assertion",
        (
            ("gate",),
            ("manager-plan validation", "manager plan validation", "plan validation"),
            ("reject", "rejects", "rejected"),
            ("explicit", "explicitly"),
            ("idempotency", "idempotent"),
            ("duplicate", "duplicates", "no duplicate", "no duplicates"),
            ("ingest_runs", "ingest runs"),
        ),
    ),
    (
        "Quality gates explicitly reject plans missing lookup_sync without hardcoded IDs assertion",
        (
            ("gate",),
            ("manager-plan validation", "manager plan validation", "plan validation"),
            ("reject", "rejects", "rejected"),
            ("explicit", "explicitly"),
            ("lookup_sync", "lookup sync"),
            ("hardcoded", "hard-coded"),
            ("id", "ids"),
        ),
    ),
    (
        "Quality gates explicitly reject plans missing needs_ocr=true/no-segments assertion",
        (
            ("gate",),
            ("manager-plan validation", "manager plan validation", "plan validation"),
            ("reject", "rejects", "rejected"),
            ("explicit", "explicitly"),
            ("needs_ocr=true", "needs_ocr = true", "needs_ocr"),
            (
                "no segments",
                "segments not created",
                "segments are not created",
                "do not create segments",
            ),
            ("mvp-1", "mvp1"),
        ),
    ),
)
PLAN_DOD_INVARIANT_FALLBACK_LINES: tuple[tuple[str, str], ...] = (
    (
        "DoD explicitly states page_index remains 1-based",
        "DoD explicitly states page_index remains 1-based (first page = 1).",
    ),
    (
        "DoD explicitly states idempotency with no duplicates except ingest_runs",
        "DoD explicitly states idempotency with no duplicates except ingest_runs.",
    ),
    (
        "DoD explicitly states lookup_sync resolves dynamic TBSearch IDs without hardcoded TreatyID/DocTypeID",
        "DoD explicitly states lookup_sync resolves dynamic TBSearch IDs without hardcoded TreatyID/DocTypeID values.",
    ),
    (
        "DoD explicitly states needs_ocr=true means no segments in MVP-1",
        "DoD explicitly states needs_ocr=true means no segments in MVP-1.",
    ),
)
PLAN_QUALITY_GATE_INVARIANT_FALLBACK_LINES: tuple[tuple[str, str], ...] = (
    (
        "Quality gates explicitly reject plans missing page_index 1-based assertion",
        "Gate: manager-plan validation explicitly rejects plans missing page_index 1-based assertion.",
    ),
    (
        "Quality gates explicitly reject plans missing idempotency/no-duplicates assertion",
        "Gate: manager-plan validation explicitly rejects plans missing idempotency with no duplicates except ingest_runs assertion.",
    ),
    (
        "Quality gates explicitly reject plans missing lookup_sync without hardcoded IDs assertion",
        "Gate: manager-plan validation explicitly rejects plans missing lookup_sync without hardcoded IDs assertion.",
    ),
    (
        "Quality gates explicitly reject plans missing needs_ocr=true/no-segments assertion",
        "Gate: manager-plan validation explicitly rejects plans missing needs_ocr=true means no segments in MVP-1 assertion.",
    ),
)


def utc_now_iso() -> str:
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z")


def bootstrap_state() -> FlowState:
    return {
        "run_id": "RUN-BOOTSTRAP",
        "phase": "INIT",
        "active_task": None,
        "iteration": 0,
        "stall_count": 0,
        "attempts": {},
        "last_good_commit": None,
        "approval": {
            "required": False,
            "kind": None,
            "reason": None,
            "details_paths": [],
        },
        "history": [],
        "current_task_id": None,
        "queue": [],
    }


def normalize_state(raw: dict[str, Any]) -> FlowState:
    base = bootstrap_state()
    base.update({k: v for k, v in raw.items() if k in base})

    active_task = raw.get("active_task")
    if not isinstance(active_task, dict):
        current_task_id = raw.get("current_task_id")
        if not current_task_id and isinstance(raw.get("queue"), list) and raw["queue"]:
            current_task_id = raw["queue"][0]
        if isinstance(current_task_id, str) and current_task_id:
            base["active_task"] = {
                "task_id": current_task_id,
                "revision": 1,
                "created_at": utc_now_iso(),
            }

    approval = base.get("approval", {})
    if not isinstance(approval, dict):
        approval = {"required": False, "kind": None}
    approval.setdefault("required", False)
    approval.setdefault("kind", None)
    approval.setdefault("reason", None)
    approval.setdefault("details_paths", [])
    base["approval"] = approval

    if base.get("phase") == "COMPLETE":
        base["phase"] = "COMPLETED"

    if not isinstance(base.get("iteration"), int):
        base["iteration"] = 0
    if not isinstance(base.get("stall_count"), int):
        base["stall_count"] = 0

    active = base.get("active_task")
    if isinstance(active, dict):
        active.setdefault("revision", 1)
        active.setdefault("created_at", utc_now_iso())
    return base


def env_flag_enabled(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def collect_required_violations(node: Any, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(node, dict):
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                issues.append(f"{path}: properties must be an object")
            else:
                required = node.get("required")
                if not isinstance(required, list):
                    issues.append(f"{path}: missing required array")
                    required_keys: set[str] = set()
                else:
                    required_keys = {item for item in required if isinstance(item, str)}
                missing = sorted(key for key in properties if key not in required_keys)
                if missing:
                    issues.append(f"{path}: required missing keys {missing}")

        for key, value in node.items():
            issues.extend(collect_required_violations(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            issues.extend(collect_required_violations(value, f"{path}[{index}]"))
    return issues


class CodexFlowDispatcher:
    def __init__(
        self,
        *,
        paths: FlowPaths,
        manager_timeout_sec: int = DEFAULT_MANAGER_TIMEOUT_SEC,
        worker_timeout_sec: int = DEFAULT_WORKER_TIMEOUT_SEC,
        max_attempts_worker: int = DEFAULT_MAX_ATTEMPTS_WORKER,
    ) -> None:
        self.paths = paths
        self.manager_timeout_sec = manager_timeout_sec
        self.worker_timeout_sec = worker_timeout_sec
        self.max_attempts_worker = max_attempts_worker

    def active_task_id(self, state: FlowState) -> str | None:
        active = state.get("active_task")
        if isinstance(active, dict):
            task_id = active.get("task_id")
            if isinstance(task_id, str) and task_id:
                return task_id
        return None

    def set_active_task(self, state: FlowState, task_id: str, revision: int) -> None:
        state["active_task"] = {
            "task_id": task_id,
            "revision": revision,
            "created_at": utc_now_iso(),
        }
        state["current_task_id"] = task_id
        state["queue"] = []

    def clear_active_task(self, state: FlowState) -> None:
        state["active_task"] = None
        state["current_task_id"] = None
        state["queue"] = []

    def parse_task_number(self, task_id: str) -> int | None:
        match = re.match(r"^TSK-(\d{4})$", task_id)
        if not match:
            return None
        return int(match.group(1))

    def next_task_id_from(self, current_task_id: str) -> str:
        current_number = self.parse_task_number(current_task_id)
        if current_number is None:
            return "TSK-0001"
        return f"TSK-{current_number + 1:04d}"

    def load_task_payload(self, task_id: str) -> dict[str, Any]:
        task_path = self.paths.task_dir(task_id) / "task.json"
        return read_json(task_path, default={})

    def save_task_payload(self, state: FlowState, task_payload: dict[str, Any], revision: int) -> str:
        task_id = str(task_payload.get("task_id", "")).strip()
        if not task_id:
            raise ValueError("task payload must include task_id")

        task_dir = self.paths.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(task_dir / "task.json", task_payload)
        write_text(task_dir / "task.md", render_task_markdown(task_payload, state["run_id"]))
        self.set_active_task(state, task_id, revision)
        return task_id

    def synthesize_followup_task(
        self,
        *,
        current_task: dict[str, Any],
        current_task_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        suggested = decision.get("next_task_suggestion")
        if isinstance(suggested, str) and re.match(r"^TSK-\d{4}$", suggested):
            next_task_id = suggested
        else:
            next_task_id = self.next_task_id_from(current_task_id)

        required_changes = decision.get("required_changes", [])
        if not isinstance(required_changes, list):
            required_changes = []
        required_changes = [str(item) for item in required_changes if str(item).strip()]

        rationale = str(decision.get("rationale", "")).strip()
        focus = required_changes[0] if required_changes else rationale
        objective = focus or f"Continue iterative improvement after {current_task_id}."

        next_task = {
            "task_id": next_task_id,
            "title": f"Follow-up for {current_task_id}",
            "objective": objective,
            "scope_in": list(current_task.get("scope_in", [])),
            "scope_out": list(current_task.get("scope_out", [])),
            "dod": list(current_task.get("dod", [])),
            "quality_gates": list(current_task.get("quality_gates", [])),
            "required_commands": list(current_task.get("required_commands", [])),
            "touched_areas": list(current_task.get("touched_areas", [])),
            "risk": str(current_task.get("risk", "medium")),
            "approval_required": False,
            "depends_on": [current_task_id],
        }
        if required_changes:
            next_task["dod"] = list(next_task["dod"]) + [f"Address: {item}" for item in required_changes]
        return next_task

    def escalate_task_approval(
        self,
        state: FlowState,
        *,
        task_id: str,
        reason: str,
        details_paths: list[str] | None = None,
    ) -> None:
        state["phase"] = "WAIT_TASK_APPROVAL"
        state["approval"] = {
            "required": True,
            "kind": "task",
            "reason": reason,
            "details_paths": details_paths or [],
        }
        self.append_history(state, "task_escalated", task_id=task_id, reason=reason)

    def detect_progress(self, report: dict[str, Any], previous_commit: str | None) -> bool:
        commits = report.get("commits", [])
        current = current_commit(self.paths.repo_root)

        if previous_commit and current and current != previous_commit:
            return True
        if previous_commit is None and current:
            return True

        if isinstance(commits, list):
            for item in commits:
                if not isinstance(item, dict):
                    continue
                sha = str(item.get("sha", "")).strip()
                if sha and sha != previous_commit:
                    return True
        return False

    def decision_required_changes(self, decision: dict[str, Any]) -> list[str]:
        changes = decision.get("required_changes", [])
        if not isinstance(changes, list):
            return []
        return [str(item).strip() for item in changes if str(item).strip()]

    def normalize_decision_artifact_checks(self, decision: dict[str, Any]) -> list[str]:
        raw = decision.get("artifact_checks", [])
        if not isinstance(raw, list):
            raw = [raw]
        checks: list[str] = []
        seen: set[str] = set()
        for item in raw:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            checks.append(text)
        return checks

    def decision_full_techspec_complete(self, decision: dict[str, Any]) -> bool:
        return decision.get("full_techspec_complete") is True

    def report_has_non_runtime_changes(self, report: dict[str, Any]) -> bool:
        raw_changed_files = report.get("changed_files")
        if not isinstance(raw_changed_files, list) or not raw_changed_files:
            # Some legacy worker reports do not include changed_files. In that
            # case we treat product changes as unknown and avoid false blocking.
            return True

        runtime_prefixes = (
            ".codexflow/",
            "docs/reports/",
        )
        runtime_paths = {
            ".codexflow/state.json",
            ".codexflow/plan.json",
            ".codexflow/plan.md",
        }
        for item in raw_changed_files:
            path = str(item).strip().replace("\\", "/")
            if not path:
                continue
            if any(path.startswith(prefix) for prefix in runtime_prefixes):
                continue
            if path in runtime_paths:
                continue
            return True
        return False

    def synthesize_artifact_checks_from_report(self, report: dict[str, Any]) -> list[str]:
        checks: list[str] = []
        changed_files = report.get("changed_files", [])
        if isinstance(changed_files, list) and changed_files:
            checks.append(f"worker changed_files count={len(changed_files)}")
        commands_run = report.get("commands_run", [])
        if isinstance(commands_run, list) and commands_run:
            checks.append(f"worker commands_run count={len(commands_run)}")
        artifacts = report.get("artifacts", {})
        if isinstance(artifacts, dict):
            artifact_keys = sorted(
                key for key, value in artifacts.items() if isinstance(value, str) and value.strip()
            )
            if artifact_keys:
                checks.append("worker artifacts keys=" + ",".join(artifact_keys))
        return checks

    def enforce_accept_decision_quality(
        self,
        *,
        task_id: str,
        decision: dict[str, Any],
        report: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        verdict = str(decision.get("verdict", "")).strip()
        artifact_checks = self.normalize_decision_artifact_checks(decision)
        if not artifact_checks and verdict == "ACCEPT":
            artifact_checks = self.synthesize_artifact_checks_from_report(report)
            if artifact_checks:
                decision["artifact_checks"] = artifact_checks
        else:
            decision["artifact_checks"] = artifact_checks

        if not isinstance(decision.get("full_techspec_complete"), bool):
            decision["full_techspec_complete"] = False

        if (
            verdict == "ACCEPT"
            and decision.get("full_techspec_complete") is True
            and not self.report_has_non_runtime_changes(report)
        ):
            decision["full_techspec_complete"] = False
            rationale = str(decision.get("rationale", "")).strip()
            suffix = (
                "Dispatcher guard forced full_techspec_complete=false because worker "
                "changes contain only runtime/report artifacts."
            )
            decision["rationale"] = f"{rationale} {suffix}".strip()

        errors: list[str] = []
        if verdict == "ACCEPT" and not decision["artifact_checks"]:
            errors.append(
                "Manager ACCEPT decision is missing artifact evidence checks. "
                "Provide explicit artifact_checks in decision output."
            )

        if errors:
            required_changes = self.decision_required_changes(decision)
            for item in errors:
                if item not in required_changes:
                    required_changes.append(item)
            decision["verdict"] = "REWORK"
            decision["rationale"] = (
                "Dispatcher safety gate downgraded ACCEPT to REWORK due to missing evidence "
                "checks in manager decision."
            )
            decision["required_changes"] = required_changes
            next_task_suggestion = decision.get("next_task_suggestion")
            if not isinstance(next_task_suggestion, str) or not next_task_suggestion.strip():
                decision["next_task_suggestion"] = self.next_task_id_from(task_id)
            decision["approval_required"] = False

        return decision, errors

    def load_state(self) -> FlowState:
        raw = read_json(self.paths.state_file, default=bootstrap_state())
        return normalize_state(raw)

    def save_state(self, state: FlowState) -> None:
        write_json_atomic(self.paths.state_file, state)

    def next_run_id(self, state: FlowState) -> str:
        today = dt.date.today().isoformat()
        prefix = f"RUN-{today}-"
        seen = set()
        run_id = state.get("run_id", "")
        if run_id:
            seen.add(run_id)
        for item in state.get("history", []):
            value = item.get("run_id")
            if isinstance(value, str):
                seen.add(value)
        number = 1
        while f"{prefix}{number:03d}" in seen:
            number += 1
        return f"{prefix}{number:03d}"

    def append_history(self, state: FlowState, event: str, **extra: Any) -> None:
        item: dict[str, Any] = {"ts": utc_now_iso(), "event": event}
        item.update(extra)
        state["history"].append(item)

    def to_repo_display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.paths.repo_root))
        except ValueError:
            return str(path)

    def file_sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def write_exec_context(
        self,
        *,
        context_path: Path,
        role: str,
        prompt_path: Path,
        schema_path: Path,
        sandbox: str,
        run_id: str | None,
        task_id: str | None,
    ) -> None:
        payload: dict[str, Any] = {
            "role": role,
            "prompt_path": self.to_repo_display_path(prompt_path),
            "prompt_sha256": self.file_sha256(prompt_path),
            "schema_path": self.to_repo_display_path(schema_path),
            "sandbox": sandbox,
            "run_id": run_id,
            "task_id": task_id,
            "timestamp": utc_now_iso(),
        }
        write_json_atomic(context_path, payload)

    def write_plan_diagnostics(
        self,
        payload: dict[str, Any],
        *,
        filename: str = "manager_plan_diagnostics.json",
    ) -> str:
        path = self.paths.tmp_logs_dir / filename
        write_json_atomic(path, payload)
        return str(path.relative_to(self.paths.repo_root))

    def fail_plan(
        self,
        state: FlowState,
        *,
        error: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> int:
        diagnostics_payload: dict[str, Any] | None = None
        if diagnostics:
            diagnostics_payload = {
                "run_id": state["run_id"],
                "error": error,
                "details": diagnostics,
                "created_at": utc_now_iso(),
            }

        details_paths: list[str] = []
        if diagnostics_payload is not None:
            details_paths.append(self.write_plan_diagnostics(diagnostics_payload))

        history_payload: dict[str, Any] = {"error": error, "run_id": state["run_id"]}
        if diagnostics:
            history_payload.update(diagnostics)
        if details_paths:
            history_payload["details_paths"] = details_paths

        self.append_history(state, "plan_failed", **history_payload)
        self.save_state(state)
        print(f"Plan generation failed: {error}")
        if details_paths:
            print(f"Diagnostics: {details_paths[0]}")
        return 1

    def extract_single_task_plan(
        self,
        plan: dict[str, Any],
        *,
        expected_task_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        raw_tasks = plan.get("tasks", [])
        raw_ordering = plan.get("ordering", [])

        if not isinstance(raw_tasks, list) or not isinstance(raw_ordering, list):
            raise ValueError("manager plan must include tasks and ordering arrays")
        if len(raw_tasks) != 1 or len(raw_ordering) != 1:
            raise ValueError("manager plan must include exactly one task and one ordering item")
        if not isinstance(raw_tasks[0], dict) or not isinstance(raw_ordering[0], str):
            raise ValueError("manager plan contains invalid task or ordering item types")

        task_payload: dict[str, Any] = dict(raw_tasks[0])
        ordering_task_id = raw_ordering[0].strip()
        if not ordering_task_id:
            raise ValueError("ordering[0] must be a non-empty task id")

        task_id = str(task_payload.get("task_id", "")).strip()
        if not task_id:
            raise ValueError("first task missing task_id")
        if task_id != ordering_task_id:
            raise ValueError("tasks[0].task_id must match ordering[0]")

        if expected_task_id is not None and task_id != expected_task_id:
            raise ValueError(f"first task must be {expected_task_id}, got {task_id}")
        return task_id, task_payload

    def missing_task_invariants(self, task: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        dod_lines = self.normalized_task_lines(task, "dod")
        quality_gate_lines = self.normalized_task_lines(task, "quality_gates")
        missing.extend(self.collect_missing_invariants(dod_lines, PLAN_DOD_INVARIANT_RULES))
        missing.extend(
            self.collect_missing_invariants(
                quality_gate_lines, PLAN_QUALITY_GATE_INVARIANT_RULES
            )
        )
        return missing

    def normalized_task_lines(self, task: dict[str, Any], field_name: str) -> list[str]:
        raw_value = task.get(field_name, [])
        if not isinstance(raw_value, list):
            return []
        return [
            item.strip().lower()
            for item in raw_value
            if isinstance(item, str) and item.strip()
        ]

    def collect_missing_invariants(
        self,
        lines: list[str],
        rules: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...],
    ) -> list[str]:
        combined = "\n".join(lines)
        missing: list[str] = []
        for description, token_groups in rules:
            covered = all(any(token in combined for token in group) for group in token_groups)
            if not covered:
                missing.append(description)
        return missing

    def append_unique_line(self, lines: list[str], value: str) -> bool:
        normalized = {item.strip().casefold() for item in lines if isinstance(item, str)}
        if value.strip().casefold() in normalized:
            return False
        lines.append(value)
        return True

    def autofill_missing_task_invariants(
        self,
        task: dict[str, Any],
        missing_invariants: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        updated = dict(task)
        raw_dod = updated.get("dod", [])
        raw_quality = updated.get("quality_gates", [])
        dod = [item for item in raw_dod if isinstance(item, str)] if isinstance(raw_dod, list) else []
        quality_gates = (
            [item for item in raw_quality if isinstance(item, str)]
            if isinstance(raw_quality, list)
            else []
        )

        added: list[str] = []
        dod_fallback = dict(PLAN_DOD_INVARIANT_FALLBACK_LINES)
        gate_fallback = dict(PLAN_QUALITY_GATE_INVARIANT_FALLBACK_LINES)
        for invariant in missing_invariants:
            if invariant in dod_fallback:
                line = dod_fallback[invariant]
                if self.append_unique_line(dod, line):
                    added.append(invariant)
                continue
            if invariant in gate_fallback:
                line = gate_fallback[invariant]
                if self.append_unique_line(quality_gates, line):
                    added.append(invariant)

        updated["dod"] = dod
        updated["quality_gates"] = quality_gates
        return updated, added

    def create_state_if_needed(self, force: bool = False) -> None:
        self.paths.ensure_layout()
        if self.paths.state_file.exists() and not force:
            return
        self.save_state(bootstrap_state())

    def cmd_init(self, force: bool = False) -> int:
        with file_lock(self.paths.lock_file):
            self.paths.ensure_layout()
            self.create_state_if_needed(force=force)
            self.ensure_plan_readme()
        print(f"Initialized CodexFlow at {self.paths.codexflow_dir}")
        return 0

    def ensure_plan_readme(self) -> None:
        plan_readme = self.paths.plan_approvals_dir / "README.md"
        if plan_readme.exists():
            return
        write_text(
            plan_readme,
            "# Plan Approval\n\n"
            "Create `approved.marker` in this directory or use dispatcher approve.\n",
        )

    def safe_remove(self, path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink(missing_ok=True)

    def sanitize_for_path(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
        return cleaned.strip("_") or "unknown"

    def truncate_note(self, text: str, *, limit: int = PRODUCT_CHECK_NOTE_LIMIT) -> str:
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    def classify_worktree_add_failure(
        self,
        *,
        stdout: str,
        stderr: str,
        worktree_snapshot: str,
    ) -> str:
        combined = "\n".join([stdout, stderr, worktree_snapshot]).lower()
        has_worktrees = ".git/worktrees/" in combined or "worktree" in combined
        has_commondir = "commondir" in combined
        missing_commondir = "no such file or directory" in combined
        broken_link = "broken link from" in combined
        not_git_repo = "not a git repository" in combined
        if has_worktrees and has_commondir and (
            missing_commondir or broken_link or not_git_repo
        ):
            return "broken_worktree_metadata"
        return "worktree_add_failed"

    def build_product_check_worktree_dir(
        self,
        *,
        run_id: str,
        task_id: str,
        target_ref: str,
        attempt: int,
    ) -> Path:
        run_part = self.sanitize_for_path(run_id)
        task_part = self.sanitize_for_path(task_id)
        ref_part = self.sanitize_for_path(target_ref)
        if len(ref_part) > 24:
            ref_hash = hashlib.sha1(target_ref.encode("utf-8")).hexdigest()[:8]
            ref_part = f"{ref_part[:15]}-{ref_hash}"
        return self.paths.tmp_dir / "worktrees" / run_part / task_part / f"{ref_part}-a{attempt}"

    def cleanup_worktree_dir(self, worktree_dir: Path, *, worktree_root: Path) -> None:
        if worktree_dir.exists():
            rc_cleanup, _, _ = self.run_command(
                ["git", "worktree", "remove", "--force", str(worktree_dir)],
                timeout_sec=120,
            )
            if rc_cleanup != 0:
                shutil.rmtree(worktree_dir, ignore_errors=True)

        current = worktree_dir.parent
        while current != worktree_root.parent:
            if current == worktree_root.parent:
                break
            try:
                current.rmdir()
            except OSError:
                break
            if current == worktree_root:
                break
            current = current.parent

    def worktree_list_snapshot(self) -> str:
        rc, out, err = self.run_command(
            ["git", "worktree", "list", "--porcelain"],
            timeout_sec=60,
        )
        text = out if out.strip() else err
        lines = [line for line in text.splitlines() if line.strip()]
        lines = lines[:PRODUCT_CHECK_WORKTREE_SNAPSHOT_LINE_LIMIT]
        body = "\n".join(lines) if lines else "<empty>"
        return self.truncate_note(f"rc={rc}\n{body}")

    def add_product_check_worktree_with_retries(
        self,
        *,
        run_id: str,
        task_id: str,
        target_ref: str,
        payload: dict[str, Any],
    ) -> tuple[Path | None, int, str]:
        worktree_root = self.paths.tmp_dir / "worktrees"
        worktree_root.mkdir(parents=True, exist_ok=True)
        last_rc = 2
        last_failure_class = "worktree_add_failed"

        for attempt in range(1, PRODUCT_CHECK_WORKTREE_ADD_MAX_ATTEMPTS + 1):
            worktree_dir = self.build_product_check_worktree_dir(
                run_id=run_id,
                task_id=task_id,
                target_ref=target_ref,
                attempt=attempt,
            )
            worktree_dir.parent.mkdir(parents=True, exist_ok=True)
            self.cleanup_worktree_dir(worktree_dir, worktree_root=worktree_root)

            rc_add, out_add, err_add = self.run_command(
                ["git", "worktree", "add", "--detach", str(worktree_dir), target_ref],
                timeout_sec=180,
            )
            if rc_add == 0:
                return worktree_dir, 0, "none"

            last_rc = rc_add
            worktree_snapshot = self.worktree_list_snapshot()
            failure_class = self.classify_worktree_add_failure(
                stdout=out_add or "",
                stderr=err_add or "",
                worktree_snapshot=worktree_snapshot,
            )
            last_failure_class = failure_class
            payload["notes"].append(
                self.truncate_note(
                    "\n".join(
                        [
                            f"worktree_add_attempt={attempt}",
                            f"failure_class={failure_class}",
                            f"rc={rc_add}",
                            f"stdout={(out_add or '').strip() or '<empty>'}",
                            f"stderr={(err_add or '').strip() or '<empty>'}",
                            "worktree_list_snapshot:",
                            worktree_snapshot,
                        ]
                    ),
                    limit=1200,
                )
            )

            self.cleanup_worktree_dir(worktree_dir, worktree_root=worktree_root)
            if attempt >= PRODUCT_CHECK_WORKTREE_ADD_MAX_ATTEMPTS:
                continue

            rc_prune, out_prune, err_prune = self.run_command(
                ["git", "worktree", "prune", "--verbose"],
                timeout_sec=120,
            )
            rc_repair, out_repair, err_repair = self.run_command(
                ["git", "worktree", "repair"],
                timeout_sec=120,
            )
            payload["notes"].append(
                self.truncate_note(
                    "\n".join(
                        [
                            f"worktree_recovery_attempt={attempt}",
                            f"failure_class={failure_class}",
                            f"prune_rc={rc_prune}",
                            f"prune_stdout={(out_prune or '').strip() or '<empty>'}",
                            f"prune_stderr={(err_prune or '').strip() or '<empty>'}",
                            f"repair_rc={rc_repair}",
                            f"repair_stdout={(out_repair or '').strip() or '<empty>'}",
                            f"repair_stderr={(err_repair or '').strip() or '<empty>'}",
                        ]
                    ),
                    limit=1200,
                )
            )

        return None, last_rc, last_failure_class

    def archive_item(self, source: Path, archive_root: Path) -> None:
        if not source.exists():
            return
        if source.is_relative_to(self.paths.codexflow_dir):
            rel = source.relative_to(self.paths.codexflow_dir)
        else:
            rel = Path(source.name)
        destination = archive_root / rel
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    def build_reset_state(
        self,
        *,
        previous_state: FlowState,
        archive_root: Path,
        keep_plan: bool,
    ) -> FlowState:
        run_id = self.next_run_id(previous_state)
        if keep_plan and self.paths.plan_json.exists():
            plan = read_json(self.paths.plan_json, default={})
            tasks = list(plan.get("tasks", []))
            first_task_id = None
            if tasks and isinstance(tasks[0], dict):
                value = tasks[0].get("task_id")
                if isinstance(value, str) and value:
                    first_task_id = value
            phase = "WAIT_PLAN_APPROVAL" if first_task_id else "INIT"
            approval = {
                "required": bool(first_task_id),
                "kind": "plan" if first_task_id else None,
                "reason": "Plan approval required before run." if first_task_id else None,
                "details_paths": [str(self.paths.plan_md.relative_to(self.paths.repo_root))]
                if first_task_id
                else [],
            }
            active_task = (
                {
                    "task_id": first_task_id,
                    "revision": 1,
                    "created_at": utc_now_iso(),
                }
                if first_task_id
                else None
            )
        else:
            phase = "INIT"
            approval = {"required": False, "kind": None, "reason": None, "details_paths": []}
            active_task = None

        history_entry = {
            "ts": utc_now_iso(),
            "event": "reset",
            "previous_run_id": previous_state.get("run_id", "RUN-UNKNOWN"),
            "keep_plan": keep_plan,
            "archive_path": str(archive_root.relative_to(self.paths.repo_root)),
        }
        return {
            "run_id": run_id,
            "phase": phase,
            "active_task": active_task,
            "iteration": 0,
            "stall_count": 0,
            "attempts": {},
            "last_good_commit": current_commit(self.paths.repo_root),
            "approval": approval,
            "history": [history_entry],
            "current_task_id": active_task["task_id"] if active_task else None,
            "queue": [],
        }

    def cmd_reset(self, keep_plan: bool = False) -> int:
        with file_lock(self.paths.lock_file):
            self.paths.ensure_layout()
            previous_state = self.load_state()
            ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_id = self.sanitize_for_path(previous_state.get("run_id", "RUN-UNKNOWN"))
            archive_root = self.paths.archive_dir / f"{ts}_{run_id}"
            archive_root.mkdir(parents=True, exist_ok=True)

            archive_sources = [
                self.paths.state_file,
                self.paths.plan_json,
                self.paths.plan_md,
                self.paths.tasks_dir,
                self.paths.reports_dir,
                self.paths.approvals_dir,
            ]
            for source in archive_sources:
                self.archive_item(source, archive_root)

            self.safe_remove(self.paths.reports_dir)
            self.safe_remove(self.paths.approvals_dir)
            self.paths.reports_dir.mkdir(parents=True, exist_ok=True)
            self.paths.plan_approvals_dir.mkdir(parents=True, exist_ok=True)
            self.ensure_plan_readme()

            if not keep_plan:
                self.safe_remove(self.paths.plan_json)
                self.safe_remove(self.paths.plan_md)
                self.safe_remove(self.paths.tasks_dir)
                self.paths.tasks_dir.mkdir(parents=True, exist_ok=True)

            new_state = self.build_reset_state(
                previous_state=previous_state,
                archive_root=archive_root,
                keep_plan=keep_plan,
            )
            self.save_state(new_state)

        print(f"Reset complete. Archive: {archive_root}")
        print(f"New run_id: {new_state['run_id']}; phase={new_state['phase']}")
        return 0

    def build_manager_plan_prompt(
        self,
        *,
        current_task_id: str | None = None,
        suggested_task_id: str | None = None,
        product_check_path: str | None = None,
        product_check_payload: dict[str, Any] | None = None,
        missing_invariants: list[str] | None = None,
        retry_attempt: int | None = None,
        retry_limit: int | None = None,
    ) -> tuple[str, Path]:
        prompt_path = self.paths.resolve_manager_plan_prompt_path()
        static = prompt_path.read_text(encoding="utf-8").rstrip()
        state = self.load_state()
        tail = (
            "\n\nDynamic context:\n"
            f"spec_path=docs/TECH_SPEC.md\n"
            f"repo_root={self.paths.repo_root}\n"
            f"state_path={self.paths.state_file.relative_to(self.paths.repo_root)}\n"
            f"run_id={state['run_id']}\n"
        )
        if current_task_id:
            tail += f"current_task_id={current_task_id}\n"
        if suggested_task_id:
            tail += f"suggested_next_task_id={suggested_task_id}\n"
        if product_check_path:
            tail += f"product_check_path={product_check_path}\n"
        if product_check_payload:
            tail += "product_check_json:\n"
            tail += json.dumps(product_check_payload, ensure_ascii=False, indent=2)
            tail += "\n"
        if missing_invariants:
            tail += "plan_retry_context:\n"
            if retry_attempt is not None and retry_limit is not None:
                tail += f"retry_attempt={retry_attempt}/{retry_limit}\n"
            tail += "previous_plan_missing_invariants:\n"
            for item in missing_invariants:
                tail += f"- {item}\n"
            tail += (
                "Regenerate the plan so all missing invariants are explicitly present in "
                "task.dod and task.quality_gates.\n"
            )
        return static + tail, prompt_path

    def cmd_plan(self) -> int:
        with file_lock(self.paths.lock_file):
            self.paths.ensure_layout()
            state = self.load_state()
            if state["run_id"] == "RUN-BOOTSTRAP":
                state["run_id"] = self.next_run_id(state)

            output_file = self.paths.tmp_dir / "manager_plan.json"
            stdout_log = self.paths.tmp_logs_dir / "manager_plan.jsonl"
            stderr_log = self.paths.tmp_logs_dir / "manager_plan.stderr.txt"
            attempt_diagnostics: list[dict[str, Any]] = []
            plan: dict[str, Any] | None = None
            first_task_id: str | None = None
            first_task: dict[str, Any] | None = None
            retry_missing: list[str] = []
            autofilled_invariants: list[str] = []
            max_attempts = 1 + MAX_PLAN_INVARIANT_RETRIES

            normalized_first_task_from: str | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    prompt_text, prompt_path = self.build_manager_plan_prompt(
                        missing_invariants=retry_missing if retry_missing else None,
                        retry_attempt=attempt if retry_missing else None,
                        retry_limit=max_attempts if retry_missing else None,
                    )
                    self.write_exec_context(
                        context_path=stdout_log.with_suffix(".context.json"),
                        role="manager_plan",
                        prompt_path=prompt_path,
                        schema_path=self.paths.manager_plan_schema,
                        sandbox="read-only",
                        run_id=state["run_id"],
                        task_id=None,
                    )
                    run_codex_exec(
                        prompt_text=prompt_text,
                        sandbox="read-only",
                        schema_path=self.paths.manager_plan_schema,
                        output_file=output_file,
                        stdout_log=stdout_log,
                        stderr_log=stderr_log,
                        cwd=self.paths.repo_root,
                        timeout_sec=self.manager_timeout_sec,
                    )
                    plan_candidate = read_json(output_file)
                except (
                    PromptResolutionError,
                    OSError,
                    UnicodeDecodeError,
                    CodexExecError,
                    json.JSONDecodeError,
                ) as exc:
                    return self.fail_plan(state, error=str(exc))

                raw_tasks = plan_candidate.get("tasks", [])
                raw_ordering = plan_candidate.get("ordering", [])
                try:
                    task_id_candidate, task_candidate = self.extract_single_task_plan(
                        plan_candidate,
                        expected_task_id=None,
                    )
                except ValueError as exc:
                    diagnostics: dict[str, Any] = {"attempt": attempt}
                    if not isinstance(raw_tasks, list):
                        diagnostics["tasks_type"] = type(raw_tasks).__name__
                    else:
                        diagnostics["tasks_count"] = len(raw_tasks)
                        if raw_tasks:
                            diagnostics["task_item_type"] = type(raw_tasks[0]).__name__
                    if not isinstance(raw_ordering, list):
                        diagnostics["ordering_type"] = type(raw_ordering).__name__
                    else:
                        diagnostics["ordering_count"] = len(raw_ordering)
                        if raw_ordering:
                            diagnostics["ordering_item_type"] = type(raw_ordering[0]).__name__
                    return self.fail_plan(
                        state,
                        error=str(exc),
                        diagnostics=diagnostics,
                    )

                normalized_from: str | None = None
                if task_id_candidate != "TSK-0001":
                    normalized_from = task_id_candidate
                    task_id_candidate = "TSK-0001"
                    task_candidate = dict(task_candidate)
                    task_candidate["task_id"] = task_id_candidate
                    normalized_first_task_from = normalized_from

                missing_invariants = self.missing_task_invariants(task_candidate)
                attempt_entry: dict[str, Any] = {
                    "attempt": attempt,
                    "task_id": task_id_candidate,
                    "missing_invariants": missing_invariants,
                }
                if normalized_from:
                    attempt_entry["normalized_task_id_from"] = normalized_from
                attempt_diagnostics.append(attempt_entry)

                if not missing_invariants:
                    plan = plan_candidate
                    first_task_id = task_id_candidate
                    first_task = task_candidate
                    break

                retry_missing = list(missing_invariants)
                if attempt < max_attempts:
                    continue

                repaired_task, autofilled_invariants = self.autofill_missing_task_invariants(
                    task_candidate, missing_invariants
                )
                remaining_missing = self.missing_task_invariants(repaired_task)
                attempt_entry["autofilled_invariants"] = autofilled_invariants
                attempt_entry["remaining_missing_invariants"] = remaining_missing
                if remaining_missing:
                    return self.fail_plan(
                        state,
                        error="TSK-0001 is missing required TechSpec invariants in dod/quality_gates",
                        diagnostics={
                            "task_id": task_id_candidate,
                            "missing_invariants": remaining_missing,
                            "attempts": attempt_diagnostics,
                            "autofilled_invariants": autofilled_invariants,
                        },
                    )
                plan = plan_candidate
                first_task_id = task_id_candidate
                first_task = repaired_task

            if plan is None or first_task_id is None or first_task is None:
                return self.fail_plan(
                    state,
                    error="manager plan stabilization exhausted attempts without producing a valid task",
                    diagnostics={"attempts": attempt_diagnostics},
                )

            if len(attempt_diagnostics) > 1 or autofilled_invariants:
                diagnostics_payload = {
                    "run_id": state["run_id"],
                    "event": "plan_invariants_stabilized",
                    "attempts": attempt_diagnostics,
                    "autofilled_invariants": autofilled_invariants,
                    "created_at": utc_now_iso(),
                }
                details_path = self.write_plan_diagnostics(diagnostics_payload)
                self.append_history(
                    state,
                    "plan_invariants_stabilized",
                    run_id=state["run_id"],
                    attempts=len(attempt_diagnostics),
                    autofilled_invariants=autofilled_invariants,
                    details_paths=[details_path],
                )

            trimmed_plan = dict(plan)
            trimmed_plan["tasks"] = [first_task]
            trimmed_plan["ordering"] = [first_task_id]
            write_json_atomic(self.paths.plan_json, trimmed_plan)
            write_text(self.paths.plan_md, render_plan_markdown(trimmed_plan))

            self.safe_remove(self.paths.tasks_dir)
            self.paths.tasks_dir.mkdir(parents=True, exist_ok=True)
            task_dir = self.paths.task_dir(first_task_id)
            task_dir.mkdir(parents=True, exist_ok=True)
            write_json_atomic(task_dir / "task.json", first_task)
            write_text(task_dir / "task.md", render_task_markdown(first_task, state["run_id"]))

            state["phase"] = "WAIT_PLAN_APPROVAL"
            state["approval"] = {
                "required": True,
                "kind": "plan",
                "reason": "Plan approval required before execution.",
                "details_paths": [
                    str(self.paths.plan_json.relative_to(self.paths.repo_root)),
                    str(self.paths.plan_md.relative_to(self.paths.repo_root)),
                ],
            }
            self.set_active_task(state, first_task_id, revision=1)
            state["iteration"] = 0
            state["stall_count"] = 0
            state["attempts"] = {}
            if normalized_first_task_from:
                self.append_history(
                    state,
                    "plan_task_id_normalized",
                    run_id=state["run_id"],
                    from_task_id=normalized_first_task_from,
                    to_task_id=first_task_id,
                )
            self.append_history(state, "plan_created", run_id=state["run_id"])
            self.save_state(state)

        print(f"Plan generated: {self.paths.plan_json}")
        return 0

    def write_marker(self, marker_path: Path, label: str) -> None:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        write_text(marker_path, f"{label}\ncreated_at={utc_now_iso()}\n")

    def validate_plan_approval_artifacts(
        self,
    ) -> tuple[bool, str, str | None]:
        if not self.paths.plan_json.exists():
            return False, "missing .codexflow/plan.json", None

        try:
            plan = read_json(self.paths.plan_json, default={})
        except (json.JSONDecodeError, OSError) as exc:
            return False, f"cannot read .codexflow/plan.json: {exc}", None

        if not isinstance(plan, dict):
            return False, "invalid .codexflow/plan.json: expected object", None

        ordering = plan.get("ordering")
        if not isinstance(ordering, list) or not ordering:
            return False, "invalid plan ordering: expected non-empty array", None

        first_task = ordering[0]
        if not isinstance(first_task, str) or not first_task.strip():
            return False, "invalid plan ordering[0]: expected non-empty task id string", None

        first_task_id = first_task.strip()
        task_path = self.paths.task_dir(first_task_id) / "task.json"
        if not task_path.exists():
            return (
                False,
                f"missing task artifact for first task: {task_path.relative_to(self.paths.repo_root)}",
                None,
            )
        try:
            task_payload = read_json(task_path, default={})
        except (json.JSONDecodeError, OSError) as exc:
            return False, f"cannot read {task_path.relative_to(self.paths.repo_root)}: {exc}", None
        if not isinstance(task_payload, dict):
            return (
                False,
                f"invalid task artifact: {task_path.relative_to(self.paths.repo_root)}",
                None,
            )
        return True, "", first_task_id

    def cmd_approve(self, kind: str, task_id: str | None) -> int:
        with file_lock(self.paths.lock_file):
            state = self.load_state()
            if kind == "plan":
                ok, reason, first_task_id = self.validate_plan_approval_artifacts()
                if not ok:
                    self.append_history(
                        state,
                        "plan_approval_rejected",
                        run_id=state["run_id"],
                        reason=reason,
                    )
                    self.save_state(state)
                    print(f"Cannot approve plan: {reason}")
                    return 1

                if first_task_id and self.active_task_id(state) != first_task_id:
                    self.set_active_task(state, first_task_id, revision=1)
                self.write_marker(self.paths.plan_approvals_dir / "approved.marker", "kind=plan")
                state["phase"] = "TASK_READY"
                state["approval"] = {"required": False, "kind": None, "reason": None, "details_paths": []}
                self.append_history(state, "plan_approved", run_id=state["run_id"])
                self.save_state(state)
                print("Plan approved")
                return 0

            if kind == "task":
                effective_task_id = task_id or self.active_task_id(state)
                if not effective_task_id:
                    print("approve --kind task requires --task-id when no active task is set")
                    return 2
                marker = self.paths.approval_dir(effective_task_id) / "approved.marker"
                self.write_marker(marker, f"kind=task\ntask_id={effective_task_id}")
                if state["phase"] == "WAIT_TASK_APPROVAL" and self.active_task_id(state) == effective_task_id:
                    state["phase"] = "TASK_READY"
                    state["approval"] = {
                        "required": False,
                        "kind": None,
                        "reason": None,
                        "details_paths": [],
                    }
                self.append_history(
                    state, "task_approved", task_id=effective_task_id, run_id=state["run_id"]
                )
                self.save_state(state)
                print(f"Task approved: {effective_task_id}")
                return 0

            print(f"Unknown approval kind: {kind}")
            return 2

    def run_command(
        self,
        command: list[str],
        *,
        timeout_sec: int = 120,
    ) -> tuple[int, str, str]:
        try:
            completed = subprocess.run(
                command,
                cwd=self.paths.repo_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_sec,
            )
            return completed.returncode, completed.stdout, completed.stderr
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            message = f"Command timed out after {timeout_sec}s: {' '.join(command)}"
            return 124, stdout, (stderr + "\n" + message).strip()
        except FileNotFoundError as exc:
            return 127, "", str(exc)

    def list_tracked_runtime_artifacts(self) -> tuple[list[str], str | None]:
        cmd = ["git", "ls-files", "--", *RUNTIME_TRACKED_PATHS]
        rc, out, err = self.run_command(cmd, timeout_sec=60)
        if rc != 0:
            details = err or out or f"git ls-files exited with code {rc}"
            return [], details.strip()
        tracked = sorted({line.strip() for line in out.splitlines() if line.strip()})
        return tracked, None

    def check_project_trust(self) -> tuple[str, str, str]:
        config_path = Path.home() / ".codex" / "config.toml"
        project_path = str(self.paths.repo_root)
        block_header = f'[projects."{project_path}"]'
        snippet = (
            "[projects.\""
            + project_path
            + "\"]\n"
            + 'trust_level = "trusted"\n'
        )
        if not config_path.exists():
            return "WARN", f"{config_path} missing", snippet

        lines = config_path.read_text(encoding="utf-8").splitlines()
        start_idx: int | None = None
        for idx, line in enumerate(lines):
            if line.strip() == block_header:
                start_idx = idx
                break
        if start_idx is None:
            return "WARN", "project trust block not found", snippet

        end_idx = len(lines)
        for idx in range(start_idx + 1, len(lines)):
            if lines[idx].strip().startswith("["):
                end_idx = idx
                break
        block = lines[start_idx:end_idx]
        trusted = any(line.strip() == 'trust_level = "trusted"' for line in block)
        if trusted:
            return "PASS", "project trust_level=trusted", "\n".join(block)
        return "WARN", "project block found but trust_level is not trusted", "\n".join(block)

    def render_preflight_report(
        self,
        *,
        checks: list[dict[str, str]],
        trust_snippet: str | None,
        manager_prompt: str,
        worker_prompt: str,
    ) -> str:
        lines: list[str] = []
        lines.append("# CodexFlow Variant A — Preflight Report")
        lines.append(f"- Date/time (UTC): {utc_now_iso()}")
        lines.append(f"- Repo root: `{self.paths.repo_root}`")
        lines.append("")
        lines.append("## Checks")
        for item in checks:
            lines.append(f"### {item['name']} — {item['status']}")
            lines.append("")
            if item.get("command"):
                lines.append("Command:")
                lines.append("```bash")
                lines.append(item["command"])
                lines.append("```")
            if item.get("details"):
                lines.append("Details:")
                lines.append("```text")
                lines.append(item["details"])
                lines.append("```")
            lines.append("")

        lines.append("## Prompt Resolution")
        lines.append("")
        lines.append(f"Manager prompt: {manager_prompt}")
        lines.append(f"Worker prompt: {worker_prompt}")
        lines.append("")

        if trust_snippet:
            lines.append("## Manual Step (Trust)")
            lines.append("")
            lines.append("```toml")
            lines.append(trust_snippet.rstrip())
            lines.append("```")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def check_schema_strict_contract(self) -> tuple[str, str]:
        schema_paths = [
            self.paths.worker_report_schema,
            self.paths.manager_review_schema,
            self.paths.manager_plan_schema,
        ]
        all_issues: list[str] = []

        for schema_path in schema_paths:
            try:
                payload = read_json(schema_path)
            except Exception as exc:  # noqa: BLE001
                all_issues.append(f"{schema_path.name}: read error: {exc}")
                continue

            issues = collect_required_violations(payload)
            for issue in issues:
                all_issues.append(f"{schema_path.name}: {issue}")

        if all_issues:
            return "FAIL", "\n".join(all_issues)
        return "PASS", "All schema object nodes satisfy required ⊇ properties."

    def cmd_preflight(self) -> int:
        checks: list[dict[str, str]] = []
        trust_snippet: str | None = None
        manager_prompt_value = "unresolved"
        worker_prompt_value = "unresolved"

        def add_check(name: str, status: str, command: str, details: str) -> None:
            checks.append(
                {
                    "name": name,
                    "status": status,
                    "command": command,
                    "details": details.strip()[:6000],
                }
            )
            print(f"[{status}] {name}")

        with file_lock(self.paths.lock_file):
            self.paths.ensure_layout()
            self.create_state_if_needed(force=False)

            try:
                manager_plan_prompt = self.paths.resolve_manager_plan_prompt_path()
                manager_review_prompt = self.paths.resolve_manager_review_prompt_path()
                worker_prompt = self.paths.resolve_worker_prompt_path()
                _ = manager_plan_prompt.read_text(encoding="utf-8")
                _ = manager_review_prompt.read_text(encoding="utf-8")
                _ = worker_prompt.read_text(encoding="utf-8")

                manager_prompt_value = self.to_repo_display_path(manager_plan_prompt)
                worker_prompt_value = self.to_repo_display_path(worker_prompt)
                prompt_details = [
                    f"Manager prompt: {manager_prompt_value}",
                    f"Worker prompt: {worker_prompt_value}",
                ]
                review_value = self.to_repo_display_path(manager_review_prompt)
                if review_value != manager_prompt_value:
                    prompt_details.append(f"Manager review prompt fallback: {review_value}")
                add_check(
                    "Prompt files",
                    "PASS",
                    "prompt path resolution and readability checks",
                    "\n".join(prompt_details),
                )
            except (PromptResolutionError, OSError, UnicodeDecodeError) as exc:
                manager_prompt_value = f"ERROR: {exc}"
                add_check(
                    "Prompt files",
                    "FAIL",
                    "prompt path resolution and readability checks",
                    str(exc),
                )

            rc, out, err = self.run_command(["codex", "login", "status"], timeout_sec=60)
            status = "PASS" if rc == 0 else "FAIL"
            add_check("Codex login", status, "codex login status", out + err)

            smoke_schema = self.paths.tmp_dir / "schema_smoke.json"
            smoke_output = self.paths.tmp_dir / "schema_smoke.output.json"
            write_json_atomic(
                smoke_schema,
                {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            )
            smoke_cmd = [
                "codex",
                "exec",
                'Return {"ok": true}.',
                "--config",
                'approval_policy="never"',
                "--sandbox",
                "read-only",
                "--output-schema",
                str(smoke_schema),
                "--output-last-message",
                str(smoke_output),
            ]
            rc, out, err = self.run_command(smoke_cmd, timeout_sec=180)
            smoke_status = "FAIL"
            smoke_details = out + err
            if rc == 0 and smoke_output.exists():
                try:
                    payload = read_json(smoke_output)
                    if payload.get("ok") is True:
                        smoke_status = "PASS"
                except Exception as exc:  # noqa: BLE001
                    smoke_details += f"\nparse_error: {exc}"
            add_check(
                "Schema smoke",
                smoke_status,
                "codex exec ... --output-schema .codexflow/_tmp/schema_smoke.json",
                smoke_details,
            )

            required_paths = [
                self.paths.tech_spec,
                self.paths.state_file,
                self.paths.manager_plan_schema,
                self.paths.worker_report_schema,
                self.paths.manager_review_schema,
                self.paths.repo_root / "scripts" / "codexflow.py",
                self.paths.product_check_script,
            ]
            missing = [str(path.relative_to(self.paths.repo_root)) for path in required_paths if not path.exists()]
            if missing:
                add_check(
                    "Required files",
                    "FAIL",
                    "path existence checks",
                    "Missing:\n" + "\n".join(missing),
                )
            else:
                add_check("Required files", "PASS", "path existence checks", "All required files exist")

            tracked_runtime, runtime_err = self.list_tracked_runtime_artifacts()
            if runtime_err:
                add_check(
                    "Runtime tracked artifacts",
                    "FAIL",
                    "git ls-files -- .codexflow/reports .codexflow/approvals ...",
                    runtime_err,
                )
            elif tracked_runtime:
                add_check(
                    "Runtime tracked artifacts",
                    "FAIL",
                    "git ls-files -- .codexflow/reports .codexflow/approvals ...",
                    "Runtime artifacts are tracked; please untrack and update .gitignore.\n"
                    + "\n".join(tracked_runtime),
                )
            else:
                add_check(
                    "Runtime tracked artifacts",
                    "PASS",
                    "git ls-files -- .codexflow/reports .codexflow/approvals ...",
                    "No codexflow runtime artifacts are tracked in git.",
                )

            strict_status, strict_details = self.check_schema_strict_contract()
            add_check(
                "Schema strict contract",
                strict_status,
                "offline recursive required/properties check",
                strict_details,
            )

            trust_status, trust_message, trust_text = self.check_project_trust()
            trust_snippet = trust_text if trust_status != "PASS" else None
            add_check("Project trust", trust_status, "~/.codex/config.toml block check", trust_message)

            rc, out, err = self.run_command(["ruff", "check", "."], timeout_sec=300)
            add_check("ruff", "PASS" if rc == 0 else "FAIL", "ruff check .", out + err)

            rc, out, err = self.run_command(["pytest", "-q"], timeout_sec=300)
            add_check("pytest", "PASS" if rc == 0 else "FAIL", "pytest -q", out + err)

            report = self.render_preflight_report(
                checks=checks,
                trust_snippet=trust_snippet,
                manager_prompt=manager_prompt_value,
                worker_prompt=worker_prompt_value,
            )
            write_text(self.paths.preflight_report, report)

        has_fail = any(item["status"] == "FAIL" for item in checks)
        has_warn = any(item["status"] == "WARN" for item in checks)
        print(f"Preflight report: {self.paths.preflight_report}")
        if has_fail:
            print("Preflight result: FAIL")
            return 1
        if has_warn:
            print("Preflight result: WARN")
            return 0
        print("Preflight result: PASS")
        return 0

    def build_worker_prompt(
        self, *, task_id: str, task_json_path: Path, task_md_path: Path
    ) -> tuple[str, Path]:
        prompt_path = self.paths.resolve_worker_prompt_path()
        static = prompt_path.read_text(encoding="utf-8").rstrip()
        relative_json = task_json_path.relative_to(self.paths.repo_root)
        relative_md = task_md_path.relative_to(self.paths.repo_root)
        tail = (
            "\n\nDynamic context:\n"
            f"task_id={task_id}\n"
            f"task_json={relative_json}\n"
            f"task_md={relative_md}\n"
            f"run_id={self.load_state()['run_id']}\n"
            f"repo_root={self.paths.repo_root}\n"
        )
        return static + tail, prompt_path

    def build_review_prompt(
        self,
        *,
        task_id: str,
        report_path: Path,
        task_json_path: Path,
        task_md_path: Path,
    ) -> tuple[str, Path]:
        prompt_path = self.paths.resolve_manager_review_prompt_path()
        static = prompt_path.read_text(encoding="utf-8").rstrip()
        branch = current_branch(self.paths.repo_root) or "unknown"
        commit = current_commit(self.paths.repo_root) or "unknown"
        diff = diff_summary(self.paths.repo_root)
        tail = (
            "\n\nDynamic context:\n"
            f"task_id={task_id}\n"
            f"worker_report_path={report_path.relative_to(self.paths.repo_root)}\n"
            f"task_json={task_json_path.relative_to(self.paths.repo_root)}\n"
            f"task_md={task_md_path.relative_to(self.paths.repo_root)}\n"
            f"git_branch={branch}\n"
            f"git_commit={commit}\n"
            "git_diff_summary:\n"
            f"{diff}\n"
        )
        return static + tail, prompt_path

    def fallback_worker_report(self, task_id: str, error: str) -> dict[str, Any]:
        branch = current_branch(self.paths.repo_root) or "unknown"
        return {
            "task_id": task_id,
            "status": "FAILED",
            "branch": branch,
            "commits": [],
            "changed_files": [],
            "commands_run": [
                {"cmd": "codex exec (worker)", "exit_code": 1, "notes": error}
            ],
            "tests": [],
            "summary": "Worker execution failed before producing schema-valid output.",
            "risks_or_notes": [error],
            "artifacts": {},
            "created_at": utc_now_iso(),
        }

    def fallback_review(self, task_id: str, error: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "verdict": "STOP",
            "rationale": f"Manager review failed: {error}",
            "required_changes": ["Fix manager review execution and rerun."],
            "next_task_suggestion": None,
            "artifact_checks": ["Manager review execution failed before artifact audit."],
            "full_techspec_complete": False,
            "approval_required": False,
            "created_at": utc_now_iso(),
        }

    def execute_worker(self, task_id: str) -> tuple[dict[str, Any], str | None]:
        task_dir = self.paths.task_dir(task_id)
        task_json_path = task_dir / "task.json"
        task_md_path = task_dir / "task.md"

        report_dir = self.paths.report_dir(task_id)
        logs_dir = report_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        output_file = self.paths.tmp_dir / f"worker_report_{task_id}.json"
        stdout_log = logs_dir / "worker.jsonl"
        stderr_log = logs_dir / "worker.stderr.txt"

        execution_error: str | None = None
        try:
            prompt_text, prompt_path = self.build_worker_prompt(
                task_id=task_id,
                task_json_path=task_json_path,
                task_md_path=task_md_path,
            )
            self.write_exec_context(
                context_path=logs_dir / "worker.context.json",
                role="worker",
                prompt_path=prompt_path,
                schema_path=self.paths.worker_report_schema,
                sandbox="workspace-write",
                run_id=self.load_state().get("run_id"),
                task_id=task_id,
            )
            run_codex_exec(
                prompt_text=prompt_text,
                sandbox="workspace-write",
                schema_path=self.paths.worker_report_schema,
                output_file=output_file,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                cwd=self.paths.repo_root,
                timeout_sec=self.worker_timeout_sec,
            )
            report = read_json(output_file)
        except (
            PromptResolutionError,
            OSError,
            UnicodeDecodeError,
            CodexExecError,
            json.JSONDecodeError,
            FileNotFoundError,
        ) as exc:
            report = self.fallback_worker_report(task_id, str(exc))
            execution_error = str(exc)

        report["task_id"] = task_id
        report.setdefault("created_at", utc_now_iso())
        artifacts = report.setdefault("artifacts", {})
        artifacts.setdefault(
            "report_json", str((report_dir / "report.json").relative_to(self.paths.repo_root))
        )
        artifacts.setdefault(
            "report_md", str((report_dir / "report.md").relative_to(self.paths.repo_root))
        )
        artifacts.setdefault("worker_log", str(stdout_log.relative_to(self.paths.repo_root)))
        artifacts.setdefault("worker_stderr", str(stderr_log.relative_to(self.paths.repo_root)))
        artifacts.setdefault(
            "worker_evidence", str((report_dir / "evidence.json").relative_to(self.paths.repo_root))
        )

        write_json_atomic(report_dir / "report.json", report)
        write_text(report_dir / "report.md", render_report_markdown(report))
        return report, execution_error

    def execute_manager_review(self, task_id: str) -> tuple[dict[str, Any], str | None]:
        task_dir = self.paths.task_dir(task_id)
        report_dir = self.paths.report_dir(task_id)
        approval_dir = self.paths.approval_dir(task_id)
        logs_dir = approval_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        output_file = self.paths.tmp_dir / f"manager_review_{task_id}.json"
        stdout_log = logs_dir / "manager_review.jsonl"
        stderr_log = logs_dir / "manager_review.stderr.txt"

        review_error: str | None = None
        try:
            prompt_text, prompt_path = self.build_review_prompt(
                task_id=task_id,
                report_path=report_dir / "report.json",
                task_json_path=task_dir / "task.json",
                task_md_path=task_dir / "task.md",
            )
            self.write_exec_context(
                context_path=logs_dir / "manager_review.context.json",
                role="manager_review",
                prompt_path=prompt_path,
                schema_path=self.paths.manager_review_schema,
                sandbox="read-only",
                run_id=self.load_state().get("run_id"),
                task_id=task_id,
            )
            run_codex_exec(
                prompt_text=prompt_text,
                sandbox="read-only",
                schema_path=self.paths.manager_review_schema,
                output_file=output_file,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                cwd=self.paths.repo_root,
                timeout_sec=self.manager_timeout_sec,
            )
            decision = read_json(output_file)
        except (
            PromptResolutionError,
            OSError,
            UnicodeDecodeError,
            CodexExecError,
            json.JSONDecodeError,
            FileNotFoundError,
        ) as exc:
            decision = self.fallback_review(task_id, str(exc))
            review_error = str(exc)

        decision["task_id"] = task_id
        decision.setdefault("created_at", utc_now_iso())
        write_json_atomic(approval_dir / "decision.json", decision)
        write_text(approval_dir / "decision.md", render_decision_markdown(decision))
        return decision, review_error

    def save_decision_artifacts(self, task_id: str, decision: dict[str, Any]) -> None:
        approval_dir = self.paths.approval_dir(task_id)
        write_json_atomic(approval_dir / "decision.json", decision)
        write_text(approval_dir / "decision.md", render_decision_markdown(decision))

    def resolve_base_branch(self, requested: str | None) -> str:
        if requested and branch_exists(self.paths.repo_root, requested):
            return requested
        if branch_exists(self.paths.repo_root, "main"):
            return "main"
        return current_branch(self.paths.repo_root) or "main"

    def try_auto_merge(self, source_branch: str | None, base_branch: str) -> tuple[bool, str]:
        if source_branch is None or source_branch.strip() in {"", "unknown", "n/a"}:
            return False, "worker report does not provide a mergeable branch name"
        if not branch_exists(self.paths.repo_root, source_branch):
            return False, f"worker branch not found: {source_branch}"
        if not branch_exists(self.paths.repo_root, base_branch):
            return False, f"base branch not found: {base_branch}"
        if not working_tree_clean(self.paths.repo_root):
            return False, "working tree is not clean; auto-merge requires clean tree"

        current = current_branch(self.paths.repo_root) or base_branch
        if current != base_branch:
            checkout = checkout_branch(self.paths.repo_root, base_branch)
            if not checkout.ok:
                return False, f"checkout failed: {checkout.output}"

        merge = merge_ff(self.paths.repo_root, source_branch)
        if not merge.ok:
            return False, f"ff-merge failed: {merge.output}"
        return True, f"merged {source_branch} -> {base_branch}"

    def create_followup_task_from_decision(
        self,
        state: FlowState,
        *,
        current_task_id: str,
        decision: dict[str, Any],
    ) -> str:
        current_task = self.load_task_payload(current_task_id)
        if not current_task:
            raise ValueError(f"missing task payload for {current_task_id}")
        active = state.get("active_task") or {}
        current_revision = int(active.get("revision", 1))
        next_revision = current_revision + 1
        followup = self.synthesize_followup_task(
            current_task=current_task,
            current_task_id=current_task_id,
            decision=decision,
        )
        return self.save_task_payload(state, followup, revision=next_revision)

    def request_manager_followup_task(
        self,
        *,
        current_task_id: str,
        product_check_payload: dict[str, Any],
    ) -> dict[str, Any]:
        suggested_task_id = self.next_task_id_from(current_task_id)
        output_file = self.paths.tmp_dir / f"manager_plan_followup_{suggested_task_id}.json"
        stdout_log = self.paths.tmp_logs_dir / f"manager_plan_followup_{suggested_task_id}.jsonl"
        stderr_log = self.paths.tmp_logs_dir / f"manager_plan_followup_{suggested_task_id}.stderr.txt"
        prompt_text, prompt_path = self.build_manager_plan_prompt(
            current_task_id=current_task_id,
            suggested_task_id=suggested_task_id,
            product_check_path=str(self.paths.product_check_output.relative_to(self.paths.repo_root)),
            product_check_payload=product_check_payload,
        )

        run_id = self.load_state().get("run_id")
        self.write_exec_context(
            context_path=stdout_log.with_suffix(".context.json"),
            role="manager_plan",
            prompt_path=prompt_path,
            schema_path=self.paths.manager_plan_schema,
            sandbox="read-only",
            run_id=run_id if isinstance(run_id, str) else None,
            task_id=current_task_id,
        )
        run_codex_exec(
            prompt_text=prompt_text,
            sandbox="read-only",
            schema_path=self.paths.manager_plan_schema,
            output_file=output_file,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            cwd=self.paths.repo_root,
            timeout_sec=self.manager_timeout_sec,
        )
        plan = read_json(output_file)
        task_id, task_payload = self.extract_single_task_plan(plan)

        normalized_task_id = task_id
        if normalized_task_id == current_task_id or not re.match(
            r"^TSK-[0-9]{4}$", normalized_task_id
        ):
            normalized_task_id = suggested_task_id
        task_payload["task_id"] = normalized_task_id

        depends_on = task_payload.get("depends_on", [])
        if not isinstance(depends_on, list):
            depends_on = []
        depends_clean = [str(item) for item in depends_on if str(item).strip()]
        if current_task_id not in depends_clean:
            depends_clean.append(current_task_id)
        task_payload["depends_on"] = depends_clean
        return task_payload

    def create_followup_task_from_product_check(
        self,
        state: FlowState,
        *,
        current_task_id: str,
        product_check_payload: dict[str, Any],
    ) -> str:
        active = state.get("active_task") or {}
        current_revision = int(active.get("revision", 1))
        next_revision = current_revision + 1
        followup = self.request_manager_followup_task(
            current_task_id=current_task_id,
            product_check_payload=product_check_payload,
        )
        return self.save_task_payload(state, followup, revision=next_revision)

    def extract_accepted_ref_from_report(self, task_id: str) -> tuple[str | None, str | None]:
        report_path = self.paths.report_dir(task_id) / "report.json"
        report = read_json(report_path, default={})
        if not isinstance(report, dict):
            return None, None

        branch_raw = report.get("branch")
        branch = branch_raw.strip() if isinstance(branch_raw, str) and branch_raw.strip() else None

        commit = None
        commits = report.get("commits", [])
        if isinstance(commits, list):
            for item in reversed(commits):
                if not isinstance(item, dict):
                    continue
                sha = str(item.get("sha", "")).strip()
                if sha:
                    commit = sha
                    break

        return branch, commit

    def normalize_missing_requirements(self, payload: dict[str, Any]) -> list[str]:
        raw = payload.get("missing_requirements")
        if raw is None:
            raw = payload.get("missing", [])
        if not isinstance(raw, list):
            raw = [raw]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def normalize_unresolved_requirements(self, payload: dict[str, Any]) -> list[str]:
        raw = payload.get("unresolved_requirements")
        if raw is None:
            raw = payload.get("missing_requirements")
        if raw is None:
            raw = payload.get("missing", [])
        if not isinstance(raw, list):
            raw = [raw]
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return normalized

    def parse_product_check_coverage_pct(self, payload: dict[str, Any]) -> float:
        raw = payload.get("techspec_coverage_pct")
        if raw is None:
            missing_requirements = self.normalize_missing_requirements(payload)
            return 0.0 if missing_requirements else TECHSPEC_COVERAGE_TARGET_PCT
        try:
            coverage = float(raw)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(TECHSPEC_COVERAGE_TARGET_PCT, coverage))

    def parse_product_check_coverage_target_pct(self, payload: dict[str, Any]) -> float:
        raw = payload.get("techspec_coverage_target_pct")
        if raw is None:
            return TECHSPEC_COVERAGE_TARGET_PCT
        try:
            target = float(raw)
        except (TypeError, ValueError):
            return TECHSPEC_COVERAGE_TARGET_PCT
        return max(0.0, min(TECHSPEC_COVERAGE_TARGET_PCT, target))

    def evaluate_product_readiness(self, *, task_id: str) -> dict[str, Any]:
        state = self.load_state()
        run_id = str(state.get("run_id", "RUN-UNKNOWN")).strip() or "RUN-UNKNOWN"
        branch_ref, commit_ref = self.extract_accepted_ref_from_report(task_id)
        report_payload = read_json(self.paths.report_dir(task_id) / "report.json", default={})
        if not isinstance(report_payload, dict):
            report_payload = {}
        parse_error: str | None = None
        technical_error: str | None = None

        payload: dict[str, Any] = {
            "ok": False,
            "missing": [],
            "missing_requirements": [],
            "unresolved_requirements": [],
            "unresolved_requirements_count": 0,
            "machine_ready_for_completion": False,
            "notes": [],
            "checked": {},
            "techspec_coverage_pct": 0.0,
            "techspec_coverage_target_pct": TECHSPEC_COVERAGE_TARGET_PCT,
        }

        target_ref: str | None = None
        target_mode: str | None = None
        if commit_ref:
            rc_commit, _, _ = self.run_command(
                ["git", "rev-parse", "--verify", commit_ref],
                timeout_sec=60,
            )
            if rc_commit == 0:
                target_ref = commit_ref
                target_mode = "commit"

        if target_ref is None and branch_ref:
            if branch_exists(self.paths.repo_root, branch_ref):
                target_ref = branch_ref
                target_mode = "branch"

        if target_ref is None:
            payload["missing"] = ["accepted_ref_unavailable"]
            payload["missing_requirements"] = list(payload["missing"])
            payload["notes"].append(
                "Could not resolve accepted branch/commit from worker report for product_check."
            )
            technical_error = (
                "Accepted git ref is unavailable for product_check "
                f"(branch={branch_ref!r}, commit={commit_ref!r})."
            )
            exit_code = 2
            write_json_atomic(self.paths.product_check_output, payload)
            details_path = str(self.paths.product_check_output.relative_to(self.paths.repo_root))
            return {
                "ok": False,
                "payload": payload,
                "exit_code": exit_code,
                "details_path": details_path,
                "error": technical_error,
                    "task_id": task_id,
                }

        (
            worktree_dir,
            worktree_add_rc,
            worktree_add_failure_class,
        ) = self.add_product_check_worktree_with_retries(
            run_id=run_id,
            task_id=task_id,
            target_ref=target_ref,
            payload=payload,
        )
        if worktree_dir is None:
            payload["missing"] = ["accepted_ref_worktree_add_failed"]
            payload["missing_requirements"] = list(payload["missing"])
            payload["checked"]["accepted_ref_worktree_add_failure_class"] = (
                worktree_add_failure_class
            )
            payload["notes"].append(
                "checked_ref: "
                + (f"{target_mode}:{target_ref}" if target_mode and target_ref else "unknown")
            )
            payload["notes"].append(
                f"worktree_add_failure_class={worktree_add_failure_class}"
            )
            technical_error = (
                f"Failed to create worktree for product_check on ref: {target_ref}. "
                f"Failure classification: {worktree_add_failure_class}. "
                "Attempted recovery: git worktree prune --verbose; git worktree repair; retry git worktree add. "
                "Manual recovery: run `git worktree list --porcelain`, "
                "`git worktree prune --verbose`, `git worktree repair || true`, then "
                f"`python scripts/codexflow.py approve --kind task --task-id {task_id}` and rerun."
            )
            exit_code = worktree_add_rc
            write_json_atomic(self.paths.product_check_output, payload)
            details_path = str(self.paths.product_check_output.relative_to(self.paths.repo_root))
            return {
                "ok": False,
                "payload": payload,
                "exit_code": exit_code,
                "details_path": details_path,
                "error": technical_error,
                "task_id": task_id,
            }

        command = [
            sys.executable,
            str(self.paths.product_check_script),
            "--format",
            "json",
            "--repo-root",
            str(worktree_dir),
        ]
        exit_code = 2
        stdout = ""
        stderr = ""
        diagnostic_notes = list(payload.get("notes", []))
        try:
            exit_code, stdout, stderr = self.run_command(command, timeout_sec=180)
        finally:
            worktree_root = self.paths.tmp_dir / "worktrees"
            rc_remove, out_remove, err_remove = self.run_command(
                ["git", "worktree", "remove", "--force", str(worktree_dir)],
                timeout_sec=120,
            )
            if rc_remove != 0 and technical_error is None:
                technical_error = (
                    "Failed to remove temporary worktree after product_check: "
                    + (out_remove + "\n" + err_remove).strip()[:600]
                )
            if worktree_dir.exists():
                shutil.rmtree(worktree_dir, ignore_errors=True)
            current = worktree_dir.parent
            while current != worktree_root.parent:
                if current == worktree_root.parent:
                    break
                try:
                    current.rmdir()
                except OSError:
                    break
                if current == worktree_root:
                    break
                current = current.parent

        try:
            raw_payload = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as exc:
            raw_payload = {}
            parse_error = str(exc)

        if isinstance(raw_payload, dict) and raw_payload:
            payload = dict(raw_payload)
        else:
            payload = {
                "ok": False,
                "missing": ["product_check_output_unavailable"],
                "missing_requirements": ["product_check_output_unavailable"],
                "unresolved_requirements": ["product_check_output_unavailable"],
                "unresolved_requirements_count": 1,
                "machine_ready_for_completion": False,
                "notes": [],
                "checked": {},
                "techspec_coverage_pct": 0.0,
                "techspec_coverage_target_pct": TECHSPEC_COVERAGE_TARGET_PCT,
            }

        payload.setdefault("ok", False)
        payload.setdefault("missing", [])
        payload.setdefault("missing_requirements", [])
        payload.setdefault("unresolved_requirements", [])
        payload.setdefault("unresolved_requirements_count", 0)
        payload.setdefault("machine_ready_for_completion", False)
        payload.setdefault("notes", [])
        payload.setdefault("checked", {})
        if not isinstance(payload["missing"], list):
            payload["missing"] = [str(payload["missing"])]
        payload["missing_requirements"] = self.normalize_missing_requirements(payload)
        payload["unresolved_requirements"] = self.normalize_unresolved_requirements(payload)
        payload["unresolved_requirements_count"] = len(payload["unresolved_requirements"])
        if not isinstance(payload["notes"], list):
            payload["notes"] = [str(payload["notes"])]
        if diagnostic_notes:
            payload["notes"] = [*diagnostic_notes, *payload["notes"]]
        payload["techspec_coverage_pct"] = self.parse_product_check_coverage_pct(payload)
        payload["techspec_coverage_target_pct"] = self.parse_product_check_coverage_target_pct(
            payload
        )
        require_product_changes = os.getenv(
            "CODEXFLOW_REQUIRE_PRODUCT_CHANGES_ON_ACCEPT", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        if require_product_changes and not self.report_has_non_runtime_changes(report_payload):
            marker = "accepted_task_requires_product_changes"
            if marker not in payload["missing_requirements"]:
                payload["missing_requirements"].append(marker)
            if marker not in payload["unresolved_requirements"]:
                payload["unresolved_requirements"].append(marker)
            payload["unresolved_requirements_count"] = len(payload["unresolved_requirements"])
            payload["machine_ready_for_completion"] = False
            payload["ok"] = False
            payload["notes"].append(
                "Accepted task changed only runtime/report artifacts; "
                "machine completion is blocked until product code requirements progress."
            )
        if not isinstance(payload.get("machine_ready_for_completion"), bool):
            payload["machine_ready_for_completion"] = (
                bool(payload.get("ok"))
                and not self.normalize_missing_requirements(payload)
                and not self.normalize_unresolved_requirements(payload)
            )
        if parse_error:
            payload["notes"].append(f"stdout JSON parse error: {parse_error}")
        if stderr.strip():
            payload["notes"].append(f"stderr: {stderr.strip()[:500]}")
        payload["notes"].append(
            "checked_ref: "
            + (f"{target_mode}:{target_ref}" if target_mode and target_ref else "unknown")
        )

        write_json_atomic(self.paths.product_check_output, payload)
        details_path = str(self.paths.product_check_output.relative_to(self.paths.repo_root))

        if parse_error and technical_error is None:
            technical_error = "Product check did not return parseable JSON."
        elif exit_code not in {0, 1} and technical_error is None:
            technical_error = (
                f"Product check command failed with exit_code={exit_code} "
                f"(expected 0 or 1)."
            )

        ok = bool(payload.get("ok")) and exit_code == 0 and technical_error is None
        return {
            "ok": ok,
            "payload": payload,
            "exit_code": exit_code,
            "details_path": details_path,
            "error": technical_error,
            "task_id": task_id,
        }

    def cmd_run(self, *, auto_merge: bool = False, base_branch: str | None = None) -> int:
        with file_lock(self.paths.lock_file):
            state = self.load_state()

            while True:
                phase = state.get("phase")
                task_id = self.active_task_id(state)

                if phase == "FAILED":
                    self.save_state(state)
                    print("State is FAILED due to dispatcher technical error. Run reset.")
                    return 1

                if phase in {"STOPPED", "COMPLETED"}:
                    self.save_state(state)
                    print(f"Flow is {phase}.")
                    return 0

                if phase == "INIT":
                    self.save_state(state)
                    print("State is INIT. Run `python scripts/codexflow.py plan` first.")
                    return 1

                if phase == "WAIT_PLAN_APPROVAL":
                    marker = self.paths.plan_approvals_dir / "approved.marker"
                    if not marker.exists():
                        self.save_state(state)
                        print("Need plan approval: run `python scripts/codexflow.py approve --kind plan`.")
                        return 0
                    state["phase"] = "TASK_READY"
                    state["approval"] = {
                        "required": False,
                        "kind": None,
                        "reason": None,
                        "details_paths": [],
                    }
                    self.append_history(state, "plan_marker_detected", run_id=state["run_id"])
                    self.save_state(state)
                    continue

                if task_id is None:
                    state["phase"] = "COMPLETED"
                    state["approval"] = {
                        "required": False,
                        "kind": None,
                        "reason": None,
                        "details_paths": [],
                    }
                    self.append_history(state, "run_complete", run_id=state["run_id"])
                    self.save_state(state)
                    print("No active task remains. COMPLETED.")
                    return 0

                if phase == "WAIT_TASK_APPROVAL":
                    marker = self.paths.approval_dir(task_id) / "approved.marker"
                    if not marker.exists():
                        self.save_state(state)
                        print(
                            "Need task approval: "
                            f"run `python scripts/codexflow.py approve --kind task --task-id {task_id}`."
                        )
                        return 0
                    state["phase"] = "TASK_READY"
                    state["approval"] = {
                        "required": False,
                        "kind": None,
                        "reason": None,
                        "details_paths": [],
                    }
                    self.append_history(state, "task_marker_detected", task_id=task_id)
                    self.save_state(state)
                    continue

                if phase not in {"TASK_READY", "WORKER_RUNNING", "REVIEW_RUNNING"}:
                    state["phase"] = "FAILED"
                    self.append_history(state, "invalid_phase", phase=phase)
                    self.save_state(state)
                    print("Invalid phase encountered; moved to FAILED.")
                    return 1

                previous_commit = current_commit(self.paths.repo_root)
                worker_logs = self.paths.report_dir(task_id) / "logs"
                print(
                    f"Starting worker for {task_id} "
                    f"(logs: {worker_logs.relative_to(self.paths.repo_root)})"
                )
                state["phase"] = "WORKER_RUNNING"
                self.save_state(state)
                report, worker_error = self.execute_worker(task_id)

                progress = self.detect_progress(report, previous_commit)
                state["stall_count"] = 0 if progress else state["stall_count"] + 1

                if worker_error:
                    artifacts = report.get("artifacts", {})
                    details = [
                        value
                        for value in [
                            artifacts.get("worker_log"),
                            artifacts.get("worker_stderr"),
                            artifacts.get("report_json"),
                        ]
                        if isinstance(value, str) and value
                    ]
                    self.escalate_task_approval(
                        state,
                        task_id=task_id,
                        reason=f"Worker technical failure: {worker_error}",
                        details_paths=details,
                    )
                    self.save_state(state)
                    print(
                        f"Worker execution escalated for {task_id}. "
                        f"Run `python scripts/codexflow.py approve --kind task --task-id {task_id}`."
                    )
                    return 0

                review_logs = self.paths.approval_dir(task_id) / "logs"
                print(
                    f"Starting manager review for {task_id} "
                    f"(logs: {review_logs.relative_to(self.paths.repo_root)})"
                )
                state["phase"] = "REVIEW_RUNNING"
                self.save_state(state)
                decision, review_error = self.execute_manager_review(task_id)

                if review_error:
                    self.escalate_task_approval(
                        state,
                        task_id=task_id,
                        reason=f"Manager review technical failure: {review_error}",
                        details_paths=[
                            str((self.paths.approval_dir(task_id) / "decision.json").relative_to(self.paths.repo_root))
                        ],
                    )
                    self.save_state(state)
                    print(
                        f"Manager review escalated for {task_id}. "
                        f"Run `python scripts/codexflow.py approve --kind task --task-id {task_id}`."
                    )
                    return 0

                decision, quality_errors = self.enforce_accept_decision_quality(
                    task_id=task_id,
                    decision=decision,
                    report=report,
                )
                if quality_errors:
                    self.save_decision_artifacts(task_id, decision)
                    self.append_history(
                        state,
                        "review_decision_downgraded",
                        task_id=task_id,
                        errors=quality_errors,
                    )

                verdict = decision.get("verdict")
                full_techspec_complete = self.decision_full_techspec_complete(decision)
                artifact_checks = self.normalize_decision_artifact_checks(decision)
                print(f"Decision for {task_id}: {verdict}")
                self.append_history(
                    state,
                    "task_reviewed",
                    task_id=task_id,
                    verdict=verdict,
                    full_techspec_complete=full_techspec_complete,
                    artifact_checks_count=len(artifact_checks),
                )
                state["iteration"] = state["iteration"] + 1

                if decision.get("approval_required") is True and verdict != "STOP":
                    self.escalate_task_approval(
                        state,
                        task_id=task_id,
                        reason=str(decision.get("rationale", "Manager requested task approval.")),
                        details_paths=[
                            str(
                                (self.paths.approval_dir(task_id) / "decision.json").relative_to(
                                    self.paths.repo_root
                                )
                            )
                        ],
                    )
                    self.save_state(state)
                    print(
                        f"Task {task_id} requires human approval. "
                        f"Run `python scripts/codexflow.py approve --kind task --task-id {task_id}`."
                    )
                    return 0

                if verdict == "ACCEPT":
                    if auto_merge:
                        merge_base = self.resolve_base_branch(base_branch)
                        merge_ok, merge_message = self.try_auto_merge(
                            report.get("branch"),
                            merge_base,
                        )
                        if merge_ok:
                            print(f"Auto-merge: {merge_message}")
                        else:
                            self.escalate_task_approval(
                                state,
                                task_id=task_id,
                                reason=f"Auto-merge failed: {merge_message}",
                                details_paths=[
                                    str((self.paths.approval_dir(task_id) / "decision.md").relative_to(self.paths.repo_root))
                                ],
                            )
                            self.save_state(state)
                            print(
                                f"Auto-merge blocked for {task_id}: {merge_message}. "
                                f"Run `python scripts/codexflow.py approve --kind task --task-id {task_id}` "
                                "after manual merge."
                            )
                            return 0

                    state["last_good_commit"] = current_commit(self.paths.repo_root)
                    state["attempts"].pop(task_id, None)
                    product_check = self.evaluate_product_readiness(task_id=task_id)
                    payload = product_check.get("payload", {})
                    if not isinstance(payload, dict):
                        payload = {}
                    missing_requirements = self.normalize_missing_requirements(payload)
                    unresolved_requirements = self.normalize_unresolved_requirements(payload)
                    coverage_pct = self.parse_product_check_coverage_pct(payload)
                    coverage_target_pct = self.parse_product_check_coverage_target_pct(payload)
                    machine_ready_flag = payload.get("machine_ready_for_completion")
                    machine_ready_for_completion = (
                        bool(product_check.get("ok"))
                        and coverage_pct >= coverage_target_pct
                        and not missing_requirements
                        and not unresolved_requirements
                        and (machine_ready_flag is True if isinstance(machine_ready_flag, bool) else True)
                    )
                    self.append_history(
                        state,
                        "product_check",
                        task_id=task_id,
                        ok=bool(product_check.get("ok")),
                        missing_count=len(missing_requirements),
                        missing_requirements=missing_requirements,
                        unresolved_count=len(unresolved_requirements),
                        unresolved_requirements=unresolved_requirements,
                        techspec_coverage_pct=coverage_pct,
                        techspec_coverage_target_pct=coverage_target_pct,
                        full_techspec_complete=full_techspec_complete,
                        machine_ready_for_completion=machine_ready_for_completion,
                        details_path=product_check.get("details_path"),
                        exit_code=product_check.get("exit_code"),
                    )

                    check_error = product_check.get("error")
                    if isinstance(check_error, str) and check_error:
                        details = [
                            value
                            for value in [
                                str(product_check.get("details_path", "")),
                                str(
                                    (
                                        self.paths.approval_dir(task_id) / "decision.json"
                                    ).relative_to(self.paths.repo_root)
                                ),
                            ]
                            if value
                        ]
                        self.escalate_task_approval(
                            state,
                            task_id=task_id,
                            reason=check_error,
                            details_paths=details,
                        )
                        self.save_state(state)
                        return 0

                    if (
                        machine_ready_for_completion
                    ):
                        self.clear_active_task(state)
                        state["phase"] = "COMPLETED"
                        state["approval"] = {
                            "required": False,
                            "kind": None,
                            "reason": None,
                            "details_paths": [],
                        }
                        self.append_history(state, "run_complete", run_id=state["run_id"])
                        self.save_state(state)
                        print("Product check passed. COMPLETED.")
                        return 0

                    if not machine_ready_for_completion:
                        payload_notes = payload.setdefault("notes", [])
                        if not isinstance(payload_notes, list):
                            payload_notes = [str(payload_notes)]
                        payload_notes.append(
                            "Product check is not machine-ready for completion; creating follow-up task."
                        )
                        if unresolved_requirements:
                            preview = ", ".join(unresolved_requirements[:5])
                            payload_notes.append(
                                f"Unresolved requirements: {preview}"
                            )
                        payload["notes"] = payload_notes

                    if (
                        state["stall_count"] > 0
                        and state["stall_count"] >= self.max_attempts_worker
                    ):
                        details = [
                            value
                            for value in [
                                str(product_check.get("details_path", "")),
                                str(
                                    (
                                        self.paths.approval_dir(task_id) / "decision.json"
                                    ).relative_to(self.paths.repo_root)
                                ),
                            ]
                            if value
                        ]
                        self.escalate_task_approval(
                            state,
                            task_id=task_id,
                            reason=(
                                "Product check still failing without repo progress "
                                f"(stall_count={state['stall_count']})."
                            ),
                            details_paths=details,
                        )
                        self.save_state(state)
                        return 0

                    try:
                        next_task_id = self.create_followup_task_from_product_check(
                            state,
                            current_task_id=task_id,
                            product_check_payload=payload if isinstance(payload, dict) else {},
                        )
                    except Exception as exc:  # noqa: BLE001
                        details = [
                            value
                            for value in [
                                str(product_check.get("details_path", "")),
                                str(
                                    (
                                        self.paths.approval_dir(task_id) / "decision.json"
                                    ).relative_to(self.paths.repo_root)
                                ),
                            ]
                            if value
                        ]
                        self.escalate_task_approval(
                            state,
                            task_id=task_id,
                            reason=f"Could not build next task after product_check failure: {exc}",
                            details_paths=details,
                        )
                        self.save_state(state)
                        return 0

                    state["phase"] = "TASK_READY"
                    self.append_history(
                        state,
                        "followup_task_created",
                        previous_task_id=task_id,
                        task_id=next_task_id,
                        source_verdict="ACCEPT_PRODUCT_CHECK_FAIL",
                    )
                    self.save_state(state)
                    continue

                if verdict == "REWORK":
                    attempts = state["attempts"].get(task_id, 0) + 1
                    state["attempts"][task_id] = attempts

                    required_changes = self.decision_required_changes(decision)
                    if not required_changes:
                        self.escalate_task_approval(
                            state,
                            task_id=task_id,
                            reason=(
                                "Manager returned REWORK without explicit required_changes. "
                                "Human task approval required."
                            ),
                            details_paths=[
                                str(
                                    (self.paths.approval_dir(task_id) / "decision.json").relative_to(
                                        self.paths.repo_root
                                    )
                                )
                            ],
                        )
                        self.save_state(state)
                        return 0

                    if state["stall_count"] > 0 and state["stall_count"] >= self.max_attempts_worker:
                        self.escalate_task_approval(
                            state,
                            task_id=task_id,
                            reason=(
                                "No progress detected across iterations "
                                f"(stall_count={state['stall_count']})."
                            ),
                            details_paths=[
                                str((self.paths.report_dir(task_id) / "report.json").relative_to(self.paths.repo_root)),
                                str((self.paths.approval_dir(task_id) / "decision.json").relative_to(self.paths.repo_root)),
                            ],
                        )
                        self.save_state(state)
                        return 0

                    if attempts >= self.max_attempts_worker:
                        self.escalate_task_approval(
                            state,
                            task_id=task_id,
                            reason=(
                                "Max REWORK iterations reached "
                                f"({self.max_attempts_worker}); human approval required."
                            ),
                            details_paths=[
                                str((self.paths.report_dir(task_id) / "report.json").relative_to(self.paths.repo_root)),
                                str((self.paths.approval_dir(task_id) / "decision.json").relative_to(self.paths.repo_root)),
                            ],
                        )
                        self.save_state(state)
                        return 0

                    try:
                        next_task_id = self.create_followup_task_from_decision(
                            state,
                            current_task_id=task_id,
                            decision=decision,
                        )
                    except Exception as exc:  # noqa: BLE001
                        self.escalate_task_approval(
                            state,
                            task_id=task_id,
                            reason=f"Cannot synthesize follow-up task for REWORK: {exc}",
                            details_paths=[
                                str((self.paths.approval_dir(task_id) / "decision.json").relative_to(self.paths.repo_root))
                            ],
                        )
                        self.save_state(state)
                        return 0

                    state["phase"] = "TASK_READY"
                    self.append_history(
                        state,
                        "followup_task_created",
                        previous_task_id=task_id,
                        task_id=next_task_id,
                        source_verdict="REWORK",
                    )
                    self.save_state(state)
                    continue

                if verdict == "NEEDS_HUMAN_APPROVAL":
                    self.escalate_task_approval(
                        state,
                        task_id=task_id,
                        reason=str(decision.get("rationale", "Manager requested human approval.")),
                        details_paths=[
                            str((self.paths.approval_dir(task_id) / "decision.json").relative_to(self.paths.repo_root))
                        ],
                    )
                    self.save_state(state)
                    print(
                        f"Task {task_id} requires human approval. "
                        f"Run `python scripts/codexflow.py approve --kind task --task-id {task_id}`."
                    )
                    return 0

                if verdict == "STOP":
                    state["phase"] = "STOPPED"
                    state["approval"] = {
                        "required": False,
                        "kind": None,
                        "reason": None,
                        "details_paths": [],
                    }
                    self.append_history(state, "stopped", task_id=task_id)
                    self.save_state(state)
                    print(f"Manager stopped the flow for {task_id}.")
                    return 0

                self.escalate_task_approval(
                    state,
                    task_id=task_id,
                    reason=f"Unknown manager verdict: {verdict}",
                    details_paths=[
                        str((self.paths.approval_dir(task_id) / "decision.json").relative_to(self.paths.repo_root))
                    ],
                )
                self.save_state(state)
                return 0

    def next_action(self, state: FlowState) -> str:
        phase = state.get("phase")
        task_id = self.active_task_id(state)
        if phase == "INIT":
            return "Run `python scripts/codexflow.py plan`."
        if phase == "WAIT_PLAN_APPROVAL":
            return "Run `python scripts/codexflow.py approve --kind plan`."
        if phase == "WAIT_TASK_APPROVAL" and task_id:
            return (
                "Run `python scripts/codexflow.py approve --kind task --task-id "
                f"{task_id}`."
            )
        if phase == "TASK_READY":
            return "Run `python scripts/codexflow.py run`."
        if phase in {"WORKER_RUNNING", "REVIEW_RUNNING"}:
            return "Dispatcher is mid-iteration; rerun `python scripts/codexflow.py run` if interrupted."
        if phase == "COMPLETED":
            return "No action required. Flow is complete."
        if phase == "STOPPED":
            return "Flow stopped by manager. Inspect decisions and approve task if continuing."
        if phase == "FAILED":
            return "Run `python scripts/codexflow.py reset` to recover, then rerun plan/run."
        return "Unknown phase; inspect state manually."

    def cmd_status(self) -> int:
        state = self.load_state() if self.paths.state_file.exists() else bootstrap_state()
        print(json.dumps(state, ensure_ascii=False, indent=2))
        print()
        print(f"Next: {self.next_action(state)}")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CodexFlow dispatcher")
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root (defaults to auto-discovery from cwd).",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=None,
        help="Override both manager and worker timeouts for each codex exec call.",
    )
    parser.add_argument(
        "--manager-timeout-sec",
        type=int,
        default=DEFAULT_MANAGER_TIMEOUT_SEC,
        help="Timeout for manager plan/review codex exec calls.",
    )
    parser.add_argument(
        "--worker-timeout-sec",
        type=int,
        default=DEFAULT_WORKER_TIMEOUT_SEC,
        help="Timeout for worker codex exec calls.",
    )
    parser.add_argument(
        "--max-attempts-worker",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS_WORKER,
        help="Maximum rolling REWORK/stall iterations before escalation to task approval.",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize .codexflow state and folders")
    init.add_argument("--force", action="store_true", help="Overwrite state.json")

    reset = sub.add_parser("reset", help="Archive artifacts and recover from FAILED/dirty state")
    reset.add_argument(
        "--keep-plan",
        action="store_true",
        help="Keep existing plan/tasks and reset phase to WAIT_PLAN_APPROVAL.",
    )

    sub.add_parser("preflight", help="Run codexflow readiness checks and write report")
    sub.add_parser("plan", help="Generate manager plan via codex exec")

    approve = sub.add_parser("approve", help="Create approval markers")
    approve.add_argument("--kind", required=True, choices=["plan", "task"])
    approve.add_argument("--task-id", default=None)

    run = sub.add_parser("run", help="Execute main manager<->worker cycle")
    run.add_argument(
        "--auto-merge",
        dest="auto_merge",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable/disable ff-only merge of worker branch into base branch on ACCEPT. "
            "Default uses CODEXFLOW_AUTO_MERGE env var, otherwise false."
        ),
    )
    run.add_argument(
        "--base-branch",
        default=None,
        help="Base branch for --auto-merge (default: main if available).",
    )
    sub.add_parser("status", help="Show state and next action")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve() if args.repo_root else None
    manager_timeout = args.manager_timeout_sec
    worker_timeout = args.worker_timeout_sec
    if args.timeout_sec is not None:
        manager_timeout = args.timeout_sec
        worker_timeout = args.timeout_sec
    paths = FlowPaths.from_start(start=root)
    dispatcher = CodexFlowDispatcher(
        paths=paths,
        manager_timeout_sec=manager_timeout,
        worker_timeout_sec=worker_timeout,
        max_attempts_worker=args.max_attempts_worker,
    )

    try:
        if args.command == "init":
            return dispatcher.cmd_init(force=args.force)
        if args.command == "reset":
            return dispatcher.cmd_reset(keep_plan=args.keep_plan)
        if args.command == "preflight":
            return dispatcher.cmd_preflight()
        if args.command == "plan":
            return dispatcher.cmd_plan()
        if args.command == "approve":
            return dispatcher.cmd_approve(kind=args.kind, task_id=args.task_id)
        if args.command == "run":
            auto_merge = args.auto_merge
            if auto_merge is None:
                auto_merge = env_flag_enabled("CODEXFLOW_AUTO_MERGE")
            return dispatcher.cmd_run(auto_merge=bool(auto_merge), base_branch=args.base_branch)
        if args.command == "status":
            return dispatcher.cmd_status()
    except LockError as exc:
        print(str(exc))
        return 2

    parser.print_help()
    return 2
