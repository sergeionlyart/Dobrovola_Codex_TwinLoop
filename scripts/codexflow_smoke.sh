#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

MANAGER_TIMEOUT_SEC="${CODEXFLOW_SMOKE_MANAGER_TIMEOUT_SEC:-300}"
WORKER_TIMEOUT_SEC="${CODEXFLOW_SMOKE_WORKER_TIMEOUT_SEC:-1800}"
BASE_BRANCH="${CODEXFLOW_SMOKE_BASE_BRANCH:-main}"

printf '\n[1/8] ruff\n'
ruff check .

printf '\n[2/8] pytest\n'
pytest -q

printf '\n[3/8] reset\n'
python scripts/codexflow.py reset

printf '\n[4/8] preflight\n'
python scripts/codexflow.py preflight

printf '\n[5/8] plan (manager timeout: %ss)\n' "$MANAGER_TIMEOUT_SEC"
python scripts/codexflow.py --manager-timeout-sec "$MANAGER_TIMEOUT_SEC" plan

printf '\n[6/8] approve plan\n'
python scripts/codexflow.py approve --kind plan

printf '\n[7/8] run (manager timeout: %ss, worker timeout: %ss, base branch: %s)\n' \
  "$MANAGER_TIMEOUT_SEC" "$WORKER_TIMEOUT_SEC" "$BASE_BRANCH"
python scripts/codexflow.py \
  --manager-timeout-sec "$MANAGER_TIMEOUT_SEC" \
  --worker-timeout-sec "$WORKER_TIMEOUT_SEC" \
  run --base-branch "$BASE_BRANCH"

printf '\n[8/8] status\n'
python scripts/codexflow.py status

printf '\n[git] status --porcelain=v1\n'
git status --porcelain=v1

printf '\n[artifacts] key files\n'
ls -la \
  .codexflow/plan.json \
  .codexflow/_tmp/product_check.json \
  2>/dev/null || true

printf '\n[artifacts] task reports\n'
find .codexflow/reports -maxdepth 3 -type f \( -name report.json -o -name report.md \) 2>/dev/null | sort || true

printf '\n[artifacts] manager decisions\n'
find .codexflow/approvals -maxdepth 3 -type f \( -name decision.json -o -name decision.md \) 2>/dev/null | sort || true

printf '\n[artifacts] context sidecars\n'
find .codexflow -maxdepth 6 -type f -name "*.context.json" 2>/dev/null | sort || true
