from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def reset_codexflow_prompt_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODEXFLOW_MANAGER_PROMPT", raising=False)
    monkeypatch.delenv("CODEXFLOW_WORKER_PROMPT", raising=False)
