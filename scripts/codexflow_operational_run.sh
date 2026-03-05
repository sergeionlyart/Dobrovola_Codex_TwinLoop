#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MANAGER_TIMEOUT_SEC="${CODEXFLOW_OP_MANAGER_TIMEOUT_SEC:-900}"
WORKER_TIMEOUT_SEC="${CODEXFLOW_OP_WORKER_TIMEOUT_SEC:-7200}"
MAX_ATTEMPTS_WORKER="${CODEXFLOW_OP_MAX_ATTEMPTS_WORKER:-4}"
MAX_PLAN_RETRIES="${CODEXFLOW_OP_MAX_PLAN_RETRIES:-3}"
MAX_RUN_CYCLES="${CODEXFLOW_OP_MAX_RUN_CYCLES:-200}"
RETRY_BACKOFF_SEC="${CODEXFLOW_OP_RETRY_BACKOFF_SEC:-20}"
AUTO_APPROVE_TECHNICAL_WAIT="${CODEXFLOW_OP_AUTO_APPROVE_TECHNICAL_WAIT:-1}"
MAX_SUPERVISOR_CYCLES="${CODEXFLOW_OP_MAX_SUPERVISOR_CYCLES:-0}"
PYTHON_BIN="${CODEXFLOW_OP_PYTHON_BIN:-python}"
BASE_BRANCH="${CODEXFLOW_OP_BASE_BRANCH:-codex/product/integration}"

INCIDENTS_LOG="${CODEXFLOW_OP_INCIDENTS_LOG:-docs/reports/autocoding_runbook_incidents.md}"

export CODEXFLOW_LOCK_TTL_SEC="${CODEXFLOW_LOCK_TTL_SEC:-28800}"
export CODEXFLOW_CODEX_EXEC_MAX_ATTEMPTS="${CODEXFLOW_CODEX_EXEC_MAX_ATTEMPTS:-2}"
export CODEXFLOW_CODEX_EXEC_RETRY_DELAY_SEC="${CODEXFLOW_CODEX_EXEC_RETRY_DELAY_SEC:-1}"

mkdir -p "$(dirname "$INCIDENTS_LOG")"
mkdir -p ".codexflow/_tmp"

utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

phase_of() {
  "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
state = json.loads(Path(".codexflow/state.json").read_text(encoding="utf-8"))
print(state.get("phase", ""))
PY
}

run_id_of() {
  "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
state = json.loads(Path(".codexflow/state.json").read_text(encoding="utf-8"))
print(state.get("run_id", ""))
PY
}

active_task_id_of() {
  "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
state = json.loads(Path(".codexflow/state.json").read_text(encoding="utf-8"))
active = state.get("active_task") or {}
print(active.get("task_id", ""))
PY
}

approval_reason_of() {
  "$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
state = json.loads(Path(".codexflow/state.json").read_text(encoding="utf-8"))
approval = state.get("approval") or {}
print((approval.get("reason") or "").strip())
PY
}

current_branch_of() {
  git rev-parse --abbrev-ref HEAD 2>/dev/null || true
}

append_incident() {
  local summary="$1"
  local artifacts="$2"
  local cause="$3"
  local fix="$4"
  local result="$5"
  local ts run_id phase task_id
  ts="$(utc_now)"
  run_id="$(run_id_of 2>/dev/null || true)"
  phase="$(phase_of 2>/dev/null || true)"
  task_id="$(active_task_id_of 2>/dev/null || true)"
  {
    echo ""
    echo "## ${ts} — incident"
    echo "- run_id: ${run_id}"
    echo "- phase: ${phase}"
    echo "- task_id: ${task_id}"
    echo "- error: ${summary}"
    echo "- artifacts: ${artifacts}"
    echo "- root_cause: ${cause}"
    echo "- fix_applied: ${fix}"
    echo "- result: ${result}"
  } >> "$INCIDENTS_LOG"
}

