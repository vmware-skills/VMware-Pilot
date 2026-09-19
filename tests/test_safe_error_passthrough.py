"""Pilot handed the agent whatever exception text it caught, from any skill.

Every tool here ended its ``except`` with ``{"error": str(e), "hint": ...}``.
For the refusals pilot authors that was correct, and it is why the gap stayed
invisible: those are the only failures anyone exercises. Feed a malformed step
reference and you get back the sentence ``executor.py`` wrote for it, so the
payload always looked right.

What no one fed it was a failure from the other side. Pilot is the orchestrator
— it dispatches into aiops, monitor, storage, vks, nsx, avi. An exception raised
inside a driven skill propagates up through ``run_until_checkpoint`` into these
handlers, and ``str(e)`` put that skill's raw text into the model's context: a
vCenter fault body, a NSX response, a task URL with credentials in its userinfo.
Pilot's own tests never see it because they drive workflows with stub dispatch.

So the rule is the family's: ``ValueError`` — pilot's entire deliberate
vocabulary — passes through, everything else is reduced to its type.

The load-bearing property is that this cannot swallow a gate. Every guard in the
executor (terminal state, missing approver, not-awaiting-approval) *returns* its
payload rather than raising, so a refusal never reaches ``_safe_error`` at all,
and the redaction cannot turn "I refuse, here is why" into a class name. The
tests below pin that directly rather than trusting it to stay true.

``RuntimeError`` is not on the allowlist and must not be added. Pilot drives
every other skill in the family, so a generic catch-all is precisely the channel
by which another skill's raw text would arrive wearing pilot's authorship.
"""

from __future__ import annotations

import pytest

from vmware_pilot.mcp_server._shared import _safe_error
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore

REDACTED_REFUSAL = (
    "Step 2 param 'password' is the redacted placeholder '***' — the workflow "
    "store masks secrets and the live value did not survive the process "
    "restart. Re-source the secret from its environment variable and re-create "
    "the workflow with create_workflow."
)


def test_executor_refusal_keeps_its_message():
    """The secret-placeholder refusal is the one that most needs its remedy."""
    assert _safe_error(ValueError(REDACTED_REFUSAL), "run_workflow") == REDACTED_REFUSAL


def test_step_reference_errors_keep_their_message():
    msg = (
        "Step reference '__from_step_5__' is a forward/self reference. Run "
        "review_workflow to see the execution order."
    )
    assert _safe_error(ValueError(msg), "run_workflow") == msg


def test_unplanned_exceptions_are_reduced():
    """A driven skill's exception can carry its raw API text and credentials."""
    out = _safe_error(RuntimeError("https://admin:hunter2@vc.internal/api/task-42"), "run_workflow")
    assert out == "RuntimeError: operation failed."
    assert "hunter2" not in out


def test_runtime_error_is_not_a_teaching_error():
    """RuntimeError is the generic catch-all — allowlisting it reopens the leak."""
    assert (
        _safe_error(RuntimeError(REDACTED_REFUSAL), "run_workflow")
        == "RuntimeError: operation failed."
    )


def test_message_is_truncated():
    """Length capping is the other half of the guard.

    500, not the family's 300: three of the executor's six authored refusals
    exceed 300 characters before interpolation (the secret one at 403) and in
    each the remedy comes last.
    """
    out = _safe_error(ValueError("x" * 900), "run_workflow")
    assert len(out) <= 500
    assert len(out) > 300


# ---------------------------------------------------------------------------
# Gate integrity — redaction must not swallow a refusal
# ---------------------------------------------------------------------------


def _workflow(state: WorkflowState) -> Workflow:
    return Workflow(
        id=f"wf-{state.value}",
        workflow_type="test",
        state=state,
        steps=[WorkflowStep(index=0, action="s", skill="aiops", tool="vm_list", params={})],
        params={},
        created_at="",
        updated_at="",
    )


@pytest.fixture()
def store(tmp_path, monkeypatch):
    from vmware_pilot.mcp_server import server as server_mod

    st = WorkflowStore(tmp_path / "wf.db")
    monkeypatch.setattr(server_mod, "_store", st)
    return st


def test_missing_approver_refusal_survives_verbatim(store):
    """approve() with no approver must still say why — it is an audit-trail gate."""
    from vmware_pilot.mcp_server.tools.lifecycle import approve

    wf = _workflow(state=WorkflowState.AWAITING_APPROVAL)
    store.save(wf)

    out = approve(wf.id, approver="")
    assert "approver is required" in out["error"]


def test_cancel_of_terminal_workflow_refusal_survives_verbatim(store):
    """The terminal-state guard returns rather than raises, so it is never reduced."""
    from vmware_pilot.mcp_server.tools.lifecycle import cancel_workflow

    wf = _workflow(state=WorkflowState.COMPLETED)
    store.save(wf)

    out = cancel_workflow(wf.id, reason="test", confirm=True)
    assert "terminal" in out["error"]
    assert "operation failed" not in out["error"]


def test_tools_actually_use_the_helper(store, monkeypatch):
    """The helper is worthless if a call site still formats str(e) itself.

    Driven through the registered tool because the defect being pinned was the
    call sites, not a missing helper.
    """
    from vmware_pilot.mcp_server.tools.lifecycle import run_workflow

    wf = _workflow(state=WorkflowState.PENDING)
    store.save(wf)

    def _boom(*_a, **_kw):
        raise RuntimeError("https://admin:hunter2@vc.internal/api/task-42")

    monkeypatch.setattr(
        "vmware_pilot.executor.WorkflowExecutor.run_until_checkpoint", _boom
    )

    out = run_workflow(wf.id)
    assert out["error"] == "RuntimeError: operation failed."
    assert "hunter2" not in out["error"]
