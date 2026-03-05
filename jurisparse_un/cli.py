"""CLI surface for deterministic JurisParse MVP-1 scaffold."""

from __future__ import annotations

import argparse
import importlib
import json
from typing import Any

STAGE_COMMANDS = (
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


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config.example.yaml")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-manifest", default=None)
    parser.add_argument("--from-db", action="store_true")


def _stage_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "config": args.config,
        "run_id": args.run_id,
        "dry_run": args.dry_run,
        "from_manifest": args.from_manifest,
        "from_db": args.from_db,
    }


def _load_stage_runner(command: str) -> Any:
    module_name = command.replace("-", "_")
    module = importlib.import_module(f"jurisparse_un.stages.{module_name}")
    return module.run


def _run_single_stage(command: str, args: argparse.Namespace) -> int:
    runner = _load_stage_runner(command)
    payload = runner(**_stage_kwargs(args))
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _run_ingest(args: argparse.Namespace) -> int:
    stage_results = []
    for command in STAGE_COMMANDS:
        runner = _load_stage_runner(command)
        stage_results.append(runner(**_stage_kwargs(args)))

    payload = {
        "command": "ingest",
        "stages": stage_results,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jurisparse_un",
        description="Deterministic JurisParse MVP-1 scaffold CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    for command in (*STAGE_COMMANDS, "ingest"):
        command_parser = subparsers.add_parser(command)
        _add_common_options(command_parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "ingest":
        return _run_ingest(args)
    return _run_single_stage(args.command, args)
