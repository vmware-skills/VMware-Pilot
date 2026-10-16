"""HLD §18 D-23: workflows record their creator; roles decide who may approve."""

from __future__ import annotations

import pytest

from vmware_pilot import models
from vmware_pilot.models import Workflow, WorkflowState, _from_dict

roles = pytest.importorskip("vmware_policy.roles")


def _wf(created_by: str) -> Workflow:
    return Workflow(
        id="wf-1",
        workflow_type="custom",
        state=WorkflowState.AWAITING_APPROVAL,
        steps=[],
        params={},
        created_at="2026-10-16T00:00:00+00:00",
        updated_at="2026-10-16T00:00:00+00:00",
        created_by=created_by,
    )


@pytest.mark.unit
def test_a_new_workflow_records_the_network_caller(monkeypatch):
    import vmware_policy.caller as caller_mod

    monkeypatch.setattr(caller_mod, "current_caller", lambda: "token:alice")
    wf = Workflow("x", "custom", WorkflowState.PENDING, [], {}, "t", "t")
    assert wf.created_by == "token:alice"


@pytest.mark.unit
def test_a_stored_workflow_keeps_its_creator_and_old_ones_have_none():
    d = _wf("token:alice").to_dict()
    assert _from_dict(d).created_by == "token:alice"
    d.pop("created_by")
    assert _from_dict(d).created_by == ""


def _as_role(monkeypatch, caller: str, approve: str | None):
    from vmware_pilot.mcp_server.tools import lifecycle

    enabled = approve is not None
    role = roles.Role("r", approve=approve or "any") if enabled else None
    monkeypatch.setattr(roles, "roles_enabled", lambda: enabled)
    monkeypatch.setattr(roles, "caller_role", lambda: (role, "r", ""))
    return lifecycle._approval_refused


@pytest.mark.unit
@pytest.mark.parametrize(
    ("approve", "creator", "refused"),
    [
        ("none", "token:bob", True),
        ("any", "token:alice", False),
        ("others", "token:alice", True),  # approving your own workflow
        ("others", "token:bob", False),
        ("others", "", True),  # created before creators were recorded
        (None, "token:alice", False),  # roles not enabled: unchanged behaviour
    ],
)
def test_approve_modes(monkeypatch, approve, creator, refused):
    check = _as_role(monkeypatch, "token:alice", approve)
    assert bool(check(_wf(creator), "token:alice")) is refused


@pytest.mark.unit
def test_models_creator_falls_back_without_policy(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_caller(name, *a, **k):
        if name == "vmware_policy.caller":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_caller)
    assert models._creator() == ""


@pytest.mark.unit
def test_approve_itself_applies_the_role_mode(monkeypatch):
    """Through the approve tool, not just the helper: 'others' refuses your own workflow."""
    import vmware_policy.caller as caller_mod

    from vmware_pilot.mcp_server.tools import lifecycle

    resumed = []

    class _Store:
        def load(self, workflow_id):
            return _wf("token:alice")

    class _Executor:
        def resume_after_approval(self, wf, approver):
            resumed.append(approver)
            return {"state": "running"}

    monkeypatch.setattr(lifecycle, "_get_store", lambda: _Store())
    monkeypatch.setattr(lifecycle, "_get_executor", lambda: _Executor())
    monkeypatch.setattr(caller_mod, "current_caller", lambda: "token:alice")
    _as_role(monkeypatch, "token:alice", "others")
    from vmware_policy.policy import PolicyDenied

    approve = getattr(lifecycle.approve, "__wrapped__", lifecycle.approve)
    with pytest.raises(PolicyDenied, match="its own token created") as refused:
        approve(workflow_id="wf-1")
    assert refused.value.result.rule == "role:r"  # filters with the other role refusals
    assert resumed == []
    monkeypatch.setattr(caller_mod, "current_caller", lambda: "token:bob")
    assert "error" not in approve(workflow_id="wf-1")
    assert resumed == ["token:bob"]


@pytest.mark.unit
def test_editing_or_confirming_a_draft_counts_as_authoring(monkeypatch):
    """Review M3: take Bob's draft, edit it, then approve it yourself under 'others'."""
    wf = _wf("token:bob")
    wf.with_author("token:alice")
    wf.with_author("token:alice")
    wf.with_author("token:bob")  # the creator is not repeated
    assert wf.authors == ["token:alice"]
    assert _from_dict(wf.to_dict()).authors == ["token:alice"]
    check = _as_role(monkeypatch, "token:alice", "others")
    assert "edited or confirmed" in check(wf, "token:alice")
    assert check(wf, "token:carol") == ""


@pytest.mark.unit
def test_update_draft_records_the_editor(monkeypatch, tmp_path):
    import vmware_policy.caller as caller_mod

    from vmware_pilot.mcp_server.tools import authoring

    saved = []
    draft = _wf("token:bob")
    draft.state = WorkflowState.DRAFT

    class _Store:
        def load(self, workflow_id):
            return draft

        def save(self, wf):
            saved.append(list(wf.authors))

    monkeypatch.setattr(authoring, "_get_store", lambda: _Store())
    monkeypatch.setattr(caller_mod, "current_caller", lambda: "token:alice")
    monkeypatch.setattr(caller_mod, "audit_user", lambda: "token:alice")
    update = getattr(authoring.update_draft, "__wrapped__", authoring.update_draft)
    update(workflow_id="wf-1", description="mine now")
    assert saved and saved[0] == ["token:alice"]
