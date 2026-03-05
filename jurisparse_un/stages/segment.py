"""Deterministic segment-stage helpers built on extract outputs."""

from __future__ import annotations

import hashlib
from typing import Any, Sequence

from jurisparse_un.models.ids import make_segment_id
from jurisparse_un.stages._shared import make_stage_payload
from jurisparse_un.text.normalize import normalize_text_v1


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
    extracted_items: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    notes = [
        "page_index is 1-based only (first page equals 1).",
        "If needs_ocr=true in MVP-1, no segments are emitted.",
    ]
    payload = make_stage_payload(
        stage="segment",
        config=config,
        run_id=run_id,
        dry_run=dry_run,
        from_manifest=from_manifest,
        from_db=from_db,
        notes=notes,
    )
    segments, stats = segment_extractions(extracted_items or [])
    payload["segments"] = segments
    payload["stats"] = stats
    return payload


def segment_extractions(
    extracted_items: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    segments: list[dict[str, Any]] = []
    segments_skipped_needs_ocr = 0

    for extraction in extracted_items:
        if bool(extraction.get("needs_ocr")):
            segments_skipped_needs_ocr += _count_pages(extraction)
            continue

        doc_version_id = _coerce_text(extraction.get("doc_version_id"))
        if not doc_version_id:
            raise ValueError("doc_version_id is required to build deterministic segment_id")

        source_artifact_id = extraction.get("source_artifact_id")
        pages = _coerce_pages(extraction.get("pages"))
        seen_page_index: set[int] = set()
        for fallback_index, page in enumerate(pages, start=1):
            page_index = _coerce_page_index(page.get("page_index"), fallback=fallback_index)
            if page_index in seen_page_index:
                raise ValueError(
                    f"duplicate page_index={page_index} for doc_version_id={doc_version_id}"
                )
            seen_page_index.add(page_index)

            text = normalize_text_v1(_coerce_text(page.get("text")))
            text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            segment_source_artifact_id = (
                page.get("source_artifact_id")
                if page.get("source_artifact_id") is not None
                else source_artifact_id
            )
            segments.append(
                {
                    "segment_id": make_segment_id(doc_version_id, page_index),
                    "segment_type": "page",
                    "doc_id": extraction.get("doc_id"),
                    "doc_version_id": doc_version_id,
                    "language": extraction.get("language"),
                    "page_index": page_index,
                    "page_label": str(page_index),
                    "text": text,
                    "text_sha256": text_sha256,
                    "text_norm_version": "v1",
                    "char_count": len(text),
                    "token_estimate": len(text.split()),
                    "source_artifact_id": segment_source_artifact_id,
                    "citation": {
                        "doc_symbol": extraction.get("doc_symbol"),
                        "language": extraction.get("language"),
                        "page_index": page_index,
                        "page_label": str(page_index),
                    },
                }
            )

    return (
        segments,
        {
            "segments_total": len(segments),
            "segments_skipped_needs_ocr": segments_skipped_needs_ocr,
        },
    )


def _coerce_pages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    pages: list[dict[str, Any]] = []
    for page in value:
        if isinstance(page, dict):
            pages.append(page)
    return pages


def _coerce_page_index(value: Any, *, fallback: int) -> int:
    page_index = fallback if value is None else value
    if isinstance(page_index, bool):
        raise ValueError("page_index must be an integer >= 1")
    try:
        parsed = int(page_index)
    except (TypeError, ValueError) as exc:
        raise ValueError("page_index must be an integer >= 1") from exc
    if parsed < 1:
        raise ValueError("page_index must be >= 1 (first page = 1)")
    return parsed


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _count_pages(extraction: dict[str, Any]) -> int:
    pages = _coerce_pages(extraction.get("pages"))
    if pages:
        return len(pages)
    fallback_page_count = extraction.get("page_count")
    if isinstance(fallback_page_count, int) and fallback_page_count > 0:
        return fallback_page_count
    return 0
