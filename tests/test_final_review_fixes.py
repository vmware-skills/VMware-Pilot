"""Final-review findings on Pilot's approval gate (2026-09-19).

* 3 — ``_acts`` recognised only ``confirm``/``confirmed`` set to True or "true";
  a step also acts on a legacy ``dry_run`` passed falsy, or on any other truthy
  ``confirm`` (``1``, ``"yes"``, ``"True"``).
* 4 — ``run_workflow`` refused only on the tier findings, so a stored workflow
  (e.g. saved by an earlier version) with ``confirm_without_approval`` ran.
* 5 — ``rollback_params`` with ``confirm: True`` run on any ``rollback`` call;
  the ``rollback`` preview is the human decision, so it lists each rollback
  step's tool and parameters, secrets redacted.
"""

from __future__ import annotations

import pytest

import vmware_pilot.mcp_server.server as server
from vmware_pilot.approval_gate import CONFIRM_KIND, _acts, gate_violations
from vmware_pilot.executor import WorkflowExecutor
from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep, WorkflowStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = WorkflowStore(tmp_path / "wf.db")
    monkeypatch.setattr(server, "_store", s)
    monkeypatch.setattr(server, "_executor", WorkflowExecutor(s))
    return s


def _wf(params, wid="wf-f", tool="create_segment", skill="nsx"):
    return Workflow(
        id=wid, workflow_type="custom_f", state=WorkflowState.PENDING,
        steps=[WorkflowStep(index=0, action="make", skill=skill, tool=tool, params=params)],
        params={"custom": True}, created_at="", updated_at="",
    )


# ═══ 3: every form of "act" is recognised ════════════════════════════════════


@pytest.mark.unit
@pytest.mark.parametrize("params", [
    {"confirm": True}, {"confirm": "true"}, {"confirm": "True"}, {"confirm": " YES "},
    {"confirm": 1}, {"confirm": 2.5}, {"confirm": "1"}, {"confirm": "on"},
    {"confirmed": True}, {"confirmed": "yes"}, {"confirmed": 1},
    {"dry_run": False}, {"dry_run": 0}, {"dry_run": "false"}, {"dry_run": "False"},
    {"dry_run": "no"}, {"dry_run": "0"}, {"dry_run": "off"},
    {"confirm": False, "dry_run": False},
])
def test_step_acts(params):
    assert _acts(params) is True
    (v,) = gate_violations(_wf(params))
    assert v.kind == CONFIRM_KIND


@pytest.mark.unit
@pytest.mark.parametrize("params", [
    {}, {"confirm": False}, {"confirm": 0}, {"confirm": ""}, {"confirm": "false"},
    {"confirm": "False"}, {"confirm": "no"}, {"confirm": "off"}, {"confirm": "0"},
    {"confirm": "none"}, {"confirm": None}, {"confirmed": False},
    {"dry_run": True}, {"dry_run": 1}, {"dry_run": "true"}, {"dry_run": None},
])
def test_step_previews(params):
    assert _acts(params) is False
    assert gate_violations(_wf(params)) == ()


# ═══ 4: run_workflow enforces confirm_without_approval ═══════════════════════


@pytest.mark.unit
def test_run_refuses_a_stored_workflow_with_confirm_and_no_approval(store):
    wf = _wf({"name": "seg-a", "confirm": True}, wid="wf-old")
    store.save(wf)  # as an earlier version would have saved it
    out = server.run_workflow("wf-old")
    assert "error" in out and "Refusing to run" in out["error"]
    assert [v["kind"] for v in out["approval_gate_violations"]] == [CONFIRM_KIND]
    out = server.run_workflow("wf-old", force=True)
    assert "error" in out
    assert store.load("wf-old").steps[0].status == "pending"


@pytest.mark.unit
def test_run_positive_control_without_confirm_is_not_refused(store):
    store.save(_wf({"name": "seg-a"}, wid="wf-ok"))
    out = server.run_workflow("wf-ok")
    assert "approval_gate_violations" not in out


# ═══ 5: the rollback preview shows each rollback step's tool and params ══════


@pytest.mark.unit
def test_rollback_preview_lists_rollback_params_with_secrets_redacted(store):
    wf = Workflow(
        id="wf-rb", workflow_type="custom_rb", state=WorkflowState.FAILED,
        steps=[
            WorkflowStep(index=0, action="make", skill="nsx", tool="create_segment",
                         params={}, status="success", rollback_tool="delete_segment",
                         rollback_params={"segment_id": "app", "confirm": True,
                                          "password": "hunter2"}),
            WorkflowStep(index=1, action="other", skill="nsx", tool="create_segment",
                         params={}, status="not_executed", rollback_tool="delete_segment",
                         rollback_params={"segment_id": "db", "token": "tok-SECRET-9"}),
        ],
        params={"custom": True}, created_at="", updated_at="",
    )
    store.save(wf)
    br = server.rollback("wf-rb")["blast_radius"]
    (undo,) = br["would_roll_back"]
    assert undo["rollback_tool"] == "delete_segment"
    assert undo["rollback_params"]["segment_id"] == "app"
    assert undo["rollback_params"]["confirm"] is True
    assert undo["rollback_params"]["password"] != "hunter2"
    (agent,) = br["not_reversed_by_pilot"]
    assert agent["rollback_params"]["segment_id"] == "db"
    assert "tok-SECRET-9" not in repr(br) and "hunter2" not in repr(br)


@pytest.mark.unit
def test_rollback_blast_radius_redacts_secrets_itself():
    """Not only because the store redacts on save: the preview redacts what it lists."""
    from vmware_pilot.lifecycle_gate import rollback_blast_radius

    wf = Workflow(
        id="wf-mem", workflow_type="custom_rb", state=WorkflowState.FAILED,
        steps=[WorkflowStep(index=0, action="make", skill="nsx", tool="create_segment",
                            params={}, status="success", rollback_tool="delete_segment",
                            rollback_params={"segment_id": "app", "password": "hunter2"})],
        params={"custom": True}, created_at="", updated_at="",
    )
    (undo,) = rollback_blast_radius(wf)["would_roll_back"]
    assert undo["rollback_params"]["segment_id"] == "app"
    assert "hunter2" not in repr(undo)


# ── rollback preview redacts compound secret names ──────────────────────────
# The persistence redaction matches exact key names only; a preview that shows
# rollback parameters to a human must not print ``db_password`` or ``apiToken``.


def test_rollback_preview_redacts_compound_secret_key_names():
    from vmware_pilot.lifecycle_gate import _redact_preview

    params = {"db_password": "p1", "apiToken": "t1", "vm_name": "web-01", "author": "ops",
              "nested": {"clientSecret": "s1", "token_count": 5}, "items": [{"auth_key": "k"}]}
    out = _redact_preview(params)
    assert out["db_password"] == out["apiToken"] == "***"
    assert out["nested"]["clientSecret"] == "***" and out["items"][0]["auth_key"] == "***"
    assert out["vm_name"] == "web-01" and out["author"] == "ops"
    assert out["nested"]["token_count"] == 5
