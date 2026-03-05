"""Deterministic extract-stage helpers for page-level PDF text extraction."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from jurisparse_un.stages._shared import make_stage_payload
from jurisparse_un.text.normalize import normalize_text_v1

PDFPageExtractor = Callable[[dict[str, Any]], Sequence[str]]


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
    extract_items: Sequence[dict[str, Any]] | None = None,
    min_total_chars: int = 1000,
    max_empty_page_ratio: float = 0.5,
    pdf_page_extractor: PDFPageExtractor | None = None,
) -> dict[str, Any]:
    notes = [
        "PDF extraction is page-level only with explicit 1-based page_index.",
        "needs_ocr is derived from total_chars and empty-page ratio.",
    ]
    payload = make_stage_payload(
        stage="extract",
        config=config,
        run_id=run_id,
        dry_run=dry_run,
        from_manifest=from_manifest,
        from_db=from_db,
        notes=notes,
    )
    extracted_items = extract_source_items(
        extract_items=extract_items or [],
        min_total_chars=min_total_chars,
        max_empty_page_ratio=max_empty_page_ratio,
        pdf_page_extractor=pdf_page_extractor,
    )
    payload["extracted_items"] = extracted_items
    payload["stats"] = {
        "docs_total": len(extracted_items),
        "docs_needs_ocr": sum(1 for item in extracted_items if item["needs_ocr"]),
        "pages_total": sum(item["page_count"] for item in extracted_items),
        "empty_pages_total": sum(item["empty_pages"] for item in extracted_items),
    }
    return payload


def extract_source_items(
    *,
    extract_items: Sequence[dict[str, Any]],
    min_total_chars: int,
    max_empty_page_ratio: float,
    pdf_page_extractor: PDFPageExtractor | None = None,
) -> list[dict[str, Any]]:
    extractor = pdf_page_extractor or _default_pdf_page_extractor
    extracted_items: list[dict[str, Any]] = []

    for item in extract_items:
        raw_pages = list(extractor(item))
        pages = _normalize_pages(raw_pages, source_artifact_id=item.get("source_artifact_id"))
        page_count = len(pages)
        total_chars = sum(page["char_count"] for page in pages)
        empty_pages = sum(1 for page in pages if page["char_count"] == 0)
        needs_ocr = _needs_ocr(
            total_chars=total_chars,
            empty_pages=empty_pages,
            page_count=page_count,
            min_total_chars=min_total_chars,
            max_empty_page_ratio=max_empty_page_ratio,
        )

        warnings: list[str] = []
        if page_count == 0:
            warnings.append("pdf_extractor_returned_no_pages")
        if needs_ocr:
            warnings.append("needs_ocr=true")

        extracted_items.append(
            {
                "doc_id": item.get("doc_id"),
                "doc_version_id": item.get("doc_version_id"),
                "doc_symbol": item.get("doc_symbol"),
                "language": item.get("language"),
                "source_artifact_id": item.get("source_artifact_id"),
                "segment_type": "page",
                "pages": pages,
                "page_count": page_count,
                "total_chars": total_chars,
                "empty_pages": empty_pages,
                "needs_ocr": needs_ocr,
                "warnings": warnings,
            }
        )

    return extracted_items


def _default_pdf_page_extractor(item: dict[str, Any]) -> Sequence[str]:
    candidate = item.get("pdf_pages")
    if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
        return ()
    return ["" if page is None else str(page) for page in candidate]


def _normalize_pages(
    raw_pages: Sequence[str],
    *,
    source_artifact_id: Any,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for page_index, raw_text in enumerate(raw_pages, start=1):
        normalized_text = normalize_text_v1(raw_text)
        pages.append(
            {
                "page_index": page_index,
                "text": normalized_text,
                "char_count": len(normalized_text),
                "source_artifact_id": source_artifact_id,
            }
        )
    return pages


def _needs_ocr(
    *,
    total_chars: int,
    empty_pages: int,
    page_count: int,
    min_total_chars: int,
    max_empty_page_ratio: float,
) -> bool:
    if page_count == 0:
        return True
    empty_ratio = empty_pages / page_count
    return total_chars < min_total_chars or empty_ratio > max_empty_page_ratio
