"""A workflow whose LAST step is a failed linked clone must not end "completed".

Write round 2026-10-17: vmware-aiops 2.4.0 answered a failed
``deploy_linked_clone`` ("Source VM 'x' not found.") with isError false, so the
executor recorded the step as success; as the last step, it closed the workflow
as "completed". vmware-aiops now sends the failure as an error frame. This pins
Pilot's half: the real ``mcp.types.CallToolResult`` that AIops sends now, at the
end of a workflow, fails the workflow.
"""

from __future__ import annotations

from typing import Any

import mcp.types as mcp_types
import pytest

from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore

FAILED_CLONE = (
    "Error: Source VM 'x' not found. Run list_virtual_machines (vmware-monitor skill) "
    "for exact names. The error message says what to change; connectivity is not the "
    "problem."
)


def _wf() -> Workflow:
    return Workflow(
        id="wf-clone-last", workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="snapshot", skill="aiops",
                         tool="create_vm_snapshot", params={"vm_name": "x"}),
            WorkflowStep(index=1, action="clone", skill="aiops",
                         tool="deploy_linked_clone", params={"source_vm_name": "x"}),
        ],
        params={}, created_at="", updated_at="",
    )


def _dispatch(is_error: bool):
    def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
        if tool == "deploy_linked_clone":
            return mcp_types.CallToolResult(
                isError=is_error, content=[mcp_types.TextContent(type="text", text=FAILED_CLONE)])
        return {"ok": True}

    return dispatch


@pytest.mark.unit
def test_a_failed_clone_as_the_last_step_fails_the_workflow(tmp_path) -> None:
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf()
    store.save(wf)

    result = WorkflowExecutor(store, dispatch=_dispatch(True)).run_until_checkpoint(wf)

    assert result["state"] == "failed"
    assert result["steps"][1]["status"] != "success"
    assert "Source VM 'x' not found" in str(wf.steps[1].result)


@pytest.mark.unit
def test_the_same_text_without_the_flag_is_read_as_success(tmp_path) -> None:
    """Why the fix belongs in the skill: Pilot does not guess failure from prose.

    This is what 2.4.0 sent. Pilot reads isError and a top-level "error", not
    prose (a successful result may quote 'Error:'); if this starts failing, that
    changed, and it should be a decision rather than an accident.
    """
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _wf()
    store.save(wf)

    result = WorkflowExecutor(store, dispatch=_dispatch(False)).run_until_checkpoint(wf)

    assert result["state"] == "completed"
