from __future__ import annotations

import pytest

from jurisparse_un.models.ids import (
    make_artifact_id,
    make_doc_id,
    make_doc_version_id,
    make_segment_id,
    make_source_item_id,
)


def test_doc_id_is_deterministic() -> None:
    first = make_doc_id("TBINTERNET", "CCPR/C/1/D/1/2024", "EN")
    second = make_doc_id("tbinternet", "  CCPR/C/1/D/1/2024  ", "en")

    assert first == second


def test_doc_version_and_artifact_ids_are_deterministic() -> None:
    doc_id = make_doc_id("tbinternet", "CAT/C/2/D/2/2024", "en")
    version_a = make_doc_version_id(doc_id, "abc123")
    version_b = make_doc_version_id(doc_id, " abc123 ")

    assert version_a == version_b
    assert make_artifact_id(version_a, "pdf", "deadbeef") == make_artifact_id(
        version_a,
        "pdf",
        "deadbeef",
    )


def test_make_segment_id_page_index_guard() -> None:
    with pytest.raises(ValueError, match="page_index must be >= 1"):
        make_segment_id("version-1", 0)


def test_make_segment_id_uses_1_based_suffix() -> None:
    assert make_segment_id("version-1", 1).endswith(":p0001")
    assert make_segment_id("version-1", 12).endswith(":p0012")


def test_source_item_id_is_deterministic() -> None:
    first = make_source_item_id(
        "tbinternet",
        "CCPR/C/3/D/3/2024",
        "en",
        "https://example.invalid/document/3",
    )
    second = make_source_item_id(
        "TBINTERNET",
        " CCPR/C/3/D/3/2024 ",
        "EN",
        "https://example.invalid/document/3",
    )

    assert first == second
