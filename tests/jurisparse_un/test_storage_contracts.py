from __future__ import annotations

from jurisparse_un.storage.contracts import build_artifact_object_path


def test_build_artifact_object_path_is_deterministic() -> None:
    first = build_artifact_object_path(
        provider="TBINTERNET",
        doc_symbol=" CCPR/C/1/D/1/2024 ",
        language="EN",
        kind="source pdf",
        sha256=" ABC123 ",
        extension=".PDF",
    )
    second = build_artifact_object_path(
        provider="tbinternet",
        doc_symbol="ccpr/c/1/d/1/2024",
        language="en",
        kind="source-pdf",
        sha256="abc123",
        extension="pdf",
    )

    assert first == second
    assert first == "artifacts/tbinternet/ccpr-c-1-d-1-2024/en/source-pdf/abc123.pdf"


def test_build_artifact_object_path_changes_for_key_input_changes() -> None:
    base = build_artifact_object_path(
        provider="tbinternet",
        doc_symbol="CAT/C/2/D/2/2024",
        language="en",
        kind="source_pdf",
        sha256="deadbeef",
        extension="pdf",
    )
    different_kind = build_artifact_object_path(
        provider="tbinternet",
        doc_symbol="CAT/C/2/D/2/2024",
        language="en",
        kind="crawl_html",
        sha256="deadbeef",
        extension="pdf",
    )
    different_sha = build_artifact_object_path(
        provider="tbinternet",
        doc_symbol="CAT/C/2/D/2/2024",
        language="en",
        kind="source_pdf",
        sha256="feedface",
        extension="pdf",
    )

    assert base != different_kind
    assert base != different_sha
