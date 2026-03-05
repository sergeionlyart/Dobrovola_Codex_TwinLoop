"""lookup_sync stage contract scaffold."""

from __future__ import annotations

from typing import Any

from jurisparse_un.stages._shared import make_stage_payload


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
) -> dict[str, Any]:
    notes = [
        "lookup_sync resolves TBSearch IDs dynamically from source lookups.",
        "No hardcoded TreatyID or DocTypeID values are used.",
    ]
    return make_stage_payload(
        stage="lookup_sync",
        config=config,
        run_id=run_id,
        dry_run=dry_run,
        from_manifest=from_manifest,
        from_db=from_db,
        notes=notes,
    )
