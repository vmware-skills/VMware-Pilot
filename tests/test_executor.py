"""Tests for the workflow executor."""

import tempfile
from pathlib import Path
from typing import Any

import pytest

from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore


def _make_store(tmp_path: Path) -> WorkflowStore:
    return WorkflowStore(tmp_path / "test.db")


def _success_dispatch(skill: str, tool: str, params: dict[str, Any]) -> dict:
    return {"ok": True, "skill": skill, "tool": tool}


def _fail_dispatch(skill: str, tool: str, params: dict[str, Any]) -> dict:
    if tool == "failing_tool":
        raise RuntimeError("simulated failure")
    return {"ok": True}


@pytest.mark.unit
class TestExecutor:
    def test_run_simple_workflow(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)

        wf = Workflow(
            id="wf-1", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="step1", skill="aiops", tool="vm_power_on", params={"vm": "a"}),
                WorkflowStep(index=1, action="step2", skill="aiops", tool="vm_power_off", params={"vm": "a"}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)
        result = executor.run_until_checkpoint(wf)
        assert result["state"] == "completed"
        assert all(s["status"] == "success" for s in result["steps"])

    def test_approval_gate_pauses(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)

        wf = Workflow(
            id="wf-2", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="clone", skill="aiops", tool="deploy_linked_clone", params={}),
                WorkflowStep(index=1, action="require_approval", skill="pilot", tool="approve", params={"message": "ok?"}),
                WorkflowStep(index=2, action="commit", skill="aiops", tool="vm_power_on", params={}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)

        # Run — should pause at approval
        result = executor.run_until_checkpoint(wf)
        assert result["state"] == "awaiting_approval"
        assert wf.steps[0].status == "success"
        assert wf.steps[1].status == "pending"  # approval not yet done
        assert wf.steps[2].status == "pending"

    def test_resume_after_approval(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)

        wf = Workflow(
            id="wf-3", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="clone", skill="aiops", tool="deploy_linked_clone", params={}),
                WorkflowStep(index=1, action="require_approval", skill="pilot", tool="approve", params={}),
                WorkflowStep(index=2, action="commit", skill="aiops", tool="vm_power_on", params={}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)

        executor.run_until_checkpoint(wf)
        assert wf.state == WorkflowState.AWAITING_APPROVAL

        result = executor.resume_after_approval(wf, approver="wei")
        assert result["state"] == "completed"
        assert result["approved_by"] == "wei"

    def test_failure_skips_remaining(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_fail_dispatch)

        wf = Workflow(
            id="wf-4", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="ok", skill="aiops", tool="vm_power_on", params={}),
                WorkflowStep(index=1, action="fail", skill="aiops", tool="failing_tool", params={}),
                WorkflowStep(index=2, action="skip", skill="aiops", tool="vm_power_off", params={}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)

        result = executor.run_until_checkpoint(wf)
        assert result["state"] == "failed"
        assert result["steps"][0]["status"] == "success"
        assert result["steps"][1]["status"] == "failed"
        assert result["steps"][2]["status"] == "skipped"

    def test_rollback(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)

        wf = Workflow(
            id="wf-5", workflow_type="test", state=WorkflowState.RUNNING,
            steps=[
                WorkflowStep(
                    index=0, action="clone", skill="aiops", tool="deploy_linked_clone", params={},
                    status="success", rollback_tool="vm_power_off", rollback_params={"vm": "staging"},
                ),
                WorkflowStep(
                    index=1, action="apply", skill="aiops", tool="vm_reconfigure", params={},
                    status="failed",
                ),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)

        result = executor.rollback(wf)
        assert result["state"] == "failed"
        assert len(result["rollback_results"]) == 1
        assert result["rollback_results"][0]["status"] == "success"


@pytest.mark.unit
class TestNoDispatchHonesty:
    """Regression for fix #1: noop dispatch must never produce COMPLETED."""

    def _wf(self, wf_id="wf-noop"):
        return Workflow(
            id=wf_id, workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="a", skill="aiops", tool="vm_power_on", params={"vm": "x"}),
                WorkflowStep(index=1, action="b", skill="aiops", tool="vm_power_off", params={"vm": "x"}),
            ],
            params={}, created_at="", updated_at="",
        )

    def test_no_dispatch_yields_dispatch_required_not_completed(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store)  # no dispatch
        wf = self._wf()
        store.save(wf)

        result = executor.run_until_checkpoint(wf)

        assert result["state"] != "completed"
        assert result["outcome"] == "dispatch_required"
        assert all(s["status"] == "not_executed" for s in result["steps"])
        assert wf.state == WorkflowState.PENDING
        # The agent gets everything it needs to perform the steps itself.
        pending = result["pending_dispatch"]
        assert [p["index"] for p in pending] == [0, 1]
        assert pending[0]["skill"] == "aiops"
        assert pending[0]["tool"] == "vm_power_on"
        assert pending[0]["params"] == {"vm": "x"}
        assert "message" in result

    def test_not_executed_steps_are_retried_with_real_dispatch(self, tmp_path):
        store = _make_store(tmp_path)
        wf = self._wf("wf-noop2")
        store.save(wf)
        WorkflowExecutor(store).run_until_checkpoint(wf)
        assert wf.steps[0].status == "not_executed"

        result = WorkflowExecutor(store, dispatch=_success_dispatch).run_until_checkpoint(wf)
        assert result["state"] == "completed"
        assert result["outcome"] == "completed"
        assert all(s["status"] == "success" for s in result["steps"])

    def test_no_dispatch_still_pauses_at_approval_gate(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store)
        wf = Workflow(
            id="wf-noop3", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="a", skill="aiops", tool="vm_power_on", params={}),
                WorkflowStep(index=1, action="require_approval", skill="pilot", tool="approve", params={}),
                WorkflowStep(index=2, action="c", skill="aiops", tool="vm_power_off", params={}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)
        result = executor.run_until_checkpoint(wf)
        assert result["state"] == "awaiting_approval"
        assert result["outcome"] == "awaiting_approval"
        assert wf.steps[0].status == "not_executed"
        assert result["pending_dispatch"][0]["index"] == 0


