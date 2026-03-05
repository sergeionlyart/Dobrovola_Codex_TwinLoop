from __future__ import annotations

from typing import Any

from jurisparse_un.stages.load import run


def test_load_deduplicates_entities_by_upsert_keys() -> None:
    db_state: dict[str, list[dict[str, Any]]] = {}
    load_input = {
        "documents": [
            {
                "_id": "doc-1",
                "doc_key": "tbinternet|ccpr/c/1/d/1/2024|en",
                "doc_type": "decision",
            }
        ],
        "document_versions": [
            {
                "_id": "docv-1",
                "doc_id": "doc-1",
                "content_sha256": "content-sha-1",
            }
        ],
        "artifacts": [
            {
                "_id": "artifact-1",
                "doc_id": "doc-1",
                "doc_version_id": "docv-1",
                "sha256": "artifact-sha-1",
            }
        ],
        "segments": [
            {
                "_id": "docv-1:p0001",
                "doc_version_id": "docv-1",
                "page_index": 1,
                "text": "First page",
            }
        ],
        "source_items": [
            {
                "source_item_id": "source-item-1",
                "doc_symbol": "CCPR/C/1/D/1/2024",
                "language": "en",
            }
        ],
    }

    first_payload = run(run_id="RUN-1", db_state=db_state, **load_input)
    second_payload = run(run_id="RUN-2", db_state=db_state, **load_input)

    assert first_payload["stats"]["documents_upserted"] == 1
    assert first_payload["stats"]["document_versions_upserted"] == 1
    assert first_payload["stats"]["artifacts_upserted"] == 1
    assert first_payload["stats"]["segments_upserted"] == 1
    assert first_payload["stats"]["source_items_upserted"] == 1

    assert second_payload["stats"]["documents_upserted"] == 0
    assert second_payload["stats"]["document_versions_upserted"] == 0
    assert second_payload["stats"]["artifacts_upserted"] == 0
    assert second_payload["stats"]["segments_upserted"] == 0
    assert second_payload["stats"]["source_items_upserted"] == 0

    assert second_payload["collection_counts"]["documents"] == 1
    assert second_payload["collection_counts"]["document_versions"] == 1
    assert second_payload["collection_counts"]["artifacts"] == 1
    assert second_payload["collection_counts"]["segments"] == 1
    assert second_payload["collection_counts"]["source_items"] == 1
    assert second_payload["collection_counts"]["ingest_runs"] == 2

    source_item = second_payload["db_state"]["source_items"][0]
    assert source_item["_id"] == "source-item-1"
