from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class PromptResolutionError(RuntimeError):
    """Raised when prompt path resolution fails."""


def discover_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate
        if (candidate / "AGENTS.md").exists() and (candidate / "docs").exists():
            return candidate
    return current


@dataclass(frozen=True)
class FlowPaths:
    repo_root: Path

    def _normalize_prompt_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (self.repo_root / candidate).resolve()
        return candidate

    def _resolve_prompt_path(
        self,
        *,
        override_env: str,
        preferred: Path,
        fallback: Path,
    ) -> Path:
        override = os.getenv(override_env, "").strip()
        if override:
            resolved = self._normalize_prompt_path(override)
            if not resolved.is_file():
                raise PromptResolutionError(
                    f"{override_env} points to missing prompt file: {resolved}"
                )
            return resolved
        if preferred.is_file():
            return preferred
        return fallback

    @property
    def codex_dir(self) -> Path:
        return self.repo_root / ".codex"

    @property
    def codexflow_dir(self) -> Path:
        return self.repo_root / ".codexflow"

    @property
    def schemas_dir(self) -> Path:
        return self.codexflow_dir / "schemas"

    @property
    def prompts_dir(self) -> Path:
        return self.codexflow_dir / "prompts"

    @property
    def tasks_dir(self) -> Path:
        return self.codexflow_dir / "tasks"

    @property
    def reports_dir(self) -> Path:
        return self.codexflow_dir / "reports"

    @property
    def approvals_dir(self) -> Path:
        return self.codexflow_dir / "approvals"

    @property
    def plan_approvals_dir(self) -> Path:
        return self.approvals_dir / "PLAN"

    @property
    def tmp_dir(self) -> Path:
        return self.codexflow_dir / "_tmp"

    @property
    def archive_dir(self) -> Path:
        return self.codexflow_dir / "_archive"

    @property
    def examples_dir(self) -> Path:
        return self.codexflow_dir / "examples"

    @property
    def lock_file(self) -> Path:
        return self.codexflow_dir / "lock"

    @property
    def state_file(self) -> Path:
        return self.codexflow_dir / "state.json"

    @property
    def plan_json(self) -> Path:
        return self.codexflow_dir / "plan.json"

    @property
    def plan_md(self) -> Path:
        return self.codexflow_dir / "plan.md"

    @property
    def preflight_report(self) -> Path:
        return self.repo_root / "docs" / "codexflow_preflight_report.md"

    @property
    def product_check_script(self) -> Path:
        return self.repo_root / "scripts" / "jurisparse_mvp1_check.py"

    @property
    def product_check_output(self) -> Path:
        return self.tmp_dir / "product_check.json"

    @property
    def tech_spec(self) -> Path:
        return self.repo_root / "docs" / "TECH_SPEC.md"

    @property
    def manager_plan_prompt(self) -> Path:
        return self.prompts_dir / "manager_plan.md"

    @property
    def worker_prompt(self) -> Path:
        return self.prompts_dir / "worker.md"

    @property
    def manager_review_prompt(self) -> Path:
        return self.prompts_dir / "manager_review.md"

    @property
    def manager_v11_prompt(self) -> Path:
        return self.prompts_dir / "Manager_Architect_V_1.1.md"

    @property
    def worker_v11_prompt(self) -> Path:
        return self.prompts_dir / "Worker_Implementer_V_1.1.md"

    def resolve_manager_plan_prompt_path(self) -> Path:
        return self._resolve_prompt_path(
            override_env="CODEXFLOW_MANAGER_PROMPT",
            preferred=self.manager_v11_prompt,
            fallback=self.manager_plan_prompt,
        )

    def resolve_manager_review_prompt_path(self) -> Path:
        return self._resolve_prompt_path(
            override_env="CODEXFLOW_MANAGER_PROMPT",
            preferred=self.manager_v11_prompt,
            fallback=self.manager_review_prompt,
        )

    def resolve_worker_prompt_path(self) -> Path:
        return self._resolve_prompt_path(
            override_env="CODEXFLOW_WORKER_PROMPT",
            preferred=self.worker_v11_prompt,
            fallback=self.worker_prompt,
        )

    @property
    def manager_plan_schema(self) -> Path:
        return self.schemas_dir / "manager_plan.schema.json"

    @property
    def worker_report_schema(self) -> Path:
        return self.schemas_dir / "worker_report.schema.json"

    @property
    def manager_review_schema(self) -> Path:
        return self.schemas_dir / "manager_review.schema.json"

    @property
    def tmp_logs_dir(self) -> Path:
        return self.tmp_dir / "logs"

    @classmethod
    def from_start(cls, start: Path | None = None) -> "FlowPaths":
        return cls(repo_root=discover_repo_root(start=start))

    def ensure_layout(self) -> None:
        directories = [
            self.codex_dir,
            self.codexflow_dir,
            self.schemas_dir,
            self.prompts_dir,
            self.tasks_dir,
            self.reports_dir,
            self.approvals_dir,
            self.plan_approvals_dir,
            self.tmp_dir,
            self.archive_dir,
            self.tmp_logs_dir,
            self.examples_dir,
        ]
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def task_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id

    def report_dir(self, task_id: str) -> Path:
        return self.reports_dir / task_id

    def approval_dir(self, task_id: str) -> Path:
        return self.approvals_dir / task_id
