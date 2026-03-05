"""Deterministic identifiers for JurisParse MVP-1 scaffold."""

from __future__ import annotations

import hashlib
import uuid

_uuid_namespace = uuid.UUID("6b66f819-df0d-43de-89b5-a6ea5438f6ce")


def _normalize_token(value: str) -> str:
    compact = value.replace("\u00a0", " ").strip().lower()
    return " ".join(compact.split())


def make_doc_id(provider: str, doc_symbol: str, language: str) -> str:
    provider_norm = _normalize_token(provider)
    doc_symbol_norm = _normalize_token(doc_symbol)
    language_norm = _normalize_token(language)
    seed = f"doc|{provider_norm}|{doc_symbol_norm}|{language_norm}"
    return str(uuid.uuid5(_uuid_namespace, seed))


def make_doc_version_id(doc_id: str, content_sha256: str) -> str:
    doc_id_norm = _normalize_token(doc_id)
    content_sha256_norm = _normalize_token(content_sha256)
    seed = f"doc_version|{doc_id_norm}|{content_sha256_norm}"
    return str(uuid.uuid5(_uuid_namespace, seed))


def make_artifact_id(doc_version_id: str, kind: str, sha256: str) -> str:
    _ = doc_version_id, kind
    content_sha256_norm = _normalize_token(sha256)
    seed = f"artifact|{content_sha256_norm}"
    return str(uuid.uuid5(_uuid_namespace, seed))


def make_segment_id(doc_version_id: str, page_index: int) -> str:
    if page_index < 1:
        raise ValueError("page_index must be >= 1 (first page = 1)")

    doc_version_id_norm = _normalize_token(doc_version_id)
    return f"{doc_version_id_norm}:p{page_index:04d}"


def make_source_item_id(
    provider: str,
    doc_symbol: str,
    language: str,
    download_page_url: str,
) -> str:
    provider_norm = _normalize_token(provider)
    doc_symbol_norm = _normalize_token(doc_symbol)
    language_norm = _normalize_token(language)
    url_norm = _normalize_token(download_page_url)

    payload = "|".join(
        (
            provider_norm,
            doc_symbol_norm,
            language_norm,
            url_norm,
            download_page_url,
        )
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
