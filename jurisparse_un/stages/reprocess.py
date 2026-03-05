"""Manifest-driven, network-free reprocess stage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Any

from jurisparse_un.models.ids import (
    make_artifact_id,
    make_doc_id,
    make_doc_version_id,
    make_source_item_id,
)
from jurisparse_un.stages import extract, load, segment
from jurisparse_un.stages._shared import make_stage_payload

ManifestRow = Mapping[str, Any]
CoreRow = dict[str, Any]
GCSDownloadFn = Callable[[str], bytes]


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
    manifest_rows: Sequence[ManifestRow] | None = None,
    db_state: MutableMapping[str, list[CoreRow]] | None = None,
    min_total_chars: int = 1000,
    max_empty_page_ratio: float = 0.5,
    local_cache_dir: str | None = None,
    allow_gcs_download: bool = False,
    gcs_download_fn: GCSDownloadFn | None = None,
) -> dict[str, Any]:
    notes = [
        "reprocess runs from manifest/cache and does not require live network.",
        "Flow is manifest -> extract -> segment -> load, with deterministic IDs.",
        "Artifact bytes are fetched from local_cache_dir first, then optional gcs_uri.",
    ]
    payload = make_stage_payload(
        stage="reprocess",
        config=config,
        run_id=run_id,
        dry_run=dry_run,
        from_manifest=from_manifest,
        from_db=from_db,
        notes=notes,
    )

    manifest = _load_manifest_rows(from_manifest=from_manifest, manifest_rows=manifest_rows)
    extract_items, load_context = _prepare_manifest_inputs(
        manifest,
        local_cache_dir=local_cache_dir,
        allow_gcs_download=allow_gcs_download,
        gcs_download_fn=gcs_download_fn,
    )
    extracted_items = extract.extract_source_items(
        extract_items=extract_items,
        min_total_chars=min_total_chars,
        max_empty_page_ratio=max_empty_page_ratio,
    )
    segments, segment_stats = segment.segment_extractions(extracted_items)
    load_rows = _prepare_load_rows(load_context=load_context, extracted_items=extracted_items)
    load_payload = load.load_stage_payload(
        run_id=run_id,
        documents=load_rows["documents"],
        document_versions=load_rows["document_versions"],
        artifacts=load_rows["artifacts"],
        segments=segments,
        source_items=load_rows["source_items"],
        db_state=db_state,
    )

    payload["manifest_rows"] = manifest
    payload["extracted_items"] = extracted_items
    payload["segments"] = segments
    payload["load"] = load_payload
    payload["stats"] = {
        "manifest_rows": len(manifest),
        "docs_total": len(extracted_items),
        "segments_total": len(segments),
        "docs_needs_ocr": sum(1 for item in extracted_items if item["needs_ocr"]),
        "segments_skipped_needs_ocr": segment_stats["segments_skipped_needs_ocr"],
    }
    return payload


def _load_manifest_rows(
    *,
    from_manifest: str | None,
    manifest_rows: Sequence[ManifestRow] | None,
) -> list[ManifestRow]:
    if manifest_rows is not None:
        return [dict(row) for row in manifest_rows]
    if from_manifest is None:
        raise ValueError("reprocess requires --from-manifest or manifest_rows input")

    path = Path(from_manifest)
    rows: list[ManifestRow] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if line == "":
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"manifest line {line_number} must be valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(row, dict):
            raise ValueError(f"manifest line {line_number} must decode to JSON object")
        rows.append(row)
    return rows


def _prepare_manifest_inputs(
    manifest_rows: Sequence[ManifestRow],
    *,
    local_cache_dir: str | None,
    allow_gcs_download: bool,
    gcs_download_fn: GCSDownloadFn | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    extract_items: list[dict[str, Any]] = []
    load_context: list[dict[str, Any]] = []

    for index, row in enumerate(manifest_rows):
        provider = _token_or_default(row.get("provider"), default="tbinternet")
        doc_symbol = _token_or_default(row.get("doc_symbol"), default=f"manifest-doc-{index + 1}")
        language = _token_or_default(row.get("language"), default="en")

        artifact_bytes, artifact_fetch_source = _fetch_artifact_bytes(
            row=row,
            local_cache_dir=local_cache_dir,
            allow_gcs_download=allow_gcs_download,
            gcs_download_fn=gcs_download_fn,
        )
        pages = _manifest_pages(row, artifact_bytes=artifact_bytes)
        content_sha256 = _content_sha256(row=row, pages=pages)
        doc_id = _token_or_default(
            row.get("doc_id"),
            default=make_doc_id(provider, doc_symbol, language),
        )
        doc_version_id = _token_or_default(
            row.get("doc_version_id"),
            default=make_doc_version_id(doc_id, content_sha256),
        )
        source_artifact_id = _token_or_default(
            row.get("canonical_artifact_id"),
            default=make_artifact_id(doc_version_id, _token_or_default(row.get("selected_format"), default="pdf"), content_sha256),
        )
        download_page_url = _token_or_default(row.get("download_page_url"), default="")
        source_item_id = _token_or_default(
            row.get("source_item_id"),
            default=make_source_item_id(provider, doc_symbol, language, download_page_url),
        )
        doc_key = f"{provider}|{doc_symbol.lower()}|{language.lower()}"

        extract_items.append(
            {
                "doc_id": doc_id,
                "doc_version_id": doc_version_id,
                "doc_symbol": doc_symbol,
                "language": language,
                "source_artifact_id": source_artifact_id,
                "pdf_pages": pages,
            }
        )
        load_context.append(
            {
                "provider": provider,
                "doc_symbol": doc_symbol,
                "language": language,
                "doc_key": doc_key,
                "doc_id": doc_id,
                "doc_version_id": doc_version_id,
                "content_sha256": content_sha256,
                "source_artifact_id": source_artifact_id,
                "source_item_id": source_item_id,
                "download_page_url": download_page_url,
                "selected_format": _token_or_default(row.get("selected_format"), default="pdf"),
                "gcs_uri": _token_or_default(row.get("gcs_uri"), default=""),
                "artifact_fetch_source": artifact_fetch_source,
            }
        )
    return extract_items, load_context


def _prepare_load_rows(
    *,
    load_context: Sequence[Mapping[str, Any]],
    extracted_items: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    extraction_by_doc_version = {
        _token_or_default(item.get("doc_version_id"), default=""): item
        for item in extracted_items
    }
    documents: list[dict[str, Any]] = []
    document_versions: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    source_items: list[dict[str, Any]] = []

    for row in load_context:
        doc_version_id = _token_or_default(row.get("doc_version_id"), default="")
        extraction = extraction_by_doc_version.get(doc_version_id, {})
        needs_ocr = bool(extraction.get("needs_ocr"))

        documents.append(
            {
                "_id": _token_or_default(row.get("doc_id"), default=""),
                "doc_id": _token_or_default(row.get("doc_id"), default=""),
                "doc_key": _token_or_default(row.get("doc_key"), default=""),
                "provider": _token_or_default(row.get("provider"), default=""),
                "doc_symbol": _token_or_default(row.get("doc_symbol"), default=""),
                "language": _token_or_default(row.get("language"), default=""),
            }
        )
        document_versions.append(
            {
                "_id": doc_version_id,
                "doc_version_id": doc_version_id,
                "doc_id": _token_or_default(row.get("doc_id"), default=""),
                "content_sha256": _token_or_default(row.get("content_sha256"), default=""),
                "page_count": int(extraction.get("page_count", 0)),
                "extraction_quality": {"needs_ocr": needs_ocr},
            }
        )
        artifacts.append(
            {
                "_id": _token_or_default(row.get("source_artifact_id"), default=""),
                "artifact_id": _token_or_default(row.get("source_artifact_id"), default=""),
                "doc_id": _token_or_default(row.get("doc_id"), default=""),
                "doc_version_id": doc_version_id,
                "sha256": _token_or_default(row.get("content_sha256"), default=""),
                "kind": _token_or_default(row.get("selected_format"), default="pdf"),
                "gcs_uri": _token_or_default(row.get("gcs_uri"), default=""),
                "artifact_fetch_source": _token_or_default(
                    row.get("artifact_fetch_source"),
                    default="manifest",
                ),
            }
        )
        source_items.append(
            {
                "_id": _token_or_default(row.get("source_item_id"), default=""),
                "source_item_id": _token_or_default(row.get("source_item_id"), default=""),
                "provider": _token_or_default(row.get("provider"), default=""),
                "doc_symbol": _token_or_default(row.get("doc_symbol"), default=""),
                "language": _token_or_default(row.get("language"), default=""),
                "download_page_url": _token_or_default(row.get("download_page_url"), default=""),
            }
        )
    return {
        "documents": documents,
        "document_versions": document_versions,
        "artifacts": artifacts,
        "source_items": source_items,
    }


def _content_sha256(*, row: ManifestRow, pages: Sequence[str]) -> str:
    for field_name in ("sha256", "artifact_sha256", "content_sha256"):
        candidate = _token_or_default(row.get(field_name), default="")
        if candidate:
            return candidate.lower()
    joined = "\n".join(pages)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _manifest_pages(
    row: ManifestRow,
    *,
    artifact_bytes: bytes | None = None,
) -> list[str]:
    for field_name in ("pdf_pages", "pages", "text_pages"):
        candidate = row.get(field_name)
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
            return ["" if value is None else str(value) for value in candidate]
    text = row.get("text")
    if text is not None:
        return [str(text)]

    if artifact_bytes is None:
        return []

    decoded = artifact_bytes.decode("utf-8", errors="replace").strip()
    if not decoded:
        return []
    return [decoded]


def _fetch_artifact_bytes(
    *,
    row: ManifestRow,
    local_cache_dir: str | None,
    allow_gcs_download: bool,
    gcs_download_fn: GCSDownloadFn | None,
) -> tuple[bytes | None, str]:
    inline_bytes = _coerce_bytes(row.get("content_bytes"))
    if inline_bytes is None:
        inline_bytes = _coerce_bytes(row.get("artifact_bytes"))
    if inline_bytes is not None:
        return inline_bytes, "inline"

    gcs_uri = _token_or_default(row.get("gcs_uri"), default="")
    cache_path = _cache_path_for_row(row=row, gcs_uri=gcs_uri, local_cache_dir=local_cache_dir)
    if cache_path is not None and cache_path.is_file():
        return cache_path.read_bytes(), "cache"

    if gcs_uri and allow_gcs_download:
        download = gcs_download_fn or download_as_bytes
        return download(gcs_uri), "gcs"

    return None, "manifest"


def _cache_path_for_row(
    *,
    row: ManifestRow,
    gcs_uri: str,
    local_cache_dir: str | None,
) -> Path | None:
    explicit_path = _token_or_default(row.get("local_cache_path"), default="")
    if explicit_path:
        return Path(explicit_path)

    if local_cache_dir is None:
        return None

    parsed_uri = _parse_gcs_uri(gcs_uri)
    if parsed_uri is None:
        return None

    _, object_path = parsed_uri
    return Path(local_cache_dir) / object_path


def _parse_gcs_uri(gcs_uri: str) -> tuple[str, str] | None:
    if not gcs_uri.startswith("gs://"):
        return None
    suffix = gcs_uri[5:].strip()
    if "/" not in suffix:
        return None
    bucket_name, object_path = suffix.split("/", 1)
    bucket_name = bucket_name.strip()
    object_path = object_path.strip()
    if not bucket_name or not object_path:
        return None
    return bucket_name, object_path


def download_as_bytes(gcs_uri: str) -> bytes:
    parsed_uri = _parse_gcs_uri(gcs_uri)
    if parsed_uri is None:
        raise ValueError("gcs_uri must be in gs://bucket/object format")

    bucket_name, object_path = parsed_uri
    try:
        from google.cloud import storage
    except ModuleNotFoundError as exc:
        raise RuntimeError("google-cloud-storage package is required for gcs_uri fetch") from exc

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_path)
    return blob.download_as_bytes()


def _coerce_bytes(value: Any) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return None


def _token_or_default(value: Any, *, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default
