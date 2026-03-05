"""Canonical text normalization rules for deterministic processing."""

from __future__ import annotations

import re

_WS_RE = re.compile(r"[ \t\f\v]+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _normalize_line(line: str) -> str:
    return _WS_RE.sub(" ", line).strip()


def normalize_text_v1(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00a0", " ")

    lines = [_normalize_line(line) for line in normalized.split("\n")]
    collapsed = "\n".join(lines).strip()
    return _MULTI_BLANK_RE.sub("\n\n", collapsed)
