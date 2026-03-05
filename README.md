# JurisParse UN MVP-1 Scaffold

Deterministic, offline-ready Stage-1 scaffold for incremental MVP-1 delivery.

## How to run

```bash
python -m jurisparse_un --help
python -m jurisparse_un ingest --config config.example.yaml --run-id RUN-DEMO --dry-run --from-manifest tests/data/test_manifest.jsonl
```

Run any individual stage:

```bash
python -m jurisparse_un lookup-sync --config config.example.yaml --run-id RUN-LOOKUP --dry-run
python -m jurisparse_un segment --config config.example.yaml --run-id RUN-SEG --from-manifest tests/data/test_manifest.jsonl
```

## Validate and reprocess usage

```bash
python -m jurisparse_un validate --config config.example.yaml --run-id RUN-VALIDATE --from-db
python -m jurisparse_un reprocess --config config.example.yaml --run-id RUN-REPROCESS --from-manifest tests/data/test_manifest.jsonl
```

## MVP-1 explicit contracts

- `page_index` is 1-based (first page is `1`).
- Processing is idempotent: no duplicate writes for `documents`, `document_versions`, `artifacts`, `segments`, or `source_items` (except `ingest_runs`).
- `lookup_sync` must resolve TBSearch IDs dynamically and must not hardcode TreatyID or DocTypeID values.
- If `needs_ocr=true`, MVP-1 emits no segments for that document version.
