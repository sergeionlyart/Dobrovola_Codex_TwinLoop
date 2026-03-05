"""Stage-1 Mongo contract definitions for the ingestion pipeline."""

from __future__ import annotations

REQUIRED_STAGE1_COLLECTIONS: tuple[str, ...] = (
    "documents",
    "document_versions",
    "artifacts",
    "segments",
    "source_items",
    "tb_lookups",
    "ingest_runs",
    "errors",
)


def get_stage1_collection_names() -> tuple[str, ...]:
    """Return required Stage-1 Mongo collections in contract order."""
    return REQUIRED_STAGE1_COLLECTIONS
