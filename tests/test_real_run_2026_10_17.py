"""Defects from the first real-vSphere run of Pilot workflows (2026-10-17).

vCenter 8.0.3, an embedder dispatching steps to the released 2.4.0 skill MCP
servers, plus the MCP server alone (no dispatcher). Every test here encodes
what went wrong on that run:

1. ``clone_and_test`` cloned from a snapshot literally named "current", powered
   the clone on, then resized it — ``MemoryHotPlugNotSupported`` on any VM
   without hot-add. Its "monitor" step was one ``get_alarms`` for the whole
   vCenter, and ``monitor_minutes`` changed nothing.
2. ``approve`` passed a gate whose earlier steps were all ``not_executed``,
   under the message "Staging VM … tested".
3. ``rollback`` "rolled back" a clone by powering it off — the clone and its
   delta disk stayed — and said nothing about the production change it left.
4. A skill that returns its failure as plain text was recorded as success.
"""

from __future__ import annotations

from typing import Any

import pytest

from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.lifecycle_gate import rollback_blast_radius
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore
from vmware_pilot.templates import clone_and_test


def _store(tmp_path) -> WorkflowStore:
    return WorkflowStore(tmp_path / "wf.db")


def _ok(skill: str, tool: str, params: dict[str, Any]) -> Any:
    return {"ok": True}


# ── 1. clone_and_test on real vSphere ────────────────────────────────────


@pytest.mark.unit
class TestCloneAndTestRunsOnRealVsphere:
    def test_snapshot_name_is_required_and_the_error_says_how_to_find_one(self):
        with pytest.raises(ValueError, match="snapshot_name") as err:
            clone_and_test(target_vm="db01", change_spec={"cpu": 2})
        assert "list_vm_snapshots" in str(err.value)

    def test_the_clone_uses_the_given_snapshot_and_stays_off(self):
        wf = clone_and_test(target_vm="db01", change_spec={"cpu": 2}, snapshot_name="pre-patch")
        clone = next(s for s in wf.steps if s.tool == "deploy_linked_clone")
        assert clone.params["snapshot_name"] == "pre-patch"
        assert clone.params["power_on"] is False

    def test_staging_is_resized_while_powered_off(self):
        """Resizing a running VM needs hot-add; most VMs do not have it."""
        wf = clone_and_test(target_vm="db01", change_spec={"memory_mb": 768},
                            snapshot_name="s1")
        staging = wf.params["staging_vm"]
        resize = next(s.index for s in wf.steps
                      if s.tool == "reconfigure_vm" and s.params["vm_name"] == staging)
        power_on = next(s.index for s in wf.steps
                        if s.tool == "power_on_vm" and s.params["vm_name"] == staging)
        assert resize < power_on

    def test_a_guest_command_runs_after_staging_is_powered_on(self):
        spec = {"command": "/bin/true", "username": "ops", "password": "pw"}
        wf = clone_and_test(target_vm="db01", change_spec=spec, snapshot_name="s1")
        staging = wf.params["staging_vm"]
        power_on = next(s.index for s in wf.steps
                        if s.tool == "power_on_vm" and s.params["vm_name"] == staging)
        guest = next(s.index for s in wf.steps
                     if s.tool == "run_vm_guest_command" and s.params["vm_name"] == staging)
        assert power_on < guest

    def test_the_check_looks_at_the_staging_vm_not_the_whole_vcenter(self):
        wf = clone_and_test(target_vm="db01", change_spec={"cpu": 2}, snapshot_name="s1")
        monitor = next(s for s in wf.steps if s.action == "monitor")
        assert monitor.params.get("vm_name") == wf.params["staging_vm"]

    def test_the_approval_says_what_happened_and_what_production_needs(self):
        wf = clone_and_test(target_vm="db01", change_spec={"cpu": 2}, snapshot_name="s1",
                            monitor_minutes=10)
        gate = [s for s in wf.steps if s.action == "require_approval"][-1]
        message = gate.params["message"]
        assert "tested" not in message.lower(), "Pilot cannot know the staging VM was tested"
        assert "hot-add" in message, "a running production VM is resized in place"
        assert "10 minute" in message, "monitor_minutes must reach the human deciding"
        assert "not rolled back" in message.lower() or "not reversible" in message.lower()

    def test_the_clone_step_does_not_claim_an_undo_it_cannot_do(self):
        """Powering a clone off is not undoing the clone."""
        wf = clone_and_test(target_vm="db01", change_spec={"cpu": 2}, snapshot_name="s1")
        clone = next(s for s in wf.steps if s.tool == "deploy_linked_clone")
        assert clone.rollback_tool in ("", "delete_vm")

    def test_create_workflow_from_template_rejects_a_missing_snapshot(self, tmp_path,
                                                                      monkeypatch):
        import vmware_pilot.mcp_server.server as server

        store = _store(tmp_path)
        monkeypatch.setattr(server, "_store", store)
        monkeypatch.setattr(server, "_executor", WorkflowExecutor(store))
        result = server.create_workflow_from_template(
            "clone_and_test", {"target_vm": "db01", "change_spec": {"cpu": 2}})
        assert "snapshot_name" in result["error"]
        assert store.list_all() == []


