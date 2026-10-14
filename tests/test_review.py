"""Tests for the structural workflow review (pure function in vmware_pilot.review)."""

from __future__ import annotations

import pytest

from vmware_pilot.models import Workflow, WorkflowState, WorkflowStep
from vmware_pilot.review import review


def _wf(steps: list[WorkflowStep]) -> Workflow:
    return Workflow(
        id="wf-test", workflow_type="test", state=WorkflowState.PENDING,
        steps=steps, params={}, created_at="", updated_at="",
    )


@pytest.mark.unit
class TestReviewVerdict:
    def test_clean_workflow_approved(self):
        wf = _wf([
            WorkflowStep(0, "list_vms", "monitor", "list_vms", {"target": "vc1"}),
            WorkflowStep(1, "require_approval", "pilot", "approve", {"message": "ok?"}),
            WorkflowStep(2, "power_on", "aiops", "power_on_vm", {"vm_name": "web-01"}),
        ])
        result = review(wf)
        assert result["verdict"] == "approved"
        assert result["summary"]["total_steps"] == 3
        assert result["summary"]["approval_gates"] == 1

    def test_destructive_without_approval_flagged(self):
        wf = _wf([
            WorkflowStep(0, "delete_vm", "aiops", "delete_vm", {"vm_name": "old-01"}),
        ])
        result = review(wf)
        assert result["verdict"] == "needs_revision"
        kinds = [f["kind"] for f in result["findings"]]
        assert "ungated_destructive" in kinds

    def test_destructive_with_approval_passes(self):
        wf = _wf([
            WorkflowStep(0, "require_approval", "pilot", "approve", {"message": "delete?"}),
            WorkflowStep(1, "delete_vm", "aiops", "delete_vm", {"vm_name": "old-01"}),
        ])
        result = review(wf)
        assert result["verdict"] == "approved"


@pytest.mark.unit
class TestReviewFindings:
    def test_empty_required_param_low_severity(self):
        wf = _wf([
            WorkflowStep(0, "create_seg", "nsx", "create_segment",
                         {"name": "", "subnet": "10.0.0.0/24"}),
        ])
        result = review(wf)
        kinds = [f["kind"] for f in result["findings"]]
        assert "empty_param" in kinds

    def test_placeholder_param_high_severity(self):
        wf = _wf([
            WorkflowStep(0, "scan", "monitor", "list_alarms",
                         {"entity": "REVIEW", "target": "vc1"}),
        ])
        result = review(wf)
        placeholder = [f for f in result["findings"] if f["kind"] == "placeholder_param"]
        assert len(placeholder) == 1
        assert placeholder[0]["severity"] == "high"

    def test_delete_then_use_high_severity(self):
        wf = _wf([
            WorkflowStep(0, "require_approval", "pilot", "approve", {"message": "ok"}),
            WorkflowStep(1, "delete_seg", "nsx", "delete_segment", {"segment_id": "seg-A"}),
            WorkflowStep(2, "list_ports", "nsx", "list_ports", {"segment_id": "seg-A"}),
        ])
        result = review(wf)
        kinds = [f["kind"] for f in result["findings"]]
        assert "delete_then_use" in kinds
        assert result["verdict"] == "needs_revision"


@pytest.mark.unit
class TestReviewGroups:
    def test_destructive_inside_parallel_group_flagged(self):
        steps = [
            WorkflowStep(0, "require_approval", "pilot", "approve", {"message": "ok"}),
            WorkflowStep(1, "delete_a", "aiops", "delete_vm", {"vm_name": "a"}, group_id="parallel-cleanup"),
            WorkflowStep(2, "delete_b", "aiops", "delete_vm", {"vm_name": "b"}, group_id="parallel-cleanup"),
        ]
        wf = _wf(steps)
        result = review(wf)
        kinds = [f["kind"] for f in result["findings"]]
        assert "destructive_in_parallel_group" in kinds

    def test_read_only_parallel_group_clean(self):
        steps = [
            WorkflowStep(0, "list_alarms", "monitor", "list_alarms", {}, group_id="gather"),
            WorkflowStep(1, "list_events", "monitor", "list_events", {}, group_id="gather"),
            WorkflowStep(2, "require_approval", "pilot", "approve", {"message": "ok"}),
        ]
        wf = _wf(steps)
        result = review(wf)
        assert result["verdict"] == "approved"
        assert result["summary"]["parallel_groups"] == 1
        assert result["summary"]["read_only_steps"] == 2


@pytest.mark.unit
class TestReviewSummary:
    def test_duration_estimate_positive(self):
        wf = _wf([
            WorkflowStep(0, "list_vms", "monitor", "list_vms", {}),
            WorkflowStep(1, "require_approval", "pilot", "approve", {"message": "ok"}),
            WorkflowStep(2, "delete_vm", "aiops", "delete_vm", {"vm_name": "x"}),
        ])
        result = review(wf)
        assert result["summary"]["est_duration_min"] >= 0.5

    def test_investigate_alert_template_clean(self):
        from vmware_pilot.templates import investigate_alert
        wf = investigate_alert(alert_entity="vm-01", deep_dive=True)
        result = review(wf)
        # All steps in investigate_alert are read-only L1/L2 — should be approved
        assert result["verdict"] == "approved"
        assert result["summary"]["parallel_groups"] == 2
