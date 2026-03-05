# Open Source Release Checklist

## 1) Secrets and confidential data

- [ ] `.env` is not tracked (`git ls-files .env` returns nothing).
- [ ] No credentials in tracked files (`rg` scan over workspace).
- [ ] No credentials in history (`git grep` over `git rev-list --all`).
- [ ] If leaks are found: rotate keys, rewrite history, force-push.

## 2) Repository hygiene

- [ ] Runtime artifacts are git-ignored (`reports/`, `.codexflow/_tmp`, etc).
- [ ] `README.md` describes project goal, context, and quick start.
- [ ] `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md` are present.
- [ ] TechSpec path/documentation is clear (`docs/TECH_SPEC.md`).

## 3) Reproducibility and quality

- [ ] `ruff check .` passes.
- [ ] `pytest -q` passes.
- [ ] Product compliance check passes:
      `python scripts/jurisparse_mvp1_check.py --format json --repo-root .`

## 4) Publication

- [ ] Final diff reviewed for accidental data disclosure.
- [ ] Release branch pushed.
- [ ] Public repository settings reviewed (issues, security advisories, license).
