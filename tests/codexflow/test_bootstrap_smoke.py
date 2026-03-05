from __future__ import annotations

from codexflow.dispatcher import bootstrap_state


def test_bootstrap_state_defaults() -> None:
    state = bootstrap_state()
    assert state["run_id"] == "RUN-BOOTSTRAP"
    assert state["phase"] == "INIT"
    assert state["iteration"] == 0
    assert state["approval"]["required"] is False