is_technical_wait_reason() {
  local reason="$1"
  local lower
  lower="$(printf '%s' "$reason" | tr '[:upper:]' '[:lower:]')"
  if [[ "$lower" == *"worker technical failure"* ]]; then return 0; fi
  if [[ "$lower" == *"manager review technical failure"* ]]; then return 0; fi
  if [[ "$lower" == *"failed to create worktree for product_check"* ]]; then return 0; fi
  if [[ "$lower" == *"lock already exists"* ]]; then return 0; fi
  if [[ "$lower" == *"codex exec timed out"* ]]; then return 0; fi
  if [[ "$lower" == *"codex exec failed with return code"* ]]; then return 0; fi
  if [[ "$lower" == *"dirty working tree before branch switch"* ]]; then return 0; fi
  return 1
}

ensure_base_branch() {
  local current_branch
  current_branch="$(current_branch_of)"
  if [[ "$current_branch" == "$BASE_BRANCH" ]]; then
    return 0
  fi

  if ! git diff --quiet || ! git diff --cached --quiet; then
    append_incident \
      "cannot checkout base branch due to dirty working tree" \
      ".codexflow/state.json" \
      "operational wrapper started outside clean branch context" \
      "clean/stash local changes and restart" \
      "retry_scheduled"
    return 1
  fi

  if ! git show-ref --verify --quiet "refs/heads/$BASE_BRANCH"; then
    if git ls-remote --exit-code --heads origin "$BASE_BRANCH" >/dev/null 2>&1; then
      git fetch origin "$BASE_BRANCH:$BASE_BRANCH"
    else
      append_incident \
        "base branch missing" \
        ".git/refs/heads,.codexflow/state.json" \
        "configured CODEXFLOW_OP_BASE_BRANCH is absent locally and on origin" \
        "create/push base branch and rerun" \
        "retry_scheduled"
      return 1
    fi
  fi

  git checkout "$BASE_BRANCH"
}

ensure_preflight() {
  if "$PYTHON_BIN" scripts/codexflow.py preflight; then
    return 0
  fi
  append_incident \
    "preflight failed" \
    "docs/codexflow_preflight_report.md,.codexflow/state.json" \
    "readiness gate failed before operational run" \
    "no auto-fix; backoff and retry cycle" \
    "retry_scheduled"
  return 1
}

ensure_planned_and_approved() {
  local phase attempt plan_ok
  phase="$(phase_of)"
  if [[ "$phase" == "WAIT_PLAN_APPROVAL" ]]; then
    "$PYTHON_BIN" scripts/codexflow.py approve --kind plan
    return 0
  fi
  if [[ "$phase" != "INIT" ]]; then
    return 0
  fi

  plan_ok=0
  for attempt in $(seq 1 "$MAX_PLAN_RETRIES"); do
    echo "[op] plan attempt $attempt/$MAX_PLAN_RETRIES"
    if "$PYTHON_BIN" scripts/codexflow.py --manager-timeout-sec "$MANAGER_TIMEOUT_SEC" plan; then
      plan_ok=1
      break
    fi
    sleep "$attempt"
  done

  if [[ "$plan_ok" -ne 1 ]]; then
    append_incident \
      "plan failed after retries" \
      ".codexflow/_tmp/logs/manager_plan_diagnostics.json,.codexflow/state.json" \
      "manager plan output invalid or timeout across retries" \
      "none (operator intervention may be required)" \
      "retry_scheduled"
    return 1
  fi

  if ! "$PYTHON_BIN" scripts/codexflow.py approve --kind plan; then
    append_incident \
      "plan approval failed" \
      ".codexflow/state.json,.codexflow/plan.json" \
      "plan artifacts missing or inconsistent at approval step" \
      "none (operator intervention may be required)" \
      "retry_scheduled"
    return 1
  fi
  return 0
}

