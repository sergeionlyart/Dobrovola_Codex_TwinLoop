from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from jurisparse_un.models.ids import make_segment_id
from jurisparse_un.stages.validate import REQUIRED_INVARIANT_IDS, run
from jurisparse_un.text.normalize import normalize_text_v1


def test_validate_invariant_catalog_contains_required_ids() -> None:
    payload = run(**_valid_inputs())
    check_ids = {check["id"] for check in payload["checks"]}

    assert check_ids == set(REQUIRED_INVARIANT_IDS)
    assert payload["ok"] is True


def test_validate_detects_needs_ocr_has_no_segments_violation() -> None:
    inputs = _valid_inputs()
    inputs["document_versions"][0]["extraction_quality"]["needs_ocr"] = True

    payload = run(**inputs)
    check = _check(payload, "needs_ocr_has_no_segments")

    assert check["ok"] is False
    assert check["violation_count"] == 1


def test_validate_detects_segments_source_artifact_exists_violation() -> None:
    inputs = _valid_inputs()
    inputs["segments"][0]["source_artifact_id"] = "missing-artifact"

    payload = run(**inputs)
    check = _check(payload, "segments_source_artifact_exists")

    assert check["ok"] is False
    assert check["violation_count"] == 1


def test_validate_detects_segments_text_sha256_matches_violation() -> None:
    inputs = _valid_inputs()
    inputs["segments"][0]["text_sha256"] = "bad-hash"

    payload = run(**inputs)
    check = _check(payload, "segments_text_sha256_matches")

    assert check["ok"] is False
    assert check["violation_count"] == 1


def test_validate_detects_segment_id_deterministic_violation() -> None:
    inputs = _valid_inputs()
    inputs["segments"][0]["segment_id"] = "docv-1:broken"

    payload = run(**inputs)
    check = _check(payload, "segment_id_deterministic")

    assert check["ok"] is False
    assert check["violation_count"] == 1


def _check(payload: dict[str, Any], invariant_id: str) -> dict[str, Any]:
    for check in payload["checks"]:
        if check["id"] == invariant_id:
            return check
    raise AssertionError(f"missing check: {invariant_id}")


def _valid_inputs() -> dict[str, list[dict[str, Any]]]:
    text = normalize_text_v1("  Valid\tpage text ")
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    segment = {
        "segment_id": make_segment_id("docv-1", 1),
        "doc_version_id": "docv-1",
        "page_index": 1,
        "text": text,
        "text_sha256": text_sha256,
        "source_artifact_id": "artifact-1",
    }

    document_version = {
        "_id": "docv-1",
        "doc_version_id": "docv-1",
        "doc_id": "doc-1",
        "content_sha256": "content-sha-1",
        "extraction_quality": {"needs_ocr": False},
    }
    artifact = {"_id": "artifact-1", "artifact_id": "artifact-1", "sha256": "artifact-sha-1"}

    return deepcopy(
        {
            "segments": [segment],
            "artifacts": [artifact],
            "document_versions": [document_version],
            "extracted_items": [],
        }
    )
