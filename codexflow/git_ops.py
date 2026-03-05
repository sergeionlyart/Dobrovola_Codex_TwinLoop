from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple


class GitOpResult(NamedTuple):
    ok: bool
    output: str


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def is_git_repo(repo_root: Path) -> bool:
    cp = _git(repo_root, "rev-parse", "--is-inside-work-tree")
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def current_branch(repo_root: Path) -> str | None:
    if not is_git_repo(repo_root):
        return None
    cp = _git(repo_root, "branch", "--show-current")
    if cp.returncode != 0:
        return None
    value = cp.stdout.strip()
    return value or None


def current_commit(repo_root: Path) -> str | None:
    if not is_git_repo(repo_root):
        return None
    cp = _git(repo_root, "rev-parse", "HEAD")
    if cp.returncode != 0:
        return None
    value = cp.stdout.strip()
    return value or None


def changed_files(repo_root: Path) -> list[str]:
    if not is_git_repo(repo_root):
        return []
    cp = _git(repo_root, "status", "--short")
    if cp.returncode != 0:
        return []
    files: list[str] = []
    for line in cp.stdout.splitlines():
        chunk = line[3:].strip()
        if chunk:
            files.append(chunk)
    return files


def diff_summary(repo_root: Path) -> str:
    if not is_git_repo(repo_root):
        return "git unavailable in this workspace"
    cp = _git(repo_root, "diff", "--stat")
    if cp.returncode != 0:
        return "git diff --stat failed"
    summary = cp.stdout.strip()
    return summary or "no unstaged diff"


def branch_exists(repo_root: Path, branch: str) -> bool:
    if not is_git_repo(repo_root):
        return False
    cp = _git(repo_root, "rev-parse", "--verify", branch)
    return cp.returncode == 0


def working_tree_clean(repo_root: Path) -> bool:
    if not is_git_repo(repo_root):
        return False
    cp = _git(repo_root, "status", "--porcelain")
    if cp.returncode != 0:
        return False
    return cp.stdout.strip() == ""


def checkout_branch(repo_root: Path, branch: str) -> GitOpResult:
    cp = _git(repo_root, "checkout", branch)
    output = (cp.stdout + "\n" + cp.stderr).strip()
    return GitOpResult(ok=cp.returncode == 0, output=output)


def merge_ff(repo_root: Path, source_branch: str) -> GitOpResult:
    cp = _git(repo_root, "merge", "--ff-only", source_branch)
    output = (cp.stdout + "\n" + cp.stderr).strip()
    return GitOpResult(ok=cp.returncode == 0, output=output)