# ── 2. the approval gate and steps nobody ran ────────────────────────────


def _gated_wf(wid: str) -> Workflow:
    return Workflow(
        id=wid, workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="clone", skill="aiops", tool="deploy_linked_clone",
                         params={"new_name": "s"}),
            WorkflowStep(index=1, action="monitor", skill="monitor", tool="investigate_vm",
                         params={"vm_name": "s"}),
            WorkflowStep(index=2, action="require_approval", skill="pilot", tool="approve",
                         params={"message": "go?"}),
            WorkflowStep(index=3, action="apply", skill="aiops", tool="reconfigure_vm",
                         params={"vm_name": "p", "cpu": 2}),
        ],
        params={}, created_at="", updated_at="",
    )


@pytest.mark.unit
class TestApprovalOverStepsNobodyRan:
    def test_approve_is_refused_when_earlier_steps_were_not_executed(self, tmp_path):
        store = _store(tmp_path)
        wf = _gated_wf("wf-gate-1")
        ex = WorkflowExecutor(store)  # the MCP server's executor: no dispatcher
        assert ex.run_until_checkpoint(wf)["outcome"] == "awaiting_approval"

        result = ex.resume_after_approval(wf, approver="wei")
        assert "error" in result
        assert [s["index"] for s in result["not_executed_before_gate"]] == [0, 1]
        assert "acknowledge_not_executed" in result["error"]
        assert wf.state == WorkflowState.AWAITING_APPROVAL
        assert wf.approved_by == ""

    def test_acknowledged_approval_records_who_ran_them(self, tmp_path):
        store = _store(tmp_path)
        wf = _gated_wf("wf-gate-2")
        ex = WorkflowExecutor(store)
        ex.run_until_checkpoint(wf)

        result = ex.resume_after_approval(wf, approver="wei", acknowledge_not_executed=True)
        assert "error" not in result
        assert [s.status for s in wf.steps[:2]] == ["reported_done", "reported_done"]
        assert wf.steps[2].result["acknowledged_not_executed"] == [0, 1]
        # Pilot must not hand the caller the clone a second time.
        assert [p["index"] for p in result["pending_dispatch"]] == [3]
        assert result["outcome"] == "dispatch_required"

    def test_with_a_dispatcher_no_acknowledgement_is_needed(self, tmp_path):
        store = _store(tmp_path)
        wf = _gated_wf("wf-gate-3")
        ex = WorkflowExecutor(store, dispatch=_ok)
        ex.run_until_checkpoint(wf)
        result = ex.resume_after_approval(wf, approver="wei")
        assert result["outcome"] == "completed"

    def test_the_mcp_approve_tool_refuses_and_then_accepts_the_acknowledgement(
            self, tmp_path, monkeypatch):
        import vmware_pilot.mcp_server.server as server

        store = _store(tmp_path)
        monkeypatch.setattr(server, "_store", store)
        monkeypatch.setattr(server, "_executor", WorkflowExecutor(store))
        wf = _gated_wf("wf-gate-4")
        store.save(wf)
        server._get_executor().run_until_checkpoint(wf)

        refused = server.approve("wf-gate-4", approver="wei")
        assert "not_executed_before_gate" in refused
        accepted = server.approve("wf-gate-4", approver="wei", acknowledge_not_executed=True)
        assert accepted.get("outcome") == "dispatch_required", accepted


