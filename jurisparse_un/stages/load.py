"""load stage contract scaffold."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Any

from jurisparse_un.stages._shared import make_stage_payload

CoreRow = dict[str, Any]
CollectionStore = list[CoreRow]
FilterBuilder = Callable[[Mapping[str, Any]], dict[str, Any]]

CORE_COLLECTIONS: tuple[str, ...] = (
    "documents",
    "document_versions",
    "artifacts",
    "segments",
    "source_items",
)

STATE_COLLECTIONS: tuple[str, ...] = CORE_COLLECTIONS + (
    "ingest_runs",
    "errors",
)


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
    documents: Sequence[Mapping[str, Any]] | None = None,
    document_versions: Sequence[Mapping[str, Any]] | None = None,
    artifacts: Sequence[Mapping[str, Any]] | None = None,
    segments: Sequence[Mapping[str, Any]] | None = None,
    source_items: Sequence[Mapping[str, Any]] | None = None,
    db_state: MutableMapping[str, list[CoreRow]] | None = None,
) -> dict[str, Any]:
    notes = [
        "Load path must be idempotent with no duplicates except ingest_runs.",
        "Upsert keys are deterministic and follow TECH_SPEC section 9.1.",
    ]
    payload = make_stage_payload(
        stage="load",
        config=config,
        run_id=run_id,
        dry_run=dry_run,
        from_manifest=from_manifest,
        from_db=from_db,
        notes=notes,
    )
    state = _coerce_db_state(db_state)

    stats = {
        "documents_upserted": _upsert_many(
            rows=documents or [],
            collection=state["documents"],
            filter_builder=_document_filter,
            collection_name="documents",
        ),
        "document_versions_upserted": _upsert_many(
            rows=document_versions or [],
            collection=state["document_versions"],
            filter_builder=_document_version_filter,
            collection_name="document_versions",
        ),
        "artifacts_upserted": _upsert_many(
            rows=artifacts or [],
            collection=state["artifacts"],
            filter_builder=_artifact_filter,
            collection_name="artifacts",
        ),
        "segments_upserted": _upsert_many(
            rows=segments or [],
            collection=state["segments"],
            filter_builder=_segment_filter,
            collection_name="segments",
        ),
        "source_items_upserted": _upsert_many(
            rows=source_items or [],
            collection=state["source_items"],
            filter_builder=_source_item_filter,
            collection_name="source_items",
        ),
    }

    state["ingest_runs"].append({"run_id": run_id})
    stats["ingest_runs_inserted"] = 1

    payload["stats"] = stats
    payload["collection_counts"] = {
        collection_name: len(state[collection_name])
        for collection_name in STATE_COLLECTIONS
    }
    payload["db_state"] = state
    return payload


def _coerce_db_state(
    db_state: MutableMapping[str, list[CoreRow]] | None,
) -> dict[str, list[CoreRow]]:
    state = {collection_name: [] for collection_name in STATE_COLLECTIONS}
    if db_state is None:
        return state

    for collection_name in STATE_COLLECTIONS:
        existing = db_state.get(collection_name)
        if isinstance(existing, list):
            state[collection_name] = existing
        else:
            db_state[collection_name] = state[collection_name]
    return state


def _upsert_many(
    *,
    rows: Sequence[Mapping[str, Any]],
    collection: CollectionStore,
    filter_builder: FilterBuilder,
    collection_name: str,
) -> int:
    inserted = 0
    for row in rows:
        prepared_row = _prepare_row(collection_name=collection_name, row=row)
        if upsert_one(
            collection,
            filter_query=filter_builder(prepared_row),
            record=prepared_row,
        ):
            inserted += 1
    return inserted


def upsert_one(
    collection: CollectionStore,
    *,
    filter_query: Mapping[str, Any],
    record: Mapping[str, Any],
) -> bool:
    row = dict(record)
    for index, existing in enumerate(collection):
        if _matches_filter(existing, filter_query):
            updated = dict(existing)
            updated.update(row)
            collection[index] = updated
            return False

    collection.append(row)
    return True


def _matches_filter(row: Mapping[str, Any], filter_query: Mapping[str, Any]) -> bool:
    return all(row.get(key) == value for key, value in filter_query.items())


def _prepare_row(collection_name: str, row: Mapping[str, Any]) -> CoreRow:
    prepared = dict(row)
    if collection_name == "segments":
        prepared["page_index"] = _required_page_index(
            prepared,
            field_name="page_index",
            collection_name=collection_name,
        )
    if collection_name == "source_items":
        prepared["_id"] = _source_item_identity(prepared)
    return prepared


def _document_filter(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "doc_key": _required_str(row, field_name="doc_key", collection_name="documents"),
    }


def _document_version_filter(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": _required_str(row, field_name="doc_id", collection_name="document_versions"),
        "content_sha256": _required_str(
            row,
            field_name="content_sha256",
            collection_name="document_versions",
        ),
    }


def _artifact_filter(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sha256": _required_str(row, field_name="sha256", collection_name="artifacts"),
    }


def _segment_filter(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "doc_version_id": _required_str(
            row,
            field_name="doc_version_id",
            collection_name="segments",
        ),
        "page_index": _required_page_index(
            row,
            field_name="page_index",
            collection_name="segments",
        ),
    }


def _source_item_filter(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"_id": _source_item_identity(row)}


def _source_item_identity(row: Mapping[str, Any]) -> str:
    for field_name in ("_id", "source_item_id"):
        value = _coerce_str(row.get(field_name))
        if value:
            return value
    raise ValueError(
        "source_items record must include non-empty '_id' or 'source_item_id' "
        "for deterministic upsert"
    )


def _required_str(
    row: Mapping[str, Any],
    *,
    field_name: str,
    collection_name: str,
) -> str:
    value = _coerce_str(row.get(field_name))
    if value:
        return value
    raise ValueError(
        f"{collection_name} record requires non-empty '{field_name}' for deterministic upsert"
    )


def _required_page_index(
    row: Mapping[str, Any],
    *,
    field_name: str,
    collection_name: str,
) -> int:
    raw = row.get(field_name)
    if isinstance(raw, bool):
        raise ValueError(f"{collection_name}.{field_name} must be a positive integer")
    if isinstance(raw, int):
        page_index = raw
    else:
        try:
            page_index = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{collection_name}.{field_name} must be a positive integer"
            ) from exc
    if page_index < 1:
        raise ValueError(f"{collection_name}.{field_name} must be >= 1")
    return page_index


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
