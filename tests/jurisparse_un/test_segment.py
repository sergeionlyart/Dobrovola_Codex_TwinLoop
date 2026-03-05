from __future__ import annotations

import hashlib

from jurisparse_un.models.ids import make_segment_id
from jurisparse_un.stages.segment import run
from jurisparse_un.text.normalize import normalize_text_v1


def test_segment_builds_deterministic_page_index_ids_with_traceability() -> None:
    extracted_items = [
        {
            "doc_id": "doc-1",
            "doc_version_id": "docv-1",
            "doc_symbol": "CCPR/C/1/D/1/2024",
            "language": "en",
            "source_artifact_id": "artifact-1",
            "needs_ocr": False,
            "pages": [
                {"page_index": 1, "text": " First\tpage "},
                {"page_index": 2, "text": "Second page"},
            ],
        }
    ]

    first_payload = run(extracted_items=extracted_items)
    second_payload = run(extracted_items=extracted_items)
    assert first_payload["segments"] == second_payload["segments"]

    first_segment = first_payload["segments"][0]
    normalized_text = normalize_text_v1(" First\tpage ")
    assert first_segment["segment_id"] == make_segment_id("docv-1", 1)
    assert first_segment["source_artifact_id"] == "artifact-1"
    assert first_segment["text_sha256"] == hashlib.sha256(
        normalized_text.encode("utf-8")
    ).hexdigest()


def test_segment_skips_needs_ocr_documents_and_tracks_skip_count() -> None:
    payload = run(
        extracted_items=[
            {
                "doc_id": "doc-2",
                "doc_version_id": "docv-2",
                "language": "en",
                "source_artifact_id": "artifact-2",
                "needs_ocr": True,
                "pages": [
                    {"page_index": 1, "text": "page one"},
                    {"page_index": 2, "text": "page two"},
                ],
            }
        ]
    )

    assert payload["segments"] == []
    assert payload["stats"]["segments_skipped_needs_ocr"] == 2
