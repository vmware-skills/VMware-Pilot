"""Two leftovers from the 2026-10-17 real-vSphere fixes.

1. A guest command that ran but exited non-zero (or timed out) was recorded as
   success, so the next approval said the change was applied.
2. After one rollback a clone that was only powered off became
   ``partially_rolled_back`` — and a second rollback's preview listed it nowhere.
"""

from __future__ import annotations

from typing import Any

import pytest

from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.lifecycle_gate import rollback_blast_radius
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore


def _guest_wf(tool: str, skill: str = "aiops") -> Workflow:
    return Workflow(
        id="wf-guest", workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="run", skill=skill, tool=tool,
                         params={"vm_name": "s", "command": "/bin/false", "confirm": True}),
            WorkflowStep(index=1, action="look", skill="monitor", tool="get_alarms", params={}),
        ],
        params={}, created_at="", updated_at="",
    )


def _guest_result(result: dict[str, Any]):
    def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
        return result if "guest" in tool else {"ok": True}
    return dispatch


@pytest.mark.unit
@pytest.mark.parametrize("tool", ["run_vm_guest_command", "run_vm_guest_command_with_output"])
@pytest.mark.parametrize("skill", ["aiops", "vmware-aiops"])
@pytest.mark.parametrize("result", [
    {"exit_code": 1, "stdout": "", "stderr": "boom", "timed_out": False},
    {"exit_code": -1, "stdout": "", "stderr": "", "timed_out": True},
    {"exit_code": 0, "stdout": "", "stderr": "", "timed_out": True},
    {"exit_code": -1, "stdout": "", "stderr": "process not found", "timed_out": False},
], ids=["exit-1", "timeout-minus-1", "timeout-exit-0", "minus-1"])
def test_a_guest_command_that_did_not_succeed_fails_the_step(tmp_path, tool, skill, result):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _guest_wf(tool, skill)
    out = WorkflowExecutor(store, dispatch=_guest_result(result)).run_until_checkpoint(wf)
    assert out["outcome"] == "failed"
    assert wf.steps[0].status == "failed"
    assert "exit" in str(wf.steps[0].result) or "timed out" in str(wf.steps[0].result)


@pytest.mark.unit
def test_a_guest_command_that_exited_zero_succeeds(tmp_path):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _guest_wf("run_vm_guest_command")
    ok = {"exit_code": 0, "stdout": "fine", "stderr": "", "timed_out": False}
    out = WorkflowExecutor(store, dispatch=_guest_result(ok)).run_until_checkpoint(wf)
    assert out["outcome"] == "completed"


@pytest.mark.unit
def test_exit_code_on_another_tool_is_data(tmp_path):
    """Only the guest-command tools promise exit_code means the call's outcome."""
    store = WorkflowStore(tmp_path / "wf.db")
    wf = _guest_wf("get_alarms", skill="monitor")
    out = WorkflowExecutor(store, dispatch=lambda s, t, p: {"exit_code": 3}).run_until_checkpoint(wf)
    assert out["outcome"] == "completed"


@pytest.mark.unit
def test_a_second_rollback_still_names_the_clone_left_behind(tmp_path):
    store = WorkflowStore(tmp_path / "wf.db")
    wf = Workflow(
        id="wf-rb2", workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="clone", skill="aiops", tool="deploy_linked_clone",
                         params={"new_name": "stg"}, rollback_tool="power_off_vm",
                         rollback_params={"vm_name": "stg", "force": True, "confirm": True}),
            WorkflowStep(index=1, action="fail", skill="aiops", tool="failing_tool", params={}),
        ],
        params={}, created_at="", updated_at="",
    )
    ex = WorkflowExecutor(store, dispatch=lambda s, t, p: {"error": "x"} if t == "failing_tool"
                          else {"ok": True})
    ex.run_until_checkpoint(wf)
    ex.rollback(wf)
    assert wf.steps[0].status == "partially_rolled_back"

    radius = rollback_blast_radius(wf)
    (entry,) = radius["left_behind_by_earlier_rollback"]
    assert entry["index"] == 0 and "stg" in entry["leaves_behind"]

    second = ex.rollback(wf)
    by_step = {r["step"]: r for r in second["rollback_results"]}
    assert by_step[0]["status"] == "partial" and "stg" in by_step[0]["left_behind"]
