"""Deterministic validate-stage invariant checks for MVP-1."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from jurisparse_un.models.ids import make_segment_id
from jurisparse_un.stages._shared import make_stage_payload
from jurisparse_un.text.normalize import normalize_text_v1

InvariantViolation = dict[str, Any]
InvariantResult = dict[str, Any]

REQUIRED_INVARIANT_IDS: tuple[str, ...] = (
    "segments_page_index_1_based_unique",
    "segments_source_artifact_exists",
    "segments_text_sha256_matches",
    "segment_id_deterministic",
    "doc_versions_unique_doc_id_content_sha256",
    "document_versions_page_count_matches_segments",
    "needs_ocr_has_no_segments",
)


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
    segments: Sequence[Mapping[str, Any]] | None = None,
    artifacts: Sequence[Mapping[str, Any]] | None = None,
    document_versions: Sequence[Mapping[str, Any]] | None = None,
    extracted_items: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    notes = [
        "validate executes explicit TECH_SPEC section 11.2 invariants.",
        "page_index is 1-based and deterministic segment identity is enforced.",
    ]
    payload = make_stage_payload(
        stage="validate",
        config=config,
        run_id=run_id,
        dry_run=dry_run,
        from_manifest=from_manifest,
        from_db=from_db,
        notes=notes,
    )

    check_results = run_invariant_checks(
        segments=segments or [],
        artifacts=artifacts or [],
        document_versions=document_versions or [],
        extracted_items=extracted_items or [],
    )
    payload["checks"] = check_results
    payload["violations"] = [
        violation
        for check in check_results
        for violation in check["violations"]
    ]
    payload["stats"] = {
        "checks_total": len(check_results),
        "checks_failed": sum(1 for check in check_results if not check["ok"]),
        "segments_total": len(segments or []),
        "artifacts_total": len(artifacts or []),
        "document_versions_total": len(document_versions or []),
        "docs_needs_ocr": len(_needs_ocr_doc_versions(document_versions or [], extracted_items or [])),
    }
    payload["ok"] = all(check["ok"] for check in check_results)
    return payload


def run_invariant_checks(
    *,
    segments: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    document_versions: Sequence[Mapping[str, Any]],
    extracted_items: Sequence[Mapping[str, Any]],
) -> list[InvariantResult]:
    checks: list[InvariantResult] = []
    checks.append(
        _result(
            invariant_id="segments_page_index_1_based_unique",
            violations=_check_segments_page_index_1_based_unique(segments),
        )
    )
    checks.append(
        _result(
            invariant_id="segments_source_artifact_exists",
            violations=_check_segments_source_artifact_exists(segments, artifacts),
        )
    )
    checks.append(
        _result(
            invariant_id="segments_text_sha256_matches",
            violations=_check_segments_text_sha256_matches(segments),
        )
    )
    checks.append(
        _result(
            invariant_id="segment_id_deterministic",
            violations=_check_segment_id_deterministic(segments),
        )
    )
    checks.append(
        _result(
            invariant_id="doc_versions_unique_doc_id_content_sha256",
            violations=_check_doc_versions_unique_doc_id_content_sha256(document_versions),
        )
    )
    checks.append(
        _result(
            invariant_id="document_versions_page_count_matches_segments",
            violations=_check_document_versions_page_count_matches_segments(
                segments=segments,
                document_versions=document_versions,
                extracted_items=extracted_items,
            ),
        )
    )
    checks.append(
        _result(
            invariant_id="needs_ocr_has_no_segments",
            violations=_check_needs_ocr_has_no_segments(
                segments=segments,
                document_versions=document_versions,
                extracted_items=extracted_items,
            ),
        )
    )
    return checks


def _result(*, invariant_id: str, violations: list[InvariantViolation]) -> InvariantResult:
    return {
        "id": invariant_id,
        "ok": len(violations) == 0,
        "violation_count": len(violations),
        "violations": violations,
    }


def _check_segments_page_index_1_based_unique(
    segments: Sequence[Mapping[str, Any]],
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    seen: dict[str, set[int]] = defaultdict(set)

    for segment in segments:
        doc_version_id = _coerce_text(_segment_doc_version_id(segment))
        segment_id = _coerce_text(segment.get("segment_id"))
        page_index = _coerce_page_index(segment.get("page_index"))
        if doc_version_id == "":
            violations.append(
                {
                    "reason": "doc_version_id_missing",
                    "segment_id": segment_id,
                    "page_index": segment.get("page_index"),
                }
            )
            continue
        if page_index is None or page_index < 1:
            violations.append(
                {
                    "reason": "page_index_not_1_based",
                    "doc_version_id": doc_version_id,
                    "segment_id": segment_id,
                    "page_index": segment.get("page_index"),
                }
            )
            continue
        if page_index in seen[doc_version_id]:
            violations.append(
                {
                    "reason": "page_index_not_unique",
                    "doc_version_id": doc_version_id,
                    "segment_id": segment_id,
                    "page_index": page_index,
                }
            )
            continue
        seen[doc_version_id].add(page_index)
    return violations


def _check_segments_source_artifact_exists(
    segments: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> list[InvariantViolation]:
    artifact_ids = {
        _coerce_text(row.get("_id"))
        for row in artifacts
        if _coerce_text(row.get("_id"))
    }
    artifact_ids.update(
        _coerce_text(row.get("artifact_id"))
        for row in artifacts
        if _coerce_text(row.get("artifact_id"))
    )

    violations: list[InvariantViolation] = []
    for segment in segments:
        source_artifact_id = _coerce_text(segment.get("source_artifact_id"))
        if source_artifact_id == "" or source_artifact_id not in artifact_ids:
            violations.append(
                {
                    "reason": "source_artifact_missing",
                    "segment_id": _coerce_text(segment.get("segment_id")),
                    "doc_version_id": _coerce_text(_segment_doc_version_id(segment)),
                    "source_artifact_id": source_artifact_id,
                }
            )
    return violations


def _check_segments_text_sha256_matches(
    segments: Sequence[Mapping[str, Any]],
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    for segment in segments:
        text = _coerce_text(segment.get("text"))
        expected_hash = hashlib.sha256(normalize_text_v1(text).encode("utf-8")).hexdigest()
        actual_hash = _coerce_text(segment.get("text_sha256")).lower()
        if actual_hash != expected_hash:
            violations.append(
                {
                    "reason": "text_sha256_mismatch",
                    "segment_id": _coerce_text(segment.get("segment_id")),
                    "doc_version_id": _coerce_text(_segment_doc_version_id(segment)),
                    "expected_text_sha256": expected_hash,
                    "actual_text_sha256": actual_hash,
                }
            )
    return violations


def _check_segment_id_deterministic(
    segments: Sequence[Mapping[str, Any]],
) -> list[InvariantViolation]:
    violations: list[InvariantViolation] = []
    for segment in segments:
        doc_version_id = _coerce_text(_segment_doc_version_id(segment))
        segment_id = _coerce_text(segment.get("segment_id"))
        page_index = _coerce_page_index(segment.get("page_index"))
        if doc_version_id == "" or page_index is None:
            violations.append(
                {
                    "reason": "segment_identity_fields_missing",
                    "segment_id": segment_id,
                    "doc_version_id": doc_version_id,
                    "page_index": segment.get("page_index"),
                }
            )
            continue
        expected_segment_id = make_segment_id(doc_version_id, page_index)
        if segment_id != expected_segment_id:
            violations.append(
                {
                    "reason": "segment_id_non_deterministic",
                    "segment_id": segment_id,
                    "expected_segment_id": expected_segment_id,
                    "doc_version_id": doc_version_id,
                    "page_index": page_index,
                }
            )
    return violations


def _check_doc_versions_unique_doc_id_content_sha256(
    document_versions: Sequence[Mapping[str, Any]],
) -> list[InvariantViolation]:
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in document_versions:
        doc_id = _coerce_text(_doc_version_id(row, field_name="doc_id"))
        content_sha256 = _coerce_text(row.get("content_sha256")).lower()
        if doc_id == "" or content_sha256 == "":
            continue
        grouped[(doc_id, content_sha256)].append(_coerce_text(_doc_version_id(row)))

    violations: list[InvariantViolation] = []
    for (doc_id, content_sha256), ids in grouped.items():
        if len(ids) > 1:
            violations.append(
                {
                    "reason": "duplicate_doc_id_content_sha256",
                    "doc_id": doc_id,
                    "content_sha256": content_sha256,
                    "doc_version_ids": ids,
                }
            )
    return violations


def _check_document_versions_page_count_matches_segments(
    *,
    segments: Sequence[Mapping[str, Any]],
    document_versions: Sequence[Mapping[str, Any]],
    extracted_items: Sequence[Mapping[str, Any]],
) -> list[InvariantViolation]:
    segment_counts: dict[str, int] = defaultdict(int)
    for segment in segments:
        doc_version_id = _coerce_text(_segment_doc_version_id(segment))
        if doc_version_id:
            segment_counts[doc_version_id] += 1

    needs_ocr_doc_versions = _needs_ocr_doc_versions(document_versions, extracted_items)
    violations: list[InvariantViolation] = []
    for row in document_versions:
        doc_version_id = _coerce_text(_doc_version_id(row))
        if doc_version_id == "":
            continue
        page_count = _coerce_page_index(row.get("page_count"))
        if page_count is None:
            continue

        segment_count = segment_counts.get(doc_version_id, 0)
        if doc_version_id in needs_ocr_doc_versions:
            if segment_count != 0:
                violations.append(
                    {
                        "reason": "needs_ocr_doc_has_segments",
                        "doc_version_id": doc_version_id,
                        "page_count": page_count,
                        "segment_count": segment_count,
                    }
                )
            continue

        if page_count != segment_count:
            violations.append(
                {
                    "reason": "page_count_segment_count_mismatch",
                    "doc_version_id": doc_version_id,
                    "page_count": page_count,
                    "segment_count": segment_count,
                }
            )
    return violations


def _check_needs_ocr_has_no_segments(
    *,
    segments: Sequence[Mapping[str, Any]],
    document_versions: Sequence[Mapping[str, Any]],
    extracted_items: Sequence[Mapping[str, Any]],
) -> list[InvariantViolation]:
    needs_ocr_doc_versions = _needs_ocr_doc_versions(document_versions, extracted_items)
    if len(needs_ocr_doc_versions) == 0:
        return []

    violations: list[InvariantViolation] = []
    for segment in segments:
        doc_version_id = _coerce_text(_segment_doc_version_id(segment))
        if doc_version_id in needs_ocr_doc_versions:
            violations.append(
                {
                    "reason": "segment_present_for_needs_ocr_doc",
                    "doc_version_id": doc_version_id,
                    "segment_id": _coerce_text(segment.get("segment_id")),
                    "page_index": segment.get("page_index"),
                }
            )
    return violations


def _needs_ocr_doc_versions(
    document_versions: Sequence[Mapping[str, Any]],
    extracted_items: Sequence[Mapping[str, Any]],
) -> set[str]:
    needs_ocr_doc_versions: set[str] = set()

    for row in document_versions:
        doc_version_id = _coerce_text(_doc_version_id(row))
        if doc_version_id == "":
            continue
        extraction_quality = row.get("extraction_quality")
        if isinstance(extraction_quality, Mapping) and bool(extraction_quality.get("needs_ocr")):
            needs_ocr_doc_versions.add(doc_version_id)
            continue
        if bool(row.get("needs_ocr")):
            needs_ocr_doc_versions.add(doc_version_id)

    for row in extracted_items:
        if not bool(row.get("needs_ocr")):
            continue
        doc_version_id = _coerce_text(_doc_version_id(row))
        if doc_version_id:
            needs_ocr_doc_versions.add(doc_version_id)
    return needs_ocr_doc_versions


def _doc_version_id(row: Mapping[str, Any], field_name: str = "_id") -> Any:
    if field_name == "_id":
        if row.get("doc_version_id") is not None:
            return row.get("doc_version_id")
        return row.get("_id")
    return row.get(field_name)


def _segment_doc_version_id(row: Mapping[str, Any]) -> Any:
    return row.get("doc_version_id")


def _coerce_page_index(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