@pytest.mark.unit
class TestCrashResume:
    """Regression for fix #4: 'running' steps must not be skipped as done."""

    def test_running_step_marked_interrupted_and_halts(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = Workflow(
            id="wf-crash", workflow_type="test", state=WorkflowState.RUNNING,
            steps=[
                WorkflowStep(index=0, action="a", skill="aiops", tool="vm_power_on", params={}, status="success"),
                WorkflowStep(index=1, action="b", skill="aiops", tool="vm_reconfigure", params={}, status="running"),
                WorkflowStep(index=2, action="c", skill="aiops", tool="vm_power_off", params={}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)

        result = executor.run_until_checkpoint(wf)
        assert wf.steps[1].status == "interrupted"
        assert result["state"] == "failed"
        assert result["outcome"] == "failed"
        assert "interrupted" in result["error"]
        assert "verify" in result["error"].lower()
        # Step 2 must NOT have executed
        assert wf.steps[2].status == "pending"


@pytest.mark.unit
class TestTransitionGuards:
    """Fix #6: executor validates allowed start states."""

    def _wf(self, state):
        return Workflow(
            id="wf-guard", workflow_type="test", state=state,
            steps=[WorkflowStep(index=0, action="a", skill="s", tool="t", params={}, status="success")],
            params={}, created_at="", updated_at="",
        )

    @pytest.mark.parametrize("state", [
        WorkflowState.DRAFT, WorkflowState.COMPLETED, WorkflowState.FAILED,
        WorkflowState.ROLLING_BACK,
    ])
    def test_run_rejected_from_invalid_states(self, tmp_path, state):
        executor = WorkflowExecutor(_make_store(tmp_path), dispatch=_success_dispatch)
        result = executor.run_until_checkpoint(self._wf(state))
        assert "error" in result
        assert "cannot be run" in result["error"]

    @pytest.mark.parametrize("state", [
        WorkflowState.PENDING, WorkflowState.RUNNING, WorkflowState.AWAITING_APPROVAL,
    ])
    def test_run_allowed_from_valid_states(self, tmp_path, state):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = self._wf(state)
        store.save(wf)
        result = executor.run_until_checkpoint(wf)
        assert "error" not in result

    @pytest.mark.parametrize("state", [
        WorkflowState.DRAFT, WorkflowState.COMPLETED, WorkflowState.ROLLING_BACK,
    ])
    def test_rollback_rejected_from_invalid_states(self, tmp_path, state):
        executor = WorkflowExecutor(_make_store(tmp_path), dispatch=_success_dispatch)
        result = executor.rollback(self._wf(state))
        assert "error" in result
        assert "cannot be rolled back" in result["error"]


@pytest.mark.unit
class TestResolveStepRefs:
    """Fix #7: teaching errors for invalid refs + nested recursion."""

    def _steps(self):
        return [
            WorkflowStep(index=0, action="a", skill="s", tool="t", params={},
                         status="success", result={"plan_id": "p-1", "extra": 7}),
            WorkflowStep(index=1, action="b", skill="s", tool="t", params={},
                         status="failed", result={"error": "boom"}),
            WorkflowStep(index=2, action="c", skill="s", tool="t", params={}),
        ]

    def test_backward_ref_with_key(self):
        out = WorkflowExecutor._resolve_step_refs(
            {"plan": "__from_step_0__:plan_id"}, self._steps(), current_index=2)
        assert out["plan"] == "p-1"

    def test_legacy_ref_whole_result(self):
        out = WorkflowExecutor._resolve_step_refs(
            {"data": "__from_step_0__"}, self._steps(), current_index=2)
        assert out["data"] == {"plan_id": "p-1", "extra": 7}

    def test_nested_dict_and_list_recursion(self):
        params = {
            "spec": {"inner": "__from_step_0__:plan_id"},
            "items": ["literal", {"deep": "__from_step_0__:extra"}],
        }
        out = WorkflowExecutor._resolve_step_refs(params, self._steps(), current_index=2)
        assert out["spec"]["inner"] == "p-1"
        assert out["items"][1]["deep"] == 7

    def test_forward_ref_raises_teaching_error(self):
        with pytest.raises(ValueError, match="forward/self reference"):
            WorkflowExecutor._resolve_step_refs(
                {"x": "__from_step_2__:k"}, self._steps(), current_index=1)

    def test_negative_index_raises(self):
        with pytest.raises(ValueError, match="steps 0\\.\\.2"):
            WorkflowExecutor._resolve_step_refs(
                {"x": "__from_step_-1__"}, self._steps(), current_index=2)

    def test_out_of_range_index_raises(self):
        with pytest.raises(ValueError, match="steps 0\\.\\.2"):
            WorkflowExecutor._resolve_step_refs(
                {"x": "__from_step_9__:k"}, self._steps(), current_index=2)

    def test_ref_to_non_success_step_raises(self):
        with pytest.raises(ValueError, match="status 'failed'"):
            WorkflowExecutor._resolve_step_refs(
                {"x": "__from_step_1__:k"}, self._steps(), current_index=2)

    def test_bad_ref_fails_step_with_teaching_message(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = Workflow(
            id="wf-refs", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="a", skill="s", tool="t",
                             params={"x": "__from_step_5__:k"}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)
        result = executor.run_until_checkpoint(wf)
        assert result["state"] == "failed"
        assert "steps 0..0" in wf.steps[0].result["error"]


@pytest.mark.unit
class TestRollbackPersistence:
    """Fix #9: rollback results persisted; blocked_reason on failure."""

    def _wf(self):
        return Workflow(
            id="wf-rbp", workflow_type="test", state=WorkflowState.RUNNING,
            steps=[
                WorkflowStep(index=0, action="a", skill="aiops", tool="deploy_linked_clone",
                             params={}, status="success",
                             rollback_tool="vm_delete", rollback_params={"vm": "staging"}),
            ],
            params={}, created_at="", updated_at="",
        )

    def test_rollback_results_persisted(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = self._wf()
        store.save(wf)
        result = executor.rollback(wf)
        assert result["rollback_results"][0]["status"] == "success"
        assert wf.rollback_results == result["rollback_results"]
        # Re-load from a FRESH store (cache miss → DB row) — persisted.
        fresh = WorkflowStore(tmp_path / "test.db")
        loaded = fresh.load("wf-rbp")
        assert loaded.rollback_results
        assert loaded.rollback_results[0]["status"] == "success"
        assert loaded.blocked_reason == ""

    def test_failed_rollback_sets_blocked_reason(self, tmp_path):
        def _rb_fail(skill, tool, params):
            raise RuntimeError("rollback exploded")

        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_rb_fail)
        wf = self._wf()
        store.save(wf)
        result = executor.rollback(wf)
        assert result["rollback_results"][0]["status"] == "failed"
        assert result["blocked_reason"] == "rollback_failed"
        fresh = WorkflowStore(tmp_path / "test.db")
        assert fresh.load("wf-rbp").blocked_reason == "rollback_failed"


@pytest.mark.unit
class TestRedactedSecretGuard:
    """Cross-process resume must refuse to dispatch the '***' placeholder.

    save() redacts secrets to '***' in the DB; the per-process live cache
    holds real values. A workflow resumed in a FRESH process (cache miss →
    redacted DB row) WITH a real dispatcher must halt loudly rather than send
    password='***' to a sibling skill.
    """

    def _wf(self, wf_id="wf-secret"):
        return Workflow(
            id=wf_id, workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="connect", skill="aiops",
                             tool="vm_power_on",
                             params={"vm": "x", "password": "s3cret"}),
            ],
            params={}, created_at="", updated_at="",
        )

    def test_cross_process_resume_halts_on_redacted_secret(self, tmp_path):
        # Process 1: save (DB copy redacted, live cache holds real value).
        store1 = _make_store(tmp_path)
        store1.save(self._wf())

        # Process 2: a fresh store has an empty cache → load() returns the
        # redacted DB row (password == '***').
        store2 = WorkflowStore(tmp_path / "test.db")
        loaded = store2.load("wf-secret")
        assert loaded.steps[0].params["password"] == "***"

        dispatched: list = []

        def _record_dispatch(skill, tool, params):
            dispatched.append((skill, tool, params))
            return {"ok": True}

        executor = WorkflowExecutor(store2, dispatch=_record_dispatch)
        result = executor.run_until_checkpoint(loaded)

        # Halted on the step, never dispatched the placeholder.
        assert dispatched == []
        assert result["state"] == "failed"
        err = loaded.steps[0].result["error"]
        assert "redacted placeholder" in err
        assert "'password'" in err
        # Case-insensitive: the instruction now opens the sentence ("Re-source
        # the secret from its environment variable ..."). What matters is that
        # the message tells the caller to re-source it, not its capitalisation.
        assert "re-source" in err.lower()

    def test_in_process_happy_path_dispatches_real_value(self, tmp_path):
        # Same process: the live cache preserves the real secret, so dispatch
        # receives the actual value and the run completes.
        store = _make_store(tmp_path)
        wf = self._wf("wf-secret-live")
        store.save(wf)

        seen: list = []

        def _record_dispatch(skill, tool, params):
            seen.append(params)
            return {"ok": True}

        executor = WorkflowExecutor(store, dispatch=_record_dispatch)
        result = executor.run_until_checkpoint(wf)

        assert result["state"] == "completed"
        assert seen[0]["password"] == "s3cret"

    def test_non_sensitive_key_equal_to_placeholder_not_blocked(self, tmp_path):
        # A non-sensitive param that merely equals '***' must NOT trip the
        # guard — only sensitive keys are checked.
        store = _make_store(tmp_path)
        wf = Workflow(
            id="wf-star-note", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="a", skill="aiops",
                             tool="vm_power_on", params={"note": "***"}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        result = executor.run_until_checkpoint(wf)
        assert result["state"] == "completed"


@pytest.mark.unit
class TestCancel:
    """Fix #7: cancel_workflow gives an approval-rejected/unsafe workflow a
    terminal CANCELLED state that can never be run."""

    def _wf(self, state=WorkflowState.PENDING, wf_id="wf-cancel"):
        return Workflow(
            id=wf_id, workflow_type="test", state=state,
            steps=[
                WorkflowStep(index=0, action="require_approval", skill="pilot",
                             tool="approve", params={}),
                WorkflowStep(index=1, action="del", skill="nsx",
                             tool="delete_segment", params={"id": "x"}),
            ],
            params={}, created_at="", updated_at="",
        )

    def test_cancel_moves_to_cancelled(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = self._wf()
        store.save(wf)
        result = executor.cancel(wf, reason="approval rejected")
        assert result["state"] == "cancelled"
        assert result["outcome"] == "cancelled"
        assert wf.state == WorkflowState.CANCELLED
        # pending steps marked skipped, cancellation audited
        assert all(s.status == "skipped" for s in wf.steps)
        assert any(e["action"] == "cancelled" for e in wf.audit_log)
        assert wf.blocked_reason == "approval rejected"
        # persisted
        fresh = WorkflowStore(tmp_path / "test.db")
        assert fresh.load("wf-cancel").state == WorkflowState.CANCELLED

    def test_run_refused_on_cancelled(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = self._wf(state=WorkflowState.CANCELLED)
        store.save(wf)
        result = executor.run_until_checkpoint(wf)
        assert "error" in result
        assert "CANCELLED" in result["error"]
        # nothing executed
        assert all(s.status != "success" for s in wf.steps)

    def test_resume_refused_on_cancelled(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = self._wf(state=WorkflowState.CANCELLED)
        store.save(wf)
        result = executor.resume_after_approval(wf, approver="wei")
        assert "error" in result
        assert "not awaiting approval" in result["error"]

    @pytest.mark.parametrize("state", [
        WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED,
    ])
    def test_cancel_rejected_from_terminal_states(self, tmp_path, state):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = self._wf(state=state)
        store.save(wf)
        result = executor.cancel(wf)
        assert "error" in result
        assert "terminal" in result["error"]
        assert wf.state == state  # unchanged

    @pytest.mark.parametrize("state", [
        WorkflowState.DRAFT, WorkflowState.PENDING, WorkflowState.RUNNING,
        WorkflowState.AWAITING_APPROVAL, WorkflowState.ROLLING_BACK,
    ])
    def test_cancel_allowed_from_non_terminal_states(self, tmp_path, state):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = self._wf(state=state)
        store.save(wf)
        result = executor.cancel(wf)
        assert "error" not in result
        assert wf.state == WorkflowState.CANCELLED


@pytest.mark.unit
class TestApproverRequired:
    """Fix #10c: non-empty approver enforced in the executor."""

    def test_empty_approver_rejected(self, tmp_path):
        store = _make_store(tmp_path)
        executor = WorkflowExecutor(store, dispatch=_success_dispatch)
        wf = Workflow(
            id="wf-appr", workflow_type="test", state=WorkflowState.AWAITING_APPROVAL,
            steps=[WorkflowStep(index=0, action="require_approval", skill="pilot",
                                tool="approve", params={})],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)
        for bad in ("", "   "):
            result = executor.resume_after_approval(wf, approver=bad)
            assert "error" in result
            assert "approver is required" in result["error"]
        assert wf.steps[0].status == "pending"


# ── Returned-failure envelopes ────────────────────────────────────────
# Every skill in this family signals failure by RETURNING {"error": ...},
# not by raising: that is what @vmware_tool / _safe_error produce. An
# executor that only treats exceptions as failure therefore reports
# "completed" for a workflow whose every step failed — the shape fixed in
# Policy v1.8.4 (a returned error envelope audited as ok), found again here
# on a real run against home-vcenter on 2026-09-12.


def _envelope_error_dispatch(skill: str, tool: str, params: dict[str, Any]) -> dict:
    if tool == "failing_tool":
        return {
            "error": "VM 'zz-nonexistent' not found on target 'home-vcenter'.",
            "hint": "Run vm_list to see available VMs.",
        }
    return {"ok": True}


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _CallToolResult:
    """Duck-typed stand-in for mcp's CallToolResult(isError=True)."""

    def __init__(self, is_error: bool) -> None:
        self.isError = is_error
        self.content = [_TextBlock("Error: VM 'zz-nonexistent' not found. Run vm_list to see available VMs.")]


def _is_error_object_dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
    return _CallToolResult(is_error=(tool == "failing_tool"))


@pytest.mark.unit
class TestReturnedFailureIsNotSuccess:
    def _wf(self, wid: str) -> Workflow:
        return Workflow(
            id=wid, workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="ok", skill="monitor", tool="get_alarms", params={}),
                WorkflowStep(index=1, action="fail", skill="aiops", tool="failing_tool", params={}),
                WorkflowStep(index=2, action="skip", skill="aiops", tool="vm_power_off", params={}),
            ],
            params={}, created_at="", updated_at="",
        )

    def test_error_envelope_fails_the_step(self, tmp_path):
        store = _make_store(tmp_path)
        wf = self._wf("wf-env-1")
        store.save(wf)
        result = WorkflowExecutor(store, dispatch=_envelope_error_dispatch).run_until_checkpoint(wf)

        assert result["state"] == "failed", "a returned error envelope must not read as success"
        assert result["steps"][1]["status"] == "failed"
        assert result["steps"][2]["status"] == "skipped"

    def test_error_envelope_keeps_the_skill_message(self, tmp_path):
        store = _make_store(tmp_path)
        wf = self._wf("wf-env-2")
        store.save(wf)
        WorkflowExecutor(store, dispatch=_envelope_error_dispatch).run_until_checkpoint(wf)

        # The skill's own teaching message is what an operator needs; do not
        # replace it with a generic "step failed".
        assert "not found" in str(wf.steps[1].result)

    def test_failed_workflow_can_be_rolled_back(self, tmp_path):
        # The point of failing honestly: rollback refuses a *completed*
        # workflow, so a false "completed" also removes the way back.
        store = _make_store(tmp_path)
        wf = self._wf("wf-env-3")
        store.save(wf)
        ex = WorkflowExecutor(store, dispatch=_envelope_error_dispatch)
        ex.run_until_checkpoint(wf)

        rb = ex.rollback(wf)
        assert "error" not in rb, f"rollback should be available after a real failure: {rb}"

    def test_is_error_object_fails_the_step(self, tmp_path):
        store = _make_store(tmp_path)
        wf = self._wf("wf-env-4")
        store.save(wf)
        result = WorkflowExecutor(store, dispatch=_is_error_object_dispatch).run_until_checkpoint(wf)

        assert result["state"] == "failed", "an isError result object must not read as success"
        # The operator must get the skill's message, not a repr of the transport object.
        assert "Run vm_list" in str(wf.steps[1].result)
        assert "TextContent" not in str(wf.steps[1].result)

    def test_ordinary_results_are_still_success(self, tmp_path):
        # Negative control: nothing here may turn a good result into a failure.
        def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
            return {
                "failing_tool": {"error": None, "items": []},   # explicit no-error
                "get_alarms": {"items": [{"error_count": 3}]},  # 'error' only nested
            }.get(tool, {"ok": True})

        store = _make_store(tmp_path)
        wf = self._wf("wf-env-5")
        store.save(wf)
        result = WorkflowExecutor(store, dispatch=dispatch).run_until_checkpoint(wf)

        assert result["state"] == "completed"
        assert [s["status"] for s in result["steps"]] == ["success", "success", "success"]

    def test_rollback_step_that_returns_an_error_is_not_a_successful_rollback(self, tmp_path):
        # Worse than the forward path: "we rolled it back" when we did not.
        store = _make_store(tmp_path)
        wf = Workflow(
            id="wf-env-6", workflow_type="test", state=WorkflowState.PENDING,
            steps=[
                WorkflowStep(index=0, action="write", skill="aiops", tool="vm_power_on",
                             params={}, rollback_tool="vm_power_off", rollback_params={}),
                WorkflowStep(index=1, action="fail", skill="aiops", tool="failing_tool", params={}),
            ],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)

        def dispatch(skill: str, tool: str, params: dict[str, Any]) -> Any:
            if tool == "failing_tool":
                return {"error": "boom"}
            if tool == "vm_power_off":                      # the rollback itself fails
                return {"error": "VM 'x' not found on target 'home-vcenter'."}
            return {"ok": True}

        ex = WorkflowExecutor(store, dispatch=dispatch)
        ex.run_until_checkpoint(wf)
        rb = ex.rollback(wf)

        statuses = [r["status"] for r in rb["rollback_results"]]
        assert "failed" in statuses, f"a rollback that returned an error must not read as success: {rb['rollback_results']}"
        assert wf.blocked_reason == "rollback_failed"
