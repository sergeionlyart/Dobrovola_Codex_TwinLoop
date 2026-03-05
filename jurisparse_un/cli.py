"""CLI surface for deterministic JurisParse MVP-1 scaffold."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import uuid
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
    parser.add_argument("--doc-id", default=None)
    parser.add_argument("--doc-version-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-manifest", default=None)
    parser.add_argument("--from-db", action="store_true")
    parser.add_argument("--max-docs", type=int, default=None)
    parser.add_argument("--max-per-committee", type=int, default=None)
    parser.add_argument("--rate-limit-rps", type=float, default=None)
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument("--log-json", action="store_true")


def _build_stage_context(
    args: argparse.Namespace,
    *,
    previous_payload: dict[str, Any] | None = None,
    stage_outputs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stage_context: dict[str, Any] = {
        "config": args.config,
        "run_id": args.run_id,
        "doc_id": args.doc_id,
        "doc_version_id": args.doc_version_id,
        "dry_run": args.dry_run,
        "from_manifest": args.from_manifest,
        "from_db": args.from_db,
        "max_docs": args.max_docs,
        "max_per_committee": args.max_per_committee,
        "rate_limit_rps": args.rate_limit_rps,
        "retries": args.retries,
        "log_json": args.log_json,
        "previous_payload": previous_payload,
        "stage_outputs": stage_outputs,
    }
    return stage_context


def _stage_kwargs(runner: Any, stage_context: dict[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(runner)
    accepts_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_var_kwargs:
        return stage_context

    return {
        key: value
        for key, value in stage_context.items()
        if key in signature.parameters
    }


def _load_stage_runner(command: str) -> Any:
    module_name = command.replace("-", "_")
    module = importlib.import_module(f"jurisparse_un.stages.{module_name}")
    return module.run


def _run_single_stage(command: str, args: argparse.Namespace) -> int:
    runner = _load_stage_runner(command)
    stage_context = _build_stage_context(args)
    payload = runner(**_stage_kwargs(runner, stage_context))
    print(json.dumps(payload, ensure_ascii=False))
    if command == "validate":
        return 0 if bool(payload.get("ok")) else 1
    return 0


def _run_ingest(args: argparse.Namespace) -> int:
    stage_results: list[dict[str, Any]] = []
    stage_outputs: dict[str, dict[str, Any]] = {}
    previous_payload: dict[str, Any] | None = None

    for command in STAGE_COMMANDS:
        runner = _load_stage_runner(command)
        next_inputs = _build_stage_context(
            args,
            previous_payload=previous_payload,
            stage_outputs=stage_outputs,
        )
        stage_payload = runner(**_stage_kwargs(runner, next_inputs))
        stage_results.append(stage_payload)
        stage_outputs[command] = stage_payload
        previous_payload = stage_payload

    payload = {
        "command": "ingest",
        "run_id": args.run_id,
        "stages": stage_results,
        "stage_outputs": stage_outputs,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _run_export_manifest(args: argparse.Namespace) -> int:
    payload = {
        "command": "export-manifest",
        "run_id": args.run_id,
        "from_manifest": args.from_manifest,
        "from_db": args.from_db,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jurisparse_un",
        description="Deterministic JurisParse MVP-1 scaffold CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    for command in (*STAGE_COMMANDS, "ingest", "export-manifest"):
        command_parser = subparsers.add_parser(command)
        _add_common_options(command_parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.run_id is None:
        args.run_id = str(uuid.uuid4())
    if args.command == "ingest":
        return _run_ingest(args)
    if args.command == "export-manifest":
        return _run_export_manifest(args)
    return _run_single_stage(args.command, args)
