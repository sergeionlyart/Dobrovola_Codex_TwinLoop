from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_STAGE_COMMANDS = (
    "lookup-sync",
    "crawl",
    "resolve",
    "download",
    "extract",
    "segment",
    "load",
    "validate",
    "reprocess",
)

PACKAGE_CANDIDATES = (
    "jurisparse_un",
    "src/jurisparse_un",
)

CONFIG_CANDIDATES = (
    "config.example.yaml",
    "config.example.yml",
    "config/config.example.yaml",
    "config/config.example.yml",
)

STUB_MARKERS = (
    "Stage stub is not implemented yet.",
    '"""Stage stub module."""',
)

ID_FUNCTION_NAMES = (
    "make_doc_id",
    "make_doc_version_id",
    "make_artifact_id",
    "make_segment_id",
    "make_source_item_id",
)

REQUIRED_COLLECTIONS = (
    "documents",
    "document_versions",
    "artifacts",
    "segments",
    "source_items",
    "tb_lookups",
    "ingest_runs",
    "errors",
)

REQUIRED_VALIDATE_CHECK_IDS = (
    "segments_page_index_1_based_unique",
    "segments_source_artifact_exists",
    "segments_text_sha256_matches",
    "segment_id_deterministic",
    "doc_versions_unique_doc_id_content_sha256",
    "needs_ocr_has_no_segments",
)

REQUIRED_STAGE_TEST_FILES = (
    "tests/jurisparse_un/test_lookup_sync.py",
    "tests/jurisparse_un/test_crawl.py",
    "tests/jurisparse_un/test_resolve.py",
    "tests/jurisparse_un/test_download.py",
    "tests/jurisparse_un/test_extract.py",
    "tests/jurisparse_un/test_segment.py",
    "tests/jurisparse_un/test_load.py",
    "tests/jurisparse_un/test_validate.py",
    "tests/jurisparse_un/test_reprocess.py",
)

REQUIRED_LOOKUP_FIXTURES = (
    "tests/fixtures/lookup_sync_tbsearch_sample.html",
    "tests/fixtures/lookup_sync_tbsearch_missing_required_label.html",
)

TECHSPEC_COVERAGE_TARGET_PCT = 100.0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _find_existing(repo_root: Path, candidates: tuple[str, ...]) -> Path | None:
    for rel in candidates:
        path = repo_root / rel
        if path.exists():
            return path
    return None


