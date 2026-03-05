from __future__ import annotations

from jurisparse_un.db.mongo import REQUIRED_STAGE1_COLLECTIONS, get_stage1_collection_names

EXPECTED_STAGE1_COLLECTIONS = (
    "documents",
    "document_versions",
    "artifacts",
    "segments",
    "source_items",
    "tb_lookups",
    "ingest_runs",
    "errors",
)


def test_required_stage1_collections_match_tech_spec_contract() -> None:
    assert REQUIRED_STAGE1_COLLECTIONS == EXPECTED_STAGE1_COLLECTIONS
    assert len(REQUIRED_STAGE1_COLLECTIONS) == len(set(REQUIRED_STAGE1_COLLECTIONS))


def test_get_stage1_collection_names_returns_contract_tuple() -> None:
    assert get_stage1_collection_names() == EXPECTED_STAGE1_COLLECTIONS
