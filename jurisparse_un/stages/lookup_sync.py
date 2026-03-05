"""lookup_sync stage helpers for dynamic TBSearch parsing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
from typing import Any, Iterable

from jurisparse_un.stages._shared import HTTPRequester, default_http_get, make_stage_payload

required_treaty_labels = frozenset({"CCPR", "CAT", "CEDAW", "CRPD"})
required_doc_type_labels = frozenset(
    {
        "Jurisprudence",
        "General Comment/recommendation",
        "Rules of procedure",
        "Working methods",
    }
)
DEFAULT_TBSEARCH_URL = "https://juris.ohchr.org/tbsearch"
DEFAULT_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class LookupOption:
    value: str
    label: str


class _TBSearchSelectParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._select_order: list[str] = []
        self._select_options: dict[str, list[LookupOption]] = {}
        self._active_select_key: str | None = None
        self._active_option_value: str | None = None
        self._active_option_label_parts: list[str] = []

    @property
    def select_order(self) -> list[str]:
        return self._select_order

    @property
    def select_options(self) -> dict[str, list[LookupOption]]:
        return self._select_options

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag == "select":
            select_key = self._resolve_select_key(attrs)
            self._active_select_key = select_key
            if select_key not in self._select_options:
                self._select_order.append(select_key)
                self._select_options[select_key] = []
            return

        if tag == "option" and self._active_select_key is not None:
            attrs_dict = self._attrs_to_dict(attrs)
            self._active_option_value = (attrs_dict.get("value") or "").strip()
            self._active_option_label_parts = []

    def handle_data(self, data: str) -> None:
        if self._active_option_value is not None:
            self._active_option_label_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._active_select_key is not None:
            if self._active_option_value is not None:
                option = LookupOption(
                    value=self._active_option_value,
                    label="".join(self._active_option_label_parts).strip(),
                )
                if self._is_valid_option(option):
                    self._select_options[self._active_select_key].append(option)
            self._active_option_value = None
            self._active_option_label_parts = []
            return

        if tag == "select":
            self._active_select_key = None

    @staticmethod
    def _attrs_to_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for key, value in attrs:
            if value is not None:
                parsed[key] = value
        return parsed

    def _resolve_select_key(self, attrs: list[tuple[str, str | None]]) -> str:
        attrs_dict = self._attrs_to_dict(attrs)
        for key in ("id", "name", "aria-label", "data-name", "title"):
            value = (attrs_dict.get(key) or "").strip()
            if value:
                return value
        return f"select_{len(self._select_order)}"

    @staticmethod
    def _is_valid_option(option: LookupOption) -> bool:
        if not option.value or not option.label:
            return False
        return not option.label.casefold().startswith("select")


def _normalize_token(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _classify_lookup_key(select_key: str) -> str | None:
    token = _normalize_token(select_key)
    if "treaty" in token or "committee" in token:
        return "treaties"
    if "doctypecategory" in token or ("doc" in token and "category" in token):
        return "doc_type_categories"
    if "doctype" in token or ("doc" in token and "type" in token):
        return "doc_types"
    if "country" in token or "stateparty" in token:
        return "countries"
    return None


def _dedupe_options(options: Iterable[LookupOption]) -> list[LookupOption]:
    deduped: list[LookupOption] = []
    seen: set[tuple[str, str]] = set()
    for option in options:
        key = (option.value.casefold(), option.label.casefold())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return deduped


def parse_tbsearch_lookups(tbsearch_html: str) -> dict[str, list[dict[str, str]]]:
    parser = _TBSearchSelectParser()
    parser.feed(tbsearch_html)

    grouped: dict[str, list[LookupOption]] = {
        "treaties": [],
        "doc_type_categories": [],
        "doc_types": [],
        "countries": [],
    }
    for select_key in parser.select_order:
        bucket = _classify_lookup_key(select_key)
        if bucket is None:
            continue
        grouped[bucket].extend(parser.select_options.get(select_key, []))

    normalized: dict[str, list[dict[str, str]]] = {}
    for bucket, options in grouped.items():
        normalized[bucket] = [
            {"value": option.value, "label": option.label}
            for option in _dedupe_options(options)
        ]
    return normalized


def _missing_labels(
    *,
    required_labels: Iterable[str],
    present_labels: set[str],
) -> list[str]:
    missing = [
        label for label in required_labels if label.casefold() not in present_labels
    ]
    return sorted(missing)


def validate_required_labels(
    lookups: dict[str, list[dict[str, str]]],
    *,
    treaty_labels: Iterable[str] = required_treaty_labels,
    doc_type_labels: Iterable[str] = required_doc_type_labels,
) -> None:
    present_treaties = {
        item.get("label", "").strip().casefold()
        for item in lookups.get("treaties", [])
    }
    present_doc_types = {
        item.get("label", "").strip().casefold()
        for item in lookups.get("doc_types", [])
    }
    missing_treaties = _missing_labels(
        required_labels=treaty_labels,
        present_labels=present_treaties,
    )
    missing_doc_types = _missing_labels(
        required_labels=doc_type_labels,
        present_labels=present_doc_types,
    )
    if not missing_treaties and not missing_doc_types:
        return

    missing_parts: list[str] = []
    if missing_treaties:
        missing_parts.append(f"treaties={missing_treaties}")
    if missing_doc_types:
        missing_parts.append(f"doc_types={missing_doc_types}")
    missing_summary = ", ".join(missing_parts)
    raise ValueError(f"Missing required TBSearch labels: {missing_summary}")


def run(
    *,
    config: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    from_manifest: str | None = None,
    from_db: bool = False,
    tbsearch_html: str | None = None,
    tbsearch_url: str = DEFAULT_TBSEARCH_URL,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    request_fn: HTTPRequester | None = None,
) -> dict[str, Any]:
    notes = [
        "lookup_sync parses TBSearch labels dynamically from source HTML.",
        "lookup_sync validates required treaty and doc type labels fail-fast.",
    ]
    payload = make_stage_payload(
        stage="lookup_sync",
        config=config,
        run_id=run_id,
        dry_run=dry_run,
        from_manifest=from_manifest,
        from_db=from_db,
        notes=notes,
    )
    fetch_url = tbsearch_url.strip() or DEFAULT_TBSEARCH_URL
    html_source = "inline"
    if tbsearch_html is None:
        fetch = request_fn or default_http_get
        response = fetch(url=fetch_url, timeout_sec=timeout_sec)
        if response.status_code >= 400:
            raise ValueError(
                f"TBSearch fetch failed: status_code={response.status_code} url={fetch_url}"
            )
        tbsearch_html = response.content.decode("utf-8", errors="replace")
        html_source = "live_fetch"

    lookups = parse_tbsearch_lookups(tbsearch_html)
    validate_required_labels(lookups)
    payload["tb_lookups"] = {
        "provider": "tbinternet",
        "language": "EN",
        "tbsearch_url": fetch_url,
        "html_sha256": hashlib.sha256(tbsearch_html.encode("utf-8")).hexdigest(),
        "lookups": lookups,
    }
    payload["tbsearch_fetch"] = {
        "source": html_source,
        "timeout_sec": timeout_sec,
    }
    return payload
