"""Whole-release review before 2.4.0 (2026-10-18)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

import pytest

from vmware_pilot import dispatcher as dispatcher_mod
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text


class _Frame:
    def __init__(self, texts, structured=None, is_error=False):
        self.content = [_Text(t) for t in texts]
        self.structuredContent = structured
        self.isError = is_error


@pytest.mark.unit
def test_an_error_frame_with_a_json_envelope_keeps_its_fields():
    """M1: family tools are '-> dict' with no structuredContent; with Policy 1.24.2
    their failures arrive isError=True and the envelope must not be flattened."""
    body = json.dumps({"error": "VM web01 not found", "hint": "list_vms"}, indent=2)
    assert dispatcher_mod._payload(_Frame([body], is_error=True)) == {
        "error": "VM web01 not found", "hint": "list_vms"}


@pytest.mark.unit
def test_an_error_frame_with_prose_is_still_an_envelope():
    out = dispatcher_mod._payload(_Frame(["Error: boom. Run x."], is_error=True))
    assert out == {"error": "Error: boom. Run x."}


def _wf() -> Workflow:
    return Workflow(
        id="wf-rb", workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="on", skill="aiops", tool="power_on_vm", params={},
                         rollback_tool="power_off_vm", rollback_params={"confirm": True}),
            WorkflowStep(index=1, action="fail", skill="aiops", tool="failing_tool", params={}),
        ],
        params={}, created_at="", updated_at="",
    )


def _dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
    if tool in ("failing_tool", "power_off_vm"):
        return {"error": f"{tool} failed"}
    return {"ok": True}


@pytest.mark.unit
def test_a_rollback_with_a_failed_undo_reports_failure(tmp_path, monkeypatch):
    """M2: Policy marks a call failed by a top-level error/outcome; the rollback
    result had neither, so a failed undo read as success."""
    import vmware_pilot.mcp_server.server as server

    store = WorkflowStore(tmp_path / "wf.db")
    ex = WorkflowExecutor(store, dispatch=_dispatch)
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_executor", ex)
    wf = _wf()
    store.save(wf)
    ex.run_until_checkpoint(wf)
    out = server.rollback("wf-rb", confirm=True)
    assert out["outcome"] == "failed"
    assert "power_off_vm" in out["error"]
    assert out["action"] == "rollback_incomplete"


@pytest.mark.unit
def test_a_clean_rollback_is_not_a_failure(tmp_path, monkeypatch):
    import vmware_pilot.mcp_server.server as server

    store = WorkflowStore(tmp_path / "wf.db")
    ex = WorkflowExecutor(store, dispatch=lambda s, t, p: {"error": "x"} if t == "failing_tool"
                          else {"ok": True})
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_executor", ex)
    wf = _wf()
    store.save(wf)
    ex.run_until_checkpoint(wf)
    out = server.rollback("wf-rb", confirm=True)
    assert out["action"] == "rolled_back"
    assert not out.get("error") and out.get("outcome") != "failed"


@pytest.mark.unit
def test_a_kubeconfig_in_a_step_result_is_not_persisted(tmp_path):
    """M3: under --dispatch a child's kubeconfig (bearer token) was stored in clear."""
    store = WorkflowStore(tmp_path / "wf.db")
    wf = Workflow(
        id="wf-kc", workflow_type="test", state=WorkflowState.PENDING,
        steps=[WorkflowStep(index=0, action="kc", skill="vks", tool="issue_tkc_kubeconfig",
                            params={})],
        params={}, created_at="", updated_at="",
    )
    secret = "apiVersion: v1\nusers:\n- user:\n    token: eyJhbGciOiJ-SECRET"
    WorkflowExecutor(store, dispatch=lambda s, t, p: {"namespace": "ns", "kubeconfig": secret}
                     ).run_until_checkpoint(wf)
    with closing(sqlite3.connect(tmp_path / "wf.db")) as conn:
        row = conn.execute("SELECT data FROM workflows WHERE id='wf-kc'").fetchone()[0]
    assert "SECRET" not in row
