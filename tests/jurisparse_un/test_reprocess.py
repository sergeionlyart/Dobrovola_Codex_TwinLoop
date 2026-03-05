from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from jurisparse_un.stages.reprocess import run


def test_reprocess_from_manifest_is_network_free_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _deny_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network access is forbidden for reprocess")

    monkeypatch.setattr(urllib.request, "urlopen", _deny_network)
    manifest_path = tmp_path / "manifest.jsonl"
    manifest_row = {
        "provider": "tbinternet",
        "doc_symbol": "CCPR/C/1/D/1/2024",
        "language": "en",
        "download_page_url": "https://example.invalid/doc/1",
        "selected_format": "pdf",
        "sha256": "a" * 64,
        "pdf_pages": [" First page ", "Second\tpage"],
    }
    manifest_path.write_text(json.dumps(manifest_row) + "\n", encoding="utf-8")

    db_state: dict[str, list[dict[str, Any]]] = {}
    first_payload = run(
        from_manifest=str(manifest_path),
        run_id="RUN-1",
        db_state=db_state,
        min_total_chars=1,
    )
    second_payload = run(
        from_manifest=str(manifest_path),
        run_id="RUN-2",
        db_state=db_state,
        min_total_chars=1,
    )

    assert first_payload["stats"]["manifest_rows"] == 1
    assert first_payload["stats"]["segments_total"] == 2
    assert [segment["page_index"] for segment in first_payload["segments"]] == [1, 2]
    assert first_payload["load"]["stats"]["documents_upserted"] == 1
    assert first_payload["load"]["stats"]["segments_upserted"] == 2

    assert second_payload["segments"] == first_payload["segments"]
    assert second_payload["load"]["stats"]["documents_upserted"] == 0
    assert second_payload["load"]["stats"]["segments_upserted"] == 0
    assert second_payload["load"]["collection_counts"]["segments"] == 2
    assert second_payload["load"]["collection_counts"]["ingest_runs"] == 2


def test_reprocess_requires_manifest_input() -> None:
    with pytest.raises(ValueError, match="reprocess requires --from-manifest"):
        run()


def test_reprocess_keeps_needs_ocr_versions_without_segments() -> None:
    payload = run(
        manifest_rows=[
            {
                "provider": "tbinternet",
                "doc_symbol": "CAT/C/1/D/2/2024",
                "language": "en",
                "download_page_url": "https://example.invalid/doc/2",
                "pdf_pages": [""],
            }
        ],
        min_total_chars=100,
    )

    assert payload["stats"]["docs_needs_ocr"] == 1
    assert payload["stats"]["segments_total"] == 0
    assert payload["load"]["stats"]["segments_upserted"] == 0


def test_reprocess_reads_artifact_from_local_cache_when_gcs_uri_present(
    tmp_path: Path,
) -> None:
    local_cache_dir = tmp_path / "cache"
    cached_file = local_cache_dir / "artifacts/tbinternet/doc-cache/en/pdf/doc-cache.pdf"
    cached_file.parent.mkdir(parents=True, exist_ok=True)
    cached_file.write_bytes(b"Cached text page")

    payload = run(
        manifest_rows=[
            {
                "provider": "tbinternet",
                "doc_symbol": "DOC-CACHE",
                "language": "en",
                "doc_id": "doc-cache",
                "doc_version_id": "docv-cache",
                "source_item_id": "source-cache",
                "download_page_url": "https://example.invalid/cache",
                "selected_format": "pdf",
                "gcs_uri": "gs://cache-bucket/artifacts/tbinternet/doc-cache/en/pdf/doc-cache.pdf",
            }
        ],
        local_cache_dir=str(local_cache_dir),
        min_total_chars=1,
    )

    extracted = payload["extracted_items"][0]
    assert extracted["needs_ocr"] is False
    assert extracted["pages"][0]["text"] == "Cached text page"
    assert payload["load"]["db_state"]["artifacts"][0]["artifact_fetch_source"] == "cache"


def test_reprocess_falls_back_to_manifest_pages_when_cache_misses(tmp_path: Path) -> None:
    payload = run(
        manifest_rows=[
            {
                "provider": "tbinternet",
                "doc_symbol": "DOC-MANIFEST",
                "language": "en",
                "doc_id": "doc-manifest",
                "doc_version_id": "docv-manifest",
                "source_item_id": "source-manifest",
                "download_page_url": "https://example.invalid/manifest",
                "selected_format": "pdf",
                "gcs_uri": "gs://cache-bucket/artifacts/tbinternet/missing/en/pdf/doc.pdf",
                "pdf_pages": ["manifest page"],
            }
        ],
        local_cache_dir=str(tmp_path / "cache"),
        min_total_chars=1,
    )

    assert payload["extracted_items"][0]["pages"][0]["text"] == "manifest page"
    assert payload["load"]["db_state"]["artifacts"][0]["artifact_fetch_source"] == "manifest"