# ── 3. rollback says what it cannot undo ─────────────────────────────────


def _rolled_wf(wid: str) -> Workflow:
    return Workflow(
        id=wid, workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            # The shape of the custom workflow on the lab: clone undone by power-off.
            WorkflowStep(index=0, action="clone", skill="aiops", tool="deploy_linked_clone",
                         params={"new_name": "stg"}, rollback_tool="power_off_vm",
                         rollback_params={"vm_name": "stg", "force": True, "confirm": True}),
            WorkflowStep(index=1, action="apply", skill="aiops", tool="reconfigure_vm",
                         params={"vm_name": "prod", "cpu": 2}),
            WorkflowStep(index=2, action="fail", skill="aiops", tool="failing_tool", params={}),
        ],
        params={}, created_at="", updated_at="",
    )


def _fail_last(skill: str, tool: str, params: dict[str, Any]) -> Any:
    return {"error": "boom"} if tool == "failing_tool" else {"ok": True}


@pytest.mark.unit
class TestRollbackIsHonestAboutWhatStays:
    def test_preview_says_the_clone_stays_and_the_change_is_not_reversible(self, tmp_path):
        store = _store(tmp_path)
        wf = _rolled_wf("wf-rb-1")
        WorkflowExecutor(store, dispatch=_fail_last).run_until_checkpoint(wf)

        radius = rollback_blast_radius(wf)
        clone = next(e for e in radius["would_roll_back"] if e["index"] == 0)
        assert "stays" in clone["leaves_behind"]
        assert [e["index"] for e in radius["left_in_place"]] == [1]
        assert "not reversible" in radius["left_in_place_note"]

    def test_rollback_does_not_call_a_power_off_a_reversed_clone(self, tmp_path):
        store = _store(tmp_path)
        wf = _rolled_wf("wf-rb-2")
        ex = WorkflowExecutor(store, dispatch=_fail_last)
        ex.run_until_checkpoint(wf)
        result = ex.rollback(wf)

        by_step = {r["step"]: r for r in result["rollback_results"]}
        assert by_step[0]["status"] == "partial"
        assert "stays" in by_step[0]["left_behind"]
        assert wf.steps[0].status == "partially_rolled_back"
        assert by_step[1]["status"] == "not_reversible"
        assert wf.steps[1].status == "success", "the production change is still applied"

    def test_a_clone_undone_by_delete_is_a_full_rollback(self, tmp_path):
        """Control: the table must not call every clone rollback partial."""
        store = _store(tmp_path)
        wf = _rolled_wf("wf-rb-3")
        wf.steps[0].rollback_tool = "delete_vm"
        ex = WorkflowExecutor(store, dispatch=_fail_last)
        ex.run_until_checkpoint(wf)
        result = ex.rollback(wf)
        assert result["rollback_results"][-1]["status"] == "success"
        assert wf.steps[0].status == "rolled_back"


# ── 4. failures returned as text ─────────────────────────────────────────


class _Text:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    """Duck-typed CallToolResult that did NOT set isError (AIops 2.4.0 did this)."""

    def __init__(self, text: str, structured: Any = None) -> None:
        self.isError = False
        self.content = [_Text(text)]
        self.structuredContent = structured


_FAILURE_SHAPES = {
    "plain string": "Error: Snapshot 'current' not found on 'agt-w2-src'. Available: s1",
    "text block": _Result("Error: Snapshot 'current' not found on 'agt-w2-src'."),
    "structured str": _Result("x", {"result": "Error: VM 'x' not found."}),
    "error list": [{"error": "VM 'x' not found.", "hint": "list VMs"}],
}


