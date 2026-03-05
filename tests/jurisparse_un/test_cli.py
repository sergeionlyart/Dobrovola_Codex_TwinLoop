from __future__ import annotations

import argparse
import importlib
import uuid as uuid_lib
from typing import Any

import pytest

from jurisparse_un.cli import STAGE_COMMANDS, build_parser, main


REQUIRED_COMMANDS = {
    "lookup-sync",
    "crawl",
    "resolve",
    "download",
    "extract",
    "segment",
    "load",
    "validate",
    "reprocess",
    "ingest",
    "export-manifest",
}


def _subparser_choices(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return set(action.choices.keys())
    return set()


def _find_subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return action.choices[name]
    raise AssertionError(f"missing subparser: {name}")


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    return {
        option
        for action in parser._actions  # noqa: SLF001
        for option in action.option_strings
    }


def test_cli_exposes_required_commands() -> None:
    parser = build_parser()
    assert REQUIRED_COMMANDS.issubset(_subparser_choices(parser))


def test_cli_common_options_are_available() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "ingest",
            "--config",
            "config.example.yaml",
            "--run-id",
            "RUN-1",
            "--dry-run",
            "--from-manifest",
            "tests/data/test_manifest.jsonl",
            "--from-db",
            "--doc-id",
            "doc-1",
            "--doc-version-id",
            "docv-1",
            "--max-docs",
            "5",
            "--max-per-committee",
            "2",
            "--rate-limit-rps",
            "1.5",
            "--retries",
            "3",
            "--log-json",
        ]
    )

    assert args.config == "config.example.yaml"
    assert args.run_id == "RUN-1"
    assert args.dry_run is True
    assert args.from_manifest == "tests/data/test_manifest.jsonl"
    assert args.from_db is True
    assert args.doc_id == "doc-1"
    assert args.doc_version_id == "docv-1"
    assert args.max_docs == 5
    assert args.max_per_committee == 2
    assert args.rate_limit_rps == 1.5
    assert args.retries == 3
    assert args.log_json is True


def test_validate_command_exposes_selector_options() -> None:
    parser = build_parser()
    validate_parser = _find_subparser(parser, "validate")
    options = _option_strings(validate_parser)

    assert {"--run-id", "--doc-id", "--doc-version-id"}.issubset(options)


def test_ingest_command_exposes_operational_and_log_options() -> None:
    parser = build_parser()
    ingest_parser = _find_subparser(parser, "ingest")
    options = _option_strings(ingest_parser)

    assert {
        "--max-docs",
        "--max-per-committee",
        "--rate-limit-rps",
        "--retries",
        "--log-json",
    }.issubset(options)


def test_stage_modules_export_run() -> None:
    for command in STAGE_COMMANDS:
        module_name = command.replace("-", "_")
        module = importlib.import_module(f"jurisparse_un.stages.{module_name}")
        assert hasattr(module, "run")


def test_main_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0


def test_validate_returns_non_zero_exit_code_on_failed_invariants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_runner(**_: Any) -> dict[str, Any]:
        return {"ok": False}

    monkeypatch.setattr("jurisparse_un.cli._load_stage_runner", lambda _command: fake_runner)

    assert main(["validate", "--run-id", "RUN-1"]) == 1


def test_main_generates_uuid4_run_id_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}

    def fake_runner(**kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {"ok": True}

    deterministic_uuid = uuid_lib.UUID("12345678-1234-4abc-8def-1234567890ab")
    monkeypatch.setattr("jurisparse_un.cli._load_stage_runner", lambda _command: fake_runner)
    monkeypatch.setattr("jurisparse_un.cli.uuid.uuid4", lambda: deterministic_uuid)

    assert main(["crawl"]) == 0
    assert captured_kwargs["run_id"] == str(deterministic_uuid)
