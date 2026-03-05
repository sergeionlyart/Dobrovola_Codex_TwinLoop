from __future__ import annotations

import argparse
import importlib

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
}


def _subparser_choices(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return set(action.choices.keys())
    return set()


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
        ]
    )

    assert args.config == "config.example.yaml"
    assert args.run_id == "RUN-1"
    assert args.dry_run is True
    assert args.from_manifest == "tests/data/test_manifest.jsonl"
    assert args.from_db is True


def test_stage_modules_export_run() -> None:
    for command in STAGE_COMMANDS:
        module_name = command.replace("-", "_")
        module = importlib.import_module(f"jurisparse_un.stages.{module_name}")
        assert hasattr(module, "run")


def test_main_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
