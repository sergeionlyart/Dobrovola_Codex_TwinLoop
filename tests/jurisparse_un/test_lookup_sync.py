from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jurisparse_un.stages.lookup_sync import (
    required_doc_type_labels,
    required_treaty_labels,
    run,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _labels(records: list[dict[str, str]]) -> set[str]:
    return {record["label"] for record in records}


def test_lookup_sync_run_parses_dynamic_labels_and_hashes_html() -> None:
    html = _read_fixture("lookup_sync_tbsearch_sample.html")

    payload = run(
        config="config.example.yaml",
        run_id="RUN-TSK-0004",
        dry_run=True,
        tbsearch_html=html,
    )
    tb_lookups = payload["tb_lookups"]
    lookups = tb_lookups["lookups"]

    assert payload["stage"] == "lookup_sync"
    assert tb_lookups["html_sha256"] == hashlib.sha256(
        html.encode("utf-8")
    ).hexdigest()
    assert set(required_treaty_labels).issubset(_labels(lookups["treaties"]))
    assert set(required_doc_type_labels).issubset(_labels(lookups["doc_types"]))
    assert _labels(lookups["doc_type_categories"]) == {
        "Jurisprudence and standards"
    }
    assert "Canada" in _labels(lookups["countries"])


def test_lookup_sync_run_fail_fast_when_required_labels_missing() -> None:
    html = _read_fixture("lookup_sync_tbsearch_missing_required_label.html")

    with pytest.raises(ValueError, match="Working methods"):
        run(tbsearch_html=html)