def _last_fails(shape: Any):
    def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
        return shape if tool == "deploy_linked_clone" else {"ok": True}
    return dispatch


def _last_step_wf(wid: str) -> Workflow:
    return Workflow(
        id=wid, workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="look", skill="monitor", tool="get_alarms", params={}),
            WorkflowStep(index=1, action="clone", skill="aiops", tool="deploy_linked_clone",
                         params={}),
        ],
        params={}, created_at="", updated_at="",
    )


@pytest.mark.unit
@pytest.mark.parametrize("shape", list(_FAILURE_SHAPES), ids=list(_FAILURE_SHAPES))
def test_a_failure_in_the_last_step_does_not_complete_the_workflow(tmp_path, shape):
    store = _store(tmp_path)
    wf = _last_step_wf("wf-last")
    result = WorkflowExecutor(store, dispatch=_last_fails(_FAILURE_SHAPES[shape])
                              ).run_until_checkpoint(wf)
    assert result["outcome"] == "failed", result["outcome"]
    assert wf.state == WorkflowState.FAILED
    assert wf.steps[1].status == "failed"
    assert "not found" in str(wf.steps[1].result)


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "Linked clone 'stg' created from 'src' @ snapshot 's1'.",
    "VM 'stg' powered on. 0 errors in the last hour.",
    "error_count: 0",
    "Errors: none",
])
def test_ordinary_text_results_are_still_success(tmp_path, text):
    """Negative control for the text marker: only the family's 'Error: ' prefix counts."""
    store = _store(tmp_path)
    wf = _last_step_wf("wf-ok")
    result = WorkflowExecutor(store, dispatch=_last_fails(text)).run_until_checkpoint(wf)
    assert result["outcome"] == "completed"


# ── review of the fix (2026-10-17) ───────────────────────────────────────


@pytest.mark.unit
def test_mcp_rollback_preview_names_the_clone_and_the_production_change(tmp_path):
    """H1: through MCP (no dispatcher) the clone has no rollback_tool and was
    never 'success' — the preview listed it nowhere, so the leftover clone and
    the production change vanished from what the human sees before confirming."""
    store = _store(tmp_path)
    wf = clone_and_test(target_vm="app", change_spec={"cpu": 4}, snapshot_name="s1")
    ex = WorkflowExecutor(store)
    ex.run_until_checkpoint(wf)
    ex.resume_after_approval(wf, approver="wei", acknowledge_not_executed=True)

    radius = rollback_blast_radius(wf)
    listed = {e["index"]: e for e in radius["not_reversible_outside_pilot"]}
    clone = next(s.index for s in wf.steps if s.tool == "deploy_linked_clone")
    prod = next(s.index for s in wf.steps if s.action == "apply_to_production")
    assert clone in listed and prod in listed
    assert "not reversible" in radius["not_reversible_outside_pilot_note"]
    assert all(wf.steps[i].action != "require_approval" for i in listed)


@pytest.mark.unit
@pytest.mark.parametrize("skill", ["vmware-aiops", "vmware_aiops", "AIops"])
def test_a_clone_powered_off_is_partial_whatever_the_skill_spelling(skill):
    """M1: the rest of Pilot accepts these spellings; the table must too."""
    from vmware_pilot.reversibility import leaves_behind

    step = WorkflowStep(index=0, action="clone", skill=skill, tool="deploy_linked_clone",
                        params={"new_name": "stg"}, rollback_tool="power_off_vm")
    assert "stays" in leaves_behind(step)


