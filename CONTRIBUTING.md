# Contributing

## Scope

Contributions are welcome for:

- TwinLoop workflow reliability (`codexflow/`, `scripts/codexflow*.sh`);
- microservice implementation (`jurisparse_un/`);
- tests, docs, and quality checks.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install ruff pytest
```

## Required checks before PR

```bash
ruff check .
pytest -q
python scripts/jurisparse_mvp1_check.py --format json --repo-root .
```

## Development rules

- Keep changes minimal and focused on one concern.
- Do not commit secrets, tokens, or local credentials files.
- Prefer adding tests for behavior changes.
- Keep workflow prompts and schemas in sync when changing agent contracts.

## Pull request format

Include:

1. What problem is solved.
2. Why this change is needed.
3. How it was tested.
4. Risks or rollback notes.