run_supervisor_cycle() {
  local cycle phase task_id reason
  for cycle in $(seq 1 "$MAX_RUN_CYCLES"); do
    echo "[op] run cycle $cycle/$MAX_RUN_CYCLES"
    "$PYTHON_BIN" scripts/codexflow.py \
      --manager-timeout-sec "$MANAGER_TIMEOUT_SEC" \
      --worker-timeout-sec "$WORKER_TIMEOUT_SEC" \
      --max-attempts-worker "$MAX_ATTEMPTS_WORKER" \
      run || true

    phase="$(phase_of)"
    echo "[op] phase=$phase"

    if [[ "$phase" == "COMPLETED" ]]; then
      echo "[op] workflow COMPLETED"
      "$PYTHON_BIN" scripts/codexflow.py status || true
      return 0
    fi

    if [[ "$phase" == "WAIT_TASK_APPROVAL" ]]; then
      task_id="$(active_task_id_of)"
      reason="$(approval_reason_of)"
      echo "[op] WAIT_TASK_APPROVAL task_id=$task_id"
      echo "[op] reason: $reason"
      if [[ -z "$task_id" ]]; then
        append_incident \
          "WAIT_TASK_APPROVAL without active task id" \
          ".codexflow/state.json" \
          "state inconsistency" \
          "none" \
          "stopped"
        return 2
      fi
      if [[ "$AUTO_APPROVE_TECHNICAL_WAIT" == "1" ]] && is_technical_wait_reason "$reason"; then
        echo "[op] auto-approving technical WAIT for $task_id"
        if ! "$PYTHON_BIN" scripts/codexflow.py approve --kind task --task-id "$task_id"; then
          append_incident \
            "auto-approve task failed" \
            ".codexflow/state.json,.codexflow/approvals/${task_id}/decision.json" \
            "approve command failed for technical WAIT task" \
            "none" \
            "retry_scheduled"
          return 1
        fi
        continue
      fi

      append_incident \
        "manual task approval required" \
        ".codexflow/state.json,.codexflow/approvals/${task_id}/decision.json" \
        "$reason" \
        "auto-approve skipped by policy or non-technical WAIT" \
        "stopped"
      return 2
    fi

    if [[ "$phase" == "WAIT_PLAN_APPROVAL" ]]; then
      echo "[op] approving plan marker"
      if ! "$PYTHON_BIN" scripts/codexflow.py approve --kind plan; then
        append_incident \
          "WAIT_PLAN_APPROVAL auto-approve failed" \
          ".codexflow/state.json,.codexflow/plan.json" \
          "plan marker approval failed in run loop" \
          "none" \
          "retry_scheduled"
        return 1
      fi
      continue
    fi

    if [[ "$phase" == "INIT" ]]; then
      append_incident \
        "phase returned to INIT during run loop" \
        ".codexflow/state.json" \
        "run interrupted before plan approval completion" \
        "restart plan sequence" \
        "retry_scheduled"
      return 1
    fi

    if [[ "$phase" == "FAILED" || "$phase" == "STOPPED" ]]; then
      append_incident \
        "terminal phase reached" \
        ".codexflow/state.json" \
        "dispatcher moved to terminal phase" \
        "none" \
        "retry_scheduled"
      return 1
    fi
  done

  append_incident \
    "max run cycles reached without completion" \
    ".codexflow/state.json" \
    "loop did not converge within configured cycle budget" \
    "increase CODEXFLOW_OP_MAX_RUN_CYCLES or inspect incidents" \
    "retry_scheduled"
  return 1
}

supervisor_idx=0
while :; do
  supervisor_idx=$((supervisor_idx + 1))
  if [[ "$MAX_SUPERVISOR_CYCLES" -gt 0 && "$supervisor_idx" -gt "$MAX_SUPERVISOR_CYCLES" ]]; then
    echo "[op] max supervisor cycles reached: $MAX_SUPERVISOR_CYCLES"
    exit 1
  fi

  echo "[op] supervisor cycle $supervisor_idx (run_id=$(run_id_of 2>/dev/null || true))"
  echo "[op] base_branch=$BASE_BRANCH current_branch=$(current_branch_of)"

  if ! ensure_base_branch; then
    sleep "$RETRY_BACKOFF_SEC"
    continue
  fi

  if ! ensure_preflight; then
    sleep "$RETRY_BACKOFF_SEC"
    continue
  fi

  if ! ensure_planned_and_approved; then
    sleep "$RETRY_BACKOFF_SEC"
    continue
  fi

  rc=0
  if run_supervisor_cycle; then
    exit 0
  else
    rc=$?
  fi

  if [[ "$rc" -eq 2 ]]; then
    exit 2
  fi

  sleep "$RETRY_BACKOFF_SEC"
done