@pytest.mark.unit
def test_a_reference_to_a_step_performed_outside_pilot_says_so(tmp_path):
    """M2: the old text said 'resolve that cause and re-run' — re-running a
    step the caller already performed duplicates it."""
    store = _store(tmp_path)
    wf = Workflow(
        id="wf-ref-1", workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="plan", skill="aiops", tool="create_vm_plan",
                         params={}),
            WorkflowStep(index=1, action="require_approval", skill="pilot", tool="approve",
                         params={"message": "go?"}),
            WorkflowStep(index=2, action="apply", skill="aiops", tool="apply_vm_plan",
                         params={"plan_id": "__from_step_0__:plan_id"}),
        ],
        params={}, created_at="", updated_at="",
    )
    WorkflowExecutor(store).run_until_checkpoint(wf)
    result = WorkflowExecutor(store, dispatch=_ok).resume_after_approval(
        wf, approver="wei", acknowledge_not_executed=True)
    assert result["outcome"] == "failed"
    message = str(wf.steps[2].result)
    assert "outside Pilot" in message
    assert "re-run the workflow" not in message


@pytest.mark.unit
def test_the_refusal_warns_against_re_executing_performed_steps(tmp_path):
    store = _store(tmp_path)
    wf = _gated_wf("wf-gate-5")
    ex = WorkflowExecutor(store)
    ex.run_until_checkpoint(wf)
    refusal = ex.resume_after_approval(wf, approver="wei")["error"]
    assert "do not" in refusal.lower() and "again" in refusal


# ── narrow second review (2026-10-17) ────────────────────────────────────


def _mcp_clone_after_approval(tmp_path):
    store = _store(tmp_path)
    wf = clone_and_test(target_vm="app", change_spec={"cpu": 4}, snapshot_name="s1")
    ex = WorkflowExecutor(store)
    ex.run_until_checkpoint(wf)
    ex.resume_after_approval(wf, approver="wei", acknowledge_not_executed=True)
    return ex, wf


@pytest.mark.unit
def test_cancel_preview_does_not_call_handed_out_steps_skipped(tmp_path):
    """The production resize was handed out in pending_dispatch; the agent may
    have done it. 'would_skip' said cancel would prevent it."""
    from vmware_pilot.lifecycle_gate import cancel_blast_radius

    ex, wf = _mcp_clone_after_approval(tmp_path)
    prod = next(s.index for s in wf.steps if s.action == "apply_to_production")
    radius = cancel_blast_radius(wf)
    assert prod in [e["index"] for e in radius["handed_out_to_caller"]]
    assert prod not in [e["index"] for e in radius["would_skip"]]
    assert "may already" in radius["handed_out_to_caller_note"]

    ex.cancel(wf, reason="test")
    assert wf.steps[prod].status == "not_executed", "the record must keep that it was handed out"


@pytest.mark.unit
def test_outside_list_leaves_out_read_only_checks(tmp_path):
    _, wf = _mcp_clone_after_approval(tmp_path)
    radius = rollback_blast_radius(wf)
    tools = [e["tool"] for e in radius["not_reversible_outside_pilot"]]
    assert "investigate_vm" not in tools
    assert "deploy_linked_clone" in tools


@pytest.mark.unit
def test_reference_refusal_does_not_point_at_a_refused_cancel(tmp_path):
    """The workflow is FAILED (terminal) by then; cancel_workflow refuses it."""
    store = _store(tmp_path)
    wf = Workflow(
        id="wf-ref-2", workflow_type="test", state=WorkflowState.PENDING,
        steps=[
            WorkflowStep(index=0, action="plan", skill="aiops", tool="create_vm_plan",
                         params={}),
            WorkflowStep(index=1, action="require_approval", skill="pilot", tool="approve",
                         params={"message": "go?"}),
            WorkflowStep(index=2, action="apply", skill="aiops", tool="apply_vm_plan",
                         params={"plan_id": "__from_step_0__:plan_id"}),
        ],
        params={}, created_at="", updated_at="",
    )
    WorkflowExecutor(store).run_until_checkpoint(wf)
    WorkflowExecutor(store, dispatch=_ok).resume_after_approval(
        wf, approver="wei", acknowledge_not_executed=True)
    assert wf.state == WorkflowState.FAILED
    assert "cancel_workflow" not in str(wf.steps[2].result)
