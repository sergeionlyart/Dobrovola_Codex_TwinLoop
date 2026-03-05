"""Mongo data-layer contracts."""

from __future__ import annotations

from jurisparse_un.db.mongo import REQUIRED_STAGE1_COLLECTIONS, get_stage1_collection_names

__all__ = ["REQUIRED_STAGE1_COLLECTIONS", "get_stage1_collection_names"]
