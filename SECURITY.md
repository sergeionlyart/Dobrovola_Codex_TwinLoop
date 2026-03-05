# Security Policy

## Supported scope

Security issues are accepted for:

- workflow runtime (`codexflow/`, `scripts/codexflow*.sh`);
- microservice runtime (`jurisparse_un/`);
- CI/quality scripts and release process.

## Reporting a vulnerability

Please do not open public issues for sensitive reports.

Use one of the following channels:

- GitHub Security Advisories (preferred);
- private contact with repository maintainers.

Include:

1. Affected file/path and attack scenario.
2. Steps to reproduce.
3. Impact assessment.
4. Suggested mitigation (if available).

## Secret handling policy

- `.env` and any credential files are local-only and must never be committed.
- Use `.env.example` as a template without real values.
- If a secret is exposed, rotate it immediately and remove it from git history.
