"""Shared helpers for stage scaffold contracts."""

from __future__ import annotations

from typing import Any


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
