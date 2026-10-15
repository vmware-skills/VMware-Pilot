"""Over HTTPS the approver is the authenticated caller (HLD §16 D-12).

Token "bob" calling approve(approver="alice (CISO)") used to record an approval
by "alice (CISO)". The audit row said token:bob; the workflow said otherwise.
"""

from __future__ import annotations

import pytest

from vmware_pilot.mcp_server.tools import lifecycle


class _Store:
    def load(self, workflow_id):
        return object()


class _Executor:
    def __init__(self):
        self.approvers = []

    def resume_after_approval(self, wf, approver):
        self.approvers.append(approver)
        return {"state": "running"}


@pytest.fixture
def executor(monkeypatch):
    ex = _Executor()
    monkeypatch.setattr(lifecycle, "_get_store", lambda: _Store())
    monkeypatch.setattr(lifecycle, "_get_executor", lambda: ex)
    return ex


def _as(monkeypatch, who):
    import vmware_policy.caller as caller_mod

    monkeypatch.setattr(caller_mod, "current_caller", lambda: who)


def _approve(**kw):
    fn = getattr(lifecycle.approve, "__wrapped__", lifecycle.approve)
    return fn(**kw)


@pytest.mark.unit
def test_network_approval_is_recorded_as_the_caller(executor, monkeypatch):
    _as(monkeypatch, "token:bob")
    result = _approve(workflow_id="wf1")
    assert "error" not in result
    assert executor.approvers == ["token:bob"]


@pytest.mark.unit
def test_a_different_approver_name_over_the_network_is_refused(executor, monkeypatch):
    _as(monkeypatch, "token:bob")
    result = _approve(workflow_id="wf1", approver="alice (CISO)")
    assert "error" in result and "token:bob" in result["error"]
    assert executor.approvers == []


@pytest.mark.unit
def test_stdio_keeps_the_free_text_approver(executor, monkeypatch):
    _as(monkeypatch, None)
    _approve(workflow_id="wf1", approver="alice")
    assert executor.approvers == ["alice"]
