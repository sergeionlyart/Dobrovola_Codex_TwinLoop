from __future__ import annotations

from jurisparse_un.stages.extract import run


def test_extract_emits_normalized_pdf_pages_with_1_based_page_index() -> None:
    payload = run(
        extract_items=[
            {
                "doc_id": "doc-1",
                "doc_version_id": "docv-1",
                "doc_symbol": "CCPR/C/1/D/1/2024",
                "language": "en",
                "source_artifact_id": "artifact-1",
                "pdf_pages": ["  First\tline\r\n\r\n", " \u00a0 "],
            }
        ],
        min_total_chars=1,
    )

    extracted = payload["extracted_items"][0]
    first_page, second_page = extracted["pages"]
    assert first_page["page_index"] == 1
    assert first_page["text"] == "First line"
    assert second_page["page_index"] == 2
    assert second_page["text"] == ""
    assert extracted["needs_ocr"] is False
    assert payload["stats"]["docs_needs_ocr"] == 0


def test_extract_marks_needs_ocr_and_tracks_stats() -> None:
    payload = run(
        extract_items=[
            {
                "doc_id": "doc-2",
                "doc_version_id": "docv-2",
                "source_artifact_id": "artifact-2",
                "pdf_pages": [""],
            }
        ],
        min_total_chars=10,
    )

    extracted = payload["extracted_items"][0]
    assert extracted["page_count"] == 1
    assert extracted["total_chars"] == 0
    assert extracted["needs_ocr"] is True
    assert payload["stats"]["docs_needs_ocr"] == 1
