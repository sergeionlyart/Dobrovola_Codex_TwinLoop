from __future__ import annotations

from typing import Any


def _list(items: list[str]) -> str:
    if not items:
        return "- (none)"
    return "\n".join(f"- {item}" for item in items)


def render_plan_markdown(plan: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# CodexFlow Plan")
    lines.append("")
    lines.append(f"Run ID: `{plan.get('run_id', 'unknown')}`")
    lines.append(f"Spec path: `{plan.get('spec_path', 'docs/TECH_SPEC.md')}`")
    lines.append(f"Created at: `{plan.get('created_at', '')}`")
    lines.append("")
    lines.append("## Assumptions")
    lines.append(_list(plan.get("assumptions", [])))
    lines.append("")
    lines.append("## Ordering")
    ordering = plan.get("ordering", [])
    if ordering:
        for idx, task_id in enumerate(ordering, start=1):
            lines.append(f"{idx}. {task_id}")
    else:
        lines.append("1. (empty)")
    lines.append("")
    lines.append("## Tasks")
    for task in plan.get("tasks", []):
        lines.append("")
        lines.append(f"### {task.get('task_id', 'TSK-XXXX')} — {task.get('title', '')}")
        lines.append(f"Objective: {task.get('objective', '')}")
        lines.append(f"Risk: {task.get('risk', 'unknown')}")
        lines.append(f"Approval required: {task.get('approval_required', False)}")
        lines.append("Scope in:")
        lines.append(_list(task.get("scope_in", [])))
        lines.append("Scope out:")
        lines.append(_list(task.get("scope_out", [])))
        lines.append("Definition of Done:")
        lines.append(_list(task.get("dod", [])))
        lines.append("Quality gates:")
        lines.append(_list(task.get("quality_gates", [])))
        lines.append("Required commands:")
        lines.append(_list(task.get("required_commands", [])))
        lines.append("Touched areas:")
        lines.append(_list(task.get("touched_areas", [])))
        lines.append("Depends on:")
        lines.append(_list(task.get("depends_on", [])))
    lines.append("")
    return "\n".join(lines)


def render_task_markdown(task: dict[str, Any], run_id: str) -> str:
    task_id = task.get("task_id", "TSK-XXXX")
    lines: list[str] = []
    lines.append(f"# Task {task_id}")
    lines.append("")
    lines.append(f"Run ID: `{run_id}`")
    lines.append(f"Title: {task.get('title', '')}")
    lines.append(f"Objective: {task.get('objective', '')}")
    lines.append(f"Risk: {task.get('risk', 'unknown')}")
    lines.append(f"Approval required: {task.get('approval_required', False)}")
    lines.append("")
    lines.append("## Scope In")
    lines.append(_list(task.get("scope_in", [])))
    lines.append("")
    lines.append("## Scope Out")
    lines.append(_list(task.get("scope_out", [])))
    lines.append("")
    lines.append("## Definition of Done")
    lines.append(_list(task.get("dod", [])))
    lines.append("")
    lines.append("## Quality Gates")
    lines.append(_list(task.get("quality_gates", [])))
    lines.append("")
    lines.append("## Required Commands")
    lines.append(_list(task.get("required_commands", [])))
    lines.append("")
    lines.append("## Touched Areas")
    lines.append(_list(task.get("touched_areas", [])))
    lines.append("")
    lines.append("## Depends On")
    lines.append(_list(task.get("depends_on", [])))
    lines.append("")
    return "\n".join(lines)


def render_report_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Worker Report {report.get('task_id', 'TSK-XXXX')}")
    lines.append("")
    lines.append(f"Status: `{report.get('status', '')}`")
    lines.append(f"Branch: `{report.get('branch', '')}`")
    lines.append(f"Created at: `{report.get('created_at', '')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append(report.get("summary", ""))
    lines.append("")
    lines.append("## Commits")
    commits = report.get("commits", [])
    if commits:
        for commit in commits:
            lines.append(f"- `{commit.get('sha', '')}` {commit.get('message', '')}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Changed Files")
    lines.append(_list(report.get("changed_files", [])))
    lines.append("")
    lines.append("## Commands Run")
    commands = report.get("commands_run", [])
    if commands:
        for item in commands:
            lines.append(
                f"- `{item.get('cmd', '')}` -> exit={item.get('exit_code', '')}; {item.get('notes', '')}"
            )
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Tests")
    tests = report.get("tests", [])
    if tests:
        for item in tests:
            lines.append(
                f"- {item.get('name', '')}: {item.get('status', '')} ({item.get('notes', '')})"
            )
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Risks or Notes")
    lines.append(_list(report.get("risks_or_notes", [])))
    lines.append("")
    lines.append("## Artifacts")
    artifacts = report.get("artifacts", {})
    if artifacts:
        for key, value in artifacts.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- (none)")
    lines.append("")
    return "\n".join(lines)


def render_decision_markdown(decision: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Manager Decision {decision.get('task_id', 'TSK-XXXX')}")
    lines.append("")
    lines.append(f"Verdict: `{decision.get('verdict', '')}`")
    lines.append(f"Approval required: {decision.get('approval_required', False)}")
    lines.append(f"Created at: `{decision.get('created_at', '')}`")
    lines.append("")
    lines.append("## Rationale")
    lines.append(decision.get("rationale", ""))
    lines.append("")
    lines.append("## Required Changes")
    lines.append(_list(decision.get("required_changes", [])))
    lines.append("")
    lines.append("## Next Task Suggestion")
    next_task = decision.get("next_task_suggestion")
    lines.append(f"{next_task}" if next_task else "null")
    lines.append("")
    lines.append("## Artifact Checks")
    lines.append(_list(decision.get("artifact_checks", [])))
    lines.append("")
    lines.append("## Full TECH_SPEC Complete")
    lines.append(str(bool(decision.get("full_techspec_complete", False))).lower())
    lines.append("")
    return "\n".join(lines)