def _contains_all(text: str, tokens: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return all(token.casefold() in folded for token in tokens)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(token.casefold() in folded for token in tokens)


def _subcommand_tokens(command: str) -> tuple[str, ...]:
    return (command, command.replace("-", "_"))


def _detect_cli_info(package_dir: Path | None, repo_root: Path) -> dict[str, Any]:
    candidates: list[Path] = []
    if package_dir is not None:
        candidates.append(package_dir / "cli.py")
        candidates.append(package_dir / "__main__.py")

    existing = [path for path in candidates if path.is_file()]
    combined_text = "\n".join(_read_text(path).lower() for path in existing)

    found_subcommands: dict[str, bool] = {}
    missing_subcommands: list[str] = []
    for command in REQUIRED_STAGE_COMMANDS:
        tokens = _subcommand_tokens(command)
        present = any(token.lower() in combined_text for token in tokens)
        found_subcommands[command] = present
        if not present:
            missing_subcommands.append(command)

    module_runnable = bool(package_dir and (package_dir / "__main__.py").is_file())
    cli_contract = {
        "ingest_command_present": _contains_any(
            combined_text,
            (
                "\"ingest\":",
                "'ingest':",
                "add_parser('ingest'",
                'add_parser("ingest"',
            ),
        ),
        "from_manifest_option_present": _contains_any(
            combined_text,
            ("--from-manifest", "from_manifest"),
        ),
        "from_db_option_present": _contains_any(
            combined_text,
            ("--from-db", "from_db"),
        ),
        "dry_run_option_present": _contains_any(
            combined_text,
            ("--dry-run", "dry_run"),
        ),
    }

    return {
        "module_runnable": module_runnable,
        "paths": [str(path.relative_to(repo_root)) for path in existing],
        "missing_subcommands": missing_subcommands,
        "found_subcommands": found_subcommands,
        "contract": cli_contract,
        "combined_text": combined_text,
    }


def _manifest_has_rows(path: Path) -> bool:
    text = _read_text(path)
    return any(line.strip() for line in text.splitlines())


def _detect_stage_implementations(
    package_dir: Path | None,
    repo_root: Path,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if package_dir is None:
        for command in REQUIRED_STAGE_COMMANDS:
            stage_key = command.replace("-", "_")
            results[stage_key] = {
                "ok": False,
                "path": None,
                "reason": "package_dir_missing",
                "is_stub": True,
                "text": "",
            }
        return results

    stages_dir = package_dir / "stages"
    for command in REQUIRED_STAGE_COMMANDS:
        stage_key = command.replace("-", "_")
        stage_path = stages_dir / f"{stage_key}.py"
        if not stage_path.is_file():
            results[stage_key] = {
                "ok": False,
                "path": str(stage_path.relative_to(repo_root)),
                "reason": "file_missing",
                "is_stub": True,
                "has_run_callable": False,
                "text": "",
            }
            continue

        text = _read_text(stage_path)
        has_runner = "def run(" in text
        is_stub = any(marker in text for marker in STUB_MARKERS)
        implemented = has_runner and not is_stub
        reason = "ok" if implemented else "stub_detected"
        if not has_runner:
            reason = "missing_run_callable"
        results[stage_key] = {
            "ok": implemented,
            "path": str(stage_path.relative_to(repo_root)),
            "reason": reason,
            "is_stub": is_stub,
            "has_run_callable": has_runner,
            "text": text,
        }
    return results


def _detect_ids_capability(package_dir: Path | None, repo_root: Path) -> dict[str, Any]:
    if package_dir is None:
        return {
            "ok": False,
            "path": None,
            "missing_functions": list(ID_FUNCTION_NAMES),
            "page_index_guard": False,
            "uuidv5_formula_present": False,
            "source_item_sha1_formula_present": False,
            "text": "",
        }

    ids_path = package_dir / "models" / "ids.py"
    if not ids_path.is_file():
        return {
            "ok": False,
            "path": str(ids_path.relative_to(repo_root)),
            "missing_functions": list(ID_FUNCTION_NAMES),
            "page_index_guard": False,
            "uuidv5_formula_present": False,
            "source_item_sha1_formula_present": False,
            "text": "",
        }

    text = _read_text(ids_path)
    missing_functions = [
        name for name in ID_FUNCTION_NAMES if f"def {name}(" not in text
    ]
    page_index_guard = "page_index must be >= 1" in text
    uuidv5_formula_present = _contains_all(
        text,
        (
            "uuid5(",
            "_uuid_namespace",
            "provider_norm",
            "doc_symbol_norm",
            "language_norm",
            "doc_id_norm",
            "content_sha256_norm",
        ),
    )
    source_item_sha1_formula_present = _contains_all(
        text,
        (
            "hashlib.sha1",
            "provider_norm",
            "doc_symbol_norm",
            "language_norm",
            "url_norm",
            "download_page_url",
        ),
    )

    ok = not missing_functions and page_index_guard
    return {
        "ok": ok,
        "path": str(ids_path.relative_to(repo_root)),
        "missing_functions": missing_functions,
        "page_index_guard": page_index_guard,
        "uuidv5_formula_present": uuidv5_formula_present,
        "source_item_sha1_formula_present": source_item_sha1_formula_present,
        "text": text,
    }


def _detect_stage_contracts(
    stage_checks: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    lookup_text = stage_checks["lookup_sync"].get("text", "")
    crawl_text = stage_checks["crawl"].get("text", "")
    resolve_text = stage_checks["resolve"].get("text", "")
    download_text = stage_checks["download"].get("text", "")
    extract_text = stage_checks["extract"].get("text", "")
    segment_text = stage_checks["segment"].get("text", "")
    load_text = stage_checks["load"].get("text", "")
    validate_text = stage_checks["validate"].get("text", "")
    reprocess_text = stage_checks["reprocess"].get("text", "")

    return {
        "lookup_sync_dynamic_labels_present": {
            "ok": _contains_all(
                lookup_text,
                (
                    "parse_tbsearch_lookups",
                    "validate_required_labels",
                    "required_treaty_labels",
                    "required_doc_type_labels",
                ),
            )
        },
        "lookup_sync_no_hardcoded_ids": {
            "ok": not _contains_any(
                lookup_text,
                ("treatyid", "doctypeid", "docTypeID", "TreatyID"),
            )
        },
        "crawl_retry_backoff_rate_limit": {
            "ok": _contains_all(
                crawl_text,
                (
                    "default_retries",
                    "default_backoff_base_sec",
                    "default_rate_limit_rps",
                    "retry_backoff",
                    "rate_limit_wait",
                ),
            )
        },
        "resolve_format_priority_present": {
            "ok": _contains_all(
                resolve_text,
                ("format_priority", "pdf", "docx", "html"),
            )
        },
        "resolve_retry_backoff_rate_limit": {
            "ok": _contains_all(
                resolve_text,
                (
                    "default_retries",
                    "default_backoff_base_sec",
                    "default_rate_limit_rps",
                    "retry_backoff",
                    "rate_limit_wait",
                ),
            )
        },
        "download_artifact_hashing_present": {
            "ok": _contains_all(
                download_text,
                ("hashlib.sha256", "make_artifact_id", "_format_to_kind"),
            )
        },
        "download_retry_backoff_rate_limit": {
            "ok": _contains_all(
                download_text,
                (
                    "default_retries",
                    "default_backoff_base_sec",
                    "default_rate_limit_rps",
                    "retry_backoff",
                    "rate_limit_wait",
                ),
            )
        },
        "extract_page_level_pdf_present": {
            "ok": _contains_all(
                extract_text,
                (
                    "_default_pdf_page_extractor",
                    "normalize_text_v1",
                    "page_index",
                ),
            )
        },
        "extract_needs_ocr_present": {
            "ok": _contains_all(
                extract_text,
                ("needs_ocr", "docs_needs_ocr"),
            )
        },
        "segment_page_index_deterministic_present": {
            "ok": _contains_all(
                segment_text,
                ("make_segment_id", "_coerce_page_index", "page_index"),
            )
        },
        "segment_needs_ocr_skip_present": {
            "ok": _contains_all(
                segment_text,
                ("needs_ocr", "segments_skipped_needs_ocr"),
            )
        },
        "segment_traceability_fields_present": {
            "ok": _contains_all(
                segment_text,
                ("source_artifact_id", "text_sha256", "doc_version_id"),
            )
        },
        "load_idempotent_upsert_filters_present": {
            "ok": _contains_all(
                load_text,
                (
                    "upsert_one",
                    "_segment_filter",
                    "_document_version_filter",
                    "_artifact_filter",
                    "_source_item_filter",
                ),
            )
        },
        "load_ingest_runs_present": {
            "ok": "ingest_runs" in load_text.casefold(),
        },
        "validate_invariants_catalog_present": {
            "ok": _contains_all(validate_text, REQUIRED_VALIDATE_CHECK_IDS),
        },
        "reprocess_manifest_pipeline_present": {
            "ok": _contains_all(
                reprocess_text,
                (
                    "from_manifest",
                    "extract.extract_source_items",
                    "segment.segment_extractions",
                    "load.load_stage_payload",
                ),
            )
        },
    }


def _detect_data_layer_contracts(repo_root: Path) -> dict[str, dict[str, Any]]:
    mongo_path = repo_root / "jurisparse_un" / "db" / "mongo.py"
    storage_path = repo_root / "jurisparse_un" / "storage" / "contracts.py"
    normalize_path = repo_root / "jurisparse_un" / "text" / "normalize.py"

    mongo_text = _read_text(mongo_path)
    storage_text = _read_text(storage_path)
    normalize_text = _read_text(normalize_path)

    mongo_contract_ok = all(collection in mongo_text for collection in REQUIRED_COLLECTIONS)
    storage_path_ok = _contains_all(
        storage_text,
        ("build_artifact_object_path", "artifacts/{provider}", "sha256"),
    )
    normalize_rules_ok = _contains_all(
        normalize_text,
        (
            "replace(\"\\r\\n\", \"\\n\")",
            "replace(\"\\u00a0\", \" \")",
            "strip()",
        ),
    )

    return {
        "mongo_collections_contract_present": {
            "ok": mongo_contract_ok,
            "path": str(mongo_path.relative_to(repo_root)),
        },
        "storage_path_builder_present": {
            "ok": storage_path_ok,
            "path": str(storage_path.relative_to(repo_root)),
        },
        "text_normalization_rules_present": {
            "ok": normalize_rules_ok,
            "path": str(normalize_path.relative_to(repo_root)),
        },
    }


def _detect_test_coverage_contracts(repo_root: Path) -> dict[str, dict[str, Any]]:
    stage_test_paths = [repo_root / rel for rel in REQUIRED_STAGE_TEST_FILES]
    stage_tests_present = all(path.is_file() for path in stage_test_paths)

    ids_test = repo_root / "tests" / "jurisparse_un" / "test_ids.py"
    segment_test = repo_root / "tests" / "jurisparse_un" / "test_segment.py"
    validate_test = repo_root / "tests" / "jurisparse_un" / "test_validate.py"
    load_test = repo_root / "tests" / "jurisparse_un" / "test_load.py"
    extract_test = repo_root / "tests" / "jurisparse_un" / "test_extract.py"

    ids_text = _read_text(ids_test)
    segment_text = _read_text(segment_test)
    validate_text = _read_text(validate_test)
    load_text = _read_text(load_test)
    extract_text = _read_text(extract_test)

    invariant_tests_present = _contains_all(
        validate_text,
        (
            "needs_ocr_has_no_segments",
            "segments_source_artifact_exists",
            "segments_text_sha256_matches",
            "segment_id_deterministic",
        ),
    )
    load_idempotency_test_present = "deduplicates_entities_by_upsert_keys" in load_text
    needs_ocr_tests_present = _contains_all(
        extract_text + "\n" + segment_text + "\n" + load_text,
        ("needs_ocr", "segments_skipped_needs_ocr"),
    )

    lookup_fixture_paths = [repo_root / rel for rel in REQUIRED_LOOKUP_FIXTURES]
    lookup_fixtures_present = all(path.is_file() for path in lookup_fixture_paths)

    return {
        "stage_tests_present": {
            "ok": stage_tests_present,
            "missing_paths": [
                str(path.relative_to(repo_root))
                for path in stage_test_paths
                if not path.is_file()
            ],
        },
        "invariant_tests_present": {
            "ok": invariant_tests_present,
            "paths": [
                str(validate_test.relative_to(repo_root)),
            ],
        },
        "load_idempotency_test_present": {
            "ok": load_idempotency_test_present,
            "path": str(load_test.relative_to(repo_root)),
        },
        "needs_ocr_tests_present": {
            "ok": needs_ocr_tests_present,
            "paths": [
                str(extract_test.relative_to(repo_root)),
                str(segment_test.relative_to(repo_root)),
                str(load_test.relative_to(repo_root)),
            ],
        },
        "lookup_sync_fixtures_present": {
            "ok": lookup_fixtures_present,
            "missing_paths": [
                str(path.relative_to(repo_root))
                for path in lookup_fixture_paths
                if not path.is_file()
            ],
        },
        "ids_tests_present": {
            "ok": ids_test.is_file() and "make_segment_id" in ids_text,
            "path": str(ids_test.relative_to(repo_root)),
        },
    }


def _detect_docs_contracts(repo_root: Path) -> dict[str, Any]:
    tech_spec_path = repo_root / "docs" / "TECH_SPEC.md"
    tech_spec_text = _read_text(tech_spec_path)
    tech_spec_ok = tech_spec_path.is_file()
    tech_spec_core_sections_present = _contains_all(
        tech_spec_text,
        (
            "2.2 инварианты",
            "4.1 стадии",
            "8) модель данных mongodb",
            "11) валидация",
            "13.2 критерии приёмки",
        ),
    )

    return {
        "tech_spec_present": tech_spec_ok,
        "tech_spec_core_sections_present": tech_spec_core_sections_present,
        "tech_spec_path": str(tech_spec_path.relative_to(repo_root)),
    }


def _requirement(
    *,
    requirement_id: str,
    description: str,
    ok: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": requirement_id,
        "description": description,
        "ok": bool(ok),
    }
    if details:
        payload["details"] = details
    return payload


def evaluate_repo(repo_root: Path) -> dict[str, Any]:
    missing: list[str] = []
    notes: list[str] = []
    checked: dict[str, Any] = {}

    package_dir = _find_existing(repo_root, PACKAGE_CANDIDATES)
    if package_dir is None:
        missing.append(
            "python_package_skeleton: expected one of "
            + ", ".join(PACKAGE_CANDIDATES)
        )
        checked["python_package"] = {"ok": False, "path": None}
    else:
        package_init = package_dir / "__init__.py"
        package_ok = package_init.is_file()
        if not package_ok:
            missing.append(
                f"python_package_init: missing {package_init.relative_to(repo_root)}"
            )
        checked["python_package"] = {
            "ok": package_ok,
            "path": str(package_dir.relative_to(repo_root)),
            "init_path": str(package_init.relative_to(repo_root)),
        }

    cli_info = _detect_cli_info(package_dir, repo_root)
    cli_stage_ok = bool(cli_info["module_runnable"]) and not cli_info["missing_subcommands"]
    if not cli_info["module_runnable"]:
        missing.append(
            "cli_module_entrypoint: missing runnable module entry "
            "(expected jurisparse_un/__main__.py or src/jurisparse_un/__main__.py)"
        )
    if cli_info["missing_subcommands"]:
        missing.append(
            "cli_subcommands: missing " + ", ".join(cli_info["missing_subcommands"])
        )
    checked["cli"] = {
        "ok": cli_stage_ok,
        "module_runnable": cli_info["module_runnable"],
        "paths": cli_info["paths"],
        "missing_subcommands": cli_info["missing_subcommands"],
        "found_subcommands": cli_info["found_subcommands"],
        "contract": cli_info["contract"],
    }

    config_path = _find_existing(repo_root, CONFIG_CANDIDATES)
    config_ok = config_path is not None and config_path.is_file()
    if not config_ok:
        missing.append("config_skeleton: expected one of " + ", ".join(CONFIG_CANDIDATES))
    checked["config_skeleton"] = {
        "ok": config_ok,
        "path": str(config_path.relative_to(repo_root)) if config_path else None,
    }

    manifest_path = repo_root / "tests" / "data" / "test_manifest.jsonl"
    manifest_exists = manifest_path.is_file()
    manifest_has_rows = manifest_exists and _manifest_has_rows(manifest_path)
    if not manifest_exists:
        missing.append("test_manifest: missing tests/data/test_manifest.jsonl")
    elif not manifest_has_rows:
        missing.append("test_manifest: tests/data/test_manifest.jsonl is empty")
    checked["test_manifest"] = {
        "ok": manifest_exists and manifest_has_rows,
        "path": str(manifest_path.relative_to(repo_root)),
        "has_rows": manifest_has_rows,
    }

    readme_path = repo_root / "README.md"
    readme_text = _read_text(readme_path).lower() if readme_path.is_file() else ""
    readme_has_run_section = (
        "how to run" in readme_text or "как запустить" in readme_text
    )
    readme_has_validate_section = "validate" in readme_text
    if not readme_path.is_file():
        missing.append("readme: missing README.md")
    elif not readme_has_run_section:
        missing.append("readme: missing 'How to run' section marker")
    checked["readme"] = {
        "ok": readme_path.is_file() and readme_has_run_section,
        "path": str(readme_path.relative_to(repo_root)),
        "has_run_section": readme_has_run_section,
        "has_validate_section": readme_has_validate_section,
    }

    docs_contract = _detect_docs_contracts(repo_root)
    checked["docs"] = docs_contract

    stage_checks = _detect_stage_implementations(package_dir, repo_root)
    checked["stages"] = {
        key: {inner_key: value for inner_key, value in item.items() if inner_key != "text"}
        for key, item in stage_checks.items()
    }

    ids_capability = _detect_ids_capability(package_dir, repo_root)
    checked["ids_capability"] = {
        key: value for key, value in ids_capability.items() if key != "text"
    }

    storage_dir = repo_root / "jurisparse_un" / "storage"
    db_dir = repo_root / "jurisparse_un" / "db"
    normalize_path = repo_root / "jurisparse_un" / "text" / "normalize.py"

    checked["storage_layer"] = {
        "ok": storage_dir.is_dir(),
        "path": str(storage_dir.relative_to(repo_root)),
    }
    checked["mongo_layer"] = {
        "ok": db_dir.is_dir(),
        "path": str(db_dir.relative_to(repo_root)),
    }
    checked["text_normalization"] = {
        "ok": normalize_path.is_file(),
        "path": str(normalize_path.relative_to(repo_root)),
    }

    if package_dir is not None and not checked["python_package"]["ok"]:
        notes.append("Package directory exists but __init__.py is missing.")
    if cli_info["paths"] and cli_info["missing_subcommands"]:
        notes.append(
            "CLI files found but required stage subcommand names were not all detected."
        )

    stage_contracts = _detect_stage_contracts(stage_checks)
    checked["stage_contracts"] = stage_contracts

    data_layer_contracts = _detect_data_layer_contracts(repo_root)
    checked["data_layer_contracts"] = data_layer_contracts

    test_contracts = _detect_test_coverage_contracts(repo_root)
    checked["test_contracts"] = test_contracts

    requirements = [
        _requirement(
            requirement_id="cli_stage_surface_complete",
            description="CLI exposes all required Stage 1 stage commands.",
            ok=cli_stage_ok,
        ),
        _requirement(
            requirement_id="cli_ingest_command_present",
            description="CLI includes end-to-end ingest command required by TECH_SPEC section 5.",
            ok=checked["cli"]["contract"]["ingest_command_present"],
        ),
        _requirement(
            requirement_id="cli_from_manifest_option_present",
            description="CLI includes --from-manifest option for manifest/reprocess flows.",
            ok=checked["cli"]["contract"]["from_manifest_option_present"],
        ),
        _requirement(
            requirement_id="cli_from_db_option_present",
            description="CLI includes --from-db option for DB-backed stage runs.",
            ok=checked["cli"]["contract"]["from_db_option_present"],
        ),
        _requirement(
            requirement_id="cli_dry_run_option_present",
            description="CLI includes --dry-run option as required in TECH_SPEC modes.",
            ok=checked["cli"]["contract"]["dry_run_option_present"],
        ),
        _requirement(
            requirement_id="config_skeleton_present",
            description="MVP-1 config skeleton exists.",
            ok=config_ok,
        ),
        _requirement(
            requirement_id="manifest_fixture_present",
            description="tests/data/test_manifest.jsonl exists and is non-empty.",
            ok=manifest_exists and manifest_has_rows,
        ),
        _requirement(
            requirement_id="tech_spec_present",
            description="docs/TECH_SPEC.md exists in repository.",
            ok=docs_contract["tech_spec_present"],
        ),
        _requirement(
            requirement_id="tech_spec_core_sections_present",
            description="TECH_SPEC includes core sections (invariants, stages, data model, validate, acceptance).",
            ok=docs_contract["tech_spec_core_sections_present"],
            details={"path": docs_contract["tech_spec_path"]},
        ),
        _requirement(
            requirement_id="readme_run_section_present",
            description="README includes runnable quick-start instructions.",
            ok=checked["readme"]["has_run_section"],
            details={"path": checked["readme"]["path"]},
        ),
        _requirement(
            requirement_id="readme_validate_section_present",
            description="README references validate command/report usage.",
            ok=checked["readme"]["has_validate_section"],
            details={"path": checked["readme"]["path"]},
        ),
    ]

    for stage_key in (
        "lookup_sync",
        "crawl",
        "resolve",
        "download",
        "extract",
        "segment",
        "load",
        "validate",
        "reprocess",
    ):
        requirement_id = f"{stage_key}_stage_implemented"
        description = f"{stage_key} stage is implemented (not stub)."
        requirements.append(
            _requirement(
                requirement_id=requirement_id,
                description=description,
                ok=stage_checks[stage_key]["ok"],
                details={
                    "path": stage_checks[stage_key]["path"],
                    "reason": stage_checks[stage_key]["reason"],
                },
            )
        )

    requirements.extend(
        [
            _requirement(
                requirement_id="deterministic_id_helpers",
                description=(
                    "Deterministic ID helpers for doc/doc_version/artifact/segment/"
                    "source_item are present."
                ),
                ok=ids_capability["ok"],
                details={
                    "path": ids_capability.get("path"),
                    "missing_functions": ids_capability.get("missing_functions", []),
                },
            ),
            _requirement(
                requirement_id="page_index_1_based_guard",
                description="Segment ID helper enforces page_index >= 1.",
                ok=bool(ids_capability.get("page_index_guard")),
                details={"path": ids_capability.get("path")},
            ),
            _requirement(
                requirement_id="id_formula_uuidv5_present",
                description="UUIDv5 formulas for doc_id/doc_version_id/artifact_id are explicit in ids helpers.",
                ok=bool(ids_capability.get("uuidv5_formula_present")),
                details={"path": ids_capability.get("path")},
            ),
            _requirement(
                requirement_id="source_item_sha1_formula_present",
                description="Source item deterministic SHA1 formula is explicit in ids helpers.",
                ok=bool(ids_capability.get("source_item_sha1_formula_present")),
                details={"path": ids_capability.get("path")},
            ),
            _requirement(
                requirement_id="lookup_sync_dynamic_labels_present",
                description="lookup_sync performs dynamic label parsing and required-label validation.",
                ok=stage_contracts["lookup_sync_dynamic_labels_present"]["ok"],
            ),
            _requirement(
                requirement_id="lookup_sync_no_hardcoded_ids",
                description="lookup_sync does not hardcode TreatyID/DocTypeID constants.",
                ok=stage_contracts["lookup_sync_no_hardcoded_ids"]["ok"],
            ),
            _requirement(
                requirement_id="crawl_retry_backoff_rate_limit",
                description="crawl includes timeout/retry/backoff/rate-limit behavior.",
                ok=stage_contracts["crawl_retry_backoff_rate_limit"]["ok"],
            ),
            _requirement(
                requirement_id="resolve_format_priority_present",
                description="resolve applies deterministic format priority (pdf > docx > html).",
                ok=stage_contracts["resolve_format_priority_present"]["ok"],
            ),
            _requirement(
                requirement_id="resolve_retry_backoff_rate_limit",
                description="resolve includes timeout/retry/backoff/rate-limit behavior.",
                ok=stage_contracts["resolve_retry_backoff_rate_limit"]["ok"],
            ),
            _requirement(
                requirement_id="download_artifact_hashing_present",
                description="download computes deterministic artifact hash/id metadata.",
                ok=stage_contracts["download_artifact_hashing_present"]["ok"],
            ),
            _requirement(
                requirement_id="download_retry_backoff_rate_limit",
                description="download includes timeout/retry/backoff/rate-limit behavior.",
                ok=stage_contracts["download_retry_backoff_rate_limit"]["ok"],
            ),
            _requirement(
                requirement_id="extract_page_level_pdf_present",
                description="extract includes page-level PDF path with text normalization.",
                ok=stage_contracts["extract_page_level_pdf_present"]["ok"],
            ),
            _requirement(
                requirement_id="extract_needs_ocr_present",
                description="extract marks needs_ocr and tracks docs_needs_ocr stats.",
                ok=stage_contracts["extract_needs_ocr_present"]["ok"],
            ),
            _requirement(
                requirement_id="segment_page_index_deterministic_present",
                description="segment uses deterministic page_index-aware segment_id creation.",
                ok=stage_contracts["segment_page_index_deterministic_present"]["ok"],
            ),
            _requirement(
                requirement_id="segment_needs_ocr_skip_present",
                description="segment contains explicit needs_ocr skip behavior.",
                ok=stage_contracts["segment_needs_ocr_skip_present"]["ok"],
            ),
            _requirement(
                requirement_id="segment_traceability_fields_present",
                description="segment payload includes source_artifact_id and text_sha256 traceability fields.",
                ok=stage_contracts["segment_traceability_fields_present"]["ok"],
            ),
            _requirement(
                requirement_id="load_idempotent_upsert_filters_present",
                description="load defines idempotent upsert filters for core collections.",
                ok=stage_contracts["load_idempotent_upsert_filters_present"]["ok"],
            ),
            _requirement(
                requirement_id="load_ingest_runs_present",
                description="load updates ingest_runs collection payload.",
                ok=stage_contracts["load_ingest_runs_present"]["ok"],
            ),
            _requirement(
                requirement_id="validate_invariants_catalog_present",
                description="validate stage includes core TECH_SPEC invariant checks.",
                ok=stage_contracts["validate_invariants_catalog_present"]["ok"],
            ),
            _requirement(
                requirement_id="reprocess_manifest_pipeline_present",
                description="reprocess orchestrates manifest -> extract -> segment -> load flow.",
                ok=stage_contracts["reprocess_manifest_pipeline_present"]["ok"],
            ),
            _requirement(
                requirement_id="mongo_collections_contract_present",
                description="Mongo contract includes required Stage 1 collections.",
                ok=data_layer_contracts["mongo_collections_contract_present"]["ok"],
                details={"path": data_layer_contracts["mongo_collections_contract_present"]["path"]},
            ),
            _requirement(
                requirement_id="storage_path_builder_present",
                description="Storage layer exposes deterministic artifact path builder.",
                ok=data_layer_contracts["storage_path_builder_present"]["ok"],
                details={"path": data_layer_contracts["storage_path_builder_present"]["path"]},
            ),
            _requirement(
                requirement_id="text_normalization_present",
                description="Text normalization module exists (jurisparse_un/text/normalize.py).",
                ok=checked["text_normalization"]["ok"],
                details={"path": checked["text_normalization"]["path"]},
            ),
            _requirement(
                requirement_id="text_normalization_rules_present",
                description="Text normalization implements TECH_SPEC v1 normalization rules.",
                ok=data_layer_contracts["text_normalization_rules_present"]["ok"],
                details={"path": data_layer_contracts["text_normalization_rules_present"]["path"]},
            ),
            _requirement(
                requirement_id="stage_tests_present",
                description="Stage-level unit tests exist for lookup/crawl/resolve/download/extract/segment/load/validate/reprocess.",
                ok=test_contracts["stage_tests_present"]["ok"],
                details={"missing_paths": test_contracts["stage_tests_present"]["missing_paths"]},
            ),
            _requirement(
                requirement_id="invariant_tests_present",
                description="Validation/invariant unit tests explicitly cover core invariant IDs.",
                ok=test_contracts["invariant_tests_present"]["ok"],
                details={"paths": test_contracts["invariant_tests_present"]["paths"]},
            ),
            _requirement(
                requirement_id="load_idempotency_test_present",
                description="Load tests include idempotency/deduplication scenario.",
                ok=test_contracts["load_idempotency_test_present"]["ok"],
                details={"path": test_contracts["load_idempotency_test_present"]["path"]},
            ),
            _requirement(
                requirement_id="needs_ocr_tests_present",
                description="Tests cover needs_ocr behavior across extract/segment/load.",
                ok=test_contracts["needs_ocr_tests_present"]["ok"],
                details={"paths": test_contracts["needs_ocr_tests_present"]["paths"]},
            ),
            _requirement(
                requirement_id="lookup_sync_fixtures_present",
                description="lookup_sync offline fixtures exist for dynamic and fail-fast cases.",
                ok=test_contracts["lookup_sync_fixtures_present"]["ok"],
                details={"missing_paths": test_contracts["lookup_sync_fixtures_present"]["missing_paths"]},
            ),
            _requirement(
                requirement_id="ids_tests_present",
                description="Deterministic ID tests are present.",
                ok=test_contracts["ids_tests_present"]["ok"],
                details={"path": test_contracts["ids_tests_present"]["path"]},
            ),
            _requirement(
                requirement_id="storage_layer_present",
                description="Storage layer package exists (jurisparse_un/storage).",
                ok=checked["storage_layer"]["ok"],
                details={"path": checked["storage_layer"]["path"]},
            ),
            _requirement(
                requirement_id="mongo_layer_present",
                description="Mongo layer package exists (jurisparse_un/db).",
                ok=checked["mongo_layer"]["ok"],
                details={"path": checked["mongo_layer"]["path"]},
            ),
        ]
    )

    requirements_total = len(requirements)
    requirements_met = sum(1 for item in requirements if item["ok"])
    techspec_coverage_pct = round((requirements_met / requirements_total) * 100.0, 2)
    unresolved_requirement_ids = [
        str(item["id"]).strip()
        for item in requirements
        if not item["ok"]
    ]
    missing_requirements = [
        f"{item['id']}: {item['description']}"
        for item in requirements
        if not item["ok"]
    ]

    checked["requirements"] = {
        "total": requirements_total,
        "met": requirements_met,
        "items": requirements,
    }

    ok = not missing and not unresolved_requirement_ids
    return {
        "ok": ok,
        "missing": missing,
        "missing_requirements": missing_requirements,
        "unresolved_requirements": unresolved_requirement_ids,
        "unresolved_requirements_count": len(unresolved_requirement_ids),
        "machine_ready_for_completion": ok,
        "techspec_coverage_pct": techspec_coverage_pct,
        "techspec_coverage_target_pct": TECHSPEC_COVERAGE_TARGET_PCT,
        "notes": notes,
        "checked": checked,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic offline readiness check for JurisParse_UN TECH_SPEC "
            "capability coverage."
        )
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root to inspect (defaults to current working directory).",
    )
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="text",
        help="Output format. Use --format json for machine parsing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    result = evaluate_repo(repo_root)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False))
    else:
        status = "PASS" if result["ok"] else "FAIL"
        print(f"JurisParse TECH_SPEC capability check: {status}")
        print(
            "TECH_SPEC coverage: "
            f"{result['techspec_coverage_pct']}% / "
            f"{result['techspec_coverage_target_pct']}%"
        )
        if result["missing"]:
            print("Missing baseline scaffold:")
            for item in result["missing"]:
                print(f"- {item}")
        if result["missing_requirements"]:
            print("Missing requirements:")
            for item in result["missing_requirements"]:
                print(f"- {item}")
        if result["notes"]:
            print("Notes:")
            for item in result["notes"]:
                print(f"- {item}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
