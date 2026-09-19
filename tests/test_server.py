"""Tests for MCP server tool behavior: approval-gate enforcement (#5) and
validate-before-persist / widened error handling (#8)."""

from pathlib import Path

import pytest

import vmware_pilot.mcp_server.server as server
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import (
    Workflow,
    WorkflowState,
    WorkflowStep,
    WorkflowStore,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = WorkflowStore(tmp_path / "wf.db")
    monkeypatch.setattr(server, "_store", s)
    monkeypatch.setattr(server, "_executor", WorkflowExecutor(s))
    return s


def _save(store: WorkflowStore, steps: list[WorkflowStep], wf_id: str) -> Workflow:
    wf = Workflow(
        id=wf_id, workflow_type="test", state=WorkflowState.PENDING,
        steps=steps, params={}, created_at="", updated_at="",
    )
    store.save(wf)
    return wf


def _step(i, tool, group_id="", action=None):
    return WorkflowStep(
        index=i, action=action or tool, skill="nsx", tool=tool,
        params={"id": "x"}, group_id=group_id,
    )


@pytest.mark.unit
class TestRunWorkflowGating:
    """Truth table for fix #5: (blocking findings, force) → refuse/run."""

    def test_ungated_destructive_without_force_refused(self, store):
        wf = _save(store, [_step(0, "delete_segment")], "wf-g1")
        result = server.run_workflow("wf-g1")
        assert "error" in result
        assert result["blocking_findings"][0]["kind"] == "ungated_destructive"
        # Nothing ran / nothing recorded as executed
        assert wf.steps[0].status == "pending"
        assert wf.state == WorkflowState.PENDING

    def test_ungated_destructive_with_force_is_still_refused_for_custom(self, store):
        """This test used to assert the opposite — that force=True ran it.

        ``workflow_type="test"`` is not a built-in, so this is a custom
        workflow, and a custom workflow's missing approval gate is not
        overridable: the fix is one inserted require_approval step.
        """
        wf = _save(store, [_step(0, "delete_segment")], "wf-g2")
        result = server.run_workflow("wf-g2", force=True)
        assert "error" in result
        assert result["blocking_findings"][0]["kind"] == "ungated_destructive"
        assert wf.steps[0].status == "pending"
        assert not any(e["action"] == "forced_run" for e in wf.audit_log)

    def test_ungated_destructive_with_force_runs_and_audits_for_builtin(self, store):
        """The audited escape hatch survives for built-in templates."""
        wf = _save(store, [_step(0, "delete_segment")], "wf-g2b")
        wf.workflow_type = "clone_and_test"
        store.save(wf)
        result = server.run_workflow("wf-g2b", force=True)
        assert "error" not in result
        # Server has no dispatcher → honest dispatch_required, not completed
        assert result["outcome"] == "dispatch_required"
        assert any(e["action"] == "forced_run" for e in wf.audit_log)

    def test_destructive_in_parallel_group_refused(self, store):
        steps = [
            WorkflowStep(index=0, action="require_approval", skill="pilot",
                         tool="approve", params={}),
            _step(1, "delete_segment", group_id="g1"),
            _step(2, "delete_nat_rule", group_id="g1"),
        ]
        _save(store, steps, "wf-g3")
        result = server.run_workflow("wf-g3")
        assert "error" in result
        kinds = {f["kind"] for f in result["blocking_findings"]}
        assert "destructive_in_parallel_group" in kinds

    def test_gated_workflow_runs_without_force(self, store):
        steps = [
            WorkflowStep(index=0, action="require_approval", skill="pilot",
                         tool="approve", params={}),
            _step(1, "delete_segment"),
        ]
        _save(store, steps, "wf-g4")
        result = server.run_workflow("wf-g4")
        assert "error" not in result
        assert result["state"] == "awaiting_approval"

    def test_clean_workflow_runs_without_force(self, store):
        _save(store, [_step(0, "list_segments")], "wf-g5")
        result = server.run_workflow("wf-g5")
        assert "error" not in result
        assert result["outcome"] == "dispatch_required"

    def test_clean_workflow_with_force_no_forced_audit(self, store):
        wf = _save(store, [_step(0, "list_segments")], "wf-g6")
        result = server.run_workflow("wf-g6", force=True)
        assert "error" not in result
        assert not any(e["action"] == "forced_run" for e in wf.audit_log)


@pytest.mark.unit
class TestRunWorkflowHonesty:
    """Fix #1 at the MCP layer: noop dispatch must never report completed."""

    def test_run_workflow_never_fakes_completion(self, store):
        _save(store, [_step(0, "list_segments"), _step(1, "get_alarms")], "wf-h1")
        result = server.run_workflow("wf-h1")
        assert result["state"] != "completed"
        assert result["outcome"] == "dispatch_required"
        assert len(result["pending_dispatch"]) == 2
        assert all(s["status"] == "not_executed" for s in result["steps"])


@pytest.mark.unit
class TestCreateWorkflowValidation:
    """Fix #8: invalid template name must not persist a workflow."""

    @pytest.mark.parametrize("bad_name", ["", "../evil", "a/b", ".hidden", "a\\b"])
    def test_invalid_name_with_template_not_persisted(self, store, bad_name):
        result = server.create_workflow(
            name=bad_name, description="d",
            steps=[{"action": "a", "skill": "monitor", "tool": "get_alarms", "params": {}}],
            save_as_template=True,
        )
        assert "error" in result
        assert store.list_all() == []

    def test_valid_name_without_template_persists(self, store):
        result = server.create_workflow(
            name="ok_flow", description="d",
            steps=[{"action": "a", "skill": "monitor", "tool": "get_alarms", "params": {}}],
            save_as_template=False,
        )
        assert "error" not in result
        assert store.load(result["workflow_id"]) is not None


@pytest.mark.unit
class TestCancelWorkflow:
    """Fix #7 at the MCP layer: cancel_workflow makes a workflow terminal and
    run_workflow then refuses it."""

    def test_cancel_then_run_is_refused(self, store):
        _save(store, [
            WorkflowStep(index=0, action="require_approval", skill="pilot",
                         tool="approve", params={}),
            _step(1, "delete_segment"),
        ], "wf-c1")
        cancel = server.cancel_workflow("wf-c1", reason="approval rejected", confirm=True)
        assert cancel["state"] == "cancelled"

        run = server.run_workflow("wf-c1")
        assert "error" in run
        assert "CANCELLED" in run["error"]
        assert run["state"] == "cancelled"

    def test_cancel_unknown_workflow(self, store):
        result = server.cancel_workflow("nope")
        assert "error" in result
        assert "not found" in result["error"]

    def test_cancel_already_terminal_errors(self, store):
        wf = _save(store, [_step(0, "list_segments")], "wf-c2")
        wf.state = WorkflowState.COMPLETED
        store.save(wf)
        result = server.cancel_workflow("wf-c2", confirm=True)
        assert "error" in result
        assert "terminal" in result["error"]


@pytest.mark.unit
class TestToolParity:
    """cancel_workflow raises the exposed Pilot MCP tool count to 13."""

    def test_expected_tools_exposed(self):
        import asyncio

        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        expected = {
            "get_skill_catalog", "list_workflows", "design_workflow",
            "update_draft", "confirm_draft", "plan_workflow", "create_workflow",
            "review_workflow", "run_workflow", "approve", "cancel_workflow",
            "rollback", "get_workflow_status",
        }
        assert names == expected
        assert len(names) == 13


@pytest.mark.unit
class TestListWorkflowsHintIsReachable:
    """A hint that describes a state the code cannot reach teaches nothing.

    ``list_workflows`` used to advise "check the YAML files in
    ~/.vmware/workflows/ against the file named in 'error'". No file name can
    ever appear in 'error': ``list_custom_workflows()`` catches per file and
    continues, so a malformed template cannot fail this call, and anything that
    *does* raise is reduced to its class name by ``_safe_error`` unless it is a
    ValueError. The caller was sent looking for a filename that is structurally
    impossible to produce.
    """

    def test_a_malformed_template_does_not_fail_the_call(self, tmp_path, monkeypatch):
        """The premise of the old hint, falsified directly."""
        from vmware_pilot import custom_loader

        (tmp_path / "broken.yaml").write_text("name: broken\nsteps: [oops\n", encoding="utf-8")
        (tmp_path / "good.yaml").write_text(
            "name: good\ndescription: fine\nsteps:\n  - action: a\n    skill: nsx\n"
            "    tool: list_segments\n"
        , encoding="utf-8")
        monkeypatch.setattr(custom_loader, "_WORKFLOWS_DIR", tmp_path)

        result = server.list_workflows()
        assert "error" not in result, "a malformed template must not fail this call"
        names = {t["name"] for t in result["templates"]}
        assert "good" in names
        assert "broken" not in names, "the bad file is skipped, not reported"

    def test_the_hint_does_not_promise_a_filename(self, monkeypatch):
        """And the wording must not send the caller after one."""

        def _boom():
            raise RuntimeError("store is unreadable")

        # _shared._get_store() defers to this accessor at call time, so this is
        # the one patch point every tool module observes.
        monkeypatch.setattr(server, "_get_store", _boom)

        result = server.list_workflows()
        assert "error" in result
        hint = result["hint"]
        assert "named in 'error'" not in hint
        # The error itself is masked to a class name, proving the point.
        assert result["error"] == "RuntimeError: operation failed."
        assert "server log" in hint, "must route somewhere that actually has it"


@pytest.mark.unit
class TestConfirmDraftValidation:
    """Fix #8: confirm_draft validates the name before flipping state."""

    def test_bad_template_name_keeps_draft_state(self, store):
        wf = Workflow(
            id="wf-d1", workflow_type="bad/name", state=WorkflowState.DRAFT,
            steps=[_step(0, "list_segments")],
            params={}, created_at="", updated_at="",
        )
        store.save(wf)
        result = server.confirm_draft("wf-d1", save_as_template=True)
        assert "error" in result
        assert wf.state == WorkflowState.DRAFT
