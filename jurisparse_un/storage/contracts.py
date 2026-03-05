"""Deterministic object-path contracts for storage artifacts."""

from __future__ import annotations

import re

_PATH_TOKEN_RE = re.compile(r"[^a-z0-9]+")
ARTIFACT_OBJECT_PATH_TEMPLATE = (
    "artifacts/{provider}/{doc_symbol}/{language}/{kind}/{sha256}.{ext}"
)


def _normalize_path_token(value: str) -> str:
    compact = value.replace("\u00a0", " ").strip().lower()
    collapsed = " ".join(compact.split())
    normalized = _PATH_TOKEN_RE.sub("-", collapsed).strip("-")
    if not normalized:
        raise ValueError("path token must contain at least one alphanumeric character")
    return normalized


def _normalize_extension(extension: str) -> str:
    normalized = extension.strip().lower().lstrip(".")
    if not normalized:
        raise ValueError("extension must not be empty")
    return normalized


def _normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("sha256 must not be empty")
    return normalized


def build_artifact_object_path(
    provider: str,
    doc_symbol: str,
    language: str,
    kind: str,
    sha256: str,
    extension: str,
) -> str:
    """Build a deterministic artifact object path for storage backends."""
    return ARTIFACT_OBJECT_PATH_TEMPLATE.format(
        provider=_normalize_path_token(provider),
        doc_symbol=_normalize_path_token(doc_symbol),
        language=_normalize_path_token(language),
        kind=_normalize_path_token(kind),
        sha256=_normalize_sha256(sha256),
        ext=_normalize_extension(extension),
    )
