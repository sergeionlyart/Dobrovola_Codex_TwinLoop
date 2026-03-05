# Dobrovola Codex TwinLoop (Open Source Demo)

This repository demonstrates an author-designed **TwinLoop workflow** based on
two OpenAI Codex agents:

- a **Manager/Architect** agent that plans, validates, and reviews;
- a **Worker/Implementer** agent that writes and tests code.

## Project purpose

The goal of this repository is to show a practical, reproducible pattern for
AI-assisted software delivery where two agents collaborate in controlled
execution loops.

## What this demo shows in practice

This demo shows how the TwinLoop workflow can design and implement a
microservice from a technical specification:

- workflow orchestration in `codexflow/` and `scripts/codexflow.py`;
- generated microservice code in `jurisparse_un/`;
- quality gates (`ruff`, `pytest`, product-check scripts) and review artifacts.

## Why this matters

The current TechSpec and generated codebase are one building block of the
future **Legal Copilot** initiative for human-rights organizations.

Practical value of this project:

- faster delivery of applied microservices and internal tools;
- repeatable engineering flow with explicit quality gates;
- auditable decision trail for AI-generated implementation.

## Project background

The project appeared as a public experiment to validate whether a strict
two-agent loop can reliably move from TechSpec to runnable code without manual
micromanagement of every coding step. The repository keeps both:

- the workflow mechanics (how tasks are planned/reviewed/executed);
- the resulting microservice implementation produced under this process.

## Read first

1. `docs/TECH_SPEC.md` - source requirements.
2. `codexflow/` and `scripts/codexflow.py` - TwinLoop engine.
3. `jurisparse_un/` - generated microservice.
4. `tests/` and `scripts/jurisparse_mvp1_check.py` - quality and compliance gates.

## Quick start

### 1) Requirements

- Python 3.11+
- `ruff`
- `pytest`
- OpenAI Codex CLI available in `PATH`

### 2) Configure environment

```bash
cp .env.example .env
# Fill real values in .env locally (never commit .env)
```

### 3) Run microservice CLI

```bash
python -m jurisparse_un --help
python -m jurisparse_un ingest --config config.example.yaml --run-id RUN-DEMO --dry-run --from-manifest tests/data/test_manifest.jsonl
```

### 4) Run workflow in operational mode

```bash
mkdir -p .codexflow/_tmp docs/reports
python scripts/codexflow.py preflight
python scripts/codexflow.py reset
nohup bash scripts/codexflow_operational_run.sh </dev/null > .codexflow/_tmp/operational_supervisor.log 2>&1 &
echo $! > .codexflow/_tmp/operational_supervisor.pid
```

### 5) Monitor

```bash
python scripts/codexflow.py status
tail -n 80 .codexflow/_tmp/operational_supervisor.log
```

## Quality gates

```bash
ruff check .
pytest -q
python scripts/jurisparse_mvp1_check.py --format json --repo-root .
```

## Security and open-source notes

- Secrets must stay only in local `.env` or external secret managers.
- Generated runtime logs and local reports are git-ignored.
- See `SECURITY.md` for disclosure policy and secret handling.
